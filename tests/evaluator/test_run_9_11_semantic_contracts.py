"""Regression proof for the evaluator defects exposed by Runs 9-11."""

from __future__ import annotations

from hm_chatbot_eval.evaluate import grouping_metrics, transport_parity_metrics
from hm_chatbot_eval.models import ScenarioContract, TurnRecord
from hm_chatbot_eval.runner import _product_failure_record
from hm_chatbot_eval.targets.base import TargetReply
from hm_chatbot_eval.test_ai import _repeated_known_failure


def _leaf(timeframe: str, direction: str, threshold: float) -> dict[str, object]:
    return {
        "node_type": "condition",
        "formula": "close_to_close_percentage",
        "movement_direction": direction,
        "operator": "gte",
        "threshold": threshold,
        "trigger_timeframe": timeframe,
    }


def test_topology_comparison_preserves_leaf_membership_not_only_shape() -> None:
    contract = ScenarioContract(
        {
            "expected_condition_leaves": {
                "a": {
                    "formula": "close_to_close_percentage",
                    "movement_direction": "up",
                    "operator": "gte",
                    "threshold_percent": 1,
                    "trigger_timeframe": "15m",
                },
                "b": {
                    "formula": "close_to_close_percentage",
                    "movement_direction": "down",
                    "operator": "gte",
                    "threshold_percent": 2,
                    "trigger_timeframe": "1h",
                },
                "c": {
                    "formula": "close_to_close_percentage",
                    "movement_direction": "up",
                    "operator": "gte",
                    "threshold_percent": 3,
                    "trigger_timeframe": "4h",
                },
            },
            "expected_boolean_groups": [
                {"group_ref": "g1", "operator": "or", "child_refs": ["b", "c"]},
                {"group_ref": "g2", "operator": "and", "child_refs": ["a", "g1"]},
            ],
            "expected_root_ref": "g2",
        }
    )
    wrong_membership = {
        "condition_ast": {
            "node_type": "and",
            "children": [
                {
                    "node_type": "or",
                    "children": [_leaf("15m", "up", 1), _leaf("1h", "down", 2)],
                },
                _leaf("4h", "up", 3),
            ],
        }
    }
    assert grouping_metrics(contract, wrong_membership)["grouping_accuracy"] == 0.0


def test_transport_parity_requires_explicit_rendered_proof() -> None:
    turn = TurnRecord(
        turn_id="a1",
        role="assistant",
        text="preview",
        timestamp="2026-08-03T00:00:00Z",
        ui_contract={
            "captured": True,
            "canonical_hash_match": True,
            "canvas_node_match": False,
        },
    )
    assert transport_parity_metrics([turn]) == {
        "transport_contract_parity": 0.0,
        "transport_verified_turns": 1.0,
    }


def test_safe_typed_product_failure_proof_is_extracted() -> None:
    reply = TargetReply(
        text="Nothing changed.",
        latency_ms=10,
        status_code=422,
        raw={
            "error": {"error_code": "PLANNER_SEMANTIC_OMISSION"},
            "failure_proof": {
                "failure_class": "PLANNER_SEMANTIC_OMISSION",
                "failure_owner": "model",
                "semantic_paths": ["condition.trigger_timeframe"],
                "source_excerpt": "trigger on 15m",
                "expected_values": {"condition.trigger_timeframe": "15m"},
                "observed_values": {"condition.trigger_timeframe": "absent"},
                "repair_eligible": True,
                "repair_decision": "SCALAR_SEMANTIC_DELTA",
                "support_reference": "setup-ref-123",
            },
        },
    )
    proof = _product_failure_record(reply)
    assert proof is not None
    assert proof["failure_class"] == "PLANNER_SEMANTIC_OMISSION"
    assert proof["semantic_paths"] == ["condition.trigger_timeframe"]
    assert proof["support_reference"] == "setup-ref-123"


def test_challenger_stops_only_on_the_same_typed_failure_fingerprint() -> None:
    turns = [
        TurnRecord("u1", "user", "trigger on 15m", "t1"),
        TurnRecord(
            "a1",
            "assistant",
            "Nothing changed.",
            "t2",
            status_code=422,
            error="semantic omission",
            product_failure={
                "failure_class": "PLANNER_SEMANTIC_OMISSION",
                "semantic_paths": ["condition.trigger_timeframe"],
                "support_reference": "same-proof",
            },
        ),
        TurnRecord("u2", "user", "15m is the trigger", "t3"),
        TurnRecord(
            "a2",
            "assistant",
            "Nothing changed.",
            "t4",
            status_code=422,
            error="semantic omission",
            product_failure={
                "failure_class": "PLANNER_SEMANTIC_OMISSION",
                "semantic_paths": ["condition.trigger_timeframe"],
                "support_reference": "same-proof",
            },
        ),
    ]
    assert _repeated_known_failure(turns)
    turns[-1].product_failure = {
        **(turns[-1].product_failure or {}),
        "support_reference": "different-proof",
    }
    assert not _repeated_known_failure(turns)
