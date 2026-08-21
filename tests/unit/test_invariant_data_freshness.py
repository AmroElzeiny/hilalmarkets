"""Reading the newest candle there is must count as fresh, on every candle period.

The product told the owner of a five-minute monitor "the prices it read were not the
newest ones" on every single check, for weeks, while the monitor was reading the newest
closed candle every time. Four separate readers said it, and all four said it the same
wrong way: they measured from the candle's **opening** time and compared the result to a
fixed budget written for tick data — full marks under five seconds.

A candle's opening is one whole candle older than the candle. So the measured delay for a
five-minute monitor was never below five minutes, never below the one-minute threshold,
and the verdict was never "fresh" — for any monitor slower than a minute, which is all of
them in practice.

The rule is therefore asserted for **every supported period**, not for five minutes:

* a scan that read the newest closed candle is current, whatever the period;
* a scan that missed candles is behind by exactly the number it missed;
* and the graders that turn this into a score agree with the count.

A fix that only helped five-minute monitors would pass none of the loops below.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_market_monitor.engine.data_freshness import (
    candle_lateness_ms,
    measure_freshness,
    timeframe_duration,
    timeframe_minutes,
    timeframe_ms,
)
from ai_market_monitor.engine.quality import _data_freshness_factor
from ai_market_monitor.schemas.timeframes import ORDERED_TIMEFRAMES, TIMEFRAME_MINUTES

NOW = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
def test_the_newest_closed_candle_is_never_late(timeframe: str) -> None:
    """The candle that just closed is the newest one there is, so nothing is missing."""

    period = timeframe_duration(timeframe)
    opened = NOW - period
    lateness = candle_lateness_ms(newest_candle_open=opened, timeframe=timeframe, now=NOW)
    measured = measure_freshness(lateness_ms=lateness, timeframe=timeframe)
    assert measured.candles_behind == 0
    assert measured.is_current
    assert measured.ratio == 1.0


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
def test_a_candle_that_has_not_closed_yet_is_never_late(timeframe: str) -> None:
    """An intrabar reading is the newest thing that exists, not late data."""

    period = timeframe_duration(timeframe)
    lateness = candle_lateness_ms(
        newest_candle_open=NOW - period / 2, timeframe=timeframe, now=NOW
    )
    assert lateness < 0
    assert measure_freshness(lateness_ms=lateness, timeframe=timeframe).is_current


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
@pytest.mark.parametrize("missed", [1, 2, 3, 7])
def test_missed_candles_are_counted_exactly(timeframe: str, missed: int) -> None:
    """Behind by three candles must read as three, on a one-minute and on a daily alike."""

    period = timeframe_duration(timeframe)
    opened = NOW - period * (missed + 1)
    lateness = candle_lateness_ms(newest_candle_open=opened, timeframe=timeframe, now=NOW)
    measured = measure_freshness(lateness_ms=lateness, timeframe=timeframe)
    assert measured.candles_behind == missed
    assert not measured.is_current


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
def test_lateness_just_under_one_candle_is_still_current(timeframe: str) -> None:
    """The boundary case the old thresholds got wrong for every period at once.

    A candle closed almost a whole period ago and its successor has not closed. Nothing
    newer exists, so this is current — even though the raw delay is large, and it is
    exactly the delay the old code called "stale".
    """

    period = timeframe_duration(timeframe)
    opened = NOW - period * 2 + timedelta(seconds=1)
    lateness = candle_lateness_ms(newest_candle_open=opened, timeframe=timeframe, now=NOW)
    assert lateness > 0
    assert measure_freshness(lateness_ms=lateness, timeframe=timeframe).is_current


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
def test_the_alert_proof_grader_gives_full_marks_for_current_data(timeframe: str) -> None:
    """The score on an alert's evidence must agree with the count, on every period."""

    factor = _data_freshness_factor(
        {"timeframe": timeframe, "data_latency_ms": 0, "data_candles_behind": 0}, 8
    )
    assert factor.score == 8
    assert factor.status == "passed"


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
def test_the_alert_proof_grader_reads_an_old_record_without_the_count(timeframe: str) -> None:
    """Evidence written before the count existed still has to be graded correctly."""

    factor = _data_freshness_factor(
        {"timeframe": timeframe, "data_latency_ms": timeframe_ms(timeframe) - 1}, 8
    )
    assert factor.score == 8
    assert factor.status == "passed"


def test_an_unmeasurable_period_is_reported_as_unknown_not_as_late() -> None:
    """Never measured and measured-and-late are different news and must not share a word."""

    assert measure_freshness(lateness_ms=None, timeframe="1h").candles_behind is None
    assert measure_freshness(lateness_ms=0, timeframe=None).candles_behind is None
    assert measure_freshness(lateness_ms=0, timeframe="7y").candles_behind is None
    assert measure_freshness(lateness_ms=None, timeframe="1h").status == "unknown"


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
def test_every_supported_period_can_be_sized(timeframe: str) -> None:
    """A period the platform offers must be one the freshness owner can measure.

    Four modules used to size a period by slicing the string, and one of them returned
    "a day" for anything it did not recognise. A period that cannot be sized has to raise
    here, so a new one added to the list can never fall through to a silent default.
    """

    assert timeframe_minutes(timeframe) == TIMEFRAME_MINUTES[timeframe]
    assert timeframe_ms(timeframe) == TIMEFRAME_MINUTES[timeframe] * 60_000


@pytest.mark.parametrize("bad", ["7y", "", "15", "m15", "0m", "abc"])
def test_an_unknown_period_is_refused_rather_than_guessed(bad: str) -> None:
    with pytest.raises(ValueError):
        timeframe_minutes(bad)


def test_every_module_that_sizes_a_candle_uses_the_same_owner() -> None:
    """One answer to "how long is a candle", however the caller reached it."""

    from ai_market_monitor.core.plans import timeframe_to_minutes
    from ai_market_monitor.provider_context import timeframe_duration as provider_duration
    from ai_market_monitor.services.market_preview import (
        timeframe_duration as preview_duration,
    )
    from ai_market_monitor.strategy_cockpit import _timeframe_minutes

    for timeframe in ORDERED_TIMEFRAMES:
        expected = timeframe_duration(timeframe)
        assert provider_duration(timeframe) == expected
        assert preview_duration(timeframe) == expected
        assert timeframe_to_minutes(timeframe) == timeframe_minutes(timeframe)
        assert _timeframe_minutes(timeframe) == timeframe_minutes(timeframe)
