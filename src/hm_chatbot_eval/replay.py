"""Resumable replay of a previous evaluation run.

Run ``20260725T122105Z`` stopped correctly at its $2.50 budget with 42 of its cases
done. There was no way to continue: ``replay`` re-runs exactly one scenario, so
verifying a fix meant paying for the whole run again from the start.

This module replays a prior run's own case plan, optionally only the cases that
failed, and checkpoints after every case. ``run_state.json`` records what has
finished so a re-invocation resumes instead of restarting, and
``run_events.jsonl`` is an append-only log of what happened and when.

Judge modes:

* ``online`` / ``deferred`` behave as in a normal run.
* ``cached-or-deferred`` reuses the source run's verdict when the replayed
  structured output is byte-identical, and defers to a batch otherwise. Reuse is
  keyed on the structured hash, so a changed answer can never inherit an old
  verdict.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .failures import ExecutionState, pause_state_for
from .models import CaseResult, ScenarioSpec, case_result_from_dict
from .report import write_reports
from .runner import (
    BudgetExceeded,
    CostAccountingError,
    EvaluationInfrastructureError,
    EvaluationRunner,
)
from .util import ensure_dir, utc_now

JUDGE_MODES = ("online", "deferred", "cached-or-deferred")


def case_key(scenario_id: str, target_kind: str, target_variant: str) -> str:
    """Stable identity for one replayable unit of work."""
    return f"{scenario_id}|{target_kind}|{target_variant}"


@dataclass
class ReplayItem:
    scenario: ScenarioSpec
    target_kind: str
    variant: dict[str, Any]
    source_passed: bool
    source_judge: dict[str, Any] | None = None
    source_structured_hash: str | None = None

    @property
    def key(self) -> str:
        return case_key(
            self.scenario.id, self.target_kind, str(self.variant.get("name", "current"))
        )


@dataclass
class RunState:
    """Checkpoint written after every completed case so a run can resume."""

    run_id: str
    source_run_id: str
    judge_mode: str
    only_failed: bool
    planned: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    execution_status: str | None = None
    execution_error: str | None = None
    updated_at: str = ""

    @property
    def remaining(self) -> list[str]:
        done = set(self.completed)
        return [key for key in self.planned if key not in done]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "source_run_id": self.source_run_id,
            "judge_mode": self.judge_mode,
            "only_failed": self.only_failed,
            "planned": list(self.planned),
            "completed": list(self.completed),
            "remaining": self.remaining,
            "execution_status": self.execution_status,
            "execution_error": self.execution_error,
            "updated_at": self.updated_at or utc_now(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunState:
        return cls(
            run_id=str(data.get("run_id") or ""),
            source_run_id=str(data.get("source_run_id") or ""),
            judge_mode=str(data.get("judge_mode") or "online"),
            only_failed=bool(data.get("only_failed")),
            planned=[str(x) for x in data.get("planned") or []],
            completed=[str(x) for x in data.get("completed") or []],
            execution_status=data.get("execution_status"),
            execution_error=data.get("execution_error"),
            updated_at=str(data.get("updated_at") or ""),
        )

    def save(self, path: Path) -> None:
        self.updated_at = utc_now()
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> RunState | None:
        if not path.exists():
            return None
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            # An unreadable checkpoint must not silently discard completed work; the
            # caller decides, and a fresh plan is safer than a partial one.
            return None


class EventLog:
    """Append-only record of what the replay did, one JSON object per line."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def emit(self, event: str, **fields: Any) -> None:
        payload = {"at": utc_now(), "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_source_items(
    source_dir: Path,
    *,
    target_kind: str | None = None,
    only_failed: bool = False,
) -> list[ReplayItem]:
    """Rebuild a prior run's case plan from its own ``cases.jsonl``."""
    path = source_dir / "cases.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"No cases.jsonl in {source_dir}")
    items: list[ReplayItem] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        kind = str(record.get("target_kind") or "backend")
        if target_kind and kind != target_kind:
            continue
        passed = bool(record.get("passed"))
        if only_failed and passed:
            continue
        variant_name = str(record.get("target_variant") or "current")
        scenario = ScenarioSpec(**record["scenario"])
        key = case_key(scenario.id, kind, variant_name)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            ReplayItem(
                scenario=scenario,
                target_kind=kind,
                variant={"name": variant_name},
                source_passed=passed,
                source_judge=record.get("judge"),
                source_structured_hash=record.get("structured_hash"),
            )
        )
    return items


def _resolve_variant(item: ReplayItem, configured: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer the live variant definition matching the recorded name."""
    name = str(item.variant.get("name", "current"))
    for variant in configured:
        if str(variant.get("name", "current")) == name:
            return variant
    return item.variant


def _apply_cached_judge(case: CaseResult, item: ReplayItem) -> CaseResult:
    """Reuse the source verdict only when the replayed output is byte-identical."""
    if case.judge is not None or not item.source_judge:
        return case
    if not case.structured_hash or case.structured_hash != item.source_structured_hash:
        return case
    cached = case_result_from_dict(
        {
            **case.to_dict(),
            "judge": item.source_judge,
        }
    )
    return replace(
        case,
        judge=cached.judge,
        passed=bool(cached.judge.passed) if cached.judge else case.passed,
    )


async def replay_run(
    runner: EvaluationRunner,
    *,
    source_run_id: str,
    target_kind: str | None,
    only_failed: bool,
    judge_mode: str,
) -> tuple[list[CaseResult], dict[str, Any]]:
    """Replay a source run's cases, checkpointing after each one."""
    if judge_mode not in JUDGE_MODES:
        raise ValueError(f"Unknown judge mode: {judge_mode}")
    settings = runner.settings
    source_dir = settings.eval_output_dir / source_run_id
    items = load_source_items(source_dir, target_kind=target_kind, only_failed=only_failed)
    run_dir = ensure_dir(runner.run_dir)
    state_path = run_dir / "run_state.json"
    events = EventLog(run_dir / "run_events.jsonl")

    planned = [item.key for item in items]
    state = RunState.load(state_path)
    if state is None or state.source_run_id != source_run_id or state.planned != planned:
        state = RunState(
            run_id=runner.run_id,
            source_run_id=source_run_id,
            judge_mode=judge_mode,
            only_failed=only_failed,
            planned=planned,
        )
        events.emit(
            "run_started",
            run_id=runner.run_id,
            source_run_id=source_run_id,
            planned=len(planned),
            only_failed=only_failed,
            judge_mode=judge_mode,
        )
    else:
        events.emit(
            "run_resumed",
            run_id=runner.run_id,
            completed=len(state.completed),
            remaining=len(state.remaining),
        )
        # A stop recorded by the previous invocation describes that invocation. Keeping
        # it would force the regenerated report to INCOMPLETE even after the resume
        # finished every planned case.
        state.execution_status = None
        state.execution_error = None
    state.save(state_path)

    # Completed cases from an earlier invocation are reloaded so the regenerated
    # report covers the whole plan, not only this invocation's slice.
    cases: list[CaseResult] = []
    cases_path = run_dir / "cases.jsonl"
    if state.completed and cases_path.exists():
        for line in cases_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                cases.append(case_result_from_dict(json.loads(line)))

    done = set(state.completed)
    effective_judge_mode = "deferred" if judge_mode == "cached-or-deferred" else judge_mode
    for item in items:
        if item.key in done:
            continue
        events.emit("case_started", case=item.key)
        try:
            case = await runner.run_case(
                item.scenario,
                item.target_kind,
                _resolve_variant(item, settings.target_variants),
                effective_judge_mode,
            )
        except BudgetExceeded as exc:
            state.execution_status = str(ExecutionState.STOPPED_BUDGET)
            state.execution_error = (
                f"{exc} Completed {len(state.completed)} of {len(planned)} planned cases. "
                f"Re-run the same command to resume."
            )
            events.emit("run_stopped", reason="budget", detail=str(exc))
            break
        except CostAccountingError as exc:
            state.execution_status = str(ExecutionState.FAILED_CONFIGURATION)
            state.execution_error = f"{exc} Completed {len(state.completed)} of {len(planned)}."
            events.emit("run_stopped", reason="cost_accounting", detail=str(exc))
            break
        except EvaluationInfrastructureError as exc:
            pause = pause_state_for(exc.failure_class)
            state.execution_status = str(pause or ExecutionState.FAILED_CONFIGURATION)
            state.execution_error = (
                f"{exc} Completed {len(state.completed)} of {len(planned)} planned cases. "
                f"Re-run the same command to resume."
            )
            events.emit(
                "run_stopped",
                reason=str(exc.failure_class),
                detail=str(exc),
            )
            break
        if judge_mode == "cached-or-deferred":
            case = _apply_cached_judge(case, item)
        cases.append(case)
        state.completed.append(item.key)
        events.emit(
            "case_completed",
            case=item.key,
            passed=case.passed,
            score=case.judge.score if case.judge else None,
            schema_valid=case.deterministic_metrics.get("schema_valid"),
            failure_class=(case.failure or {}).get("failure_class"),
            judge_source=(
                "cached"
                if judge_mode == "cached-or-deferred" and case.judge is not None
                else effective_judge_mode
            ),
        )
        state.save(state_path)
        write_reports(
            run_dir,
            cases,
            budget_usd=runner.budget,
            measured_spend_usd=runner.spent,
        )
    state.save(state_path)
    summary = write_reports(
        run_dir,
        cases,
        budget_usd=runner.budget,
        measured_spend_usd=runner.spent,
        execution_status=state.execution_status,
        execution_error=state.execution_error,
    )
    summary["replay"] = {
        "source_run_id": source_run_id,
        "planned_cases": len(planned),
        "completed_cases": len(state.completed),
        "remaining_cases": len(state.remaining),
        "only_failed": only_failed,
        "judge_mode": judge_mode,
        "resumable": bool(state.remaining),
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    events.emit(
        "run_finished",
        completed=len(state.completed),
        remaining=len(state.remaining),
        release_gate=summary.get("release_gate"),
    )
    return cases, summary
