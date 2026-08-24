"""How often a monitor needs checking, decided in one place.

A monitor watching one-minute candles has to be checked every minute. That sentence is the
whole rule, and it was not what the product did.

**What it did instead.** ``strategy_universes.scan_interval_seconds`` is a stored number,
set once when a monitor is made and never compared to the candles the monitor actually
watches. Measured on the live server on 24 August 2026: every monitor was on the ``1m``
timeframe, and each was being checked **about once an hour**. A person had asked to be
told about one-minute candles and was being shown prices up to sixty candles old, while
the freshness score on their own dashboard tried to explain the gap.

A stored cadence and a watched timeframe are two owners of one fact, which is the recurring
defect in this codebase — see the table in ``CLAUDE.md``. There is one owner now: the
candle. If a monitor watches ``5m`` it is checked every five minutes; ``15m``, every
fifteen; ``1h``, hourly. Nothing has to be kept in step, because nothing is written down
twice.

**Why this is safe on a small server.** Asking more often does not mean doing more work
than the machine has. ``ScanScheduler.schedule_due`` refuses to queue a second job for a
monitor whose previous one is still queued or running, so a monitor whose check takes
longer than its candle is simply checked as often as it can be, and the queue never grows.
The cadence is a target, not a promise, and the shortfall is visible in the freshness score
rather than hidden in a stored number.
"""

from __future__ import annotations

from typing import Any

from ai_market_monitor.engine.data_freshness import timeframe_ms

#: Used when a monitor's timeframe cannot be read at all — a version whose stored schema
#: is missing or unreadable. One minute, because the product's shortest supported candle is
#: one minute: checking too often costs a skipped job, checking too rarely costs a late
#: alert, and only one of those two reaches a customer.
FALLBACK_INTERVAL_SECONDS = 60

#: The floor. A candle shorter than this does not exist in the product, and an interval of
#: zero would make every pass through the scheduler queue a new job.
MINIMUM_INTERVAL_SECONDS = 1


def base_timeframe_of(schema_json: Any) -> str | None:
    """The candle a monitor is built on, read from a stored version's schema.

    Read as one key rather than by validating the whole definition, because the scheduler
    asks this of every active monitor every minute and validating a full ``StrategyDefinition``
    to reach one string is work that grows with the number of customers. The key is the
    same one ``StrategyDefinition.base_timeframe`` is stored under, so the two cannot
    disagree about which candle is the base — there is only one place it is written.
    """

    if not isinstance(schema_json, dict):
        return None
    value = schema_json.get("base_timeframe")
    return value if isinstance(value, str) and value else None


def scan_interval_seconds(schema_json: Any) -> int:
    """How many seconds should pass between two checks of this monitor.

    One candle. A monitor exists to notice something on the candle it watches, so checking
    it less often than that candle closes cannot do the job it was made for.
    """

    timeframe = base_timeframe_of(schema_json)
    if timeframe is None:
        return FALLBACK_INTERVAL_SECONDS
    try:
        period_ms = timeframe_ms(timeframe)
    except ValueError:
        return FALLBACK_INTERVAL_SECONDS
    return max(MINIMUM_INTERVAL_SECONDS, period_ms // 1000)
