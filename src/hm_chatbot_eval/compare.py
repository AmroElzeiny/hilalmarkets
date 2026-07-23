from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any


def load_cases(run_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    result = {}
    with (run_dir / "cases.jsonl").open(encoding="utf-8") as f:
        for line in f:
            case = json.loads(line)
            key = (case["scenario"]["id"], case["target_kind"])
            result[key] = case
    return result


def compare_runs(a: Path, b: Path) -> dict[str, Any]:
    ca, cb = load_cases(a), load_cases(b)
    common = sorted(set(ca) & set(cb))
    rows = []
    for key in common:
        x, y = ca[key], cb[key]
        sx = (x.get("judge") or {}).get(
            "score", x.get("deterministic_metrics", {}).get("schema_valid", 0)
        )
        sy = (y.get("judge") or {}).get(
            "score", y.get("deterministic_metrics", {}).get("schema_valid", 0)
        )
        rows.append(
            {
                "scenario_id": key[0],
                "target_kind": key[1],
                "pass_flip": x["passed"] != y["passed"],
                "score_a": sx,
                "score_b": sy,
                "score_delta": sy - sx,
                "structured_changed": x.get("structured_hash") != y.get("structured_hash"),
                "latency_delta_ms": y.get("total_latency_ms", 0) - x.get("total_latency_ms", 0),
            }
        )
    return {
        "run_a": a.name,
        "run_b": b.name,
        "common_cases": len(rows),
        "pass_flip_rate": sum(r["pass_flip"] for r in rows) / max(1, len(rows)),
        "semantic_score_delta_mean": mean(r["score_delta"] for r in rows) if rows else 0,
        "semantic_score_delta_abs": mean(abs(r["score_delta"]) for r in rows) if rows else 0,
        "structured_change_rate": sum(r["structured_changed"] for r in rows) / max(1, len(rows)),
        "regressions": sorted(
            [r for r in rows if r["score_delta"] < -0.05 or r["pass_flip"]],
            key=lambda r: r["score_delta"],
        ),
    }
