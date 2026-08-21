"""How late the market data behind one check was — counted in candles, not milliseconds.

One owner, because four unrelated modules were each answering "were these the newest
prices?" and all four were answering it the same wrong way: they took the *open* time of
the newest candle, subtracted it from the clock, and graded the difference against a
budget written for tick data — full marks under five seconds, a warning past one minute.

A candle's open time is one whole candle older than the candle itself. On a five-minute
monitor the newest closed candle opened between five and ten minutes ago **even when
nothing newer exists anywhere**, so the difference was never under a minute and the
answer was never "fresh". Every monitor slower than one minute was permanently marked
"the prices it read were not the newest ones", which was untrue for all of them.

The honest question is not "how many milliseconds old is this row". It is **"did we read
the newest candle the exchange could have given us?"** — and that can only be asked
against the candle period. So the measure here is:

    lateness  = now - (open time + one candle)      # time past the candle's close
    behind    = how many further candles have closed since

``behind == 0`` means nothing newer existed to read. That is the only state that means
fresh, and it is the state a healthy monitor of any period sits in.

Milliseconds are kept beside the count because an evidence record should carry the raw
measurement, never only a verdict. They are never graded on their own.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ai_market_monitor.schemas.timeframes import TIMEFRAME_MINUTES

__all__ = [
    "DataFreshness",
    "candle_lateness_ms",
    "freshness_from_proof",
    "measure_freshness",
    "timeframe_duration",
    "timeframe_minutes",
    "timeframe_ms",
]


def timeframe_minutes(timeframe: str) -> int:
    """Minutes in one candle of ``timeframe``.

    The supported list is the owner of this answer. Four modules used to parse the string
    by hand — ``int(timeframe[:-1])`` plus a unit letter — and one of them fell back to
    "a day" for anything it did not recognise, so a typo became a daily candle in silence
    instead of an error.
    """

    normalized = timeframe.strip().casefold()
    try:
        return TIMEFRAME_MINUTES[normalized]
    except KeyError:
        raise ValueError(f"Unsupported timeframe: {timeframe}") from None


def timeframe_duration(timeframe: str) -> timedelta:
    """One candle of ``timeframe``, as a length of time."""

    return timedelta(minutes=timeframe_minutes(timeframe))


def timeframe_ms(timeframe: str) -> int:
    """One candle of ``timeframe``, in milliseconds."""

    return timeframe_minutes(timeframe) * 60_000


def candle_lateness_ms(
    *,
    newest_candle_open: datetime,
    timeframe: str,
    now: datetime,
) -> int:
    """Milliseconds past the moment a newer candle became available.

    Zero or negative means the newest candle we read is still the newest one there is:
    either it is still forming, or it has closed and its successor has not.

    The subtraction is deliberately from the candle's **close**, not its open. Measuring
    from the open charges every monitor one full candle of lateness it did not earn.
    """

    closes_at = newest_candle_open + timeframe_duration(timeframe)
    return int((now - closes_at).total_seconds() * 1000)


#: What each number of missed candles is worth, as a share of the component's maximum.
#:
#: The shape matches the four bands the old absolute thresholds used, so a score computed
#: before this change and one computed after are still the same kind of number. What
#: changed is what the bands are measured against.
_RATIO_BY_CANDLES_BEHIND: dict[int, float] = {0: 1.0, 1: 0.7, 2: 0.35}
_RATIO_WHEN_FAR_BEHIND = 0.0

#: Used when there is nothing to measure — no candle, or a period we cannot size. Neither
#: full marks nor zero: not knowing is not the same as being late, and a monitor must not
#: be marked broken for a measurement that was never taken.
_RATIO_WHEN_UNKNOWN = 0.6


@dataclass(frozen=True, slots=True)
class DataFreshness:
    """Whether a check read the newest prices, and how far behind it was if not."""

    candles_behind: int | None
    lateness_ms: int | None
    timeframe: str | None

    @property
    def is_current(self) -> bool:
        """True only when nothing newer existed to read."""

        return self.candles_behind == 0

    @property
    def is_known(self) -> bool:
        return self.candles_behind is not None

    @property
    def ratio(self) -> float:
        """How much of a freshness score this deserves, between 0 and 1."""

        if self.candles_behind is None:
            return _RATIO_WHEN_UNKNOWN
        return _RATIO_BY_CANDLES_BEHIND.get(self.candles_behind, _RATIO_WHEN_FAR_BEHIND)

    @property
    def status(self) -> str:
        """The one word an evidence record carries beside the score."""

        if self.candles_behind is None:
            return "unknown"
        if self.candles_behind == 0:
            return "passed"
        if self.candles_behind == 1:
            return "partial"
        if self.candles_behind == 2:
            return "stale"
        return "failed"

    def explain(self) -> str:
        """One sentence for an evidence record, in the terms actually measured."""

        if self.candles_behind is None:
            return "Data lateness could not be measured."
        if self.candles_behind == 0:
            return "The newest closed candle was read."
        candles = "candle" if self.candles_behind == 1 else "candles"
        return f"{self.candles_behind} newer {candles} had closed and were not read."


def freshness_from_proof(proof: Mapping[str, Any]) -> DataFreshness:
    """The freshness an evidence record carries, or the one it implies.

    One owner, because three surfaces ask this of the same receipt — the score on an
    alert's evidence, the words in the email that alert becomes, and the monitor card.
    Records written before ``data_candles_behind`` existed carry only the milliseconds
    and the period, so the count is measured back from those rather than called unknown.
    """

    behind = proof.get("data_candles_behind")
    latency = proof.get("data_latency_ms")
    timeframe = proof.get("data_freshness_timeframe") or proof.get("timeframe")
    if isinstance(behind, int) and not isinstance(behind, bool):
        return DataFreshness(
            behind,
            latency if isinstance(latency, int) else None,
            timeframe if isinstance(timeframe, str) else None,
        )
    return measure_freshness(
        lateness_ms=int(latency) if isinstance(latency, int | float) else None,
        timeframe=timeframe if isinstance(timeframe, str) else None,
    )


def measure_freshness(*, lateness_ms: int | None, timeframe: str | None) -> DataFreshness:
    """Turn a raw lateness into "how many candles behind", which is the real question.

    Both inputs can be missing, and a missing one is reported as unknown rather than
    guessed. An absent measurement used to be scored as though it were a late one.
    """

    if lateness_ms is None or not timeframe:
        return DataFreshness(None, lateness_ms, timeframe)
    try:
        period = timeframe_ms(timeframe)
    except ValueError:
        return DataFreshness(None, lateness_ms, timeframe)
    if period <= 0:  # pragma: no cover - the supported list has no zero-length candle
        return DataFreshness(None, lateness_ms, timeframe)
    # A candle that closed less than one period ago still has no successor, so nothing
    # newer existed to read. Only once a further period has elapsed is a candle missing.
    return DataFreshness(max(0, math.floor(lateness_ms / period)), lateness_ms, timeframe)
