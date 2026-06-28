import pytest

from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter


def _guided(text: str) -> GuidedSetupRequest:
    return GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        setup_text=text,
        trigger_mode="candle_close",
        maximum_stop_percent=2,
        minimum_reward_to_risk=2,
        delivery_channels=["telegram"],
    )


def _operand_names(preview) -> set[str]:
    names: set[str] = set()
    for condition in preview.strategy.conditions.children:
        if condition.left.name:
            names.add(condition.left.name)
        if condition.right and condition.right.name:
            names.add(condition.right.name)
    return names


@pytest.mark.parametrize(
    ("prompt", "expected_names"),
    [
        (
            "RSI crosses back above 30 while price is above 4h EMA 200 and volume is at "
            "least 1.5x average",
            {"rsi", "ema", "volume_ratio"},
        ),
        (
            "MACD histogram turns positive with range breakout and close near high",
            {"macd", "breakout_from_consolidation", "strong_close_near_high"},
        ),
        ("VWAP reclaim after pullback and volume spike", {"vwap", "volume_ratio"}),
        (
            "Bollinger squeeze breakout above resistance",
            {"bollinger_squeeze", "breakout_from_consolidation", "price_rejects_resistance"},
        ),
        ("EMA 20 crosses above EMA 50 with EMA slope rising", {"ema", "ema_slope"}),
        (
            "price breaks above the previous 20 day high with volume spike",
            {"higher_high", "volume_ratio"},
        ),
        ("coins decreasing by 3% near midnight", {"percent_change_down", "time_window"}),
        (
            "short resistance rejection with RSI overbought above 70",
            {"rsi", "price_rejects_resistance"},
        ),
        ("low volume pullback to EMA retest", {"pullback_to_ema", "volume_ratio"}),
        (
            "stochastic crosses above 20 after support bounce",
            {"stochastic", "price_bounces_from_support"},
        ),
        (
            "change of character and break of structure",
            {"market_structure_shift_bullish"},
        ),
        (
            "equal highs liquidity pool then sweep highs",
            {"equal_highs_liquidity_pool", "buy_side_liquidity_sweep"},
        ),
    ],
)
async def test_interpreter_recognizes_expanded_prompt_mechanics(prompt, expected_names):
    preview = await RuleBasedStrategyInterpreter().interpret(_guided(prompt))

    assert preview.activation_blocked is False
    assert expected_names.issubset(_operand_names(preview))
    assert preview.raw_metadata["detected_categories"]


async def test_interpreter_keeps_primary_timeframe_and_adds_higher_timeframe_condition():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided("Price is above the four-hour 200 EMA while RSI crosses above 30")
    )

    assert preview.strategy.base_timeframe == "15m"
    assert preview.strategy.supporting_timeframes == ["4h"]
    assert any(
        condition.timeframe == "4h" and condition.right and condition.right.name == "ema"
        for condition in preview.strategy.conditions.children
    )


async def test_interpreter_recognizes_optional_provider_condition_without_blocking():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided("Find breakouts, prefer no meme coins")
    )

    assert preview.activation_blocked is False
    assert any(
        issue.code == "provider_required" and issue.blocking is False
        for issue in preview.unsupported_conditions
    )
    assert "breakout_from_consolidation" in _operand_names(preview)


async def test_interpreter_builds_mandatory_market_cap_provider_condition():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided("Only alert if market cap is above 1B and price breaks out")
    )

    assert preview.activation_blocked is True
    assert any(
        issue.code == "provider_required" and issue.source_fragment == "market cap"
        for issue in preview.unsupported_conditions
    )
    assert "breakout_from_consolidation" in _operand_names(preview)
    assert "close" not in _operand_names(preview)


async def test_interpreter_builds_cross_market_filter_without_guessing_values():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided(
            "Find alts breaking above their 20-day high with volume spike and BTC above 4h EMA 200"
        )
    )

    assert preview.activation_blocked is True
    assert {"higher_high", "volume_ratio"}.issubset(_operand_names(preview))
    btc = next(
        condition
        for condition in preview.strategy.conditions.children
        if condition.left.name == "btc_trend_filter"
    )
    assert btc.timeframe == "4h"
    assert btc.left.parameters["provider"] == "cross_market"
    assert any(
        issue.code == "cross_symbol_context_provider_required"
        for issue in preview.unsupported_conditions
    )
