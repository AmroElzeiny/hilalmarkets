from datetime import UTC, datetime, timedelta

from ai_market_monitor.db.models.enums import ConditionType
from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.schemas.strategy import (
    Comparator,
    ConditionGroup,
    ConditionRule,
    Operand,
    OperandKind,
)
from ai_market_monitor.services.interfaces import Candle
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter
from tests.factories import load_strategy, market


def _guided(text: str, timeframe: str = "15m") -> GuidedSetupRequest:
    return GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe=timeframe,
        setup_mode="free_text",
        setup_text=text,
        trigger_mode="candle_close",
        delivery_channels=["web"],
    )


async def test_previous_green_daily_candle_is_a_deterministic_trigger():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided("bring me all symbols with previous green daily candle")
    )
    start = datetime(2026, 4, 1, tzinfo=UTC)
    candles = [
        Candle(
            timestamp=start + timedelta(days=index),
            open=100,
            high=102,
            low=98,
            close=101,
            volume=1000,
            is_closed=True,
        )
        for index in range(60)
    ]
    candles[-1] = Candle(
        timestamp=candles[-1].timestamp,
        open=101,
        high=102,
        low=98,
        close=99,
        volume=1000,
        is_closed=True,
    )

    result = StrategyRuleEngine().evaluate(
        preview.strategy,
        market(),
        {"1d": candles},
        evaluation_time=candles[-1].timestamp,
        strategy_version="finder-test",
    )

    assert result.outcome.value == "confirmed"
    assert result.conditions[0].actual_value is True


async def test_historical_candle_move_searches_the_window_not_only_latest_candle():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided(
            "find me symbols that had a 1 minute candle that had a value of 1% over the past week",
            timeframe="1m",
        )
    )
    start = datetime(2026, 6, 20, tzinfo=UTC)
    candles = [
        Candle(
            timestamp=start + timedelta(minutes=index),
            open=100,
            high=100.2,
            low=99.8,
            close=100,
            volume=1000,
            is_closed=True,
        )
        for index in range(60)
    ]
    candles[25] = Candle(
        timestamp=candles[25].timestamp,
        open=100,
        high=102.5,
        low=99.8,
        close=102,
        volume=1000,
        is_closed=True,
    )

    result = StrategyRuleEngine().evaluate(
        preview.strategy,
        market(),
        {"1m": candles},
        evaluation_time=candles[-1].timestamp,
        strategy_version="finder-test",
    )

    assert result.outcome.value == "confirmed"
    assert result.conditions[0].actual_value is True


async def test_not_doji_prompt_uses_is_false_candle_pattern():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided("find symbols where the daily candle is not doji", timeframe="1d")
    )
    start = datetime(2026, 6, 1, tzinfo=UTC)
    candles = [
        Candle(
            timestamp=start + timedelta(days=index),
            open=100,
            high=112,
            low=98,
            close=110,
            volume=1000,
            is_closed=True,
        )
        for index in range(60)
    ]

    result = StrategyRuleEngine().evaluate(
        preview.strategy,
        market(),
        {"1d": candles},
        evaluation_time=candles[-1].timestamp,
        strategy_version="finder-test",
    )

    assert preview.strategy.conditions.children[0].comparator == Comparator.IS_FALSE
    assert result.outcome.value == "confirmed"
    assert result.conditions[0].actual_value is False


async def test_previous_daily_doji_is_executable():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided("bring me symbols with previous daily candle doji")
    )
    start = datetime(2026, 6, 1, tzinfo=UTC)
    candles = [
        Candle(
            timestamp=start + timedelta(days=index),
            open=100,
            high=112,
            low=98,
            close=108,
            volume=1000,
            is_closed=True,
        )
        for index in range(60)
    ]
    candles[-2] = Candle(
        timestamp=candles[-2].timestamp,
        open=100,
        high=112,
        low=88,
        close=100.4,
        volume=1000,
        is_closed=True,
    )

    result = StrategyRuleEngine().evaluate(
        preview.strategy,
        market(),
        {"1d": candles},
        evaluation_time=candles[-1].timestamp,
        strategy_version="finder-test",
    )

    assert result.outcome.value == "confirmed"
    assert result.conditions[0].actual_value is True


async def test_consecutive_red_daily_candles_are_executable():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided("find symbols with 5 days in a row daily candles red")
    )
    start = datetime(2026, 6, 1, tzinfo=UTC)
    candles = [
        Candle(
            timestamp=start + timedelta(days=index),
            open=105,
            high=106,
            low=98,
            close=100,
            volume=1000,
            is_closed=True,
        )
        for index in range(60)
    ]

    result = StrategyRuleEngine().evaluate(
        preview.strategy,
        market(),
        {"1d": candles},
        evaluation_time=candles[-1].timestamp,
        strategy_version="finder-test",
    )

    assert result.outcome.value == "confirmed"
    assert result.conditions[0].actual_value == 1.0


async def test_absence_of_hammer_in_last_sixty_one_minute_candles_is_executable():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided(
            "find symbols that did NOT have any hummer candles on the "
            "1-minute timeframe in the last 60 minutes",
            timeframe="1m",
        )
    )
    start = datetime(2026, 6, 1, tzinfo=UTC)
    candles = [
        Candle(
            timestamp=start + timedelta(minutes=index),
            open=100,
            high=101,
            low=99,
            close=100.4,
            volume=1000,
            is_closed=True,
        )
        for index in range(90)
    ]

    result = StrategyRuleEngine().evaluate(
        preview.strategy,
        market(),
        {"1m": candles},
        evaluation_time=candles[-1].timestamp,
        strategy_version="finder-test",
    )

    assert result.outcome.value == "confirmed"
    assert result.conditions[0].actual_value is False


def test_time_window_market_metric_from_builder_is_executable():
    strategy = load_strategy().model_copy(deep=True)
    strategy.universe.min_historical_candles = 10
    strategy.supporting_timeframes = []
    strategy.conditions = ConditionGroup(
        key="entry_conditions",
        operator="and",
        children=[
            ConditionRule(
                key="time_window",
                label="Evaluation inside selected window",
                condition_type=ConditionType.MARKET_FILTER,
                timeframe="15m",
                left=Operand(
                    kind=OperandKind.MARKET_METRIC,
                    name="time_window",
                    parameters={"timezone": "UTC", "start_hour": 12, "end_hour": 14},
                ),
                comparator=Comparator.IS_TRUE,
                required_data=["candle_timestamp"],
            )
        ],
    )
    start = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    candles = [
        Candle(
            timestamp=start + timedelta(minutes=15 * index),
            open=100,
            high=101,
            low=99,
            close=100.5,
            volume=1000,
            is_closed=True,
        )
        for index in range(50)
    ]
    candles[-1] = Candle(
        timestamp=datetime(2026, 6, 1, 12, 45, tzinfo=UTC),
        open=100,
        high=101,
        low=99,
        close=100.5,
        volume=1000,
        is_closed=True,
    )

    result = StrategyRuleEngine().evaluate(
        strategy,
        market(),
        {"15m": candles},
        evaluation_time=candles[-1].timestamp,
        strategy_version="builder-time-window",
    )

    assert result.outcome.value == "confirmed"
    assert result.conditions[0].actual_value is True
