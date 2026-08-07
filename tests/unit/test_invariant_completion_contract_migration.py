"""Workflow progress has exactly one writable owner, and legacy records reach it safely.

Progress used to live in two places at once: inside an unresolved field's
``expected_answer_schema`` under an ``x-`` extension key, and again on the conversation.
Two writable copies of one fact is the defect class this repository keeps paying for —
they were written at different moments and drifted, so a correct answer was validated
against a step the trader was no longer looking at.

These tests assert the rule, not the one reported record:

* the answer schema holds an answer *shape* and nothing else, for every legacy shape;
* every accepted value and every piece of evidence survives the move;
* two copies that disagree fail closed rather than one being picked silently;
* a value can never be owned by two writers at once.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_market_monitor.schemas.clarification_continuation import (
    SUPPORTED_REQUEST_SCHEMA_KEY,
    ClarificationCompletionContract,
    completion_contract_from_metadata,
    metadata_from_completion_contract,
)
from ai_market_monitor.schemas.setup_agent import PendingClarificationWorkflow
from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2, UnresolvedFieldV2

_LEGACY_METADATA: dict[str, object] = {
    "schema_version": 1,
    "missing_slots": ["trigger_timeframe", "comparator"],
    "next_field": "trigger_timeframe",
    "threshold": 5.0,
    "movement_direction": "up",
    "unit": "percent",
    "symbols": ["BTC/USDT"],
    "evidence_fragments": ["alert me when BTC rises 5%"],
}


def _legacy(metadata: object, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "unresolved_id": "supported_1",
        "source_fragment": "alert me when BTC rises 5%",
        "target_type": "condition_creation",
        "question": "Which candle period should I use?",
        "reason": "one user-controlled choice is still required",
        "expected_answer_schema": {
            "type": "string",
            SUPPORTED_REQUEST_SCHEMA_KEY: metadata,
        },
    }
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------------
# Complete legacy metadata
# ---------------------------------------------------------------------------------


def test_complete_legacy_metadata_becomes_one_canonical_contract() -> None:
    item = UnresolvedFieldV2.model_validate(_legacy(_LEGACY_METADATA))

    assert item.completion_contract is not None
    assert item.completion_contract.contract_id == "supported_1"
    assert item.completion_contract.current_field == "trigger_timeframe"
    assert item.completion_contract.pending_fields == ["trigger_timeframe", "comparator"]


def test_the_answer_schema_keeps_no_workflow_progression_after_migration() -> None:
    """The whole point: an answer *shape* cannot also be a place progress is stored."""

    item = UnresolvedFieldV2.model_validate(_legacy(_LEGACY_METADATA))

    assert item.expected_answer_schema == {"type": "string"}
    assert SUPPORTED_REQUEST_SCHEMA_KEY not in item.expected_answer_schema
    assert not any(str(key).startswith("x-") for key in item.expected_answer_schema)


def test_every_accepted_value_and_every_evidence_span_survives() -> None:
    item = UnresolvedFieldV2.model_validate(_legacy(_LEGACY_METADATA))
    contract = item.completion_contract
    assert contract is not None

    assert contract.accepted_values == {"threshold": 5.0}
    assert contract.grounded_values["movement_direction"] == "up"
    assert contract.symbols == ["BTC/USDT"]
    assert contract.evidence_fragments == ["alert me when BTC rises 5%"]


def test_the_flat_reading_is_identical_to_what_callers_used_to_read() -> None:
    """A derived reading cannot drift from its source; a second stored copy always did."""

    item = UnresolvedFieldV2.model_validate(_legacy(_LEGACY_METADATA))
    flat = metadata_from_completion_contract(item.completion_contract)

    for key, value in _LEGACY_METADATA.items():
        if key == "schema_version":
            continue
        assert flat[key] == value, key


# ---------------------------------------------------------------------------------
# Partial, malformed and already-answered legacy records
# ---------------------------------------------------------------------------------


def test_a_partially_complete_workflow_keeps_the_values_already_settled() -> None:
    metadata = {
        **_LEGACY_METADATA,
        "missing_slots": ["comparator"],
        "next_field": "comparator",
        "trigger_timeframe": "1h",
    }
    contract = UnresolvedFieldV2.model_validate(_legacy(metadata)).completion_contract

    assert contract is not None
    assert contract.accepted_values["trigger_timeframe"] == "1h"
    assert contract.accepted_values["threshold"] == 5.0
    assert contract.pending_fields == ["comparator"]


def test_an_already_answered_legacy_question_carries_no_pending_field() -> None:
    metadata = {
        **_LEGACY_METADATA,
        "missing_slots": [],
        "next_field": "",
        "trigger_timeframe": "1h",
        "comparator": "gte",
    }
    contract = UnresolvedFieldV2.model_validate(_legacy(metadata)).completion_contract

    assert contract is not None
    assert contract.pending_fields == []
    assert contract.current_field == ""
    assert contract.accepted_values["comparator"] == "gte"


@pytest.mark.parametrize("malformed", [{}, "not a mapping", 17, None])
def test_malformed_legacy_metadata_is_dropped_never_guessed_at(malformed: object) -> None:
    item = UnresolvedFieldV2.model_validate(_legacy(malformed))

    assert SUPPORTED_REQUEST_SCHEMA_KEY not in item.expected_answer_schema
    # Nothing recoverable was there, so nothing is invented in its place. The blocker
    # itself is untouched and still visible.
    assert item.unresolved_id == "supported_1"


def test_a_legacy_question_with_no_workflow_still_gets_a_progress_record() -> None:
    """A blocker that names its missing values can be asked about deterministically."""

    item = UnresolvedFieldV2.model_validate(
        {
            "unresolved_id": "planner_1",
            "source_fragment": "alert me on a breakout",
            "target_type": "condition_creation",
            "question": "What defines the move?",
            "reason": "the mechanic is missing",
            "missing_slots": ["formula", "threshold"],
            "expected_answer_schema": {"type": "string"},
        }
    )

    assert item.completion_contract is not None
    assert item.completion_contract.pending_fields == ["formula", "threshold"]
    assert item.completion_contract.current_field == "formula"


def test_a_pending_typo_proposal_is_conversation_state_and_never_canonical() -> None:
    """A near miss is not an answer, so it must not reach the executable record."""

    item = UnresolvedFieldV2.model_validate(_legacy(_LEGACY_METADATA))
    contract = item.completion_contract
    assert contract is not None

    workflow = PendingClarificationWorkflow(
        workflow_id="supported_1",
        workflow_kind="supported_rule",
        question_id="q_1",
        current_field="trigger_timeframe",
        proposed_value="1h",
        proposed_evidence="qh",
    ).bound_to(contract)

    assert workflow.proposed_value == "1h"
    assert "trigger_timeframe" not in contract.accepted_values
    assert workflow.matches_canonical(contract) is True


# ---------------------------------------------------------------------------------
# Divergent copies fail closed
# ---------------------------------------------------------------------------------


def test_two_records_of_the_same_progress_that_disagree_are_refused() -> None:
    """Picking a winner silently is what a fail-closed rule exists to prevent."""

    other = ClarificationCompletionContract(
        contract_id="supported_1",
        pending_fields=["comparator"],
        current_field="comparator",
        accepted_values={"trigger_timeframe": "4h"},
    )
    with pytest.raises(ValidationError, match="two different records"):
        UnresolvedFieldV2.model_validate(
            _legacy(_LEGACY_METADATA, completion_contract=other)
        )


def test_two_records_that_agree_are_accepted_and_stay_one_record() -> None:
    same = completion_contract_from_metadata("supported_1", _LEGACY_METADATA)
    item = UnresolvedFieldV2.model_validate(
        _legacy(_LEGACY_METADATA, completion_contract=same)
    )

    assert item.completion_contract is not None
    assert item.completion_contract.contract_hash == same.contract_hash


def test_a_projection_bound_to_a_contract_that_moved_is_stale() -> None:
    contract = completion_contract_from_metadata("supported_1", _LEGACY_METADATA)
    workflow = PendingClarificationWorkflow(
        workflow_id="supported_1",
        workflow_kind="supported_rule",
        question_id="q_1",
        current_field="trigger_timeframe",
    ).bound_to(contract)

    moved = contract.model_copy(update={"accepted_values": {"threshold": 9.0}})

    assert workflow.matches_canonical(contract) is True
    assert workflow.matches_canonical(moved) is False
    assert workflow.matches_canonical(None) is False


def test_an_unbound_projection_claims_nothing_and_cannot_be_wrong() -> None:
    workflow = PendingClarificationWorkflow(
        workflow_id="supported_1",
        workflow_kind="supported_rule",
        question_id="q_1",
        current_field="trigger_timeframe",
    )

    assert workflow.canonical_contract_hash == ""
    assert workflow.matches_canonical(None) is True


# ---------------------------------------------------------------------------------
# No value may have two writers
# ---------------------------------------------------------------------------------


def test_a_value_cannot_be_both_grounded_and_answered() -> None:
    with pytest.raises(ValidationError, match="two writable owners"):
        ClarificationCompletionContract(
            contract_id="supported_1",
            accepted_values={"threshold": 5.0},
            grounded_values={"threshold": 9.0},
        )


def test_an_answered_field_cannot_still_be_queued() -> None:
    """It would be asked twice, and the second answer would overwrite the first."""

    with pytest.raises(ValidationError, match="answered and still queued"):
        ClarificationCompletionContract(
            contract_id="supported_1",
            pending_fields=["threshold"],
            current_field="threshold",
            accepted_values={"threshold": 5.0},
        )


def test_the_field_being_asked_must_be_one_of_the_fields_still_pending() -> None:
    with pytest.raises(ValidationError, match="not one of the fields still pending"):
        ClarificationCompletionContract(
            contract_id="supported_1",
            pending_fields=["comparator"],
            current_field="trigger_timeframe",
        )


def test_a_whole_draft_round_trips_through_json_without_losing_progress() -> None:
    """Stored sessions are JSON. Progress that does not survive that is progress lost."""

    draft = StrategyDraftV2(
        unresolved_fields=[UnresolvedFieldV2.model_validate(_legacy(_LEGACY_METADATA))]
    )
    restored = StrategyDraftV2.model_validate(draft.model_dump(mode="json"))

    before = draft.unresolved_fields[0].completion_contract
    after = restored.unresolved_fields[0].completion_contract
    assert before is not None
    assert after is not None
    assert after.contract_hash == before.contract_hash
    assert restored.unresolved_fields[0].expected_answer_schema == {"type": "string"}
