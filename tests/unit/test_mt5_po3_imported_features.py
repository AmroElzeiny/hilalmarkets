from datetime import UTC, datetime, timedelta

import pytest

from ai_market_monitor.engine.indicators import IndicatorRegistry
from ai_market_monitor.engine.price_action import evaluate_price_action
from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.services.interfaces import Candle
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter


def _candle(
    index: int,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100,
) -> Candle:
    return Candle(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        is_closed=True,
    )


def _po3_bullish_sequence() -> list[Candle]:
    return [
        _candle(0, open_price=100.0, high=101.0, low=99.0, close=100.2),
        _candle(1, open_price=100.1, high=101.2, low=99.2, close=100.4),
        _candle(2, open_price=100.3, high=100.9, low=99.1, close=100.0),
        _candle(3, open_price=100.0, high=101.4, low=99.3, close=100.8),
        _candle(4, open_price=100.8, high=101.1, low=99.4, close=100.1),
        _candle(5, open_price=99.7, high=100.2, low=98.7, close=99.6, volume=120),
        _candle(6, open_price=99.7, high=102.6, low=99.6, close=102.2, volume=220),
        _candle(7, open_price=102.1, high=103.5, low=101.8, close=103.1, volume=180),
    ]


def test_mt5_po3_sweep_displacement_structure_is_deterministic():
    candles = _po3_bullish_sequence()

    assert evaluate_price_action(
        "po3_dealing_range_sweep_bullish",
        candles[:6],
        {"lookback": 5, "dealing_range_lookback": 5, "max_dealing_range_percent": 3.0},
    )
    assert evaluate_price_action(
        "po3_sweep_displacement_bullish",
        candles,
        {"lookback": 5, "dealing_range_lookback": 5, "displacement_search_bars": 5},
    )
    assert evaluate_price_action(
        "po3_sweep_displacement_structure_bullish",
        candles,
        {
            "dealing_range_lookback": 5,
            "lookback": 5,
            "displacement_search_bars": 5,
            "structure_swing_span": 1,
        },
    )


def test_mt5_fvg_state_tracks_open_touched_and_filled_states():
    virgin = [
        _candle(0, open_price=9.5, high=10.0, low=9.0, close=9.8),
        _candle(1, open_price=10.1, high=10.9, low=9.9, close=10.7),
        _candle(2, open_price=11.2, high=12.0, low=11.0, close=11.8),
        _candle(3, open_price=12.0, high=12.2, low=11.3, close=11.9),
    ]
    touched = [
        *virgin[:-1],
        _candle(3, open_price=11.8, high=12.0, low=10.7, close=11.4),
    ]
    filled = [
        *virgin[:-1],
        _candle(3, open_price=11.8, high=12.0, low=10.0, close=10.4),
    ]

    assert evaluate_price_action("fvg_still_open_bullish", virgin, {"lookback": 2})
    assert evaluate_price_action("fvg_virgin", virgin, {"lookback": 2})
    assert evaluate_price_action("fvg_touched", touched, {"lookback": 2})
    assert evaluate_price_action("fvg_fully_mitigated", filled, {"lookback": 2})
    assert not evaluate_price_action("fvg_still_open_bullish", filled, {"lookback": 2})


def test_mt5_indicator_adapters_are_registered_and_deterministic():
    candles = [
        _candle(
            index,
            open_price=100 + index * 0.2,
            high=101 + index * 0.25,
            low=99 + index * 0.15,
            close=100.3 + index * 0.22,
            volume=1000 + index * 10,
        )
        for index in range(80)
    ]
    registry = IndicatorRegistry()

    assert registry.supports("trend_strength")
    assert registry.supports("expansion_ratio")
    assert registry.supports("anchored_vwap")
    assert 0 <= registry.calculate("trend_strength", candles, period=50) <= 1
    assert registry.calculate("expansion_ratio", candles, short_period=14, long_period=50) > 0
    assert registry.calculate("anchored_vwap", candles, anchor_bars=50) > 0


async def test_prompt_vocabulary_discovers_imported_mt5_features():
    preview = await RuleBasedStrategyInterpreter().interpret(
        GuidedSetupRequest(
            exchange="binance",
            quote_currency="USDT",
            timeframe="15m",
            setup_mode="free_text",
            setup_text=(
                "Find bullish PO3 with an open FVG and strong trend strength "
                "during New York session"
            ),
            trigger_mode="candle_close",
            delivery_channels=["web"],
        )
    )

    operands = {
        condition.left.name
        for condition in preview.strategy.conditions.children
        if condition.left.name
    }
    assert preview.activation_blocked is False
    assert {
        "po3_sweep_displacement_structure_bullish",
        "fvg_still_open",
        "trend_strength",
        "time_window",
    }.issubset(operands)
