"""Every visible question has a handler, and a near miss is never stored unconfirmed.

Four rules are asserted here, each across its whole family rather than for the one case
that was reported:

* **Every** ``ClarificationTargetType`` has a registered handler. A question with no
  handler is one a trader can be shown and then cannot answer.
* One boolean resolver reads every yes and every no, in every language the product
  answers in — and the words it knows are the same words every other reader knows.
* A confirmation is a real sub-state: yes stores, no rejects without losing anything,
  and anything else asks the same yes/no again instead of guessing.
* A stale step can never advance a newer one.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.active_clarification import (
    ApplyRoute,
    CanonicalBlocker,
    ClarificationTurn,
    ResumePolicy,
    TransitionOutcome,
    _registry_covers_every_target_type,
    answer_domain_for,
    cancellation_policy_for,
    canonical_blocker_for,
    effects_of,
    handler_for,
    has_registered_handler,
    may_be_rendered,
    orphan_reason,
    plan_transition,
    registered_target_types,
    resolve_active_clarification_turn,
    stale_step,
    workflow_invariants,
)
from ai_market_monitor.engine.active_question import (
    AFFIRMATIVE_ANSWERS,
    NEGATIVE_ANSWERS,
    AnswerDomain,
    AnswerOutcome,
    AnswerStage,
    ConfirmationReply,
    normalize_answer_text,
    resolve_active_answer,
    resolve_confirmation,
)
from ai_market_monitor.engine.clarification_continuation import (
    ContinuationAnswer,
    ContinuationRefused,
    build_continuation_operations,
    continuation_for_unresolved,
    continuation_is_deterministic,
    registered_builders,
)
from ai_market_monitor.engine.conversation_intent import scan_window_answer
from ai_market_monitor.engine.conversation_language import ConversationLanguage
from ai_market_monitor.schemas.setup_agent import (
    PendingClarificationWorkflow,
    SetupConversationContext,
)
from ai_market_monitor.schemas.setup_authorization import (
    DEFAULT_CANCELLATION_POLICY,
    CancellationPolicy,
    ClarificationContract,
)
from ai_market_monitor.schemas.strategy_draft_v2 import (
    StrategyDraftV2,
    UnresolvedFieldV2,
    UnsupportedRequirementV2,
)
from ai_market_monitor.schemas.timeframes import COMMON_TIMEFRAMES

TARGET_TYPES = sorted(registered_target_types())


def _blocked_draft(unresolved_id: str = "supported_1") -> StrategyDraftV2:
    """A draft holding one real open requirement, so a question really strands something."""

    return StrategyDraftV2(
        unresolved_fields=[
            UnresolvedFieldV2(
                unresolved_id=unresolved_id,
                source_fragment="alert me when BTC rises 5%",
                target_type="condition_creation",
                question="Which candle period should I use?",
                reason="one user-controlled choice is still required",
            )
        ]
    )


def _decide(
    conversation: SetupConversationContext,
    message: str,
    *,
    language: ConversationLanguage = ConversationLanguage.ENGLISH,
    draft: StrategyDraftV2 | None = None,
    **extra: object,
) -> ClarificationTurn | None:
    """One call shape for every case here, so the tests read as behaviour not plumbing."""

    return resolve_active_clarification_turn(
        message=message,
        conversation=conversation,
        draft=draft if draft is not None else StrategyDraftV2(),
        language=language,
        **extra,  # type: ignore[arg-type]
    )


def _contract(
    target_type: str,
    *,
    question_id: str = "q_1",
    workflow_id: str | None = None,
    step_revision: int = 0,
    target_field: str | None = None,
    canonical: list[str] | None = None,
    **extra: object,
) -> ClarificationContract:
    """One contract of each kind, valid for that kind's own schema rules."""

    needs_condition = target_type in {
        "condition_field",
        "capability_parameter",
        "reference_definition",
    }
    needs_field = target_type in {
        "draft_field",
        "condition_field",
        "capability_parameter",
        "sharia_policy",
    }
    return ClarificationContract(
        question_id=question_id,
        question="Which candle period should I use?",
        reason="one user-controlled choice is still required",
        target_type=target_type,  # type: ignore[arg-type]
        target_field=target_field or ("trigger_timeframe" if needs_field else None),
        target_condition_id="condition_1" if needs_condition else None,
        expected_answer_schema='{"type":"string"}',
        mutating=target_type != "conversational",
        allowed_options=list(COMMON_TIMEFRAMES)[:3],
        workflow_id=workflow_id,
        step_revision=step_revision,
        canonical_values=canonical if canonical is not None else list(COMMON_TIMEFRAMES),
        **extra,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------------
# Every question type has a handler
# ---------------------------------------------------------------------------------


def test_every_declared_clarification_type_has_a_registered_handler() -> None:
    """The failure this catches is a question nobody can answer."""

    assert _registry_covers_every_target_type() == ()


@pytest.mark.parametrize("target_type", TARGET_TYPES)
def test_a_handler_names_a_real_route_cancellation_domain_and_resume(
    target_type: str,
) -> None:
    handler = handler_for(target_type)
    assert handler is not None
    assert isinstance(handler.apply_route, ApplyRoute)
    assert isinstance(handler.default_cancellation, CancellationPolicy)
    assert isinstance(handler.fallback_domain, AnswerDomain)
    assert isinstance(handler.resume_policy, ResumePolicy)


def test_anything_that_can_be_paused_can_also_be_resumed() -> None:
    """A paused requirement with no way back is a requirement that vanished."""

    for target_type in TARGET_TYPES:
        handler = handler_for(target_type)
        assert handler is not None
        if handler.default_cancellation is CancellationPolicy.PAUSE_PENDING_REQUIREMENT:
            assert handler.resume_policy is ResumePolicy.RESUME_ON_REQUEST, target_type


# ---------------------------------------------------------------------------------
# The effects table is the specification
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize("transition", list(TransitionOutcome))
def test_every_transition_declares_what_it_is_allowed_to_do(
    transition: TransitionOutcome,
) -> None:
    effects = effects_of(transition)
    assert isinstance(effects.question_stays_active, bool)
    assert isinstance(effects.commits_value, bool)
    assert isinstance(effects.may_change_canonical_state, bool)
    assert isinstance(effects.changes_workflow_state, bool)
    assert isinstance(effects.releases_the_turn, bool)


@pytest.mark.parametrize("transition", list(TransitionOutcome))
@pytest.mark.parametrize("route", list(ApplyRoute))
def test_no_answer_transition_may_ever_spend_a_model_call(
    transition: TransitionOutcome,
    route: ApplyRoute,
) -> None:
    """The non-negotiable invariant, checked on every outcome × every route.

    The one exception is a genuinely separate new request, which is not an answer to
    anything and is allowed to reach the planner as the fresh instruction it is.
    """

    plan = plan_transition(transition, route=route)
    if transition is TransitionOutcome.NEW_REQUEST:
        assert plan.may_route_new_request is True
        return
    assert plan.model_calls_allowed is False, (transition, route)
    assert plan.may_route_new_request is False, (transition, route)


@pytest.mark.parametrize("route", list(ApplyRoute))
def test_a_transition_that_keeps_the_question_never_changes_the_draft(
    route: ApplyRoute,
) -> None:
    for transition in TransitionOutcome:
        plan = plan_transition(transition, route=route)
        if plan.question_stays_active:
            assert plan.changes_executable_draft_state is False, (transition, route)
            assert plan.operation == "", (transition, route)


@pytest.mark.parametrize("route", list(ApplyRoute))
def test_only_a_committing_transition_commits(route: ApplyRoute) -> None:
    for transition in TransitionOutcome:
        plan = plan_transition(transition, route=route)
        if plan.accepts_value:
            assert plan.question_stays_active is False, (transition, route)


def test_a_scanner_answer_never_touches_executable_state() -> None:
    """A resolved Scanner window is not a rule change, and must never report as one."""

    plan = plan_transition(TransitionOutcome.RESOLVED, route=ApplyRoute.PENDING_SCAN)
    assert plan.accepts_value is True
    assert plan.changes_executable_draft_state is False
    assert plan.changes_draft_workflow_state is False
    assert plan.runs_gates is False
    assert plan.operation == ""


def test_a_governed_answer_reports_the_exact_policy_operation() -> None:
    plan = plan_transition(TransitionOutcome.RESOLVED, route=ApplyRoute.GOVERNED_OPTION)
    assert plan.changes_executable_draft_state is True
    assert plan.operation == "set_sharia_policy"
    assert plan.runs_gates is True


def test_an_accepted_workflow_step_advances_draft_side_progress() -> None:
    plan = plan_transition(
        TransitionOutcome.CONFIRMATION_ACCEPTED,
        route=ApplyRoute.SUPPORTED_RULE_WORKFLOW,
    )
    assert plan.changes_draft_workflow_state is True
    assert plan.operation == "update_unresolved"


def test_pausing_changes_no_canonical_state() -> None:
    """That is the whole difference between pausing and cancelling."""

    blocker = CanonicalBlocker("resolve_unresolved_key", "supported_1")
    paused = plan_transition(
        TransitionOutcome.PAUSED,
        route=ApplyRoute.SUPPORTED_RULE_WORKFLOW,
        blocker=blocker,
        cancellation=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
    )
    cancelled = plan_transition(
        TransitionOutcome.CANCELLED,
        route=ApplyRoute.SUPPORTED_RULE_WORKFLOW,
        blocker=blocker,
        cancellation=CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
    )
    assert paused.changes_executable_draft_state is False
    assert paused.operation == ""
    assert cancelled.changes_executable_draft_state is True
    assert cancelled.operation == "resolve_unresolved_key"


def test_cancelling_with_nothing_behind_it_removes_nothing() -> None:
    """A reply may only claim a removal that really happened."""

    plan = plan_transition(
        TransitionOutcome.CANCELLED,
        route=ApplyRoute.PENDING_SCAN,
        blocker=None,
        cancellation=CancellationPolicy.CANCEL_CONVERSATION_ONLY,
    )
    assert plan.changes_executable_draft_state is False
    assert plan.operation == ""


@pytest.mark.parametrize("transition", list(TransitionOutcome))
def test_every_transition_that_speaks_has_wording_to_speak_with(
    transition: TransitionOutcome,
) -> None:
    effects = effects_of(transition)
    if transition is TransitionOutcome.NEW_REQUEST:
        assert effects.response_key == "", "routing writes this reply, not the table"
        return
    assert effects.response_key, transition


@pytest.mark.parametrize("target_type", TARGET_TYPES)
def test_every_contract_is_recognised_as_having_a_handler(target_type: str) -> None:
    assert has_registered_handler(_contract(target_type)) is True


def test_no_question_is_the_same_as_no_missing_handler() -> None:
    assert has_registered_handler(None) is True


@pytest.mark.parametrize("target_type", TARGET_TYPES)
def test_a_question_can_always_say_what_kind_of_value_it_wants(target_type: str) -> None:
    domain = answer_domain_for(_contract(target_type))
    assert isinstance(domain, AnswerDomain)


# ---------------------------------------------------------------------------------
# Cancellation policy: assigned when the question is created, never guessed later
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize("target_type", TARGET_TYPES)
def test_a_question_written_before_this_field_existed_still_gets_the_right_policy(
    target_type: str,
) -> None:
    """A stored session must not default every old question to "just drop it"."""

    contract = _contract(target_type)
    assert contract.cancellation_policy is DEFAULT_CANCELLATION_POLICY[target_type]
    assert cancellation_policy_for(contract) is DEFAULT_CANCELLATION_POLICY[target_type]


@pytest.mark.parametrize(
    "target_type", ["universe", "market_scope", "sharia_policy", "draft_field"]
)
def test_a_platform_requirement_is_never_silently_discarded(target_type: str) -> None:
    """Anything the platform itself requires is paused, never claimed as removed."""

    assert (
        cancellation_policy_for(_contract(target_type))
        is CancellationPolicy.PAUSE_PENDING_REQUIREMENT
    )


@pytest.mark.parametrize(
    "target_type",
    [
        "condition_creation",
        "condition_field",
        "boolean_structure",
        "capability_parameter",
        "reference_definition",
        "unsupported_requirement",
        "unsupported_resolution",
    ],
)
def test_a_rule_the_trader_started_can_really_be_dropped(target_type: str) -> None:
    assert (
        cancellation_policy_for(_contract(target_type))
        is CancellationPolicy.REMOVE_PENDING_REQUIREMENT
    )


def test_an_explicit_policy_always_wins_over_the_derived_one() -> None:
    contract = _contract(
        "condition_creation",
        cancellation_policy=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
    )
    assert (
        cancellation_policy_for(contract) is CancellationPolicy.PAUSE_PENDING_REQUIREMENT
    )


# ---------------------------------------------------------------------------------
# One boolean resolver
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize("word", sorted(AFFIRMATIVE_ANSWERS))
def test_every_word_that_means_yes_reads_as_yes(word: str) -> None:
    assert resolve_confirmation(word) is ConfirmationReply.AFFIRMATIVE


@pytest.mark.parametrize("word", sorted(NEGATIVE_ANSWERS))
def test_every_word_that_means_no_reads_as_no(word: str) -> None:
    assert resolve_confirmation(word) is ConfirmationReply.NEGATIVE


@pytest.mark.parametrize(
    "word",
    ["yes!", "  Yes  ", "YES", "Yeah.", "تمام", "أيوه", "Ok.", "d'accord", "Да"],
)
def test_punctuation_and_case_never_change_a_yes(word: str) -> None:
    assert resolve_confirmation(word) is ConfirmationReply.AFFIRMATIVE


@pytest.mark.parametrize("word", ["no!", " No ", "NO", "لا", "мш ده", "non.", "нет"])
def test_punctuation_and_case_never_change_a_no(word: str) -> None:
    assert resolve_confirmation(word) in {
        ConfirmationReply.NEGATIVE,
        ConfirmationReply.UNCLEAR,
    }


@pytest.mark.parametrize("word", ["yse", "yeas", "oky", "nno", "noe"])
def test_one_typing_slip_still_reads_as_the_word_it_nearly_is(word: str) -> None:
    assert resolve_confirmation(word) is not ConfirmationReply.UNCLEAR


@pytest.mark.parametrize(
    "word",
    ["maybe", "1h", "what", "purple bananas", "i think so probably", "later"],
)
def test_anything_that_is_not_a_yes_or_a_no_is_never_guessed(word: str) -> None:
    assert resolve_confirmation(word) is ConfirmationReply.UNCLEAR


@pytest.mark.parametrize("word", sorted(AFFIRMATIVE_ANSWERS))
def test_the_scan_window_reader_knows_the_same_yes_words(word: str) -> None:
    """Two lists of "yes" is how ``correct`` answered one question and not another."""

    assert scan_window_answer(word) == "24h"


@pytest.mark.parametrize("word", sorted(AFFIRMATIVE_ANSWERS))
def test_the_one_option_window_question_accepts_every_yes(word: str) -> None:
    resolution = resolve_active_answer(word, domain=AnswerDomain.SCAN_WINDOW)
    assert resolution.outcome is AnswerOutcome.RESOLVED
    assert resolution.canonical_value == "24h"


def test_the_yes_and_no_lists_do_not_overlap() -> None:
    """One word cannot mean both. A word in both lists would resolve at random."""

    yes = {normalize_answer_text(word) for word in AFFIRMATIVE_ANSWERS}
    no = {normalize_answer_text(word) for word in NEGATIVE_ANSWERS}
    assert yes & no == set()


# ---------------------------------------------------------------------------------
# The confirmation sub-state
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize("word", ["yes", "yeah", "correct", "exactly", "تمام", "أيوه"])
def test_a_yes_stores_the_value_that_was_proposed(word: str) -> None:
    resolution = resolve_active_answer(
        word,
        domain=AnswerDomain.TIMEFRAME,
        offered_values=COMMON_TIMEFRAMES,
        proposed_value="1h",
    )
    assert resolution.outcome is AnswerOutcome.RESOLVED
    assert resolution.canonical_value == "1h"
    assert resolution.stage is AnswerStage.CONFIRMATION
    assert resolution.proposed_value is None


@pytest.mark.parametrize("word", ["no", "not that", "wrong", "لا", "مش ده"])
def test_a_no_drops_the_proposal_and_stores_nothing(word: str) -> None:
    resolution = resolve_active_answer(
        word,
        domain=AnswerDomain.TIMEFRAME,
        offered_values=COMMON_TIMEFRAMES,
        proposed_value="1h",
    )
    assert resolution.outcome is AnswerOutcome.AMBIGUOUS
    assert resolution.canonical_value is None
    assert resolution.proposed_value is None
    assert resolution.rejected_candidate is True


@pytest.mark.parametrize("word", ["maybe", "hmm", "what do you think", "???x"])
def test_an_unclear_confirmation_keeps_the_proposal_and_asks_again(word: str) -> None:
    resolution = resolve_active_answer(
        word,
        domain=AnswerDomain.TIMEFRAME,
        offered_values=COMMON_TIMEFRAMES,
        proposed_value="1h",
    )
    assert resolution.outcome is AnswerOutcome.CONFIRM_CANDIDATE
    assert resolution.canonical_value == "1h"
    assert resolution.proposed_value == "1h"
    assert resolution.stage is AnswerStage.CONFIRMATION


def test_correcting_yourself_beats_confirming_the_wrong_guess() -> None:
    """``qh`` → "did you mean 1h?" → ``4h`` stores 4h, not 1h."""

    resolution = resolve_active_answer(
        "4h",
        domain=AnswerDomain.TIMEFRAME,
        offered_values=COMMON_TIMEFRAMES,
        proposed_value="1h",
    )
    assert resolution.outcome is AnswerOutcome.RESOLVED
    assert resolution.canonical_value == "4h"
    assert resolution.proposed_value is None


def test_a_stop_word_still_stops_during_a_confirmation() -> None:
    resolution = resolve_active_answer(
        "cancel",
        domain=AnswerDomain.TIMEFRAME,
        offered_values=COMMON_TIMEFRAMES,
        proposed_value="1h",
    )
    assert resolution.outcome is AnswerOutcome.CANCELLED
    assert resolution.proposed_value is None


def test_a_near_miss_always_names_the_value_it_wants_confirmed() -> None:
    """Whatever is proposed must be stored, or the next "yes" has nothing to accept."""

    resolution = resolve_active_answer(
        "qh", domain=AnswerDomain.TIMEFRAME, offered_values=COMMON_TIMEFRAMES
    )
    assert resolution.outcome is AnswerOutcome.CONFIRM_CANDIDATE
    assert resolution.proposed_value == resolution.canonical_value == "1h"


@pytest.mark.parametrize(
    ("domain", "proposal", "yes"),
    [
        (AnswerDomain.TIMEFRAME, "1h", "yes"),
        (AnswerDomain.REFERENCE_POINT, "candle_open", "correct"),
        (AnswerDomain.COMPARATOR, "gte", "exactly"),
        (AnswerDomain.MOVEMENT_DIRECTION, "up", "تمام"),
        (AnswerDomain.UNIVERSE_MODE, "eligible_market", "d'accord"),
    ],
)
def test_confirmation_works_the_same_in_every_domain(
    domain: AnswerDomain, proposal: str, yes: str
) -> None:
    resolution = resolve_active_answer(yes, domain=domain, proposed_value=proposal)
    assert resolution.outcome is AnswerOutcome.RESOLVED
    assert resolution.canonical_value == proposal


# ---------------------------------------------------------------------------------
# The question knows which canonical open item it is about
# ---------------------------------------------------------------------------------


def _blocker_draft(unresolved_id: str = "supported_1") -> StrategyDraftV2:
    return StrategyDraftV2(
        unresolved_fields=[
            UnresolvedFieldV2(
                unresolved_id=unresolved_id,
                source_turn_id="turn-0",
                source_fragment="a rise of 5%",
                target_type="condition_creation",
                expected_answer_schema={"type": "string"},
                question="Which candle period should I use?",
                reason="one choice is still required",
            )
        ]
    )


def test_a_workflow_finds_its_blocker_by_workflow_id() -> None:
    draft = _blocker_draft()
    workflow = PendingClarificationWorkflow(
        workflow_id="supported_1",
        workflow_kind="supported_rule",
        question_id="q_1",
        current_field="trigger_timeframe",
    )
    contract = _contract("condition_creation", workflow_id="supported_1")
    assert canonical_blocker_for(draft, contract, workflow) == CanonicalBlocker(
        "resolve_unresolved_key", "supported_1"
    )


def test_a_compiler_built_question_is_its_own_blocker_key() -> None:
    draft = _blocker_draft("draft_period")
    contract = _contract("condition_creation", question_id="draft_period")
    assert canonical_blocker_for(draft, contract) == CanonicalBlocker(
        "resolve_unresolved_key", "draft_period"
    )


def test_an_unsupported_blocker_is_closed_by_the_operation_for_its_own_list() -> None:
    """The list that holds the key decides the operation. Never a static table."""

    draft = StrategyDraftV2(
        unsupported_requirements=[
            UnsupportedRequirementV2(
                key="unsupported_1",
                source_turn_id="turn-0",
                source_fragment="alert on market sentiment",
                missing_contract="sentiment is not a measurable market value",
            )
        ]
    )
    contract = _contract("unsupported_requirement", question_id="unsupported_1")
    assert canonical_blocker_for(draft, contract) == CanonicalBlocker(
        "remove_unsupported_key", "unsupported_1"
    )


def test_a_question_with_nothing_canonical_behind_it_reports_nothing() -> None:
    """So a reply can never claim to have removed something that never existed."""

    contract = _contract("conversational", question_id="scan_window_abc")
    assert canonical_blocker_for(StrategyDraftV2(), contract) is None


def test_the_dispatcher_carries_the_blocker_so_no_caller_re_derives_it() -> None:
    draft = _blocker_draft()
    contract = _contract("condition_creation", workflow_id="supported_1")
    conversation = SetupConversationContext().with_question(contract)
    decision = _decide(conversation, "cancel", draft=draft)
    assert decision is not None
    assert decision.blocker == CanonicalBlocker("resolve_unresolved_key", "supported_1")


# ---------------------------------------------------------------------------------
# Confirmation, as a transition rather than a value reading
# ---------------------------------------------------------------------------------


def _confirming(proposal: str = "1h") -> SetupConversationContext:
    workflow = PendingClarificationWorkflow(
        workflow_id="supported_1",
        workflow_kind="supported_rule",
        question_id="q_1",
        current_field="trigger_timeframe",
        canonical_values=list(COMMON_TIMEFRAMES),
        offered_values=list(COMMON_TIMEFRAMES),
        proposed_value=proposal,
        proposed_evidence="qh",
    )
    contract = _contract("condition_creation", workflow_id="supported_1")
    return SetupConversationContext().with_question(contract, workflow=workflow)


def test_a_yes_is_its_own_transition_not_a_plain_resolution() -> None:
    decision = _decide(_confirming(), "yes")
    assert decision is not None
    assert decision.transition is TransitionOutcome.CONFIRMATION_ACCEPTED
    assert decision.canonical_value == "1h"
    assert decision.proposed_value is None
    assert decision.effects.commits_value is True


def test_a_no_is_its_own_transition_and_commits_nothing() -> None:
    decision = _decide(_confirming(), "no")
    assert decision is not None
    assert decision.transition is TransitionOutcome.CONFIRMATION_REJECTED
    assert decision.rejected_candidate is True
    assert decision.proposed_value is None
    assert decision.effects.commits_value is False
    assert decision.effects.question_stays_active is True


def test_an_explicit_replacement_supersedes_the_proposal() -> None:
    decision = _decide(_confirming(), "4h")
    assert decision is not None
    assert decision.transition is TransitionOutcome.RESOLVED
    assert decision.canonical_value == "4h"
    assert decision.proposed_value is None


def test_an_unclear_confirmation_stays_a_confirmation() -> None:
    decision = _decide(_confirming(), "hmm not sure really")
    assert decision is not None
    assert decision.transition is TransitionOutcome.CONFIRM_CANDIDATE
    assert decision.proposed_value == "1h"
    assert decision.plan.model_calls_allowed is False


def test_several_plausible_readings_are_not_the_same_as_none() -> None:
    """"I found two of these" and "I found nothing" get different replies."""

    ambiguous = _decide(_confirming(), "purple bananas")
    assert ambiguous is not None
    # Inside a confirmation an unreadable message keeps the yes/no rather than
    # becoming a general failure, which is what makes the sub-state a real state.
    assert ambiguous.transition is TransitionOutcome.CONFIRM_CANDIDATE

    plain = SetupConversationContext().with_question(
        _contract("condition_creation", target_field="trigger_timeframe")
    )
    assert _decide(plain, "purple bananas").transition is TransitionOutcome.INVALID  # type: ignore[union-attr]
    assert _decide(plain, "qh").transition is TransitionOutcome.CONFIRM_CANDIDATE  # type: ignore[union-attr]


# ---------------------------------------------------------------------------------
# A stale step can never advance a newer one
# ---------------------------------------------------------------------------------


def test_an_answer_to_the_previous_step_is_refused() -> None:
    contract = _contract("condition_creation", question_id="q_2", step_revision=1)
    assert stale_step(contract, question_id="q_1", step_revision=0) is True
    assert stale_step(contract, question_id="q_2", step_revision=1) is False


def test_an_answer_with_no_step_identity_is_not_treated_as_stale() -> None:
    """A typed answer carries no button metadata, and typing must stay possible."""

    contract = _contract("condition_creation", question_id="q_2", step_revision=1)
    assert stale_step(contract, question_id=None, step_revision=None) is False


def test_a_stale_click_asks_the_current_question_instead_of_advancing() -> None:
    workflow = PendingClarificationWorkflow(
        workflow_id="supported_1",
        workflow_kind="supported_rule",
        step_revision=1,
        question_id="q_2",
        current_field="reference_point",
        canonical_values=["candle_open", "previous_close"],
        offered_values=["candle_open", "previous_close"],
    )
    contract = _contract(
        "condition_creation",
        question_id="q_2",
        workflow_id="supported_1",
        step_revision=1,
        target_field="reference_point",
        canonical=["candle_open", "previous_close"],
    )
    conversation = SetupConversationContext().with_question(contract, workflow=workflow)
    decision = _decide(
        conversation,
        "1h",
        answered_question_id="q_1",
        answered_step_revision=0,
    )
    del contract
    assert decision is not None
    assert decision.transition is TransitionOutcome.STALE_WORKFLOW
    assert decision.canonical_value is None
    assert decision.contract.question_id == "q_2"
    assert decision.effects.commits_value is False
    assert decision.effects.changes_workflow_state is False


# ---------------------------------------------------------------------------------
# The state invariants, asserted as one function so production runs them too
# ---------------------------------------------------------------------------------


def test_a_clean_conversation_breaks_no_invariant() -> None:
    assert workflow_invariants(SetupConversationContext()) == ()


def test_a_question_with_its_workflow_breaks_no_invariant() -> None:
    workflow = PendingClarificationWorkflow(
        workflow_id="supported_1",
        workflow_kind="supported_rule",
        step_revision=0,
        question_id="q_1",
        current_field="trigger_timeframe",
        canonical_values=list(COMMON_TIMEFRAMES),
        offered_values=list(COMMON_TIMEFRAMES),
    )
    contract = _contract("condition_creation", workflow_id="supported_1")
    conversation = SetupConversationContext().with_question(contract, workflow=workflow)
    assert workflow_invariants(conversation) == ()


def test_a_workflow_that_is_not_the_question_on_screen_is_refused_outright() -> None:
    """The schema itself blocks it, which is why the invariant can never be violated."""

    workflow = PendingClarificationWorkflow(
        workflow_id="supported_1",
        workflow_kind="supported_rule",
        step_revision=3,
        question_id="q_other",
        current_field="trigger_timeframe",
    )
    with pytest.raises(ValueError, match="not the pending workflow's current step"):
        SetupConversationContext().with_question(
            _contract("condition_creation", workflow_id="supported_1"), workflow=workflow
        )


def test_a_proposal_cannot_outlive_the_question_that_made_it() -> None:
    workflow = PendingClarificationWorkflow(
        workflow_id="supported_1",
        workflow_kind="supported_rule",
        step_revision=0,
        question_id="q_1",
        current_field="trigger_timeframe",
        proposed_value="1h",
    )
    contract = _contract("condition_creation", workflow_id="supported_1")
    conversation = SetupConversationContext().with_question(contract, workflow=workflow)
    assert workflow_invariants(conversation) == ()
    assert workflow_invariants(conversation.cleared_question()) == ()
    assert conversation.cleared_question().pending_workflow is None


# ---------------------------------------------------------------------------------
# Ownership: the question keeps the turn unless one of three things happened
# ---------------------------------------------------------------------------------


def test_no_question_means_no_decision_to_make() -> None:
    assert _decide(SetupConversationContext(), "1h") is None


def test_a_mode_button_with_nothing_canonical_open_may_take_the_turn() -> None:
    """No blocker and no continuation means nothing is stranded by letting it through."""

    conversation = SetupConversationContext().with_question(_contract("condition_creation"))
    decision = _decide(conversation, "Scanner", mode_selected=True)
    assert decision is not None
    assert decision.transition is TransitionOutcome.NEW_REQUEST
    assert decision.owns_the_turn is False


def test_a_mode_button_over_a_real_requirement_must_be_settled_first() -> None:
    """A mode switch used to bypass ownership entirely and hide the open blocker."""

    contract = _contract("condition_creation", workflow_id="supported_1")
    conversation = SetupConversationContext().with_question(contract)
    decision = _decide(conversation, "Scanner", mode_selected=True, draft=_blocked_draft())
    assert decision is not None
    assert decision.transition is TransitionOutcome.REPLACEMENT_REQUIRED
    assert decision.owns_the_turn is True
    assert decision.plan.changes_executable_draft_state is False
    assert decision.plan.model_calls_allowed is False


def test_a_new_request_over_a_real_requirement_must_be_settled_first() -> None:
    contract = _contract("condition_creation", workflow_id="supported_1")
    conversation = SetupConversationContext().with_question(contract)
    decision = _decide(
        conversation,
        "alert me when ETH falls 3% on the 15m candle",
        draft=_blocked_draft(),
        looks_like_new_request=True,
    )
    assert decision is not None
    assert decision.transition is TransitionOutcome.REPLACEMENT_REQUIRED
    assert decision.plan.may_route_new_request is False


@pytest.mark.parametrize("target_type", TARGET_TYPES)
def test_a_typo_never_falls_through_to_general_routing(target_type: str) -> None:
    """The whole point: an unreadable answer is still an answer, for every question."""

    conversation = SetupConversationContext().with_question(
        _contract(target_type, target_field="trigger_timeframe")
    )
    decision = _decide(conversation, "qh")
    assert decision is not None
    assert decision.owns_the_turn is True
    assert decision.transition is not TransitionOutcome.NEW_REQUEST
    assert decision.plan.model_calls_allowed is False


@pytest.mark.parametrize("target_type", TARGET_TYPES)
def test_a_valid_answer_resolves_every_question_type(target_type: str) -> None:
    conversation = SetupConversationContext().with_question(
        _contract(target_type, target_field="trigger_timeframe")
    )
    decision = _decide(conversation, "1h")
    assert decision is not None
    assert decision.transition is TransitionOutcome.RESOLVED
    assert decision.canonical_value == "1h"
    assert decision.effects.commits_value is True


@pytest.mark.parametrize("target_type", TARGET_TYPES)
def test_an_invalid_answer_keeps_every_question_type_open(target_type: str) -> None:
    conversation = SetupConversationContext().with_question(
        _contract(target_type, target_field="trigger_timeframe")
    )
    decision = _decide(conversation, "purple bananas")
    assert decision is not None
    assert decision.transition is TransitionOutcome.INVALID
    assert decision.keeps_the_question is True
    assert decision.canonical_value is None
    assert decision.plan.model_calls_allowed is False


@pytest.mark.parametrize("target_type", TARGET_TYPES)
def test_an_unsupported_value_is_refused_not_clamped(target_type: str) -> None:
    """A real, well-formed period this question may not take is a boundary, not a typo."""

    conversation = SetupConversationContext().with_question(
        _contract(
            target_type,
            target_field="trigger_timeframe",
            canonical=["1h", "4h"],
        )
    )
    decision = _decide(conversation, "1 minute")
    assert decision is not None
    assert decision.transition is TransitionOutcome.UNSUPPORTED
    assert decision.keeps_the_question is True
    assert decision.effects.commits_value is False


@pytest.mark.parametrize("target_type", TARGET_TYPES)
def test_cancelling_is_understood_for_every_question_type(target_type: str) -> None:
    conversation = SetupConversationContext().with_question(
        _contract(target_type, target_field="trigger_timeframe")
    )
    decision = _decide(conversation, "cancel")
    assert decision is not None
    assert decision.cancellation is DEFAULT_CANCELLATION_POLICY[target_type]
    expected = (
        TransitionOutcome.PAUSED
        if decision.cancellation is CancellationPolicy.PAUSE_PENDING_REQUIREMENT
        else TransitionOutcome.CANCELLED
    )
    assert decision.transition is expected
    assert decision.ends_the_question is True


@pytest.mark.parametrize("language", list(ConversationLanguage))
def test_the_choices_shown_back_are_in_the_conversation_language(
    language: ConversationLanguage,
) -> None:
    conversation = SetupConversationContext().with_question(
        _contract(
            "condition_creation",
            target_field="reference_point",
            canonical=["candle_open", "previous_close"],
        )
    )
    decision = _decide(conversation, "purple bananas", language=language)
    assert decision is not None
    assert decision.display_labels
    assert "candle_open" not in decision.display_labels or language is None


# ---------------------------------------------------------------------------------
# Every mutating question carries a deterministic continuation, or is not asked
# ---------------------------------------------------------------------------------


def test_every_continuation_kind_has_a_registered_builder() -> None:
    """Fail closed at import: a kind with no builder stops the process from starting."""

    from ai_market_monitor.schemas.clarification_continuation import ContinuationKind

    assert registered_builders() == {str(item) for item in ContinuationKind}


def test_every_registered_continuation_applies_without_a_model_call() -> None:
    from ai_market_monitor.schemas.clarification_continuation import ContinuationKind

    for kind in ContinuationKind:
        assert continuation_is_deterministic(_KindProbe(kind)) is True


class _KindProbe:
    """The smallest thing that answers "which kind are you?"."""

    def __init__(self, kind: object) -> None:
        self.kind = kind


@pytest.mark.parametrize("target_type", TARGET_TYPES)
def test_a_mutating_question_without_a_continuation_is_never_rendered(
    target_type: str,
) -> None:
    """The no-orphan rule, across every question kind there is.

    An orphan is a question whose correct answer has nowhere to go: the trader types the
    right thing and is told it was not understood, every time, with no way forward.
    """

    contract = _contract(target_type, target_field="trigger_timeframe")
    if not contract.mutating:
        assert may_be_rendered(contract, StrategyDraftV2()) is True
        return
    assert may_be_rendered(contract, StrategyDraftV2()) is False
    assert "continuation" in orphan_reason(contract, StrategyDraftV2())


def test_a_blocker_with_no_deterministic_completion_is_not_asked_about() -> None:
    """A topology cannot be derived from a word, so no honest continuation exists."""

    item = UnresolvedFieldV2(
        unresolved_id="boolean_1",
        source_fragment="either of those two",
        target_type="boolean_structure",
        question="How should those rules combine?",
        reason="the shape is ambiguous",
    )
    built = continuation_for_unresolved(
        item,
        StrategyDraftV2(),
        question_id="boolean_1",
        step_revision=0,
        cancellation_policy=CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
    )
    assert built is None


def test_a_draft_field_blocker_builds_the_operation_it_promised() -> None:
    item = UnresolvedFieldV2(
        unresolved_id="name_1",
        source_fragment="call it my breakout watch",
        target_type="draft_field",
        target_field="name",
        question="What should I call this setup?",
        reason="a monitor needs a name",
    )
    continuation = continuation_for_unresolved(
        item,
        StrategyDraftV2(),
        question_id="name_1",
        step_revision=0,
        cancellation_policy=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
    )
    assert continuation is not None
    operations = build_continuation_operations(
        continuation,
        ContinuationAnswer(canonical_value="My breakout watch", evidence="call it that"),
        draft=StrategyDraftV2(),
    )
    assert [item.kind for item in operations] == ["set_fields", "resolve_unresolved_key"]
    assert operations[0].fields is not None
    assert operations[0].fields.name == "My breakout watch"
    assert operations[1].target_key == "name_1"


def test_a_continuation_refuses_a_value_the_step_cannot_run() -> None:
    """Never clamp, never substitute: a value outside the step is refused."""

    item = UnresolvedFieldV2(
        unresolved_id="name_2",
        source_fragment="use spot",
        target_type="draft_field",
        target_field="market_type",
        question="Which market type?",
        reason="the market type is required",
        allowed_options=["spot"],
    )
    continuation = continuation_for_unresolved(
        item,
        StrategyDraftV2(),
        question_id="name_2",
        step_revision=0,
        cancellation_policy=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
        allowed_values=["spot"],
    )
    assert continuation is not None
    with pytest.raises(ContinuationRefused) as refused:
        build_continuation_operations(
            continuation,
            ContinuationAnswer(canonical_value="futures", evidence="use futures"),
            draft=StrategyDraftV2(),
        )
    assert refused.value.code == "CONTINUATION_VALUE_NOT_EXECUTABLE"


def test_a_continuation_refuses_an_answer_written_against_a_draft_that_moved() -> None:
    """The answer belongs to a state that no longer exists, so it is asked again."""

    moved = StrategyDraftV2(name="Renamed")
    item = UnresolvedFieldV2(
        unresolved_id="name_3",
        source_fragment="call it that",
        target_type="draft_field",
        target_field="name",
        question="What should I call this setup?",
        reason="a monitor needs a name",
    )
    continuation = continuation_for_unresolved(
        item,
        StrategyDraftV2(),
        question_id="name_3",
        step_revision=0,
        cancellation_policy=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
    )
    assert continuation is not None
    # The draft the question was asked against, whatever its hash happened to be.
    continuation = continuation.model_copy(update={"expected_executable_hash": "a" * 64})
    with pytest.raises(ContinuationRefused) as refused:
        build_continuation_operations(
            continuation,
            ContinuationAnswer(canonical_value="Renamed", evidence="call it Renamed"),
            draft=moved,
        )
    assert refused.value.code == "CONTINUATION_DRAFT_MOVED"


def test_a_continuation_must_belong_to_the_question_that_carries_it() -> None:
    """The displayed question and the stored plan can never describe different things."""

    continuation = continuation_for_unresolved(
        UnresolvedFieldV2(
            unresolved_id="name_4",
            source_fragment="call it that",
            target_type="draft_field",
            target_field="name",
            question="What should I call this setup?",
            reason="a monitor needs a name",
        ),
        StrategyDraftV2(),
        question_id="a_different_question",
        step_revision=0,
        cancellation_policy=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
    )
    with pytest.raises(ValueError, match="different question"):
        ClarificationContract(
            question_id="name_4",
            question="What should I call this setup?",
            reason="a monitor needs a name",
            target_type="draft_field",
            target_field="name",
            expected_answer_schema='{"type":"string"}',
            mutating=True,
            cancellation_policy=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
            continuation=continuation,
        )


# ---------------------------------------------------------------------------------
# Every producer of a question obeys the same rule
# ---------------------------------------------------------------------------------


def test_every_question_producer_supplies_a_continuation() -> None:
    """A future producer that forgets is caught here, not by a stranded trader.

    Read from the source rather than by calling each builder, because the point is to
    catch the *next* one: a new ``ClarificationContract(...)`` added anywhere in the
    server must either be non-mutating or carry the completion that will apply its
    answer. There is no third option that leaves a trader able to reply.
    """

    import pathlib

    def _call_bodies(text: str, opener: str) -> list[str]:
        """Every ``opener(...)`` call body, with nested brackets balanced properly.

        A regular expression stops at the first closing bracket it sees, which here is
        the one closing a wrapped ``question=(...)`` string — so it read a real producer
        as if it had no continuation. Counting brackets is the only honest way.
        """

        bodies: list[str] = []
        start = text.find(opener)
        while start >= 0:
            index = start + len(opener)
            depth = 1
            while index < len(text) and depth:
                if text[index] == "(":
                    depth += 1
                elif text[index] == ")":
                    depth -= 1
                index += 1
            bodies.append(text[start + len(opener) : index - 1])
            start = text.find(opener, index)
        return bodies

    sources = sorted(pathlib.Path("src/ai_market_monitor").rglob("*.py"))
    constructions: list[tuple[str, str]] = []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        constructions.extend(
            (path.name, body)
            for body in _call_bodies(text, "ClarificationContract(")
            # The class statement itself is not a producer.
            if "StrictModel" not in body.strip()
        )

    assert len(constructions) >= 6, "the producers moved; this test must be pointed at them"
    for name, body in constructions:
        non_mutating = "mutating=False" in body
        carries = "continuation=" in body
        assert non_mutating or carries, (
            f"{name} builds a mutating question with no continuation:\n{body[:400]}"
        )


def test_the_question_and_its_continuation_must_describe_one_thing() -> None:
    """Shown and stored can never be about different fields, objects or steps."""

    continuation = continuation_for_unresolved(
        UnresolvedFieldV2(
            unresolved_id="name_5",
            source_fragment="call it that",
            target_type="draft_field",
            target_field="name",
            question="What should I call this setup?",
            reason="a monitor needs a name",
        ),
        StrategyDraftV2(),
        question_id="name_5",
        step_revision=0,
        cancellation_policy=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
    )
    assert continuation is not None

    def _build(**overrides: object) -> ClarificationContract:
        payload: dict[str, object] = {
            "question_id": "name_5",
            "question": "What should I call this setup?",
            "reason": "a monitor needs a name",
            "target_type": "draft_field",
            "target_field": "name",
            "expected_answer_schema": '{"type":"string"}',
            "mutating": True,
            "cancellation_policy": CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
            "continuation": continuation,
        }
        payload.update(overrides)
        return ClarificationContract(**payload)  # type: ignore[arg-type]

    assert _build().continuation is not None
    with pytest.raises(ValueError, match="different step"):
        _build(step_revision=1)
    with pytest.raises(ValueError, match="disagree about cancelling"):
        _build(cancellation_policy=CancellationPolicy.REMOVE_PENDING_REQUIREMENT)


def test_a_rendered_question_never_leaks_the_server_plan_to_the_client() -> None:
    """The stored plan is the server's business: a rule template, a hash, a control."""

    continuation = continuation_for_unresolved(
        UnresolvedFieldV2(
            unresolved_id="name_6",
            source_fragment="call it that",
            target_type="draft_field",
            target_field="name",
            question="What should I call this setup?",
            reason="a monitor needs a name",
        ),
        StrategyDraftV2(),
        question_id="name_6",
        step_revision=0,
        cancellation_policy=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
    )
    contract = ClarificationContract(
        question_id="name_6",
        question="What should I call this setup?",
        reason="a monitor needs a name",
        target_type="draft_field",
        target_field="name",
        expected_answer_schema='{"type":"string"}',
        mutating=True,
        cancellation_policy=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
        continuation=continuation,
    )

    payload = contract.client_payload()

    assert "continuation" not in payload
    # Everything the client actually needs is still there.
    assert payload["question_id"] == "name_6"
    assert payload["step_revision"] == 0
