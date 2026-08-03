"""INV-FAIL: a failure is named for what happened, and recovery follows from the name.

Runs 9-11 refused 40 cases and reported ``COMPILER_INVARIANT_VIOLATION`` on almost all
of them. That class is terminal: no repair, no question, HTTP 422, and a message saying
nothing changed. It was being used as the catch-all for anything the classifier could
not attribute, and the cost is visible in the transcripts:

* ``precedence_grouping-013-1996163001`` — one ordinary instruction, eight identical
  refusals, no change to the draft, nothing the trader could do differently.
* ``operator_mapping-026-512624184`` — five refusals, then the trader degraded their own
  wording until something compiled, and what compiled was the wrong comparison.

Two rules are asserted here, across the whole family rather than for one code:

1. ``COMPILER_INVARIANT_VIOLATION`` means only "the server built something invalid from
   a valid reading". A planner omission, a source-association problem, an ambiguous
   expression and a canonical refusal each get their own name.
2. A paid correction starts only when it can succeed. Everything else is decided
   deterministically, for free, before a provider is called.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.planner_intent_compiler import (
    IntentCompileError,
    SemanticIntentOutcome,
    compile_planner_intents,
    failure_class_for_code,
    semantic_value_is_grounded,
)
from ai_market_monitor.engine.repair_eligibility import (
    MINIMUM_REPAIR_SECONDS,
    RepairDecision,
    decide_repair,
)
from ai_market_monitor.engine.setup_failure_taxonomy import (
    FailureOwner,
    SetupFailureClass,
    TurnFailureRecord,
    failure_fingerprint,
    is_operator_alertable,
    owner_for,
)
from ai_market_monitor.engine.validated_intent_snapshot import (
    GroundedRequirement,
    ValidatedIntentSnapshot,
    normalized_intent_hash,
    repeat_state,
)
from ai_market_monitor.schemas.planner_intent import PlannerIntentEnvelope
from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2
from ai_market_monitor.services.setup_chat_agent import grounded_requirements_from

# ---------------------------------------------------------------------------------
# The name means what it says
# ---------------------------------------------------------------------------------

#: Every code the compiler can raise, and the class it must be reported as. A code that
#: is not a genuine server fault must never map to COMPILER_INVARIANT_VIOLATION.
CODE_CLASSES: tuple[tuple[str, SetupFailureClass], ...] = (
    ("PLANNER_SEMANTIC_OMISSION", SetupFailureClass.PLANNER_SEMANTIC_OMISSION),
    ("INTENT_VALUE_UNREADABLE", SetupFailureClass.PLANNER_VALUE_MISMATCH),
    ("INTENT_SEGMENT_NOT_IN_MESSAGE", SetupFailureClass.SOURCE_ASSOCIATION_MISMATCH),
    ("SOURCE_ASSOCIATION_MISMATCH", SetupFailureClass.SOURCE_ASSOCIATION_MISMATCH),
    ("BOOLEAN_TOPOLOGY_MISSING", SetupFailureClass.BOOLEAN_TOPOLOGY_MISSING),
    ("BOOLEAN_TOPOLOGY_AMBIGUOUS", SetupFailureClass.BOOLEAN_TOPOLOGY_AMBIGUOUS),
    ("INTENT_INCOMPLETE", SetupFailureClass.USER_INFORMATION_REQUIRED),
    ("UNSUPPORTED_REQUIREMENT", SetupFailureClass.UNSUPPORTED_REQUIREMENT),
    ("VALUE_NOT_GROUNDED", SetupFailureClass.GROUNDING_MISMATCH),
    ("COMPILER_INVARIANT_VIOLATION", SetupFailureClass.COMPILER_INVARIANT_VIOLATION),
)


@pytest.mark.parametrize(("code", "expected"), CODE_CLASSES)
def test_every_code_reports_the_class_it_actually_is(
    code: str, expected: SetupFailureClass
) -> None:
    assert failure_class_for_code(code) is expected


@pytest.mark.parametrize(("code", "expected"), CODE_CLASSES)
def test_only_a_real_server_fault_is_a_compiler_invariant(
    code: str, expected: SetupFailureClass
) -> None:
    if code != "COMPILER_INVARIANT_VIOLATION":
        assert expected is not SetupFailureClass.COMPILER_INVARIANT_VIOLATION


@pytest.mark.parametrize("failure", list(SetupFailureClass))
def test_every_class_has_exactly_one_owner(failure: SetupFailureClass) -> None:
    assert isinstance(owner_for(failure), FailureOwner)


def test_the_user_is_never_asked_to_fix_the_servers_own_fault() -> None:
    """A compiler fault and a canonical refusal go to an operator, not to the customer."""

    assert is_operator_alertable(SetupFailureClass.COMPILER_INVARIANT_VIOLATION)
    assert is_operator_alertable(SetupFailureClass.CANONICAL_VALIDATION_FAILURE)
    assert owner_for(SetupFailureClass.COMPILER_INVARIANT_VIOLATION) is FailureOwner.COMPILER
    assert not is_operator_alertable(SetupFailureClass.USER_INFORMATION_REQUIRED)


# ---------------------------------------------------------------------------------
# Several omissions on one rule are one correction, not an internal fault
# ---------------------------------------------------------------------------------


def _omission_envelope(message: str, drop: set[str]) -> PlannerIntentEnvelope:
    condition = {
        "source_quote": message,
        "formula_key": "close_to_close_percentage",
        "movement_direction": "down",
        "comparator": "lte",
        "threshold": 3.0,
        "unit": "percent",
        "trigger_timeframe": "1h",
        "context_timeframes": ["15m"],
    }
    for field in drop:
        if field.endswith("_timeframes"):
            condition[field] = []
        else:
            condition.pop(field, None)
    return PlannerIntentEnvelope.model_validate(
        {
            "segments": [
                {
                    "segment_ref": "s1",
                    "exact_source_text": message,
                    "segment_kind": "STRATEGY_INSTRUCTION",
                }
            ],
            "semantic_intents": [
                {
                    "segment_ref": "s1",
                    "payload": {"action": "add_condition", "condition": condition},
                }
            ],
            "overall_confidence": 0.95,
        }
    )


OMISSION_MESSAGE = (
    "Watch SOLUSDT on the 15m context and the 1h trigger, and only show me a bearish "
    "move of at most 3%."
)

#: One, two and three omissions on the same rule. Before, one was repairable and two or
#: more were reported as an internal compiler fault — the same evidence, the same
#: cause, a completely different and unrecoverable outcome.
OMISSION_SETS: tuple[frozenset[str], ...] = (
    frozenset({"movement_direction"}),
    frozenset({"context_timeframes"}),
    frozenset({"trigger_timeframe"}),
    frozenset({"movement_direction", "context_timeframes"}),
    frozenset({"movement_direction", "trigger_timeframe"}),
    frozenset({"context_timeframes", "trigger_timeframe"}),
    frozenset({"movement_direction", "context_timeframes", "trigger_timeframe"}),
)


@pytest.mark.parametrize("drop", OMISSION_SETS, ids=lambda x: "+".join(sorted(x)))
def test_any_number_of_omissions_stays_a_correctable_model_mistake(
    drop: frozenset[str],
) -> None:
    with pytest.raises(IntentCompileError) as captured:
        compile_planner_intents(
            _omission_envelope(OMISSION_MESSAGE, set(drop)),
            draft=StrategyDraftV2(),
            message=OMISSION_MESSAGE,
            source_turn_id="turn-omission",
        )
    failure = captured.value
    assert failure.failure_class is not SetupFailureClass.COMPILER_INVARIANT_VIOLATION
    assert failure.outcome is SemanticIntentOutcome.SEMANTIC_INTENT_REPAIR_REQUIRED
    # Every omitted field is named, so one bounded correction can address them all.
    assert len(failure.target_paths) == len(drop)
    assert {path.removeprefix("condition.") for path in failure.target_paths} == set(drop)


@pytest.mark.parametrize("drop", OMISSION_SETS, ids=lambda x: "+".join(sorted(x)))
def test_the_compiler_still_writes_nothing_into_the_traders_rule(
    drop: frozenset[str],
) -> None:
    """Detecting an omission is not permission to fill it in."""

    envelope = _omission_envelope(OMISSION_MESSAGE, set(drop))
    with pytest.raises(IntentCompileError):
        compile_planner_intents(
            envelope,
            draft=StrategyDraftV2(),
            message=OMISSION_MESSAGE,
            source_turn_id="turn-omission",
        )
    condition = envelope.semantic_intents[0].payload.condition  # type: ignore[union-attr]
    for field in drop:
        value = getattr(condition, field)
        assert value in (None, []), field


# ---------------------------------------------------------------------------------
# A paid correction starts only when it can succeed
# ---------------------------------------------------------------------------------


def _decide(failure: SetupFailureClass, **overrides: object) -> RepairDecision:
    arguments: dict[str, object] = {
        "intent_parsed": True,
        "target_paths": ("condition.threshold",),
        "intent_ref": "intent_1",
        "segment_ref": "s1",
        "source_verified": True,
        "replacement_is_groundable": True,
        "seconds_remaining": 30.0,
        "budget_remaining_usd": 1.0,
        "attempted_fingerprints": (),
        "fingerprint": "abc",
    }
    arguments.update(overrides)
    return decide_repair(failure, **arguments).decision  # type: ignore[arg-type]


#: Every class, and whether a provider call may be started for it. The 18 corrections
#: that recovered nothing in runs 9-11 were all decidable as hopeless from facts the
#: server already had.
@pytest.mark.parametrize(
    ("failure", "expected"),
    (
        (SetupFailureClass.PLANNER_SEMANTIC_OMISSION, RepairDecision.SCALAR_SEMANTIC_DELTA),
        (SetupFailureClass.PLANNER_VALUE_MISMATCH, RepairDecision.SCALAR_SEMANTIC_DELTA),
        (SetupFailureClass.GROUNDING_MISMATCH, RepairDecision.SCALAR_SEMANTIC_DELTA),
        (SetupFailureClass.BOOLEAN_TOPOLOGY_MISSING, RepairDecision.BOOLEAN_TOPOLOGY_REPAIR),
        (SetupFailureClass.BOOLEAN_TOPOLOGY_AMBIGUOUS, RepairDecision.USER_CLARIFICATION),
        (SetupFailureClass.USER_INFORMATION_REQUIRED, RepairDecision.USER_CLARIFICATION),
        (SetupFailureClass.UNSUPPORTED_REQUIREMENT, RepairDecision.UNSUPPORTED),
        (SetupFailureClass.COMPILER_INVARIANT_VIOLATION, RepairDecision.INTERNAL_BUG),
        (SetupFailureClass.NON_RECOVERABLE_FAILURE, RepairDecision.INTERNAL_BUG),
        (SetupFailureClass.PROVIDER_FAILURE, RepairDecision.NO_REPAIR_VALUE),
        (SetupFailureClass.PLANNER_SCHEMA_INVALID, RepairDecision.NO_REPAIR_VALUE),
    ),
)
def test_each_class_has_one_decided_recovery(
    failure: SetupFailureClass, expected: RepairDecision
) -> None:
    assert _decide(failure) is expected


@pytest.mark.parametrize(
    "failure",
    (
        SetupFailureClass.UNSUPPORTED_REQUIREMENT,
        SetupFailureClass.COMPILER_INVARIANT_VIOLATION,
        SetupFailureClass.NON_RECOVERABLE_FAILURE,
        SetupFailureClass.PROVIDER_FAILURE,
        SetupFailureClass.PLANNER_SCHEMA_INVALID,
        SetupFailureClass.USER_INFORMATION_REQUIRED,
        SetupFailureClass.BOOLEAN_TOPOLOGY_AMBIGUOUS,
    ),
)
def test_an_impossible_correction_never_calls_a_provider(failure: SetupFailureClass) -> None:
    plan = decide_repair(
        failure,
        intent_parsed=True,
        target_paths=("condition.threshold",),
        intent_ref="intent_1",
        segment_ref="s1",
        source_verified=True,
        replacement_is_groundable=True,
        seconds_remaining=30.0,
        budget_remaining_usd=1.0,
        attempted_fingerprints=(),
        fingerprint="abc",
    )
    assert not plan.spends_model_call


def test_a_failure_already_corrected_once_is_not_paid_for_again() -> None:
    assert (
        _decide(
            SetupFailureClass.PLANNER_SEMANTIC_OMISSION,
            attempted_fingerprints=("abc",),
        )
        is RepairDecision.NO_REPAIR_VALUE
    )


def test_no_time_and_no_budget_both_stop_a_correction() -> None:
    assert (
        _decide(
            SetupFailureClass.PLANNER_SEMANTIC_OMISSION,
            seconds_remaining=MINIMUM_REPAIR_SECONDS - 0.1,
        )
        is RepairDecision.NO_REPAIR_VALUE
    )
    assert (
        _decide(SetupFailureClass.PLANNER_SEMANTIC_OMISSION, budget_remaining_usd=0.0)
        is RepairDecision.NO_REPAIR_VALUE
    )


def test_words_the_server_already_settled_cost_nothing() -> None:
    plan = decide_repair(
        SetupFailureClass.PLANNER_VALUE_MISMATCH,
        intent_parsed=True,
        target_paths=("condition.comparator",),
        intent_ref="intent_1",
        segment_ref="s1",
        source_verified=True,
        replacement_is_groundable=True,
        seconds_remaining=30.0,
        budget_remaining_usd=1.0,
        attempted_fingerprints=(),
        fingerprint="abc",
        deterministic_answer_known=True,
    )
    assert plan.decision is RepairDecision.DETERMINISTIC_NORMALIZATION
    assert not plan.spends_model_call


def test_a_turn_gets_at_most_one_correction() -> None:
    assert (
        _decide(SetupFailureClass.PLANNER_SEMANTIC_OMISSION, repair_already_used=True)
        is RepairDecision.NO_REPAIR_VALUE
    )


# ---------------------------------------------------------------------------------
# The same request, sent again, is not the same work again
# ---------------------------------------------------------------------------------


def test_a_reworded_restatement_is_recognised_as_the_same_request() -> None:
    """The loop stayed invisible because "repeated questions" counted exact wording."""

    first = normalized_intent_hash(
        "Build a Watchlist for ETHUSDT only and exclude SOLUSDT. Use 1h as context."
    )
    reworded = normalized_intent_hash(
        "use 1h as context. build a watchlist for ETHUSDT only, and exclude SOLUSDT!"
    )
    different = normalized_intent_hash(
        "Build a Watchlist for BTCUSDT only and exclude SOLUSDT. Use 1h as context."
    )
    assert first == reworded
    assert first != different


def test_retry_identity_never_collapses_different_boolean_topologies() -> None:
    left = normalized_intent_hash(
        "the 15m move is bullish at least 1% AND "
        "(the 1h move is bearish at least 2% OR the 4h move is bullish at least 3%)"
    )
    right = normalized_intent_hash(
        "(the 15m move is bullish at least 1% AND "
        "the 1h move is bearish at least 2%) OR the 4h move is bullish at least 3%"
    )
    assert left != right


def test_platform_neutral_default_is_not_grounded_user_evidence() -> None:
    source = "Alert on a bullish 15m move of at least 2 percent"
    assert not semantic_value_is_grounded(
        "neutral", source, path="condition.strategy_bias"
    )
    assert semantic_value_is_grounded(
        "neutral", "Keep the strategy bias neutral", path="condition.strategy_bias"
    )


def test_failure_record_preserves_all_typed_paths_and_values() -> None:
    record = TurnFailureRecord(
        failure_class=SetupFailureClass.PLANNER_SEMANTIC_OMISSION,
        owner=FailureOwner.MODEL,
        semantic_path="condition.trigger_timeframe",
        semantic_paths=(
            "condition.trigger_timeframe",
            "condition.context_timeframes",
        ),
        expected_values=(
            ("condition.trigger_timeframe", "15m"),
            ("condition.context_timeframes", "1h"),
        ),
        observed_values=(
            ("condition.trigger_timeframe", "absent"),
            ("condition.context_timeframes", "absent"),
        ),
    ).to_dict()
    assert record["semantic_paths"] == [
        "condition.trigger_timeframe",
        "condition.context_timeframes",
    ]
    assert record["expected_values"]["condition.context_timeframes"] == "1h"


def test_the_third_identical_attempt_is_recognised_as_a_loop() -> None:
    intent = normalized_intent_hash("watch ETHUSDT with a bullish move of at least 2.5%")
    fingerprint = failure_fingerprint(
        canonical_draft_hash="draft-1",
        normalized_user_intent_hash=intent,
        failure_class=SetupFailureClass.PLANNER_SEMANTIC_OMISSION,
        failure_paths=("condition.trigger_timeframe",),
    )
    history = [
        ValidatedIntentSnapshot(
            session_id="chat-1",
            source_turn_id=f"turn-{index}",
            canonical_draft_hash="draft-1",
            normalized_user_intent_hash=intent,
            grounded_requirements=(
                GroundedRequirement(
                    semantic_path="condition.threshold",
                    value="2.5",
                    source_excerpt="a bullish move of at least 2.5%",
                ),
            ),
            failure_class=SetupFailureClass.PLANNER_SEMANTIC_OMISSION.value,
            failure_paths=("condition.trigger_timeframe",),
            failure_fingerprint=fingerprint,
        ).to_dict()
        for index in (1, 2)
    ]
    state = repeat_state(
        history,
        canonical_draft_hash="draft-1",
        normalized_user_intent_hash=intent,
    )
    assert state.is_repeat
    assert state.is_loop
    assert state.same_intent_retry_count == 2
    assert fingerprint in state.attempted_fingerprints
    # And the values already proved are still available, so the trader is not asked to
    # type the whole instruction a third time.
    assert {item.semantic_path for item in state.reusable_requirements} == {
        "condition.threshold"
    }


def test_a_failed_turn_still_keeps_what_it_understood() -> None:
    intent = normalized_intent_hash("watch ETHUSDT")
    snapshot = ValidatedIntentSnapshot(
        session_id="chat-1",
        source_turn_id="turn-1",
        canonical_draft_hash="draft-1",
        normalized_user_intent_hash=intent,
        grounded_requirements=(
            GroundedRequirement("symbol", "ETHUSDT", "watch ETHUSDT"),
        ),
        failure_class=SetupFailureClass.PLANNER_SEMANTIC_OMISSION.value,
    )
    restored = ValidatedIntentSnapshot.from_dict(snapshot.to_dict())
    assert restored.grounded_requirements == snapshot.grounded_requirements
    assert restored.failure_class == snapshot.failure_class


def test_a_different_draft_is_not_the_same_failure() -> None:
    """A draft that moved on is genuinely new work, not a loop to break out of."""

    intent = normalized_intent_hash("watch ETHUSDT")
    history = [
        ValidatedIntentSnapshot(
            session_id="chat-1",
            source_turn_id="turn-1",
            canonical_draft_hash="draft-1",
            normalized_user_intent_hash=intent,
            failure_fingerprint="fp-1",
            failure_class=SetupFailureClass.PLANNER_SEMANTIC_OMISSION.value,
        ).to_dict()
    ]
    state = repeat_state(
        history,
        canonical_draft_hash="draft-2",
        normalized_user_intent_hash=intent,
    )
    assert state.same_failure_repeat_count == 0
    assert state.attempted_fingerprints == ()
    assert state.reusable_requirements == ()


def test_unrelated_request_evidence_and_questions_are_not_reused() -> None:
    wanted = normalized_intent_hash("watch ETHUSDT")
    unrelated = normalized_intent_hash("exclude SOLUSDT")
    history = [
        ValidatedIntentSnapshot(
            session_id="chat-1",
            source_turn_id="turn-1",
            canonical_draft_hash="draft-1",
            normalized_user_intent_hash=unrelated,
            grounded_requirements=(
                GroundedRequirement(
                    "exclude_symbol.symbol",
                    "SOLUSDT",
                    "exclude SOLUSDT",
                ),
            ),
            asked_question="Which exchange should SOL use?",
        ).to_dict(),
        ValidatedIntentSnapshot(
            session_id="chat-1",
            source_turn_id="turn-2",
            canonical_draft_hash="draft-1",
            normalized_user_intent_hash=wanted,
            grounded_requirements=(
                GroundedRequirement("include_symbol.symbol", "ETHUSDT", "watch ETHUSDT"),
            ),
            asked_question="Which exchange should ETH use?",
        ).to_dict(),
    ]

    state = repeat_state(
        history,
        canonical_draft_hash="draft-1",
        normalized_user_intent_hash=wanted,
    )

    assert [item.semantic_path for item in state.reusable_requirements] == [
        "include_symbol.symbol"
    ]
    assert state.questions_already_asked == ("Which exchange should ETH use?",)


def test_grounded_snapshot_keeps_each_independent_condition_value() -> None:
    first = "ETH rises at least 2% on 15m"
    second = "BTC falls at most 3% on 1h"
    message = f"{first} and {second}"
    envelope = PlannerIntentEnvelope.model_validate(
        {
            "segments": [
                {
                    "segment_ref": "s1",
                    "exact_source_text": first,
                    "segment_kind": "STRATEGY_INSTRUCTION",
                },
                {
                    "segment_ref": "s2",
                    "exact_source_text": second,
                    "segment_kind": "STRATEGY_INSTRUCTION",
                },
            ],
            "semantic_intents": [
                {
                    "segment_ref": "s1",
                    "payload": {
                        "action": "add_condition",
                        "condition": {
                            "source_quote": first,
                            "formula_key": "close_to_close_percentage",
                            "movement_direction": "up",
                            "comparator": "gte",
                            "threshold": 2,
                            "unit": "percent",
                            "trigger_timeframe": "15m",
                        },
                    },
                },
                {
                    "segment_ref": "s2",
                    "payload": {
                        "action": "add_condition",
                        "condition": {
                            "source_quote": second,
                            "formula_key": "close_to_close_percentage",
                            "movement_direction": "down",
                            "comparator": "lte",
                            "threshold": 3,
                            "unit": "percent",
                            "trigger_timeframe": "1h",
                        },
                    },
                },
            ],
            "overall_confidence": 0.99,
        }
    )

    grounded = grounded_requirements_from(envelope, message)
    threshold_rows = [item for item in grounded if item.semantic_path.endswith(".threshold")]

    assert len(threshold_rows) == 2
    assert {item.value for item in threshold_rows} == {"2.0", "3.0"}
    assert len({item.semantic_path for item in threshold_rows}) == 2
