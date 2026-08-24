"""One reading of a market serves every monitor that wants it, and is never stale.

Fifty monitors watching one-minute candles nearly all ask about the same markets. Before
this, each paid the exchange separately for an identical answer, and ccxt's rate limiter
charges that in real sleeping: 100 ms per candle request, 4,000 ms for the unfiltered
ticker read. Fifty monitors x 22 symbols x 100 ms is 110 seconds of sleeping to fill a
60-second minute, which no amount of concurrency can fix — a rate limit is not a queue
that goes faster when more callers join it.

Two rules are asserted here, and both matter equally:

* **shared** — callers inside one window cost one request, however many of them there are;
* **never stale** — no reading is reused for longer than one candle, on any timeframe.

A cache that broke the second rule would make the product faster and wrong, which is worse
than slow. Every test below is parametrised over every timeframe the product supports,
because a window that is right for ``1m`` and wrong for ``1d`` is the same defect wearing
a different number.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ai_market_monitor.engine.data_freshness import timeframe_ms
from ai_market_monitor.schemas.timeframes import ORDERED_TIMEFRAMES
from ai_market_monitor.services.interfaces import Candle
from ai_market_monitor.services.market_cache import (
    MAX_ENTRY_BYTES,
    CachedMarketDataProvider,
    bucket_of,
    bucket_seconds,
    decode_candles,
    encode_candles,
)

MAX_AGE_VALUES = [1, 5, 30, 60, 300, 3600]


class FakeRedis:
    """Enough of redis.asyncio to hold bytes with a lifetime, plus a way to break it."""

    def __init__(self, *, fail_on_get: bool = False, fail_on_set: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.fail_on_get = fail_on_get
        self.fail_on_set = fail_on_set

    async def get(self, key: str) -> str | None:
        if self.fail_on_get:
            raise ConnectionError("redis is down")
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        if self.fail_on_set:
            raise ConnectionError("redis is down")
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex


class CountingProvider:
    """A provider that answers correctly and remembers exactly what it was asked."""

    def __init__(self) -> None:
        self.ohlcv_calls: list[tuple[str, str, str, int]] = []
        self.metadata_calls: list[list[str]] = []

    async def fetch_ohlcv(
        self, exchange: str, symbol: str, timeframe: str, limit: int
    ) -> list[Candle]:
        self.ohlcv_calls.append((exchange, symbol, timeframe, limit))
        start = datetime(2026, 8, 24, tzinfo=UTC)
        step = timedelta(milliseconds=timeframe_ms(timeframe))
        return [
            Candle(
                timestamp=start + step * index,
                open=100.0 + index,
                high=101.0 + index,
                low=99.0 + index,
                close=100.5 + index,
                volume=10.0 + index,
                is_closed=index < limit - 1,
                quote_volume=None if index % 3 == 0 else 1000.0 + index,
            )
            for index in range(limit)
        ]

    async def fetch_universe_metadata(
        self, exchange: str, symbols: list[str], *, include_listing_dates: bool = False
    ) -> dict[str, dict[str, Any]]:
        self.metadata_calls.append(list(symbols))
        return {symbol: {"bid": 10.0, "symbol": symbol} for symbol in symbols}

    async def something_only_the_inner_provider_has(self) -> str:
        return "reached the inner provider"


def cached(
    provider: CountingProvider, redis: FakeRedis, max_age: int = 60
) -> CachedMarketDataProvider:
    return CachedMarketDataProvider(provider, redis, max_age_seconds=max_age)


class FrozenClock:
    """Stands in for ``datetime`` inside the module so a window can be stepped over."""

    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self, tz: Any = None) -> datetime:
        return self.at


# --------------------------------------------------------------- the window is honest


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
@pytest.mark.parametrize("max_age", MAX_AGE_VALUES)
def test_a_reading_is_never_reused_for_longer_than_one_candle(timeframe, max_age):
    """The rule that keeps the cache honest, over every timeframe and every setting."""

    window = bucket_seconds(timeframe, max_age)
    candle_seconds = timeframe_ms(timeframe) // 1000

    assert window <= candle_seconds, "a reading outlived the candle it describes"
    assert window <= max_age, "a reading outlived the configured ceiling"
    assert window >= 1


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
@pytest.mark.parametrize("max_age", MAX_AGE_VALUES)
def test_the_window_is_the_smaller_of_the_two_bounds(timeframe, max_age):
    assert bucket_seconds(timeframe, max_age) == min(timeframe_ms(timeframe) // 1000, max_age)


@pytest.mark.parametrize(
    "timeframe", [None, "", "not-a-timeframe", "15", "1y", "3z", "  ", "1 m"]
)
def test_an_unreadable_timeframe_falls_back_to_the_ceiling_not_to_a_guess(timeframe):
    """Being wrong towards fresher costs a request. Being wrong towards staler costs a
    late alert, and only one of those reaches a customer."""

    assert bucket_seconds(timeframe, 60) == 60


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
def test_two_moments_in_one_window_share_a_bucket_and_the_next_one_does_not(timeframe):
    window = bucket_seconds(timeframe, 60)
    base = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
    # Start of a window, so that stepping by less than one stays inside it.
    aligned = datetime.fromtimestamp((int(base.timestamp()) // window) * window, tz=UTC)

    assert bucket_of(timeframe, 60, aligned) == bucket_of(
        timeframe, 60, aligned + timedelta(seconds=window - 1)
    )
    assert bucket_of(timeframe, 60, aligned) != bucket_of(
        timeframe, 60, aligned + timedelta(seconds=window)
    )


# ------------------------------------------------------------------ readings are shared


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
@pytest.mark.parametrize("callers", [2, 5, 50])
async def test_many_callers_in_one_window_cost_exactly_one_request(timeframe, callers):
    """The whole point, over every timeframe and at the scale that broke the server."""

    provider = CountingProvider()
    reader = cached(provider, FakeRedis())

    answers = [
        await reader.fetch_ohlcv("binance", "BTC/USDT", timeframe, 300) for _ in range(callers)
    ]

    assert len(provider.ohlcv_calls) == 1, "every caller paid the exchange separately"
    assert all(answer == answers[0] for answer in answers), "callers got different answers"
    assert reader.hits == callers - 1


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
async def test_a_new_window_pays_again(timeframe, monkeypatch):
    """Sharing must not become holding. When the window turns over, so does the reading."""

    provider = CountingProvider()
    reader = cached(provider, FakeRedis())
    window = bucket_seconds(timeframe, 60)
    clock = FrozenClock(datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC))
    monkeypatch.setattr("ai_market_monitor.services.market_cache.datetime", clock)

    await reader.fetch_ohlcv("binance", "BTC/USDT", timeframe, 300)
    clock.at = clock.at + timedelta(seconds=window)
    await reader.fetch_ohlcv("binance", "BTC/USDT", timeframe, 300)

    assert len(provider.ohlcv_calls) == 2


@pytest.mark.parametrize(
    "left,right",
    [
        (("binance", "BTC/USDT", "1m"), ("bybit", "BTC/USDT", "1m")),
        (("binance", "BTC/USDT", "1m"), ("binance", "ETH/USDT", "1m")),
        (("binance", "BTC/USDT", "1m"), ("binance", "BTC/USDT", "5m")),
    ],
)
async def test_two_different_questions_never_share_one_answer(left, right):
    """Sharing is by exchange, symbol and timeframe together. Any difference is a
    different question, and answering it from another market's reading would be a
    fabricated price."""

    provider = CountingProvider()
    reader = cached(provider, FakeRedis())

    await reader.fetch_ohlcv(*left, 300)
    await reader.fetch_ohlcv(*right, 300)

    assert len(provider.ohlcv_calls) == 2


# ---------------------------------------------------------------- the answer is correct


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
@pytest.mark.parametrize("limit", [1, 2, 7, 300, 302])
async def test_a_shared_reading_is_identical_to_an_unshared_one(timeframe, limit):
    """A cached answer that differed from a fresh one would be a wrong price, quietly."""

    provider = CountingProvider()
    reader = cached(provider, FakeRedis())

    first = await reader.fetch_ohlcv("binance", "BTC/USDT", timeframe, limit)
    second = await reader.fetch_ohlcv("binance", "BTC/USDT", timeframe, limit)
    direct = await provider.fetch_ohlcv("binance", "BTC/USDT", timeframe, limit)

    assert first == direct
    assert second == direct


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
async def test_a_wider_reading_answers_a_narrower_question_exactly(timeframe):
    """"The newest 300" contains "the newest 10", so the second costs nothing. It has to
    be the *newest* ten, though — serving the oldest ten would be silently wrong."""

    provider = CountingProvider()
    reader = cached(provider, FakeRedis())

    wide = await reader.fetch_ohlcv("binance", "BTC/USDT", timeframe, 300)
    narrow = await reader.fetch_ohlcv("binance", "BTC/USDT", timeframe, 10)

    assert len(provider.ohlcv_calls) == 1
    assert narrow == wide[-10:]
    assert len(narrow) == 10


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
async def test_a_narrower_reading_never_answers_a_wider_question(timeframe):
    """The other half. Ten candles cannot be stretched into three hundred, and returning
    ten where three hundred were asked for would starve an indicator of its history."""

    provider = CountingProvider()
    reader = cached(provider, FakeRedis())

    await reader.fetch_ohlcv("binance", "BTC/USDT", timeframe, 10)
    wide = await reader.fetch_ohlcv("binance", "BTC/USDT", timeframe, 300)

    assert len(provider.ohlcv_calls) == 2
    assert len(wide) == 300


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
@pytest.mark.parametrize("limit", [1, 2, 50, 302])
def test_a_candle_survives_being_stored_and_read_back_unchanged(timeframe, limit):
    """Every field, including the two that are easy to drop: ``is_closed`` decides whether
    a candle may be judged at all, and ``quote_volume`` is often ``None``."""

    start = datetime(2026, 8, 24, tzinfo=UTC)
    step = timedelta(milliseconds=timeframe_ms(timeframe))
    original = [
        Candle(
            timestamp=start + step * index,
            open=100.25 + index,
            high=101.5 + index,
            low=99.125 + index,
            close=100.75 + index,
            volume=10.5 + index,
            is_closed=index < limit - 1,
            quote_volume=None if index % 3 == 0 else 1000.5 + index,
        )
        for index in range(limit)
    ]

    assert decode_candles(encode_candles(original)) == original


# ------------------------------------------------------------- failure is never a crash


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
async def test_a_dead_cache_still_answers_correctly(timeframe):
    """A slow answer is a much smaller problem than no answer. Redis being down must cost
    speed and nothing else."""

    provider = CountingProvider()
    reader = cached(provider, FakeRedis(fail_on_get=True, fail_on_set=True))

    answer = await reader.fetch_ohlcv("binance", "BTC/USDT", timeframe, 50)
    metadata = await reader.fetch_universe_metadata("binance", ["BTC/USDT", "ETH/USDT"])

    assert answer == await provider.fetch_ohlcv("binance", "BTC/USDT", timeframe, 50)
    assert set(metadata) == {"BTC/USDT", "ETH/USDT"}


@pytest.mark.parametrize(
    "rubbish",
    ["", "not json", "[]", "null", '{"limit": 300}', '{"limit": 300, "payload": "{}"}',
     '{"limit": 300, "payload": "{\\"candles\\": [[1]]}"}',
     '{"limit": 300, "payload": "{\\"candles\\": \\"nope\\"}"}'],
)
async def test_a_damaged_entry_is_a_miss_rather_than_a_failure(rubbish):
    """Anything that does not decode is treated as absent. A cache that can raise is a new
    way for a scan to fail, which would trade reliability for speed."""

    provider = CountingProvider()
    redis = FakeRedis()
    reader = cached(provider, redis)
    redis.store[f"hm:mkt:1:ohlcv:binance:BTC/USDT:1m:{bucket_of('1m', 60)}"] = rubbish

    answer = await reader.fetch_ohlcv("binance", "BTC/USDT", "1m", 300)

    assert len(answer) == 300
    assert len(provider.ohlcv_calls) == 1


@pytest.mark.parametrize("limit", [1, 50, 302, 1000, 4000, 10_000, 25_000])
async def test_a_reading_is_answered_whether_or_not_it_is_small_enough_to_share(limit):
    """This Redis is the Celery broker, and `docker-compose.prod.yml` sets no `maxmemory`
    on it on purpose: evicting keys would silently drop queued jobs. So filling it stops
    the product rather than slowing it, and this cache is not allowed to be what fills it.

    `_history_limit` will ask for up to 25,000 candles, and a few hundred of those would
    be hundreds of megabytes against a 160 MB container. The rule is asserted against the
    stored size rather than against a candle count, because how many candles fit in
    256 KB depends on how long the prices are — a rule written as a count would be right
    for one market and wrong for another.
    """

    provider = CountingProvider()
    redis = FakeRedis()
    reader = cached(provider, redis)

    answer = await reader.fetch_ohlcv("binance", "BTC/USDT", "1m", limit)

    assert len(answer) == limit, "a reading must reach its caller whatever its size"
    if reader.too_large:
        assert redis.store == {}, "an over-large reading was stored anyway"
    else:
        assert len(redis.store) == 1
        assert all(len(value) <= MAX_ENTRY_BYTES for value in redis.store.values())


async def test_nothing_over_the_ceiling_is_ever_stored():
    """The bound, stated once and directly: whatever is in the store fits."""

    provider = CountingProvider()
    redis = FakeRedis()
    reader = cached(provider, redis)

    for limit in (1, 302, 4000, 25_000):
        await reader.fetch_ohlcv("binance", f"A{limit}/USDT", "1m", limit)

    assert redis.store, "nothing was shared at all"
    assert all(len(value) <= MAX_ENTRY_BYTES for value in redis.store.values())


@pytest.mark.parametrize("limit", [1, 50, 302])
async def test_an_ordinary_reading_is_always_shared(limit):
    """The bound must not be so tight that normal work stops being shared — that would
    turn the whole cache off quietly. 302 candles is what a scan actually asks for."""

    provider = CountingProvider()
    redis = FakeRedis()
    reader = cached(provider, redis)

    await reader.fetch_ohlcv("binance", "BTC/USDT", "1m", limit)

    assert len(redis.store) == 1, "an ordinary reading was refused"
    assert reader.too_large == 0


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
async def test_every_stored_reading_is_given_a_lifetime(timeframe):
    """A key with no expiry would stay in the broker's memory for ever. The lifetime is
    what keeps the cache's footprint bounded without an eviction policy."""

    provider = CountingProvider()
    redis = FakeRedis()
    reader = cached(provider, redis)

    await reader.fetch_ohlcv("binance", "BTC/USDT", timeframe, 300)
    await reader.fetch_universe_metadata("binance", ["BTC/USDT"])

    assert set(redis.ttls) == set(redis.store), "a reading was stored with no lifetime"
    assert all(1 <= ttl <= 60 for ttl in redis.ttls.values()), redis.ttls


async def test_closing_the_provider_closes_the_cache_connection_too():
    """The worker builds a provider per task and closes it in a ``finally``. If ``close``
    reached only the inner provider, every task would leave a Redis connection pool open —
    fifty per child before it is recycled, leaked into the Celery broker itself."""

    closed: list[str] = []

    class ClosableProvider(CountingProvider):
        async def close(self) -> None:
            closed.append("provider")

    class ClosableRedis(FakeRedis):
        async def aclose(self) -> None:
            closed.append("redis")

    await cached(ClosableProvider(), ClosableRedis()).close()

    assert closed == ["provider", "redis"]


async def test_a_provider_that_fails_to_close_still_releases_the_cache_connection():
    """A half-closed provider must not become a leak as well."""

    closed: list[str] = []

    class AngryProvider(CountingProvider):
        async def close(self) -> None:
            raise RuntimeError("the exchange client would not close")

    class ClosableRedis(FakeRedis):
        async def aclose(self) -> None:
            closed.append("redis")

    with pytest.raises(RuntimeError):
        await cached(AngryProvider(), ClosableRedis()).close()

    assert closed == ["redis"]


async def test_closing_a_provider_that_cannot_be_closed_is_not_an_error():
    """Not every provider has a ``close``. The fixture provider is one of them."""

    class Bare:
        pass

    await CachedMarketDataProvider(Bare(), FakeRedis()).close()


async def test_an_attribute_the_wrapper_does_not_define_reaches_the_real_provider():
    """``fetch_ohlcv_range`` and ``close`` are both found with ``getattr`` in this
    codebase. Wrapping must never remove a capability a caller reaches for."""

    provider = CountingProvider()
    reader = cached(provider, FakeRedis())

    assert await reader.something_only_the_inner_provider_has() == "reached the inner provider"


# ------------------------------------------------------------------- metadata is shared


async def test_two_universes_that_overlap_only_pay_for_what_is_new():
    """Stored per symbol rather than per universe, so every overlap counts. One key per
    whole answer would only help a monitor whose universe exactly matched another's."""

    provider = CountingProvider()
    reader = cached(provider, FakeRedis())

    await reader.fetch_universe_metadata("binance", ["BTC/USDT", "ETH/USDT"])
    await reader.fetch_universe_metadata("binance", ["ETH/USDT", "SOL/USDT"])

    assert provider.metadata_calls == [["BTC/USDT", "ETH/USDT"], ["SOL/USDT"]]


async def test_a_repeated_universe_costs_nothing_at_all():
    provider = CountingProvider()
    reader = cached(provider, FakeRedis())
    universe = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

    first = await reader.fetch_universe_metadata("binance", universe)
    second = await reader.fetch_universe_metadata("binance", universe)

    assert len(provider.metadata_calls) == 1
    assert first == second
    assert set(first) == set(universe)


async def test_listing_dates_are_a_different_question_from_prices():
    """``include_listing_dates`` changes what the answer contains, so an answer read
    without it must never be served to a caller that asked for it."""

    provider = CountingProvider()
    reader = cached(provider, FakeRedis())

    await reader.fetch_universe_metadata("binance", ["BTC/USDT"], include_listing_dates=False)
    await reader.fetch_universe_metadata("binance", ["BTC/USDT"], include_listing_dates=True)

    assert len(provider.metadata_calls) == 2
