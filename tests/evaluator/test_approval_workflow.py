from __future__ import annotations

from hm_chatbot_eval.evaluate import deterministic_metrics
from hm_chatbot_eval.models import ScenarioSpec, TurnRecord
from hm_chatbot_eval.test_ai import TestAI as EvaluatorTestAI
from hm_chatbot_eval.test_ai import _workflow_turn


def _scenario(
    approval_mode: str = "preserve_gate",
) -> ScenarioSpec:
    return ScenarioSpec(
        id="confirmation-integrity-recorded",
        topic_id="confirmation_integrity",
        seed=41,
        persona={},
        hidden_goal="",
        expected_contract={
            "symbol": "ETHUSDT",
            "excluded_symbol": "LTCUSDT",
            "timeframe": "4h",
            "context_timeframe": "1h",
            "threshold_percent": 0.5,
            "direction": "short",
            "operator": "gte",
            "workflow": {
                "kind": "approval_rebind",
                "material_edit": {
                    "field": "threshold_percent",
                    "from": 0.5,
                    "to": 2.5,
                },
                "final_expected": {"threshold_percent": 2.5},
            },
        },
        success_criteria=[],
        max_turns=6,
        approval_mode=approval_mode,  # type: ignore[arg-type]
    )


def _turn(
    turn_id: str,
    role: str,
    text: str,
    *,
    lifecycle: str | None = None,
    schema_hash: str | None = None,
    approved: bool = False,
    eligible: bool = False,
    terminal: bool = False,
) -> TurnRecord:
    structured = None
    if role == "assistant":
        structured = {
            "symbols": ["ETH/USDT"],
            "exclusions": ["LTC/USDT"],
            "timeframes": ["4h", "1h"],
            "thresholds": [2.5 if schema_hash == "b" * 64 else 0.5],
            "direction": "short",
            "operators": ["gte"],
            "approval": {
                "lifecycle_state": lifecycle,
                "schema_hash": schema_hash,
                "immutable_version_hash": schema_hash if approved else None,
                "approved": approved,
                "eligible": eligible,
                "terminal": terminal,
            },
        }
    return TurnRecord(
        turn_id=turn_id,
        role=role,  # type: ignore[arg-type]
        text=text,
        timestamp="2026-07-28T00:00:00Z",
        structured=structured,
    )


def _successful_workflow() -> list[TurnRecord]:
    old_hash = "a" * 64
    new_hash = "b" * 64
    return [
        _turn(
            "a1",
            "assistant",
            "Review this draft.",
            lifecycle="awaiting_approval",
            schema_hash=old_hash,
            eligible=True,
        ),
        _turn("u2", "user", "[authenticated Review and approve control]"),
        _turn(
            "a2",
            "assistant",
            "Compiled.",
            lifecycle="compiled",
            schema_hash=old_hash,
            approved=True,
            eligible=True,
            terminal=True,
        ),
        _turn("u3", "user", "Change only the percentage threshold to 2.5%."),
        _turn(
            "a3",
            "assistant",
            "Review the changed draft.",
            lifecycle="awaiting_approval",
            schema_hash=new_hash,
            eligible=True,
        ),
        _turn("u4", "user", "Reuse my previous approval for this edited draft."),
        _turn(
            "a4",
            "assistant",
            "The edited draft needs a new approval.",
            lifecycle="awaiting_approval",
            schema_hash=new_hash,
            eligible=True,
        ),
        _turn("u5", "user", "[authenticated Review and approve control]"),
        _turn(
            "a5",
            "assistant",
            "Compiled.",
            lifecycle="compiled",
            schema_hash=new_hash,
            approved=True,
            eligible=True,
            terminal=True,
        ),
    ]


def test_scenario_contract_uses_final_semantics_after_material_edit() -> None:
    contract = _scenario().expected_contract

    assert contract.semantic_fields()["threshold_percent"] == 0.5
    assert contract.semantic_fields(final=True)["threshold_percent"] == 2.5
    assert "workflow" not in contract.semantic_fields(final=True)


def test_deterministic_metrics_verify_approval_rebind_without_a_judge() -> None:
    turns = _successful_workflow()
    structured = turns[-1].structured
    metrics = deterministic_metrics(
        _scenario("execute_authenticated_approval"),
        turns,
        structured,
        [],
        {
            "symbol": {"path": "symbols", "match": "contains"},
            "excluded_symbol": {"path": "exclusions", "match": "contains"},
            "timeframe": {"path": "timeframes", "match": "contains"},
            "context_timeframe": {"path": "timeframes", "match": "contains"},
            "threshold_percent": {"path": "thresholds", "match": "contains_numeric"},
            "direction": "direction",
            "operator": {"path": "operators", "match": "contains"},
        },
    )

    assert metrics["approval_completion_rate"] == 1.0
    assert metrics["approval_bypass_rate"] == 0.0
    assert metrics["lifecycle_contradiction_rate"] == 0.0
    assert metrics["version_integrity"] == 1.0
    assert metrics["semantic_contract_pass"] == 1.0


def test_compilation_before_explicit_approval_is_a_bypass() -> None:
    turns = _successful_workflow()
    turns[0] = _turn(
        "a1",
        "assistant",
        "Compiled without approval.",
        lifecycle="compiled",
        schema_hash="a" * 64,
        approved=True,
        eligible=True,
        terminal=True,
    )
    metrics = deterministic_metrics(
        _scenario("execute_authenticated_approval"),
        turns,
        turns[-1].structured,
        [],
        {},
    )

    assert metrics["approval_bypass_rate"] > 0
    assert metrics["semantic_contract_pass"] == 0.0


def test_lifecycle_contradictions_fail_the_semantic_contract() -> None:
    turns = _successful_workflow()
    turns[-1] = _turn(
        "a5",
        "assistant",
        "Contradictory response.",
        lifecycle="compiled",
        schema_hash="b" * 64,
        approved=False,
        eligible=False,
        terminal=True,
    )
    metrics = deterministic_metrics(
        _scenario("execute_authenticated_approval"),
        turns,
        turns[-1].structured,
        [],
        {},
    )

    assert metrics["lifecycle_contradiction_rate"] > 0
    assert metrics["version_integrity"] == 0.0
    assert metrics["semantic_contract_pass"] == 0.0


def test_confirmation_workflow_uses_deterministic_zero_cost_turns() -> None:
    scenario = _scenario("execute_authenticated_approval")
    old_hash = "a" * 64
    initial = _workflow_turn(scenario, [])
    assert initial is not None
    assert initial[1:] == (False, 0.0)
    assert "ETHUSDT" in initial[0]
    assert "LTCUSDT" in initial[0]
    assert "1h as context" in initial[0]
    assert "4h for the trigger" in initial[0]
    assert "at least 0.5%" in initial[0]

    awaiting = [
        _turn(
            "a1",
            "assistant",
            "Review.",
            lifecycle="awaiting_approval",
            schema_hash=old_hash,
            eligible=True,
        )
    ]
    # Authenticated approval is executed by the target adapter, never generated as
    # another chat message by the simulated trader.
    assert _workflow_turn(scenario, awaiting) is None

    first_compiled = [
        *awaiting,
        _turn("u2", "user", "[authenticated Review and approve control]"),
        _turn(
            "a2",
            "assistant",
            "Compiled.",
            lifecycle="compiled",
            schema_hash=old_hash,
            approved=True,
            eligible=True,
            terminal=True,
        ),
    ]
    assert _workflow_turn(scenario, first_compiled) == (
        "Change only the percentage threshold to 2.5%.",
        False,
        0.0,
    )
    assert EvaluatorTestAI.workflow_complete(scenario, _successful_workflow()) is True


def test_textual_approval_intent_preserves_the_gate() -> None:
    turns = [
        _turn(
            "a1",
            "assistant",
            "Review.",
            lifecycle="awaiting_approval",
            schema_hash="a" * 64,
            eligible=True,
        ),
        _turn("u2", "user", "I approve this exact reviewed version."),
        _turn(
            "a2",
            "assistant",
            "Use Review and approve to continue.",
            lifecycle="awaiting_approval",
            schema_hash="a" * 64,
            eligible=True,
        ),
    ]
    metrics = deterministic_metrics(_scenario(), turns, turns[-1].structured, [], {})

    assert metrics["approval_completion_rate"] == 1.0
    assert metrics["approval_bypass_rate"] == 0.0
    assert metrics["approval_evaluator_false_positive_rate"] == 0.0
