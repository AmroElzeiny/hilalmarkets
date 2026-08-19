"""Turning a board drawn on the canvas into a monitor the platform can run.

The canvas is a drawing. Everything on it — which cards, which numbers, how they are
joined, which coins, how to be told — is chosen by a person and kept in their browser.
This module is the one place that reads that drawing and produces the executable
definition, so the picture and the monitor can never mean two different things.

Three rules from CLAUDE.md shape it:

* **Nothing is re-implemented.** A card becomes a rule through
  :func:`ai_market_monitor.engine.builder_operations.build_condition` — the same
  function the Guided Builder uses — and the tree becomes a definition through
  :func:`ai_market_monitor.engine.strategy_compiler_v2.compile_strategy_draft_v2`.
  A second reader of "what does this card mean" is how the canvas and the Builder would
  come to disagree about the same board.
* **Fail closed.** A card the platform no longer offers, a value nobody set, a coin
  nobody chose: every one of those refuses the whole request with a sentence naming what
  is missing. Nothing is defaulted, guessed or dropped.
* **No Sharia status is decided here.** The screening method, its version and the
  allowed statuses are read from the governed record through the same context the canvas
  page itself is drawn from. Choosing a Favorites list selects a governed record by id;
  it does not create or alter a ruling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import ApprovedWatchlist
from ai_market_monitor.db.models.enums import ComplianceChangeBehavior, ShariaAssetStatus
from ai_market_monitor.engine.builder_contract import disabled_capabilities_from
from ai_market_monitor.engine.builder_operations import BuilderActionError, build_condition
from ai_market_monitor.engine.capability_shortlist import (
    configured_runtime_provider_requirements,
)
from ai_market_monitor.engine.strategy_compiler_v2 import (
    StrategyV2CompileError,
    compile_strategy_draft_v2,
)
from ai_market_monitor.schemas.strategy import AlertPolicy, StrategyDefinition
from ai_market_monitor.schemas.strategy_draft_v2 import (
    GROUP_ARITY,
    ConditionNodeType,
    ConditionNodeV2,
    DraftMode,
    MarketScopeV2,
    ShariaPolicyV2,
    ShariaUniverseMode,
    StrategyDraftV2,
    StrategyUniverseV2,
)
from ai_market_monitor.services.watchlist_snapshot import (
    WatchlistIdentityError,
    scope_from_draft,
    watchlist_content_hash,
)

#: The most cards one board may carry into a request. The compiler has its own node
#: budget and refuses anything past it; this is only a bound on how much a single
#: request may ask the server to read.
MAX_CANVAS_NODES = 200


class CanvasCompileError(ValueError):
    """The board cannot become a monitor. Carries a sentence a beginner can act on."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# What the page sends
# ---------------------------------------------------------------------------


class CanvasNode(BaseModel):
    """One card, or one group of cards, exactly as the board draws it."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["rule", "group"]
    #: Groups only: how the cards inside count together.
    op: Literal["and", "or", "not"] | None = None
    #: Rules only: which condition from the platform's own catalogue this card is.
    mechanic: str | None = Field(default=None, max_length=120)
    #: Rules only: what the person typed into that card's own form.
    values: dict[str, float | int | bool | str] = Field(default_factory=dict)
    required: bool = True
    children: list[CanvasNode] = Field(default_factory=list, max_length=100)


class CanvasUniverse(BaseModel):
    """Which coins the monitor would watch."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["eligible_market", "approved_watchlist", "explicit_assets"]
    #: The person's own Favorites list, when that is the mode.
    watchlist_id: UUID | None = None
    #: The coins they named themselves, when that is the mode. Plain tickers as the
    #: search offered them — "BTC", not "BTC/USDT". The trading pair is built here,
    #: from the market scope, so the page never has to know what a quote asset is.
    symbols: list[str] = Field(default_factory=list, max_length=200)


class CanvasAlert(BaseModel):
    """How the person asked to be told."""

    model_config = ConfigDict(extra="forbid")

    channels: list[str] = Field(min_length=1, max_length=8)
    cooldown_minutes: int = Field(default=15, ge=0, le=1440)


class CanvasPlan(BaseModel):
    """One whole board.

    ``root`` is the monitor itself. Cards that were set aside on the board are not part
    of the monitor, so the page never sends them: what arrives here is what the person
    joined up.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=120)
    root: CanvasNode
    universe: CanvasUniverse
    alert: CanvasAlert


@dataclass(frozen=True, slots=True)
class CanvasScreening:
    """The governed screening record this board would run under."""

    methodology_id: UUID | None
    methodology_version: str | None
    allowed_statuses: tuple[ShariaAssetStatus, ...]
    compliance_change_behavior: ComplianceChangeBehavior
    enforced: bool


def screening_from_context(context: dict[str, Any]) -> CanvasScreening:
    """Read the screening record out of the canvas page's own context.

    The same dictionary the page is rendered from, so the standard named in the banner
    above the board is the standard the monitor is built with. Reading the governed
    tables a second time here would be a second opinion about which method is active.
    """

    policy = context.get("policy") or {}
    raw_id = policy.get("methodology_id")
    statuses = tuple(
        ShariaAssetStatus(value)
        for value in policy.get("allowed_statuses", [])
        if value in {item.value for item in ShariaAssetStatus}
    ) or (
        ShariaAssetStatus.ELIGIBLE,
        ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS,
    )
    try:
        behavior = ComplianceChangeBehavior(
            policy.get("compliance_change_behavior", ComplianceChangeBehavior.PAUSE_ASSET.value)
        )
    except ValueError:
        behavior = ComplianceChangeBehavior.PAUSE_ASSET
    # The id and the version travel together or not at all. The context carries them in
    # two different places — the id inside the governed policy, the version beside it for
    # the banner — and the policy is absent entirely where screening is not enforced. A
    # version with no id is refused by the draft, so taking one without the other turned
    # a local database with no governance rows into an unexplainable validation error.
    methodology_id = UUID(str(raw_id)) if raw_id else None
    raw_version = context.get("methodology_version")
    return CanvasScreening(
        methodology_id=methodology_id,
        methodology_version=str(raw_version) if methodology_id and raw_version else None,
        allowed_statuses=statuses,
        compliance_change_behavior=behavior,
        enforced=bool(context.get("enforced")),
    )


# ---------------------------------------------------------------------------
# The board as an expression
# ---------------------------------------------------------------------------


def _node_count(node: CanvasNode) -> int:
    return 1 + sum(_node_count(child) for child in node.children)


def build_condition_ast(
    root: CanvasNode,
    *,
    source_turn_id: str,
    settings: Settings,
) -> ConditionNodeV2:
    """The whole board as one validated expression.

    Every card is read by ``build_condition``, which checks it against the catalogue the
    canvas itself was drawn from: an unknown card, a card the platform cannot run, or a
    required value nobody set stops the whole thing here rather than compiling into a
    rule that watches something else.
    """

    if _node_count(root) > MAX_CANVAS_NODES:
        raise CanvasCompileError(
            "board_too_large",
            "That board has more cards than one monitor can hold.",
        )
    configured = configured_runtime_provider_requirements(settings.market_data_provider)
    disabled = disabled_capabilities_from(settings.builder_capabilities_disabled)
    counter = {"index": 0}

    def convert(node: CanvasNode) -> ConditionNodeV2:
        if node.kind == "rule":
            if not node.mechanic:
                raise CanvasCompileError(
                    "card_without_condition",
                    "One card on the board has no condition chosen. Open it and pick one.",
                )
            counter["index"] += 1
            try:
                built, _ = build_condition(
                    mechanic_key=node.mechanic,
                    values=dict(node.values),
                    source_turn_id=source_turn_id,
                    node_id=f"canvas_{counter['index']}",
                    required=node.required,
                    configured_providers=configured,
                    disabled_capabilities=disabled,
                )
            except BuilderActionError as exc:
                raise CanvasCompileError(exc.code, str(exc)) from exc
            return built

        node_type = ConditionNodeType(node.op or "and")
        children = [convert(child) for child in node.children]
        fewest, most = GROUP_ARITY[node_type]
        if len(children) < fewest:
            raise CanvasCompileError(
                "group_too_small",
                "A group on the board has nothing in it. Put a card inside it, or remove it.",
            )
        if most is not None and len(children) > most:
            raise CanvasCompileError(
                "group_too_large",
                "“None of these” holds one card. Group the extra cards together first.",
            )
        counter["index"] += 1
        return ConditionNodeV2(
            node_id=f"canvas_group_{counter['index']}",
            node_type=node_type,
            children=children,
        )

    built_root = convert(root)
    if built_root.node_type == ConditionNodeType.CONDITION:
        # Every draft's outermost node is a group. One card on the board is still a
        # monitor, and wrapping it keeps the stored shape the same whether the board
        # holds one card or twenty.
        return ConditionNodeV2(
            node_id="canvas_root",
            node_type=ConditionNodeType.AND,
            children=[built_root],
        )
    return built_root


async def _watchlist_policy(
    session: AsyncSession,
    *,
    user_id: UUID,
    watchlist_id: UUID | None,
    draft: StrategyDraftV2,
) -> tuple[UUID, str]:
    """One Favorites list, owned by this person, plus its governed content identity."""

    if watchlist_id is None:
        raise CanvasCompileError(
            "watchlist_not_chosen",
            "Choose which Favorites list this monitor should watch.",
        )
    watchlist = await session.scalar(
        select(ApprovedWatchlist).where(
            ApprovedWatchlist.id == watchlist_id,
            ApprovedWatchlist.user_id == user_id,
        )
    )
    if watchlist is None:
        raise CanvasCompileError(
            "watchlist_not_found",
            "That Favorites list is not on this account any more. Pick another one.",
        )
    try:
        identity = await watchlist_content_hash(
            session,
            watchlist,
            scope=scope_from_draft(draft),
            require_resolved=True,
        )
    except WatchlistIdentityError as exc:
        raise CanvasCompileError("watchlist_unresolved", str(exc)) from exc
    return watchlist.id, identity


async def canvas_definition(
    session: AsyncSession,
    *,
    user_id: UUID,
    plan: CanvasPlan,
    screening: CanvasScreening,
    settings: Settings,
) -> StrategyDefinition:
    """The board, as the definition a monitor really runs on.

    Raises :class:`CanvasCompileError` with a sentence naming what is missing. It never
    substitutes a nearest capability, a default level or a fallback comparison: a
    refused board keeps the misunderstanding visible, an invented one silently watches
    the wrong thing.
    """

    if screening.enforced and screening.methodology_id is None:
        raise CanvasCompileError(
            "screening_not_published",
            "No screening standard is published yet, so nothing can be monitored. "
            "This is not something you can fix from here.",
        )
    if screening.methodology_id is not None and not screening.methodology_version:
        # Fail closed rather than drop the method. A monitor bound to a standard whose
        # version nobody recorded cannot be checked against that standard later, and
        # quietly removing the binding would make it look unscreened instead.
        raise CanvasCompileError(
            "screening_version_missing",
            "The screening standard on this account has no version recorded, so a "
            "monitor cannot be tied to it. This is not something you can fix from here.",
        )

    source_turn_id = f"canvas_{uuid4().hex[:16]}"
    ast = build_condition_ast(plan.root, source_turn_id=source_turn_id, settings=settings)
    scope = MarketScopeV2()

    mode = ShariaUniverseMode(plan.universe.mode)
    explicit: list[str] = []
    if mode is ShariaUniverseMode.EXPLICIT_ASSETS:
        if not plan.universe.symbols:
            raise CanvasCompileError(
                "coins_not_chosen",
                "Add at least one coin for this monitor to watch.",
            )
        # The page sends what a person recognises — a ticker. The market it trades as
        # is built here, from the one market scope this draft carries, so the page can
        # never send a pair that belongs to a different quote asset.
        explicit = [
            f"{symbol.strip().upper()}/{scope.quote_asset}"
            if "/" not in symbol
            else symbol.strip().upper()
            for symbol in plan.universe.symbols
        ]

    draft = StrategyDraftV2(
        mode=DraftMode.MONITOR,
        name=(plan.name.strip() or "Monitor")[:160],
        market_scope=scope,
        universe=StrategyUniverseV2(included_symbols=explicit),
        sharia_policy=ShariaPolicyV2(
            universe_mode=mode,
            methodology_id=screening.methodology_id,
            methodology_version=screening.methodology_version,
            allowed_statuses=list(screening.allowed_statuses),
            compliance_change_behavior=screening.compliance_change_behavior,
            explicit_symbols=explicit,
        ),
        condition_ast=ast,
    )

    if mode is ShariaUniverseMode.APPROVED_WATCHLIST:
        watchlist_id, identity = await _watchlist_policy(
            session,
            user_id=user_id,
            watchlist_id=plan.universe.watchlist_id,
            draft=draft,
        )
        draft = draft.model_copy(
            update={
                "sharia_policy": draft.sharia_policy.model_copy(
                    update={
                        "approved_watchlist_id": watchlist_id,
                        "approved_watchlist_version": identity,
                    }
                )
            }
        )

    try:
        definition = compile_strategy_draft_v2(draft)
    except StrategyV2CompileError as exc:
        raise CanvasCompileError(exc.code, str(exc)) from exc

    # The compiler fills in a delivery policy of its own, because a draft written from a
    # typed message may never have said one. The board did say one — it is a card on it —
    # so the person's own answer replaces the compiler's placeholder rather than sitting
    # beside it.
    return definition.model_copy(
        update={
            "alerts": AlertPolicy(
                channels=list(plan.alert.channels),  # type: ignore[arg-type]
                cooldown_seconds=plan.alert.cooldown_minutes * 60,
                near_miss_threshold=definition.alerts.near_miss_threshold,
                forming_alerts=definition.alerts.forming_alerts,
            )
        }
    )
