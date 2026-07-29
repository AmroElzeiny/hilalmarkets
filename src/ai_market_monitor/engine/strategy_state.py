"""Canonical typed state for a strategy under construction.

The setup chat accumulates the user's text and recompiles the whole blob each turn.
That keeps the draft independent of the model's memory, which is right, but it makes
the *first* mention of a field win: a session that says ``use the 15m`` and later
``actually make it 1h`` still compiles 15m, because the extractor scans the joined
text front to back. Run 20260725T122105Z exercised exactly this under the
``repeated_correction_cycles`` and ``revert_correction`` topics.

This module replaces first-mention-wins with an ordered patch log:

* every turn contributes a patch per field it actually names, never for fields it
  does not mention, so silence never overwrites a known value
* the current value of a field is the value of its newest patch — latest correction
  wins, however many corrections came before it
* each patch carries the previous value, so a reversion restores the exact prior
  value rather than re-deriving it from text that has since changed
* exclusions accumulate across the whole conversation instead of being recomputed
  from whichever clause happens to be nearest

Nothing here calls a model, and nothing here decides approval. It is a deterministic
record of what the user has said about each field, and in which order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Literal, cast

from ai_market_monitor.engine.comparators import detect_comparator
from ai_market_monitor.engine.text_normalization import repair_utf8_mojibake
from ai_market_monitor.engine.turn_fragments import (
    REVERSION_RE,
    classify_turn,
    extract_explicit_exclusions,
    extract_timeframe_roles,
    to_pair,
)
from ai_market_monitor.schemas.strategy import Comparator, StrategyDirection

StateField = Literal[
    "direction",
    "base_timeframe",
    "context_timeframes",
    "include_symbols",
    "exclude_symbols",
    "exchange",
    "quote_asset",
    "market_type",
    "comparator",
    "threshold",
    "formula",
    "mechanic_fragments",
    "formula_fragments",
    "boolean_groups",
]

STATE_FIELDS: tuple[StateField, ...] = (
    "direction",
    "base_timeframe",
    "context_timeframes",
    "include_symbols",
    "exclude_symbols",
    "exchange",
    "quote_asset",
    "market_type",
    "comparator",
    "threshold",
    "formula",
    "mechanic_fragments",
    "formula_fragments",
    "boolean_groups",
)

#: Fields holding an ordered set of values rather than a single scalar.
COLLECTION_FIELDS: frozenset[StateField] = frozenset(
    {
        "context_timeframes",
        "include_symbols",
        "exclude_symbols",
        "mechanic_fragments",
        "formula_fragments",
        "boolean_groups",
    }
)

ApprovalState = Literal[
    "COLLECTING",
    "NEEDS_CLARIFICATION",
    "READY_FOR_CONFIRMATION",
    "AWAITING_APPROVAL",
    "APPROVED",
    "COMPILED",
    "ACTIVATED",
]

#: Wording that replaces a universe outright instead of adding to it. `only BTCUSDT`
#: after `watch ETHUSDT` narrows the universe; `add SOLUSDT` widens it.
_REPLACEMENT_MARKERS: tuple[str, ...] = (
    "only",
    "solely",
    "exclusively",
    "nothing but",
    "just",
    "instead of",
    "replace",
    "switch to",
    "change to",
    "فقط",
    "بس",
)

_ADDITIVE_MARKERS: tuple[str, ...] = ("add", "also", "include", "as well", "plus", "along with")

#: One reversion vocabulary, shared with the fragment classifier so a phrase that
#: rolls state back is never also read as a market mechanic to resolve.
_REVERSION_RE = REVERSION_RE

_UNEXCLUDE_RE = re.compile(
    r"\b(?:stop\s+excluding|un-?exclude|put\s+back|bring\s+back|re-?include)\b",
    re.IGNORECASE,
)

# Data-contract descriptions constrain whether evaluation may run; they are not
# price conditions. Keeping them out of compiler prose prevents endpoint fields,
# list numbering, freshness examples, and formula scale factors from becoming
# executable thresholds or unsupported trading mechanics.
_PROVIDER_REQUIREMENT_RE = re.compile(
    r"\b(?:provider|data\s+(?:feed|source|fields?|requirements?)|endpoint|ohlcv|"
    r"open_time|close_time|timestamp|timezone|venue|stale(?:ness)?|"
    r"missing\s+(?:feed|candles?|data)|availability)\b",
    re.IGNORECASE,
)
_GENERIC_FORMULA_DISCUSSION_RE = re.compile(
    r"\b(?:formula|percent(?:age)?|move|bullish|bearish|open|close|high|low|"
    r"start|end|cprev|c0)\b",
    re.IGNORECASE,
)
_DISTINCT_MECHANIC_RE = re.compile(
    r"\b(?:rsi|mfi|macd|roc|ema|sma|hma|wma|vwap|atr|adx|bollinger|"
    r"stochastic|volume|liquidity|spread|order\s*book|trend|breakout|"
    r"breakdown|support|resistance|swing|pullback|retest|reversal|"
    r"momentum|divergence|cross|sweep|wick|doji|hammer|engulfing)\b",
    re.IGNORECASE,
)
_DIALOGUE_RESIDUE_RE = re.compile(
    r"^\W*(?:nah|no|hey|yo)?\W*(?:"
    r"(?:your\s+)?(?:draft|reply|response|answer)\b|"
    r"(?:state|give|list|spell|confirm|tell)\s+(?:me\s+)?(?:the\s+)?exact\b|"
    r"don['’]?t\s+(?:hand[-\s]*wave|dodge|reinterpret|substitute)|"
    r"(?:what|which|how)\b.*(?:you(?:'ll|\s+will)|do\s+you)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FieldPatch:
    """One turn's statement about one field, with the value it replaced."""

    field: StateField
    value: Any
    turn: int
    source_text: str = ""
    previous_value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "value": _encode(self.value),
            "turn": self.turn,
            "source_text": self.source_text,
            "previous_value": _encode(self.previous_value),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FieldPatch:
        name = str(data.get("field") or "")
        if name not in STATE_FIELDS:
            raise ValueError(f"Unknown strategy state field: {name!r}")
        state_field: StateField = name
        return cls(
            field=state_field,
            value=_decode(state_field, data.get("value")),
            turn=int(data.get("turn") or 0),
            source_text=str(data.get("source_text") or ""),
            previous_value=_decode(state_field, data.get("previous_value")),
        )


@dataclass(frozen=True, slots=True)
class StrategyDraftState:
    """The ordered patch log and the values it resolves to."""

    patches: tuple[FieldPatch, ...] = field(default_factory=tuple)
    draft_version: int = 0
    approval_state: ApprovalState = "COLLECTING"
    canonical_hash: str | None = None
    approved_hash: str | None = None
    approved_version: int | None = None
    conversation_snapshot_hash: str | None = None
    approved_conversation_snapshot_hash: str | None = None
    approved_user_id: str | None = None
    unresolved_definitions: tuple[str, ...] = field(default_factory=tuple)
    unsupported_capabilities: tuple[str, ...] = field(default_factory=tuple)
    provider_requirements: tuple[str, ...] = field(default_factory=tuple)

    @property
    def turn(self) -> int:
        return max((patch.turn for patch in self.patches), default=0)

    def history(self, name: StateField) -> tuple[FieldPatch, ...]:
        """Every patch for ``name``, oldest first."""
        return tuple(patch for patch in self.patches if patch.field == name)

    def value(self, name: StateField) -> Any:
        """The current value: whatever the newest patch set. Latest wins."""
        history = self.history(name)
        if not history:
            return () if name in COLLECTION_FIELDS else None
        return history[-1].value

    def previous(self, name: StateField) -> Any:
        """The value this field held before its current one."""
        history = self.history(name)
        if not history:
            return () if name in COLLECTION_FIELDS else None
        return history[-1].previous_value

    def stated_fields(self) -> tuple[StateField, ...]:
        """Fields the user has actually spoken about, in first-mention order."""
        seen: list[StateField] = []
        for patch in self.patches:
            if patch.field not in seen:
                seen.append(patch.field)
        return tuple(seen)

    def resolved(self) -> dict[str, Any]:
        """Every stated field's current value, ready to hand to the compiler."""
        return {name: self.value(name) for name in STATE_FIELDS if self.history(name)}

    def apply(self, patches: tuple[FieldPatch, ...] | list[FieldPatch]) -> StrategyDraftState:
        incoming = tuple(patches)
        if not incoming:
            return self
        return replace(
            self,
            patches=(*self.patches, *incoming),
            draft_version=self.draft_version + 1,
            approval_state="COLLECTING",
            canonical_hash=None,
            approved_hash=None,
            approved_version=None,
            conversation_snapshot_hash=None,
            approved_conversation_snapshot_hash=None,
            approved_user_id=None,
        )

    def with_compilation(
        self,
        *,
        canonical_hash: str,
        conversation_snapshot_hash: str,
        unresolved_definitions: tuple[str, ...] = (),
        unsupported_capabilities: tuple[str, ...] = (),
        provider_requirements: tuple[str, ...] = (),
    ) -> StrategyDraftState:
        """Attach deterministic compiler output to this exact draft version."""

        blocked = bool(unresolved_definitions or unsupported_capabilities)
        return replace(
            self,
            canonical_hash=canonical_hash,
            conversation_snapshot_hash=conversation_snapshot_hash,
            unresolved_definitions=unresolved_definitions,
            unsupported_capabilities=unsupported_capabilities,
            provider_requirements=provider_requirements,
            approval_state=("NEEDS_CLARIFICATION" if blocked else "READY_FOR_CONFIRMATION"),
        )

    def awaiting_approval(self) -> StrategyDraftState:
        """Expose a complete compiled draft for an explicit human decision."""

        if self.approval_state != "READY_FOR_CONFIRMATION":
            raise ValueError("only a complete reviewed draft can await approval")
        if not self.canonical_hash or not self.conversation_snapshot_hash:
            raise ValueError("approval requires a compiled draft identity")
        return replace(self, approval_state="AWAITING_APPROVAL")

    def with_approval(
        self,
        *,
        canonical_hash: str,
        draft_version: int,
        conversation_snapshot_hash: str,
        user_id: str,
    ) -> StrategyDraftState:
        """Bind explicit human approval to an immutable draft identity."""

        if self.approval_state != "AWAITING_APPROVAL":
            raise ValueError("the current draft is not awaiting approval")
        if not self.canonical_hash or canonical_hash != self.canonical_hash:
            raise ValueError("approval hash does not match the current canonical draft")
        if draft_version != self.draft_version:
            raise ValueError("approval version does not match the current draft version")
        if conversation_snapshot_hash != self.conversation_snapshot_hash:
            raise ValueError("approval conversation snapshot does not match")
        if not user_id.strip():
            raise ValueError("approval requires an authenticated user")
        if self.unresolved_definitions or self.unsupported_capabilities:
            raise ValueError("a blocked draft cannot be approved")
        return replace(
            self,
            approval_state="APPROVED",
            approved_hash=canonical_hash,
            approved_version=draft_version,
            approved_conversation_snapshot_hash=conversation_snapshot_hash,
            approved_user_id=user_id,
        )

    def mark_compiled(self) -> StrategyDraftState:
        """Record that the approved hash passed the immutable version gates."""

        if self.approval_state != "APPROVED":
            raise ValueError("only an approved draft can become compiled")
        if self.approved_hash != self.canonical_hash:
            raise ValueError("compiled draft hash no longer matches approval")
        if self.approved_conversation_snapshot_hash != self.conversation_snapshot_hash:
            raise ValueError("compiled draft snapshot no longer matches approval")
        if not self.approved_user_id:
            raise ValueError("compiled draft has no authenticated approver")
        return replace(self, approval_state="COMPILED")

    def mark_activated(self) -> StrategyDraftState:
        """Record activation after the separate application action succeeds."""

        if self.approval_state != "COMPILED":
            raise ValueError("only a compiled draft can become activated")
        return replace(self, approval_state="ACTIVATED")

    def to_dict(self) -> dict[str, Any]:
        return {
            "patches": [patch.to_dict() for patch in self.patches],
            "draft_version": self.draft_version,
            "approval_state": self.approval_state,
            "canonical_hash": self.canonical_hash,
            "approved_hash": self.approved_hash,
            "approved_version": self.approved_version,
            "conversation_snapshot_hash": self.conversation_snapshot_hash,
            "approved_conversation_snapshot_hash": self.approved_conversation_snapshot_hash,
            "approved_user_id": self.approved_user_id,
            "unresolved_definitions": list(self.unresolved_definitions),
            "unsupported_capabilities": list(self.unsupported_capabilities),
            "provider_requirements": list(self.provider_requirements),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> StrategyDraftState:
        if not isinstance(data, dict):
            return cls()
        raw = data.get("patches")
        if not isinstance(raw, list):
            return cls()
        patches: list[FieldPatch] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                patches.append(FieldPatch.from_dict(item))
            except (ValueError, TypeError):
                # A patch we cannot read is dropped rather than guessed at; the
                # remaining log still resolves every field it does cover.
                continue
        approval_state = str(data.get("approval_state") or "COLLECTING")
        if approval_state not in {
            "COLLECTING",
            "NEEDS_CLARIFICATION",
            "READY_FOR_CONFIRMATION",
            "AWAITING_APPROVAL",
            "APPROVED",
            "COMPILED",
            "ACTIVATED",
        }:
            approval_state = "COLLECTING"
        return cls(
            patches=tuple(patches),
            draft_version=max(0, int(data.get("draft_version") or 0)),
            approval_state=cast(ApprovalState, approval_state),
            canonical_hash=_optional_text(data.get("canonical_hash")),
            approved_hash=_optional_text(data.get("approved_hash")),
            approved_version=(
                max(0, int(data["approved_version"]))
                if data.get("approved_version") is not None
                else None
            ),
            conversation_snapshot_hash=_optional_text(data.get("conversation_snapshot_hash")),
            approved_conversation_snapshot_hash=_optional_text(
                data.get("approved_conversation_snapshot_hash")
            ),
            approved_user_id=_optional_text(data.get("approved_user_id")),
            unresolved_definitions=_string_tuple(data.get("unresolved_definitions")),
            unsupported_capabilities=_string_tuple(data.get("unsupported_capabilities")),
            provider_requirements=_string_tuple(data.get("provider_requirements")),
        )


def is_reversion_request(text: str) -> bool:
    """True when the turn asks to go back to a previous value."""
    return bool(_REVERSION_RE.search(text or ""))


def patches_for_turn(
    text: str,
    state: StrategyDraftState,
    *,
    turn: int | None = None,
) -> tuple[FieldPatch, ...]:
    """Derive this turn's patches. Fields the turn does not name get no patch."""
    normalized = repair_utf8_mojibake(text or "")
    collapsed = " ".join(normalized.split())
    if not collapsed:
        return ()
    turn_number = state.turn + 1 if turn is None else turn
    # Newlines and list boundaries are semantic boundaries. Flattening before
    # classification joined "ETH only" to a later "BTC excluded" clause and could
    # reverse the universe.
    report = classify_turn(normalized)
    state_fragments = tuple(
        item for item in report.fragments if item.contributes_strategy_state
    )
    state_text = " ".join(dict.fromkeys(item.text for item in state_fragments))
    # Canonical-field readers may use presentation clauses without routing those
    # clauses into capability resolution.
    roles = extract_timeframe_roles(normalized)
    lowered = state_text.casefold()
    has_open_decision = any(item.kind == "decision_request" for item in report.fragments)
    patches: list[FieldPatch] = []

    def add(name: StateField, value: Any) -> None:
        for index in range(len(patches) - 1, -1, -1):
            pending = patches[index]
            if pending.field != name:
                continue
            if _equal(pending.value, value):
                return
            if _equal(pending.previous_value, value):
                del patches[index]
                return
            patches[index] = FieldPatch(
                field=name,
                value=value,
                turn=turn_number,
                source_text=collapsed[:500],
                previous_value=pending.previous_value,
            )
            return
        current = state.value(name)
        if _equal(current, value):
            return
        patches.append(
            FieldPatch(
                field=name,
                value=value,
                turn=turn_number,
                source_text=collapsed[:500],
                previous_value=current,
            )
        )

    if report.direction is not None:
        add("direction", report.direction)
    if roles.trigger is not None:
        add("base_timeframe", roles.trigger)
    elif len(report.timeframes) == 1 and not roles.context:
        # A single unqualified timeframe is the timeframe the rule is evaluated on.
        add("base_timeframe", report.timeframes[0])
    if roles.context:
        add(
            "context_timeframes",
            _merge_collection(state.value("context_timeframes"), roles.context, lowered=lowered),
        )
    alternatives = _offers_unresolved_alternatives(lowered)
    percent_fragments = [
        item
        for item in report.fragments
        if item.threshold_is_percent and item.threshold is not None
    ]
    percent_comparator = next(
        (
            item.comparator
            for item in reversed(percent_fragments)
            if item.comparator is not None
        ),
        None,
    )
    percent_threshold = percent_fragments[-1].threshold if percent_fragments else None
    has_percent_threshold = percent_threshold is not None
    settled_formula = state.value("formula")
    settled_formula_comparator = (
        str(settled_formula.get("comparator")) if isinstance(settled_formula, dict) else None
    )
    if settled_formula_comparator is None and state.value("comparator") is not None:
        settled_formula_comparator = str(state.value("comparator"))
    settled_formula_threshold = (
        float(str(settled_formula.get("threshold_percent")))
        if isinstance(settled_formula, dict)
        and settled_formula.get("threshold_percent") is not None
        else (
            float(state.value("threshold"))
            if state.value("threshold") is not None
            else None
        )
    )
    explicit_operator_edit = bool(
        re.search(
            r"\b(?:operator|comparator)\s*[:=]?\s*(?:gte|lte|gt|lt|eq)\b",
            lowered,
        )
    )
    explicit_numeric_edit = explicit_operator_edit or bool(
        re.search(
            r"\b(?:change|replace|correct|update|switch|instead|"
            r"actually|(?:actually|now)\s+use|"
            r"use\s+(?:the\s+)?(?:operator|comparator))\b",
            lowered,
        )
    )
    updates_primary_formula = (
        settled_formula_threshold is None
        or explicit_numeric_edit
    )
    if (
        percent_comparator is not None
        and updates_primary_formula
        and (
            not alternatives
            or (
                state.value("formula") is None
                and percent_comparator is not None
                and percent_comparator is not Comparator.EQUAL
            )
        )
        and (
            settled_formula_comparator is None
            or percent_comparator.value == settled_formula_comparator
            or explicit_operator_edit
            or explicit_numeric_edit
        )
        and not _lists_comparator_contracts(lowered)
    ):
        add("comparator", percent_comparator)
    if percent_threshold is not None and updates_primary_formula and (
        not alternatives or (state.value("formula") is None and has_percent_threshold)
    ):
        add("threshold", percent_threshold)
    exchange = _exchange_from_text(lowered)
    if exchange is not None and not alternatives and not has_open_decision:
        add("exchange", exchange)
    market_type = _market_type_from_text(lowered)
    if market_type is not None and not alternatives and not has_open_decision:
        add("market_type", market_type)
    if re.search(
        r"\b(?:no|without|remove|drop)\s+(?:a\s+|the\s+)?"
        r"(?:direction(?:al)?|trend|bias)\s+(?:filter|bias)\b",
        lowered,
    ) and not alternatives:
        add("direction", StrategyDirection.BOTH)

    forced_exclusions = extract_explicit_exclusions(normalized)
    inferred_quote = _quote_for_turn(report.symbols, state)
    if inferred_quote is not None:
        forced_exclusions = tuple(
            dict.fromkeys(
                (
                    *forced_exclusions,
                    *_explicit_bare_asset_exclusions(
                        normalized,
                        quote=inferred_quote,
                    ),
                )
            )
        )
    excluded_names = tuple(dict.fromkeys((*report.excluded_symbols, *forced_exclusions)))
    excluded = tuple(to_pair(symbol) for symbol in excluded_names)
    included = tuple(
        to_pair(symbol) for symbol in report.symbols if symbol not in set(excluded_names)
    )
    quote_assets = {pair.rsplit("/", 1)[1] for pair in (*included, *excluded) if "/" in pair}
    if len(quote_assets) == 1:
        add("quote_asset", quote_assets.pop())
    if included or excluded:
        patches.extend(
            _universe_patches(
                state,
                included=included,
                excluded=excluded,
                lowered=lowered,
                turn_number=turn_number,
                source_text=collapsed[:500],
            )
        )
    mechanics = tuple(item.text for item in report.trading_conditions)
    if mechanics:
        add(
            "mechanic_fragments",
            _merge_collection(
                state.value("mechanic_fragments"),
                mechanics,
                lowered=lowered,
            ),
        )
    formula_parts = [
        item.text
        for item in state_fragments
        if item.category in {"FORMULA", "OPERATOR", "THRESHOLD", "TRADING_MECHANIC"}
        and (
            item.threshold_is_percent
            or "%" in item.text
            or re.search(
                r"\b(?:percent|percentage|pct)_?(?:change|move)?\b",
                item.text,
                re.IGNORECASE,
            )
            or _contains_formula(item.text.casefold())
        )
    ]
    formula_text = " ".join(dict.fromkeys(formula_parts))
    if (
        formula_text
        and _contains_formula(formula_text.casefold())
        and (not alternatives or state.value("formula") is None)
    ):
        from ai_market_monitor.engine.formula_compiler import parse_percentage_formula

        default_timeframe = (
            roles.trigger
            or (report.timeframes[-1] if report.timeframes else None)
            or state.value("base_timeframe")
            or "15m"
        )
        default_direction = (
            report.direction or state.value("direction") or StrategyDirection.BOTH
        )
        parsed_candidates = [
            (part, parsed)
            for part in formula_parts
            if not re.search(
                r"\b(?:fail|fails|failed|reject(?:ed)?|skip(?:ped)?|"
                r"does\s+not\s+pass|doesn'?t\s+pass)\b",
                part,
                re.IGNORECASE,
            )
            and not re.search(
                r"\u0627\u0633\u062a\u0628\u0639\u0627\u062f|"
                r"\u0645\u0631\u0641\u0648\u0636|"
                r"\u0644\u0627\s+\u064a\u0646\u062c\u062d",
                part,
            )
            if (
                parsed := parse_percentage_formula(
                    part,
                    default_timeframe=default_timeframe,
                    default_direction=default_direction,
                )
            )
            is not None
        ]
        if not parsed_candidates:
            combined = parse_percentage_formula(
                formula_text,
                default_timeframe=default_timeframe,
                default_direction=default_direction,
            )
            parsed_candidates = [(formula_text, combined)] if combined is not None else []

        existing_formula = state.value("formula")
        explicit_formula_edit = explicit_numeric_edit
        selected: tuple[str, Any] | None = None
        if parsed_candidates and isinstance(existing_formula, dict) and not explicit_formula_edit:
            existing_threshold = float(existing_formula.get("threshold_percent") or 0.0)
            existing_direction = str(existing_formula.get("direction") or "")
            existing_comparator = str(existing_formula.get("comparator") or "")
            matching = [
                candidate
                for candidate in parsed_candidates
                if abs(candidate[1].threshold_percent - existing_threshold) < 1e-9
                and candidate[1].direction == existing_direction
                and candidate[1].comparator.value == existing_comparator
            ]
            selected = matching[-1] if matching else None
        elif (
            parsed_candidates
            and settled_formula_threshold is not None
            and settled_formula_comparator is not None
            and not explicit_formula_edit
        ):
            matching = [
                candidate
                for candidate in parsed_candidates
                if abs(candidate[1].threshold_percent - settled_formula_threshold) < 1e-9
                and candidate[1].comparator.value == settled_formula_comparator
            ]
            selected = matching[-1] if matching else None
        elif parsed_candidates:
            selected = parsed_candidates[-1]

        formula = selected[1] if selected is not None else None
        if formula is not None:
            formula_source = selected[0][:500] if selected is not None else formula.source_fragment
            add("comparator", formula.comparator)
            add("threshold", formula.threshold_percent)
            if formula.direction == "up":
                add("direction", StrategyDirection.LONG)
            elif formula.direction == "down":
                add("direction", StrategyDirection.SHORT)
            add(
                "formula",
                {
                    "formula": formula.formula,
                    "direction": formula.direction,
                    "comparator": formula.comparator.value,
                    "threshold_percent": formula.threshold_percent,
                    "timeframe": formula.timeframe,
                    "reference_timeframe": formula.reference_timeframe,
                    "reference_field": formula.reference_field,
                    "current_field": formula.current_field,
                    "lookback": formula.lookback,
                    "source_fragment": formula_source or formula.source_fragment,
                },
            )
        add(
            "formula_fragments",
            _merge_collection(
                state.value("formula_fragments"),
                tuple(formula_parts),
                lowered=lowered,
            ),
        )
    if _contains_boolean_group(lowered) and report.trading_conditions:
        add(
            "boolean_groups",
            _merge_collection(
                state.value("boolean_groups"),
                (collapsed,),
                lowered=lowered,
            ),
        )
    return tuple(patches)


def canonical_compiler_text(
    state: StrategyDraftState | dict[str, Any],
    *,
    fallback: str,
) -> str:
    """Render only the settled mechanics needed by the deterministic compiler."""

    resolved = state.resolved() if isinstance(state, StrategyDraftState) else state
    parts = [
        str(item).strip()
        for item in resolved.get("mechanic_fragments") or ()
        if str(item).strip() and classify_turn(str(item)).trading_conditions
    ]
    formula = resolved.get("formula")
    if isinstance(formula, dict):
        from ai_market_monitor.engine.formula_compiler import (
            compile_explicit_formula_group,
            parse_percentage_formula,
        )

        formula_timeframe = str(resolved.get("base_timeframe") or formula.get("timeframe") or "15m")
        explicit_groups = [
            str(item).strip()
            for item in resolved.get("boolean_groups") or ()
            if re.search(
                r"\bpercent(?:age)?_?move\b.*(?:>=|<=|gte|lte).*(?:>=|<=|gte|lte)|"
                r"\(\s*close\s*-\s*open\s*\)\s*/\s*open",
                str(item),
                re.IGNORECASE | re.DOTALL,
            )
            and compile_explicit_formula_group(
                str(item),
                timeframe=formula_timeframe,
            )
            is not None
        ]
        if explicit_groups:
            return explicit_groups[-1]
        direction_value = resolved.get("direction") or formula.get("direction") or "both"
        if isinstance(direction_value, StrategyDirection):
            formula_direction = direction_value
        elif str(direction_value) == "up":
            formula_direction = StrategyDirection.LONG
        elif str(direction_value) == "down":
            formula_direction = StrategyDirection.SHORT
        else:
            try:
                formula_direction = StrategyDirection(str(direction_value))
            except ValueError:
                formula_direction = StrategyDirection.BOTH
        parts = [
            part
            for part in parts
            if parse_percentage_formula(
                part,
                default_timeframe=formula_timeframe,
                default_direction=formula_direction,
            )
            is None
            and not _PROVIDER_REQUIREMENT_RE.search(part)
            and not _DIALOGUE_RESIDUE_RE.search(part)
            and not (
                _GENERIC_FORMULA_DISCUSSION_RE.search(part)
                and not _DISTINCT_MECHANIC_RE.search(part)
            )
        ]
        # A later one-field correction is authoritative over the formula snapshot
        # captured on the original turn. Otherwise `change only the threshold` leaves
        # compiler prose at the old value and forces the user to restate the rule.
        threshold = resolved.get("threshold", formula.get("threshold_percent"))
        parts.append(
            " ".join(
                (
                    "percentage_change",
                    str(resolved.get("comparator") or formula.get("comparator") or ""),
                    f"{threshold}%",
                    str(resolved.get("direction") or formula.get("direction") or ""),
                    str(formula.get("formula") or ""),
                    f"reference {formula.get('reference_field') or ''}",
                    f"current {formula.get('current_field') or ''}",
                    f"on {resolved.get('base_timeframe') or formula.get('timeframe') or ''}",
                )
            ).strip()
        )
    canonical = "\n".join(dict.fromkeys(part for part in parts if part))
    return canonical or repair_utf8_mojibake(fallback)


def revert_patches(
    state: StrategyDraftState,
    *,
    fields: tuple[StateField, ...] | None = None,
    turn: int | None = None,
    source_text: str = "",
) -> tuple[FieldPatch, ...]:
    """Restore the exact previous value of each named field.

    With no ``fields``, only the single most recently changed field is reverted —
    ``undo that`` means the last thing, not the whole conversation.
    """
    turn_number = state.turn + 1 if turn is None else turn
    if fields is None:
        if not state.patches:
            return ()
        last = state.patches[-1]
        fields = (last.field,)
    patches: list[FieldPatch] = []
    for name in fields:
        history = state.history(name)
        if not history:
            continue
        restored = history[-1].previous_value
        if restored is None and name not in COLLECTION_FIELDS:
            # There is no earlier value to restore. Say nothing rather than
            # inventing a default the user never chose.
            continue
        current = state.value(name)
        if _equal(current, restored):
            continue
        patches.append(
            FieldPatch(
                field=name,
                value=restored,
                turn=turn_number,
                source_text=source_text[:500],
                previous_value=current,
            )
        )
    return tuple(patches)


def _universe_patches(
    state: StrategyDraftState,
    *,
    included: tuple[str, ...],
    excluded: tuple[str, ...],
    lowered: str,
    turn_number: int,
    source_text: str,
) -> tuple[FieldPatch, ...]:
    """Resolve include/exclude together so the two can never disagree."""
    current_include = tuple(state.value("include_symbols") or ())
    current_exclude = tuple(state.value("exclude_symbols") or ())

    if _UNEXCLUDE_RE.search(lowered):
        # `stop excluding ETHUSDT` names the symbol next to exclusion wording, so the
        # fragment classifier reads it as an exclusion. The turn means the opposite:
        # every symbol it names is being lifted out of the exclusion set.
        lifted = {*included, *excluded}
        return _diff_patches(
            current_include,
            tuple(x for x in current_exclude if x not in lifted),
            current_include=current_include,
            current_exclude=current_exclude,
            turn_number=turn_number,
            source_text=source_text,
        )

    next_include = _merge_collection(current_include, included, lowered=lowered)
    next_exclude = _merge_collection(current_exclude, excluded, lowered=lowered)

    # Naming a symbol as an inclusion lifts an earlier exclusion of it: the newest
    # statement about a symbol is the one that counts.
    if included and any(x in next_exclude for x in included):
        next_exclude = tuple(x for x in next_exclude if x not in set(included))
    # An exclusion always removes the symbol from the universe, whenever it was added.
    if next_exclude:
        next_include = tuple(x for x in next_include if x not in set(next_exclude))

    return _diff_patches(
        next_include,
        next_exclude,
        current_include=current_include,
        current_exclude=current_exclude,
        turn_number=turn_number,
        source_text=source_text,
    )


def _diff_patches(
    next_include: tuple[str, ...],
    next_exclude: tuple[str, ...],
    *,
    current_include: tuple[str, ...],
    current_exclude: tuple[str, ...],
    turn_number: int,
    source_text: str,
) -> tuple[FieldPatch, ...]:
    """Emit a patch only for the universe field this turn actually changed."""
    patches: list[FieldPatch] = []
    if not _equal(current_include, next_include):
        patches.append(
            FieldPatch(
                field="include_symbols",
                value=next_include,
                turn=turn_number,
                source_text=source_text,
                previous_value=current_include,
            )
        )
    if not _equal(current_exclude, next_exclude):
        patches.append(
            FieldPatch(
                field="exclude_symbols",
                value=next_exclude,
                turn=turn_number,
                source_text=source_text,
                previous_value=current_exclude,
            )
        )
    return tuple(patches)


def _merge_collection(
    current: Any,
    incoming: tuple[str, ...],
    *,
    lowered: str,
) -> tuple[str, ...]:
    """Add to the set, unless the wording narrows it to exactly what was named."""
    if not incoming:
        return tuple(current or ())
    narrowing = any(marker in lowered for marker in _REPLACEMENT_MARKERS) and not any(
        marker in lowered for marker in _ADDITIVE_MARKERS
    )
    if narrowing:
        return _unique(incoming)
    return _unique((*(current or ()), *incoming))


def _unique(values: Any) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return tuple(seen)


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, tuple | list) or isinstance(right, tuple | list):
        return tuple(left or ()) == tuple(right or ())
    return left == right


def _encode(value: Any) -> Any:
    if isinstance(value, StrategyDirection | Comparator):
        return value.value
    if isinstance(value, tuple | list):
        return [_encode(item) for item in value]
    return value


def _decode(name: StateField, value: Any) -> Any:
    if value is None:
        return () if name in COLLECTION_FIELDS else None
    if name == "direction":
        return StrategyDirection(value) if not isinstance(value, StrategyDirection) else value
    if name == "comparator":
        return Comparator(value) if not isinstance(value, Comparator) else value
    if name in COLLECTION_FIELDS:
        return tuple(str(item) for item in value)
    if name == "threshold":
        return float(value)
    if name == "formula":
        return dict(value) if isinstance(value, dict) else None
    return value


def _exchange_from_text(text: str) -> str | None:
    for exchange in ("binance", "bybit", "okx", "kucoin"):
        if re.search(rf"\b{exchange}\b", text):
            return exchange
    return None


def _quote_for_turn(
    symbols: tuple[str, ...],
    state: StrategyDraftState,
) -> str | None:
    quotes = {
        pair.rsplit("/", 1)[1] for pair in (to_pair(symbol) for symbol in symbols) if "/" in pair
    }
    if len(quotes) == 1:
        return quotes.pop()
    existing = state.value("quote_asset")
    return str(existing) if isinstance(existing, str) and existing else None


def _explicit_bare_asset_exclusions(text: str, *, quote: str) -> tuple[str, ...]:
    """Resolve `no ETH` against the already authoritative quote asset."""

    reserved = {
        "AND",
        "BEAR",
        "BEARISH",
        "BOTH",
        "CONTEXT",
        "DIRECTION",
        "DOWN",
        "EQ",
        "FILTER",
        "GT",
        "GTE",
        "LONG",
        "LT",
        "LTE",
        "NOT",
        "OPERATOR",
        "OR",
        "SHORT",
        "STATUS",
        "THRESHOLD",
        "TRIGGER",
        "UP",
        "WATCHLIST",
        "WITH",
        "WITHOUT",
    }
    patterns = (
        r"(?<!yes/)\bno\s+[*_`]*(?P<base>[A-Z][A-Z0-9]{1,9})\b",
        r"\b(?:exclude|excluding|exclusions?)\s*:?\s*(?:only\s+)?"
        r"[*_`]*(?P<base>[A-Z][A-Z0-9]{1,9})\b",
        r"\b(?P<base>[A-Z][A-Z0-9]{1,9})\b"
        r"\s+(?:is\s+)?(?:excluded|not\s+included)\b",
    )
    results: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            base = match.group("base")
            if base in reserved or base.endswith(quote):
                continue
            symbol = f"{base}{quote}"
            if symbol not in results:
                results.append(symbol)
    return tuple(results)


def _market_type_from_text(text: str) -> str | None:
    if re.search(r"\b(?:perpetuals?|perps?|futures|future\s+contracts?)\b", text):
        return "futures"
    if re.search(r"\bspot\b", text):
        return "spot"
    return None


def _contains_formula(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:open[\s-]*to[\s-]*close|close[\s-]*to[\s-]*close|"
            r"percent_move|percentage_move|move_pct|pct_move|"
            r"percent_change|percentage_change|pct_change)\b|"
            r"\(\s*(?:\d{1,2}\s*(?:m|h|d|w)\s+)?"
            r"(?:open|close|high|low)\s*-\s*"
            r"(?:\d{1,2}\s*(?:m|h|d|w)\s+)?"
            r"(?:open|close|high|low)\s*\)\s*/",
            text,
        )
        or (
            re.search(r"[-+]?\d+(?:\.\d+)?\s*%", text)
            and re.search(
                r"\b(?:bullish|bearish|long|short|move|drop|rise|gain|"
                r"threshold_percent|bearish_move|bullish_move)\b",
                text,
            )
            and (
                detect_comparator(text) is not None
                or re.search(
                    r"\b(?:operator|comparator)\s*[:=]?\s*(?:gte|lte|gt|lt|eq)\b",
                    text,
                )
            )
        )
    )


def _contains_boolean_group(text: str) -> bool:
    return bool(
        re.search(r"\b(?:and|or|not)\b", text)
        and ("(" in text or re.search(r"\b(?:and|or|not)\b", text))
    )


def _offers_unresolved_alternatives(text: str) -> bool:
    """Questions listing candidate definitions do not change the settled draft."""

    return bool(
        re.search(
            r"\b(?:pick|choose|which|do\s+you\s+mean|is\s+it)\b.{0,180}\b(?:or|option)\b|"
            r"\boption\s*(?:\(|:)?\s*[1-9a-d]\b|"
            r"\([a-d]\).{0,120}\([a-d]\)",
            text,
        )
    )


def _lists_comparator_contracts(text: str) -> bool:
    """A glossary of operators explains semantics; it does not edit the trigger."""

    vocabulary = {
        "above": r"\babove\b|(?<![<>=])>(?!=)",
        "below": r"\bbelow\b|(?<![<>=])<(?!=)",
        "at_least": r"\bat\s+least\b|>=|\bgte\b",
        "at_most": r"\bat\s+most\b|<=|\blte\b",
        "crosses": r"\bcross(?:es|ed|ing)?\b",
        "sweeps": r"\bsweep(?:s|ed|ing)?\b",
    }
    return sum(bool(re.search(pattern, text)) for pattern in vocabulary.values()) >= 3


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value if str(item).strip())
