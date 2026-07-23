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
from .config import Settings
from .doctor import checks
from .profiles import (
    cases_per_topic,
    max_turns_for_topic,
    repeats_for_topic,
    target_kinds_for_topic,
    topics_for_mode,
    variants_for_topic,
)
from .runner import EvaluationRunner
from .topics import TOPICS

app = typer.Typer(no_args_is_help=True, help="AI-vs-AI evaluator for HilalMarkets AI Setup Chat.")
console = Console()


def _settings() -> Settings:
    return Settings()


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
    judge_mode: Annotated[str, typer.Option(help="online or deferred")] = "online",
    budget_usd: Annotated[float, typer.Option()] = 0,
    scenario: Annotated[str, typer.Option(help="Re-run one deterministic scenario ID")] = "",
    run_id: Annotated[str, typer.Option()] = "",
) -> None:
    settings = _settings()
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
            case = await runner.run_case(found, target, settings.target_variants[0], judge_mode)
            from .report import write_reports

            summary = write_reports(runner.run_dir, [case])
            return summary, runner.run_dir
        finally:
            await runner.close()

    summary, run_dir = asyncio.run(execute())
    console.print(summary)
    console.print(f"Report: {run_dir / 'report.html'}")


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
