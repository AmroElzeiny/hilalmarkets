import inspect
from collections.abc import Callable
from functools import lru_cache
from math import exp, log, sqrt
from typing import Any
from zoneinfo import ZoneInfo

from ai_market_monitor.services.interfaces import Candle


class IndicatorWarmupError(ValueError):
    pass


class IndicatorDomainError(ValueError):
    """A setting this measure cannot be taken with, whatever the market does.

    Different from a warm-up: waiting for more candles will never help. Raised in plain
    words so what reaches the trader is a sentence and not an exception class name.
    """


#: The smallest window an indicator can be measured over, where that is more than one.
#: Owned beside the measures themselves, because it is the arithmetic in these functions
#: that decides it, and read by the capability registry so the form never offers a window
#: the measure cannot use. ``choppiness_index`` divides by ``log(period)``.
INDICATOR_MINIMUM_PERIOD: dict[str, int] = {"choppiness_index": 2}


#: What a compiled rule carries **about itself**, not about the reading it takes.
#:
#: The compiler puts these beside an operand's real settings so that context conditions
#: and price-action readers — which take a whole dictionary — can see them. An indicator
#: takes named arguments instead, so the same bag made every indicator call fail. They
#: are listed once, here, because this is the only place that has to tell the two apart.
#:
#: ``threshold`` and ``timeframe`` are on this list on purpose. Both are real settings a
#: person chooses, and neither is a setting of the *reading*: the threshold becomes the
#: number on the other side of the comparison, and the timeframe chooses which candles
#: are handed in.
RULE_PARAMETERS: frozenset[str] = frozenset(
    {
        "threshold",
        "timeframe",
        "trigger_timeframe",
        "context_timeframes",
        "confirmation_timeframes",
        "reference_timeframe",
        "reference_definition",
        "comparator",
        "direction",
        "movement_direction",
        "strategy_bias",
        "formula",
        "unit",
        "scale",
        "closed_only",
    }
)


@lru_cache(maxsize=512)
def _accepted_arguments(function: Callable[..., Any]) -> frozenset[str]:
    """The setting names this indicator really takes."""

    parameters = inspect.signature(function).parameters
    return frozenset(
        name
        for name, parameter in parameters.items()
        if parameter.kind
        in {inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    ) - {"candles"}


def _field_values(candles: list[Candle], field: str) -> list[float]:
    return [float(getattr(candle, field)) for candle in candles]


def sma(candles: list[Candle], *, period: int, field: str = "close") -> float:
    if len(candles) < period:
        raise IndicatorWarmupError(f"sma({period}) requires {period} candles")
    values = _field_values(candles[-period:], field)
    return sum(values) / period


def average_volume(candles: list[Candle], *, period: int = 20) -> float:
    if len(candles) < period:
        raise IndicatorWarmupError(f"average_volume({period}) requires {period} candles")
    return sum(candle.volume for candle in candles[-period:]) / period


def ema(candles: list[Candle], *, period: int, field: str = "close") -> float:
    if len(candles) < period:
        raise IndicatorWarmupError(f"ema({period}) requires {period} candles")
    values = _field_values(candles, field)
    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def ema_slope(
    candles: list[Candle],
    *,
    period: int = 20,
    bars: int = 1,
    field: str = "close",
) -> float:
    if len(candles) < period + bars:
        raise IndicatorWarmupError(f"ema_slope({period}) requires {period + bars} candles")
    return ema(candles, period=period, field=field) - ema(
        candles[:-bars], period=period, field=field
    )


def sma_slope(
    candles: list[Candle],
    *,
    period: int = 20,
    bars: int = 1,
    field: str = "close",
) -> float:
    if len(candles) < period + bars:
        raise IndicatorWarmupError(f"sma_slope({period}) requires {period + bars} candles")
    return sma(candles, period=period, field=field) - sma(
        candles[:-bars], period=period, field=field
    )


def moving_average_distance_percent(
    candles: list[Candle],
    *,
    period: int = 20,
    average: str = "ema",
    field: str = "close",
) -> float:
    if not candles:
        raise IndicatorWarmupError("moving_average_distance_percent requires candles")
    moving_average = (
        ema(candles, period=period, field=field)
        if average == "ema"
        else sma(candles, period=period, field=field)
    )
    if moving_average == 0:
        raise IndicatorWarmupError("moving average value is zero")
    return ((candles[-1].close - moving_average) / moving_average) * 100


def _ema_series(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        raise IndicatorWarmupError(f"ema({period}) requires {period} values")
    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period
    series = [result]
    for value in values[period:]:
        result = (value - result) * multiplier + result
        series.append(result)
    return series


def atr(candles: list[Candle], *, period: int = 14) -> float:
    if len(candles) < period + 1:
        raise IndicatorWarmupError(f"atr({period}) requires {period + 1} candles")
    true_ranges: list[float] = []
    for previous, current in zip(candles[-period - 1 : -1], candles[-period:], strict=True):
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return sum(true_ranges) / period


def atr_percent(candles: list[Candle], *, period: int = 14) -> float:
    if not candles:
        raise IndicatorWarmupError("atr_percent requires candles")
    close = candles[-1].close
    if close == 0:
        raise IndicatorWarmupError("atr_percent close is zero")
    return (atr(candles, period=period) / close) * 100


def volume_ratio(candles: list[Candle], *, period: int = 20) -> float:
    if len(candles) < period + 1:
        raise IndicatorWarmupError(f"volume_ratio({period}) requires {period + 1} candles")
    average = sum(candle.volume for candle in candles[-period - 1 : -1]) / period
    if average == 0:
        raise IndicatorWarmupError("volume average is zero")
    return candles[-1].volume / average


def relative_volume_slope(candles: list[Candle], *, period: int = 20, bars: int = 1) -> float:
    if len(candles) < period + bars + 1:
        raise IndicatorWarmupError(
            f"relative_volume_slope({period}) requires {period + bars + 1} candles"
        )
    return volume_ratio(candles, period=period) - volume_ratio(candles[:-bars], period=period)


def rsi(candles: list[Candle], *, period: int = 14, field: str = "close") -> float:
    if len(candles) < period + 1:
        raise IndicatorWarmupError(f"rsi({period}) requires {period + 1} candles")
    values = _field_values(candles[-period - 1 :], field)
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values[:-1], values[1:], strict=True):
        delta = current - previous
        gains.append(max(delta, 0))
        losses.append(abs(min(delta, 0)))
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def macd(
    candles: list[Candle],
    *,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
    field: str = "close",
    component: str = "line",
) -> float:
    required = slow_period + signal_period - 1
    if len(candles) < required:
        raise IndicatorWarmupError(f"macd requires {required} candles")
    values = _field_values(candles, field)
    fast = _ema_series(values, fast_period)
    slow = _ema_series(values, slow_period)
    aligned_fast = fast[-len(slow) :]
    line = [
        fast_value - slow_value for fast_value, slow_value in zip(aligned_fast, slow, strict=True)
    ]
    signal = _ema_series(line, signal_period)
    if component == "line":
        return line[-1]
    if component == "signal":
        return signal[-1]
    if component == "histogram":
        return line[-1] - signal[-1]
    raise KeyError(f"Unsupported MACD component: {component}")


def macd_histogram_delta(
    candles: list[Candle],
    *,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
    field: str = "close",
    bars: int = 1,
) -> float:
    required = slow_period + signal_period - 1 + bars
    if len(candles) < required:
        raise IndicatorWarmupError(f"macd_histogram_delta requires {required} candles")
    current = macd(
        candles,
        fast_period=fast_period,
        slow_period=slow_period,
        signal_period=signal_period,
        field=field,
        component="histogram",
    )
    previous = macd(
        candles[:-bars],
        fast_period=fast_period,
        slow_period=slow_period,
        signal_period=signal_period,
        field=field,
        component="histogram",
    )
    return current - previous


def bollinger_band(
    candles: list[Candle],
    *,
    period: int = 20,
    standard_deviations: float = 2,
    field: str = "close",
    component: str = "middle",
) -> float:
    if len(candles) < period:
        raise IndicatorWarmupError(f"bollinger_band({period}) requires {period} candles")
    values = _field_values(candles[-period:], field)
    middle = sum(values) / period
    deviation = sqrt(sum((value - middle) ** 2 for value in values) / period)
    bands = {
        "middle": middle,
        "upper": middle + standard_deviations * deviation,
        "lower": middle - standard_deviations * deviation,
        "width": (2 * standard_deviations * deviation) / middle if middle else 0,
    }
    if component not in bands:
        raise KeyError(f"Unsupported Bollinger component: {component}")
    return bands[component]


def bollinger_bandwidth_percent(
    candles: list[Candle],
    *,
    period: int = 20,
    standard_deviations: float = 2,
    field: str = "close",
) -> float:
    return (
        bollinger_band(
            candles,
            period=period,
            standard_deviations=standard_deviations,
            field=field,
            component="width",
        )
        * 100
    )


def bollinger_bandwidth_delta(
    candles: list[Candle],
    *,
    period: int = 20,
    standard_deviations: float = 2,
    field: str = "close",
    bars: int = 1,
) -> float:
    if len(candles) < period + bars:
        raise IndicatorWarmupError(
            f"bollinger_bandwidth_delta({period}) requires {period + bars} candles"
        )
    return bollinger_bandwidth_percent(
        candles,
        period=period,
        standard_deviations=standard_deviations,
        field=field,
    ) - bollinger_bandwidth_percent(
        candles[:-bars],
        period=period,
        standard_deviations=standard_deviations,
        field=field,
    )


def stochastic(
    candles: list[Candle],
    *,
    period: int = 14,
    smooth_period: int = 3,
    component: str = "k",
) -> float:
    required = period + smooth_period - 1
    if len(candles) < required:
        raise IndicatorWarmupError(f"stochastic requires {required} candles")

    def percent_k(window: list[Candle]) -> float:
        highest = max(candle.high for candle in window)
        lowest = min(candle.low for candle in window)
        if highest == lowest:
            return 50.0
        return ((window[-1].close - lowest) / (highest - lowest)) * 100

    k_values = [
        percent_k(candles[index - period + 1 : index + 1])
        for index in range(period - 1, len(candles))
    ]
    if component == "k":
        return k_values[-1]
    if component == "d":
        return sum(k_values[-smooth_period:]) / smooth_period
    raise KeyError(f"Unsupported stochastic component: {component}")


def vwap(candles: list[Candle], *, period: int = 20) -> float:
    if len(candles) < period:
        raise IndicatorWarmupError(f"vwap({period}) requires {period} candles")
    recent = candles[-period:]
    volume = sum(candle.volume for candle in recent)
    if volume == 0:
        raise IndicatorWarmupError("vwap volume is zero")
    return (
        sum(((candle.high + candle.low + candle.close) / 3) * candle.volume for candle in recent)
        / volume
    )


def anchored_vwap(
    candles: list[Candle],
    *,
    anchor_bars: int = 100,
    anchor_timestamp: str | None = None,
) -> float:
    if not candles:
        raise IndicatorWarmupError("anchored_vwap requires candles")
    if anchor_timestamp:
        anchor = candles[-1].timestamp.__class__.fromisoformat(anchor_timestamp)
        recent = [candle for candle in candles if candle.timestamp >= anchor]
    else:
        if len(candles) < anchor_bars:
            raise IndicatorWarmupError(f"anchored_vwap requires {anchor_bars} candles")
        recent = candles[-anchor_bars:]
    volume = sum(candle.volume for candle in recent)
    if volume == 0:
        raise IndicatorWarmupError("anchored_vwap volume is zero")
    return (
        sum(((candle.high + candle.low + candle.close) / 3) * candle.volume for candle in recent)
        / volume
    )


def vwap_deviation_percent(candles: list[Candle], *, period: int = 20) -> float:
    value = vwap(candles, period=period)
    if value == 0:
        raise IndicatorWarmupError("vwap value is zero")
    return ((candles[-1].close - value) / value) * 100


def range_ratio(candles: list[Candle], *, period: int = 20) -> float:
    if len(candles) < period + 1:
        raise IndicatorWarmupError(f"range_ratio({period}) requires {period + 1} candles")
    average_range = sum(candle.high - candle.low for candle in candles[-period - 1 : -1]) / period
    if average_range <= 0:
        raise IndicatorWarmupError("average candle range is zero")
    current_range = candles[-1].high - candles[-1].low
    return current_range / average_range


def expansion_ratio(
    candles: list[Candle],
    *,
    short_period: int = 14,
    long_period: int = 50,
) -> float:
    required = max(short_period, long_period) + 1
    if len(candles) < required:
        raise IndicatorWarmupError(f"expansion_ratio requires {required} candles")
    short_atr = atr(candles, period=short_period)
    long_atr = atr(candles, period=long_period)
    if long_atr == 0:
        raise IndicatorWarmupError("expansion_ratio long ATR is zero")
    return short_atr / long_atr


def trend_strength(candles: list[Candle], *, period: int = 50) -> float:
    if len(candles) < period + 1:
        raise IndicatorWarmupError(f"trend_strength({period}) requires {period + 1} candles")
    recent = candles[-period:]
    atr_period = min(14, len(candles) - 1)
    atr_value = atr(candles, period=atr_period)
    close = recent[-1].close
    if atr_value <= 0 or close <= 0:
        raise IndicatorWarmupError("trend_strength requires non-zero ATR and close")

    values = [candle.close for candle in recent]
    count = len(values)
    x_mean = (count - 1) / 2
    y_mean = sum(values) / count
    denominator = sum((index - x_mean) ** 2 for index in range(count))
    slope = (
        sum((index - x_mean) * (value - y_mean) for index, value in enumerate(values)) / denominator
        if denominator
        else 0.0
    )
    intercept = y_mean - slope * x_mean
    ss_res = sum((value - (intercept + slope * index)) ** 2 for index, value in enumerate(values))
    ss_tot = sum((value - y_mean) ** 2 for value in values)
    fit = 0.0 if ss_tot <= 1e-12 else max(0.0, min(1.0, 1.0 - ss_res / ss_tot))
    net_move = abs(values[-1] - values[0])
    path = sum(abs(right - left) for left, right in zip(values[:-1], values[1:], strict=True))
    efficiency = 0.0 if path <= 0 else max(0.0, min(1.0, net_move / path))
    direction = 1 if values[-1] >= values[0] else -1
    directional_moves = [
        1
        for left, right in zip(values[:-1], values[1:], strict=True)
        if (right - left) * direction > 0
    ]
    directional_fraction = len(directional_moves) / max(1, count - 1)
    travel_atr = max(net_move, abs(slope) * (count - 1)) / atr_value
    move_component = 1.0 - exp(-0.55 * travel_atr)
    return move_component * 0.45 + efficiency * 0.25 + directional_fraction * 0.15 + fit * 0.15


def pullback_depth_percent(
    candles: list[Candle],
    *,
    lookback: int = 20,
    direction: str = "long",
) -> float:
    if len(candles) < lookback + 1:
        raise IndicatorWarmupError(
            f"pullback_depth_percent({lookback}) requires {lookback + 1} candles"
        )
    recent = candles[-lookback - 1 : -1]
    current = candles[-1]
    if direction == "short":
        recent_low = min(candle.low for candle in recent)
        if recent_low == 0:
            raise IndicatorWarmupError("recent low is zero")
        return ((current.close - recent_low) / recent_low) * 100
    recent_high = max(candle.high for candle in recent)
    if recent_high == 0:
        raise IndicatorWarmupError("recent high is zero")
    return ((recent_high - current.close) / recent_high) * 100


def adx(candles: list[Candle], *, period: int = 14) -> float:
    required = period * 2
    if len(candles) < required:
        raise IndicatorWarmupError(f"adx({period}) requires {required} candles")
    true_ranges: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for previous, current in zip(candles[:-1], candles[1:], strict=True):
        up_move = current.high - previous.high
        down_move = previous.low - current.low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    dx_values: list[float] = []
    for end in range(period, len(true_ranges) + 1):
        tr = sum(true_ranges[end - period : end])
        if tr == 0:
            dx_values.append(0)
            continue
        plus_di = 100 * sum(plus_dm[end - period : end]) / tr
        minus_di = 100 * sum(minus_dm[end - period : end]) / tr
        denominator = plus_di + minus_di
        dx_values.append(0 if denominator == 0 else 100 * abs(plus_di - minus_di) / denominator)
    return sum(dx_values[-period:]) / period


def _wma_values(values: list[float], period: int) -> float:
    if len(values) < period:
        raise IndicatorWarmupError(f"wma({period}) requires {period} values")
    weights = range(1, period + 1)
    denominator = period * (period + 1) / 2
    return sum(value * weight for value, weight in zip(values[-period:], weights, strict=True)) / (
        denominator
    )


def _wma_series(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        raise IndicatorWarmupError(f"wma({period}) requires {period} values")
    return [_wma_values(values[: index + 1], period) for index in range(period - 1, len(values))]


def _rsi_value_series(values: list[float], period: int) -> list[float]:
    if len(values) < period + 1:
        raise IndicatorWarmupError(f"rsi({period}) requires {period + 1} values")
    results: list[float] = []
    for end in range(period + 1, len(values) + 1):
        window = values[end - period - 1 : end]
        gains = [
            max(current - previous, 0)
            for previous, current in zip(window[:-1], window[1:], strict=True)
        ]
        losses = [
            max(previous - current, 0)
            for previous, current in zip(window[:-1], window[1:], strict=True)
        ]
        average_gain = sum(gains) / period
        average_loss = sum(losses) / period
        if average_loss == 0:
            results.append(100.0 if average_gain > 0 else 50.0)
        else:
            strength = average_gain / average_loss
            results.append(100 - (100 / (1 + strength)))
    return results


def stochastic_rsi(
    candles: list[Candle],
    *,
    rsi_period: int = 14,
    stoch_period: int = 14,
    k_period: int = 3,
    d_period: int = 3,
    field: str = "close",
    component: str = "k",
) -> float:
    required = rsi_period + stoch_period + k_period + d_period - 2
    if len(candles) < required:
        raise IndicatorWarmupError(f"stochastic_rsi requires {required} candles")
    rsi_values = _rsi_value_series(_field_values(candles, field), rsi_period)
    raw_values: list[float] = []
    for end in range(stoch_period, len(rsi_values) + 1):
        window = rsi_values[end - stoch_period : end]
        low = min(window)
        high = max(window)
        raw_values.append(50.0 if high == low else ((window[-1] - low) / (high - low)) * 100)
    k_values = [
        sum(raw_values[end - k_period : end]) / k_period
        for end in range(k_period, len(raw_values) + 1)
    ]
    if component == "k":
        return k_values[-1]
    if component == "d":
        if len(k_values) < d_period:
            raise IndicatorWarmupError(f"stochastic_rsi d requires {required} candles")
        return sum(k_values[-d_period:]) / d_period
    raise KeyError(f"Unsupported Stochastic RSI component: {component}")


def money_flow_index(candles: list[Candle], *, period: int = 14) -> float:
    if len(candles) < period + 1:
        raise IndicatorWarmupError(f"money_flow_index({period}) requires {period + 1} candles")
    recent = candles[-period - 1 :]
    typical = [(candle.high + candle.low + candle.close) / 3 for candle in recent]
    positive = 0.0
    negative = 0.0
    for index in range(1, len(recent)):
        flow = typical[index] * recent[index].volume
        if typical[index] > typical[index - 1]:
            positive += flow
        elif typical[index] < typical[index - 1]:
            negative += flow
    if negative == 0:
        return 100.0 if positive > 0 else 50.0
    ratio = positive / negative
    return 100 - (100 / (1 + ratio))


def commodity_channel_index(candles: list[Candle], *, period: int = 20) -> float:
    if len(candles) < period:
        raise IndicatorWarmupError(f"commodity_channel_index({period}) requires {period} candles")
    typical = [(candle.high + candle.low + candle.close) / 3 for candle in candles[-period:]]
    average = sum(typical) / period
    mean_deviation = sum(abs(value - average) for value in typical) / period
    if mean_deviation == 0:
        return 0.0
    return (typical[-1] - average) / (0.015 * mean_deviation)


def williams_percent_r(candles: list[Candle], *, period: int = 14) -> float:
    if len(candles) < period:
        raise IndicatorWarmupError(f"williams_percent_r({period}) requires {period} candles")
    recent = candles[-period:]
    high = max(candle.high for candle in recent)
    low = min(candle.low for candle in recent)
    if high == low:
        return -50.0
    return -100 * ((high - recent[-1].close) / (high - low))


def rate_of_change(
    candles: list[Candle],
    *,
    period: int = 12,
    field: str = "close",
) -> float:
    if len(candles) < period + 1:
        raise IndicatorWarmupError(f"rate_of_change({period}) requires {period + 1} candles")
    values = _field_values(candles, field)
    reference = values[-period - 1]
    if reference == 0:
        raise IndicatorWarmupError("rate_of_change reference is zero")
    return ((values[-1] - reference) / reference) * 100


def momentum(
    candles: list[Candle],
    *,
    period: int = 10,
    field: str = "close",
) -> float:
    if len(candles) < period + 1:
        raise IndicatorWarmupError(f"momentum({period}) requires {period + 1} candles")
    values = _field_values(candles, field)
    return values[-1] - values[-period - 1]


def true_strength_index(
    candles: list[Candle],
    *,
    long_period: int = 25,
    short_period: int = 13,
    signal_period: int = 7,
    field: str = "close",
    component: str = "tsi",
) -> float:
    required = long_period + short_period + signal_period + 1
    if len(candles) < required:
        raise IndicatorWarmupError(f"true_strength_index requires {required} candles")
    values = _field_values(candles, field)
    changes = [
        current - previous for previous, current in zip(values[:-1], values[1:], strict=True)
    ]
    first = _ema_series(changes, long_period)
    second = _ema_series(first, short_period)
    abs_first = _ema_series([abs(value) for value in changes], long_period)
    abs_second = _ema_series(abs_first, short_period)
    tsi_series = [
        0.0 if denominator == 0 else 100 * numerator / denominator
        for numerator, denominator in zip(second, abs_second, strict=True)
    ]
    if component == "tsi":
        return tsi_series[-1]
    if component == "signal":
        return _ema_series(tsi_series, signal_period)[-1]
    raise KeyError(f"Unsupported TSI component: {component}")


def ultimate_oscillator(
    candles: list[Candle],
    *,
    short: int = 7,
    medium: int = 14,
    long: int = 28,
) -> float:
    if len(candles) < long + 1:
        raise IndicatorWarmupError(f"ultimate_oscillator requires {long + 1} candles")
    buying_pressure: list[float] = []
    true_range: list[float] = []
    for previous, current in zip(candles[:-1], candles[1:], strict=True):
        minimum = min(current.low, previous.close)
        maximum = max(current.high, previous.close)
        buying_pressure.append(current.close - minimum)
        true_range.append(maximum - minimum)

    def average(period: int) -> float:
        denominator = sum(true_range[-period:])
        return 0.0 if denominator == 0 else sum(buying_pressure[-period:]) / denominator

    return 100 * ((4 * average(short)) + (2 * average(medium)) + average(long)) / 7


def relative_vigor_index(
    candles: list[Candle],
    *,
    period: int = 10,
    signal_period: int = 4,
    component: str = "rvi",
) -> float:
    required = period + signal_period - 1
    if len(candles) < required:
        raise IndicatorWarmupError(f"relative_vigor_index requires {required} candles")

    def value(window: list[Candle]) -> float:
        numerator = sum(candle.close - candle.open for candle in window[-period:])
        denominator = sum(candle.high - candle.low for candle in window[-period:])
        return 0.0 if denominator == 0 else numerator / denominator

    rvi_values = [value(candles[:end]) for end in range(period, len(candles) + 1)]
    if component == "rvi":
        return rvi_values[-1]
    if component == "signal":
        return sum(rvi_values[-signal_period:]) / signal_period
    raise KeyError(f"Unsupported RVI component: {component}")


def connors_rsi(
    candles: list[Candle],
    *,
    rsi_period: int = 3,
    streak_rsi_period: int = 2,
    percent_rank_period: int = 100,
    field: str = "close",
) -> float:
    required = percent_rank_period + max(rsi_period, streak_rsi_period) + 2
    if len(candles) < required:
        raise IndicatorWarmupError(f"connors_rsi requires {required} candles")
    values = _field_values(candles, field)
    price_rsi = _rsi_value_series(values, rsi_period)[-1]
    streaks = [0.0]
    for previous, current in zip(values[:-1], values[1:], strict=True):
        if current > previous:
            streaks.append(max(streaks[-1], 0) + 1)
        elif current < previous:
            streaks.append(min(streaks[-1], 0) - 1)
        else:
            streaks.append(0.0)
    streak_rsi = _rsi_value_series(streaks, streak_rsi_period)[-1]
    changes = [
        0.0 if previous == 0 else ((current - previous) / previous) * 100
        for previous, current in zip(values[:-1], values[1:], strict=True)
    ]
    current_change = changes[-1]
    ranked = changes[-percent_rank_period:]
    percent_rank = 100 * sum(value < current_change for value in ranked) / len(ranked)
    return (price_rsi + streak_rsi + percent_rank) / 3


def weighted_moving_average(
    candles: list[Candle],
    *,
    period: int = 20,
    field: str = "close",
) -> float:
    return _wma_values(_field_values(candles, field), period)


def hull_moving_average(
    candles: list[Candle],
    *,
    period: int = 20,
    field: str = "close",
) -> float:
    root_period = max(1, round(sqrt(period)))
    required = period + root_period - 1
    if len(candles) < required:
        raise IndicatorWarmupError(f"hull_moving_average({period}) requires {required} candles")
    values = _field_values(candles, field)
    half = _wma_series(values, max(1, period // 2))
    full = _wma_series(values, period)
    aligned_half = half[-len(full) :]
    difference = [
        (2 * half_value) - full_value
        for half_value, full_value in zip(aligned_half, full, strict=True)
    ]
    return _wma_values(difference, root_period)


def double_exponential_moving_average(
    candles: list[Candle],
    *,
    period: int = 20,
    field: str = "close",
) -> float:
    required = (period * 2) - 1
    if len(candles) < required:
        raise IndicatorWarmupError(
            f"double_exponential_moving_average({period}) requires {required} candles"
        )
    first = _ema_series(_field_values(candles, field), period)
    second = _ema_series(first, period)
    return (2 * first[-1]) - second[-1]


def triple_exponential_moving_average(
    candles: list[Candle],
    *,
    period: int = 20,
    field: str = "close",
) -> float:
    required = (period * 3) - 2
    if len(candles) < required:
        raise IndicatorWarmupError(
            f"triple_exponential_moving_average({period}) requires {required} candles"
        )
    first = _ema_series(_field_values(candles, field), period)
    second = _ema_series(first, period)
    third = _ema_series(second, period)
    return (3 * first[-1]) - (3 * second[-1]) + third[-1]


def kaufman_adaptive_moving_average(
    candles: list[Candle],
    *,
    period: int = 10,
    fast_period: int = 2,
    slow_period: int = 30,
    field: str = "close",
) -> float:
    if len(candles) < period + 1:
        raise IndicatorWarmupError(
            f"kaufman_adaptive_moving_average({period}) requires {period + 1} candles"
        )
    values = _field_values(candles, field)
    fast = 2 / (fast_period + 1)
    slow = 2 / (slow_period + 1)
    result = sum(values[:period]) / period
    for index in range(period, len(values)):
        change = abs(values[index] - values[index - period])
        volatility = sum(
            abs(values[position] - values[position - 1])
            for position in range(index - period + 1, index + 1)
        )
        efficiency = 0.0 if volatility == 0 else change / volatility
        smoothing = (efficiency * (fast - slow) + slow) ** 2
        result += smoothing * (values[index] - result)
    return result


def volume_weighted_moving_average(
    candles: list[Candle],
    *,
    period: int = 20,
    field: str = "close",
) -> float:
    if len(candles) < period:
        raise IndicatorWarmupError(
            f"volume_weighted_moving_average({period}) requires {period} candles"
        )
    recent = candles[-period:]
    volume = sum(candle.volume for candle in recent)
    if volume == 0:
        raise IndicatorWarmupError("volume_weighted_moving_average volume is zero")
    return sum(float(getattr(candle, field)) * candle.volume for candle in recent) / volume


def linear_regression_moving_average(
    candles: list[Candle],
    *,
    period: int = 20,
    field: str = "close",
) -> float:
    if len(candles) < period:
        raise IndicatorWarmupError(
            f"linear_regression_moving_average({period}) requires {period} candles"
        )
    values = _field_values(candles[-period:], field)
    x_mean = (period - 1) / 2
    y_mean = sum(values) / period
    denominator = sum((index - x_mean) ** 2 for index in range(period))
    slope = (
        sum((index - x_mean) * (value - y_mean) for index, value in enumerate(values)) / denominator
        if denominator
        else 0.0
    )
    intercept = y_mean - slope * x_mean
    return intercept + slope * (period - 1)


def zero_lag_ema(
    candles: list[Candle],
    *,
    period: int = 20,
    field: str = "close",
) -> float:
    lag = max(1, (period - 1) // 2)
    required = period + lag
    if len(candles) < required:
        raise IndicatorWarmupError(f"zero_lag_ema({period}) requires {required} candles")
    values = _field_values(candles, field)
    adjusted = [
        value if index < lag else value + (value - values[index - lag])
        for index, value in enumerate(values)
    ]
    return _ema_series(adjusted, period)[-1]


def moving_average_ribbon(
    candles: list[Candle],
    *,
    periods: str | list[int | float | str | bool] = "10,20,50,100",
    average: str = "ema",
    field: str = "close",
    component: str = "bullish_stack",
    compression_threshold_percent: float = 1.0,
    bars: int = 1,
) -> float:
    parsed = (
        [int(value.strip()) for value in periods.split(",") if value.strip()]
        if isinstance(periods, str)
        else [int(value) for value in periods]
    )
    if len(parsed) < 2:
        raise ValueError("moving_average_ribbon requires at least two periods")

    def averages(history: list[Candle]) -> list[float]:
        calculator = ema if average == "ema" else sma
        return [calculator(history, period=period, field=field) for period in parsed]

    values = averages(candles)
    if component == "bullish_stack":
        return float(all(left > right for left, right in zip(values[:-1], values[1:], strict=True)))
    if component == "bearish_stack":
        return float(all(left < right for left, right in zip(values[:-1], values[1:], strict=True)))
    mean = sum(values) / len(values)
    spread = 0.0 if mean == 0 else ((max(values) - min(values)) / abs(mean)) * 100
    if component == "compression":
        return float(spread <= compression_threshold_percent)
    if component == "spread_percent":
        return spread
    if component == "expansion":
        if len(candles) <= bars:
            raise IndicatorWarmupError("moving_average_ribbon expansion requires prior candles")
        previous = averages(candles[:-bars])
        previous_mean = sum(previous) / len(previous)
        previous_spread = (
            0.0
            if previous_mean == 0
            else ((max(previous) - min(previous)) / abs(previous_mean)) * 100
        )
        return spread - previous_spread
    raise KeyError(f"Unsupported moving average ribbon component: {component}")


def ichimoku_cloud(
    candles: list[Candle],
    *,
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_b_period: int = 52,
    displacement: int = 26,
    component: str = "tenkan",
) -> float:
    required = max(senkou_b_period, displacement + 1)
    if len(candles) < required:
        raise IndicatorWarmupError(f"ichimoku_cloud requires {required} candles")

    def midpoint(period: int) -> float:
        recent = candles[-period:]
        return (max(candle.high for candle in recent) + min(candle.low for candle in recent)) / 2

    tenkan = midpoint(tenkan_period)
    kijun = midpoint(kijun_period)
    senkou_a = (tenkan + kijun) / 2
    senkou_b = midpoint(senkou_b_period)
    values = {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a,
        "senkou_b": senkou_b,
        "chikou": candles[-displacement - 1].close,
        "cloud_top": max(senkou_a, senkou_b),
        "cloud_bottom": min(senkou_a, senkou_b),
        "future_cloud_bullish": float(senkou_a > senkou_b),
        "future_cloud_bearish": float(senkou_a < senkou_b),
        "price_above_cloud": float(candles[-1].close > max(senkou_a, senkou_b)),
        "price_below_cloud": float(candles[-1].close < min(senkou_a, senkou_b)),
        "price_inside_cloud": float(
            min(senkou_a, senkou_b) <= candles[-1].close <= max(senkou_a, senkou_b)
        ),
    }
    if component not in values:
        raise KeyError(f"Unsupported Ichimoku component: {component}")
    return values[component]


def supertrend(
    candles: list[Candle],
    *,
    atr_period: int = 10,
    multiplier: float = 3.0,
    component: str = "direction",
) -> float:
    required = atr_period + 2
    if len(candles) < required:
        raise IndicatorWarmupError(f"supertrend requires {required} candles")
    true_ranges = [0.0]
    for previous, current in zip(candles[:-1], candles[1:], strict=True):
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    final_upper = 0.0
    final_lower = 0.0
    direction = 1
    line = candles[atr_period].low
    for index in range(atr_period, len(candles)):
        average_range = sum(true_ranges[index - atr_period + 1 : index + 1]) / atr_period
        midpoint = (candles[index].high + candles[index].low) / 2
        basic_upper = midpoint + multiplier * average_range
        basic_lower = midpoint - multiplier * average_range
        previous_close = candles[index - 1].close
        final_upper = (
            basic_upper
            if index == atr_period or basic_upper < final_upper or previous_close > final_upper
            else final_upper
        )
        final_lower = (
            basic_lower
            if index == atr_period or basic_lower > final_lower or previous_close < final_lower
            else final_lower
        )
        if direction == -1 and candles[index].close > final_upper:
            direction = 1
        elif direction == 1 and candles[index].close < final_lower:
            direction = -1
        line = final_lower if direction == 1 else final_upper
    if component == "line":
        return line
    if component == "direction":
        return float(direction)
    if component == "price_above":
        return float(candles[-1].close > line)
    if component == "price_below":
        return float(candles[-1].close < line)
    raise KeyError(f"Unsupported SuperTrend component: {component}")


def parabolic_sar(
    candles: list[Candle],
    *,
    step: float = 0.02,
    max_step: float = 0.2,
    component: str = "direction",
) -> float:
    if len(candles) < 3:
        raise IndicatorWarmupError("parabolic_sar requires 3 candles")
    bullish = candles[1].close >= candles[0].close
    sar = candles[0].low if bullish else candles[0].high
    extreme = candles[0].high if bullish else candles[0].low
    acceleration = step
    for index in range(1, len(candles)):
        candle = candles[index]
        sar += acceleration * (extreme - sar)
        if bullish:
            sar = min(sar, candles[index - 1].low)
            if index > 1:
                sar = min(sar, candles[index - 2].low)
            if candle.low < sar:
                bullish = False
                sar = extreme
                extreme = candle.low
                acceleration = step
            elif candle.high > extreme:
                extreme = candle.high
                acceleration = min(max_step, acceleration + step)
        else:
            sar = max(sar, candles[index - 1].high)
            if index > 1:
                sar = max(sar, candles[index - 2].high)
            if candle.high > sar:
                bullish = True
                sar = extreme
                extreme = candle.high
                acceleration = step
            elif candle.low < extreme:
                extreme = candle.low
                acceleration = min(max_step, acceleration + step)
    if component == "line":
        return sar
    if component == "direction":
        return 1.0 if bullish else -1.0
    if component == "price_above":
        return float(candles[-1].close > sar)
    if component == "price_below":
        return float(candles[-1].close < sar)
    raise KeyError(f"Unsupported Parabolic SAR component: {component}")


def aroon(
    candles: list[Candle],
    *,
    period: int = 25,
    component: str = "oscillator",
) -> float:
    if len(candles) < period + 1:
        raise IndicatorWarmupError(f"aroon({period}) requires {period + 1} candles")
    recent = candles[-period - 1 :]
    high_index = max(range(len(recent)), key=lambda index: recent[index].high)
    low_index = min(range(len(recent)), key=lambda index: recent[index].low)
    periods_since_high = len(recent) - 1 - high_index
    periods_since_low = len(recent) - 1 - low_index
    up = 100 * (period - periods_since_high) / period
    down = 100 * (period - periods_since_low) / period
    values = {"aroon_up": up, "aroon_down": down, "oscillator": up - down}
    if component not in values:
        raise KeyError(f"Unsupported Aroon component: {component}")
    return values[component]


def directional_movement(
    candles: list[Candle],
    *,
    period: int = 14,
    component: str = "plus_di",
) -> float:
    required = (period * 2) + 1 if component in {"adx", "adxr"} else period + 1
    if len(candles) < required:
        raise IndicatorWarmupError(f"directional_movement requires {required} candles")

    def components(history: list[Candle]) -> tuple[float, float, float]:
        recent = history[-period - 1 :]
        ranges: list[float] = []
        plus_moves: list[float] = []
        minus_moves: list[float] = []
        for previous, current in zip(recent[:-1], recent[1:], strict=True):
            up = current.high - previous.high
            down = previous.low - current.low
            plus_moves.append(up if up > down and up > 0 else 0.0)
            minus_moves.append(down if down > up and down > 0 else 0.0)
            ranges.append(
                max(
                    current.high - current.low,
                    abs(current.high - previous.close),
                    abs(current.low - previous.close),
                )
            )
        total_range = sum(ranges)
        if total_range == 0:
            return 0.0, 0.0, 0.0
        plus = 100 * sum(plus_moves) / total_range
        minus = 100 * sum(minus_moves) / total_range
        denominator = plus + minus
        dx_value = 0.0 if denominator == 0 else 100 * abs(plus - minus) / denominator
        return plus, minus, dx_value

    plus_di, minus_di, dx_value = components(candles)
    if component == "plus_di":
        return plus_di
    if component == "minus_di":
        return minus_di
    if component == "dx":
        return dx_value
    dx_series = [components(candles[:end])[2] for end in range(period + 1, len(candles) + 1)]
    adx_value = sum(dx_series[-period:]) / period
    if component == "adx":
        return adx_value
    if component == "adxr":
        if len(dx_series) < period * 2:
            raise IndicatorWarmupError(f"directional_movement adxr requires {required} candles")
        previous_adx = sum(dx_series[-period * 2 : -period]) / period
        return (adx_value + previous_adx) / 2
    raise KeyError(f"Unsupported directional movement component: {component}")


def elder_impulse(
    candles: list[Candle],
    *,
    ema_period: int = 13,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
    component: str = "state",
) -> float:
    required = max(ema_period + 1, slow_period + signal_period)
    if len(candles) < required:
        raise IndicatorWarmupError(f"elder_impulse requires {required} candles")
    slope = ema_slope(candles, period=ema_period)
    histogram_change = macd_histogram_delta(
        candles,
        fast_period=fast_period,
        slow_period=slow_period,
        signal_period=signal_period,
    )
    if slope > 0 and histogram_change > 0:
        state = 1.0
    elif slope < 0 and histogram_change < 0:
        state = -1.0
    else:
        state = 0.0
    if component == "state":
        return state
    if component == "bullish":
        return float(state == 1)
    if component == "bearish":
        return float(state == -1)
    if component == "neutral":
        return float(state == 0)
    raise KeyError(f"Unsupported Elder Impulse component: {component}")


def keltner_channel(
    candles: list[Candle],
    *,
    ema_period: int = 20,
    atr_period: int = 10,
    multiplier: float = 2.0,
    field: str = "close",
    component: str = "middle",
) -> float:
    middle = ema(candles, period=ema_period, field=field)
    range_value = atr(candles, period=atr_period)
    values = {
        "middle": middle,
        "upper": middle + multiplier * range_value,
        "lower": middle - multiplier * range_value,
        "width_percent": (
            0.0 if middle == 0 else ((2 * multiplier * range_value) / abs(middle)) * 100
        ),
    }
    if component not in values:
        raise KeyError(f"Unsupported Keltner component: {component}")
    return values[component]


def donchian_channel(
    candles: list[Candle],
    *,
    period: int = 20,
    component: str = "middle",
) -> float:
    if len(candles) < period + 1:
        raise IndicatorWarmupError(f"donchian_channel({period}) requires {period + 1} candles")
    prior = candles[-period - 1 : -1]
    upper = max(candle.high for candle in prior)
    lower = min(candle.low for candle in prior)
    values = {"upper": upper, "lower": lower, "middle": (upper + lower) / 2}
    if component not in values:
        raise KeyError(f"Unsupported Donchian component: {component}")
    return values[component]


def bollinger_percent_b(
    candles: list[Candle],
    *,
    period: int = 20,
    standard_deviations: float = 2,
    field: str = "close",
) -> float:
    upper = bollinger_band(
        candles,
        period=period,
        standard_deviations=standard_deviations,
        field=field,
        component="upper",
    )
    lower = bollinger_band(
        candles,
        period=period,
        standard_deviations=standard_deviations,
        field=field,
        component="lower",
    )
    if upper == lower:
        return 0.5
    return (float(getattr(candles[-1], field)) - lower) / (upper - lower)


def squeeze_detection(
    candles: list[Candle],
    *,
    bb_period: int = 20,
    bb_standard_deviations: float = 2,
    kc_ema_period: int = 20,
    kc_atr_period: int = 10,
    kc_multiplier: float = 1.5,
    component: str = "squeeze_on",
) -> float:
    def state(history: list[Candle]) -> bool:
        bb_upper = bollinger_band(
            history,
            period=bb_period,
            standard_deviations=bb_standard_deviations,
            component="upper",
        )
        bb_lower = bollinger_band(
            history,
            period=bb_period,
            standard_deviations=bb_standard_deviations,
            component="lower",
        )
        kc_upper = keltner_channel(
            history,
            ema_period=kc_ema_period,
            atr_period=kc_atr_period,
            multiplier=kc_multiplier,
            component="upper",
        )
        kc_lower = keltner_channel(
            history,
            ema_period=kc_ema_period,
            atr_period=kc_atr_period,
            multiplier=kc_multiplier,
            component="lower",
        )
        return bb_upper < kc_upper and bb_lower > kc_lower

    current = state(candles)
    if component == "squeeze_on":
        return float(current)
    if component == "squeeze_off":
        return float(not current)
    if len(candles) < max(bb_period, kc_ema_period, kc_atr_period + 1) + 1:
        raise IndicatorWarmupError("squeeze_detection fired state requires one prior candle")
    previous = state(candles[:-1])
    fired = previous and not current
    if component == "squeeze_fired":
        return float(fired)
    if component == "bullish_fire":
        return float(fired and candles[-1].close > candles[-2].close)
    if component == "bearish_fire":
        return float(fired and candles[-1].close < candles[-2].close)
    raise KeyError(f"Unsupported squeeze component: {component}")


def _percentile_rank(values: list[float], current: float) -> float:
    if not values:
        raise IndicatorWarmupError("percentile rank requires values")
    return 100 * sum(value <= current for value in values) / len(values)


def historical_volatility(
    candles: list[Candle],
    *,
    period: int = 20,
    annualization_periods: float = 365,
    field: str = "close",
    component: str = "value",
    percentile_lookback: int = 100,
    bars: int = 1,
) -> float:
    def value(history: list[Candle]) -> float:
        if len(history) < period + 1:
            raise IndicatorWarmupError(
                f"historical_volatility({period}) requires {period + 1} candles"
            )
        prices = _field_values(history[-period - 1 :], field)
        if any(price <= 0 for price in prices):
            raise IndicatorWarmupError("historical volatility requires positive prices")
        returns = [
            log(current / previous)
            for previous, current in zip(prices[:-1], prices[1:], strict=True)
        ]
        average = sum(returns) / len(returns)
        variance = sum((item - average) ** 2 for item in returns) / len(returns)
        return sqrt(variance) * sqrt(annualization_periods) * 100

    current = value(candles)
    if component == "value":
        return current
    if component == "delta":
        if len(candles) < period + bars + 1:
            raise IndicatorWarmupError(
                f"historical_volatility delta requires {period + bars + 1} candles"
            )
        return current - value(candles[:-bars])
    if component == "percentile":
        required = period + percentile_lookback
        if len(candles) < required:
            raise IndicatorWarmupError(
                f"historical_volatility percentile requires {required} candles"
            )
        series = [value(candles[:end]) for end in range(period + 1, len(candles) + 1)][
            -percentile_lookback:
        ]
        return _percentile_rank(series, current)
    raise KeyError(f"Unsupported historical volatility component: {component}")


def normalized_atr(candles: list[Candle], *, period: int = 14) -> float:
    return atr_percent(candles, period=period)


def choppiness_index(candles: list[Candle], *, period: int = 14) -> float:
    # The formula divides by log(period), which is nought when the window is one candle.
    # The Builder offered a window of one — every count field's smallest sensible value —
    # and the trader got a condition in the error state reading only "ZeroDivisionError".
    # Refused here in words, and INDICATOR_MINIMUM_PERIOD keeps the form from offering it.
    if period < INDICATOR_MINIMUM_PERIOD["choppiness_index"]:
        raise IndicatorDomainError(
            "the choppiness measure compares a window against itself, so it needs at "
            "least two candles"
        )
    if len(candles) < period + 1:
        raise IndicatorWarmupError(f"choppiness_index({period}) requires {period + 1} candles")
    recent = candles[-period - 1 :]
    true_ranges = [
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in zip(recent[:-1], recent[1:], strict=True)
    ]
    highest = max(candle.high for candle in recent[1:])
    lowest = min(candle.low for candle in recent[1:])
    price_range = highest - lowest
    if price_range <= 0:
        return 100.0
    return 100 * log(sum(true_ranges) / price_range, 10) / log(period, 10)


def ulcer_index(
    candles: list[Candle],
    *,
    period: int = 14,
    field: str = "close",
) -> float:
    if len(candles) < period:
        raise IndicatorWarmupError(f"ulcer_index({period}) requires {period} candles")
    values = _field_values(candles[-period:], field)
    peak = values[0]
    squared_drawdowns: list[float] = []
    for value in values:
        peak = max(peak, value)
        drawdown = 0.0 if peak == 0 else ((value - peak) / peak) * 100
        squared_drawdowns.append(drawdown**2)
    return sqrt(sum(squared_drawdowns) / period)


def on_balance_volume(
    candles: list[Candle],
    *,
    component: str = "value",
    ma_period: int = 20,
    bars: int = 1,
) -> float:
    if len(candles) < 2:
        raise IndicatorWarmupError("on_balance_volume requires 2 candles")
    series = [0.0]
    for previous, current in zip(candles[:-1], candles[1:], strict=True):
        direction = (
            1 if current.close > previous.close else -1 if current.close < previous.close else 0
        )
        series.append(series[-1] + direction * current.volume)
    if component == "value":
        return series[-1]
    if component == "delta":
        if len(series) <= bars:
            raise IndicatorWarmupError("on_balance_volume delta requires prior candles")
        return series[-1] - series[-bars - 1]
    if component == "ma":
        if len(series) < ma_period:
            raise IndicatorWarmupError(f"on_balance_volume ma requires {ma_period} candles")
        return sum(series[-ma_period:]) / ma_period
    if component == "distance_from_ma":
        if len(series) < ma_period:
            raise IndicatorWarmupError(f"on_balance_volume ma requires {ma_period} candles")
        return series[-1] - (sum(series[-ma_period:]) / ma_period)
    raise KeyError(f"Unsupported OBV component: {component}")


def chaikin_money_flow(candles: list[Candle], *, period: int = 20) -> float:
    if len(candles) < period:
        raise IndicatorWarmupError(f"chaikin_money_flow({period}) requires {period} candles")
    recent = candles[-period:]
    volume = sum(candle.volume for candle in recent)
    if volume == 0:
        raise IndicatorWarmupError("chaikin money flow volume is zero")
    flow = 0.0
    for candle in recent:
        candle_range = candle.high - candle.low
        multiplier = (
            0.0
            if candle_range == 0
            else ((candle.close - candle.low) - (candle.high - candle.close)) / candle_range
        )
        flow += multiplier * candle.volume
    return flow / volume


def accumulation_distribution(
    candles: list[Candle],
    *,
    component: str = "value",
    bars: int = 1,
    breakout_lookback: int = 20,
) -> float:
    if not candles:
        raise IndicatorWarmupError("accumulation_distribution requires candles")
    series: list[float] = []
    total = 0.0
    for candle in candles:
        candle_range = candle.high - candle.low
        multiplier = (
            0.0
            if candle_range == 0
            else ((candle.close - candle.low) - (candle.high - candle.close)) / candle_range
        )
        total += multiplier * candle.volume
        series.append(total)
    if component == "value":
        return series[-1]
    if component == "delta":
        if len(series) <= bars:
            raise IndicatorWarmupError("accumulation_distribution delta requires prior candles")
        return series[-1] - series[-bars - 1]
    if component == "breakout":
        if len(series) < breakout_lookback + 1:
            raise IndicatorWarmupError(
                f"accumulation_distribution breakout requires {breakout_lookback + 1} candles"
            )
        return float(series[-1] > max(series[-breakout_lookback - 1 : -1]))
    raise KeyError(f"Unsupported A/D component: {component}")


def ease_of_movement(
    candles: list[Candle],
    *,
    period: int = 14,
    volume_divisor: float = 100_000_000,
) -> float:
    if len(candles) < period + 1:
        raise IndicatorWarmupError(f"ease_of_movement({period}) requires {period + 1} candles")
    values: list[float] = []
    recent = candles[-period - 1 :]
    for previous, current in zip(recent[:-1], recent[1:], strict=True):
        distance = ((current.high + current.low) / 2) - ((previous.high + previous.low) / 2)
        candle_range = current.high - current.low
        box_ratio = (current.volume / volume_divisor) / candle_range if candle_range > 0 else 0
        values.append(0.0 if box_ratio == 0 else distance / box_ratio)
    return sum(values) / len(values)


def force_index(
    candles: list[Candle],
    *,
    period: int = 13,
    component: str = "value",
) -> float:
    if len(candles) < period + 1:
        raise IndicatorWarmupError(f"force_index({period}) requires {period + 1} candles")
    raw = [
        (current.close - previous.close) * current.volume
        for previous, current in zip(candles[:-1], candles[1:], strict=True)
    ]
    smoothed = _ema_series(raw, period)
    if component == "value":
        return smoothed[-1]
    if component == "spike_ratio":
        baseline = sum(abs(value) for value in smoothed[-period:]) / min(period, len(smoothed))
        return 0.0 if baseline == 0 else abs(smoothed[-1]) / baseline
    raise KeyError(f"Unsupported Force Index component: {component}")


def volume_oscillator(
    candles: list[Candle],
    *,
    short_period: int = 5,
    long_period: int = 20,
    component: str = "percent",
) -> float:
    if len(candles) < long_period:
        raise IndicatorWarmupError(f"volume_oscillator requires {long_period} candles")
    volumes = [float(candle.volume) for candle in candles]
    short = _ema_series(volumes, short_period)[-1]
    long = _ema_series(volumes, long_period)[-1]
    if component == "difference":
        return short - long
    if component == "percent":
        return 0.0 if long == 0 else ((short - long) / long) * 100
    raise KeyError(f"Unsupported volume oscillator component: {component}")


def volume_profile_proxy(
    candles: list[Candle],
    *,
    period: int = 100,
    bins: int = 24,
    component: str = "high_volume_price_zone",
    near_percent: float = 1.0,
) -> float:
    if len(candles) < period:
        raise IndicatorWarmupError(f"volume_profile_proxy({period}) requires {period} candles")
    recent = candles[-period:]
    low = min(candle.low for candle in recent)
    high = max(candle.high for candle in recent)
    if high == low:
        return recent[-1].close if component == "high_volume_price_zone" else 0.0
    width = (high - low) / bins
    volumes = [0.0] * bins
    for candle in recent:
        typical = (candle.high + candle.low + candle.close) / 3
        index = min(bins - 1, max(0, int((typical - low) / width)))
        volumes[index] += candle.volume
    node = max(range(bins), key=volumes.__getitem__)
    zone = low + (node + 0.5) * width
    current = recent[-1]
    distance = 0.0 if zone == 0 else ((current.close - zone) / zone) * 100
    if component == "high_volume_price_zone":
        return zone
    if component == "distance_percent":
        return distance
    if component == "volume_node_near_price":
        return float(abs(distance) <= near_percent)
    if component == "price_above_recent_high_volume_zone":
        return float(current.close > zone)
    if component == "price_rejects_high_volume_zone":
        touched = current.low <= zone <= current.high
        return float(touched and abs(distance) > near_percent / 4)
    raise KeyError(f"Unsupported volume profile proxy component: {component}")


def relative_volume_by_session(
    candles: list[Candle],
    *,
    component: str = "same_time_ratio",
    timezone: str = "UTC",
    lookback_days: int = 30,
    session_start_hour: float = 0,
    session_end_hour: float = 24,
) -> float:
    if len(candles) < 2:
        raise IndicatorWarmupError("relative_volume_by_session requires history")
    zone = ZoneInfo(timezone)
    current = candles[-1]
    current_local = current.timestamp.astimezone(zone)
    cutoff = current_local.date().toordinal() - lookback_days
    same_time = [
        candle.volume
        for candle in candles[:-1]
        if candle.timestamp.astimezone(zone).date().toordinal() >= cutoff
        and candle.timestamp.astimezone(zone).hour == current_local.hour
        and candle.timestamp.astimezone(zone).minute == current_local.minute
    ]
    if component in {"same_time_ratio", "same_time_percentile"}:
        if not same_time:
            raise IndicatorWarmupError(
                "relative_volume_by_session requires prior same-time candles"
            )
        if component == "same_time_ratio":
            average = sum(same_time) / len(same_time)
            return 0.0 if average == 0 else current.volume / average
        return _percentile_rank(same_time, current.volume)

    def in_session(candle: Candle) -> bool:
        local = candle.timestamp.astimezone(zone)
        value = local.hour + local.minute / 60
        return (
            session_start_hour <= value < session_end_hour
            if session_start_hour <= session_end_hour
            else value >= session_start_hour or value < session_end_hour
        )

    current_date = current_local.date()
    current_session_volume = sum(
        candle.volume
        for candle in candles
        if candle.timestamp.astimezone(zone).date() == current_date and in_session(candle)
    )
    daily_totals: dict[object, float] = {}
    for candle in candles[:-1]:
        local = candle.timestamp.astimezone(zone)
        if local.date().toordinal() < cutoff or not in_session(candle):
            continue
        daily_totals[local.date()] = daily_totals.get(local.date(), 0.0) + candle.volume
    if not daily_totals:
        raise IndicatorWarmupError("relative_volume_by_session requires prior sessions")
    average_session = sum(daily_totals.values()) / len(daily_totals)
    if component == "session_ratio":
        return 0.0 if average_session == 0 else current_session_volume / average_session
    if component == "session_percentile":
        return _percentile_rank(list(daily_totals.values()), current_session_volume)
    raise KeyError(f"Unsupported session relative volume component: {component}")


def dollar_volume(
    candles: list[Candle],
    *,
    period: int = 1,
    component: str = "value",
    baseline_period: int = 20,
) -> float:
    if len(candles) < max(period, baseline_period if component == "spike_ratio" else period):
        required = max(period, baseline_period if component == "spike_ratio" else period)
        raise IndicatorWarmupError(f"dollar_volume requires {required} candles")

    def value(candle: Candle) -> float:
        return (
            candle.quote_volume if candle.quote_volume is not None else candle.close * candle.volume
        )

    current = sum(value(candle) for candle in candles[-period:])
    if component == "value":
        return current
    if component == "spike_ratio":
        prior = [value(candle) for candle in candles[-baseline_period - 1 : -1]]
        average = sum(prior) / len(prior)
        return 0.0 if average == 0 else value(candles[-1]) / average
    raise KeyError(f"Unsupported dollar volume component: {component}")


def buy_sell_pressure_proxy(
    candles: list[Candle],
    *,
    component: str = "pressure_score",
    period: int = 20,
) -> float:
    if len(candles) < period:
        raise IndicatorWarmupError(f"buy_sell_pressure_proxy requires {period} candles")

    def close_position(candle: Candle) -> float:
        candle_range = candle.high - candle.low
        return 0.5 if candle_range <= 0 else (candle.close - candle.low) / candle_range

    current_position = close_position(candles[-1])
    if component == "close_position_in_range":
        return current_position * 100
    up_volume = sum(candle.volume * close_position(candle) for candle in candles[-period:])
    down_volume = sum(candle.volume * (1 - close_position(candle)) for candle in candles[-period:])
    if component == "up_volume_proxy":
        return up_volume
    if component == "down_volume_proxy":
        return down_volume
    if component == "pressure_score":
        total = up_volume + down_volume
        return 0.0 if total == 0 else ((up_volume - down_volume) / total) * 100
    raise KeyError(f"Unsupported pressure proxy component: {component}")


def pivot_points(
    candles: list[Candle],
    *,
    lookback: int = 1,
    component: str = "pivot",
) -> float:
    if len(candles) < lookback + 1:
        raise IndicatorWarmupError(f"pivot_points requires {lookback + 1} candles")
    reference = candles[-lookback - 1]
    pivot = (reference.high + reference.low + reference.close) / 3
    values = {
        "pivot": pivot,
        "r1": (2 * pivot) - reference.low,
        "s1": (2 * pivot) - reference.high,
        "r2": pivot + (reference.high - reference.low),
        "s2": pivot - (reference.high - reference.low),
    }
    if component not in values:
        raise KeyError(f"Unsupported pivot component: {component}")
    return values[component]


def candle_anatomy(
    candles: list[Candle],
    *,
    component: str = "body_percent",
    period: int = 20,
    count: int = 3,
) -> float:
    if not candles:
        raise IndicatorWarmupError("candle_anatomy requires candles")
    candle = candles[-1]
    candle_range = candle.high - candle.low
    body = abs(candle.close - candle.open)
    upper = candle.high - max(candle.open, candle.close)
    lower = min(candle.open, candle.close) - candle.low
    if component == "body_percent":
        return 0.0 if candle_range <= 0 else (body / candle_range) * 100
    if component == "upper_wick_percent":
        return 0.0 if candle_range <= 0 else (upper / candle_range) * 100
    if component == "lower_wick_percent":
        return 0.0 if candle_range <= 0 else (lower / candle_range) * 100
    if component == "close_position":
        return 50.0 if candle_range <= 0 else ((candle.close - candle.low) / candle_range) * 100
    if component == "open_position":
        return 50.0 if candle_range <= 0 else ((candle.open - candle.low) / candle_range) * 100
    if component in {"body_vs_average", "range_vs_average"}:
        if len(candles) < period + 1:
            raise IndicatorWarmupError(f"candle anatomy average requires {period + 1} candles")
        if component == "body_vs_average":
            average = (
                sum(abs(item.close - item.open) for item in candles[-period - 1 : -1]) / period
            )
            return 0.0 if average == 0 else body / average
        average = sum(item.high - item.low for item in candles[-period - 1 : -1]) / period
        return 0.0 if average == 0 else candle_range / average
    if len(candles) < count:
        raise IndicatorWarmupError(f"candle anatomy sequence requires {count} candles")
    recent = candles[-count:]
    sequence_values = {
        "consecutive_bullish": all(item.close > item.open for item in recent),
        "consecutive_bearish": all(item.close < item.open for item in recent),
        "consecutive_higher_closes": all(
            left.close < right.close for left, right in zip(recent[:-1], recent[1:], strict=True)
        ),
        "consecutive_lower_closes": all(
            left.close > right.close for left, right in zip(recent[:-1], recent[1:], strict=True)
        ),
        "gap_up": len(candles) >= 2 and candle.low > candles[-2].high,
        "gap_down": len(candles) >= 2 and candle.high < candles[-2].low,
    }
    if component in sequence_values:
        return float(sequence_values[component])
    raise KeyError(f"Unsupported candle anatomy component: {component}")


def distance_to_reference(
    candles: list[Candle],
    *,
    reference: str = "ema",
    period: int = 200,
    lookback: int = 20,
    field: str = "close",
) -> float:
    if not candles:
        raise IndicatorWarmupError("distance_to_reference requires candles")
    current = float(getattr(candles[-1], field))
    if reference == "ema":
        level = ema(candles, period=period, field=field)
    elif reference == "vwap":
        level = vwap(candles, period=period)
    elif reference in {"support", "swing_low"}:
        if len(candles) < lookback + 1:
            raise IndicatorWarmupError(f"distance_to_reference requires {lookback + 1} candles")
        level = min(candle.low for candle in candles[-lookback - 1 : -1])
    elif reference in {"resistance", "swing_high"}:
        if len(candles) < lookback + 1:
            raise IndicatorWarmupError(f"distance_to_reference requires {lookback + 1} candles")
        level = max(candle.high for candle in candles[-lookback - 1 : -1])
    else:
        raise KeyError(f"Unsupported distance reference: {reference}")
    if level == 0:
        raise IndicatorWarmupError("distance reference is zero")
    return ((current - level) / level) * 100


IndicatorFunction = Callable[..., float]


class IndicatorRegistry:
    def __init__(self) -> None:
        self._functions: dict[str, IndicatorFunction] = {
            "sma": sma,
            "ema": ema,
            "ema_slope": ema_slope,
            "sma_slope": sma_slope,
            "moving_average_distance_percent": moving_average_distance_percent,
            "atr": atr,
            "atr_percent": atr_percent,
            "average_volume": average_volume,
            "volume_ratio": volume_ratio,
            "relative_volume_slope": relative_volume_slope,
            "rsi": rsi,
            "macd": macd,
            "macd_histogram_delta": macd_histogram_delta,
            "bollinger_band": bollinger_band,
            "bollinger_bandwidth_percent": bollinger_bandwidth_percent,
            "bollinger_bandwidth_delta": bollinger_bandwidth_delta,
            "stochastic": stochastic,
            "vwap": vwap,
            "anchored_vwap": anchored_vwap,
            "vwap_deviation_percent": vwap_deviation_percent,
            "range_ratio": range_ratio,
            "expansion_ratio": expansion_ratio,
            "trend_strength": trend_strength,
            "pullback_depth_percent": pullback_depth_percent,
            "adx": adx,
            "stochastic_rsi": stochastic_rsi,
            "money_flow_index": money_flow_index,
            "commodity_channel_index": commodity_channel_index,
            "williams_percent_r": williams_percent_r,
            "rate_of_change": rate_of_change,
            "momentum": momentum,
            "true_strength_index": true_strength_index,
            "ultimate_oscillator": ultimate_oscillator,
            "relative_vigor_index": relative_vigor_index,
            "connors_rsi": connors_rsi,
            "weighted_moving_average": weighted_moving_average,
            "hull_moving_average": hull_moving_average,
            "double_exponential_moving_average": double_exponential_moving_average,
            "triple_exponential_moving_average": triple_exponential_moving_average,
            "kaufman_adaptive_moving_average": kaufman_adaptive_moving_average,
            "volume_weighted_moving_average": volume_weighted_moving_average,
            "linear_regression_moving_average": linear_regression_moving_average,
            "zero_lag_ema": zero_lag_ema,
            "moving_average_ribbon": moving_average_ribbon,
            "ichimoku_cloud": ichimoku_cloud,
            "supertrend": supertrend,
            "parabolic_sar": parabolic_sar,
            "aroon": aroon,
            "directional_movement": directional_movement,
            "elder_impulse": elder_impulse,
            "keltner_channel": keltner_channel,
            "donchian_channel": donchian_channel,
            "bollinger_percent_b": bollinger_percent_b,
            "squeeze_detection": squeeze_detection,
            "historical_volatility": historical_volatility,
            "normalized_atr": normalized_atr,
            "choppiness_index": choppiness_index,
            "ulcer_index": ulcer_index,
            "on_balance_volume": on_balance_volume,
            "chaikin_money_flow": chaikin_money_flow,
            "accumulation_distribution": accumulation_distribution,
            "ease_of_movement": ease_of_movement,
            "force_index": force_index,
            "volume_oscillator": volume_oscillator,
            "volume_profile_proxy": volume_profile_proxy,
            "relative_volume_by_session": relative_volume_by_session,
            "dollar_volume": dollar_volume,
            "buy_sell_pressure_proxy": buy_sell_pressure_proxy,
            "pivot_points": pivot_points,
            "candle_anatomy": candle_anatomy,
            "distance_to_reference": distance_to_reference,
        }

    def calculate(self, name: str, candles: list[Candle], **parameters) -> float:
        """One indicator reading, given only the settings that indicator has.

        A compiled rule carries two different things in one bag: the settings of the
        reading (``period``, ``field``) and facts about the **rule** around it — which
        way it is compared, on which candle size, against which number. Splatting the
        whole bag at the indicator meant every rule built from a registered indicator
        died on ``rsi() got an unexpected keyword argument 'threshold'``. "RSI above 70"
        — the most ordinary rule a beginner can ask for — could never be evaluated.

        So the rule's own facts are dropped here, by name, and everything else must be a
        setting the indicator really has. An unknown setting is refused rather than
        quietly ignored: ignoring it would read something the person did not ask for.
        """

        function = self._functions.get(name)
        if function is None:
            raise KeyError(f"Unsupported indicator: {name}")
        accepted = _accepted_arguments(function)
        unknown = sorted(
            key
            for key in parameters
            if key not in accepted and key not in RULE_PARAMETERS
        )
        if unknown:
            raise KeyError(f"{name} has no setting called {', '.join(unknown)}")
        return float(
            function(
                candles,
                **{key: value for key, value in parameters.items() if key in accepted},
            )
        )

    def supports(self, name: str) -> bool:
        return name in self._functions
