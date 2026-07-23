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
from .util import ensure_dir


def _case_score(case: CaseResult) -> float:
    return case.judge.score if case.judge else case.deterministic_metrics.get("schema_valid", 0.0)


def _metric_values(items: list[CaseResult], metric: str) -> list[float]:
    if metric in {"pass_flip_rate", "semantic_score_delta_abs"}:
        grouped: dict[tuple[str, str], list[CaseResult]] = defaultdict(list)
        for case in items:
            grouped[(case.scenario.id, case.target_kind)].append(case)
        flips: list[float] = []
        deltas: list[float] = []
        for variants in grouped.values():
            if len(variants) < 2:
                continue
            base = variants[0]
            for other in variants[1:]:
                flips.append(float(base.passed != other.passed))
                deltas.append(abs(_case_score(base) - _case_score(other)))
        if metric == "pass_flip_rate":
            return [sum(flips) / len(flips)] if flips else []
        return [sum(deltas) / len(deltas)] if deltas else []
    if metric == "ui_backend_parity":
        grouped: dict[tuple[str, str], dict[str, CaseResult]] = defaultdict(dict)
        for case in items:
            grouped[(case.scenario.id, case.target_variant)][case.target_kind] = case
        values = []
        for pair in grouped.values():
            if "ui" in pair and "backend" in pair:
                a, b = pair["ui"], pair["backend"]
                if a.structured_hash and b.structured_hash:
                    values.append(float(a.structured_hash == b.structured_hash))
                else:
                    values.append(float(abs(_case_score(a) - _case_score(b)) <= 0.02))
        return values
    if metric == "reproducibility":
        grouped: dict[tuple[str, str, str], list[CaseResult]] = defaultdict(list)
        for case in items:
            grouped[(case.scenario.id, case.target_kind, case.target_variant)].append(case)
        return [float(group[0].structured_hash == group[1].structured_hash) for group in grouped.values() if len(group) >= 2 and group[0].structured_hash and group[1].structured_hash]
    values: list[float] = []
    for case in items:
        if metric in case.deterministic_metrics:
            values.append(float(case.deterministic_metrics[metric]))
        elif case.judge and metric in case.judge.dimension_scores:
            values.append(float(case.judge.dimension_scores[metric]))
        elif metric == "avg_test_ai_cost_usd" and case.test_ai_cost_usd is not None:
            values.append(float(case.test_ai_cost_usd))
        elif metric == "avg_target_cost_usd" and case.target_cost_usd is not None:
            values.append(float(case.target_cost_usd))
        elif metric == "avg_total_cost_usd":
            values.append(float(case.test_ai_cost_usd or 0) + float(case.target_cost_usd or 0))
    return values


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
    topic_rows = []
    critical_failed = []
    pending_judges = sum(c.judge is None for c in cases)
    for topic_id, items in sorted(by_topic.items()):
        topic = TOPIC_BY_ID[topic_id]
        pass_rate = sum(c.passed for c in items) / len(items)
        score = mean(_case_score(c) for c in items)
        latency = mean(c.total_latency_ms for c in items)
        test_cost = sum((c.test_ai_cost_usd or 0) for c in items)
        target_cost = sum((c.target_cost_usd or 0) for c in items)
        criteria = []
        topic_criteria_pass = True
        for criterion in topic.criteria:
            values = _metric_values(items, criterion.metric)
            actual = mean(values) if values else None
            passed = bool(values) and _criterion_pass(criterion.operator, float(actual), criterion.threshold)
            criteria.append({
                "metric": criterion.metric,
                "operator": criterion.operator,
                "threshold": criterion.threshold,
                "actual": actual,
                "passed": passed,
                "critical": criterion.critical,
                "description": criterion.description,
                "measured_cases": len(values),
            })
            if criterion.critical and not passed:
                topic_criteria_pass = False
        row = {
            "topic_id": topic_id,
            "title": topic.title,
            "category": topic.category,
            "severity": topic.severity,
            "cases": len(items),
            "pass_rate": pass_rate,
            "score": score,
            "avg_latency_ms": latency,
            "test_ai_cost_usd": test_cost,
            "target_cost_usd": target_cost,
            "criteria_pass": topic_criteria_pass,
            "criteria": criteria,
        }
        topic_rows.append(row)
        if topic.severity == "critical" and (pass_rate < 1 or not topic_criteria_pass):
            critical_failed.append(topic_id)
    total_cost = sum((c.test_ai_cost_usd or 0) + (c.target_cost_usd or 0) for c in cases)
    if pending_judges:
        gate = "PENDING_JUDGE"
    else:
        gate = "PASS" if cases and not critical_failed and all(c.passed for c in cases) else "FAIL"
    return {
        "cases": len(cases),
        "passed": sum(c.passed for c in cases),
        "pass_rate": sum(c.passed for c in cases) / max(1, len(cases)),
        "average_score": mean(_case_score(c) for c in cases) if cases else 0,
        "total_test_cost_usd": total_cost,
        "pending_judges": pending_judges,
        "critical_topics_failed": critical_failed,
        "release_gate": gate,
        "topics": topic_rows,
    }


def write_reports(run_dir: Path, cases: list[CaseResult]) -> dict[str, Any]:
    ensure_dir(run_dir)
    summary = aggregate(cases)
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (run_dir / "cases.jsonl").open("w", encoding="utf-8") as f:
        for case in cases:
            f.write(json.dumps(case.to_dict(), ensure_ascii=False) + "\n")
    failures = [c for c in cases if not c.passed]
    with (run_dir / "failures.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["scenario_id", "topic", "target", "score", "error", "proof", "recommended_fix", "reproduce"])
        writer.writeheader()
        for c in failures:
            proof = "; ".join(e.detail for e in (c.judge.evidence if c.judge else [])[:3])
            fix = "; ".join(str(x.get("action", x)) for x in (c.judge.fixes if c.judge else [])[:2])
            writer.writerow({
                "scenario_id": c.scenario.id,
                "topic": c.scenario.topic_id,
                "target": f"{c.target_kind}:{c.target_variant}",
                "score": _case_score(c),
                "error": c.error or "",
                "proof": proof,
                "recommended_fix": fix,
                "reproduce": f"hm-chatbot-eval replay {c.run_id} {c.scenario.id} --target {c.target_kind}",
            })
    md = [f"# HilalMarkets AI Setup Chat Evaluation\n", f"**Release gate:** {summary['release_gate']}  ", f"**Cases:** {summary['cases']} · **Pass rate:** {summary['pass_rate']:.1%} · **Average score:** {summary['average_score']:.3f} · **Measured evaluator cost:** ${summary['total_test_cost_usd']:.4f}\n", "## Topic results\n", "| Topic | Severity | Cases | Pass | Score |", "|---|---:|---:|---:|---:|"]
    for row in summary["topics"]:
        md.append(f"| {row['title']} | {row['severity']} | {row['cases']} | {row['pass_rate']:.1%} | {row['score']:.3f} |")
        for criterion in row['criteria']:
            status = 'PASS' if criterion['passed'] else 'FAIL'
            md.append(f"  - {status} `{criterion['metric']}` actual={criterion['actual']} required {criterion['operator']} {criterion['threshold']} — {criterion['description']}")
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
            for fix in case.judge.fixes[:3]:
                md.append(f"- Fix: {fix}")
        md.append(f"- Reproduce: `hm-chatbot-eval replay {case.run_id} {case.scenario.id} --target {case.target_kind}`\n")
    (run_dir / "report.md").write_text("\n".join(md), encoding="utf-8")

    topic_cards = "".join(
        f"<tr><td>{html.escape(r['title'])}</td><td>{r['severity']}</td><td>{r['cases']}</td><td>{r['pass_rate']:.1%}</td><td>{r['score']:.3f}</td><td>{'PASS' if r['criteria_pass'] else 'FAIL'}</td><td>{r['avg_latency_ms']:.0f} ms</td></tr>"
        for r in summary["topics"]
    )
    failure_cards = "".join(
        f"<article><h3>{html.escape(c.scenario.id)} <span>{_case_score(c):.3f}</span></h3>"
        f"<p><b>Topic:</b> {html.escape(c.scenario.topic_id)} · <b>Target:</b> {c.target_kind}:{html.escape(c.target_variant)}</p>"
        f"<p><b>Observed:</b> {html.escape(str((c.judge.failures[0] if c.judge and c.judge.failures else c.error or c.schema_errors[:1]) ))}</p>"
        f"<p><b>Proof:</b> {html.escape('; '.join(e.detail for e in (c.judge.evidence if c.judge else [])[:3]))}</p>"
        f"<p><b>Fix:</b> {html.escape('; '.join(str(x) for x in (c.judge.fixes if c.judge else [])[:2]))}</p></article>"
        for c in sorted(failures, key=_case_score)[:100]
    ) or "<p>No failing cases.</p>"
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>HilalMarkets Chatbot Evaluation</title><style>
body{{font:15px system-ui;margin:0;background:#f6f4ec;color:#18231e}}main{{max-width:1200px;margin:auto;padding:32px}}header{{background:#073f34;color:white;padding:28px;border-radius:18px}}.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}}.kpi,article{{background:white;border:1px solid #ddd8c8;border-radius:14px;padding:16px}}.kpi b{{font-size:26px;display:block}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{padding:10px;border-bottom:1px solid #eee;text-align:left}}th{{position:sticky;top:0;background:#eee9da}}.fail{{color:#a02d2d}}.pass{{color:#0b6b51}}article h3{{margin-top:0;display:flex;justify-content:space-between}}code{{white-space:pre-wrap}}@media(max-width:700px){{.kpis{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main><header><h1>AI Setup Chat Evaluation</h1><p>Evidence-backed AI-vs-AI quality and reliability report</p><h2 class="{'pass' if summary['release_gate']=='PASS' else 'fail'}">Release gate: {summary['release_gate']}</h2></header><section class="kpis"><div class="kpi"><b>{summary['cases']}</b>Cases</div><div class="kpi"><b>{summary['pass_rate']:.1%}</b>Pass rate</div><div class="kpi"><b>{summary['average_score']:.3f}</b>Quality</div><div class="kpi"><b>${summary['total_test_cost_usd']:.4f}</b>Measured cost</div></section><h2>Topic results</h2><div style="overflow:auto;max-height:650px"><table><thead><tr><th>Topic</th><th>Severity</th><th>Cases</th><th>Pass</th><th>Score</th><th>Criteria</th><th>Latency</th></tr></thead><tbody>{topic_cards}</tbody></table></div><h2>Failures and fixes with proof</h2>{failure_cards}</main></body></html>"""
    (run_dir / "report.html").write_text(page, encoding="utf-8")
    return summary
