from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_market_monitor.engine.indicators import IndicatorWarmupError
from ai_market_monitor.services.interfaces import Candle


@dataclass(frozen=True, slots=True)
class CandleShape:
    body: float
    candle_range: float
    upper_wick: float
    lower_wick: float
    body_percent: float
    upper_wick_percent: float
    lower_wick_percent: float
    close_position: float
    bullish: bool
    bearish: bool


PATTERN_DIRECTIONS: dict[str, str] = {
    "bullish_engulfing": "bullish",
    "bearish_engulfing": "bearish",
    "hammer": "bullish",
    "hanging_man": "bearish",
    "shooting_star": "bearish",
    "inverted_hammer": "bullish",
    "dragonfly_doji": "bullish",
    "gravestone_doji": "bearish",
    "spinning_top_bullish": "bullish",
    "spinning_top_bearish": "bearish",
    "marubozu_bullish": "bullish",
    "marubozu_bearish": "bearish",
    "morning_star": "bullish",
    "evening_star": "bearish",
    "morning_doji_star": "bullish",
    "evening_doji_star": "bearish",
    "piercing_pattern": "bullish",
    "dark_cloud_cover": "bearish",
    "bullish_harami": "bullish",
    "bearish_harami": "bearish",
    "harami_cross_bullish": "bullish",
    "harami_cross_bearish": "bearish",
    "tweezer_top": "bearish",
    "tweezer_bottom": "bullish",
    "three_white_soldiers": "bullish",
    "three_black_crows": "bearish",
    "rising_three_methods": "bullish",
    "falling_three_methods": "bearish",
    "upside_tasuki_gap": "bullish",
    "downside_tasuki_gap": "bearish",
    "kicking_bullish": "bullish",
    "kicking_bearish": "bearish",
    "abandoned_baby_bullish": "bullish",
    "abandoned_baby_bearish": "bearish",
    "three_inside_up": "bullish",
    "three_inside_down": "bearish",
    "three_outside_up": "bullish",
    "three_outside_down": "bearish",
    "belt_hold_bullish": "bullish",
    "belt_hold_bearish": "bearish",
    "matching_low": "bullish",
    "on_neck_bearish": "bearish",
    "in_neck_bearish": "bearish",
    "thrusting_pattern": "bearish",
    "separating_lines_bullish": "bullish",
    "separating_lines_bearish": "bearish",
    "long_upper_shadow": "bearish",
    "long_lower_shadow": "bullish",
}

PATTERN_BARS: dict[str, int] = {
    "morning_star": 3,
    "evening_star": 3,
    "morning_doji_star": 3,
    "evening_doji_star": 3,
    "three_white_soldiers": 3,
    "three_black_crows": 3,
    "upside_tasuki_gap": 3,
    "downside_tasuki_gap": 3,
    "abandoned_baby_bullish": 3,
    "abandoned_baby_bearish": 3,
    "three_inside_up": 3,
    "three_inside_down": 3,
    "three_outside_up": 3,
    "three_outside_down": 3,
    "rising_three_methods": 5,
    "falling_three_methods": 5,
}


def pattern_names() -> tuple[str, ...]:
    basics = {
        "bullish_engulfing",
        "bearish_engulfing",
        "hammer",
        "hanging_man",
        "shooting_star",
        "inverted_hammer",
        "doji",
        "dragonfly_doji",
        "gravestone_doji",
        "long_legged_doji",
        "spinning_top_bullish",
        "spinning_top_bearish",
        "marubozu_bullish",
        "marubozu_bearish",
        "inside_bar",
        "outside_bar",
        "pin_bar",
        "strong_close_near_high",
        "strong_close_near_low",
        "bullish_candle",
        "bearish_candle",
        "green_candle",
        "red_candle",
    }
    return tuple(sorted(basics | set(PATTERN_DIRECTIONS)))


def candle_shape(candle: Candle) -> CandleShape:
    candle_range = candle.high - candle.low
    body = abs(candle.close - candle.open)
    upper = candle.high - max(candle.open, candle.close)
    lower = min(candle.open, candle.close) - candle.low
    if candle_range <= 0:
        return CandleShape(body, 0, upper, lower, 0, 0, 0, 50, False, False)
    return CandleShape(
        body=body,
        candle_range=candle_range,
        upper_wick=upper,
        lower_wick=lower,
        body_percent=(body / candle_range) * 100,
        upper_wick_percent=(upper / candle_range) * 100,
        lower_wick_percent=(lower / candle_range) * 100,
        close_position=((candle.close - candle.low) / candle_range) * 100,
        bullish=candle.close > candle.open,
        bearish=candle.close < candle.open,
    )


def _near(left: float, right: float, tolerance_percent: float = 0.15) -> bool:
    reference = max(abs(left), abs(right), 1e-12)
    return abs(left - right) / reference <= tolerance_percent / 100


def _trend(candles: list[Candle], direction: str, period: int = 5) -> bool:
    if len(candles) < period + 1:
        raise IndicatorWarmupError(f"trend context requires {period + 1} candles")
    start = candles[-period - 1].close
    end = candles[-1].close
    return end > start if direction == "up" else end < start


def _body_inside(inner: Candle, outer: Candle) -> bool:
    inner_low, inner_high = sorted((inner.open, inner.close))
    outer_low, outer_high = sorted((outer.open, outer.close))
    return inner_low >= outer_low and inner_high <= outer_high


def _single_pattern(name: str, candle: Candle, parameters: dict[str, Any]) -> bool:
    shape = candle_shape(candle)
    strength = str(parameters.get("pattern_strength", "medium"))
    body_min = float(parameters.get("min_body_percent", 45 if strength == "strong" else 25))
    body_max = float(parameters.get("max_body_percent", 12 if "doji" in name else 40))
    wick_ratio = float(parameters.get("wick_ratio", 2.0 if strength != "strong" else 2.5))
    if name in {"green_candle", "bullish_candle"}:
        return shape.bullish
    if name in {"red_candle", "bearish_candle"}:
        return shape.bearish
    if name == "doji":
        return shape.body_percent <= body_max
    if name == "dragonfly_doji":
        return (
            shape.body_percent <= body_max
            and shape.lower_wick >= max(shape.body, 1e-12) * wick_ratio
            and shape.upper_wick_percent <= 10
        )
    if name == "gravestone_doji":
        return (
            shape.body_percent <= body_max
            and shape.upper_wick >= max(shape.body, 1e-12) * wick_ratio
            and shape.lower_wick_percent <= 10
        )
    if name == "long_legged_doji":
        return (
            shape.body_percent <= body_max
            and shape.upper_wick_percent >= 30
            and shape.lower_wick_percent >= 30
        )
    if name in {"hammer", "hanging_man", "pin_bar"}:
        return (
            shape.body_percent <= 40
            and shape.lower_wick >= max(shape.body, 1e-12) * wick_ratio
            and shape.upper_wick_percent <= 20
        )
    if name in {"shooting_star", "inverted_hammer"}:
        return (
            shape.body_percent <= 40
            and shape.upper_wick >= max(shape.body, 1e-12) * wick_ratio
            and shape.lower_wick_percent <= 20
        )
    if name == "spinning_top_bullish":
        return (
            shape.bullish
            and shape.body_percent <= body_max
            and min(shape.upper_wick_percent, shape.lower_wick_percent) >= 20
        )
    if name == "spinning_top_bearish":
        return (
            shape.bearish
            and shape.body_percent <= body_max
            and min(shape.upper_wick_percent, shape.lower_wick_percent) >= 20
        )
    if name == "marubozu_bullish":
        return (
            shape.bullish
            and shape.body_percent >= max(body_min, 80)
            and shape.upper_wick_percent <= 8
            and shape.lower_wick_percent <= 8
        )
    if name == "marubozu_bearish":
        return (
            shape.bearish
            and shape.body_percent >= max(body_min, 80)
            and shape.upper_wick_percent <= 8
            and shape.lower_wick_percent <= 8
        )
    if name == "belt_hold_bullish":
        return shape.bullish and shape.body_percent >= body_min and shape.lower_wick_percent <= 5
    if name == "belt_hold_bearish":
        return shape.bearish and shape.body_percent >= body_min and shape.upper_wick_percent <= 5
    if name == "long_upper_shadow":
        return shape.upper_wick >= max(shape.body, 1e-12) * wick_ratio
    if name == "long_lower_shadow":
        return shape.lower_wick >= max(shape.body, 1e-12) * wick_ratio
    if name == "strong_close_near_high":
        return shape.close_position >= float(parameters.get("minimum_close_percent", 75))
    if name == "strong_close_near_low":
        return shape.close_position <= float(parameters.get("maximum_close_percent", 25))
    return False


def _two_candle_pattern(
    name: str,
    previous: Candle,
    current: Candle,
    parameters: dict[str, Any],
) -> bool:
    prior_shape = candle_shape(previous)
    shape = candle_shape(current)
    midpoint = (previous.open + previous.close) / 2
    tolerance = float(parameters.get("tolerance_percent", 0.15))
    if name == "bullish_engulfing":
        return (
            prior_shape.bearish
            and shape.bullish
            and current.close >= previous.open
            and current.open <= previous.close
        )
    if name == "bearish_engulfing":
        return (
            prior_shape.bullish
            and shape.bearish
            and current.open >= previous.close
            and current.close <= previous.open
        )
    if name == "inside_bar":
        return current.high < previous.high and current.low > previous.low
    if name == "outside_bar":
        return current.high > previous.high and current.low < previous.low
    if name == "piercing_pattern":
        return (
            prior_shape.bearish
            and shape.bullish
            and current.close > midpoint
            and current.close < previous.open
        )
    if name == "dark_cloud_cover":
        return (
            prior_shape.bullish
            and shape.bearish
            and current.close < midpoint
            and current.close > previous.open
        )
    if name == "bullish_harami":
        return prior_shape.bearish and shape.bullish and _body_inside(current, previous)
    if name == "bearish_harami":
        return prior_shape.bullish and shape.bearish and _body_inside(current, previous)
    if name == "harami_cross_bullish":
        return prior_shape.bearish and shape.body_percent <= 12 and _body_inside(current, previous)
    if name == "harami_cross_bearish":
        return prior_shape.bullish and shape.body_percent <= 12 and _body_inside(current, previous)
    if name == "tweezer_top":
        return (
            prior_shape.bullish and shape.bearish and _near(previous.high, current.high, tolerance)
        )
    if name == "tweezer_bottom":
        return prior_shape.bearish and shape.bullish and _near(previous.low, current.low, tolerance)
    if name == "kicking_bullish":
        return (
            _single_pattern("marubozu_bearish", previous, parameters)
            and _single_pattern("marubozu_bullish", current, parameters)
            and current.low > previous.high
        )
    if name == "kicking_bearish":
        return (
            _single_pattern("marubozu_bullish", previous, parameters)
            and _single_pattern("marubozu_bearish", current, parameters)
            and current.high < previous.low
        )
    if name == "matching_low":
        return (
            prior_shape.bearish
            and shape.bearish
            and _near(previous.close, current.close, tolerance)
        )
    if name == "on_neck_bearish":
        return (
            prior_shape.bearish and shape.bullish and _near(current.close, previous.low, tolerance)
        )
    if name == "in_neck_bearish":
        return prior_shape.bearish and shape.bullish and previous.low < current.close < midpoint
    if name == "thrusting_pattern":
        return prior_shape.bearish and shape.bullish and previous.close < current.close < midpoint
    if name == "separating_lines_bullish":
        return (
            prior_shape.bearish and shape.bullish and _near(previous.open, current.open, tolerance)
        )
    if name == "separating_lines_bearish":
        return (
            prior_shape.bullish and shape.bearish and _near(previous.open, current.open, tolerance)
        )
    return False


def _three_candle_pattern(name: str, candles: list[Candle], parameters: dict[str, Any]) -> bool:
    first, second, third = candles[-3:]
    first_shape, second_shape, third_shape = map(candle_shape, (first, second, third))
    midpoint = (first.open + first.close) / 2
    small_middle = second_shape.body_percent <= float(parameters.get("max_body_percent", 30))
    if name in {"morning_star", "morning_doji_star"}:
        doji_required = name == "morning_doji_star"
        return (
            first_shape.bearish
            and small_middle
            and (not doji_required or second_shape.body_percent <= 12)
            and third_shape.bullish
            and third.close > midpoint
        )
    if name in {"evening_star", "evening_doji_star"}:
        doji_required = name == "evening_doji_star"
        return (
            first_shape.bullish
            and small_middle
            and (not doji_required or second_shape.body_percent <= 12)
            and third_shape.bearish
            and third.close < midpoint
        )
    if name == "three_white_soldiers":
        return all(candle_shape(item).bullish for item in candles[-3:]) and (
            first.close < second.close < third.close
        )
    if name == "three_black_crows":
        return all(candle_shape(item).bearish for item in candles[-3:]) and (
            first.close > second.close > third.close
        )
    if name == "upside_tasuki_gap":
        return (
            first_shape.bullish
            and second_shape.bullish
            and second.low > first.high
            and third_shape.bearish
            and first.high < third.close < second.open
        )
    if name == "downside_tasuki_gap":
        return (
            first_shape.bearish
            and second_shape.bearish
            and second.high < first.low
            and third_shape.bullish
            and second.open < third.close < first.low
        )
    if name == "abandoned_baby_bullish":
        return (
            first_shape.bearish
            and second_shape.body_percent <= 12
            and second.high < first.low
            and third_shape.bullish
            and third.low > second.high
        )
    if name == "abandoned_baby_bearish":
        return (
            first_shape.bullish
            and second_shape.body_percent <= 12
            and second.low > first.high
            and third_shape.bearish
            and third.high < second.low
        )
    if name == "three_inside_up":
        return (
            _two_candle_pattern("bullish_harami", first, second, parameters)
            and third.close > first.open
        )
    if name == "three_inside_down":
        return (
            _two_candle_pattern("bearish_harami", first, second, parameters)
            and third.close < first.open
        )
    if name == "three_outside_up":
        return (
            _two_candle_pattern("bullish_engulfing", first, second, parameters)
            and third.close > second.close
        )
    if name == "three_outside_down":
        return (
            _two_candle_pattern("bearish_engulfing", first, second, parameters)
            and third.close < second.close
        )
    return False


def _five_candle_pattern(name: str, candles: list[Candle], parameters: dict[str, Any]) -> bool:
    first, *middle, last = candles[-5:]
    first_shape = candle_shape(first)
    last_shape = candle_shape(last)
    if name == "rising_three_methods":
        return (
            first_shape.bullish
            and all(first.low < item.low and item.high < first.high for item in middle)
            and last_shape.bullish
            and last.close > first.close
        )
    if name == "falling_three_methods":
        return (
            first_shape.bearish
            and all(first.low < item.low and item.high < first.high for item in middle)
            and last_shape.bearish
            and last.close < first.close
        )
    return False


def detect_candle_pattern(
    name: str,
    candles: list[Candle],
    parameters: dict[str, Any] | None = None,
) -> bool:
    parameters = parameters or {}
    confirmation_required = bool(parameters.get("confirmation_required", False))
    required = PATTERN_BARS.get(
        name,
        2
        if name
        not in {
            "green_candle",
            "red_candle",
            "doji",
            "dragonfly_doji",
            "gravestone_doji",
            "long_legged_doji",
            "hammer",
            "hanging_man",
            "shooting_star",
            "inverted_hammer",
            "pin_bar",
            "spinning_top_bullish",
            "spinning_top_bearish",
            "marubozu_bullish",
            "marubozu_bearish",
            "belt_hold_bullish",
            "belt_hold_bearish",
            "long_upper_shadow",
            "long_lower_shadow",
            "strong_close_near_high",
            "strong_close_near_low",
        }
        else 1,
    )
    total_required = required + (1 if confirmation_required else 0)
    if len(candles) < total_required:
        raise IndicatorWarmupError(f"{name} requires {total_required} candles")
    evaluation = candles[:-1] if confirmation_required else candles
    if required == 1:
        matched = _single_pattern(name, evaluation[-1], parameters)
    elif required == 2:
        matched = _two_candle_pattern(name, evaluation[-2], evaluation[-1], parameters)
    elif required == 3:
        matched = _three_candle_pattern(name, evaluation, parameters)
    else:
        matched = _five_candle_pattern(name, evaluation, parameters)
    if not matched:
        return False
    direction = str(parameters.get("direction") or PATTERN_DIRECTIONS.get(name, "neutral"))
    if bool(parameters.get("trend_context_required", False)):
        context = evaluation[:-required]
        if direction == "bullish" and not _trend(context, "down"):
            return False
        if direction == "bearish" and not _trend(context, "up"):
            return False
    if confirmation_required:
        confirmation = candle_shape(candles[-1])
        if direction == "bullish" and not confirmation.bullish:
            return False
        if direction == "bearish" and not confirmation.bearish:
            return False
    return True
