"""Drift criteria must report NOT_MEASURED instead of inventing a regression.

Run 20260723T152343Z reported `model_version_drift` as a critical FAIL with
`pass_flip_rate actual=None measured_cases=0`. Nothing had been compared: only one
variant of the scenario ran, so there was no baseline/candidate pair at all.
"""

from __future__ import annotations

from dataclasses import replace

from hm_chatbot_eval.models import CaseResult, JudgeVerdict
from hm_chatbot_eval.report import aggregate
from hm_chatbot_eval.scenarios import build_scenario
from hm_chatbot_eval.topics import TOPIC_BY_ID

DRIFT_TOPIC = TOPIC_BY_ID["model_version_drift"]


def _case(
    scenario,
    *,
    variant: str,
    passed: bool = True,
    score: float = 0.9,
) -> CaseResult:
    return CaseResult(
        run_id="drift-run",
        scenario=scenario,
        target_kind="backend",
        target_variant=variant,
        started_at="2026-07-23T00:00:00Z",
        finished_at="2026-07-23T00:00:01Z",
        turns=[],
        deterministic_metrics={"schema_valid": 1.0},
        judge=JudgeVerdict(
            passed=passed,
            score=score,
            confidence=0.9,
            dimension_scores={},
            failures=[],
            strengths=[],
            fixes=[],
            evidence=[],
        ),
        structured_output={"symbols": ["BTCUSDT"]},
        structured_hash="hash-1",
        schema_errors=[],
        total_latency_ms=10.0,
        target_cost_usd=0.0,
        test_ai_cost_usd=0.0,
        passed=passed,
    )


def _drift_criteria(summary: dict) -> dict[str, dict]:
    row = next(r for r in summary["topics"] if r["topic_id"] == "model_version_drift")
    return {item["metric"]: item for item in row["criteria"]}


def test_single_variant_reports_not_measured_not_failed() -> None:
    scenario = build_scenario(DRIFT_TOPIC, 1, 42)
    summary = aggregate([_case(scenario, variant="current")])
    criteria = _drift_criteria(summary)

    for metric in ("pass_flip_rate", "semantic_score_delta_abs"):
        assert criteria[metric]["status"] == "NOT_MEASURED"
        assert criteria[metric]["measured_cases"] == 0
        assert criteria[metric]["actual"] is None
        assert criteria[metric]["requires_paired_variants"] is True

    assert "model_version_drift" not in summary["critical_topics_failed"]
    assert "model_version_drift" in summary["critical_topics_not_measured"]
    assert summary["release_gate"] == "INCOMPLETE"


def test_repeating_the_same_variant_is_still_not_a_comparison() -> None:
    """Two runs of the same variant are repetitions, not a baseline/candidate pair."""
    scenario = build_scenario(DRIFT_TOPIC, 1, 42)
    summary = aggregate([_case(scenario, variant="current"), _case(scenario, variant="current")])
    criteria = _drift_criteria(summary)
    assert criteria["pass_flip_rate"]["status"] == "NOT_MEASURED"


def test_paired_variants_are_measured_and_can_pass() -> None:
    scenario = build_scenario(DRIFT_TOPIC, 1, 42)
    summary = aggregate(
        [
            _case(scenario, variant="baseline", passed=True, score=0.90),
            _case(scenario, variant="candidate", passed=True, score=0.91),
        ]
    )
    criteria = _drift_criteria(summary)

    assert criteria["pass_flip_rate"]["status"] == "PASS"
    assert criteria["pass_flip_rate"]["actual"] == 0.0
    assert criteria["semantic_score_delta_abs"]["status"] == "PASS"
    assert criteria["semantic_score_delta_abs"]["measured_cases"] == 1


def test_paired_variants_detect_a_real_flip() -> None:
    scenario = build_scenario(DRIFT_TOPIC, 1, 42)
    summary = aggregate(
        [
            _case(scenario, variant="baseline", passed=True, score=0.95),
            _case(scenario, variant="candidate", passed=False, score=0.10),
        ]
    )
    criteria = _drift_criteria(summary)

    assert criteria["pass_flip_rate"]["status"] == "FAIL"
    assert criteria["pass_flip_rate"]["actual"] == 1.0
    assert "model_version_drift" in summary["critical_topics_failed"]


def test_variants_under_different_contracts_are_not_compared() -> None:
    """A drift number only means something when both runs share the contract."""
    baseline = build_scenario(DRIFT_TOPIC, 1, 42)
    candidate_scenario = replace(
        build_scenario(DRIFT_TOPIC, 1, 42),
        expected_contract={"symbol": "SOMETHING-ELSE"},
    )
    summary = aggregate(
        [
            _case(baseline, variant="baseline"),
            _case(candidate_scenario, variant="candidate", passed=False, score=0.0),
        ]
    )
    criteria = _drift_criteria(summary)
    assert criteria["pass_flip_rate"]["status"] == "NOT_MEASURED"
