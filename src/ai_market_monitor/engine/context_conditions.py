from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class TimeConditionField:
    """One setting a time condition actually reads."""

    name: str
    type: str
    default: Any = None
    required: bool = False
    description: str = ""
    options: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None


_TIMEZONE = TimeConditionField(
    "timezone", "text", "UTC", description="Which clock to read the time on."
)
_START_HOUR = TimeConditionField(
    "start_hour",
    "number",
    0,
    description="Hour of the day the window opens, from 0 to 24.",
    minimum=0,
    maximum=24,
)
_END_HOUR = TimeConditionField(
    "end_hour",
    "number",
    24,
    description="Hour of the day the window closes, from 0 to 24.",
    minimum=0,
    maximum=24,
)
_DAYS = TimeConditionField(
    "days",
    "text",
    None,
    required=True,
    description="Which day of the week this rule is allowed to run on.",
    options=(
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ),
)
#: Sessions a window can be measured against. The values are the condition names
#: `_session_bounds` knows, so the form can never offer a session the reader cannot read.
SESSION_CHOICES: tuple[str, ...] = (
    "asia_session",
    "london_session",
    "new_york_session",
    "specific_utc_session",
)
_SESSION = TimeConditionField(
    "session",
    "text",
    None,
    required=True,
    description="Which trading session this window belongs to.",
    options=SESSION_CHOICES,
)
_MINUTES = TimeConditionField(
    "minutes",
    "number",
    30,
    description="How many minutes long the window is.",
    minimum=1,
    maximum=1440,
)

#: The three conditions below exist so a trader can name their own window, so the hours
#: are theirs to give and there is no sensible stand-in. They used to default to 0 and
#: 24 — the whole day — which meant a beginner who accepted the form as it opened got a
#: "time window" filter that let every candle through and looked like it was working.
_CHOSEN_START_HOUR = TimeConditionField(
    "start_hour",
    "number",
    None,
    required=True,
    description="Hour of the day the window opens, from 0 to 24.",
    minimum=0,
    maximum=24,
)
_CHOSEN_END_HOUR = TimeConditionField(
    "end_hour",
    "number",
    None,
    required=True,
    description="Hour of the day the window closes, from 0 to 24.",
    minimum=0,
    maximum=24,
)

_HOUR_WINDOW = (_TIMEZONE, _CHOSEN_START_HOUR, _CHOSEN_END_HOUR)

#: Conditions whose window is the trader's to state. Named here so the reader refuses
#: rather than standing in for them.
_TRADER_CHOSEN_WINDOWS: frozenset[str] = frozenset(
    {"time_window", "specific_hour_range", "specific_utc_session"}
)

#: The opposite: sessions whose hours are the session's own and were never a setting.
_PRESET_SESSIONS: frozenset[str] = frozenset(
    {"asia_session", "london_session", "new_york_session"}
)

#: **One owner for what each time condition needs.**
#:
#: Every condition below was previously given the same three fields — timezone,
#: start_hour and end_hour — whatever it actually read. The damage ran in both
#: directions and no test could see it, because each half was self-consistent:
#:
#: * A field the reader wanted but the form never offered was silently replaced by a
#:   default inside the reader. ``day_of_week`` read ``days`` and, finding nothing,
#:   fell back to ``[0]`` — so every "day of week" monitor anybody ever built meant
#:   **Monday**, with no way to see it or change it.
#: * A field the form always sent overrode a preset the reader held. ``start_hour=0``
#:   and ``end_hour=24`` are the whole day, so "London Session", "New York Session" and
#:   "Asia Session" all collapsed to *always true* and became the same card. The same
#:   two values turned ``avoid_low_liquidity_hours`` and ``session_expired`` into
#:   ``not (always true)`` — conditions that could **never** be met, on any candle, for
#:   any coin.
#:
#: `capabilities.py` builds the form from this table and `evaluate_time_condition`
#: reads exactly these names, so the offer and the reading cannot drift apart again.
TIME_CONDITION_FIELDS: dict[str, tuple[TimeConditionField, ...]] = {
    "day_of_week": (_TIMEZONE, _DAYS),
    "weekend_filter": (_TIMEZONE,),
    "weekday_only": (_TIMEZONE,),
    # These three are the ones that genuinely ask the trader for an hour window.
    "time_window": _HOUR_WINDOW,
    "specific_hour_range": _HOUR_WINDOW,
    "specific_utc_session": _HOUR_WINDOW,
    # Presets. Offering hours here is what made all three identical.
    "asia_session": (),
    "london_session": (),
    "new_york_session": (),
    # A window measured from the edge of a session the trader picks.
    "session_open_window": (_SESSION, _MINUTES),
    "first_n_minutes_of_session": (_SESSION, _MINUTES),
    "session_close_window": (_SESSION, _MINUTES),
    "last_n_minutes_of_session": (_SESSION, _MINUTES),
    "session_expired": (_SESSION,),
    "avoid_low_liquidity_hours": (
        _TIMEZONE,
        TimeConditionField(
            "start_hour",
            "number",
            21,
            description="Hour the quiet stretch starts, from 0 to 24.",
            minimum=0,
            maximum=24,
        ),
        TimeConditionField(
            "end_hour",
            "number",
            0,
            description="Hour the quiet stretch ends, from 0 to 24.",
            minimum=0,
            maximum=24,
        ),
    ),
    "avoid_daily_reset": (
        _TIMEZONE,
        TimeConditionField(
            "reset_hour",
            "number",
            0,
            description="The hour the exchange's day rolls over.",
            minimum=0,
            maximum=24,
        ),
        TimeConditionField(
            "buffer_minutes",
            "number",
            15,
            description="How many minutes either side of that hour to stay out of.",
            minimum=1,
            maximum=720,
        ),
    ),
}


def time_condition_fields(name: str) -> tuple[TimeConditionField, ...]:
    """The settings this time condition reads. Timezone only, unless stated above."""

    return TIME_CONDITION_FIELDS.get(name, (_TIMEZONE,))


def _is_number(value: Any) -> bool:
    """True for a real number a trader could have typed. A bool is not one."""

    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    try:
        float(str(value))
    except (TypeError, ValueError):
        return False
    return True


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
    if name in _PRESET_SESSIONS:
        # A named session's hours are the session's, not a setting. No screen has ever
        # offered these three fields for these three cards, so a stored value on one of
        # them can only be the 0/24/UTC the old form injected into every time rule — the
        # values that made Asia, London and New York the same "always yes" card. Ignoring
        # them here is what lets monitors saved before the fix heal on deploy instead of
        # keeping the old meaning until somebody rebuilds them by hand.
        return start, end, timezone
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
        expected = parameters.get("days", parameters.get("day"))
        # No day chosen is not "Monday". It used to be: the default `[0]` meant every
        # day-of-week monitor ever built ran on Mondays only, and the screen that built
        # it never showed a day at all. A rule whose meaning was never stated is refused
        # so the misunderstanding stays visible.
        if expected is None or expected == [] or expected == "":
            raise ContextDataUnavailable("this rule does not say which day of the week")
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
        if name in _TRADER_CHOSEN_WINDOWS and not (
            _is_number(parameters.get("start_hour")) and _is_number(parameters.get("end_hour"))
        ):
            raise ContextDataUnavailable("this rule does not say which hours the window covers")
        # `parameters` is passed through untouched on purpose. Merging the resolved
        # timezone in used to hand `_session_bounds` a `timezone` key on every call, so
        # its own presets — Europe/London for the London session, America/New_York for
        # New York — could never be reached and every session was read on UTC.
        start, end, zone = _session_bounds(name, parameters)
        return _inside_hours(_hour_value(timestamp, zone), start, end)
    if name in {
        "session_open_window",
        "first_n_minutes_of_session",
        "session_close_window",
        "last_n_minutes_of_session",
        "session_expired",
    }:
        session = str(parameters.get("session") or "")
        # "Which session?" has no sensible default. It used to fall back to
        # `specific_utc_session`, whose bounds are the whole day, which made
        # `session_expired` mean "outside the whole day" — false on every candle for
        # ever — and pinned every open/close window to midnight UTC.
        if session not in SESSION_CHOICES:
            raise ContextDataUnavailable("this rule does not say which trading session")
        start, end, zone = _session_bounds(session, parameters)
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
        raw_timestamp: Any = context.get(key)
        if name == "time_since_condition_true":
            condition_key = str(
                parameters.get("condition_key") or context.get("current_condition_key") or ""
            )
            first_true_by_condition = context.get("condition_first_true_at_by_key", {})
            if condition_key and isinstance(first_true_by_condition, dict):
                raw_timestamp = first_true_by_condition.get(condition_key, raw_timestamp)
            if raw_timestamp is None and isinstance(first_true_by_condition, dict):
                available = [
                    ensure_aware(datetime.fromisoformat(item))
                    if isinstance(item, str)
                    else ensure_aware(item)
                    for item in first_true_by_condition.values()
                    if isinstance(item, (str, datetime))
                ]
                raw_timestamp = max(available) if available else None
        if raw_timestamp is None:
            raise ContextDataUnavailable(f"{key} unavailable")
        if isinstance(raw_timestamp, str):
            parsed = ensure_aware(datetime.fromisoformat(raw_timestamp))
        elif isinstance(raw_timestamp, datetime):
            parsed = ensure_aware(raw_timestamp)
        else:
            raise ContextDataUnavailable(f"{key} is not a timestamp")
        return (timestamp - parsed).total_seconds() / 60
    if name in {
        "condition_before_timestamp",
        "condition_after_timestamp",
        "condition_valid_until",
    }:
        raw = parameters.get("timestamp")
        if not raw:
            raise ContextDataUnavailable("condition timestamp is required")
        try:
            target = ensure_aware(datetime.fromisoformat(str(raw)))
        except ValueError as exc:
            # A date nobody can read is a missing measurement, not a crash. Letting the
            # ValueError escape recorded the rule's failure as "ValueError", which tells
            # the person nothing about the date they typed.
            raise ContextDataUnavailable(
                f"condition timestamp is not a date we can read: {str(raw)[:40]}"
            ) from exc
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
        # A stated limit is the trader's own number and is honoured as written. Without
        # one, "not late" can only mean "nothing newer had closed" — the count the
        # freshness owner produces. The old default was a flat 60000 ms, which no
        # monitor slower than one minute could ever satisfy, so the condition was
        # permanently false for them and blocked every alert they would have sent.
        stated = parameters.get("maximum_lateness_ms", parameters.get("threshold"))
        if stated is None:
            return int(context.get("data_candles_behind") or 0) == 0
        return float(context.get("data_latency_ms", 0)) <= float(stated)
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
