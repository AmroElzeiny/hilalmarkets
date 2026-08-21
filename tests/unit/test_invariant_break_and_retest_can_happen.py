"""A "break the level, then come back to it" card must be able to say yes.

The four readings here — break and retest confirmed, retest after breakout, pullback to
the breakout level, retest after breakdown — all asked the same question:

    did one of the last three candles close **above** the highest high of the window?

The window they measured against *contained those three candles*. A candle's close is
never above its own high, and its high is never above the highest high of a window it
belongs to. So the answer was no. Always. On every candle, of every coin, on every
timeframe, for every trader who ever picked one of these four cards.

Nothing in the product could see it. The cards were offered, they compiled, they
evaluated without error, and they returned a perfectly ordinary "no" for ever. Only
asking "can this ever be true?" finds a defect shaped like that.

The level has to come from the window **before** the candles that may have broken it,
and that is what these cases pin down.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_market_monitor.engine.price_action import evaluate_price_action
from ai_market_monitor.services.interfaces import Candle

_START = datetime(2026, 1, 1, tzinfo=UTC)

#: The three readings that describe breaking *up* through a level and coming back to it.
UPWARD_RETESTS = (
    "break_and_retest_confirmed",
    "retest_after_breakout",
    "pullback_to_breakout_level",
)

_SETTINGS = {"tolerance_percent": 1.0}


def _candles(rows: list[tuple[float, float, float, float, float]]) -> list[Candle]:
    return [
        Candle(
            timestamp=_START + timedelta(minutes=15 * index),
            open=row[0],
            high=row[1],
            low=row[2],
            close=row[3],
            volume=row[4],
            is_closed=True,
        )
        for index, row in enumerate(rows)
    ]


def _quiet_range(count: int = 50) -> list[tuple[float, float, float, float, float]]:
    """A flat range whose ceiling is 100.20 and whose floor is 99.80."""

    return [(100.0, 100.2, 99.8, 100.0, 1000.0)] * count


def _break_then_retest() -> list[Candle]:
    """Range, a candle that breaks the ceiling, a drift back to it, then a close above."""

    return _candles(
        [
            *_quiet_range(),
            (100.0, 102.5, 99.9, 102.3, 9000.0),  # the break
            (102.3, 102.6, 101.0, 101.4, 2000.0),
            (101.4, 101.6, 100.15, 100.9, 1800.0),  # drifts back to the old ceiling
            (100.9, 101.9, 100.21, 101.8, 4000.0),  # touches it and closes above
        ]
    )


def _break_then_keep_going() -> list[Candle]:
    """The same break, but price never comes back to the level."""

    return _candles(
        [
            *_quiet_range(),
            (100.0, 102.5, 99.9, 102.3, 9000.0),
            (102.3, 103.4, 102.2, 103.2, 2000.0),
            (103.2, 104.5, 103.1, 104.4, 1800.0),
            (104.4, 105.6, 104.3, 105.5, 4000.0),
        ]
    )


def _quiet_only() -> list[Candle]:
    """Nothing breaks at all."""

    return _candles(_quiet_range(60))


@pytest.mark.parametrize("name", UPWARD_RETESTS)
def test_a_textbook_break_and_retest_is_recognised(name: str) -> None:
    """The whole point of the card. This was impossible before."""

    assert evaluate_price_action(name, _break_then_retest(), dict(_SETTINGS)) is True


def test_a_break_downwards_and_retest_is_recognised() -> None:
    """The same shape, mirrored: break the floor, come back up to it, close below."""

    rows = _candles(
        [
            *_quiet_range(),
            (100.0, 100.1, 97.5, 97.7, 9000.0),  # breaks the 99.80 floor
            (97.7, 99.0, 97.4, 98.6, 2000.0),
            (98.6, 99.7, 98.4, 99.5, 1800.0),  # drifts back up to it
            (99.5, 99.79, 98.5, 98.9, 4000.0),  # touches it and closes below
        ]
    )
    assert evaluate_price_action("retest_after_breakdown", rows, dict(_SETTINGS)) is True


@pytest.mark.parametrize("name", (*UPWARD_RETESTS, "retest_after_breakdown"))
@pytest.mark.parametrize("market", ("quiet", "ran_away"))
def test_it_still_says_no_when_there_was_no_retest(name: str, market: str) -> None:
    """Being able to say yes is only worth anything if it can still say no."""

    candles = _quiet_only() if market == "quiet" else _break_then_keep_going()
    assert evaluate_price_action(name, candles, dict(_SETTINGS)) is False


@pytest.mark.parametrize("name", (*UPWARD_RETESTS, "retest_after_breakdown"))
def test_the_level_is_never_taken_from_the_candles_that_broke_it(name: str) -> None:
    """The arithmetic that made these four impossible, stated directly.

    If the level were taken from a window that includes the breaking candles, then no
    candle could ever close beyond it, and no market at all could produce a yes. One
    market that produces a yes is the whole proof.
    """

    upward = name != "retest_after_breakdown"
    candles = (
        _break_then_retest()
        if upward
        else _candles(
            [
                *_quiet_range(),
                (100.0, 100.1, 97.5, 97.7, 9000.0),
                (97.7, 99.0, 97.4, 98.6, 2000.0),
                (98.6, 99.7, 98.4, 99.5, 1800.0),
                (99.5, 99.79, 98.5, 98.9, 4000.0),
            ]
        )
    )
    assert evaluate_price_action(name, candles, dict(_SETTINGS)) is True
