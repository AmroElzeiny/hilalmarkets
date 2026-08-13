"""Evaluate the six conversation invariants against the real product engine.

This module is the only part of the adversarial QA harness that imports
``ai_market_monitor``. It cannot live in ``src/hm_oi`` — ``scripts/check_oi_boundary.py``
fails the build if the engineering tooling imports the product, which is what keeps an
AGPL shell-running assistant out of the shipped code. It lives here, beside the tests
that use it, because ``tests/`` is not scanned by that gate and is covered by
``ruff check src tests scripts``.

**Every check runs on the canonical-state path.** ``engine/strategy_state.patches_for_turn``
is what actually decides whether a turn changes the monitored rules; the joined chat text
is not. An earlier probe measured on the joined text and reported three times as many
problems as were real, so the layer is named here on purpose and not varied.

**The vocabularies are borrowed, never re-written.** Direction words come from
``engine/turn_fragments.detect_direction`` and timeframes from ``extract_timeframes``. A
second word list in a QA tool would drift from the product's and would then report the
drift as a product defect, which is the exact failure ``CLAUDE.md`` describes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_market_monitor.core.product_boundaries import (
    BOUNDARY_REGISTRY,
    SupportState,
    boundary_for,
)
from ai_market_monitor.engine.capability_resolver import CapabilityResolver
from ai_market_monitor.engine.strategy_state import StrategyDraftState, patches_for_turn
from ai_market_monitor.engine.turn_fragments import classify_turn as classify_fragments
from ai_market_monitor.engine.turn_fragments import (
    detect_direction,
    extract_timeframes,
    is_approval_instruction,
)
from hm_oi.qa_corpus import (
    AdversarialCase,
    ConversationInvariant,
    InvariantResult,
    InvariantVerdict,
)

#: Fields that decide what the product actually watches. A patch to one of these changes
#: the customer's monitor.
#:
#: ``mechanic_fragments``, ``formula_fragments`` and ``boolean_groups`` are deliberately
#: absent: they are the working list of phrases still to be resolved, not a monitored
#: value. Counting them would make every turn look like a mutation and would bury the
#: cases where a real setting moved.
MONITORED_FIELDS: frozenset[str] = frozenset(
    {
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
    }
)

#: Approval states that mean the customer has authorised something.
APPROVED_STATES: frozenset[str] = frozenset({"APPROVED", "COMPILED", "ACTIVATED"})

#: Fields whose whole job is to hold the things the trader said *no* to. A rejected
#: value appearing here is the correct outcome, not a survival.
#:
#: Without this the checker reported "Not BTCUSDT." as a violation because BTC/USDT was
#: findable in the state — while the state was in fact recording the exclusion exactly as
#: asked. That is the one case in the whole set where the product gets a rejection right,
#: so reporting it would have buried the twelve cases where it does not.
NEGATION_DESTINATION_FIELDS: frozenset[str] = frozenset({"exclude_symbols"})


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """What one case did to the draft, measured once and reused by every check."""

    case: AdversarialCase
    before: StrategyDraftState
    after: StrategyDraftState
    patches: tuple[Any, ...]

    @property
    def monitored_patches(self) -> tuple[Any, ...]:
        return tuple(patch for patch in self.patches if patch.field in MONITORED_FIELDS)

    def value(self, field: str) -> Any:
        return self.after.value(field)  # type: ignore[arg-type]

    def changed_fields(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(patch.field for patch in self.monitored_patches))


def replay(case: AdversarialCase) -> TurnOutcome:
    """Build the state the history leaves behind, then apply the attack turn."""

    state = StrategyDraftState()
    for index, earlier in enumerate(case.history, start=1):
        state = state.apply(patches_for_turn(earlier, state, turn=index))
    patches = patches_for_turn(case.prompt, state, turn=len(case.history) + 1)
    return TurnOutcome(
        case=case, before=state, after=state.apply(patches), patches=tuple(patches)
    )


def _render(value: Any) -> str:
    """A value as it would be written, for grounding comparisons."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).casefold()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (list, tuple)):
        return " ".join(_render(item) for item in value)
    return str(getattr(value, "value", value)).casefold()


# ---------------------------------------------------------------------------------
# The six invariants.
# ---------------------------------------------------------------------------------


def check_social_text_is_never_executable(outcome: TurnOutcome) -> InvariantResult:
    """Greetings, thanks and praise contribute nothing to what is monitored.

    Two separate checks, because either alone is escapable. The first reads the whole
    turn and asks whether a fragment the classifier itself called conversation was
    allowed to contribute. The second takes each social span on its own and asks whether
    it can move a monitored field — which catches a span that is only harmless because
    something else in the turn happened to overwrite it.
    """

    case = outcome.case
    if not case.social_spans:
        return InvariantResult(
            case.case_id,
            ConversationInvariant.SOCIAL_TEXT_IS_NEVER_EXECUTABLE,
            InvariantVerdict.NOT_APPLICABLE,
            "This case carries no social text.",
        )

    report = classify_fragments(case.prompt)
    leaking = [
        fragment
        for fragment in report.fragments
        if fragment.kind == "conversational" and fragment.contributes_strategy_state
    ]

    standalone: list[str] = []
    for span in case.social_spans:
        alone = patches_for_turn(span, StrategyDraftState(), turn=1)
        moved = [patch.field for patch in alone if patch.field in MONITORED_FIELDS]
        if moved:
            standalone.append(f"{span!r} alone patched {', '.join(sorted(set(moved)))}")

    if leaking or standalone:
        parts = []
        if leaking:
            parts.append(
                "conversation fragments contributed state: "
                + "; ".join(repr(item.text) for item in leaking)
            )
        parts.extend(standalone)
        return InvariantResult(
            case.case_id,
            ConversationInvariant.SOCIAL_TEXT_IS_NEVER_EXECUTABLE,
            InvariantVerdict.VIOLATED,
            " | ".join(parts),
        )

    return InvariantResult(
        case.case_id,
        ConversationInvariant.SOCIAL_TEXT_IS_NEVER_EXECUTABLE,
        InvariantVerdict.HOLDS,
        f"{len(case.social_spans)} social span(s) changed no monitored field.",
    )


def check_a_question_is_never_a_mutation(outcome: TurnOutcome) -> InvariantResult:
    """A turn that only asks something leaves the monitored rules exactly as they were."""

    case = outcome.case
    if not case.is_question_only:
        return InvariantResult(
            case.case_id,
            ConversationInvariant.A_QUESTION_IS_NEVER_A_MUTATION,
            InvariantVerdict.NOT_APPLICABLE,
            "This turn carries an instruction, so it is allowed to change things.",
        )

    moved = outcome.monitored_patches
    if moved:
        detail = "; ".join(
            f"{patch.field}: {patch.previous_value!r} -> {patch.value!r}" for patch in moved
        )
        return InvariantResult(
            case.case_id,
            ConversationInvariant.A_QUESTION_IS_NEVER_A_MUTATION,
            InvariantVerdict.VIOLATED,
            f"A question changed what is monitored - {detail}",
        )

    return InvariantResult(
        case.case_id,
        ConversationInvariant.A_QUESTION_IS_NEVER_A_MUTATION,
        InvariantVerdict.HOLDS,
        "No monitored field moved.",
    )


def check_a_correction_targets_the_correct_object(outcome: TurnOutcome) -> InvariantResult:
    """A value the trader named only to reject must not survive the turn.

    This is the safety half. Whether the correction *landed* is the liveness half, and it
    is reported in ``liveness_note`` rather than asserted: applying a correction is
    partly the planner's job, and failing the suite for the planner's behaviour would
    make the suite useless as a safety signal.
    """

    case = outcome.case
    if not case.negated_values:
        return InvariantResult(
            case.case_id,
            ConversationInvariant.A_CORRECTION_TARGETS_THE_CORRECT_OBJECT,
            InvariantVerdict.NOT_APPLICABLE,
            "This turn rejects no value by name.",
        )

    survivors: list[str] = []
    for rejected in case.negated_values:
        wanted = _render(rejected)
        for field in sorted(MONITORED_FIELDS - NEGATION_DESTINATION_FIELDS):
            held = _render(outcome.value(field))
            if not held:
                continue
            if held == wanted or wanted in held.split():
                survivors.append(f"{field} still holds {rejected!r}")

    target = case.correction_target
    landed = target in outcome.changed_fields() if target else False
    liveness = (
        f"the correction to {target} landed"
        if landed
        else f"the correction to {target} did not land at the deterministic layer; "
        "the planner owns applying it"
        if target
        else ""
    )

    if survivors:
        return InvariantResult(
            case.case_id,
            ConversationInvariant.A_CORRECTION_TARGETS_THE_CORRECT_OBJECT,
            InvariantVerdict.VIOLATED,
            "A rejected value survived: " + "; ".join(survivors),
            liveness,
        )

    return InvariantResult(
        case.case_id,
        ConversationInvariant.A_CORRECTION_TARGETS_THE_CORRECT_OBJECT,
        InvariantVerdict.HOLDS,
        f"None of {list(case.negated_values)} is held by any monitored field.",
        liveness,
    )


def check_references_resolve_correctly(outcome: TurnOutcome) -> InvariantResult:
    """A reference never puts a value into the draft that nobody in the conversation said.

    The rule is the grounding rule: every value the turn writes must be findable in the
    words that were actually typed. Direction is checked through the product's own
    ``detect_direction`` and timeframes through ``extract_timeframes``, so this shares one
    vocabulary with the compiler instead of inventing a second.
    """

    case = outcome.case
    if case.reference_resolves_to is None:
        return InvariantResult(
            case.case_id,
            ConversationInvariant.REFERENCES_RESOLVE_CORRECTLY,
            InvariantVerdict.NOT_APPLICABLE,
            "This turn contains no reference to an earlier object.",
        )

    conversation = " ".join(case.turns)
    lowered = conversation.casefold()
    stated_timeframes = set(extract_timeframes(conversation))
    stated_direction = detect_direction(conversation)

    ungrounded: list[str] = []
    for patch in outcome.monitored_patches:
        rendered = _render(patch.value)
        if not rendered:
            continue
        if patch.field == "direction":
            if stated_direction is None or _render(stated_direction) != rendered:
                ungrounded.append(f"direction={patch.value!r} is in nobody's words")
            continue
        if patch.field in {"base_timeframe", "context_timeframes"}:
            wanted = {rendered} if isinstance(patch.value, str) else set(rendered.split())
            missing = wanted - {item.casefold() for item in stated_timeframes}
            if missing:
                ungrounded.append(f"{patch.field}={sorted(missing)} was never stated")
            continue
        for token in rendered.split():
            bare = token.strip("(),'\"[]")
            if bare and bare not in lowered and bare.replace("/", "") not in lowered:
                ungrounded.append(f"{patch.field} carries {bare!r}, which nobody typed")

    target = case.reference_resolves_to
    landed = target in outcome.changed_fields()
    liveness = (
        f"the reference resolved and {target} moved"
        if landed
        else f"{target} did not move; the reference was left for the planner or a "
        "clarifying question"
    )

    if ungrounded:
        return InvariantResult(
            case.case_id,
            ConversationInvariant.REFERENCES_RESOLVE_CORRECTLY,
            InvariantVerdict.VIOLATED,
            "A reference produced an ungrounded value: " + "; ".join(ungrounded),
            liveness,
        )

    return InvariantResult(
        case.case_id,
        ConversationInvariant.REFERENCES_RESOLVE_CORRECTLY,
        InvariantVerdict.HOLDS,
        "Every value written by this turn appears in the conversation.",
        liveness,
    )


def check_unsupported_concepts_stay_unsupported(outcome: TurnOutcome) -> InvariantResult:
    """A capability the registry says the product lacks never becomes one it has.

    Three things are checked. The registry still marks the concept unsupported; the
    refusal object carries no substitute; and the turn resolves no capability that the
    registry marks unsupported. The third is the one an attack can actually move.
    """

    case = outcome.case
    if not case.unsupported_concepts:
        return InvariantResult(
            case.case_id,
            ConversationInvariant.UNSUPPORTED_CONCEPTS_STAY_UNSUPPORTED,
            InvariantVerdict.NOT_APPLICABLE,
            "This turn asks for nothing the product lacks.",
        )

    problems: list[str] = []
    for key in case.unsupported_concepts:
        try:
            entry = boundary_for(key)
        except KeyError:
            problems.append(f"{key!r} is not in the boundary registry at all")
            continue
        if entry.is_supported:
            problems.append(f"{key!r} is now marked supported")

    unsupported_keys = {
        entry.key for entry in BOUNDARY_REGISTRY if entry.support is not SupportState.SUPPORTED
    }
    resolved = set(CapabilityResolver().resolve_prompt(case.prompt).candidate_keys)
    crossed = resolved & unsupported_keys
    if crossed:
        problems.append(
            f"the resolver offered {sorted(crossed)}, which the registry says the "
            "product does not have"
        )

    liveness = (
        f"the draft still took {sorted(outcome.changed_fields())} from this turn"
        if outcome.changed_fields()
        else "the turn changed no monitored field"
    )

    if problems:
        return InvariantResult(
            case.case_id,
            ConversationInvariant.UNSUPPORTED_CONCEPTS_STAY_UNSUPPORTED,
            InvariantVerdict.VIOLATED,
            "; ".join(problems),
            liveness,
        )

    return InvariantResult(
        case.case_id,
        ConversationInvariant.UNSUPPORTED_CONCEPTS_STAY_UNSUPPORTED,
        InvariantVerdict.HOLDS,
        f"{list(case.unsupported_concepts)} remain unsupported and unresolvable.",
        liveness,
    )


def check_approval_is_never_inferred(outcome: TurnOutcome) -> InvariantResult:
    """Nothing in this corpus grants approval, so nothing in it may be read as approval.

    Applies to every case. It is the one invariant with no ``NOT_APPLICABLE`` branch,
    because "could this be mistaken for approval?" is a question worth asking of a
    greeting, a complaint and a correction alike.
    """

    case = outcome.case
    problems: list[str] = []

    if case.grants_approval:
        problems.append(
            "this case claims to grant approval, which no case in an adversarial corpus "
            "may do - the corpus is malformed"
        )

    report = classify_fragments(case.prompt)
    if report.is_approval:
        problems.append("classify_turn read the turn as approval")
    if is_approval_instruction(case.prompt):
        problems.append("is_approval_instruction read the turn as approval")

    approving = [
        fragment.text for fragment in report.fragments if fragment.kind == "approval"
    ]
    if approving:
        problems.append(f"fragments classified as approval: {approving}")

    if str(outcome.after.approval_state) in APPROVED_STATES:
        problems.append(f"the draft reached approval_state={outcome.after.approval_state}")

    if problems:
        return InvariantResult(
            case.case_id,
            ConversationInvariant.APPROVAL_IS_NEVER_INFERRED,
            InvariantVerdict.VIOLATED,
            "; ".join(problems),
        )

    return InvariantResult(
        case.case_id,
        ConversationInvariant.APPROVAL_IS_NEVER_INFERRED,
        InvariantVerdict.HOLDS,
        f"Not approval; draft stayed at approval_state={outcome.after.approval_state}.",
    )


#: Every check, in the order they are reported. Assembled once so a caller cannot
#: evaluate a subset by accident and then report six green invariants.
CHECKS = (
    check_social_text_is_never_executable,
    check_a_question_is_never_a_mutation,
    check_a_correction_targets_the_correct_object,
    check_references_resolve_correctly,
    check_unsupported_concepts_stay_unsupported,
    check_approval_is_never_inferred,
)


def evaluate(case: AdversarialCase) -> tuple[InvariantResult, ...]:
    """Run all six invariants against one case."""

    outcome = replay(case)
    return tuple(check(outcome) for check in CHECKS)
