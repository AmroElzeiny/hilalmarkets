from __future__ import annotations

from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ai_market_monitor.engine.candle_patterns import detect_candle_pattern
from ai_market_monitor.engine.indicators import (
    IndicatorWarmupError,
    atr,
    ema,
    rsi,
    vwap,
)
from ai_market_monitor.engine.technical_patterns import (
    TECHNICAL_PATTERN_NAMES,
    evaluate_technical_pattern,
)
from ai_market_monitor.services.interfaces import Candle

PRICE_ACTION_NAMES = {
    "certified_dynamic",
    "breaks_n_candle_high",
    "breaks_n_candle_low",
    "closes_above_n_candle_high",
    "closes_below_n_candle_low",
    "wick_breaks_high_returns_below",
    "wick_breaks_low_returns_above",
    "breakout_with_volume_confirmation",
    "breakout_without_volume_confirmation",
    "bollinger_reentry",
    "failed_breakout",
    "failed_breakdown",
    "retest_after_breakout",
    "retest_after_breakdown",
    "break_and_retest_confirmed",
    "close_above_previous_day_high",
    "close_below_previous_day_low",
    "close_above_previous_week_high",
    "close_below_previous_week_low",
    "all_time_high_breakout",
    "n_day_high_breakout",
    "n_day_low_breakdown",
    "price_near_horizontal_level",
    "price_touches_level",
    "price_rejects_level",
    "price_closes_above_level",
    "price_closes_below_level",
    "price_bounces_from_support",
    "price_rejects_resistance",
    "multiple_touches_of_level",
    "support_becomes_resistance",
    "resistance_becomes_support",
    "level_strength_score",
    "level_distance_percent",
    "range_high_rejection",
    "range_low_rejection",
    "price_touches_trendline",
    "price_breaks_trendline",
    "price_bounces_from_trendline",
    "price_retests_broken_trendline",
    "dynamic_trendline",
    "auto_channel_upper_touch",
    "auto_channel_lower_touch",
    "auto_channel_breakout",
    "auto_channel_breakdown",
    "linear_regression_channel_touch",
    "linear_regression_channel_breakout",
    "market_structure_shift_bullish",
    "market_structure_shift_bearish",
    "higher_high",
    "higher_low",
    "lower_high",
    "lower_low",
    "internal_structure_break",
    "external_structure_break",
    "swing_high_formed",
    "swing_low_formed",
    "strong_swing_high",
    "strong_swing_low",
    "weak_high",
    "weak_low",
    "protected_high",
    "protected_low",
    "buy_side_liquidity_sweep",
    "sell_side_liquidity_sweep",
    "po3_dealing_range_sweep_bullish",
    "po3_dealing_range_sweep_bearish",
    "po3_sweep_displacement_bullish",
    "po3_sweep_displacement_bearish",
    "po3_sweep_displacement_structure_bullish",
    "po3_sweep_displacement_structure_bearish",
    "equal_highs_liquidity_pool",
    "equal_lows_liquidity_pool",
    "sweep_and_reclaim",
    "sweep_and_displacement",
    "liquidity_grab_close_inside",
    "stop_hunt_above_range",
    "stop_hunt_below_range",
    "previous_high_swept",
    "previous_low_swept",
    "session_high_swept",
    "session_low_swept",
    "daily_high_swept",
    "daily_low_swept",
    "reference_period_sweep",
    "weekly_high_swept",
    "weekly_low_swept",
    "bullish_fair_value_gap",
    "bearish_fair_value_gap",
    "price_enters_fvg",
    "price_fills_fvg",
    "price_rejects_fvg",
    "fvg_still_open",
    "fvg_still_open_bullish",
    "fvg_still_open_bearish",
    "fvg_virgin",
    "fvg_touched",
    "fvg_mid_mitigated",
    "fvg_fully_mitigated",
    "fvg_structure_invalidated",
    "fvg_midpoint_touched",
    "displacement_candle_bullish",
    "displacement_candle_bearish",
    "large_body_relative_to_atr",
    "wide_range_candle",
    "narrow_range_candle",
    "range_contraction_candle",
    "impulse_leg_detected",
    "correction_leg_detected",
    "bullish_order_block_candidate",
    "bearish_order_block_candidate",
    "price_returns_to_order_block",
    "order_block_mitigated",
    "order_block_invalidated",
    "order_block_rejection",
    "last_down_before_bullish_displacement",
    "last_up_before_bearish_displacement",
    "inside_range",
    "above_range",
    "below_range",
    "range_compression",
    "range_expansion",
    "breakout_from_consolidation",
    "breakdown_from_consolidation",
    "tight_consolidation",
    "sideways_market",
    "compression_before_breakout",
    "consecutive_inside_bars",
    "nr4_candle",
    "nr7_candle",
    "pullback_to_ema",
    "pullback_to_vwap",
    "pullback_to_breakout_level",
    "pullback_to_fibonacci_zone",
    "shallow_pullback",
    "deep_pullback",
    "pullback_with_declining_volume",
    "pullback_ending_reversal_candle",
    "trend_continuation_after_pullback",
    "rsi_divergence",
    "previous_session_high_low",
    *TECHNICAL_PATTERN_NAMES,
}


def supports_price_action(name: str | None) -> bool:
    return bool(name and name in PRICE_ACTION_NAMES)


def _prior(candles: list[Candle], lookback: int) -> tuple[Candle, list[Candle]]:
    if len(candles) < lookback + 1:
        raise IndicatorWarmupError(f"price action requires {lookback + 1} candles")
    return candles[-1], candles[-lookback - 1 : -1]


def _volume_ratio(candles: list[Candle], period: int = 20) -> float:
    if len(candles) < period + 1:
        raise IndicatorWarmupError(f"volume confirmation requires {period + 1} candles")
    average = sum(candle.volume for candle in candles[-period - 1 : -1]) / period
    return 0.0 if average == 0 else candles[-1].volume / average


def _period_reference(
    candles: list[Candle],
    *,
    unit: str,
    timezone: str,
) -> list[Candle]:
    zone = ZoneInfo(timezone)
    current = candles[-1].timestamp.astimezone(zone)
    if unit == "day":
        prior_day = current.date() - timedelta(days=1)
        result = [
            candle
            for candle in candles[:-1]
            if candle.timestamp.astimezone(zone).date() == prior_day
        ]
    elif unit == "week":
        current_week_start = current.date() - timedelta(days=current.weekday())
        prior_start = current_week_start - timedelta(days=7)
        result = [
            candle
            for candle in candles[:-1]
            if prior_start <= candle.timestamp.astimezone(zone).date() < current_week_start
        ]
    elif unit == "month":
        current_month_start = current.replace(day=1).date()
        previous_month = current_month_start - timedelta(days=1)
        result = [
            candle
            for candle in candles[:-1]
            if (
                candle.timestamp.astimezone(zone).year == previous_month.year
                and candle.timestamp.astimezone(zone).month == previous_month.month
            )
        ]
    else:
        raise ValueError(f"Unsupported period reference: {unit}")
    if not result:
        raise IndicatorWarmupError(f"previous {unit} candles unavailable")
    return result


def _level(parameters: dict[str, Any], prior: list[Candle]) -> float:
    if "level" in parameters:
        return float(parameters["level"])
    source = str(parameters.get("level_source", "range_high"))
    if source in {"support", "range_low", "low"}:
        return min(candle.low for candle in prior)
    if source in {"midpoint", "range_mid"}:
        return (max(candle.high for candle in prior) + min(candle.low for candle in prior)) / 2
    return max(candle.high for candle in prior)


def _linear_regression(values: list[float]) -> tuple[float, float, float]:
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
    residuals = [value - (intercept + slope * index) for index, value in enumerate(values)]
    deviation = (sum(value**2 for value in residuals) / len(residuals)) ** 0.5
    return intercept + slope * (count - 1), slope, deviation


def _swings(candles: list[Candle], pivot: int = 2) -> tuple[list[int], list[int]]:
    highs: list[int] = []
    lows: list[int] = []
    for index in range(pivot, len(candles) - pivot):
        window = candles[index - pivot : index + pivot + 1]
        if candles[index].high == max(item.high for item in window):
            highs.append(index)
        if candles[index].low == min(item.low for item in window):
            lows.append(index)
    return highs, lows


def _latest_fvg_detail(
    candles: list[Candle],
    direction: str,
    *,
    newest_index_max: int | None = None,
    search_start: int = 0,
) -> dict[str, Any] | None:
    if len(candles) < 3:
        return None
    newest_index_max = len(candles) - 1 if newest_index_max is None else newest_index_max
    newest_index_max = min(newest_index_max, len(candles) - 1)
    for index in range(newest_index_max, max(1, search_start), -1):
        first, _, third = candles[index - 2 : index + 1]
        if direction == "bullish" and third.low > first.high:
            return {
                "index": index,
                "direction": "bullish",
                "lower": first.high,
                "upper": third.low,
            }
        if direction == "bearish" and third.high < first.low:
            return {
                "index": index,
                "direction": "bearish",
                "lower": third.high,
                "upper": first.low,
            }
    return None


def _latest_fvg(candles: list[Candle], direction: str) -> tuple[float, float] | None:
    detail = _latest_fvg_detail(candles, direction)
    if detail is not None:
        return float(detail["lower"]), float(detail["upper"])
    return None


def _fvg_mitigation_state(
    candles: list[Candle],
    direction: str,
    *,
    lookback: int,
) -> str:
    search_start = max(0, len(candles) - lookback - 1)
    # State checks use the latest completed FVG before the current signal candle.
    detail = _latest_fvg_detail(
        candles,
        direction,
        newest_index_max=len(candles) - 2,
        search_start=search_start,
    )
    if detail is None:
        return "missing"
    index = int(detail["index"])
    lower = float(detail["lower"])
    upper = float(detail["upper"])
    width = max(upper - lower, 1e-12)
    eps = max(width * 0.04, 1e-12)
    midpoint = (lower + upper) / 2
    touched = False
    mid_mitigated = False
    fully_filled = False
    structure_invalidated = False

    for candle in candles[index + 1 :]:
        overlaps = candle.low <= upper and candle.high >= lower
        if overlaps:
            touched = True
        if candle.low <= midpoint <= candle.high:
            mid_mitigated = True
        if direction == "bullish":
            if candle.low < lower - eps:
                structure_invalidated = True
                break
            if candle.low <= lower + eps:
                fully_filled = True
        else:
            if candle.high > upper + eps:
                structure_invalidated = True
                break
            if candle.high >= upper - eps:
                fully_filled = True

    if structure_invalidated:
        return "structure_invalidated"
    if fully_filled:
        return "fully_mitigated"
    if mid_mitigated:
        return "mid_mitigated"
    if touched:
        return "touched"
    return "virgin"


def _candle_body_fraction(candle: Candle) -> float:
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return 0.0
    return abs(candle.close - candle.open) / candle_range


def _close_near_extreme_fraction(candle: Candle, want_long: bool) -> float:
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return 1.0
    if want_long:
        return (candle.high - candle.close) / candle_range
    return (candle.close - candle.low) / candle_range


def _average_prior_range(candles: list[Candle], index: int, count: int) -> float:
    start = max(0, index - count)
    prior = candles[start:index]
    if not prior:
        return 0.0
    return sum(candle.high - candle.low for candle in prior) / len(prior)


def _average_prior_volume(candles: list[Candle], index: int, count: int) -> float:
    start = max(0, index - count)
    prior = candles[start:index]
    if not prior:
        return 0.0
    return sum(max(candle.volume, 1.0) for candle in prior) / len(prior)


def _body_close_through_level(
    candle: Candle,
    level: float,
    want_long: bool,
    *,
    min_fraction: float,
) -> bool:
    body_hi = max(candle.open, candle.close)
    body_lo = min(candle.open, candle.close)
    body = body_hi - body_lo
    if body <= 0:
        return False
    if want_long:
        if candle.close <= level:
            return False
        beyond = body_hi - max(level, body_lo)
    else:
        if candle.close >= level:
            return False
        beyond = min(level, body_hi) - body_lo
    return beyond > 0 and beyond / body >= min_fraction


def _matches_po3_displacement(
    candles: list[Candle],
    index: int,
    want_long: bool,
    parameters: dict[str, Any],
) -> bool:
    if index <= 1 or index >= len(candles):
        return False
    history = candles[: index + 1]
    atr_period = min(int(parameters.get("atr_period", 14)), len(history) - 1)
    if atr_period < 2:
        return False
    atr_value = atr(history, period=atr_period)
    if atr_value <= 0:
        return False

    candle = candles[index]
    bullish = candle.close > candle.open
    direction_ok = bullish if want_long else not bullish
    if not direction_ok:
        return False

    body = abs(candle.close - candle.open)
    candle_range = candle.high - candle.low
    body_fraction = _candle_body_fraction(candle)
    recent_range = _average_prior_range(candles, index, int(parameters.get("recent_range_bars", 4)))
    recent_volume = _average_prior_volume(
        candles,
        index,
        int(parameters.get("recent_volume_bars", 6)),
    )
    range_vs_recent = candle_range / recent_range if recent_range > 0 else candle_range / atr_value
    volume_ratio = max(candle.volume, 1.0) / recent_volume if recent_volume > 0 else 1.0
    near_extreme = _close_near_extreme_fraction(candle, want_long)

    return (
        body >= atr_value * float(parameters.get("body_atr_min", 0.14))
        and candle_range >= atr_value * float(parameters.get("range_atr_min", 0.24))
        and body_fraction >= float(parameters.get("body_fraction_min", 0.14))
        and range_vs_recent >= float(parameters.get("range_vs_recent_min", 0.65))
        and near_extreme <= float(parameters.get("close_near_extreme_max", 0.65))
        and volume_ratio >= float(parameters.get("volume_ratio_min", 0.6))
    )


def _dealing_range_for_index(
    candles: list[Candle],
    index: int,
    lookback: int,
) -> tuple[float, float]:
    if index < lookback:
        raise IndicatorWarmupError(f"PO3 dealing range requires {lookback + 1} candles")
    prior = candles[index - lookback : index]
    return max(candle.high for candle in prior), min(candle.low for candle in prior)


def _po3_closed_sweep_at(
    candles: list[Candle],
    index: int,
    want_long: bool,
    parameters: dict[str, Any],
) -> bool:
    lookback = int(parameters.get("dealing_range_lookback", 5))
    dr_high, dr_low = _dealing_range_for_index(candles, index, lookback)
    candle = candles[index]
    midpoint = (dr_high + dr_low) / 2
    if midpoint <= 0:
        raise IndicatorWarmupError("PO3 dealing range midpoint is zero")
    max_range_percent = float(parameters.get("max_dealing_range_percent", 3.0))
    if max_range_percent > 0 and ((dr_high - dr_low) / midpoint) * 100 > max_range_percent:
        return False
    min_penetration_percent = float(parameters.get("min_penetration_percent", 0.0))
    if want_long:
        penetration = max(0.0, dr_low - candle.low)
        return (
            candle.low < dr_low
            and candle.close > dr_low
            and (
                min_penetration_percent <= 0
                or (penetration / max(dr_low, 1e-12)) * 100 >= min_penetration_percent
            )
        )
    penetration = max(0.0, candle.high - dr_high)
    return (
        candle.high > dr_high
        and candle.close < dr_high
        and (
            min_penetration_percent <= 0
            or (penetration / max(dr_high, 1e-12)) * 100 >= min_penetration_percent
        )
    )


def _po3_structure_shift_after(
    candles: list[Candle],
    anchor_index: int,
    want_long: bool,
    parameters: dict[str, Any],
) -> bool:
    span = int(parameters.get("structure_swing_span", 1))
    if anchor_index <= span * 2 + 1:
        return False
    prior = candles[:anchor_index]
    highs, lows = _swings(prior, pivot=span)
    swing_indexes = highs if want_long else lows
    if not swing_indexes:
        return False
    break_level = prior[swing_indexes[-1]].high if want_long else prior[swing_indexes[-1]].low
    min_fraction = float(parameters.get("structure_body_break_min_fraction", 0.03))
    require_follow = bool(parameters.get("require_structure_follow_through", False))
    for index in range(anchor_index + 1, len(candles)):
        if not _body_close_through_level(
            candles[index],
            break_level,
            want_long,
            min_fraction=min_fraction,
        ):
            continue
        if not require_follow:
            return True
        if index + 1 >= len(candles):
            return False
        follow = candles[index + 1]
        follows = (
            follow.close > follow.open
            and follow.close > break_level
            and follow.close >= candles[index].close
            if want_long
            else follow.close < follow.open
            and follow.close < break_level
            and follow.close <= candles[index].close
        )
        if follows and _candle_body_fraction(follow) >= 0.30:
            return True
    return False


def _po3_sequence_detected(
    candles: list[Candle],
    want_long: bool,
    parameters: dict[str, Any],
    *,
    require_displacement: bool,
    require_structure: bool,
) -> bool:
    lookback = int(parameters.get("dealing_range_lookback", 5))
    max_context_age = int(parameters.get("max_context_age_bars", 24))
    displacement_search_bars = int(parameters.get("displacement_search_bars", 60))
    start = max(lookback, len(candles) - max_context_age - displacement_search_bars - 2)
    for sweep_index in range(start, len(candles)):
        if not _po3_closed_sweep_at(candles, sweep_index, want_long, parameters):
            continue
        if not require_displacement:
            return True
        max_displacement_index = min(len(candles) - 1, sweep_index + displacement_search_bars)
        for displacement_index in range(sweep_index + 1, max_displacement_index + 1):
            if not _matches_po3_displacement(candles, displacement_index, want_long, parameters):
                continue
            if not require_structure or _po3_structure_shift_after(
                candles,
                displacement_index,
                want_long,
                parameters,
            ):
                return True
    return False


def evaluate_price_action(
    name: str,
    candles: list[Candle],
    parameters: dict[str, Any] | None = None,
) -> bool | float:
    parameters = parameters or {}
    if name == "certified_dynamic":
        from ai_market_monitor.engine.dynamic_mechanics import evaluate_serialized_expression

        return evaluate_serialized_expression(
            str(parameters.get("expression_json") or ""),
            candles,
            str(parameters.get("parameters_json") or "{}"),
        )
    if name in TECHNICAL_PATTERN_NAMES:
        return evaluate_technical_pattern(name, candles, parameters)
    lookback = int(parameters.get("lookback", 20))
    tolerance = float(parameters.get("tolerance_percent", 0.25)) / 100
    current, prior = _prior(candles, lookback)
    prior_high = max(candle.high for candle in prior)
    prior_low = min(candle.low for candle in prior)

    if name == "rsi_divergence":
        period = int(parameters.get("rsi_period", 14))
        pivot = int(parameters.get("pivot_bars", 2))
        direction = str(parameters.get("direction", "bullish"))
        highs, lows = _swings(candles[-lookback - 1 :], pivot=pivot)
        offset = len(candles) - len(candles[-lookback - 1 :])
        indexes = lows if direction == "bullish" else highs
        if len(indexes) < 2:
            raise IndicatorWarmupError("RSI divergence requires two confirmed swing points")
        first_index, second_index = indexes[-2] + offset, indexes[-1] + offset
        if first_index < period or second_index < period:
            raise IndicatorWarmupError(f"RSI divergence requires at least {period + 1} candles")
        first_rsi = rsi(candles[: first_index + 1], period=period)
        second_rsi = rsi(candles[: second_index + 1], period=period)
        if direction == "bearish":
            return candles[second_index].high > candles[first_index].high and second_rsi < first_rsi
        return candles[second_index].low < candles[first_index].low and second_rsi > first_rsi
    if name == "previous_session_high_low":
        timezone = str(parameters.get("timezone", "UTC"))
        zone = ZoneInfo(timezone)
        start_hour = float(parameters.get("start_hour", 0))
        end_hour = float(parameters.get("end_hour", 8))
        local_current = current.timestamp.astimezone(zone)
        session_date = (
            local_current.date()
            if local_current.hour + local_current.minute / 60 >= end_hour
            else local_current.date() - timedelta(days=1)
        )
        session = [
            candle
            for candle in candles[:-1]
            if candle.timestamp.astimezone(zone).date() == session_date
            and _inside_session_hour(
                candle.timestamp.astimezone(zone).hour
                + candle.timestamp.astimezone(zone).minute / 60,
                start_hour,
                end_hour,
            )
        ]
        if not session:
            raise IndicatorWarmupError("previous session candles unavailable")
        session_high = max(candle.high for candle in session)
        session_low = min(candle.low for candle in session)
        mode = str(parameters.get("mode", "break_high"))
        return {
            "break_high": current.close > session_high,
            "break_low": current.close < session_low,
            "sweep_high": current.high > session_high and current.close <= session_high,
            "sweep_low": current.low < session_low and current.close >= session_low,
            "inside": session_low <= current.close <= session_high,
        }.get(mode, False)

    if name in {"breaks_n_candle_high", "closes_above_n_candle_high"}:
        return current.close > prior_high
    if name in {"breaks_n_candle_low", "closes_below_n_candle_low"}:
        return current.close < prior_low
    if name == "wick_breaks_high_returns_below":
        return current.high > prior_high and current.close <= prior_high
    if name == "wick_breaks_low_returns_above":
        return current.low < prior_low and current.close >= prior_low
    if name in {
        "breakout_with_volume_confirmation",
        "breakout_without_volume_confirmation",
    }:
        confirmed = _volume_ratio(candles, int(parameters.get("volume_period", 20))) >= float(
            parameters.get("volume_multiplier", 1.5)
        )
        return current.close > prior_high and (
            confirmed if name.endswith("with_volume_confirmation") else not confirmed
        )
    if name == "failed_breakout":
        return current.high > prior_high and current.close < prior_high
    if name == "failed_breakdown":
        return current.low < prior_low and current.close > prior_low
    if name in {
        "retest_after_breakout",
        "break_and_retest_confirmed",
        "pullback_to_breakout_level",
    }:
        broke = any(candle.close > prior_high for candle in candles[-4:-1])
        return broke and current.low <= prior_high * (1 + tolerance) and current.close > prior_high
    if name == "retest_after_breakdown":
        broke = any(candle.close < prior_low for candle in candles[-4:-1])
        return broke and current.high >= prior_low * (1 - tolerance) and current.close < prior_low

    timezone = str(parameters.get("timezone", "UTC"))
    if name == "reference_period_sweep":
        unit = str(parameters.get("reference_period", "week")).casefold()
        side = str(parameters.get("side", "")).casefold()
        if unit not in {"day", "week", "month"} or side not in {"high", "low"}:
            raise ValueError("reference_period_sweep requires day/week/month and high/low")
        reference = _period_reference(candles, unit=unit, timezone=timezone)
        level = (
            max(candle.high for candle in reference)
            if side == "high"
            else min(candle.low for candle in reference)
        )
        return (
            current.high > level and current.close < level
            if side == "high"
            else current.low < level and current.close > level
        )
    if "previous_day" in name or name.startswith("daily_"):
        reference = _period_reference(candles, unit="day", timezone=timezone)
        high = max(candle.high for candle in reference)
        low = min(candle.low for candle in reference)
        if name == "close_above_previous_day_high":
            return current.close > high
        if name == "close_below_previous_day_low":
            return current.close < low
        if name == "daily_high_swept":
            return current.high > high and current.close < high
        if name == "daily_low_swept":
            return current.low < low and current.close > low
    if "previous_week" in name or name.startswith("weekly_"):
        reference = _period_reference(candles, unit="week", timezone=timezone)
        high = max(candle.high for candle in reference)
        low = min(candle.low for candle in reference)
        if name == "close_above_previous_week_high":
            return current.close > high
        if name == "close_below_previous_week_low":
            return current.close < low
        if name == "weekly_high_swept":
            return current.high > high and current.close < high
        if name == "weekly_low_swept":
            return current.low < low and current.close > low
    if name == "all_time_high_breakout":
        if len(candles) < 2:
            raise IndicatorWarmupError("all_time_high_breakout requires history")
        return current.close > max(candle.high for candle in candles[:-1])
    if name in {"n_day_high_breakout", "n_day_low_breakdown"}:
        days = int(parameters.get("days", 20))
        cutoff = current.timestamp - timedelta(days=days)
        reference = [candle for candle in candles[:-1] if candle.timestamp >= cutoff]
        if not reference:
            raise IndicatorWarmupError("N-day breakout reference unavailable")
        return (
            current.close > max(candle.high for candle in reference)
            if name == "n_day_high_breakout"
            else current.close < min(candle.low for candle in reference)
        )

    if "level" in name or name in {
        "price_bounces_from_support",
        "price_rejects_resistance",
        "support_becomes_resistance",
        "resistance_becomes_support",
    }:
        level = _level(parameters, prior)
        distance = 0.0 if level == 0 else ((current.close - level) / level) * 100
        touched = current.low <= level <= current.high
        if name == "level_distance_percent":
            return distance
        if name == "level_strength_score":
            touches = sum(
                candle.low <= level * (1 + tolerance) and candle.high >= level * (1 - tolerance)
                for candle in prior
            )
            return min(100.0, touches * 20.0)
        if name == "price_near_horizontal_level":
            return abs(distance) <= float(parameters.get("near_percent", 0.5))
        if name == "price_touches_level":
            return touched
        if name == "price_rejects_level":
            return touched and abs(distance) > float(parameters.get("rejection_percent", 0.1))
        if name == "price_closes_above_level":
            return current.close > level
        if name == "price_closes_below_level":
            return current.close < level
        if name == "price_bounces_from_support":
            return touched and current.close > level and current.close > current.open
        if name == "price_rejects_resistance":
            return touched and current.close < level and current.close < current.open
        if name == "multiple_touches_of_level":
            minimum = int(parameters.get("minimum_touches", 3))
            touches = sum(
                candle.low <= level * (1 + tolerance) and candle.high >= level * (1 - tolerance)
                for candle in prior
            )
            return touches >= minimum
        if name == "support_becomes_resistance":
            return (
                any(candle.close < level for candle in prior[-5:])
                and touched
                and current.close < level
            )
        if name == "resistance_becomes_support":
            return (
                any(candle.close > level for candle in prior[-5:])
                and touched
                and current.close > level
            )

    if name == "range_high_rejection":
        return current.high >= prior_high * (1 - tolerance) and current.close < prior_high
    if name == "range_low_rejection":
        return current.low <= prior_low * (1 + tolerance) and current.close > prior_low

    if (
        "trendline" in name
        or name.startswith("auto_channel")
        or name.startswith("linear_regression_channel")
    ):
        closes = [candle.close for candle in prior]
        line, slope, deviation = _linear_regression(closes)
        projection = line + slope
        multiplier = float(parameters.get("channel_deviations", 2))
        upper = projection + deviation * multiplier
        lower = projection - deviation * multiplier
        near = float(parameters.get("near_percent", 0.3)) / 100
        if name == "dynamic_trendline":
            return projection
        if name == "price_touches_trendline":
            return current.low <= projection * (1 + near) and current.high >= projection * (
                1 - near
            )
        if name == "price_breaks_trendline":
            direction = str(parameters.get("direction", "above"))
            return (
                current.close > projection if direction == "above" else current.close < projection
            )
        if name in {"price_bounces_from_trendline", "price_retests_broken_trendline"}:
            touched = current.low <= projection * (1 + near) and current.high >= projection * (
                1 - near
            )
            return touched and (
                current.close > projection if slope >= 0 else current.close < projection
            )
        if name in {"auto_channel_upper_touch", "linear_regression_channel_touch"}:
            return current.high >= upper * (1 - near)
        if name == "auto_channel_lower_touch":
            return current.low <= lower * (1 + near)
        if name in {"auto_channel_breakout", "linear_regression_channel_breakout"}:
            return current.close > upper
        if name == "auto_channel_breakdown":
            return current.close < lower

    highs, lows = _swings(candles[-lookback - 5 :], int(parameters.get("pivot_bars", 2)))
    swing_history = candles[-lookback - 5 :]
    if name in {
        "market_structure_shift_bullish",
        "market_structure_shift_bearish",
        "internal_structure_break",
        "external_structure_break",
    }:
        if not highs or not lows:
            raise IndicatorWarmupError("market structure requires confirmed swing points")
        latest_high = swing_history[highs[-1]].high
        latest_low = swing_history[lows[-1]].low
        if name in {"market_structure_shift_bullish", "external_structure_break"}:
            return current.close > latest_high
        if name == "market_structure_shift_bearish":
            return current.close < latest_low
        internal_high = max(candle.high for candle in prior[-max(3, lookback // 3) :])
        internal_low = min(candle.low for candle in prior[-max(3, lookback // 3) :])
        direction = str(parameters.get("direction", "bullish"))
        return (
            current.close > internal_high
            if direction == "bullish"
            else current.close < internal_low
        )
    if name in {"higher_high", "lower_high"}:
        if len(highs) < 2:
            raise IndicatorWarmupError("higher/lower high requires two confirmed swing highs")
        previous = swing_history[highs[-2]].high
        latest = swing_history[highs[-1]].high
        return latest > previous if name == "higher_high" else latest < previous
    if name in {"higher_low", "lower_low"}:
        if len(lows) < 2:
            raise IndicatorWarmupError("higher/lower low requires two confirmed swing lows")
        previous = swing_history[lows[-2]].low
        latest = swing_history[lows[-1]].low
        return latest > previous if name == "higher_low" else latest < previous
    if name in {
        "swing_high_formed",
        "strong_swing_high",
        "weak_high",
        "protected_high",
    }:
        if not highs:
            return False
        swing = swing_history[highs[-1]]
        if name == "swing_high_formed":
            return True
        strength = (swing.high - min(item.low for item in swing_history[highs[-1] :])) / max(
            atr(swing_history, period=min(14, len(swing_history) - 1)), 1e-12
        )
        if name == "strong_swing_high":
            return strength >= float(parameters.get("atr_strength", 1.5))
        if name == "weak_high":
            return strength < float(parameters.get("atr_strength", 1.0))
        return current.close < swing.high
    if name in {
        "swing_low_formed",
        "strong_swing_low",
        "weak_low",
        "protected_low",
    }:
        if not lows:
            return False
        swing = swing_history[lows[-1]]
        if name == "swing_low_formed":
            return True
        strength = (max(item.high for item in swing_history[lows[-1] :]) - swing.low) / max(
            atr(swing_history, period=min(14, len(swing_history) - 1)), 1e-12
        )
        if name == "strong_swing_low":
            return strength >= float(parameters.get("atr_strength", 1.5))
        if name == "weak_low":
            return strength < float(parameters.get("atr_strength", 1.0))
        return current.close > swing.low

    if name in {
        "buy_side_liquidity_sweep",
        "previous_high_swept",
        "stop_hunt_above_range",
    }:
        return current.high > prior_high and current.close < prior_high
    if name in {
        "sell_side_liquidity_sweep",
        "previous_low_swept",
        "stop_hunt_below_range",
    }:
        return current.low < prior_low and current.close > prior_low
    if name in {"po3_dealing_range_sweep_bullish", "po3_dealing_range_sweep_bearish"}:
        return _po3_closed_sweep_at(
            candles,
            len(candles) - 1,
            name.endswith("bullish"),
            parameters,
        )
    if name == "equal_highs_liquidity_pool":
        touches = sum(abs(candle.high - prior_high) / prior_high <= tolerance for candle in prior)
        return touches >= int(parameters.get("minimum_touches", 2))
    if name == "equal_lows_liquidity_pool":
        touches = sum(
            abs(candle.low - prior_low) / max(prior_low, 1e-12) <= tolerance for candle in prior
        )
        return touches >= int(parameters.get("minimum_touches", 2))
    if name in {"sweep_and_reclaim", "liquidity_grab_close_inside"}:
        direction = str(parameters.get("direction", "bullish"))
        return (
            current.low < prior_low and current.close > prior_low
            if direction == "bullish"
            else current.high > prior_high and current.close < prior_high
        )
    if name == "sweep_and_displacement":
        direction = str(parameters.get("direction", "bullish"))
        return _po3_sequence_detected(
            candles,
            direction == "bullish",
            parameters,
            require_displacement=True,
            require_structure=False,
        )
    if name in {"po3_sweep_displacement_bullish", "po3_sweep_displacement_bearish"}:
        return _po3_sequence_detected(
            candles,
            name.endswith("bullish"),
            parameters,
            require_displacement=True,
            require_structure=False,
        )
    if name in {
        "po3_sweep_displacement_structure_bullish",
        "po3_sweep_displacement_structure_bearish",
    }:
        return _po3_sequence_detected(
            candles,
            name.endswith("bullish"),
            parameters,
            require_displacement=True,
            require_structure=True,
        )
    if name in {"session_high_swept", "session_low_swept"}:
        zone = ZoneInfo(timezone)
        local = current.timestamp.astimezone(zone)
        start_hour = float(parameters.get("session_start_hour", 0))
        session = [
            candle
            for candle in candles[:-1]
            if candle.timestamp.astimezone(zone).date() == local.date()
            and candle.timestamp.astimezone(zone).hour >= start_hour
        ]
        if not session:
            raise IndicatorWarmupError("session sweep requires prior session candles")
        if name == "session_high_swept":
            level = max(candle.high for candle in session)
            return current.high > level and current.close < level
        level = min(candle.low for candle in session)
        return current.low < level and current.close > level

    if "fair_value_gap" in name:
        direction = "bullish" if name.startswith("bullish") else "bearish"
        return _latest_fvg(candles[-3:], direction) is not None
    if "fvg" in name:
        direction = (
            "bullish"
            if name.endswith("_bullish")
            else "bearish"
            if name.endswith("_bearish")
            else str(parameters.get("direction", "bullish"))
        )
        if name in {
            "fvg_still_open",
            "fvg_still_open_bullish",
            "fvg_still_open_bearish",
            "fvg_virgin",
            "fvg_touched",
            "fvg_mid_mitigated",
            "fvg_fully_mitigated",
            "fvg_structure_invalidated",
        }:
            state = _fvg_mitigation_state(candles, direction, lookback=lookback)
            expected_state = {
                "fvg_virgin": "virgin",
                "fvg_touched": "touched",
                "fvg_mid_mitigated": "mid_mitigated",
                "fvg_fully_mitigated": "fully_mitigated",
                "fvg_structure_invalidated": "structure_invalidated",
            }.get(name)
            if expected_state:
                return state == expected_state
            return state in {"virgin", "touched", "mid_mitigated"}
        gap = _latest_fvg(candles[:-1], direction)
        if gap is None:
            return False
        lower, upper = gap
        midpoint = (lower + upper) / 2
        if name == "price_enters_fvg":
            return current.low <= upper and current.high >= lower
        if name == "price_fills_fvg":
            return current.low <= lower if direction == "bullish" else current.high >= upper
        if name == "price_rejects_fvg":
            touched = current.low <= upper and current.high >= lower
            return touched and (
                current.close > upper if direction == "bullish" else current.close < lower
            )
        if name == "fvg_still_open":
            return all(
                candle.low > lower if direction == "bullish" else candle.high < upper
                for candle in candles[-lookback:]
            )
        if name == "fvg_midpoint_touched":
            return current.low <= midpoint <= current.high

    if name in {
        "displacement_candle_bullish",
        "displacement_candle_bearish",
        "large_body_relative_to_atr",
        "wide_range_candle",
        "narrow_range_candle",
        "range_contraction_candle",
    }:
        range_value = current.high - current.low
        body = abs(current.close - current.open)
        atr_value = atr(candles, period=int(parameters.get("atr_period", 14)))
        multiplier = float(parameters.get("atr_multiplier", 1.5))
        if name == "displacement_candle_bullish":
            return current.close > current.open and body >= atr_value * multiplier
        if name == "displacement_candle_bearish":
            return current.close < current.open and body >= atr_value * multiplier
        if name == "large_body_relative_to_atr":
            return body >= atr_value * multiplier
        if name == "wide_range_candle":
            return range_value >= atr_value * multiplier
        return range_value <= atr_value * float(parameters.get("maximum_atr_multiplier", 0.75))
    if name in {"impulse_leg_detected", "correction_leg_detected"}:
        period = int(parameters.get("leg_bars", 5))
        if len(candles) < period + 1:
            raise IndicatorWarmupError(f"leg detection requires {period + 1} candles")
        change = candles[-1].close - candles[-period - 1].close
        total = sum(
            abs(right.close - left.close)
            for left, right in zip(candles[-period - 1 : -1], candles[-period:], strict=True)
        )
        efficiency = 0.0 if total == 0 else abs(change) / total
        threshold = float(parameters.get("efficiency_threshold", 0.65))
        return efficiency >= threshold if name == "impulse_leg_detected" else efficiency < threshold

    if "order_block" in name or name.startswith("last_down") or name.startswith("last_up"):
        direction = "bullish" if "bullish" in name or "last_down" in name else "bearish"
        displacement_name = (
            "displacement_candle_bullish"
            if direction == "bullish"
            else "displacement_candle_bearish"
        )
        candidates = candles[-lookback - 1 : -1]
        found: Candle | None = None
        for index in range(len(candidates) - 1, 0, -1):
            candidate = candidates[index - 1]
            future = candidates[: index + 1]
            opposite = (
                candidate.close < candidate.open
                if direction == "bullish"
                else candidate.close > candidate.open
            )
            atr_period = min(int(parameters.get("atr_period", 14)), len(future) - 1)
            if atr_period < 2:
                continue
            displacement = future[-1]
            displacement_body = abs(displacement.close - displacement.open)
            displacement_direction = (
                displacement.close > displacement.open
                if displacement_name.endswith("bullish")
                else displacement.close < displacement.open
            )
            displaced = displacement_direction and displacement_body >= atr(
                future, period=atr_period
            ) * float(parameters.get("atr_multiplier", 1.5))
            if opposite and displaced:
                found = candidate
                break
        if found is None:
            return False
        if name in {
            "bullish_order_block_candidate",
            "bearish_order_block_candidate",
            "last_down_before_bullish_displacement",
            "last_up_before_bearish_displacement",
        }:
            return True
        touched = current.low <= found.high and current.high >= found.low
        if name == "price_returns_to_order_block":
            return touched
        if name == "order_block_mitigated":
            midpoint = (found.high + found.low) / 2
            return current.low <= midpoint <= current.high
        if name == "order_block_invalidated":
            return (
                current.close < found.low if direction == "bullish" else current.close > found.high
            )
        if name == "order_block_rejection":
            return touched and (
                current.close > found.high if direction == "bullish" else current.close < found.low
            )

    range_percent = 0.0 if prior_low == 0 else ((prior_high - prior_low) / prior_low) * 100
    if name == "inside_range":
        return prior_low <= current.close <= prior_high
    if name == "above_range":
        return current.close > prior_high
    if name == "below_range":
        return current.close < prior_low
    if name in {"range_compression", "tight_consolidation", "sideways_market"}:
        return range_percent <= float(parameters.get("maximum_range_percent", 5))
    if name == "range_expansion":
        previous_window = candles[-lookback * 2 - 1 : -lookback - 1]
        if len(previous_window) < lookback:
            raise IndicatorWarmupError(f"range expansion requires {lookback * 2 + 1} candles")
        previous_range = max(item.high for item in previous_window) - min(
            item.low for item in previous_window
        )
        return (prior_high - prior_low) > previous_range * float(
            parameters.get("expansion_multiplier", 1.25)
        )
    if name in {"breakout_from_consolidation", "compression_before_breakout"}:
        compressed = range_percent <= float(parameters.get("maximum_range_percent", 5))
        return compressed and current.close > prior_high
    if name == "breakdown_from_consolidation":
        compressed = range_percent <= float(parameters.get("maximum_range_percent", 5))
        return compressed and current.close < prior_low
    if name == "consecutive_inside_bars":
        count = int(parameters.get("count", 2))
        if len(candles) < count + 1:
            raise IndicatorWarmupError(f"consecutive inside bars requires {count + 1} candles")
        recent = candles[-count - 1 :]
        return all(
            child.high < parent.high and child.low > parent.low
            for parent, child in zip(recent[:-1], recent[1:], strict=True)
        )
    if name in {"nr4_candle", "nr7_candle"}:
        count = 4 if name == "nr4_candle" else 7
        if len(candles) < count:
            raise IndicatorWarmupError(f"{name} requires {count} candles")
        ranges = [candle.high - candle.low for candle in candles[-count:]]
        return ranges[-1] == min(ranges)

    if name in {"pullback_to_ema", "pullback_to_vwap"}:
        period = int(parameters.get("period", 20))
        value = (
            ema(candles, period=period)
            if name == "pullback_to_ema"
            else vwap(candles, period=period)
        )
        direction = str(parameters.get("direction", "long"))
        return (
            current.low <= value * (1 + tolerance) and current.close > value
            if direction == "long"
            else current.high >= value * (1 - tolerance) and current.close < value
        )
    if name == "pullback_to_fibonacci_zone":
        high = prior_high
        low = prior_low
        retracement = 0.0 if high == low else (high - current.close) / (high - low)
        return (
            float(parameters.get("minimum_retracement", 0.382))
            <= retracement
            <= float(parameters.get("maximum_retracement", 0.618))
        )
    if name in {"shallow_pullback", "deep_pullback"}:
        depth = (
            0.0
            if prior_high == prior_low
            else (prior_high - current.close) / (prior_high - prior_low)
        )
        threshold = float(parameters.get("depth_threshold", 0.382))
        return depth <= threshold if name == "shallow_pullback" else depth >= threshold
    if name == "pullback_with_declining_volume":
        bars = int(parameters.get("pullback_bars", 3))
        if len(candles) < bars + 1:
            raise IndicatorWarmupError(f"declining-volume pullback requires {bars + 1} candles")
        volumes = [candle.volume for candle in candles[-bars:]]
        return all(left > right for left, right in zip(volumes[:-1], volumes[1:], strict=True))
    if name == "pullback_ending_reversal_candle":
        pattern = str(parameters.get("pattern", "bullish_engulfing"))
        return detect_candle_pattern(pattern, candles, parameters)
    if name == "trend_continuation_after_pullback":
        average = ema(candles, period=int(parameters.get("period", 20)))
        direction = str(parameters.get("direction", "long"))
        reversal = detect_candle_pattern(
            "bullish_engulfing" if direction == "long" else "bearish_engulfing",
            candles,
            parameters,
        )
        return reversal and (
            current.close > average if direction == "long" else current.close < average
        )
    raise KeyError(f"Unsupported price action: {name}")


def _inside_session_hour(value: float, start: float, end: float) -> bool:
    return start <= value < end if start <= end else value >= start or value < end
