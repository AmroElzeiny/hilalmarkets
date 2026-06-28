from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ai_market_monitor.engine.models import ensure_aware
from ai_market_monitor.services.interfaces import Candle


class ContextDataUnavailable(LookupError):
    pass


TIME_CONDITION_NAMES = {
    "day_of_week",
    "weekend_filter",
    "weekday_only",
    "time_window",
    "specific_hour_range",
    "specific_utc_session",
    "asia_session",
    "london_session",
    "new_york_session",
    "session_open_window",
    "session_close_window",
    "first_n_minutes_of_session",
    "last_n_minutes_of_session",
    "avoid_low_liquidity_hours",
    "avoid_daily_reset",
    "monthly_open",
    "weekly_open",
    "daily_open",
    "new_day_breakout",
    "new_week_breakout",
    "time_since_last_alert",
    "time_since_setup_detected",
    "time_since_condition_true",
    "condition_before_timestamp",
    "condition_after_timestamp",
    "condition_valid_until",
    "session_expired",
}


def _hour_value(timestamp: datetime, timezone: str) -> float:
    local = ensure_aware(timestamp).astimezone(ZoneInfo(timezone))
    return local.hour + local.minute / 60 + local.second / 3600


def _inside_hours(value: float, start: float, end: float) -> bool:
    return start <= value < end if start <= end else value >= start or value < end


def _session_bounds(name: str, parameters: dict[str, Any]) -> tuple[float, float, str]:
    defaults = {
        "time_window": (0.0, 24.0, "UTC"),
        "asia_session": (0.0, 8.0, "UTC"),
        "london_session": (8.0, 16.0, "Europe/London"),
        "new_york_session": (9.5, 16.0, "America/New_York"),
        "specific_utc_session": (0.0, 24.0, "UTC"),
    }
    start, end, timezone = defaults.get(name, (0.0, 24.0, "UTC"))
    return (
        float(parameters.get("start_hour", start)),
        float(parameters.get("end_hour", end)),
        str(parameters.get("timezone", timezone)),
    )


def evaluate_time_condition(
    name: str,
    candles: list[Candle],
    parameters: dict[str, Any],
    context: dict[str, Any],
) -> bool | float:
    if not candles:
        raise ContextDataUnavailable("candle timestamp unavailable")
    timestamp = ensure_aware(candles[-1].timestamp)
    timezone = str(parameters.get("timezone", context.get("timezone", "UTC")))
    local = timestamp.astimezone(ZoneInfo(timezone))
    if name == "day_of_week":
        expected = parameters.get("days", parameters.get("day", [0]))
        if not isinstance(expected, list):
            expected = [expected]
        normalized = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        days = {
            normalized.get(str(day).casefold(), int(day) if str(day).isdigit() else -1)
            for day in expected
        }
        return local.weekday() in days
    if name == "weekend_filter":
        return local.weekday() >= 5
    if name == "weekday_only":
        return local.weekday() < 5
    if name in {
        "specific_hour_range",
        "time_window",
        "specific_utc_session",
        "asia_session",
        "london_session",
        "new_york_session",
    }:
        start, end, zone = _session_bounds(name, {**parameters, "timezone": timezone})
        return _inside_hours(_hour_value(timestamp, zone), start, end)
    if name in {
        "session_open_window",
        "first_n_minutes_of_session",
        "session_close_window",
        "last_n_minutes_of_session",
        "session_expired",
    }:
        start, end, zone = _session_bounds(
            str(parameters.get("session", "specific_utc_session")),
            {**parameters, "timezone": timezone},
        )
        value = _hour_value(timestamp, zone)
        minutes = float(parameters.get("minutes", parameters.get("window_minutes", 30))) / 60
        if name in {"session_open_window", "first_n_minutes_of_session"}:
            return _inside_hours(value, start, start + minutes)
        if name in {"session_close_window", "last_n_minutes_of_session"}:
            return _inside_hours(value, end - minutes, end)
        return not _inside_hours(value, start, end)
    if name == "avoid_low_liquidity_hours":
        start = float(parameters.get("start_hour", 21))
        end = float(parameters.get("end_hour", 0))
        return not _inside_hours(_hour_value(timestamp, timezone), start, end)
    if name == "avoid_daily_reset":
        reset_hour = float(parameters.get("reset_hour", 0))
        buffer_minutes = float(parameters.get("buffer_minutes", 15)) / 60
        value = _hour_value(timestamp, timezone)
        distance = min(abs(value - reset_hour), 24 - abs(value - reset_hour))
        return distance > buffer_minutes
    if name in {"daily_open", "weekly_open", "monthly_open"}:
        unit = {
            "daily_open": "day",
            "weekly_open": "week",
            "monthly_open": "monthly",
        }[name]
        period_candles = _current_period_candles(candles, unit, timezone)
        return period_candles[0].open
    if name in {"new_day_breakout", "new_week_breakout"}:
        unit = "day" if name == "new_day_breakout" else "week"
        current_period = _current_period_candles(candles, unit, timezone)
        prior = [candle for candle in candles if candle.timestamp < current_period[0].timestamp]
        if not prior:
            raise ContextDataUnavailable(f"previous {unit} history unavailable")
        if unit == "day":
            prior_date = prior[-1].timestamp.astimezone(ZoneInfo(timezone)).date()
            reference = [
                candle
                for candle in prior
                if candle.timestamp.astimezone(ZoneInfo(timezone)).date() == prior_date
            ]
        else:
            prior_local = prior[-1].timestamp.astimezone(ZoneInfo(timezone))
            prior_week = prior_local.isocalendar()[:2]
            reference = [
                candle
                for candle in prior
                if candle.timestamp.astimezone(ZoneInfo(timezone)).isocalendar()[:2] == prior_week
            ]
        return candles[-1].close > max(candle.high for candle in reference)
    if name in {
        "time_since_last_alert",
        "time_since_setup_detected",
        "time_since_condition_true",
    }:
        key = {
            "time_since_last_alert": "last_triggered_at",
            "time_since_setup_detected": "setup_first_detected_at",
            "time_since_condition_true": "condition_first_true_at",
        }[name]
        value = context.get(key)
        if name == "time_since_condition_true":
            condition_key = str(
                parameters.get("condition_key")
                or context.get("current_condition_key")
                or ""
            )
            first_true_by_condition = context.get("condition_first_true_at_by_key", {})
            if condition_key and isinstance(first_true_by_condition, dict):
                value = first_true_by_condition.get(condition_key, value)
            if value is None and isinstance(first_true_by_condition, dict):
                available = list(first_true_by_condition.values())
                value = max(available) if available else None
        if value is None:
            raise ContextDataUnavailable(f"{key} unavailable")
        parsed = (
            ensure_aware(datetime.fromisoformat(value))
            if isinstance(value, str)
            else ensure_aware(value)
        )
        return (timestamp - parsed).total_seconds() / 60
    if name in {
        "condition_before_timestamp",
        "condition_after_timestamp",
        "condition_valid_until",
    }:
        raw = parameters.get("timestamp")
        if not raw:
            raise ContextDataUnavailable("condition timestamp is required")
        target = ensure_aware(datetime.fromisoformat(str(raw)))
        if name == "condition_after_timestamp":
            return timestamp >= target
        return timestamp <= target
    raise KeyError(f"Unsupported time condition: {name}")


def _current_period_candles(
    candles: list[Candle],
    unit: str,
    timezone: str,
) -> list[Candle]:
    zone = ZoneInfo(timezone)
    current = candles[-1].timestamp.astimezone(zone)
    if unit == "day":
        result = [
            candle
            for candle in candles
            if candle.timestamp.astimezone(zone).date() == current.date()
        ]
    elif unit == "week":
        key = current.isocalendar()[:2]
        result = [
            candle
            for candle in candles
            if candle.timestamp.astimezone(zone).isocalendar()[:2] == key
        ]
    elif unit == "monthly":
        result = [
            candle
            for candle in candles
            if (
                candle.timestamp.astimezone(zone).year,
                candle.timestamp.astimezone(zone).month,
            )
            == (current.year, current.month)
        ]
    else:
        raise ValueError(f"Unsupported time unit: {unit}")
    if not result:
        raise ContextDataUnavailable(f"current {unit} candles unavailable")
    return result


def context_metric(
    name: str,
    parameters: dict[str, Any],
    context: dict[str, Any],
) -> Any:
    category = str(parameters.get("context_category", "market_context"))
    if category in {"alert_behavior", "setup_lifecycle"}:
        return runtime_context_metric(name, parameters, context)
    values = context.get(category, {})
    if name in values:
        if values[name] is None:
            raise ContextDataUnavailable(f"{name} is unavailable")
        return values[name]
    provider = parameters.get("provider")
    suffix = f" from {provider}" if provider else ""
    raise ContextDataUnavailable(f"{name}{suffix} is unavailable")


def runtime_context_metric(
    name: str,
    parameters: dict[str, Any],
    context: dict[str, Any],
) -> Any:
    evaluated_at = context.get("evaluation_time")
    if evaluated_at is None:
        raise ContextDataUnavailable("evaluation_time unavailable")
    evaluated_at = (
        ensure_aware(datetime.fromisoformat(evaluated_at))
        if isinstance(evaluated_at, str)
        else ensure_aware(evaluated_at)
    )
    if name in {"same_symbol_alert_cooldown", "same_strategy_alert_cooldown"}:
        key = (
            "last_symbol_triggered_at"
            if name == "same_symbol_alert_cooldown"
            else "last_strategy_triggered_at"
        )
        previous = context.get(key)
        if previous is None:
            return True
        previous = (
            ensure_aware(datetime.fromisoformat(previous))
            if isinstance(previous, str)
            else ensure_aware(previous)
        )
        cooldown_minutes = float(parameters.get("cooldown_minutes", 60))
        return (evaluated_at - previous).total_seconds() >= cooldown_minutes * 60
    if name == "maximum_alerts_per_hour_condition":
        return int(context.get("alerts_last_hour", 0)) < int(
            parameters.get("maximum_alerts", parameters.get("threshold", 50))
        )
    if name == "daily_alert_budget_condition":
        maximum = parameters.get("daily_budget", parameters.get("threshold"))
        if maximum is None:
            return True
        return int(context.get("alerts_last_day", 0)) < int(maximum)
    if name == "alert_only_on_state_change":
        return bool(context.get("setup_state_changed", True))
    if name == "maximum_alert_lateness_condition":
        return float(context.get("data_latency_ms", 0)) <= float(
            parameters.get("maximum_lateness_ms", parameters.get("threshold", 60_000))
        )
    if name == "setup_state_is":
        expected = str(parameters.get("state", parameters.get("expected_state", "forming")))
        actual = context.get("setup_state")
        if actual is None:
            raise ContextDataUnavailable("setup_state unavailable")
        return str(actual) == expected
    if name in {"setup_age_minutes", "setup_first_detected_within"}:
        first = context.get("setup_first_detected_at")
        if first is None:
            raise ContextDataUnavailable("setup_first_detected_at unavailable")
        first = (
            ensure_aware(datetime.fromisoformat(first))
            if isinstance(first, str)
            else ensure_aware(first)
        )
        age = (evaluated_at - first).total_seconds() / 60
        if name == "setup_age_minutes":
            return age
        return age <= float(parameters.get("minutes", parameters.get("threshold", 60)))
    if name == "setup_entry_zone_active":
        return bool(context.get("setup_entry_zone_active", False))
    if name == "setup_not_invalidated":
        if context.get("setup_state") is None:
            raise ContextDataUnavailable("setup_state unavailable")
        return str(context.get("setup_state")) != "invalidated"
    if name == "setup_not_expired":
        expires_at = context.get("setup_expires_at")
        if expires_at is None:
            if context.get("setup_state") is None:
                raise ContextDataUnavailable("setup_state unavailable")
            return str(context.get("setup_state")) != "expired"
        expires_at = (
            ensure_aware(datetime.fromisoformat(expires_at))
            if isinstance(expires_at, str)
            else ensure_aware(expires_at)
        )
        return evaluated_at < expires_at and str(context.get("setup_state") or "") != "expired"
    raise ContextDataUnavailable(f"{name} runtime context is unavailable")
