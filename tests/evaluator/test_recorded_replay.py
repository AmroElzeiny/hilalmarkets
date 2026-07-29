from __future__ import annotations

import json
from pathlib import Path

from hm_chatbot_eval.config import Settings
from hm_chatbot_eval.recorded_replay import (
    _expected_contract_measurement_issues,
    replay_recorded_run,
)


async def test_replays_every_case_from_a_recorded_run_without_api_cost(
    tmp_path: Path,
) -> None:
    output = tmp_path / "runs"
    source = output / "source"
    source.mkdir(parents=True)
    recorded = {
        "scenario": {
            "id": "mapping-001",
            "topic_id": "operator_mapping",
            "seed": 1,
            "persona": {},
            "hidden_goal": "",
            "expected_contract": {
                "symbol": "BTCUSDT",
                "excluded_symbol": "ETHUSDT",
                "timeframe": "15m",
                "context_timeframe": "1h",
                "threshold_percent": 5.0,
                "direction": "long",
                "operator": "gte",
                "requires_explicit_approval": True,
                "must_not_assign_sharia_status": True,
            },
            "success_criteria": [],
            "max_turns": 3,
            "fault": None,
        },
        "target_kind": "backend",
        "turns": [
            {
                "role": "user",
                "text": (
                    "BTCUSDT only, exclude ETHUSDT. Use 1h context and a 15m "
                    "trigger. Long when close-to-close percentage change is at "
                    "least 5% (gte)."
                ),
            }
        ],
    }
    (source / "cases.jsonl").write_text(
        json.dumps(recorded) + "\n" + json.dumps(recorded) + "\n",
        encoding="utf-8",
    )
    settings = Settings(eval_output_dir=output)
    results, summary, run_dir = await replay_recorded_run(
        settings,
        source_run_id="source",
        run_id="result",
    )
    assert len(results) == 2
    assert summary["cases"] == 2
    assert summary["total_test_cost_usd"] == 0
    assert (run_dir / "report.html").exists()
    assert (run_dir / "failures.csv").exists()


async def test_repository_regression_artifact_contains_all_30_recorded_cases(
    tmp_path: Path,
) -> None:
    source_run = Path("chatbot_eval_runs/20260726T171424Z/cases.jsonl")
    if not source_run.exists():
        return
    settings = Settings(eval_output_dir=tmp_path)
    target = tmp_path / "latest"
    target.mkdir()
    (target / "cases.jsonl").write_text(
        source_run.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    results, summary, _ = await replay_recorded_run(
        settings,
        source_run_id="latest",
        run_id="result",
    )
    assert len(results) == 30
    assert summary["cases"] == 30


async def test_latest_22_case_artifact_is_fully_replayable_without_api_cost(
    tmp_path: Path,
) -> None:
    source_run = Path("chatbot_eval_runs/20260729T081005Z/cases.jsonl")
    if not source_run.exists():
        return
    settings = Settings(eval_output_dir=tmp_path)
    target = tmp_path / "latest-22"
    target.mkdir()
    (target / "cases.jsonl").write_text(
        source_run.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    results, summary, _ = await replay_recorded_run(
        settings,
        source_run_id="latest-22",
        run_id="result-22",
    )
    assert len(results) == 22
    assert summary["cases"] == 22
    assert summary["total_test_cost_usd"] == 0


def test_expected_symbol_absent_from_user_turns_is_not_measured() -> None:
    issues = _expected_contract_measurement_issues(
        {"symbol": "LTCUSDT", "excluded_symbol": "XRPUSDT"},
        ["Build the setup and exclude XRPUSDT."],
    )
    assert issues == ["expected_symbol_absent_from_recorded_user_turns:LTCUSDT"]


def test_open_final_definition_is_not_scored_as_compiled_quality() -> None:
    issues = _expected_contract_measurement_issues(
        {"direction": "long"},
        ["Should the trigger use a candle close or a wick touch?"],
        activation_blocked=True,
    )
    assert "expected_direction_absent_from_recorded_user_turns:long" in issues
    assert "recorded_conversation_ends_with_open_definition" in issues
