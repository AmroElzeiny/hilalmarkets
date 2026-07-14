from copy import deepcopy

from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.verified_strategy import semantic_strategy_diff
from tests.factories import load_strategy


def test_semantic_diff_keeps_repeated_capability_instances_separate() -> None:
    before_payload = load_strategy().model_dump(mode="json")
    original = before_payload["conditions"]["children"][2]
    original["capability_key"] = "relative_volume"
    original["capability_version"] = "1.0.0"
    duplicate = deepcopy(original)
    duplicate["key"] = "relative_volume_1h"
    duplicate["timeframe"] = "1h"
    before_payload["conditions"]["children"].append(duplicate)

    after_payload = deepcopy(before_payload)
    after_payload["conditions"]["children"][2]["right"]["value"] = 1.8

    diff = semantic_strategy_diff(
        StrategyDefinition.model_validate(before_payload),
        StrategyDefinition.model_validate(after_payload),
    )

    threshold_changes = [item for item in diff if item["path"].endswith("threshold")]
    assert threshold_changes == [
        {
            "path": "Volume at least 1.5x average.threshold",
            "operation": "modified",
            "before": 1.5,
            "after": 1.8,
        }
    ]
