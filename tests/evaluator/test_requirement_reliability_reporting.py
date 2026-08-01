from __future__ import annotations

from hm_chatbot_eval.models import CaseResult, TurnRecord
from hm_chatbot_eval.report import _metric_values, aggregate
from hm_chatbot_eval.scenarios import build_scenario
from hm_chatbot_eval.topics import TOPICS


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

