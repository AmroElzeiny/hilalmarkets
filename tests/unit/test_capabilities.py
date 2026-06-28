from ai_market_monitor.engine.capabilities import (
    capability_registry_payload,
    executable_capabilities,
    unsupported_capabilities,
)


def test_capability_registry_exposes_required_part2_breadth():
    payload = capability_registry_payload()
    keys = {item["key"] for item in payload["items"]}
    aliases = {item["phrase"] for item in payload["aliases"]}
    implemented_keys = {item["key"] for item in payload["items"]}

    assert payload["counts"]["total"] >= 70
    assert payload["counts"]["executable"] >= 50
    assert payload["counts"]["templates"] >= 20
    assert len(executable_capabilities()) == payload["counts"]["executable"]
    assert len(unsupported_capabilities()) == payload["counts"]["recognized_not_executable"]
    assert {
        "percent_change_lookback",
        "time_window",
        "bullish_liquidity_sweep",
        "range_breakout",
        "vwap_reclaim",
        "bollinger_squeeze",
        "strong_close_near_high",
    }.issubset(keys)
    assert {"btc_trend_filter", "market_cap_minimum", "meme_coin_exclusion"}.issubset(
        implemented_keys
    )
    assert payload["unsupported"] == []
    assert {"pump", "dump", "no meme coins", "trend filter"}.issubset(aliases)


def test_capability_registry_is_dashboard_builder_ready():
    payload = capability_registry_payload()

    assert payload["condition_types"]
    assert payload["indicators"]
    assert payload["price_actions"]
    assert payload["candle_patterns"]
    assert payload["builder_defaults"]["indicator"] == "volume_ratio"
    assert any(
        template["key"] == "six_month_high_breakout" for template in payload["strategy_templates"]
    )
