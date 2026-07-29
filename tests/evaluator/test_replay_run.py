"""Resumable replay of a prior run.

Run 20260725T122105Z stopped at its $2.50 budget with cases still planned. Verifying
a fix required paying for the whole run again, because `replay` handles exactly one
scenario. These cases pin the plan reconstruction, the checkpoint and the rules for
reusing a cached judge verdict.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from hm_chatbot_eval.models import CaseResult, JudgeVerdict
from hm_chatbot_eval.replay import (
    JUDGE_MODES,
    EventLog,
    ReplayItem,
    RunState,
    _apply_cached_judge,
    case_key,
    load_source_items,
)
from hm_chatbot_eval.scenarios import build_scenario
from hm_chatbot_eval.topics import TOPIC_BY_ID

TOPIC = TOPIC_BY_ID["universe_mapping"]


def _verdict(passed: bool, score: float = 0.9) -> JudgeVerdict:
    return JudgeVerdict(
        passed=passed,
        score=score,
        confidence=0.9,
        dimension_scores={},
        failures=[],
        strengths=[],
        fixes=[],
        evidence=[],
    )


def _case(
    scenario,
    *,
    passed: bool,
    kind: str = "backend",
    variant: str = "current",
    structured_hash: str | None = "hash-1",
) -> CaseResult:
    return CaseResult(
        run_id="source-run",
        scenario=scenario,
        target_kind=kind,
        target_variant=variant,
        started_at="2026-07-26T00:00:00Z",
        finished_at="2026-07-26T00:00:01Z",
        turns=[],
        deterministic_metrics={"schema_valid": 1.0},
        judge=_verdict(passed),
        structured_output={"symbols": ["BTCUSDT"]},
        structured_hash=structured_hash,
        schema_errors=[],
        total_latency_ms=10.0,
        target_cost_usd=0.0,
        test_ai_cost_usd=0.0,
        passed=passed,
    )


def _write_source(tmp_path: Path, cases: list[CaseResult]) -> Path:
    run_dir = tmp_path / "source-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "cases.jsonl").open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case.to_dict(), ensure_ascii=False) + "\n")
    return run_dir


def test_the_plan_is_rebuilt_from_the_source_run(tmp_path: Path) -> None:
    first = build_scenario(TOPIC, 1, 7, max_turns=3)
    second = build_scenario(TOPIC, 2, 7, max_turns=3)
    run_dir = _write_source(tmp_path, [_case(first, passed=True), _case(second, passed=False)])
    items = load_source_items(run_dir)
    assert [item.scenario.id for item in items] == [first.id, second.id]
    assert all(item.target_kind == "backend" for item in items)


def test_only_failed_replays_exactly_the_failed_cases(tmp_path: Path) -> None:
    first = build_scenario(TOPIC, 1, 7, max_turns=3)
    second = build_scenario(TOPIC, 2, 7, max_turns=3)
    run_dir = _write_source(tmp_path, [_case(first, passed=True), _case(second, passed=False)])
    items = load_source_items(run_dir, only_failed=True)
    assert [item.scenario.id for item in items] == [second.id]


def test_a_target_filter_keeps_only_that_target(tmp_path: Path) -> None:
    scenario = build_scenario(TOPIC, 1, 7, max_turns=3)
    run_dir = _write_source(
        tmp_path,
        [
            _case(scenario, passed=False, kind="backend"),
            _case(scenario, passed=False, kind="ui"),
        ],
    )
    assert [i.target_kind for i in load_source_items(run_dir, target_kind="ui")] == ["ui"]
    assert len(load_source_items(run_dir)) == 2


def test_the_same_case_is_never_planned_twice(tmp_path: Path) -> None:
    """A source run repeats scenarios; the replay plan must be a set of units."""
    scenario = build_scenario(TOPIC, 1, 7, max_turns=3)
    run_dir = _write_source(
        tmp_path, [_case(scenario, passed=False), _case(scenario, passed=False)]
    )
    assert len(load_source_items(run_dir)) == 1


def test_a_missing_source_run_is_reported_not_guessed(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_source_items(tmp_path / "nope")


def test_state_records_what_remains(tmp_path: Path) -> None:
    state = RunState(
        run_id="replay-1",
        source_run_id="source-run",
        judge_mode="online",
        only_failed=True,
        planned=["a|backend|current", "b|backend|current"],
    )
    assert state.remaining == ["a|backend|current", "b|backend|current"]
    state.completed.append("a|backend|current")
    assert state.remaining == ["b|backend|current"]


def test_state_survives_a_save_and_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "run_state.json"
    state = RunState(
        run_id="replay-1",
        source_run_id="source-run",
        judge_mode="cached-or-deferred",
        only_failed=False,
        planned=["a|backend|current"],
        completed=["a|backend|current"],
        execution_status="STOPPED_BUDGET",
        execution_error="stopped",
    )
    state.save(path)
    loaded = RunState.load(path)
    assert loaded is not None
    assert loaded.completed == ["a|backend|current"]
    assert loaded.remaining == []
    assert loaded.execution_status == "STOPPED_BUDGET"
    assert loaded.updated_at


def test_an_unreadable_checkpoint_does_not_crash_the_replay(tmp_path: Path) -> None:
    path = tmp_path / "run_state.json"
    path.write_text("{not json", encoding="utf-8")
    assert RunState.load(path) is None
    assert RunState.load(tmp_path / "absent.json") is None


def test_events_are_appended_one_object_per_line(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "run_events.jsonl")
    log.emit("run_started", planned=2)
    log.emit("case_completed", case="a|backend|current", passed=True)
    lines = (tmp_path / "run_events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["event"] == "run_started"
    assert first["planned"] == 2
    assert second["passed"] is True
    assert all("at" in json.loads(line) for line in lines)


def test_case_key_identifies_scenario_target_and_variant() -> None:
    assert case_key("s1", "backend", "current") == "s1|backend|current"
    assert case_key("s1", "backend", "current") != case_key("s1", "ui", "current")
    assert case_key("s1", "backend", "current") != case_key("s1", "backend", "next")


def _item(scenario, *, source_hash: str | None, judge_passed: bool = True) -> ReplayItem:
    return ReplayItem(
        scenario=scenario,
        target_kind="backend",
        variant={"name": "current"},
        source_passed=judge_passed,
        source_judge={
            "passed": judge_passed,
            "score": 0.88,
            "confidence": 0.9,
            "dimension_scores": {},
            "failures": [],
            "strengths": [],
            "fixes": [],
            "evidence": [],
        },
        source_structured_hash=source_hash,
    )


def test_a_cached_verdict_is_reused_when_the_output_is_identical() -> None:
    scenario = build_scenario(TOPIC, 1, 7, max_turns=3)
    fresh = replace(_case(scenario, passed=False), judge=None, passed=False)
    result = _apply_cached_judge(fresh, _item(scenario, source_hash="hash-1"))
    assert result.judge is not None
    assert result.judge.score == pytest.approx(0.88)
    assert result.passed is True


def test_a_changed_output_never_inherits_the_old_verdict() -> None:
    """The whole point of a replay is to detect a changed answer."""
    scenario = build_scenario(TOPIC, 1, 7, max_turns=3)
    fresh = replace(
        _case(scenario, passed=False), judge=None, passed=False, structured_hash="hash-2"
    )
    result = _apply_cached_judge(fresh, _item(scenario, source_hash="hash-1"))
    assert result.judge is None
    assert result.passed is False


def test_no_structured_output_never_inherits_a_verdict() -> None:
    scenario = build_scenario(TOPIC, 1, 7, max_turns=3)
    fresh = replace(_case(scenario, passed=False), judge=None, passed=False, structured_hash=None)
    assert _apply_cached_judge(fresh, _item(scenario, source_hash=None)).judge is None


def test_a_fresh_online_verdict_is_never_overwritten_by_the_cache() -> None:
    scenario = build_scenario(TOPIC, 1, 7, max_turns=3)
    fresh = _case(scenario, passed=False)
    result = _apply_cached_judge(fresh, _item(scenario, source_hash="hash-1"))
    assert result.judge is not None
    assert result.judge.passed is False


def test_the_supported_judge_modes_are_explicit() -> None:
    assert JUDGE_MODES == ("online", "deferred", "cached-or-deferred")


class _StubSettings:
    def __init__(self, root: Path) -> None:
        self.eval_output_dir = root
        self.target_variants = [{"name": "current"}]


class _StubRunner:
    """Runs planned cases from a script, stopping on budget where told to."""

    def __init__(self, root: Path, run_id: str, *, stop_after: int | None = None) -> None:
        from hm_chatbot_eval.runner import BudgetExceeded

        self._budget_exceeded = BudgetExceeded
        self.settings = _StubSettings(root)
        self.run_id = run_id
        self.run_dir = root / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.budget = 2.5
        self.spent = 0.0
        self.stop_after = stop_after
        self.ran: list[str] = []

    async def run_case(self, scenario, kind, variant, judge_mode):  # noqa: ANN001, ANN202
        if self.stop_after is not None and len(self.ran) >= self.stop_after:
            raise self._budget_exceeded("Hard evaluator budget exceeded")
        self.ran.append(scenario.id)
        return _case(scenario, passed=True)


def _seed_source(tmp_path: Path, count: int) -> list:
    scenarios = [build_scenario(TOPIC, i, 7, max_turns=3) for i in range(1, count + 1)]
    _write_source(tmp_path, [_case(s, passed=False) for s in scenarios])
    return scenarios


async def test_a_budget_stop_leaves_a_resumable_checkpoint(tmp_path: Path) -> None:
    from hm_chatbot_eval.replay import replay_run

    _seed_source(tmp_path, 2)
    runner = _StubRunner(tmp_path, "replay-1", stop_after=1)
    _, summary = await replay_run(
        runner,
        source_run_id="source-run",
        target_kind="backend",
        only_failed=True,
        judge_mode="online",
    )
    assert summary["replay"]["completed_cases"] == 1
    assert summary["replay"]["remaining_cases"] == 1
    assert summary["replay"]["resumable"] is True
    state = RunState.load(runner.run_dir / "run_state.json")
    assert state is not None
    assert state.execution_status == "STOPPED_BUDGET"


async def test_resuming_runs_only_what_is_left(tmp_path: Path) -> None:
    from hm_chatbot_eval.replay import replay_run

    scenarios = _seed_source(tmp_path, 2)
    stopped = _StubRunner(tmp_path, "replay-1", stop_after=1)
    await replay_run(
        stopped,
        source_run_id="source-run",
        target_kind="backend",
        only_failed=True,
        judge_mode="online",
    )
    resumed = _StubRunner(tmp_path, "replay-1")
    _, summary = await replay_run(
        resumed,
        source_run_id="source-run",
        target_kind="backend",
        only_failed=True,
        judge_mode="online",
    )
    assert resumed.ran == [scenarios[1].id]
    assert summary["replay"]["remaining_cases"] == 0
    assert summary["replay"]["resumable"] is False


async def test_a_completed_resume_clears_the_earlier_stop(tmp_path: Path) -> None:
    """The stop belonged to the previous invocation. Carrying it forward would report
    a finished replay as incomplete."""
    from hm_chatbot_eval.replay import replay_run

    _seed_source(tmp_path, 2)
    stopped = _StubRunner(tmp_path, "replay-1", stop_after=1)
    await replay_run(
        stopped,
        source_run_id="source-run",
        target_kind="backend",
        only_failed=True,
        judge_mode="online",
    )
    resumed = _StubRunner(tmp_path, "replay-1")
    _, summary = await replay_run(
        resumed,
        source_run_id="source-run",
        target_kind="backend",
        only_failed=True,
        judge_mode="online",
    )
    assert "execution_status" not in summary
    state = RunState.load(resumed.run_dir / "run_state.json")
    assert state is not None
    assert state.execution_status is None


async def test_a_changed_plan_starts_a_fresh_checkpoint(tmp_path: Path) -> None:
    """Replaying a different set of cases must not inherit unrelated progress."""
    from hm_chatbot_eval.replay import replay_run

    _seed_source(tmp_path, 2)
    first = _StubRunner(tmp_path, "replay-1")
    await replay_run(
        first,
        source_run_id="source-run",
        target_kind="backend",
        only_failed=True,
        judge_mode="online",
    )
    _seed_source(tmp_path, 3)
    second = _StubRunner(tmp_path, "replay-1")
    _, summary = await replay_run(
        second,
        source_run_id="source-run",
        target_kind="backend",
        only_failed=True,
        judge_mode="online",
    )
    assert summary["replay"]["planned_cases"] == 3
    assert len(second.ran) == 3


async def test_every_replay_writes_a_state_file_and_an_event_log(tmp_path: Path) -> None:
    from hm_chatbot_eval.replay import replay_run

    _seed_source(tmp_path, 1)
    runner = _StubRunner(tmp_path, "replay-1")
    await replay_run(
        runner,
        source_run_id="source-run",
        target_kind="backend",
        only_failed=True,
        judge_mode="online",
    )
    assert (runner.run_dir / "run_state.json").exists()
    events = [
        json.loads(line)
        for line in (runner.run_dir / "run_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [item["event"] for item in events] == [
        "run_started",
        "case_started",
        "case_completed",
        "run_finished",
    ]


async def test_an_unknown_judge_mode_is_refused(tmp_path: Path) -> None:
    from hm_chatbot_eval.replay import replay_run

    _seed_source(tmp_path, 1)
    runner = _StubRunner(tmp_path, "replay-1")
    with pytest.raises(ValueError, match="judge mode"):
        await replay_run(
            runner,
            source_run_id="source-run",
            target_kind="backend",
            only_failed=True,
            judge_mode="whatever",
        )
