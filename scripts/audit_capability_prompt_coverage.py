"""Measure whether supported prompt language reaches the AI rerank shortlist."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from ai_market_monitor.engine.capabilities import executable_capabilities
from ai_market_monitor.engine.capability_index import get_capability_index
from ai_market_monitor.engine.capability_resolver import CapabilityResolver

FRAMES = (
    "I want {condition}",
    "Bring me coins where {condition}",
    "Check whether {condition}",
)


def audit(*, shortlist_size: int = 8) -> dict:
    resolver = CapabilityResolver()
    total = 0
    recalled = 0
    misses = []
    for capability in executable_capabilities():
        samples = list(dict.fromkeys([*capability.aliases[:1], *capability.intent_examples[:1]]))
        for sample in samples:
            for frame in FRAMES:
                prompt = frame.format(condition=sample)
                total += 1
                report = resolver.resolve_prompt(prompt)
                candidate_keys = {
                    candidate.capability_key
                    for fragment in report.fragments
                    for candidate in fragment.candidates[:shortlist_size]
                }
                if capability.key in candidate_keys:
                    recalled += 1
                else:
                    misses.append(
                        {
                            "capability_key": capability.key,
                            "prompt": prompt,
                            "candidate_keys": sorted(candidate_keys),
                        }
                    )
    recall = 100.0 if total == 0 else recalled / total * 100
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "registry_hash": get_capability_index().registry_hash,
        "metric": "supported_capability_candidate_recall",
        "shortlist_size": shortlist_size,
        "prompt_variants": total,
        "recalled": recalled,
        "missed": len(misses),
        "recall_percent": round(recall, 2),
        "misses": misses,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minimum", type=float, default=95.0)
    parser.add_argument("--shortlist-size", type=int, default=8)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/capability_prompt_coverage.json"),
    )
    args = parser.parse_args()
    result = audit(shortlist_size=args.shortlist_size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        f"Capability candidate recall: {result['recalled']}/{result['prompt_variants']} "
        f"({result['recall_percent']:.2f}%)"
    )
    print(f"Report: {args.output.resolve()}")
    return 0 if result["recall_percent"] >= args.minimum else 1


if __name__ == "__main__":
    raise SystemExit(main())
