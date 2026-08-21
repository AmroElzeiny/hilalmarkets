"""A time card must offer what it reads, and must be able to answer both ways.

Every time condition used to be built from one template that gave it the same three
fields — ``timezone``, ``start_hour``, ``end_hour`` — no matter what the condition
actually read. Nothing caught it, because each half was consistent with itself: the form
offered three fields and the reader read three names, and they were simply not the same
three. The damage ran both ways.

**A field the reader wanted and the form never offered** was replaced by a default
inside the reader. ``day_of_week`` looked for ``days``, found nothing, and used ``[0]``.
So every "day of week" monitor anyone had ever built meant **Monday** — and the screen
that built it never showed a day at all.

**A field the form always sent** buried a preset the reader was holding. ``start_hour=0``
with ``end_hour=24`` is the whole day, so "Asia Session", "London Session" and "New York
Session" were three names for *always true*, and their real hours — including
Europe/London and America/New_York — could never be reached. The same two numbers turned
``avoid_low_liquidity_hours`` and ``session_expired`` into ``not (always true)``:
conditions that could **never** be met, on any candle, on any coin, for anybody.

``time_window`` and ``killzone_filter`` were declared with no fields at all and hit the
same fallback, so a filter whose whole job is to narrow the clock answered yes always.

The checks below are what the old code could not survive. They are written against the
family, not against the cards that were reported.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ai_market_monitor.engine.capabilities import capability_by_key
from ai_market_monitor.engine.context_conditions import (
    TIME_CONDITION_FIELDS,
    ContextDataUnavailable,
    evaluate_time_condition,
    time_condition_fields,
)
from ai_market_monitor.services.interfaces import Candle

#: Conditions that answer yes or no from the clock alone. Everything else in the time
#: family answers with a number (an opening price, minutes since something) or needs a
#: date the trader types, and neither is what this file is about.
CLOCK_CONDITIONS: tuple[str, ...] = (
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
    "first_n_minutes_of_session",
    "session_close_window",
    "last_n_minutes_of_session",
    "session_expired",
    "avoid_low_liquidity_hours",
    "avoid_daily_reset",
)

#: A whole week, every half hour. Any clock rule worth offering separates some of these
#: moments from the others.
WEEK: tuple[datetime, ...] = tuple(
    datetime(2026, 1, 5, tzinfo=UTC) + timedelta(minutes=30 * step) for step in range(7 * 48)
)


def _candles(moment: datetime) -> list[Candle]:
    return [
        Candle(
            timestamp=moment,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000.0,
            is_closed=True,
        )
    ]


#: A field the trader must state has no default by design, so this file states one. The
#: numbers are only "a real window somebody could have typed" — 08:00 to 16:00.
_CHOSEN: dict[str, Any] = {"start_hour": 8, "end_hour": 16}


def _settings(name: str) -> dict[str, Any]:
    """What the Builder would send with the form filled in."""

    values: dict[str, Any] = {}
    for field in time_condition_fields(name):
        if field.default is not None:
            values[field.name] = field.default
        elif field.options:
            values[field.name] = field.options[0]
        elif field.name in _CHOSEN:
            values[field.name] = _CHOSEN[field.name]
    return values


@pytest.mark.parametrize("name", sorted(TIME_CONDITION_FIELDS))
def test_the_form_offers_every_field_the_reader_reads(name: str) -> None:
    """The offer and the reading come from one table, so they cannot disagree."""

    capability = capability_by_key().get(name)
    if capability is None:
        pytest.skip(f"{name} is not a capability of its own")
    offered = {parameter.name for parameter in capability.parameters}
    needed = {field.name for field in time_condition_fields(name)}
    assert needed <= offered, (
        f"{name} reads {sorted(needed - offered)} but the form never offers it, so the "
        "reader substitutes its own default and the trader cannot see or change it."
    )


@pytest.mark.parametrize("name", CLOCK_CONDITIONS)
def test_a_clock_rule_can_answer_yes_and_can_answer_no(name: str) -> None:
    """The check that no version of the old code could pass.

    A rule that says yes at every moment of a week is not filtering anything. A rule
    that says no at every moment can never be met, so a monitor built on it is silent
    for ever.
    """

    settings = _settings(name)
    answers = {
        bool(evaluate_time_condition(name, _candles(moment), dict(settings), {}))
        for moment in WEEK
    }
    assert answers == {True, False}, (
        f"{name} answered {answers.pop()} at every one of {len(WEEK)} moments across a "
        "whole week. It cannot be filtering on the clock at all."
    )


@pytest.mark.parametrize(
    "name",
    sorted(
        name
        for name, fields in TIME_CONDITION_FIELDS.items()
        if any(field.required for field in fields)
    ),
)
def test_a_setting_with_no_answer_is_refused_not_guessed(name: str) -> None:
    """Fail closed. A rule nobody finished must never quietly mean something."""

    for field in time_condition_fields(name):
        if not field.required:
            continue
        settings = _settings(name)
        settings.pop(field.name, None)
        with pytest.raises(ContextDataUnavailable):
            evaluate_time_condition(name, _candles(WEEK[0]), settings, {})


def test_the_named_sessions_are_three_different_cards() -> None:
    """Asia, London and New York cannot all be the same answer all week."""

    sessions = ("asia_session", "london_session", "new_york_session")
    readings = {
        name: tuple(
            bool(evaluate_time_condition(name, _candles(moment), {}, {})) for moment in WEEK
        )
        for name in sessions
    }
    for first in range(len(sessions)):
        for second in range(first + 1, len(sessions)):
            left, right = sessions[first], sessions[second]
            assert readings[left] != readings[right], (
                f"{left} and {right} give the same answer at every moment of a week. "
                "They are being read on the same hours, so one of them is not its own "
                "session at all."
            )


@pytest.mark.parametrize("name", sorted(TIME_CONDITION_FIELDS))
def test_an_hour_field_says_which_hours_are_allowed(name: str) -> None:
    """An hour of the day is 0 to 24. The form used to accept 50, and 100."""

    for field in time_condition_fields(name):
        if not field.name.endswith("_hour"):
            continue
        assert field.minimum == 0 and field.maximum == 24, (
            f"{name}.{field.name} is an hour of the day but declares "
            f"{field.minimum}..{field.maximum}"
        )
