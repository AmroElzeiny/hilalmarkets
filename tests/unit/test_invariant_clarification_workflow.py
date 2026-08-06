"""Invariants for a multi-step question: one step, one identity, nothing lost.

These assert rules across whole families rather than the reported transcripts:

* an authorized workflow step always survives the patch that writes it;
* the question shown is always the question stored;
* every displayed option is executable, and every executable value is answerable;
* nothing but an explicit cancellation may clear an open question.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.active_question import (
    AnswerDomain,
    AnswerOutcome,
    canonical_values,
    display_options,
    domain_for_field,
    labels_for,
    normalize_answer_text,
    resolve_active_answer,
)
from ai_market_monitor.engine.conversation_language import (
    ConversationLanguage,
    localized,
    translation_coverage,
)
from ai_market_monitor.engine.draft_diff import diff_drafts, is_material
from ai_market_monitor.engine.requirement_state import _coalesced_unresolved
from ai_market_monitor.engine.strategy_draft_v2 import apply_strategy_patch
from ai_market_monitor.schemas.setup_agent import (
    PendingClarificationWorkflow,
    SetupConversationContext,
)
from ai_market_monitor.schemas.setup_authorization import ClarificationContract
from ai_market_monitor.schemas.strategy_draft_v2 import (
    StrategyDraftV2,
    StrategyPatch,
    UnresolvedFieldV2,
)
from ai_market_monitor.schemas.timeframes import (
    COMMON_TIMEFRAMES,
    ORDERED_TIMEFRAMES,
    SUPPORTED_TIMEFRAMES,
    normalize_timeframe_alias,
)

OPTION_DOMAINS = [
    AnswerDomain.UNIVERSE_MODE,
    AnswerDomain.REFERENCE_POINT,
    AnswerDomain.COMPARATOR,
    AnswerDomain.MOVEMENT_DIRECTION,
    AnswerDomain.TIMEFRAME,
    AnswerDomain.SCAN_WINDOW,
]


def _step(field_name: str, *, question: str, options: list[str]) -> UnresolvedFieldV2:
    return UnresolvedFieldV2(
        unresolved_id="supported_abc",
        semantic_object_id="object_abc",
        source_turn_id="turn-1",
        source_fragment="Inform me once any coin increases 5%",
        target_type="condition_creation",
        expected_answer_schema={"type": "string", "enum": options},
        missing_slots=[field_name],
        allowed_options=options,
        question=question,
        reason="One user-controlled choice is still required for this supported rule.",
    )


STEP_ONE = _step(
    "trigger_timeframe",
    question="Which candle period should I use: 1h, 4h, or 1d?",
    options=list(COMMON_TIMEFRAMES),
)
STEP_TWO = _step(
    "formula",
    question="Measure the move from that candle's open or the previous close?",
    options=["Candle open", "Previous candle close"],
)


def _draft(*items: UnresolvedFieldV2) -> StrategyDraftV2:
    return StrategyDraftV2.model_validate(
        StrategyDraftV2()
        .model_copy(
            update={
                "unresolved_fields": list(items),
                "executable_hash": "",
                "workflow_state_hash": "",
            }
        )
        .model_dump(mode="json")
    )


# ---------------------------------------------------------------------------------
# A workflow step survives the patch that writes it
# ---------------------------------------------------------------------------------


def test_advancing_a_step_is_never_deleted_by_its_own_patch() -> None:
    """`update_unresolved` closes and rewrites one key. Closing ran last, and the new
    step was deleted the instant it was written."""

    outcome = apply_strategy_patch(
        _draft(STEP_ONE),
        StrategyPatch(
            source_turn_id="turn-2",
            unresolved_references=[STEP_TWO],
            remove_unresolved_keys=[STEP_ONE.unresolved_id],
        ),
    )

    assert len(outcome.draft.unresolved_fields) == 1
    assert outcome.draft.unresolved_fields[0].question == STEP_TWO.question


def test_a_genuine_close_still_removes_the_item() -> None:
    outcome = apply_strategy_patch(
        _draft(STEP_ONE),
        StrategyPatch(
            source_turn_id="turn-2",
            remove_unresolved_keys=[STEP_ONE.unresolved_id],
        ),
    )

    assert outcome.draft.unresolved_fields == []


def test_a_step_that_moved_on_is_a_visible_change() -> None:
    """Comparing keys alone made an advance look like nothing had happened, so the
    operation that advanced it was recorded as a no-op."""

    changes = diff_drafts(_draft(STEP_ONE), _draft(STEP_TWO))

    assert [item.kind for item in changes] == ["unresolved_advanced"]
    assert not is_material(changes), "answering a question is not a change to what fires"


def test_the_newest_record_wins_when_both_sides_hold_one() -> None:
    grouped = _coalesced_unresolved([STEP_ONE], [STEP_TWO])

    assert len(grouped) == 1
    representative, _aliases = grouped[0]
    assert representative.question == STEP_TWO.question


def test_a_step_this_turn_deleted_still_comes_back() -> None:
    """Fail closed: a removed blocker returns unless it was really answered."""

    grouped = _coalesced_unresolved([STEP_ONE], [])

    assert [item.question for item, _ in grouped] == [STEP_ONE.question]


# ---------------------------------------------------------------------------------
# One step, one identity
# ---------------------------------------------------------------------------------


def _workflow(field_name: str = "trigger_timeframe") -> PendingClarificationWorkflow:
    skeleton = PendingClarificationWorkflow(
        workflow_id="supported_abc",
        workflow_kind="supported_rule",
        question_id="pending",
        current_field=field_name,
        remaining_fields=["reference_point"],
    )
    return skeleton.model_copy(
        update={"question_id": skeleton.step_question_id(field_name, 0)}
    )


def _contract(workflow: PendingClarificationWorkflow) -> ClarificationContract:
    return ClarificationContract(
        question_id=workflow.question_id,
        question="Which candle period should I use?",
        reason="One user-controlled choice is still required.",
        target_type="condition_creation",
        target_field=workflow.current_field,
        expected_answer_schema='{"type":"string"}',
        workflow_id=workflow.workflow_id,
        step_revision=workflow.step_revision,
    )


def test_a_shown_question_must_be_the_stored_step() -> None:
    workflow = _workflow()
    stale = _contract(workflow)
    advanced = workflow.accepting("1h", evidence="1h")

    with pytest.raises(ValueError, match="not the pending workflow's current step"):
        SetupConversationContext().with_question(stale, workflow=advanced)


def test_a_stored_workflow_cannot_exist_without_its_question() -> None:
    with pytest.raises(ValueError, match="must have its current question"):
        SetupConversationContext(pending_workflow=_workflow())


def test_every_step_gets_its_own_question_identity() -> None:
    workflow = _workflow()
    seen = {workflow.question_id}
    for value in ("1h", "candle_open"):
        workflow = workflow.accepting(value, evidence=value)
        assert workflow.question_id not in seen, "a step reused an earlier question id"
        seen.add(workflow.question_id)


def test_accepting_a_value_never_forgets_an_earlier_one() -> None:
    workflow = _workflow().accepting("1h", evidence="1h")
    workflow = workflow.accepting("candle_open", evidence="Candle open")

    assert workflow.accepted_values == {
        "trigger_timeframe": "1h",
        "reference_point": "candle_open",
    }
    assert workflow.source_evidence == ["1h", "Candle open"]


def test_a_workflow_cannot_offer_what_it_cannot_execute() -> None:
    with pytest.raises(ValueError, match="not executable"):
        PendingClarificationWorkflow(
            workflow_id="w",
            workflow_kind="supported_rule",
            question_id="q",
            current_field="trigger_timeframe",
            canonical_values=["1h", "4h"],
            offered_values=["1h", "30m"],
        )


def test_clearing_a_question_clears_its_workflow_too() -> None:
    workflow = _workflow()
    conversation = SetupConversationContext().with_question(
        _contract(workflow), workflow=workflow
    )

    cleared = conversation.cleared_question()

    assert cleared.pending_workflow is None
    assert cleared.active_question is None


# ---------------------------------------------------------------------------------
# One option authority
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize("domain", OPTION_DOMAINS)
def test_every_displayed_option_is_executable(domain: AnswerDomain) -> None:
    assert set(display_options(domain)) <= set(canonical_values(domain))


@pytest.mark.parametrize("domain", OPTION_DOMAINS)
@pytest.mark.parametrize("language", list(ConversationLanguage))
def test_every_displayed_option_has_words_and_answers_to_them(
    domain: AnswerDomain, language: ConversationLanguage
) -> None:
    shown = display_options(domain)
    labels = labels_for(domain, shown, language)

    assert set(labels) == set(shown), "an option lost its wording"
    for value, label in labels.items():
        assert label, f"{domain} {value} has an empty label"
        resolution = resolve_active_answer(
            label,
            domain=domain,
            allowed_options=canonical_values(domain),
            offered_values=shown,
            display_labels=labels,
        )
        assert resolution.outcome is AnswerOutcome.RESOLVED
        assert resolution.canonical_value == value


@pytest.mark.parametrize("timeframe", sorted(SUPPORTED_TIMEFRAMES))
def test_every_executable_period_is_reachable_in_words(timeframe: str) -> None:
    assert normalize_timeframe_alias(timeframe) == timeframe
    assert resolve_active_answer(
        timeframe, domain=AnswerDomain.TIMEFRAME
    ).canonical_value == timeframe


def test_the_period_shortlist_comes_from_the_registry() -> None:
    assert set(COMMON_TIMEFRAMES) <= set(ORDERED_TIMEFRAMES)


@pytest.mark.parametrize(
    ("field_name", "domain"),
    [
        ("trigger_timeframe", AnswerDomain.TIMEFRAME),
        ("measurement_window", AnswerDomain.TIMEFRAME),
        ("reference_point", AnswerDomain.REFERENCE_POINT),
        ("comparator", AnswerDomain.COMPARATOR),
        ("operator", AnswerDomain.COMPARATOR),
        ("threshold", AnswerDomain.PERCENT),
        ("sharia_policy.universe_mode", AnswerDomain.UNIVERSE_MODE),
        ("scan_window", AnswerDomain.SCAN_WINDOW),
    ],
)
def test_each_canonical_field_knows_what_kind_of_answer_it_wants(
    field_name: str, domain: AnswerDomain
) -> None:
    assert domain_for_field(field_name) is domain


# ---------------------------------------------------------------------------------
# Tolerance that never guesses
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize("domain", OPTION_DOMAINS)
def test_no_decoration_of_an_option_changes_its_meaning(domain: AnswerDomain) -> None:
    for value in display_options(domain):
        baseline = resolve_active_answer(value, domain=domain)
        for decorated in (
            value.upper(),
            f" {value} ",
            f"{value}.",
            f"{value}!",
            f"  {value}  ",
            value.replace("_", "-"),
        ):
            resolution = resolve_active_answer(decorated, domain=domain)
            assert resolution.outcome is baseline.outcome, f"{domain} {decorated!r}"
            assert resolution.canonical_value == baseline.canonical_value


@pytest.mark.parametrize("domain", OPTION_DOMAINS)
def test_nothing_outside_the_domain_is_ever_resolved(domain: AnswerDomain) -> None:
    for nonsense in ("purple bananas", "zzzzzzzz", "!!!!", "the third one maybe"):
        resolution = resolve_active_answer(nonsense, domain=domain)
        assert resolution.outcome is not AnswerOutcome.RESOLVED, f"{domain} {nonsense}"


@pytest.mark.parametrize("domain", OPTION_DOMAINS)
def test_an_unusable_answer_always_keeps_the_question(domain: AnswerDomain) -> None:
    resolution = resolve_active_answer("qqqq zzzz", domain=domain)
    assert resolution.keeps_the_question
    assert not resolution.stores_a_value


def test_normalisation_is_stable_under_repetition() -> None:
    for text in ("All Eligible Spot Assets!", " 1-Minute ", "كل العملات", "Mes Favoris"):
        once = normalize_answer_text(text)
        assert normalize_answer_text(once) == once


# ---------------------------------------------------------------------------------
# Wording exists everywhere it is used
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "ask.confirm_candidate",
        "ask.repeat_options",
        "ask.unsupported_value",
        "status.question_cancelled",
    ],
)
@pytest.mark.parametrize("language", list(ConversationLanguage))
def test_every_new_sentence_exists_in_every_language(
    key: str, language: ConversationLanguage
) -> None:
    rendered = localized(key, language, options="A, B", candidate="1h")

    assert rendered
    assert "{" not in rendered, "an unfilled placeholder reached the reader"


def test_no_sentence_is_missing_a_translation() -> None:
    assert translation_coverage() == {}
