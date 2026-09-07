from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.schemas.strategy import ConditionRule, StrategyDefinition
from ai_market_monitor.services.interfaces import Candle
from tests.factories import load_strategy
from tests.integration.test_dashboard_api import _signup

CASES = [
    ("min_body_percent", "belt_hold_bullish", 60, [(100, 110, 100, 103)]),
    ("max_body_percent", "doji", 10, [(104, 110, 100, 107)]),
    ("wick_ratio", "hammer", 4, [(107, 110, 100, 109)]),
    (
        "trend_context_required",
        "bullish_engulfing",
        True,
        [(90 + i, 92 + i, 89 + i, 91 + i) for i in range(6)]
        + [(100, 101, 98, 99), (98, 102, 97, 101)],
    ),
    ("confirmation_required", "green_candle", True, [(103, 104, 99, 100), (100, 104, 99, 103)]),
]


@pytest.mark.parametrize("name,key,changed,ohlc", CASES, ids=[row[0] for row in CASES])
async def test_published_candle_input_survives_save_edit_reload_and_changes_evaluation(
    test_context,
    name,
    key,
    changed,
    ohlc,
):
    await _signup(test_context, f"{name}@example.com")
    client = test_context["client"]
    response = await client.get("/api/v1/dashboard/capabilities")
    assert response.status_code == 200
    item = next(item for item in response.json()["items"] if item["key"] == key)
    template = deepcopy(item["condition_template"])
    if name == "trend_context_required":
        # Trend context is directional; the published neutral default imposes no trend.
        template["left"]["parameters"]["direction"] = "bullish"
        template["resolved_parameters"]["direction"] = "bullish"
    candles = [
        Candle(
            timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=15 * i),
            open=o,
            high=h,
            low=low,
            close=c,
            volume=1000,
        )
        for i, (o, h, low, c) in enumerate(ohlc)
    ]
    baseline = ConditionRule.model_validate(template)
    assert StrategyRuleEngine._candle_pattern(baseline.left, candles) is True
    template["left"]["parameters"][name] = changed
    template["resolved_parameters"][name] = changed
    definition = load_strategy().model_dump(mode="json")
    definition["conditions"]["children"] = [template]
    saved = await client.post("/api/v1/dashboard/strategies", json={"definition": definition})
    assert saved.status_code == 201, saved.text
    strategy_id = saved.json()["strategy"]["id"]
    versions_url = f"/api/v1/dashboard/strategies/{strategy_id}/versions"

    async def reload():
        response = await client.get(versions_url)
        assert response.status_code == 200
        persisted = StrategyDefinition.model_validate(response.json()["items"][0]["schema_json"])
        rule = persisted.conditions.children[0]
        assert rule.left.parameters[name] == changed
        assert rule.resolved_parameters[name] == changed
        # The production operand evaluator dispatches to the real detector. Each
        # synthetic history passes at the default and fails at the persisted value.
        assert StrategyRuleEngine._candle_pattern(rule.left, candles) is False
        return persisted

    persisted = await reload()
    persisted.description = "Synthetic edit retaining the chosen candle setting."
    edited = await client.post(versions_url, json={"definition": persisted.model_dump(mode="json")})
    assert edited.status_code == 201, edited.text
    reloaded = await reload()
    assert reloaded.description == persisted.description
