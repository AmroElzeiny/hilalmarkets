from __future__ import annotations

import json
from pathlib import Path

from ai_market_monitor.engine.capability_compatibility import compatibility_report
from ai_market_monitor.engine.capability_index import get_capability_index


def capability_quality_snapshot(
    *,
    report_path: Path = Path("reports/capability_prompt_coverage.json"),
) -> dict:
    index = get_capability_index()
    retrieval = {"passed": 0, "total": 0, "percent": None, "current": False}
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        report = {}
    if report.get("registry_hash") == index.registry_hash:
        retrieval = {
            "passed": int(report.get("recalled") or 0),
            "total": int(report.get("prompt_variants") or 0),
            "percent": float(report.get("recall_percent") or 0),
            "current": True,
        }
    executable = [row for row in compatibility_report() if row.availability == "available"]
    evaluator_passed = sum(
        row.template_valid and row.evaluator_supported for row in executable
    )
    return {
        "registry_hash": index.registry_hash,
        "retrieval": retrieval,
        "evaluator": {
            "passed": evaluator_passed,
            "total": len(executable),
            "percent": _percent(evaluator_passed, len(executable)),
        },
    }


def _percent(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100, 2) if denominator else None
