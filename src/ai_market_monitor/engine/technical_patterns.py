from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_market_monitor.engine.indicators import IndicatorWarmupError
from ai_market_monitor.services.interfaces import Candle

TECHNICAL_PATTERN_NAMES = {
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


@dataclass(frozen=True, slots=True)
class PatternEvidence:
    formed: bool
    confirmed: bool
    boundary: float | None = None
    pivot_indexes: tuple[int, ...] = ()


#: What each setting of a chart pattern is allowed to be. **One owner.**
#:
#: These numbers were written inline at each call below, and nothing else knew them. The
#: Builder therefore drew a number box with no range on it, accepted whatever was typed,
#: and the refusal arrived at scan time as a bare ``ValueError`` — which reaches the
#: trader as a condition in the **error** state carrying no error code and no sentence
#: they could act on. A ``lookback`` of 5 and a ``pivot_bars`` of 20 are both things the
#: form invited somebody to enter.
#:
#: The registry declares the form's boxes from this table, so the range the form allows
#: and the range the reader enforces are the same range.
PATTERN_PARAMETER_RANGES: dict[str, tuple[float, float]] = {
    "lookback": (12, 500),
    "pivot_bars": (1, 8),
    "shoulder_tolerance_percent": (0.1, 25.0),
    "head_prominence_percent": (0.05, 25.0),
    "maximum_spacing_ratio": (1.0, 8.0),
    "breakout_buffer_percent": (0.0, 5.0),
    "level_tolerance_percent": (0.1, 15.0),
    "minimum_depth_percent": (0.05, 30.0),
    "flat_slope_percent_per_bar": (0.001, 2.0),
    "minimum_slope_percent_per_bar": (0.001, 2.0),
}


def _setting(parameters: dict[str, Any], name: str, fallback: float) -> float:
    """One setting, refused rather than clamped when it is outside its own range."""

    low, high = PATTERN_PARAMETER_RANGES[name]
    return _bounded_float(parameters.get(name, fallback), low, high)


def _count_setting(parameters: dict[str, Any], name: str, fallback: int) -> int:
    low, high = PATTERN_PARAMETER_RANGES[name]
    return _bounded_int(parameters.get(name, fallback), int(low), int(high))


def evaluate_technical_pattern(
    name: str,
    candles: list[Candle],
    parameters: dict[str, Any] | None = None,
) -> bool:
    if name not in TECHNICAL_PATTERN_NAMES:
        raise ValueError(f"Unsupported technical pattern: {name}")
    parameters = parameters or {}
    lookback = _count_setting(parameters, "lookback", 80)
    pivot_bars = _count_setting(parameters, "pivot_bars", 2)
    minimum = max(12, pivot_bars * 6 + 3)
    if len(candles) < minimum:
        raise IndicatorWarmupError(
            f"{name.replace('_', ' ')} requires at least {minimum} closed candles"
        )
    history = candles[-min(len(candles), lookback) :]

    if name.startswith("head_and_shoulders"):
        evidence = _head_and_shoulders(history, parameters, inverse=False)
        return evidence.confirmed if name.endswith("neckline_break") else evidence.formed
    if name.startswith("inverse_head_and_shoulders"):
        evidence = _head_and_shoulders(history, parameters, inverse=True)
        return evidence.confirmed if name.endswith("neckline_break") else evidence.formed
    if name == "double_top_neckline_break":
        return _double_pattern(history, parameters, bottom=False).confirmed
    if name == "double_bottom_neckline_break":
        return _double_pattern(history, parameters, bottom=True).confirmed
    return _triangle(history, parameters, name).confirmed


def _head_and_shoulders(
    candles: list[Candle],
    parameters: dict[str, Any],
    *,
    inverse: bool,
) -> PatternEvidence:
    pivot_bars = _count_setting(parameters, "pivot_bars", 2)
    shoulder_tolerance = _setting(parameters, "shoulder_tolerance_percent", 5.0)
    head_prominence = _setting(parameters, "head_prominence_percent", 1.0)
    maximum_spacing_ratio = _setting(parameters, "maximum_spacing_ratio", 3.0)
    breakout_buffer = _setting(parameters, "breakout_buffer_percent", 0.0) / 100
    highs, lows = _pivot_indexes(candles, pivot_bars)
    pivots = lows if inverse else highs
    if len(pivots) < 3:
        return PatternEvidence(False, False)

    # Evaluate the latest confirmed triples first. Restricting this to adjacent pivots avoids
    # silently skipping an intervening shoulder and calling a different structure the pattern.
    for first, head, third in reversed(list(zip(pivots, pivots[1:], pivots[2:], strict=False))):
        if third >= len(candles) - pivot_bars:
            continue
        if head - first < 2 or third - head < 2:
            continue
        left_value = candles[first].low if inverse else candles[first].high
        head_value = candles[head].low if inverse else candles[head].high
        right_value = candles[third].low if inverse else candles[third].high
        shoulder_mean = (left_value + right_value) / 2
        if shoulder_mean <= 0:
            continue
        shoulder_difference = abs(left_value - right_value) / shoulder_mean * 100
        if shoulder_difference > shoulder_tolerance:
            continue
        prominence = (
            (min(left_value, right_value) - head_value) / shoulder_mean * 100
            if inverse
            else (head_value - max(left_value, right_value)) / shoulder_mean * 100
        )
        if prominence < head_prominence:
            continue
        left_width = head - first
        right_width = third - head
        spacing_ratio = max(left_width, right_width) / max(1, min(left_width, right_width))
        if spacing_ratio > maximum_spacing_ratio:
            continue

        first_neck_index, first_neck = _neck_anchor(candles, first, head, inverse=inverse)
        second_neck_index, second_neck = _neck_anchor(candles, head, third, inverse=inverse)
        boundary = _project_line(
            first_neck_index,
            first_neck,
            second_neck_index,
            second_neck,
            len(candles) - 1,
        )
        close = candles[-1].close
        confirmed = (
            close > boundary * (1 + breakout_buffer)
            if inverse
            else close < boundary * (1 - breakout_buffer)
        )
        return PatternEvidence(
            formed=True,
            confirmed=confirmed,
            boundary=boundary,
            pivot_indexes=(first, head, third),
        )
    return PatternEvidence(False, False)


def _double_pattern(
    candles: list[Candle],
    parameters: dict[str, Any],
    *,
    bottom: bool,
) -> PatternEvidence:
    pivot_bars = _count_setting(parameters, "pivot_bars", 2)
    level_tolerance = _setting(parameters, "level_tolerance_percent", 2.0)
    minimum_depth = _setting(parameters, "minimum_depth_percent", 1.0)
    breakout_buffer = _setting(parameters, "breakout_buffer_percent", 0.0) / 100
    highs, lows = _pivot_indexes(candles, pivot_bars)
    pivots = lows if bottom else highs
    if len(pivots) < 2:
        return PatternEvidence(False, False)
    for first, second in reversed(list(zip(pivots, pivots[1:], strict=False))):
        if second >= len(candles) - pivot_bars or second - first < pivot_bars * 2:
            continue
        first_value = candles[first].low if bottom else candles[first].high
        second_value = candles[second].low if bottom else candles[second].high
        mean_level = (first_value + second_value) / 2
        if mean_level <= 0 or abs(first_value - second_value) / mean_level * 100 > level_tolerance:
            continue
        middle = candles[first + 1 : second]
        if not middle:
            continue
        boundary = max(item.high for item in middle) if bottom else min(item.low for item in middle)
        depth = (
            (boundary - mean_level) / mean_level * 100
            if bottom
            else (mean_level - boundary) / mean_level * 100
        )
        if depth < minimum_depth:
            continue
        confirmed = (
            candles[-1].close > boundary * (1 + breakout_buffer)
            if bottom
            else candles[-1].close < boundary * (1 - breakout_buffer)
        )
        return PatternEvidence(True, confirmed, boundary, (first, second))
    return PatternEvidence(False, False)


def _triangle(
    candles: list[Candle],
    parameters: dict[str, Any],
    name: str,
) -> PatternEvidence:
    pivot_bars = _count_setting(parameters, "pivot_bars", 2)
    flat_tolerance = _setting(parameters, "flat_slope_percent_per_bar", 0.15)
    minimum_slope = _setting(parameters, "minimum_slope_percent_per_bar", 0.02)
    breakout_buffer = _setting(parameters, "breakout_buffer_percent", 0.0) / 100
    highs, lows = _pivot_indexes(candles, pivot_bars)
    if len(highs) < 2 or len(lows) < 2:
        return PatternEvidence(False, False)
    high_points = [(index, candles[index].high) for index in highs[-3:]]
    low_points = [(index, candles[index].low) for index in lows[-3:]]
    high_boundary, high_slope = _fit_line(high_points, len(candles) - 1)
    low_boundary, low_slope = _fit_line(low_points, len(candles) - 1)
    high_slope_percent = high_slope / max(1e-12, _mean(value for _, value in high_points)) * 100
    low_slope_percent = low_slope / max(1e-12, _mean(value for _, value in low_points)) * 100
    converging = high_boundary > low_boundary

    if name == "ascending_triangle_breakout":
        formed = abs(high_slope_percent) <= flat_tolerance and low_slope_percent >= minimum_slope
        confirmed = formed and candles[-1].close > high_boundary * (1 + breakout_buffer)
        boundary = high_boundary
    elif name == "descending_triangle_breakdown":
        formed = abs(low_slope_percent) <= flat_tolerance and high_slope_percent <= -minimum_slope
        confirmed = formed and candles[-1].close < low_boundary * (1 - breakout_buffer)
        boundary = low_boundary
    else:
        formed = (
            converging
            and high_slope_percent <= -minimum_slope
            and low_slope_percent >= minimum_slope
        )
        if name == "symmetrical_triangle_breakout":
            confirmed = formed and candles[-1].close > high_boundary * (1 + breakout_buffer)
            boundary = high_boundary
        else:
            confirmed = formed and candles[-1].close < low_boundary * (1 - breakout_buffer)
            boundary = low_boundary
    return PatternEvidence(
        formed,
        confirmed,
        boundary,
        tuple(index for index, _ in [*high_points, *low_points]),
    )


def _pivot_indexes(candles: list[Candle], span: int) -> tuple[list[int], list[int]]:
    highs: list[int] = []
    lows: list[int] = []
    for index in range(span, len(candles) - span):
        current = candles[index]
        neighbours = candles[index - span : index] + candles[index + 1 : index + span + 1]
        if all(current.high >= item.high for item in neighbours) and any(
            current.high > item.high for item in neighbours
        ):
            highs.append(index)
        if all(current.low <= item.low for item in neighbours) and any(
            current.low < item.low for item in neighbours
        ):
            lows.append(index)
    return highs, lows


def _neck_anchor(
    candles: list[Candle],
    start: int,
    end: int,
    *,
    inverse: bool,
) -> tuple[int, float]:
    indexes = range(start + 1, end)
    if inverse:
        index = max(indexes, key=lambda item: candles[item].high)
        return index, candles[index].high
    index = min(indexes, key=lambda item: candles[item].low)
    return index, candles[index].low


def _project_line(
    first_index: int,
    first_value: float,
    second_index: int,
    second_value: float,
    target_index: int,
) -> float:
    slope = (second_value - first_value) / max(1, second_index - first_index)
    return second_value + slope * (target_index - second_index)


def _fit_line(points: list[tuple[int, float]], target_index: int) -> tuple[float, float]:
    x_mean = _mean(index for index, _ in points)
    y_mean = _mean(value for _, value in points)
    denominator = sum((index - x_mean) ** 2 for index, _ in points)
    slope = (
        sum((index - x_mean) * (value - y_mean) for index, value in points) / denominator
        if denominator
        else 0.0
    )
    intercept = y_mean - slope * x_mean
    return intercept + slope * target_index, slope


def _mean(values: Any) -> float:
    collected = list(values)
    return sum(collected) / len(collected)


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
    result = int(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"Pattern integer must be between {minimum} and {maximum}")
    return result


def _bounded_float(value: Any, minimum: float, maximum: float) -> float:
    result = float(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"Pattern value must be between {minimum} and {maximum}")
    return result
