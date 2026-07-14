import pytest
from pydantic import ValidationError

from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.schemas.strategy import ConditionGroup, StrategyDefinition
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter


def _guided(text: str) -> GuidedSetupRequest:
    return GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        setup_text=text,
        trigger_mode="candle_close",
        delivery_channels=["web"],
    )


async def test_interpreter_builds_multi_timeframe_rules_without_inventing_values():
    guided = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        setup_text=(
            "Find bullish liquidity sweeps. Price above the four-hour 200 EMA. "
            "Volume at least 1.5 times average."
        ),
        trigger_mode="candle_close",
        maximum_stop_percent=2,
        minimum_reward_to_risk=2.5,
        delivery_channels=["telegram"],
    )
    preview = await RuleBasedStrategyInterpreter().interpret(guided)
    assert preview.activation_blocked is False
    assert preview.strategy.supporting_timeframes == ["4h"]
    assert len(preview.strategy.conditions.children) == 3
    assert preview.assumptions


async def test_unknown_language_is_explicitly_unsupported():
    guided = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        setup_text="Only buy when the chart feels unusually optimistic",
        trigger_mode="candle_close",
        maximum_stop_percent=2,
        minimum_reward_to_risk=2.5,
        delivery_channels=["telegram"],
    )
    preview = await RuleBasedStrategyInterpreter().interpret(guided)
    assert preview.activation_blocked is True
    assert preview.unsupported_conditions[0].code == "no_supported_monitor_condition"


async def test_interpreter_supports_six_month_high_breakout_language():
    guided = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        setup_text="prices breaking all time high in the last 6 months",
        trigger_mode="candle_close",
        maximum_stop_percent=2,
        minimum_reward_to_risk=2.5,
        delivery_channels=["telegram"],
    )

    preview = await RuleBasedStrategyInterpreter().interpret(guided)

    assert preview.activation_blocked is False
    child = preview.strategy.conditions.children[0]
    assert child.key.startswith("breakout_")
    assert "6-month" in child.label


async def test_interpreter_supports_pdl_sweep_shorthand_and_common_sweep_spelling():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided("bring coins which sweeped the PDL through")
    )

    assert preview.activation_blocked is False
    condition = preview.strategy.conditions.children[0]
    assert condition.left.name == "daily_low_swept"
    assert condition.left.parameters["timezone"] == "UTC"
    assert condition.required is True
    assert any("PDL is interpreted" in assumption for assumption in preview.assumptions)


async def test_interpreter_treats_plain_price_threshold_as_quick_scan_condition():
    guided = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        setup_text="bring me symbols that have price above 1000$",
        trigger_mode="candle_close",
        maximum_stop_percent=None,
        minimum_reward_to_risk=None,
        delivery_channels=["telegram"],
    )

    preview = await RuleBasedStrategyInterpreter().interpret(guided)

    assert preview.activation_blocked is False
    condition = preview.strategy.conditions.children[0]
    assert condition.left.field == "close"
    assert condition.right.value == 1000
    assert condition.comparator.value == "gt"
    assert preview.strategy.risk.enabled is False


async def test_interpreter_treats_growth_percent_as_percent_change_not_price():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided("find any symbol that grew 5% or more today")
    )

    assert preview.activation_blocked is False
    condition = preview.strategy.conditions.children[0]
    assert condition.left.name == "percent_change_up"
    assert condition.left.parameters["threshold_percent"] == 5
    assert condition.left.parameters["lookback"] == 96
    assert condition.comparator.value == "is_true"


async def test_interpreter_uses_exact_month_lookback_for_breakout():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided("prices breaking the all time high over the last month")
    )

    condition = preview.strategy.conditions.children[0]
    assert condition.left.name == "higher_high"
    assert condition.left.parameters["lookback"] == 2880
    assert "previous 2880 closed candles" in preview.assumptions[0]


async def test_interpreter_supports_previous_candle_state_and_historical_event_window():
    previous = await RuleBasedStrategyInterpreter().interpret(
        _guided("bring me all symbols with previous green daily candle")
    )
    previous_condition = previous.strategy.conditions.children[0]
    assert previous_condition.left.name == "green_candle"
    assert previous_condition.timeframe == "1d"
    assert previous_condition.left.parameters["offset"] == 1

    historical = await RuleBasedStrategyInterpreter().interpret(
        _guided(
            "find me symbols that had a 1 minute candle that had a value of 1% over the past week"
        )
    )
    event_condition = historical.strategy.conditions.children[0]
    assert event_condition.left.name == "candle_change_percent"
    assert event_condition.timeframe == "1m"
    assert event_condition.left.parameters["threshold_percent"] == 1
    assert event_condition.left.parameters["search_lookback"] == 10080

    historical_price = await RuleBasedStrategyInterpreter().interpret(
        _guided("find symbols where price was above 1000 dollars in 2019")
    )
    price_condition = historical_price.strategy.conditions.children[0]
    assert price_condition.left.parameters["aggregate"] == "max"
    assert price_condition.left.parameters["search_start"].startswith("2019-01-01")


async def test_interpreter_supports_complex_candle_status_filters():
    previous_doji = await RuleBasedStrategyInterpreter().interpret(
        _guided("bring me symbols with previous daily candle doji")
    )
    previous_condition = previous_doji.strategy.conditions.children[0]
    assert previous_condition.left.name == "doji"
    assert previous_condition.timeframe == "1d"
    assert previous_condition.left.parameters["offset"] == 1

    sequence = await RuleBasedStrategyInterpreter().interpret(
        _guided("find symbols with 5 days in a row daily candles red")
    )
    sequence_condition = sequence.strategy.conditions.children[0]
    assert sequence_condition.left.name == "candle_anatomy"
    assert sequence_condition.left.parameters["component"] == "consecutive_bearish"
    assert sequence_condition.left.parameters["count"] == 5
    assert sequence_condition.timeframe == "1d"

    no_hammer = await RuleBasedStrategyInterpreter().interpret(
        _guided(
            "find symbols that did NOT have any hummer candles on the "
            "1-minute timeframe in the last 60 minutes"
        )
    )
    hammer_condition = no_hammer.strategy.conditions.children[0]
    assert hammer_condition.left.name == "hammer"
    assert hammer_condition.comparator.value == "is_false"
    assert hammer_condition.timeframe == "1m"
    assert hammer_condition.left.parameters["search_lookback"] == 60


async def test_interpreter_reports_mandatory_fragments_it_cannot_convert():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided("find RSI above 50 and only if the chart feels unusually optimistic")
    )

    assert preview.activation_blocked is True
    assert any(
        issue.code == "instruction_not_converted"
        for issue in preview.unsupported_conditions
    )


def test_strategy_rejects_duplicate_condition_keys():
    guided = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="template",
        template_key="trend_pullback",
        trigger_mode="candle_close",
        maximum_stop_percent=2,
        minimum_reward_to_risk=2.5,
        delivery_channels=["telegram"],
    )

    async def build():
        return await RuleBasedStrategyInterpreter().interpret(guided)

    import asyncio

    definition = asyncio.run(build()).strategy
    payload = definition.model_dump(mode="json")
    child = payload["conditions"]["children"][0]
    payload["conditions"] = ConditionGroup(
        key=child["key"], operator="and", children=[child]
    ).model_dump(mode="json")
    with pytest.raises(ValidationError, match="keys must be unique"):
        StrategyDefinition.model_validate(payload)
