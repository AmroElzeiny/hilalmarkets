from __future__ import annotations

import json
from pathlib import Path

from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2, StrategyPatch
from ai_market_monitor.services.agent_tools import strict_json_schema

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tests" / "evaluator" / "contracts"


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    schemas = {
        "strategy_draft_v2.schema.json": StrategyDraftV2.model_json_schema(),
        "strategy_patch.schema.json": strict_json_schema(StrategyPatch),
    }
    for filename, schema in schemas.items():
        (TARGET / filename).write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(f"Exported {len(schemas)} launch schemas to {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

