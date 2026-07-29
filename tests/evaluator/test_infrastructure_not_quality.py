"""Infrastructure failures must never be scored as chatbot quality.

Run 20260723T152343Z scored two truncated exchanges (`Server disconnected without
sending a response`) as chatbot failures. A transport fault, an expired login or a
budget stop says nothing about the answers the assistant gave.
"""

from __future__ import annotations

from hm_chatbot_eval.failures import FailureClass, FailureRecord
from hm_chatbot_eval.models import CaseResult, JudgeVerdict
from hm_chatbot_eval.report import aggregate
from hm_chatbot_eval.scenarios import build_scenario
from hm_chatbot_eval.topics import TOPIC_BY_ID

TOPIC = TOPIC_BY_ID["universe_mapping"]


def _record(failure_class: FailureClass) -> dict:
    return FailureRecord(
        failure_class=failure_class,
        role="target",
        stage="turn",
        retryable=False,
        case_id="case-1",
    ).to_dict()


def _case(
    scenario,
    *,
    passed: bool,
    failure: dict | None = None,
    score: float = 0.9,
) -> CaseResult:
    return CaseResult(
        run_id="infra-run",
        scenario=scenario,
        target_kind="backend",
        target_variant="current",
        started_at="2026-07-26T00:00:00Z",
        finished_at="2026-07-26T00:00:01Z",
        turns=[],
        deterministic_metrics={"schema_valid": 1.0 if passed else 0.0},
        judge=JudgeVerdict(
            passed=passed,
            score=score,
            confidence=0.9,
            dimension_scores={},
            failures=[],
            strengths=[],
            fixes=[],
            evidence=[],
        )
        if failure is None
        else None,
        structured_output={"symbols": ["BTCUSDT"]} if failure is None else None,
        structured_hash="hash-1" if failure is None else None,
        schema_errors=[],
        total_latency_ms=10.0,
        target_cost_usd=0.0,
        test_ai_cost_usd=0.0,
        passed=passed,
        error=None if failure is None else "RemoteProtocolError: disconnected",
        failure=failure,
    )


def test_a_truncated_exchange_is_reported_but_not_scored() -> None:
    scenario = build_scenario(TOPIC, 1, 7, max_turns=3)
    other = build_scenario(TOPIC, 2, 7, max_turns=3)
    summary = aggregate(
        [
            _case(scenario, passed=True),
            _case(other, passed=False, failure=_record(FailureClass.TARGET_PARTIAL_STREAM)),
        ]
    )
    assert summary["infrastructure_failed_cases"] == 1
    assert summary["infrastructure_failure_classes"] == ["TARGET_PARTIAL_STREAM"]
    assert summary["quality_measured_cases"] == 1
    # One answer was observed and it passed. The pass rate must say 100%, not 50%.
    assert summary["passed"] == 1
    assert summary["pass_rate"] == 1.0
    assert summary["measurement_coverage"] == 0.5
    assert summary["schema_valid_rate"] == 1.0
    assert summary["semantic_contract_pass_rate"] == 0.0


def test_a_case_lost_to_infrastructure_is_flagged_on_the_record() -> None:
    scenario = build_scenario(TOPIC, 1, 7, max_turns=3)
    case = _case(scenario, passed=False, failure=_record(FailureClass.UI_AUTH_EXPIRED))
    assert case.is_infrastructure_failure is True
    assert _case(scenario, passed=True).is_infrastructure_failure is False


def test_a_run_with_no_observed_answers_is_incomplete_not_failed() -> None:
    """Zero quality evidence means no verdict, not a failing verdict."""
    scenario = build_scenario(TOPIC, 1, 7, max_turns=3)
    summary = aggregate(
        [_case(scenario, passed=False, failure=_record(FailureClass.EVALUATOR_AUTH_FAILURE))]
    )
    assert summary["release_gate"] == "INCOMPLETE"
    assert summary["quality_measured_cases"] == 0


def test_a_topic_of_only_infrastructure_losses_reports_not_measured() -> None:
    scenario = build_scenario(TOPIC, 1, 7, max_turns=3)
    summary = aggregate(
        [_case(scenario, passed=False, failure=_record(FailureClass.TARGET_HTTP_5XX))]
    )
    row = next(r for r in summary["topics"] if r["topic_id"] == TOPIC.id)
    assert row["quality_measured_cases"] == 0
    assert row["infrastructure_failed_cases"] == 1
    assert row["criteria_not_measured"] is True
    assert TOPIC.id not in summary["critical_topics_failed"]


def test_a_genuine_quality_failure_still_fails_the_gate() -> None:
    """The exclusion is narrow: only cases carrying a failure record are excluded."""
    scenario = build_scenario(TOPIC, 1, 7, max_turns=3)
    summary = aggregate([_case(scenario, passed=False, score=0.1)])
    assert summary["infrastructure_failed_cases"] == 0
    assert summary["release_gate"] == "FAIL"


def test_semantic_failure_is_not_hidden_by_a_deferred_judge() -> None:
    scenario = build_scenario(TOPIC, 1, 7, max_turns=3)
    case = _case(scenario, passed=False, score=0.0)
    case.judge = None
    case.deterministic_metrics = {
        "schema_valid": 1.0,
        "semantic_contract_pass": 0.0,
    }

    summary = aggregate([case])

    assert summary["pending_judges"] == 1
    assert summary["quality_status"] == "FAIL"
    assert summary["release_gate"] == "FAIL"
