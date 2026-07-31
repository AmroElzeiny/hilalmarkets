from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_market_monitor.schemas.setup_chat_evaluation import SetupChatEvaluationContract
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2, StrategyPatch

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIR = ROOT / "tests" / "evaluator" / "contracts"

FIELD_MAP: dict[str, str | dict[str, str]] = {
    "universe": "strategy.universe",
    "symbols": "symbols",
    "symbol": {"path": "symbols", "match": "contains"},
    "exclusions": "exclusions",
    "excluded_symbol": {"path": "exclusions", "match": "contains"},
    "direction": "direction",
    "timeframes": "timeframes",
    "timeframe": {"path": "timeframes", "match": "contains"},
    "context_timeframe": {"path": "timeframes", "match": "contains"},
    "operators": "operators",
    "operator": {"path": "operators", "match": "contains"},
    "thresholds": "thresholds",
    "threshold_percent": {"path": "thresholds", "match": "contains_numeric"},
    "nested_groups": "condition_groups",
    "condition_groups": "condition_groups",
    "filters": "filters",
    "alerts": "alerts",
    "assumptions": "assumptions",
    "confidence": "confidence",
    "unsupported_capabilities": "unsupported_capabilities",
    "provider_required_capabilities": "provider_required_capabilities",
    "approval_state": "approval",
    "lifecycle_state": "approval.lifecycle_state",
    "turn_complete": "approval.terminal",
    "requires_explicit_approval": "requires_explicit_approval",
    "must_not_assign_sharia_status": "must_not_assign_sharia_status",
    "strategy_version": "approval.strategy_version_number",
    "version_hash": "approval.immutable_version_hash",
    "canonical_hash": "canonical_hash",
    "canvas_nodes": "canvas.nodes",
    "canvas_groups": "canvas.groups",
    "canvas_edges": "canvas.edges",
}


def artifacts() -> dict[Path, dict[str, Any]]:
    return {
        CONTRACT_DIR / "strategy_definition.schema.json": (StrategyDefinition.model_json_schema()),
        CONTRACT_DIR / "strategy_draft_v2.schema.json": StrategyDraftV2.model_json_schema(),
        CONTRACT_DIR / "strategy_patch.schema.json": StrategyPatch.model_json_schema(),
        CONTRACT_DIR / "setup_chat_evaluation_contract.schema.json": (
            SetupChatEvaluationContract.model_json_schema()
        ),
        CONTRACT_DIR / "field_map.json": FIELD_MAP,
    }


def render(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export evaluator contracts from production Pydantic models."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if committed evaluator contracts do not match production models.",
    )
    args = parser.parse_args()
    stale: list[str] = []
    for path, value in artifacts().items():
        expected = render(value)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != expected:
                stale.append(str(path.relative_to(ROOT)))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8")
    if stale:
        print("Stale evaluator contracts:")
        for item in stale:
            print(f"- {item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
