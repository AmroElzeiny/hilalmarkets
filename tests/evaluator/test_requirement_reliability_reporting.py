from __future__ import annotations

from hm_chatbot_eval.evaluate import deterministic_metrics
from hm_chatbot_eval.models import CaseResult, ScenarioSpec, TurnRecord
from hm_chatbot_eval.report import _metric_values, aggregate
from hm_chatbot_eval.scenarios import build_scenario
from hm_chatbot_eval.topics import TOPICS
from hm_chatbot_eval.util import semantic_contract_hash


def _case(*, target: str = "backend", canonical_state=None) -> CaseResult:
    scenario = build_scenario(TOPICS[0], 1, 42)
    unresolved = {
        "unresolved_id": "threshold-question",
        "blocking": True,
    }
    blocking = {
        "requirement_id": "req-threshold",
        "blocking": True,
        "satisfied": False,
    }
    first_state = {
        "unresolved_fields": [unresolved],
        "requirement_states": [blocking],
    }
    final_state = canonical_state or first_state
    structured = {
        "approval": {
            "eligible": True,
            "approved": False,
            "lifecycle_state": "awaiting_approval",
        }
    }
    return CaseResult(
        run_id="requirement-report",
        scenario=scenario,
        target_kind=target,
        target_variant="current",
        started_at="2026-08-01T00:00:00Z",
        finished_at="2026-08-01T00:00:01Z",
        turns=[
            TurnRecord(
                turn_id="u1",
                role="user",
                text="set the threshold",
                timestamp="2026-08-01T00:00:00Z",
            ),
            TurnRecord(
                turn_id="a1",
                role="assistant",
                text="Which threshold?",
                timestamp="2026-08-01T00:00:00Z",
                latency_ms=10,
                canonical_state=first_state,
                usage={
                    "estimated_cost_usd": 0.01,
                    "planner_repair_attempt_count": 1,
                    "planner_repair_success_count": 1,
                },
            ),
            TurnRecord(
                turn_id="u2",
                role="user",
                text="5 percent",
                timestamp="2026-08-01T00:00:01Z",
            ),
            TurnRecord(
                turn_id="a2",
                role="assistant",
                text="Review the draft.",
                timestamp="2026-08-01T00:00:01Z",
                latency_ms=20,
                structured=structured,
                canonical_state=final_state,
                usage={"estimated_cost_usd": 0.02},
            ),
        ],
        deterministic_metrics={"schema_valid": 1.0, "semantic_contract_pass": 1.0},
        judge=None,
        structured_output=structured,
        structured_hash="hash",
        schema_errors=[],
        total_latency_ms=30,
        target_cost_usd=0.03,
        test_ai_cost_usd=0,
        passed=True,
        eventual_case_success=True,
        canonical_state=final_state,
    )


def test_summary_reports_repair_questions_and_milestones_separately() -> None:
    summary = aggregate([_case()])

    assert summary["planner_repair_attempts"] == 1
    assert summary["planner_repair_success_rate"] == 1
    assert summary["repeated_question_rate"] == 0.5
    assert summary["unnecessary_question_rate"] == 0
    assert summary["turns_to_first_valid_draft"] == 2
    assert summary["latency_ms_to_approval_eligibility"] == 30
    assert summary["cost_usd_to_approval_eligibility"] == 0.03


def test_requirement_parity_uses_canonical_state_even_without_a_compiled_contract() -> None:
    backend = _case(target="backend")
    ui = _case(target="ui")
    backend.structured_output = None
    backend.structured_hash = None
    ui.structured_output = None
    ui.structured_hash = None

    assert _metric_values(
        [backend, ui], "backend_ui_requirement_state_match"
    ) == [1.0]


def test_backend_ui_parity_ignores_fresh_session_provenance_but_not_semantics() -> None:
    """Fresh sessions must compare the same contract without hiding a real change."""

    backend = {
        "approval": {
            "eligible": True,
            "approved": False,
            "lifecycle_state": "awaiting_approval",
            "schema_hash": "backend-session-bound-hash",
        },
        "requirement_states": [
            {
                "requirement_id": "backend-requirement",
                "source_turn_id": "backend-turn",
                "source_segment_id": "backend-segment",
                "target_path": "condition_ast.condition_aaaaaaaaaaaa.threshold",
                "normalized_value": 5,
                "satisfied": True,
            }
        ],
        "semantic_role_assignments": [
            {
                "source_turn_id": "backend-turn",
                "source_segment_id": "backend-segment",
                "target_path": "condition_ast.condition_aaaaaaaaaaaa.trigger_timeframe",
                "normalized_value": "5m",
                "role": "trigger",
            }
        ],
    }
    ui = {
        "approval": {
            "eligible": True,
            "approved": False,
            "lifecycle_state": "awaiting_approval",
            "schema_hash": "ui-session-bound-hash",
        },
        "requirement_states": [
            {
                "requirement_id": "ui-requirement",
                "source_turn_id": "ui-turn",
                "source_segment_id": "ui-segment",
                "target_path": "condition_ast.condition_bbbbbbbbbbbb.threshold",
                "normalized_value": 5,
                "satisfied": True,
            }
        ],
        "semantic_role_assignments": [
            {
                "source_turn_id": "ui-turn",
                "source_segment_id": "ui-segment",
                "target_path": "condition_ast.condition_bbbbbbbbbbbb.trigger_timeframe",
                "normalized_value": "5m",
                "role": "trigger",
            }
        ],
    }
    assert semantic_contract_hash(backend) == semantic_contract_hash(ui)

    ui["semantic_role_assignments"][0]["normalized_value"] = "15m"
    assert semantic_contract_hash(backend) != semantic_contract_hash(ui)


def test_unsafe_guess_rate_is_a_deterministic_zero_is_good_rate() -> None:
    scenario = ScenarioSpec(
        id="ambiguous-unsafe-rate",
        topic_id="ambiguous_trading_language",
        seed=1,
        persona={},
        hidden_goal="Include BTC/USDT without inventing another asset.",
        expected_contract={"symbol": "BTC/USDT"},
        success_criteria=[
            {
                "metric": "unsafe_guess_rate",
                "operator": "==",
                "threshold": 0,
                "description": "No silent invention",
                "critical": True,
            }
        ],
        max_turns=2,
    )
    structured = {"symbols": ["BTC/USDT"]}
    metrics = deterministic_metrics(
        scenario,
        [
            TurnRecord(
                turn_id="a1",
                role="assistant",
                text="BTC/USDT is included.",
                timestamp="2026-08-02T00:00:00Z",
            )
        ],
        structured,
        [],
        {"symbol": "symbols"},
    )

    assert metrics["unsafe_guess_rate"] == 0.0
    result = _case()
    result.scenario = scenario
    result.deterministic_metrics = metrics
    result.judge = None
    assert _metric_values([result], "unsafe_guess_rate") == [0.0]
