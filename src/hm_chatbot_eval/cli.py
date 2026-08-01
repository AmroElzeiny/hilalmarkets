from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .batch import BatchManager
from .compare import compare_runs
from .config import (
    Settings,
    discard_stale_isolated_target_overrides,
    discard_stale_process_openai_key,
)
from .doctor import checks, fault_control_availability
from .profiles import (
    cases_per_topic,
    max_turns_for_topic,
    repeats_for_topic,
    target_kinds_for_topic,
    topics_for_mode,
    variants_for_topic,
)
from .runner import BudgetExceeded, EvaluationRunner
from .topics import TOPICS

app = typer.Typer(no_args_is_help=True, help="AI-vs-AI evaluator for HilalMarkets AI Setup Chat.")
console = Console()


def _settings() -> Settings:
    discard_stale_process_openai_key()
    discard_stale_isolated_target_overrides()
    return Settings()


def _selected_topics_require_fault_control(mode: str, requested_topics: str) -> bool:
    requested = {item.strip() for item in requested_topics.split(",") if item.strip()}
    configured = [topic for topic in TOPICS if not requested or topic.id in requested]
    return any(topic.fault is not None for topic in topics_for_mode(mode, configured))


def _require_fault_control_target(settings: Settings, *, mode: str, topics: str) -> None:
    """Stop a fault run before it can target a development/production app."""

    if (
        settings.target_backend_adapter != "hilalmarkets"
        or not _selected_topics_require_fault_control(mode, topics)
    ):
        return
    available, detail = fault_control_availability(settings)
    if available:
        return
    raise typer.BadParameter(
        "Selected topics use test-only fault injection, but the configured target cannot "
        f"accept it: {detail}. Use "
        ".\\scripts\\run_isolated_setup_chat_smoke.ps1 -Topic <fault-topic> "
        "-EnableFaults, or select only no-fault topics for port 8000. Do not enable "
        "evaluator fault controls on the development or production app.",
        param_hint="--topics",
    )


@app.command("doctor")
def doctor() -> None:
    settings = _settings()
    table = Table("Check", "Status", "Detail")
    failed = False
    for name, ok, detail in checks(settings):
        table.add_row(name, "PASS" if ok else "FAIL", detail)
        failed |= not ok
    console.print(table)
    raise typer.Exit(1 if failed else 0)


@app.command("list-topics")
def list_topics() -> None:
    table = Table("ID", "Category", "Severity", "Title", "Default cases")
    for topic in TOPICS:
        table.add_row(
            topic.id, topic.category, topic.severity, topic.title, str(topic.default_cases)
        )
    console.print(table)
    console.print(
        f"[bold]{len(TOPICS)} topics[/bold]; full default = {sum(t.default_cases for t in TOPICS)} scenarios per target variant."
    )


@app.command("plan")
def plan(
    mode: Annotated[str, typer.Option()] = "budget",
    tests_per_topic: Annotated[int, typer.Option()] = 24,
    target: Annotated[str, typer.Option()] = "backend",
    topics: Annotated[str, typer.Option(help="Comma-separated topic IDs; empty means all")] = "",
) -> None:
    configured = [
        topic
        for topic in TOPICS
        if not topics or topic.id in {item.strip() for item in topics.split(",")}
    ]
    selected = topics_for_mode(mode, configured)
    count = cases_per_topic(mode, tests_per_topic)
    requested_kinds = ["backend", "ui"] if target == "both" else [target]
    settings = _settings()
    total = 0
    turns = 0
    for topic in selected:
        topic_targets = target_kinds_for_topic(mode, requested_kinds, topic)
        topic_runs = (
            count
            * repeats_for_topic(topic)
            * len(topic_targets)
            * len(variants_for_topic(mode, settings.target_variants, topic))
        )
        total += topic_runs
        turns += topic_runs * max_turns_for_topic(mode, topic)
    console.print(
        {
            "topics": len(selected),
            "cases_per_topic": count,
            "target_runs": total,
            "maximum_dynamic_turns": turns,
            "judge_calls": total,
            "variants": [x.get("name") for x in settings.target_variants],
            "all_in_budget_usd": min(
                settings.eval_budget_usd,
                settings.eval_budget_profile_max_usd,
            )
            if mode == "budget"
            else settings.eval_budget_usd,
        }
    )


@app.command("run")
def run(
    mode: Annotated[str, typer.Option()] = "smoke",
    target: Annotated[str, typer.Option(help="backend, ui, or both")] = "backend",
    tests_per_topic: Annotated[int, typer.Option()] = 24,
    topics: Annotated[str, typer.Option(help="Comma-separated topic IDs")] = "",
    seed: Annotated[int, typer.Option()] = 20260723,
    selection_seed: Annotated[
        int,
        typer.Option(
            help="Reproduce random case selection; 0 derives a fresh seed from the run ID"
        ),
    ] = 0,
    judge_mode: Annotated[str, typer.Option(help="online or deferred")] = "online",
    budget_usd: Annotated[float, typer.Option()] = 0,
    scenario: Annotated[str, typer.Option(help="Re-run one deterministic scenario ID")] = "",
    run_id: Annotated[str, typer.Option()] = "",
) -> None:
    settings = _settings()
    _require_fault_control_target(settings, mode=mode, topics=topics)
    actual_run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    budget = budget_usd or settings.eval_budget_usd
    if mode == "budget":
        budget = min(budget, settings.eval_budget_profile_max_usd)
    kinds = ["backend", "ui"] if target == "both" else [target]
    topic_ids = [x.strip() for x in topics.split(",") if x.strip()] or None

    async def execute():
        runner = EvaluationRunner(settings, actual_run_id, budget)
        try:
            _, summary = await runner.run(
                mode=mode,
                target_kinds=kinds,
                topic_ids=topic_ids,
                tests_per_topic=tests_per_topic,
                seed=seed,
                judge_mode=judge_mode,
                only_scenario=scenario or None,
                selection_seed=selection_seed or None,
            )
            return summary, runner.run_dir
        finally:
            await runner.close()

    summary, run_dir = asyncio.run(execute())
    console.print(summary)
    console.print(f"[bold]Report:[/bold] {run_dir / 'report.html'}")
    raise typer.Exit(0 if summary["release_gate"] in {"PASS", "PENDING_JUDGE"} else 2)


@app.command("replay")
def replay(
    source_run_id: str,
    scenario_id: str,
    target: Annotated[str, typer.Option()] = "backend",
    judge_mode: Annotated[str, typer.Option()] = "online",
    budget_usd: Annotated[float, typer.Option()] = 0,
) -> None:
    from .models import ScenarioSpec

    settings = _settings()
    source = settings.eval_output_dir / source_run_id / "cases.jsonl"
    found = None
    for line in source.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if item["scenario"]["id"] == scenario_id and item["target_kind"] == target:
            found = ScenarioSpec(**item["scenario"])
            break
    if found is None:
        raise typer.BadParameter("Scenario not found in source run")
    new_run_id = datetime.now(UTC).strftime("replay-%Y%m%dT%H%M%SZ")

    async def execute():
        runner = EvaluationRunner(settings, new_run_id, budget_usd or settings.eval_budget_usd)
        try:
            from .report import write_reports

            try:
                case = await runner.run_case(
                    found,
                    target,
                    settings.target_variants[0],
                    judge_mode,
                )
            except BudgetExceeded as exc:
                summary = write_reports(
                    runner.run_dir,
                    [],
                    budget_usd=runner.budget,
                    measured_spend_usd=runner.spent,
                    execution_status="STOPPED_BUDGET",
                    execution_error=f"{exc} Completed 0 cases before stopping.",
                )
                return summary, runner.run_dir
            summary = write_reports(
                runner.run_dir,
                [case],
                budget_usd=runner.budget,
                measured_spend_usd=runner.spent,
            )
            return summary, runner.run_dir
        finally:
            await runner.close()

    summary, run_dir = asyncio.run(execute())
    console.print(summary)
    console.print(f"Report: {run_dir / 'report.html'}")


@app.command("replay-run")
def replay_run_command(
    source_run_id: str,
    target: Annotated[
        str, typer.Option(help="backend, ui, or all to replay every recorded target")
    ] = "backend",
    only_failed: Annotated[
        bool, typer.Option("--only-failed", help="Replay only the cases that failed")
    ] = False,
    judge_mode: Annotated[
        str, typer.Option(help="online, deferred, or cached-or-deferred")
    ] = "online",
    budget_usd: Annotated[float, typer.Option()] = 0,
    run_id: Annotated[
        str, typer.Option(help="Reuse a run id to resume that replay where it stopped")
    ] = "",
) -> None:
    """Replay a previous run's cases, checkpointing so it can resume after a stop."""
    from .replay import JUDGE_MODES, replay_run

    if judge_mode not in JUDGE_MODES:
        raise typer.BadParameter(f"judge_mode must be one of {', '.join(JUDGE_MODES)}")
    settings = _settings()
    source_dir = settings.eval_output_dir / source_run_id
    if not (source_dir / "cases.jsonl").exists():
        raise typer.BadParameter(f"No cases.jsonl for run {source_run_id}")
    actual_run_id = run_id or f"replay-{source_run_id}"
    kind = None if target == "all" else target

    async def execute():
        runner = EvaluationRunner(settings, actual_run_id, budget_usd or settings.eval_budget_usd)
        try:
            _, summary = await replay_run(
                runner,
                source_run_id=source_run_id,
                target_kind=kind,
                only_failed=only_failed,
                judge_mode=judge_mode,
            )
            return summary, runner.run_dir
        finally:
            await runner.close()

    summary, run_dir = asyncio.run(execute())
    console.print(summary)
    console.print(f"[bold]Report:[/bold] {run_dir / 'report.html'}")
    replay_info = summary.get("replay") or {}
    if replay_info.get("resumable"):
        console.print(
            f"[yellow]{replay_info['remaining_cases']} case(s) remaining.[/yellow] "
            f"Re-run with --run-id {actual_run_id} to resume."
        )
    raise typer.Exit(0 if summary["release_gate"] in {"PASS", "PENDING_JUDGE"} else 2)


@app.command("recorded-replay")
def recorded_replay_command(
    source_run_id: str,
    run_id: Annotated[str, typer.Option()] = "",
) -> None:
    """Replay captured user turns through the local deterministic compiler only."""

    from .recorded_replay import replay_recorded_run

    settings = _settings()
    actual_run_id = run_id or (
        f"recorded-{source_run_id}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    try:
        results, summary, run_dir = asyncio.run(
            replay_recorded_run(
                settings,
                source_run_id=source_run_id,
                run_id=actual_run_id,
            )
        )
    except Exception as exc:
        console.print(f"[red]Recorded replay failed safely ({type(exc).__name__}).[/red]")
        raise typer.Exit(2) from None
    console.print(summary)
    console.print(f"[bold]Report:[/bold] {run_dir / 'report.html'}")
    raise typer.Exit(0 if results and summary["deterministic_preflight_status"] == "PASS" else 2)


@app.command("launch-core")
def launch_core_command(
    run_id: Annotated[str, typer.Option()] = "",
) -> None:
    """Run the zero-cost deterministic launch grammar and semantic contracts."""

    from .launch_core import run_launch_core

    settings = _settings()
    summary, run_dir = run_launch_core(
        settings.eval_output_dir,
        run_id=run_id or None,
    )
    console.print(summary)
    console.print(f"[bold]Report:[/bold] {run_dir / 'report.html'}")
    raise typer.Exit(0 if summary["stable_regression_status"] == "PASS" else 2)


@app.command("batch-submit")
def batch_submit(run_id: str) -> None:
    settings = _settings()
    data = BatchManager(settings).submit(settings.eval_output_dir / run_id)
    console.print(data)


@app.command("batch-collect")
def batch_collect(run_id: str, batch_id: str = "") -> None:
    settings = _settings()
    data = BatchManager(settings).collect(settings.eval_output_dir / run_id, batch_id or None)
    console.print(data)


@app.command("compare")
def compare(run_a: str, run_b: str) -> None:
    settings = _settings()
    result = compare_runs(settings.eval_output_dir / run_a, settings.eval_output_dir / run_b)
    out = settings.eval_output_dir / f"compare-{run_a}-vs-{run_b}.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    console.print(result)
    console.print(f"Saved: {out}")


if __name__ == "__main__":
    app()
