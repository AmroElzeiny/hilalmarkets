"""A market reading must reach the moment it was asked for.

The scan said "checked two minutes ago" and it was true. The candles it judged were
about three intervals old — three hours behind on an hourly monitor — because the pager
stopped as soon as it had collected ``limit`` rows. An exchange returns candles forward
from ``since``, so stopping on a count stopped at the *oldest* end of the window and the
walk never arrived at ``end``.

These tests assert the rule for every timeframe and every window size: the newest candle
returned is the newest candle that exists, and the walk costs a sane number of calls.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.market_preview import (
    CcxtMarketDataProvider,
    timeframe_duration,
)

EVERY_TIMEFRAME = ["1m", "5m", "15m", "1h", "4h", "1d"]


class FakeExchange:
    """An exchange that answers the way public OHLCV endpoints really do.

    Candles run **forward** from ``since`` and never number more than ``limit``. That
    one behaviour is the whole trap: a pager that stops counting stops in the past.
    """

    def __init__(self, timeframe: str, *, now: datetime) -> None:
        self.duration_ms = int(timeframe_duration(timeframe).total_seconds() * 1000)
        self.now_ms = int(now.timestamp() * 1000)
        self.calls: list[int] = []

    @property
    def newest_ms(self) -> int:
        return (self.now_ms // self.duration_ms) * self.duration_ms

    async def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
        self.calls.append(int(limit or 0))
        first = -(-int(since) // self.duration_ms) * self.duration_ms
        rows = []
        stamp = first
        while stamp <= self.newest_ms and len(rows) < int(limit or 500):
            rows.append([stamp, 100.0, 101.0, 99.0, 100.5, 10.0])
            stamp += self.duration_ms
        return rows


def _provider(fake: FakeExchange) -> CcxtMarketDataProvider:
    provider = CcxtMarketDataProvider(Settings())

    async def _client(exchange: str):
        return fake

    provider._client = _client  # type: ignore[method-assign]
    return provider


@pytest.mark.parametrize("limit", [50, 300, 1200])
@pytest.mark.parametrize("timeframe", EVERY_TIMEFRAME)
@pytest.mark.asyncio
async def test_the_newest_candle_is_the_newest_candle(timeframe: str, limit: int) -> None:
    """Whatever the timeframe or the window, the reading arrives at now."""

    now = datetime.now(UTC)
    fake = FakeExchange(timeframe, now=now)
    candles = await _provider(fake).fetch_ohlcv("binance", "BTC/USDT", timeframe, limit)

    assert candles, f"{timeframe}:{limit} returned nothing"
    newest_ms = int(candles[-1].timestamp.timestamp() * 1000)
    assert newest_ms == fake.newest_ms, (
        f"{timeframe}:{limit} stopped "
        f"{(fake.newest_ms - newest_ms) / fake.duration_ms:.0f} candles short of now"
    )


@pytest.mark.parametrize("limit", [50, 300, 1200])
@pytest.mark.parametrize("timeframe", EVERY_TIMEFRAME)
@pytest.mark.asyncio
async def test_the_window_holds_what_was_asked_for(timeframe: str, limit: int) -> None:
    """Reaching the end must not cost the history behind it."""

    fake = FakeExchange(timeframe, now=datetime.now(UTC))
    candles = await _provider(fake).fetch_ohlcv("binance", "BTC/USDT", timeframe, limit)

    assert len(candles) == limit
    stamps = [candle.timestamp for candle in candles]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)


@pytest.mark.parametrize("timeframe", EVERY_TIMEFRAME)
@pytest.mark.asyncio
async def test_one_market_costs_one_call(timeframe: str) -> None:
    """A scan reads every market it watches, so the ordinary window is one call."""

    fake = FakeExchange(timeframe, now=datetime.now(UTC))
    await _provider(fake).fetch_ohlcv("binance", "BTC/USDT", timeframe, 300)

    assert len(fake.calls) == 1, f"{timeframe} took {len(fake.calls)} calls"


@pytest.mark.asyncio
async def test_a_wide_window_with_a_small_limit_stays_bounded() -> None:
    """A narrow ask over a long stretch must not become thousands of calls."""

    now = datetime.now(UTC)
    fake = FakeExchange("1m", now=now)
    candles = await _provider(fake).fetch_ohlcv_range(
        "binance",
        "BTC/USDT",
        "1m",
        now - timedelta(days=30),
        now,
        5,
    )

    assert len(fake.calls) <= 64
    assert len(candles) == 5
    # And it is still the newest five, not the five it happened to reach first.
    assert int(candles[-1].timestamp.timestamp() * 1000) == fake.newest_ms
