from __future__ import annotations

import csv
import html
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_market_monitor.engine.setup_intent import decide_setup_intent
from ai_market_monitor.engine.strategy_compiler_v2 import (
    StrategyV2CompileError,
    compile_strategy_draft_v2,
)
from ai_market_monitor.engine.strategy_draft_v2 import (
    DraftPatchError,
    apply_strategy_patch,
)
from ai_market_monitor.engine.text_normalization import repair_utf8_mojibake
from ai_market_monitor.engine.turn_fragments import (
    classify_turn,
    split_symbol,
)
from ai_market_monitor.schemas.strategy_draft_v2 import (
    SetupIntent,
    StrategyDraftV2,
)
from ai_market_monitor.services.setup_chat_evaluation import (
    build_setup_chat_evaluation_contract,
)
from ai_market_monitor.services.strategy_patch_extractor import (
    deterministic_strategy_patch,
)

from .config import Settings
from .evaluate import deterministic_metrics, validate_schema
from .models import ScenarioSpec, TurnRecord
from .util import ensure_dir, utc_now


@dataclass(frozen=True, slots=True)
class RecordedReplayResult:
    source_scenario_id: str
    topic_id: str
    target_kind: str
    expected_contract: dict[str, Any]
    structured_output: dict[str, Any] | None
    metrics: dict[str, float]
    schema_errors: list[str]
    failures: list[str]
    unsupported_items: list[dict[str, Any]]
    canonical_state: dict[str, Any]
    elapsed_ms: float
    measurement_status: str
    measurement_issues: list[str]
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_scenario_id": self.source_scenario_id,
            "topic_id": self.topic_id,
            "target_kind": self.target_kind,
            "expected_contract": self.expected_contract,
            "structured_output": self.structured_output,
            "metrics": self.metrics,
            "schema_errors": self.schema_errors,
            "failures": self.failures,
            "unsupported_items": self.unsupported_items,
            "canonical_state": self.canonical_state,
            "elapsed_ms": self.elapsed_ms,
            "measurement_status": self.measurement_status,
            "measurement_issues": self.measurement_issues,
            "passed": self.passed,
        }


async def replay_recorded_run(
    settings: Settings,
    *,
    source_run_id: str,
    run_id: str,
) -> tuple[list[RecordedReplayResult], dict[str, Any], Path]:
    """Compile every captured conversation without calling a model or live target."""

    source = settings.eval_output_dir / source_run_id / "cases.jsonl"
    if not source.exists():
        raise FileNotFoundError(f"No recorded cases at {source}")
    run_dir = ensure_dir(settings.eval_output_dir / run_id)
    raw_cases = [
        json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    schema = (
        json.loads(Path(settings.target_schema_file).read_text(encoding="utf-8"))
        if settings.target_schema_file
        else None
    )
    results = [
        await _replay_one(
            raw,
            schema=schema,
            field_map=settings.target_field_map,
        )
        for raw in raw_cases
    ]
    summary = _write_reports(
        run_dir,
        source_run_id=source_run_id,
        results=results,
    )
    return results, summary, run_dir


async def _replay_one(
    raw: dict[str, Any],
    *,
    schema: dict[str, Any] | None,
    field_map: dict[str, Any],
) -> RecordedReplayResult:
    started = time.perf_counter()
    scenario = ScenarioSpec(**raw["scenario"])
    user_turns = [
        repair_utf8_mojibake(str(turn.get("text") or ""))
        for turn in raw.get("turns", [])
        if turn.get("role") == "user"
    ]
    if not user_turns:
        return RecordedReplayResult(
            source_scenario_id=scenario.id,
            topic_id=scenario.topic_id,
            target_kind=str(raw.get("target_kind") or "unknown"),
            expected_contract=scenario.expected_contract,
            structured_output=None,
            metrics={
                "schema_valid": 0.0,
                "mapped_field_accuracy": 0.0,
                "hallucination_rate": 0.0,
                "excluded_symbol_leakage_rate": 0.0,
                "direction_inversion_rate": 0.0,
                "timeframe_inversion_rate": 0.0,
                "operator_inversion_rate": 0.0,
                "semantic_contract_pass": 0.0,
                "unrelated_capability_substitution_rate": 0.0,
                "unsupported_source_evidence_rate": 1.0,
                "approval_phrase_recognized": 0.0,
                "compiler_latency_within_target": 1.0,
            },
            schema_errors=[],
            failures=[],
            unsupported_items=[],
            canonical_state=StrategyDraftV2().model_dump(mode="json"),
            elapsed_ms=(time.perf_counter() - started) * 1000,
            measurement_status="NOT_MEASURED",
            measurement_issues=["no_recorded_user_turns"],
            passed=False,
        )
    draft = StrategyDraftV2()
    history: list[dict[str, Any]] = []
    replay_issues: list[str] = []
    approval_seen = False
    for index, text in enumerate(user_turns, 1):
        intent = decide_setup_intent(text).intent
        if intent == SetupIntent.APPROVAL_ACTION:
            approval_seen = True
            continue
        if intent != SetupIntent.STRATEGY_PATCH:
            continue
        patch = deterministic_strategy_patch(
            draft,
            text,
            source_turn_id=f"recorded-{index}",
        )
        if patch is None:
            replay_issues.append(f"turn_{index}_requires_structured_extraction")
            continue
        try:
            result = apply_strategy_patch(draft, patch, history=history)
        except DraftPatchError as exc:
            replay_issues.append(f"turn_{index}_patch_rejected:{exc}")
            continue
        if result.material_change:
            history.append(draft.model_dump(mode="json"))
        draft = result.draft

    compile_error: str | None = None
    strategy = None
    if not draft.blocking and draft.condition_ast is not None:
        try:
            strategy = compile_strategy_draft_v2(draft)
        except StrategyV2CompileError as exc:
            compile_error = f"{exc.code}:{exc}"
            replay_issues.append(f"compile_error:{compile_error}")
    activation_blocked = strategy is None
    unsupported = [
        {
            "code": item.key,
            "message": item.missing_contract,
            "source_fragment": item.source_fragment,
            "blocking": item.blocking,
        }
        for item in draft.unsupported_requirements
    ]
    unsupported.extend(
        {
            "code": item.key,
            "message": item.question,
            "source_fragment": item.source_fragment,
            "blocking": item.blocking,
        }
        for item in draft.unresolved_fields
    )
    if strategy is None:
        source = next(
            (
                user_turns[int(issue.split("_", 2)[1]) - 1]
                for issue in replay_issues
                if issue.startswith("turn_")
            ),
            user_turns[-1],
        )
        unsupported.append(
            {
                "code": "recorded_replay_not_compiled",
                "message": compile_error or "A structured extraction call is required.",
                "source_fragment": source,
                "blocking": True,
            }
        )
        contract: dict[str, Any] = {}
        schema_errors: list[str] = []
    else:
        session_status = "ready_for_approval"
        contract = build_setup_chat_evaluation_contract(
            strategy,
            session_status=session_status,
            approval_eligible=True,
            blocking_findings=False,
            assumptions=[],
            confidence=[],
            unsupported_capabilities=unsupported,
        ).model_dump(mode="json")
        schema_errors = validate_schema(contract, schema)
    evaluation_turns = [
        TurnRecord(
            turn_id=f"u{index}",
            role="user",
            text=text,
            timestamp=utc_now(),
        )
        for index, text in enumerate(user_turns, 1)
    ]
    metrics = deterministic_metrics(
        scenario,
        evaluation_turns,
        contract,
        schema_errors,
        field_map,
    )
    metrics.update(
        {
            "unrelated_capability_substitution_rate": float(
                bool(_unrelated_substitutions(contract))
            ),
            "unsupported_source_evidence_rate": _unsupported_source_evidence_rate(unsupported),
            "approval_phrase_recognized": float(approval_seen),
            "compiler_latency_within_target": float(
                (time.perf_counter() - started) * 1000 <= 20_000
            ),
        }
    )
    measurement_issues = _expected_contract_measurement_issues(
        scenario.expected_contract,
        user_turns,
        activation_blocked=activation_blocked,
    )
    measurement_issues.extend(replay_issues)
    if strategy is None:
        measurement_issues.append("no_v2_compiled_strategy")
    measured = not measurement_issues
    failures: list[str] = []
    if schema_errors:
        failures.append("schema_invalid")
    if measured and metrics["mapped_field_accuracy"] != 1.0:
        failures.append("semantic_field_mismatch")
    if measured and metrics["hallucination_rate"] != 0.0:
        failures.append("unexpected_universe_value")
    if measured and metrics["excluded_symbol_leakage_rate"] != 0.0:
        failures.append("excluded_symbol_leakage")
    if measured and metrics["direction_inversion_rate"] != 0.0:
        failures.append("direction_inversion")
    if measured and metrics["timeframe_inversion_rate"] != 0.0:
        failures.append("timeframe_inversion")
    if measured and metrics["operator_inversion_rate"] != 0.0:
        failures.append("operator_inversion")
    substitutions = _unrelated_substitutions(contract)
    failures.extend(f"unrelated_capability:{item}" for item in substitutions)
    if metrics["unsupported_source_evidence_rate"] != 1.0:
        failures.append("unsupported_item_missing_source_evidence")
    elapsed_ms = (time.perf_counter() - started) * 1000
    if elapsed_ms > 20_000:
        failures.append("compiler_latency_over_20s")
    passed = measured and not failures
    metrics["semantic_contract_pass"] = float(passed)
    return RecordedReplayResult(
        source_scenario_id=scenario.id,
        topic_id=scenario.topic_id,
        target_kind=str(raw.get("target_kind") or "unknown"),
        expected_contract=scenario.expected_contract,
        structured_output=contract if strategy is not None else None,
        metrics=metrics,
        schema_errors=schema_errors,
        failures=failures,
        unsupported_items=unsupported,
        canonical_state=draft.model_dump(mode="json"),
        elapsed_ms=elapsed_ms,
        measurement_status="MEASURED" if measured else "NOT_MEASURED",
        measurement_issues=measurement_issues,
        passed=passed,
    )


_SUBSTITUTION_CONTRACTS: dict[str, tuple[str, ...]] = {
    "support_retest": ("support", "retest"),
    "resistance_retest": ("resistance", "retest"),
    "weekly_high_low": ("weekly", "week", "high", "low"),
    "price_up_percent": ("bull", "rise", "gain", "up", "increase"),
    "price_down_percent": ("bear", "drop", "fall", "down", "decrease"),
    "clarification_required": (),
}


def _unrelated_substitutions(contract: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for condition in contract.get("conditions") or []:
        key = str(condition.get("capability_key") or "")
        if key not in _SUBSTITUTION_CONTRACTS:
            continue
        required_terms = _SUBSTITUTION_CONTRACTS[key]
        source = str(condition.get("source_fragment") or "").casefold()
        if not required_terms or not any(term in source for term in required_terms):
            failures.append(key)
    return sorted(set(failures))


def _unsupported_source_evidence_rate(items: list[dict[str, Any]]) -> float:
    blocking = [item for item in items if item.get("blocking", True)]
    if not blocking:
        return 1.0
    evidenced = sum(bool(str(item.get("source_fragment") or "").strip()) for item in blocking)
    return evidenced / len(blocking)


def _expected_contract_measurement_issues(
    expected: dict[str, Any],
    user_turns: list[str],
    *,
    activation_blocked: bool = False,
) -> list[str]:
    """Reject expectations the captured conversation never made measurable."""

    normalized_turns = re.sub(
        r"[/_\-\s]",
        "",
        "\n".join(user_turns).upper(),
    )
    issues: list[str] = []
    for field in ("symbol", "excluded_symbol"):
        value = str(expected.get(field) or "").strip().upper()
        if not value:
            continue
        normalized = re.sub(r"[/_\-\s]", "", value)
        grounded = normalized in normalized_turns
        pair = split_symbol(value)
        if not grounded and field == "excluded_symbol" and pair is not None:
            base, _quote = pair
            grounded = bool(
                re.search(
                    rf"(?<!yes/)\bno\s+[*_`]*{re.escape(base)}\b|"
                    rf"\b(?:exclude|excluding|excluded)\b.{{0,20}}"
                    rf"\b{re.escape(base)}\b|"
                    rf"\b{re.escape(base)}\b.{{0,20}}\bexcluded\b",
                    "\n".join(user_turns),
                    re.IGNORECASE,
                )
            )
        if not grounded:
            issues.append(f"expected_{field}_absent_from_recorded_user_turns:{value}")
    expected_direction = str(expected.get("direction") or "").strip().casefold()
    if expected_direction:
        observed_directions = {
            report.direction.value
            for turn in user_turns
            if (report := classify_turn(turn)).direction is not None
        }
        if expected_direction not in observed_directions:
            issues.append(
                "expected_direction_absent_from_recorded_user_turns:"
                f"{expected_direction}"
            )
    if activation_blocked and user_turns:
        final_report = classify_turn(user_turns[-1])
        if any(item.kind == "decision_request" for item in final_report.fragments):
            issues.append("recorded_conversation_ends_with_open_definition")
    return issues


def _write_reports(
    run_dir: Path,
    *,
    source_run_id: str,
    results: list[RecordedReplayResult],
) -> dict[str, Any]:
    case_path = run_dir / "cases.jsonl"
    case_path.write_text(
        "".join(
            json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            for item in results
        ),
        encoding="utf-8",
    )
    failures = [(item, failure) for item in results for failure in item.failures]
    with (run_dir / "failures.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("scenario_id", "topic_id", "target_kind", "failure"))
        for item, failure in failures:
            writer.writerow(
                (
                    item.source_scenario_id,
                    item.topic_id,
                    item.target_kind,
                    failure,
                )
            )
    total_cases = len(results)
    measured_results = [item for item in results if item.measurement_status == "MEASURED"]
    measured = len(measured_results)
    not_measured = total_cases - measured
    passed = sum(item.passed for item in results)
    measured_failures = measured - passed
    measured_subset_status = (
        "NOT_MEASURED" if not measured else ("FAIL" if measured_failures else "PASS")
    )
    quality_status = (
        "FAIL"
        if measured_failures
        else "NOT_MEASURED"
        if not_measured
        else measured_subset_status
    )
    summary = {
        "source_run_id": source_run_id,
        "execution_mode": "recorded_deterministic_compiler_replay",
        "cases": total_cases,
        "measured_cases": measured,
        "not_measured_cases": not_measured,
        "passed": passed,
        "pass_rate": passed / max(1, measured),
        "schema_valid_cases": sum(
            item.structured_output is not None and not item.schema_errors
            for item in results
        ),
        "average_semantic_field_accuracy": (
            sum(item.metrics["mapped_field_accuracy"] for item in measured_results)
            / max(1, measured)
        ),
        "unrelated_capability_substitutions": sum(
            any(failure.startswith("unrelated_capability:") for failure in item.failures)
            for item in results
        ),
        "excluded_symbol_leakage_cases": sum(
            "excluded_symbol_leakage" in item.failures for item in results
        ),
        "direction_inversion_cases": sum(
            "direction_inversion" in item.failures for item in results
        ),
        "timeframe_inversion_cases": sum(
            "timeframe_inversion" in item.failures for item in results
        ),
        "operator_inversion_cases": sum("operator_inversion" in item.failures for item in results),
        "maximum_compile_latency_ms": max(
            (item.elapsed_ms for item in results),
            default=0.0,
        ),
        "total_test_cost_usd": 0.0,
        "quality_status": quality_status,
        "measured_subset_status": measured_subset_status,
        "critical_release_status": (
            "FAIL"
            if measured_failures
            else "NOT_MEASURED"
            if not_measured
            else "PASS"
        ),
        "workflow_status": "COMPLETED",
        "measurement_status": "PARTIAL" if not_measured else "COMPLETE",
        "infrastructure_status": "NOT_APPLICABLE",
        "deterministic_preflight_status": (
            "FAIL"
            if measured_failures
            else "PARTIAL"
            if not_measured
            else "PASS"
        ),
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    rows = "\n".join(
        "| "
        + " | ".join(
            (
                item.source_scenario_id,
                item.target_kind,
                (
                    "NOT_MEASURED"
                    if item.measurement_status == "NOT_MEASURED"
                    else ("PASS" if item.passed else "FAIL")
                ),
                f"{item.metrics['mapped_field_accuracy']:.3f}",
                ", ".join((*item.failures, *item.measurement_issues)) or "None",
            )
        )
        + " |"
        for item in results
    )
    markdown = (
        "# Recorded Setup-Chat Compiler Replay\n\n"
        f"Source run: `{source_run_id}`\n\n"
        f"Deterministic preflight: **{summary['deterministic_preflight_status']}**\n\n"
        f"- Cases: {total_cases}\n"
        f"- Measured: {measured}\n"
        f"- Not measured: {not_measured}\n"
        f"- Passed: {passed}\n"
        f"- Schema-valid: {summary['schema_valid_cases']}\n"
        f"- Mean semantic accuracy: {summary['average_semantic_field_accuracy']:.3f}\n"
        f"- Quality: {summary['quality_status']}\n"
        f"- Measured subset: {summary['measured_subset_status']}\n"
        f"- Critical release: {summary['critical_release_status']}\n"
        f"- Workflow: {summary['workflow_status']}\n"
        f"- Measurement: {summary['measurement_status']}\n"
        f"- Infrastructure: {summary['infrastructure_status']}\n"
        f"- API cost: $0.00\n\n"
        "| Scenario | Target | Result | Semantic accuracy | Failures |\n"
        "| --- | --- | --- | ---: | --- |\n"
        f"{rows}\n"
    )
    (run_dir / "report.md").write_text(markdown, encoding="utf-8")
    html_rows = "".join(
        "<tr>"
        f"<td>{html.escape(item.source_scenario_id)}</td>"
        f"<td>{html.escape(item.target_kind)}</td>"
        f"<td>{'NOT_MEASURED' if item.measurement_status == 'NOT_MEASURED' else ('PASS' if item.passed else 'FAIL')}</td>"
        f"<td>{item.metrics['mapped_field_accuracy']:.3f}</td>"
        f"<td>{html.escape(', '.join((*item.failures, *item.measurement_issues)) or 'None')}</td>"
        "</tr>"
        for item in results
    )
    (run_dir / "report.html").write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        "<title>Recorded Setup-Chat Compiler Replay</title>"
        "<style>body{font:15px system-ui;margin:32px;color:#2b2e35}"
        "table{border-collapse:collapse;width:100%}th,td{padding:9px;"
        "border:1px solid #e1e5ea;text-align:left}th{background:#f5f8fb}"
        "</style></head><body>"
        "<h1>Recorded Setup-Chat Compiler Replay</h1>"
        f"<p>Source: <code>{html.escape(source_run_id)}</code></p>"
        f"<h2>{summary['deterministic_preflight_status']}</h2>"
        f"<p>{passed}/{measured} passed; API cost $0.00.</p>"
        "<table><thead><tr><th>Scenario</th><th>Target</th><th>Result</th>"
        "<th>Semantic accuracy</th><th>Failures</th></tr></thead>"
        f"<tbody>{html_rows}</tbody></table></body></html>",
        encoding="utf-8",
    )
    return summary
