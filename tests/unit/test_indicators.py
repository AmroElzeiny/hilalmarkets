from datetime import UTC, datetime, timedelta
from math import isfinite

import pytest

from ai_market_monitor.engine.indicators import IndicatorRegistry, IndicatorWarmupError
from ai_market_monitor.services.interfaces import Candle
from tests.factories import candles


def test_canonical_indicator_registry_supports_required_indicators():
    history = candles(
        80,
        start=datetime(2026, 1, 1, tzinfo=UTC),
        minutes=15,
        close=100,
        volume=1_000,
    )
    registry = IndicatorRegistry()
    calculations = {
        "sma": {"period": 20},
        "ema": {"period": 20},
        "ema_slope": {"period": 20},
        "sma_slope": {"period": 20},
        "moving_average_distance_percent": {"period": 20},
        "rsi": {"period": 14},
        "atr": {"period": 14},
        "atr_percent": {"period": 14},
        "average_volume": {"period": 20},
        "volume_ratio": {"period": 20},
        "relative_volume_slope": {"period": 20},
        "macd": {"component": "histogram"},
        "macd_histogram_delta": {},
        "bollinger_band": {"period": 20, "component": "upper"},
        "bollinger_bandwidth_percent": {"period": 20},
        "bollinger_bandwidth_delta": {"period": 20},
        "stochastic": {"period": 14, "component": "d"},
        "adx": {"period": 14},
        "vwap": {"period": 20},
        "vwap_deviation_percent": {"period": 20},
        "range_ratio": {"period": 20},
        "pullback_depth_percent": {"lookback": 20},
    }
    for name, parameters in calculations.items():
        assert registry.supports(name)
        assert isinstance(registry.calculate(name, history, **parameters), float)


def test_indicator_warmup_fails_explicitly():
    history = candles(
        5,
        start=datetime(2026, 1, 1, tzinfo=UTC),
        minutes=15,
    )
    with pytest.raises(IndicatorWarmupError, match="requires"):
        IndicatorRegistry().calculate("macd", history)


def test_extended_indicator_registry_calculates_every_new_family():
    start = datetime(2025, 1, 1, tzinfo=UTC)
    price = 100.0
    history: list[Candle] = []
    for index in range(260):
        price += (0.18 if index % 11 < 7 else -0.11) + ((index % 5) - 2) * 0.025
        history.append(
            Candle(
                timestamp=start + timedelta(minutes=index * 15),
                open=price - 0.25,
                high=price + 0.9 + (index % 3) * 0.1,
                low=price - 0.8,
                close=price,
                volume=1000 + (index % 13) * 47,
                is_closed=True,
            )
        )
    registry = IndicatorRegistry()
    names = {
        "stochastic_rsi",
        "money_flow_index",
        "commodity_channel_index",
        "williams_percent_r",
        "rate_of_change",
        "momentum",
        "true_strength_index",
        "ultimate_oscillator",
        "relative_vigor_index",
        "connors_rsi",
        "weighted_moving_average",
        "hull_moving_average",
        "double_exponential_moving_average",
        "triple_exponential_moving_average",
        "kaufman_adaptive_moving_average",
        "volume_weighted_moving_average",
        "linear_regression_moving_average",
        "zero_lag_ema",
        "moving_average_ribbon",
        "ichimoku_cloud",
        "supertrend",
        "parabolic_sar",
        "aroon",
        "directional_movement",
        "elder_impulse",
        "keltner_channel",
        "donchian_channel",
        "bollinger_percent_b",
        "squeeze_detection",
    }
    for name in names:
        assert registry.supports(name)
        assert isfinite(registry.calculate(name, history))
