"""What the monitor canvas asks the server for.

Four questions, and nothing else:

* **which coins can I choose?** — the screened list, searched by name or ticker;
* **which Favorites lists do I have, and what is on them?**;
* **show me the board this monitor was drawn from**, so it can be changed;
* **turn this board into a monitor and prove it can reach me.**

The last one runs the product's own activation path — create, approve, check, start —
rather than a shortcut of its own. A monitor made from the canvas and a monitor made
anywhere else are the same object, went through the same gates, and can be paused,
edited and audited in exactly the same way. A private path here would be a second way
into the runtime, and the first one to drift would be the one nobody tests.

Changing a monitor uses that same path. It writes a **new version** of the monitor that
already exists rather than a second monitor, so the alerts, the history and the evidence
trail stay attached to the one thing the person has been watching.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import UserPrincipal, get_market_previewer
from ai_market_monitor.api.routers.dashboard import _builder_screening_context
from ai_market_monitor.api.routers.dashboard_api import get_dashboard_principal
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.dashboard_paths import MONITOR_CANVAS_PREFIX, SETTINGS_PATH
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.db.models import (
    ApprovedWatchlist,
    ApprovedWatchlistAsset,
    Strategy,
    StrategyVersion,
    User,
)
from ai_market_monitor.schemas.strategy import InterpretationPreview
from ai_market_monitor.services.entitlements import EntitlementError, EntitlementService
from ai_market_monitor.services.interfaces import RecentMarketPreviewer
from ai_market_monitor.services.monitor_canvas import (
    CanvasCompileError,
    CanvasPlan,
    canvas_definition,
    screening_from_context,
)
from ai_market_monitor.services.monitor_test_alert import MonitorTestAlertService
from ai_market_monitor.services.notification_preferences import alert_channel_choices
from ai_market_monitor.services.risk_disclaimer import (
    DisclaimerIdentityMissing,
)
from ai_market_monitor.services.risk_disclaimer import (
    has_accepted as has_accepted_risk_note,
)
from ai_market_monitor.services.risk_disclaimer import (
    record_acceptance as record_risk_note_acceptance,
)
from ai_market_monitor.services.sharia_screening import ShariaScreeningService
from ai_market_monitor.services.strategy import StrategyGateError, StrategyService
from ai_market_monitor.services.verified_strategy import (
    VerifiedStrategyError,
    VerifiedStrategyService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix=MONITOR_CANVAS_PREFIX, tags=["monitor-canvas"])


async def _current_user(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    user = await session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Dashboard session required")
    return user


# ── Which coins ───────────────────────────────────────────────────────────────


@router.get("/coins")
async def canvas_coin_search(
    q: str = Query(default="", max_length=60),
    limit: int = Query(default=12, ge=1, le=50),
    user: User = Depends(_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Screened coins matching what somebody typed.

    Only coins with a published review under the active standard, because those are the
    only ones a monitor may watch. Nothing here decides a Shariah status: it reads the
    governed list and reports what the review already said.
    """

    context = await _builder_screening_context(session, user, settings)
    policy = context.get("policy") or {}
    raw_methodology = policy.get("methodology_id")
    screening = ShariaScreeningService(session, settings)
    result = await screening.list_screened_assets(
        methodology_id=UUID(str(raw_methodology)) if raw_methodology else None,
        search=q.strip() or None,
        page=1,
        limit=limit,
    )
    return {
        "items": [
            {
                "symbol": item.canonical_asset,
                "name": item.asset_name or item.canonical_asset,
                "status": item.status.value,
                "status_label": item.status_label,
                "logo_url": item.logo_url,
            }
            for item in result.items
        ],
        "total": result.total,
        # Said once, here, so the page never has to explain an empty list itself.
        "notice": result.warning,
    }


@router.get("/favorites")
async def canvas_favorites(
    user: User = Depends(_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """The person's own Favorites lists, with the coins on each one.

    Scoped to the signed-in person. A list belonging to somebody else is not a display
    problem: offering it would leak both its name and its coins.
    """

    lists = (
        await session.scalars(
            select(ApprovedWatchlist)
            .where(ApprovedWatchlist.user_id == user.id)
            .order_by(ApprovedWatchlist.is_default.desc(), ApprovedWatchlist.name.asc())
            .limit(30)
        )
    ).all()
    if not lists:
        return {"items": []}

    rows = (
        await session.execute(
            select(
                ApprovedWatchlistAsset.watchlist_id,
                ApprovedWatchlistAsset.canonical_asset,
            )
            .where(ApprovedWatchlistAsset.watchlist_id.in_([item.id for item in lists]))
            .order_by(ApprovedWatchlistAsset.canonical_asset.asc())
        )
    ).all()
    coins: dict[UUID, list[str]] = {}
    for watchlist_id, canonical_asset in rows:
        coins.setdefault(watchlist_id, []).append(canonical_asset)

    return {
        "items": [
            {
                "id": str(item.id),
                "name": item.name,
                "is_default": bool(item.is_default),
                "coins": coins.get(item.id, []),
                "empty_reason": (
                    "This list has no coins in it yet." if not coins.get(item.id) else None
                ),
            }
            for item in lists
        ]
    }


# ── Opening a monitor somebody already has ────────────────────────────────────


#: Why a monitor cannot be drawn on the canvas, in words that name the next step.
#:
#: Never a guessed board. A monitor whose drawing was not kept is not "an empty canvas";
#: an empty canvas that then gets switched on would replace the person's rules with
#: nothing. The page says what happened and offers the only honest way forward.
_NO_BOARD = (
    "This monitor was not drawn on the canvas, so there is no board to open. You can "
    "draw a new monitor here, and put this one away when the new one is running."
)

_MONITOR_GONE = "That monitor is not on this account any more."


def _monitor_key(raw: str) -> UUID | None:
    """One monitor id, or nothing. A mistyped address is not an error to report."""

    try:
        return UUID(raw)
    except (ValueError, AttributeError):
        return None


@router.get("/monitors/{monitor_id}")
async def canvas_monitor_board(
    monitor_id: str,
    user: User = Depends(_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """The board one monitor was drawn from, so the canvas can open it again.

    The board is read back exactly as it was sent — never rebuilt from the compiled
    rules. Nothing turns a compiled monitor back into cards, and a second reader that
    tried would be a second opinion about what each card meant.

    The id is read as text and parsed here rather than by the route, so a mistyped
    address answers with the same sentence as a monitor that is not on this account. A
    validation error would show a beginner a wall of field names, and telling the two
    apart would tell a stranger which ids are real.
    """

    key = _monitor_key(monitor_id)
    strategy = await session.get(Strategy, key) if key is not None else None
    if strategy is None or strategy.user_id != user.id or strategy.archived_at is not None:
        raise HTTPException(
            status_code=404,
            detail={"code": "monitor_gone", "message": _MONITOR_GONE},
        )

    version = await session.scalar(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy.id)
        .order_by(StrategyVersion.version_number.desc())
        .limit(1)
    )
    board = version.canvas_plan_json if version is not None else None
    return {
        "monitor": {"id": str(strategy.id), "name": strategy.name},
        # `None` is an answer, not a missing field. The page shows the sentence below it
        # rather than an empty board a person could switch on by accident.
        "plan": board,
        "reason": None if board else _NO_BOARD,
    }


# ── Turning the board into a monitor ──────────────────────────────────────────


class CanvasActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The monitor this board belongs to, when somebody is changing one they already
    #: have. Empty means a new monitor. Sent by the page rather than inferred, because
    #: "change this" and "make another one just like it" are different intentions and
    #: nothing about the board itself tells them apart.
    #:
    #: Text rather than a typed id, so a mistyped one is answered by the same sentence as
    #: a monitor that is not on this account — see ``_monitor_key``. A validation error
    #: here would reach a beginner as a list of field names.
    monitor_id: str = Field(default="", max_length=64)
    plan: CanvasPlan
    #: The board's own sentence, in the words the person read on the page. Sent rather
    #: than rebuilt here on purpose: the readout, the popup and the test message must
    #: say the same thing, and a second writer of that sentence is a second opinion.
    in_plain_words: str = Field(default="", max_length=1200)
    #: Whether the person ticked the risk note in the popup. Never assumed: a monitor
    #: cannot start without an acceptance on file, and recording one because a request
    #: arrived would be writing a legal record on somebody's behalf.
    accepted_risk_note: bool = False


def _monitor_name(plan: CanvasPlan) -> str:
    name = plan.name.strip()
    return (name or "My monitor")[:120]


@router.post("/activate")
async def activate_canvas_monitor(
    payload: CanvasActivateRequest,
    user: User = Depends(_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    previewer: RecentMarketPreviewer = Depends(get_market_previewer),
) -> dict[str, Any]:
    """Turn the board into a running monitor, then prove it can reach this person.

    Order matters and it is the honest one: the monitor is created and started first,
    and only then is the test message sent. A test that went out before the monitor
    existed would tell somebody their monitor works when nothing had been saved.

    A channel that fails does **not** undo the monitor. The monitor is real and running;
    what failed is one way of being told, which is a thing the person can fix in
    Settings without building anything again. The reply says exactly which is which.

    When ``monitor_id`` is sent the board changes a monitor that already exists: a new
    version of the same monitor, through the same gates. The old version is superseded
    by the platform's own activation, so a person never ends up with two monitors where
    they meant to change one.
    """

    plan = payload.plan

    # Whose monitor this is, settled before anything is built. A board carrying somebody
    # else's monitor id must not reach the compiler, and "not yours" and "not there" are
    # deliberately the same answer: the difference would tell a stranger that an id is
    # real.
    existing: Strategy | None = None
    if payload.monitor_id.strip():
        key = _monitor_key(payload.monitor_id.strip())
        existing = await session.get(Strategy, key) if key is not None else None
        if (
            existing is None
            or existing.user_id != user.id
            or existing.archived_at is not None
        ):
            raise HTTPException(
                status_code=404,
                detail={"code": "monitor_gone", "message": _MONITOR_GONE},
            )

    # What the platform can really deliver, for this person's plan. Checked before
    # anything is built: a channel that reached the alert schema unchecked would raise
    # a validation error deep in the compiler, and a beginner would be shown a 500 for a
    # choice the page should never have accepted.
    entitlement = await EntitlementService(session).current(user.id)
    offered = {
        choice["value"]
        for choice in alert_channel_choices(
            settings,
            whatsapp_allowed=entitlement.feature_enabled("whatsapp"),
        )
    }
    refused = [channel for channel in plan.alert.channels if channel not in offered]
    if refused:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "channel_not_available",
                "message": (
                    "One of the ways you chose to be told is not available on this "
                    "account. Open the card and pick another."
                ),
            },
        )

    # The risk note. A monitor cannot start without one on file — the activation gate
    # refuses with `disclaimer_required` — and nothing on the website had ever asked,
    # because only the Telegram bot and the older onboarding flow could record it. So
    # every monitor built on the dashboard failed at the last step with a word from
    # inside the machine. The popup asks, once, and this writes down the answer.
    already = await has_accepted_risk_note(
        session,
        user_id=user.id,
        version=settings.disclaimer_version,
    )
    if not already:
        if not payload.accepted_risk_note:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "risk_note_required",
                    "message": (
                        "Tick the box to say you understand what a monitor does before "
                        "switching it on."
                    ),
                },
            )
        try:
            await record_risk_note_acceptance(
                session,
                user_id=user.id,
                version=settings.disclaimer_version,
                source="monitor_canvas",
            )
        except DisclaimerIdentityMissing as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "identity_missing",
                    "message": (
                        "Something is missing from this account, so nothing was saved. "
                        "Please contact support."
                    ),
                },
            ) from exc

    context = await _builder_screening_context(session, user, settings)
    try:
        definition = await canvas_definition(
            session,
            user_id=user.id,
            plan=plan,
            screening=screening_from_context(context),
            settings=settings,
        )
    except CanvasCompileError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    name = _monitor_name(plan)
    definition = definition.model_copy(update={"name": name})
    strategy_service = StrategyService(session, settings.disclaimer_version, settings)
    verification_service = VerifiedStrategyService(session, settings)

    # The board is kept beside the rules it compiled to, in this same transaction. It is
    # what "Change it" opens later; without it the canvas could only ever offer an empty
    # board for a monitor that already has rules on it.
    board = plan.model_dump(mode="json")

    try:
        if existing is not None:
            strategy = existing
            version = await strategy_service.revise(
                existing,
                definition,
                user_id=user.id,
                source_text=payload.in_plain_words[:2000] or name,
                interpreter="monitor-canvas",
                canvas_plan=board,
            )
        else:
            strategy, version = await strategy_service.create_from_interpretation(
                user.id,
                InterpretationPreview(
                    strategy=definition,
                    assumptions=[],
                    ambiguities=[],
                    unsupported_conditions=[],
                    interpreter="monitor-canvas",
                ),
                source_text=payload.in_plain_words[:2000] or name,
                canvas_plan=board,
            )
        await verification_service.prepare_version(
            user_id=user.id,
            strategy=strategy,
            version=version,
        )
        await verification_service.approve_visible_draft(
            user_id=user.id,
            version=version,
            expected_schema_hash=version.schema_hash,
        )
        await strategy_service.approve(
            version,
            user_id=user.id,
            expected_schema_hash=version.schema_hash,
        )
        await verification_service.approval_gate(user_id=user.id, version=version)
        await strategy_service.run_preview(version, user_id=user.id, previewer=previewer)
        await strategy_service.activate(version, user_id=user.id, strategy_name=name)
    except (StrategyGateError, EntitlementError, VerifiedStrategyError) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": getattr(exc, "code", "activation_refused"),
                "message": _activation_message(getattr(exc, "code", "")),
            },
        ) from exc
    await session.commit()

    results = await MonitorTestAlertService(session, settings).send(
        user_id=user.id,
        monitor_name=name,
        in_plain_words=payload.in_plain_words.strip() or name,
        channels=list(plan.alert.channels),
        strategy_version_id=version.id,
    )
    await session.commit()

    return {
        "monitor": {
            "id": str(strategy.id),
            "name": name,
            "status": "watching",
            # Which of the two things just happened, said by the side that knows. The
            # page words its confirmation from this rather than from whether it happened
            # to be holding a monitor id — a person who changed a monitor must not be
            # told a second one was made.
            "changed": existing is not None,
        },
        "results": [item.to_dict() for item in results],
        "all_sent": all(item.sent for item in results),
        "settings_url": SETTINGS_PATH,
    }


#: What a refused activation means, in words that name the next step.
#:
#: Written here rather than shown raw, because every one of these codes is an internal
#: name — "screened_universe_empty" tells a beginner nothing about what to do.
_ACTIVATION_WORDS: dict[str, str] = {
    "screened_universe_empty": (
        "None of the coins you chose pass the screening right now, so there is nothing "
        "to watch. Choose different coins and try again."
    ),
    "screened_preview_unavailable": (
        "We could not check the market just now, so the monitor was not started. "
        "Nothing was saved. Please try again in a few minutes."
    ),
    "strategy_conflict_detected": (
        "This monitor clashes with one you already have. Look at your monitors and "
        "pause or change the other one first."
    ),
    "plan_limit_reached": (
        "Your plan does not allow another monitor running at once. Pause one, or look "
        "at your plan."
    ),
    "preview_required": (
        "We could not check the market against your rules just now, so the monitor was "
        "not started. Nothing was saved. Please try again in a few minutes."
    ),
    "notification_channel_required": (
        "A monitor needs somewhere to send its message. Open the “How you hear about "
        "it” card and pick at least one."
    ),
    "provider_disabled": (
        "The market data this monitor needs is switched off for this account. Nothing "
        "was saved."
    ),
    "strategy_changed": (
        "Something about the board changed while it was being switched on, so nothing "
        "was saved. Press the button again."
    ),
    "ambiguities_unresolved": (
        "One of the cards is not clear enough to run. Open the checks below the board "
        "and finish what they name."
    ),
}


def _activation_message(code: str) -> str:
    return _ACTIVATION_WORDS.get(
        code,
        "The monitor could not be started, so nothing was saved. Your board is "
        "untouched — please try again.",
    )


__all__ = ["router"]
