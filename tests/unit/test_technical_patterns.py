from dataclasses import replace
from datetime import UTC, datetime, timedelta

from ai_market_monitor.engine.capabilities import capability_by_key
from ai_market_monitor.engine.price_action import evaluate_price_action, supports_price_action
from ai_market_monitor.services.interfaces import Candle


def _candles(values: list[float]) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(
            timestamp=start + timedelta(minutes=index),
            open=value - 0.1,
            high=value + 0.4,
            low=value - 0.4,
            close=value,
            volume=1000,
            is_closed=True,
        )
        for index, value in enumerate(values)
    ]


def test_head_and_shoulders_structure_and_neckline_break_are_separate_states():
    forming = _candles(
        [95, 96, 98, 101, 105, 101, 97, 103, 112, 104, 98, 102, 105, 102, 100, 99]
    )
    confirmed = [*forming[:-1], replace(forming[-1], close=96, low=95.6)]
    parameters = {
        "lookback": 30,
        "pivot_bars": 1,
        "shoulder_tolerance_percent": 2,
        "head_prominence_percent": 3,
        "maximum_spacing_ratio": 2,
        "breakout_buffer_percent": 0,
    }

    assert evaluate_price_action("head_and_shoulders_formed", forming, parameters)
    assert not evaluate_price_action(
        "head_and_shoulders_neckline_break", forming, parameters
    )
    assert evaluate_price_action(
        "head_and_shoulders_neckline_break", confirmed, parameters
    )


def test_double_bottom_requires_structure_and_close_above_neckline():
    candles = _candles([105, 103, 100, 96, 92, 97, 102, 98, 93, 97, 101, 104])
    assert evaluate_price_action(
        "double_bottom_neckline_break",
        candles,
        {
            "lookback": 30,
            "pivot_bars": 1,
            "level_tolerance_percent": 2,
            "minimum_depth_percent": 2,
            "breakout_buffer_percent": 0,
        },
    )


def test_head_and_shoulders_ignores_adjacent_plateau_pivots():
    candles = _candles([95, 103, 103, 96, 110, 110, 96, 103, 103, 95, 94, 93])

    assert not evaluate_price_action(
        "head_and_shoulders_formed",
        candles,
        {"lookback": 30, "pivot_bars": 1},
    )


def test_technical_pattern_catalog_is_executable_and_versioned():
    expected = {
        "head_and_shoulders_formed",
        "head_and_shoulders_neckline_break",
        "inverse_head_and_shoulders_formed",
        "inverse_head_and_shoulders_neckline_break",
        "double_top_neckline_break",
        "double_bottom_neckline_break",
        "ascending_triangle_breakout",
        "descending_triangle_breakdown",
        "symmetrical_triangle_breakout",
        "symmetrical_triangle_breakdown",
    }
    registry = capability_by_key()
    assert expected.issubset(registry)
    for key in expected:
        assert registry[key].availability == "available"
        assert registry[key].capability_version
        assert supports_price_action(registry[key].operand_name)
