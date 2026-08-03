from __future__ import annotations

import csv
import html
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from .models import CaseResult
from .topics import TOPIC_BY_ID
from .util import ensure_dir, normalized_evaluation_contract, semantic_contract_hash


def _case_score(case: CaseResult) -> float:
    return case.judge.score if case.judge else case.deterministic_metrics.get("schema_valid", 0.0)


#: Metrics that only have meaning when the same scenario ran against a baseline and
#: a candidate variant. With one variant there is nothing to compare, so they are
#: reported as NOT_MEASURED rather than counted as a drift failure.
PAIRED_VARIANT_METRICS = frozenset({"pass_flip_rate", "semantic_score_delta_abs"})


def _drift_pairs(items: list[CaseResult]) -> list[tuple[CaseResult, CaseResult]]:
    """Pair baseline and candidate runs of the same scenario under the same contract.

    A pair is only valid when the two cases differ by variant while agreeing on the
    scenario and its expected contract. Comparing a case against itself, or against a
    case run under a different contract, would manufacture a drift number.
    """
    groups: dict[tuple[str, str], list[CaseResult]] = defaultdict(list)
    for case in items:
        groups[(case.scenario.id, case.target_kind)].append(case)
    pairs: list[tuple[CaseResult, CaseResult]] = []
    for variants in groups.values():
        by_variant: dict[str, CaseResult] = {}
        for case in variants:
            by_variant.setdefault(case.target_variant, case)
        if len(by_variant) < 2:
            continue
        ordered = [by_variant[name] for name in sorted(by_variant)]
        base = ordered[0]
        for other in ordered[1:]:
            if base.scenario.expected_contract != other.scenario.expected_contract:
                continue
            pairs.append((base, other))
    return pairs


def _metric_values(items: list[CaseResult], metric: str) -> list[float]:
    if metric in PAIRED_VARIANT_METRICS:
        pairs = _drift_pairs(items)
        if not pairs:
            return []
        if metric == "pass_flip_rate":
            flips = [float(a.passed != b.passed) for a, b in pairs]
            return [sum(flips) / len(flips)]
        deltas = [abs(_case_score(a) - _case_score(b)) for a, b in pairs]
        return [sum(deltas) / len(deltas)]
    if metric == "ui_backend_parity":
        target_pairs: dict[tuple[str, str], dict[str, CaseResult]] = defaultdict(dict)
        for case in items:
            target_pairs[(case.scenario.id, case.target_variant)][case.target_kind] = case
        parity_values = []
        for pair in target_pairs.values():
            if "ui" in pair and "backend" in pair:
                a, b = pair["ui"], pair["backend"]
                if a.structured_hash and b.structured_hash:
                    parity_values.append(float(a.structured_hash == b.structured_hash))
        return parity_values
    if metric.startswith("backend_ui_"):
        contract_pairs: dict[tuple[str, str, str], dict[str, CaseResult]] = defaultdict(dict)
        for case in items:
            contract_pairs[
                (case.scenario.id, case.target_variant, case.scenario.approval_mode)
            ][case.target_kind] = case
        values: list[float] = []
        for pair in contract_pairs.values():
            if "ui" not in pair or "backend" not in pair:
                continue
            backend, ui = pair["backend"], pair["ui"]
            if metric == "backend_ui_contract_match":
                backend_contract = backend.structured_output or backend.canonical_state
                ui_contract = ui.structured_output or ui.canonical_state
                if backend_contract is not None and ui_contract is not None:
                    values.append(
                        float(
                            semantic_contract_hash(backend_contract)
                            == semantic_contract_hash(ui_contract)
                        )
                    )
            elif metric == "backend_ui_status_match":
                values.append(float(_case_lifecycle(backend) == _case_lifecycle(ui)))
            elif metric == "backend_ui_error_class_match":
                values.append(
                    float(
                        backend.product_failure_classes == ui.product_failure_classes
                        and (backend.failure or {}).get("failure_class")
                        == (ui.failure or {}).get("failure_class")
                    )
                )
            elif metric == "backend_ui_turn_count_delta":
                values.append(float(abs(len(backend.turns) - len(ui.turns))))
            elif metric == "backend_ui_approval_state_match":
                values.append(float(_case_approval(backend) == _case_approval(ui)))
            elif metric == "backend_ui_requirement_state_match":
                backend_requirements = _parity_requirements(
                    backend.canonical_state
                ) or _parity_requirements(backend.structured_output)
                ui_requirements = _parity_requirements(
                    ui.canonical_state
                ) or _parity_requirements(ui.structured_output)
                if backend_requirements or ui_requirements:
                    values.append(float(backend_requirements == ui_requirements))
        return values
    if metric == "reproducibility":
        repeat_groups: dict[tuple[str, str, str], list[CaseResult]] = defaultdict(list)
        for case in items:
            repeat_groups[(case.scenario.id, case.target_kind, case.target_variant)].append(case)
        return [
            float(group[0].structured_hash == group[1].structured_hash)
            for group in repeat_groups.values()
            if len(group) >= 2 and group[0].structured_hash and group[1].structured_hash
        ]
    metric_values: list[float] = []
    for case in items:
        if metric in case.deterministic_metrics:
            metric_values.append(float(case.deterministic_metrics[metric]))
        elif case.judge and metric in case.judge.dimension_scores:
            metric_values.append(float(case.judge.dimension_scores[metric]))
        elif metric == "avg_test_ai_cost_usd" and case.test_ai_cost_usd is not None:
            metric_values.append(float(case.test_ai_cost_usd))
        elif metric == "avg_target_cost_usd" and case.target_cost_usd is not None:
            metric_values.append(float(case.target_cost_usd))
        elif metric == "avg_total_cost_usd":
            metric_values.append(
                float(case.test_ai_cost_usd or 0) + float(case.target_cost_usd or 0)
            )
    return metric_values


def _case_approval(case: CaseResult) -> dict[str, Any]:
    approval = (case.structured_output or {}).get("approval")
    normalized = normalized_evaluation_contract(approval)
    return dict(normalized) if isinstance(normalized, dict) else {}


def _case_lifecycle(case: CaseResult) -> str:
    return str(_case_approval(case).get("lifecycle_state") or "")


def _canonical_requirements(value: dict[str, Any] | None) -> list[dict[str, Any]]:
    values = (value or {}).get("requirement_states")
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _parity_requirements(value: dict[str, Any] | None) -> list[dict[str, Any]]:
    normalized = normalized_evaluation_contract(value or {})
    values = normalized.get("requirement_states") if isinstance(normalized, dict) else None
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _canonical_unresolved(value: dict[str, Any] | None) -> list[dict[str, Any]]:
    values = (value or {}).get("unresolved_fields")
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _turn_reliability_metrics(cases: list[CaseResult]) -> dict[str, float]:
    assistant_turns = [
        turn for case in cases for turn in case.turns if turn.role == "assistant"
    ]
    repair_attempts = sum(
        int((turn.usage or {}).get("planner_repair_attempt_count") or 0)
        for turn in assistant_turns
    )
    repair_successes = sum(
        int((turn.usage or {}).get("planner_repair_success_count") or 0)
        for turn in assistant_turns
    )
    blocking_states = [
        item
        for case in cases
        for item in _canonical_requirements(case.canonical_state)
        if item.get("blocking")
    ]
    stale_states = [
        item
        for item in blocking_states
        if item.get("satisfied")
        or (
            item.get("supported", True)
            and item.get("grounded")
            and not item.get("conflicting")
            and item.get("normalized_value") not in (None, "", [], {})
        )
    ]

    question_occurrences = 0
    repeated_questions = 0
    unnecessary_questions = 0
    for case in cases:
        previously_seen: set[str] = set()
        for turn in case.turns:
            if turn.role != "assistant" or turn.canonical_state is None:
                continue
            unresolved = _canonical_unresolved(turn.canonical_state)
            blockers = {
                str(item.get("requirement_id"))
                for item in _canonical_requirements(turn.canonical_state)
                if item.get("blocking") and item.get("requirement_id")
            }
            current_ids = {
                str(item.get("unresolved_id"))
                for item in unresolved
                if item.get("blocking", True) and item.get("unresolved_id")
            }
            question_occurrences += len(current_ids)
            repeated_questions += len(current_ids & previously_seen)
            if current_ids and not blockers:
                unnecessary_questions += len(current_ids)
            previously_seen |= current_ids

    grounding_failures = sum(
        failure in {"VALUE_NOT_GROUNDED", "SPAN_NOT_GROUNDED"}
        for case in cases
        for failure in case.product_failure_classes
    )
    total_product_failures = sum(len(case.product_failure_classes) for case in cases)
    return {
        "planner_repair_attempts": float(repair_attempts),
        "planner_repair_successes": float(repair_successes),
        "planner_repair_success_rate": repair_successes / max(1, repair_attempts),
        "stale_requirement_rate": len(stale_states) / max(1, len(blocking_states)),
        "repeated_question_rate": repeated_questions / max(1, question_occurrences),
        "unnecessary_question_rate": unnecessary_questions / max(1, question_occurrences),
        "grounding_rejection_rate": grounding_failures / max(1, total_product_failures),
    }


def _milestone_metrics(cases: list[CaseResult]) -> dict[str, float]:
    observed: dict[str, list[tuple[float, float, float]]] = {
        "first_valid_draft": [],
        "approval_eligibility": [],
    }
    for case in cases:
        user_turns = 0
        latency_ms = 0.0
        cost_usd = 0.0
        found: set[str] = set()
        for turn in case.turns:
            if turn.role == "user":
                user_turns += 1
                continue
            latency_ms += float(turn.latency_ms or 0.0)
            cost_usd += float((turn.usage or {}).get("estimated_cost_usd") or 0.0)
            if turn.structured is None:
                continue
            if "first_valid_draft" not in found:
                observed["first_valid_draft"].append((float(user_turns), latency_ms, cost_usd))
                found.add("first_valid_draft")
            approval = turn.structured.get("approval")
            if (
                "approval_eligibility" not in found
                and isinstance(approval, dict)
                and approval.get("eligible") is True
            ):
                observed["approval_eligibility"].append(
                    (float(user_turns), latency_ms, cost_usd)
                )
                found.add("approval_eligibility")
    metrics: dict[str, float] = {}
    for name, values in observed.items():
        metrics[f"{name}_coverage"] = len(values) / max(1, len(cases))
        metrics[f"turns_to_{name}"] = mean(item[0] for item in values) if values else 0.0
        metrics[f"latency_ms_to_{name}"] = mean(item[1] for item in values) if values else 0.0
        metrics[f"cost_usd_to_{name}"] = mean(item[2] for item in values) if values else 0.0
    return metrics


def _criterion_pass(operator: str, actual: float, threshold: float) -> bool:
    if operator == ">=":
        return actual >= threshold
    if operator == "<=":
        return actual <= threshold
    if operator == "==":
        return abs(actual - threshold) <= 1e-9
    raise ValueError(operator)


def aggregate(cases: list[CaseResult]) -> dict[str, Any]:
    by_topic: dict[str, list[CaseResult]] = defaultdict(list)
    for case in cases:
        by_topic[case.scenario.topic_id].append(case)
    topic_rows: list[dict[str, Any]] = []
    critical_failed: list[str] = []
    unmeasured_topics: list[str] = []
    errored_cases = sum(c.error is not None for c in cases)
    # A transport fault, expired login or truncated stream is not evidence about the
    # chatbot's answers. Run 20260723T152343Z scored two truncated exchanges as
    # chatbot failures; these are counted and named separately instead.
    infrastructure_cases = [c for c in cases if c.failure is not None]
    infrastructure_classes = sorted(
        {
            str((c.failure or {}).get("failure_class"))
            for c in infrastructure_cases
            if (c.failure or {}).get("failure_class")
        }
    )
    quality_cases = [
        c
        for c in cases
        if c.failure is None and c.measurement_status == "MEASURED"
    ]
    not_measured_cases = [
        c
        for c in cases
        if c.failure is None and c.measurement_status == "NOT_MEASURED"
    ]
    pending_judges = sum(
        c.judge is None
        and c.error is None
        and c.measurement_status == "MEASURED"
        and bool(c.deterministic_metrics)
        and bool(c.deterministic_metrics.get("judge_eligible", 1.0))
        for c in cases
    )
    for topic_id, items in sorted(by_topic.items()):
        topic = TOPIC_BY_ID[topic_id]
        # Quality is scored only over cases where the chatbot actually answered. A
        # case lost to infrastructure is reported, never averaged into the verdict.
        scored = [
            c
            for c in items
            if c.failure is None and c.measurement_status == "MEASURED"
        ]
        topic_infrastructure = sum(c.failure is not None for c in items)
        pass_rate = sum(c.passed for c in scored) / len(scored) if scored else 0.0
        score = mean(_case_score(c) for c in scored) if scored else 0.0
        latency = mean(c.total_latency_ms for c in items)
        test_cost = sum((c.test_ai_cost_usd or 0) for c in items)
        target_cost = sum((c.target_cost_usd or 0) for c in items)
        criteria = []
        topic_criteria_pass = True
        topic_unmeasured = not scored or any(
            c.measurement_status == "NOT_MEASURED" for c in items
        )
        for criterion in topic.criteria:
            values = _metric_values(scored, criterion.metric)
            actual = mean(values) if values else None
            # An unmeasurable criterion is not a failed criterion. Marking drift FAIL
            # when no baseline/candidate pair exists reports a regression that was
            # never observed; the run is incomplete, not broken.
            if not values:
                status = "NOT_MEASURED"
                passed = False
            elif _criterion_pass(criterion.operator, float(actual or 0.0), criterion.threshold):
                status = "PASS"
                passed = True
            else:
                status = "FAIL"
                passed = False
            criteria.append(
                {
                    "metric": criterion.metric,
                    "operator": criterion.operator,
                    "threshold": criterion.threshold,
                    "actual": actual,
                    "status": status,
                    "passed": passed,
                    "critical": criterion.critical,
                    "description": criterion.description,
                    "measured_cases": len(values),
                    "requires_paired_variants": criterion.metric in PAIRED_VARIANT_METRICS,
                }
            )
            if criterion.critical and status == "FAIL":
                topic_criteria_pass = False
            if criterion.critical and status == "NOT_MEASURED":
                topic_unmeasured = True
        row = {
            "topic_id": topic_id,
            "title": topic.title,
            "category": topic.category,
            "severity": topic.severity,
            "cases": len(items),
            "quality_measured_cases": len(scored),
            "infrastructure_failed_cases": topic_infrastructure,
            "pass_rate": pass_rate,
            "score": score,
            "avg_latency_ms": latency,
            "test_ai_cost_usd": test_cost,
            "target_cost_usd": target_cost,
            "criteria_pass": topic_criteria_pass,
            "criteria_not_measured": topic_unmeasured,
            "criteria_status": (
                "FAIL"
                if any(item["status"] == "FAIL" for item in criteria)
                else "NOT_MEASURED"
                if any(item["status"] == "NOT_MEASURED" for item in criteria)
                else "PASS"
            ),
            "criteria": criteria,
        }
        topic_rows.append(row)
        if topic.severity == "critical" and scored and (pass_rate < 1 or not topic_criteria_pass):
            critical_failed.append(topic_id)
        if topic_unmeasured:
            unmeasured_topics.append(topic_id)
    total_cost = sum((c.test_ai_cost_usd or 0) + (c.target_cost_usd or 0) for c in cases)
    target_cost = sum(c.target_cost_usd or 0 for c in cases)
    evaluator_cost = sum(c.test_ai_cost_usd or 0 for c in cases)
    schema_valid_cases = sum(
        float(c.deterministic_metrics.get("schema_valid", 0.0)) >= 1.0 for c in quality_cases
    )
    semantic_contract_passed = sum(
        float(c.deterministic_metrics.get("semantic_contract_pass", 0.0)) >= 1.0
        for c in quality_cases
    )
    judged_cases = [case for case in quality_cases if case.judge is not None]
    judge_passed_cases = sum(bool(case.judge and case.judge.passed) for case in judged_cases)
    measured_criterion_failed = any(
        criterion["status"] == "FAIL" for row in topic_rows for criterion in row["criteria"]
    )
    deterministic_quality_failed = any(
        (
            "schema_valid" in case.deterministic_metrics
            and float(case.deterministic_metrics["schema_valid"]) < 1.0
        )
        or (
            "semantic_contract_pass" in case.deterministic_metrics
            and float(case.deterministic_metrics["semantic_contract_pass"]) < 1.0
        )
        for case in quality_cases
    )
    if not quality_cases:
        quality_status = "NOT_MEASURED"
    elif deterministic_quality_failed or measured_criterion_failed:
        # A pending language-model judge cannot overrule a deterministic schema or
        # semantic-contract failure. Report the known verdict immediately.
        quality_status = "FAIL"
    elif pending_judges:
        quality_status = "PENDING_JUDGE"
    elif not all(case.passed for case in quality_cases):
        quality_status = "FAIL"
    elif unmeasured_topics:
        quality_status = "PARTIAL"
    else:
        quality_status = "PASS"

    if critical_failed:
        critical_release_status = "FAIL"
    elif unmeasured_topics:
        critical_release_status = "NOT_MEASURED"
    else:
        critical_release_status = "PASS"

    if not cases:
        workflow_status = "NOT_STARTED"
    elif infrastructure_cases and not quality_cases:
        workflow_status = "BLOCKED"
    elif infrastructure_cases or errored_cases:
        workflow_status = "PARTIAL"
    elif pending_judges:
        workflow_status = "PENDING_JUDGE"
    else:
        workflow_status = "COMPLETED"

    if not quality_cases:
        measurement_status = "NOT_MEASURED"
    elif pending_judges:
        measurement_status = "PENDING"
    elif unmeasured_topics:
        measurement_status = "PARTIAL"
    else:
        measurement_status = "COMPLETE"

    infrastructure_status = (
        "UNAVAILABLE"
        if infrastructure_cases and not quality_cases
        else "DEGRADED"
        if infrastructure_cases
        else "HEALTHY"
    )

    if infrastructure_cases and not quality_cases:
        # Nothing about the chatbot was observed, so there is no verdict to give.
        gate = "INCOMPLETE"
    elif errored_cases or quality_status == "FAIL" or critical_release_status == "FAIL":
        gate = "FAIL"
    elif pending_judges:
        gate = "PENDING_JUDGE"
    elif critical_failed or not all(c.passed for c in quality_cases) or not cases:
        gate = "FAIL"
    elif unmeasured_topics:
        # Everything that could be measured passed, but a critical criterion had no
        # data. That is an incomplete run, not a release-blocking regression.
        gate = "INCOMPLETE"
    else:
        gate = "PASS"
    reliability_metrics = _turn_reliability_metrics(quality_cases)
    milestone_metrics = _milestone_metrics(quality_cases)
    parity_metrics: dict[str, float | None] = {}
    for metric in (
        "backend_ui_contract_match",
        "backend_ui_status_match",
        "backend_ui_error_class_match",
        "backend_ui_turn_count_delta",
        "backend_ui_approval_state_match",
        "backend_ui_requirement_state_match",
    ):
        values = _metric_values(cases, metric)
        parity_metrics[metric] = mean(values) if values else None
    return {
        "cases": len(cases),
        "passed": sum(c.passed for c in quality_cases),
        "pass_rate": sum(c.passed for c in quality_cases) / max(1, len(quality_cases)),
        "strict_pass_rate": sum(c.passed for c in quality_cases) / max(1, len(quality_cases)),
        "clean_turn_success_rate": sum(c.clean_turn_success for c in quality_cases)
        / max(1, len(quality_cases)),
        "eventual_case_success_rate": sum(c.eventual_case_success for c in quality_cases)
        / max(1, len(quality_cases)),
        "product_reliability_failure_classes": sorted(
            {
                failure
                for case in quality_cases
                for failure in case.product_failure_classes
            }
        ),
        **reliability_metrics,
        **milestone_metrics,
        **parity_metrics,
        "schema_failure_rate": 1.0
        - (schema_valid_cases / max(1, len(quality_cases))),
        "semantic_role_swap_rate": mean(
            case.deterministic_metrics.get("semantic_role_swap_rate", 0.0)
            for case in quality_cases
        )
        if quality_cases
        else 0.0,
        "approval_evaluator_false_positive_rate": mean(
            case.deterministic_metrics.get(
                "approval_evaluator_false_positive_rate", 0.0
            )
            for case in quality_cases
        )
        if quality_cases
        else 0.0,
        "approval_evaluator_false_negative_rate": mean(
            case.deterministic_metrics.get(
                "approval_evaluator_false_negative_rate", 0.0
            )
            for case in quality_cases
        )
        if quality_cases
        else 0.0,
        "average_score": mean(_case_score(c) for c in quality_cases) if quality_cases else 0,
        "total_test_cost_usd": total_cost,
        "target_api_cost_usd": target_cost,
        "evaluator_api_cost_usd": evaluator_cost,
        "errored_cases": errored_cases,
        "infrastructure_failed_cases": len(infrastructure_cases),
        "infrastructure_failure_classes": infrastructure_classes,
        "quality_measured_cases": len(quality_cases),
        "not_measured_cases": len(not_measured_cases),
        "measurement_coverage": len(quality_cases) / max(1, len(cases)),
        "schema_valid_cases": schema_valid_cases,
        "schema_valid_rate": schema_valid_cases / max(1, len(quality_cases)),
        "semantic_contract_passed": semantic_contract_passed,
        "semantic_contract_pass_rate": semantic_contract_passed
        / max(1, len(quality_cases)),
        "judge_measured_cases": len(judged_cases),
        "judge_passed_cases": judge_passed_cases,
        "judge_pass_rate": judge_passed_cases / max(1, len(judged_cases)),
        "pending_judges": pending_judges,
        "critical_topics_failed": critical_failed,
        "critical_topics_not_measured": unmeasured_topics,
        "quality_status": quality_status,
        "critical_release_status": critical_release_status,
        "workflow_status": workflow_status,
        "measurement_status": measurement_status,
        "infrastructure_status": infrastructure_status,
        "release_gate": gate,
        "topics": topic_rows,
    }


def write_reports(
    run_dir: Path,
    cases: list[CaseResult],
    *,
    budget_usd: float | None = None,
    measured_spend_usd: float | None = None,
    execution_status: str | None = None,
    execution_error: str | None = None,
    run_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_dir(run_dir)
    summary = aggregate(cases)
    if budget_usd is not None:
        measured = (
            float(measured_spend_usd)
            if measured_spend_usd is not None
            else float(summary["total_test_cost_usd"])
        )
        summary["budget_usd"] = float(budget_usd)
        summary["measured_spend_usd"] = measured
        summary["budget_remaining_usd"] = max(0.0, float(budget_usd) - measured)
    if run_metadata:
        summary["run_metadata"] = run_metadata
    if execution_status:
        summary["execution_status"] = execution_status
        summary["execution_error"] = execution_error
        summary["workflow_status"] = (
            "STOPPED_BUDGET" if execution_status == "STOPPED_BUDGET" else "BLOCKED"
        )
        if execution_status != "STOPPED_BUDGET":
            summary["infrastructure_status"] = "UNAVAILABLE"
        summary["release_gate"] = "INCOMPLETE"
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (run_dir / "cases.jsonl").open("w", encoding="utf-8") as f:
        for case in cases:
            f.write(json.dumps(case.to_dict(), ensure_ascii=False) + "\n")
    failures = [c for c in cases if not c.passed]
    with (run_dir / "failures.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "scenario_id",
                "topic",
                "target",
                "score",
                "error",
                "failure_class",
                "is_quality_signal",
                "proof",
                "recommended_fix",
                "reproduce",
            ],
        )
        writer.writeheader()
        for c in failures:
            proof = "; ".join(e.detail for e in (c.judge.evidence if c.judge else [])[:3])
            recommended_fix = "; ".join(
                str(item.get("action", item)) for item in (c.judge.fixes if c.judge else [])[:2]
            )
            writer.writerow(
                {
                    "scenario_id": c.scenario.id,
                    "topic": c.scenario.topic_id,
                    "target": f"{c.target_kind}:{c.target_variant}",
                    "score": _case_score(c),
                    "error": c.error or "",
                    # Names the infrastructure cause when there is one, so a reader
                    # can tell a transport fault from a wrong answer at a glance.
                    "failure_class": str((c.failure or {}).get("failure_class") or ""),
                    "is_quality_signal": "false" if c.failure else "true",
                    "proof": proof,
                    "recommended_fix": recommended_fix,
                    "reproduce": f"hm-chatbot-eval replay {c.run_id} {c.scenario.id} --target {c.target_kind}",
                }
            )
    parity_summary = " · ".join(
        f"{key.removeprefix('backend_ui_').replace('_', ' ')}: "
        + ("NOT MEASURED" if summary[key] is None else f"{summary[key]:.3f}")
        for key in (
            "backend_ui_contract_match",
            "backend_ui_status_match",
            "backend_ui_error_class_match",
            "backend_ui_turn_count_delta",
            "backend_ui_approval_state_match",
            "backend_ui_requirement_state_match",
        )
    )
    md = [
        "# HilalMarkets AI Setup Chat Evaluation\n",
        f"**Release gate:** {summary['release_gate']}  ",
        (
            f"**Quality:** {summary['quality_status']} | "
            f"**Critical release:** {summary['critical_release_status']} | "
            f"**Workflow:** {summary['workflow_status']} | "
            f"**Measurement:** {summary['measurement_status']} | "
            f"**Infrastructure:** {summary['infrastructure_status']}  "
        ),
        f"**Attempted:** {summary['cases']} · **Measured:** "
        f"{summary['quality_measured_cases']} ({summary['measurement_coverage']:.1%}) · "
        f"**Strict end-to-end pass:** {summary['passed']}/"
        f"{summary['quality_measured_cases']} ({summary['strict_pass_rate']:.1%}) · "
        f"**Average judge score:** {summary['average_score']:.3f}\n",
        f"**Schema valid:** {summary['schema_valid_cases']}/"
        f"{summary['quality_measured_cases']} ({summary['schema_valid_rate']:.1%}) · "
        f"**Deterministic semantic contract:** {summary['semantic_contract_passed']}/"
        f"{summary['quality_measured_cases']} "
        f"({summary['semantic_contract_pass_rate']:.1%}) · **Judge pass:** "
        f"{summary['judge_passed_cases']}/{summary['judge_measured_cases']} "
        f"({summary['judge_pass_rate']:.1%})\n",
        f"**Measured API cost:** ${summary['total_test_cost_usd']:.4f} "
        f"(target ${summary['target_api_cost_usd']:.4f}; evaluator "
        f"${summary['evaluator_api_cost_usd']:.4f})\n",
        "## Reliability and convergence\n",
        f"**Clean turn success:** {summary['clean_turn_success_rate']:.1%} · "
        f"**Eventual case success:** {summary['eventual_case_success_rate']:.1%} · "
        f"**Schema failure:** {summary['schema_failure_rate']:.1%}\n",
        f"**Planner repair:** {int(summary['planner_repair_successes'])}/"
        f"{int(summary['planner_repair_attempts'])} successful "
        f"({summary['planner_repair_success_rate']:.1%}) · "
        f"**Grounding rejection:** {summary['grounding_rejection_rate']:.1%}\n",
        f"**Stale requirements:** {summary['stale_requirement_rate']:.1%} · "
        f"**Repeated questions:** {summary['repeated_question_rate']:.1%} · "
        f"**Unnecessary questions:** {summary['unnecessary_question_rate']:.1%} · "
        f"**Semantic role swaps:** {summary['semantic_role_swap_rate']:.1%}\n",
        f"**Approval evaluator false positive / false negative:** "
        f"{summary['approval_evaluator_false_positive_rate']:.1%} / "
        f"{summary['approval_evaluator_false_negative_rate']:.1%}\n",
        f"**First valid draft:** {summary['turns_to_first_valid_draft']:.2f} turns, "
        f"{summary['latency_ms_to_first_valid_draft']:.0f} ms, "
        f"${summary['cost_usd_to_first_valid_draft']:.4f} average "
        f"(coverage {summary['first_valid_draft_coverage']:.1%})\n",
        f"**Approval eligibility:** {summary['turns_to_approval_eligibility']:.2f} turns, "
        f"{summary['latency_ms_to_approval_eligibility']:.0f} ms, "
        f"${summary['cost_usd_to_approval_eligibility']:.4f} average "
        f"(coverage {summary['approval_eligibility_coverage']:.1%})\n",
        f"**Backend/UI parity:** {parity_summary}\n",
        "## Topic results\n",
        "| Topic | Severity | Cases | Pass | Score |",
        "|---|---:|---:|---:|---:|",
    ]
    if execution_status:
        md.insert(
            3,
            f"**Execution stopped:** `{execution_status}` · {execution_error or 'No detail'}\n",
        )
    if run_metadata:
        md.insert(
            3,
            f"**Randomized selection seed:** `{run_metadata['selection_seed']}` · "
            f"Scenario seed: `{run_metadata['scenario_seed']}` · "
            f"Plan: `{run_metadata['selection_strategy']}`\n",
        )
    if summary["infrastructure_failed_cases"]:
        md.insert(
            3,
            f"**Not a quality signal:** {summary['infrastructure_failed_cases']} of "
            f"{summary['cases']} cases ended on infrastructure "
            f"(`{'`, `'.join(summary['infrastructure_failure_classes'])}`) and are "
            f"excluded from the pass rate and scores above.\n",
        )
    for row in summary["topics"]:
        md.append(
            f"| {row['title']} | {row['severity']} | {row['cases']} | {row['pass_rate']:.1%} | {row['score']:.3f} |"
        )
        for criterion in row["criteria"]:
            status = criterion.get("status") or ("PASS" if criterion["passed"] else "FAIL")
            if status == "NOT_MEASURED":
                reason = (
                    "no baseline/candidate variant pair"
                    if criterion.get("requires_paired_variants")
                    else "no measured cases"
                )
                md.append(
                    f"  - NOT_MEASURED `{criterion['metric']}` ({reason}) required "
                    f"{criterion['operator']} {criterion['threshold']} — {criterion['description']}"
                )
                continue
            md.append(
                f"  - {status} `{criterion['metric']}` actual={criterion['actual']} required {criterion['operator']} {criterion['threshold']} — {criterion['description']}"
            )
    md.append("\n## Evidence-backed failures\n")
    for case in sorted(failures, key=_case_score)[:100]:
        md.append(f"### {case.scenario.id} — {_case_score(case):.3f}")
        if case.error:
            md.append(f"- Runtime error: `{case.error}`")
        if case.schema_errors:
            md.append(f"- Schema: {case.schema_errors[0]}")
        if case.judge:
            for failure in case.judge.failures[:4]:
                md.append(f"- Failure: {failure}")
            for evidence in case.judge.evidence[:4]:
                md.append(f"- Proof `{evidence.reference}`: {evidence.detail}")
            for fix_item in case.judge.fixes[:3]:
                md.append(f"- Fix: {fix_item}")
        md.append(
            f"- Reproduce: `hm-chatbot-eval replay {case.run_id} {case.scenario.id} --target {case.target_kind}`\n"
        )
    (run_dir / "report.md").write_text("\n".join(md), encoding="utf-8")

    topic_cards = "".join(
        f"<tr><td>{html.escape(r['title'])}</td><td>{r['severity']}</td><td>{r['cases']}</td><td>{r['pass_rate']:.1%}</td><td>{r['score']:.3f}</td><td>{r['criteria_status']}</td><td>{r['avg_latency_ms']:.0f} ms</td></tr>"
        for r in summary["topics"]
    )
    failure_cards = (
        "".join(
            f"<article><h3>{html.escape(c.scenario.id)} <span>{_case_score(c):.3f}</span></h3>"
            f"<p><b>Topic:</b> {html.escape(c.scenario.topic_id)} · <b>Target:</b> {c.target_kind}:{html.escape(c.target_variant)}</p>"
            f"<p><b>Observed:</b> {html.escape(str(c.judge.failures[0] if c.judge and c.judge.failures else c.error or c.schema_errors[:1]))}</p>"
            f"<p><b>Proof:</b> {html.escape('; '.join(e.detail for e in (c.judge.evidence if c.judge else [])[:3]))}</p>"
            f"<p><b>Fix:</b> {html.escape('; '.join(str(x) for x in (c.judge.fixes if c.judge else [])[:2]))}</p></article>"
            for c in sorted(failures, key=_case_score)[:100]
        )
        or "<p>No failing cases.</p>"
    )
    budget_card = (
        f'<div class="kpi"><b>${summary["budget_remaining_usd"]:.2f}</b>Budget remaining</div>'
        if "budget_remaining_usd" in summary
        else ""
    )
    reliability_cards = (
        f'<div class="kpi"><b>{summary["clean_turn_success_rate"]:.1%}</b>Clean turns</div>'
        f'<div class="kpi"><b>{summary["eventual_case_success_rate"]:.1%}</b>Eventual cases</div>'
        f'<div class="kpi"><b>{int(summary["planner_repair_successes"])}/'
        f'{int(summary["planner_repair_attempts"])}</b>Planner repairs</div>'
        f'<div class="kpi"><b>{summary["stale_requirement_rate"]:.1%}</b>Stale requirements</div>'
        f'<div class="kpi"><b>{summary["repeated_question_rate"]:.1%}</b>Repeated questions</div>'
        f'<div class="kpi"><b>{summary["semantic_role_swap_rate"]:.1%}</b>Role swaps</div>'
        f'<div class="kpi"><b>{summary["turns_to_first_valid_draft"]:.2f}</b>Turns to draft</div>'
        f'<div class="kpi"><b>{summary["turns_to_approval_eligibility"]:.2f}</b>Turns to approval eligibility</div>'
    )
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>HilalMarkets Chatbot Evaluation</title><style>
body{{font:15px system-ui;margin:0;background:#f6f4ec;color:#18231e}}main{{max-width:1200px;margin:auto;padding:32px}}header{{background:#073f34;color:white;padding:28px;border-radius:18px}}.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}}.kpi,article{{background:white;border:1px solid #ddd8c8;border-radius:14px;padding:16px}}.kpi b{{font-size:26px;display:block}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{padding:10px;border-bottom:1px solid #eee;text-align:left}}th{{position:sticky;top:0;background:#eee9da}}.fail{{color:#a02d2d}}.pass{{color:#0b6b51}}article h3{{margin-top:0;display:flex;justify-content:space-between}}code{{white-space:pre-wrap}}@media(max-width:700px){{.kpis{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main><header><h1>AI Setup Chat Evaluation</h1><p>Evidence-backed AI-vs-AI quality and reliability report</p><h2 class="{"pass" if summary["release_gate"] == "PASS" else "fail"}">Release gate: {summary["release_gate"]}</h2><p>Quality: {summary["quality_status"]} | Critical release: {summary["critical_release_status"]} | Workflow: {summary["workflow_status"]} | Measurement: {summary["measurement_status"]} | Infrastructure: {summary["infrastructure_status"]}</p></header><section class="kpis"><div class="kpi"><b>{summary["quality_measured_cases"]}/{summary["cases"]}</b>Measured answers</div><div class="kpi"><b>{summary["strict_pass_rate"]:.1%}</b>Strict end-to-end pass</div><div class="kpi"><b>{summary["schema_valid_rate"]:.1%}</b>Schema valid</div><div class="kpi"><b>{summary["semantic_contract_pass_rate"]:.1%}</b>Semantic contract</div><div class="kpi"><b>${summary["target_api_cost_usd"]:.4f}</b>Target API cost</div><div class="kpi"><b>${summary["evaluator_api_cost_usd"]:.4f}</b>Evaluator API cost</div>{budget_card}</section><h2>Reliability and convergence</h2><section class="kpis">{reliability_cards}</section><p>{html.escape(parity_summary)}</p><h2>Topic results</h2><div style="overflow:auto;max-height:650px"><table><thead><tr><th>Topic</th><th>Severity</th><th>Cases</th><th>Pass</th><th>Score</th><th>Criteria</th><th>Latency</th></tr></thead><tbody>{topic_cards}</tbody></table></div><h2>Failures and fixes with proof</h2>{failure_cards}</main></body></html>"""
    (run_dir / "report.html").write_text(page, encoding="utf-8")
    return summary
