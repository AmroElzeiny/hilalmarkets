"""One reading of a market, shared by every monitor that watches it.

Fifty monitors watching one-minute candles are fifty checks a minute. Almost all of them
want the same markets: the same BTC/USDT candles, the same tickers. Nothing shared them,
so each monitor paid the exchange separately for an identical answer.

Two costs, both measured against Binance on 24 August 2026:

===============================  ==========  ===================================
Request                          Answer      What ccxt's rate limiter charges
===============================  ==========  ===================================
302 one-minute candles           51,380 B    weight 2  ->   100 ms of sleep
tickers for 2 named symbols       1,109 B    weight 2  ->   100 ms of sleep
tickers, unfiltered            1,886,437 B   weight 80 -> 4,000 ms of sleep
===============================  ==========  ===================================

The sleep is the part that matters. ``CcxtMarketDataProvider`` sets
``enableRateLimit: True`` and keeps one client per exchange, so that sleep is shared by
everything in the process. Fifty monitors × 22 symbols × 100 ms is 110 seconds of
sleeping to fill one minute — the work cannot fit in the time no matter how fast the
machine is, and no amount of concurrency changes it, because a rate limit is not a
queue that goes faster when more people join it.

Sending fewer requests is the only lever. That is all this is: the answer to
"what is BTC/USDT doing right now" is stored for a short while, and the other
forty-nine monitors read the stored one.

**How long "a short while" is.** A bucket is ``min(one candle, max_age_seconds)``. The
candle bound is the honest one — inside a single one-minute candle every monitor asking
about one-minute candles wants the same closed candles, so sharing costs nothing at all.
The ``max_age_seconds`` bound exists for the other end: without it a daily monitor would
hold one reading for a whole day and the still-forming candle would never move. With
both, no reading is ever reused for longer than one candle **or** longer than
``max_age_seconds``, whichever is shorter.

Nothing here is allowed to break a scan. Every Redis failure falls through to the real
provider, because a slow answer is a much smaller problem than no answer.
"""

from __future__ import annotations

import json
import logging
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.data_freshness import timeframe_ms
from ai_market_monitor.services.interfaces import Candle

logger = logging.getLogger(__name__)

#: Bumped when the stored shape changes. Old entries then miss rather than being misread,
#: which is what a version in the key buys: a deploy never has to clear the cache by hand.
CACHE_SCHEMA_VERSION = 1

_KEY_PREFIX = f"hm:mkt:{CACHE_SCHEMA_VERSION}"

#: The largest single reading that may be stored, in bytes.
#:
#: This exists because of what Redis is here. It is the Celery **broker**, and
#: `docker-compose.prod.yml` deliberately sets no `maxmemory` on it: evicting keys under
#: pressure would silently drop queued background jobs, so the container limit is the only
#: ceiling and reaching it kills the broker — which stops the whole product, not just the
#: cache.
#:
#: So this cache is not allowed to be the thing that fills it. A normal reading is about
#: 24 KB (302 candles), and a few hundred of those with a one-minute lifetime is single
#: figures of megabytes. But `_history_limit` will ask for up to
#: `capability_extension_max_history_candles` — 25,000 by default — and a few hundred of
#: *those* would be hundreds of megabytes against a 160 MB container.
#:
#: A reading too large to store is still returned to its caller. It simply is not shared,
#: which costs speed for that one monitor and nothing else. 256 KB leaves room for about
#: 3,000 candles, far above what any normal monitor reads.
MAX_ENTRY_BYTES = 256 * 1024


def bucket_seconds(timeframe: str | None, max_age_seconds: int) -> int:
    """How long one stored reading may serve, for this timeframe.

    An unreadable timeframe falls back to ``max_age_seconds`` rather than guessing a long
    one: being wrong towards *fresher* costs a request, being wrong towards staler costs a
    late alert, and only one of those reaches a customer.
    """

    ceiling = max(1, max_age_seconds)
    if timeframe is None:
        return ceiling
    try:
        candle_seconds = max(1, timeframe_ms(timeframe) // 1000)
    except (ValueError, KeyError, TypeError):
        return ceiling
    return max(1, min(candle_seconds, ceiling))


def bucket_of(timeframe: str | None, max_age_seconds: int, at: datetime | None = None) -> int:
    """Which window ``at`` falls in. Two callers in one window share one answer."""

    moment = at or datetime.now(UTC)
    window = bucket_seconds(timeframe, max_age_seconds)
    return int(moment.timestamp()) // window


def encode_candles(candles: list[Candle]) -> str:
    """Compact enough that a 302-candle set stays small in Redis.

    Positional rather than named: a list of eight values per candle instead of eight keys
    repeated three hundred times.
    """

    return json.dumps(
        {
            "candles": [
                [
                    int(candle.timestamp.timestamp() * 1000),
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                    candle.is_closed,
                    candle.quote_volume,
                ]
                for candle in candles
            ]
        },
        separators=(",", ":"),
    )


def decode_candles(payload: str) -> list[Candle] | None:
    """``None`` for anything that does not decode, so a bad entry is a miss, not a crash.

    A cache that can raise is a new way for a scan to fail, which would make the product
    less reliable in exchange for making it faster. It is only allowed to be faster.
    """

    try:
        rows = json.loads(payload)["candles"]
    except (ValueError, KeyError, TypeError):
        return None
    candles: list[Candle] = []
    try:
        for row in rows:
            candles.append(
                Candle(
                    timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    is_closed=bool(row[6]),
                    quote_volume=None if row[7] is None else float(row[7]),
                )
            )
    except (IndexError, TypeError, ValueError, OSError):
        return None
    return candles


class CachedMarketDataProvider:
    """A market data provider that asks the exchange only when nobody else just did.

    Wraps any provider matching :class:`~ai_market_monitor.services.interfaces.MarketDataProvider`.
    Anything not named here is passed straight through, so wrapping never removes a
    capability a caller reaches for with ``getattr`` — ``fetch_ohlcv_range`` and ``close``
    are both found that way in this codebase.
    """

    def __init__(
        self,
        inner: Any,
        redis: Any,
        *,
        max_age_seconds: int = 60,
    ) -> None:
        self._inner = inner
        self._redis = redis
        self._max_age_seconds = max(1, max_age_seconds)
        #: Counted so a scan can report how much it did not have to ask for.
        self.hits = 0
        self.misses = 0
        #: Readings answered but not shared, because storing them would risk the broker.
        self.too_large = 0

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes this class does not define, so it cannot shadow
        # the cached methods below.
        return getattr(self._inner, name)

    async def close(self) -> None:
        """Close the provider underneath, and this cache's own connection with it.

        Defined rather than delegated on purpose. The worker builds a provider per task
        and closes it in a ``finally``; left to ``__getattr__`` that call would reach the
        inner provider and quietly leave the Redis client made in ``__init__`` open — one
        leaked connection pool per task, fifty per child before the child is recycled.
        Leaking connections into the broker is the same class of failure this whole
        change exists to remove.

        The inner provider is closed first and its failure never stops the Redis client
        being closed, because a half-closed provider must not become a leak as well.
        """

        try:
            inner_close = getattr(self._inner, "close", None)
            if callable(inner_close):
                await inner_close()
        finally:
            # `aclose` on redis-py 5, `close` on older ones, neither on a test double.
            closer = getattr(self._redis, "aclose", None) or getattr(self._redis, "close", None)
            if callable(closer):
                with suppress(Exception):
                    await closer()

    # ------------------------------------------------------------------ candles

    async def fetch_ohlcv(
        self, exchange: str, symbol: str, timeframe: str, limit: int
    ) -> list[Candle]:
        key = (
            f"{_KEY_PREFIX}:ohlcv:{exchange.lower()}:{symbol.upper()}:{timeframe}"
            f":{bucket_of(timeframe, self._max_age_seconds)}"
        )
        stored = await self._read(key)
        if stored is not None:
            # A reading taken for a larger window answers a smaller one exactly, because
            # both mean "the newest N". A reading taken for a smaller window cannot answer
            # a larger one, so that counts as a miss and is fetched properly.
            held = int(stored.get("limit") or 0)
            payload = stored.get("payload")
            # Every part of a stored entry is checked before it is used. An entry holding
            # a size but no candles is not a contradiction worth raising over — it is an
            # entry that cannot answer, which is what a miss means.
            if held >= limit and isinstance(payload, str):
                candles = decode_candles(payload)
                if candles is not None:
                    self.hits += 1
                    return candles[-limit:] if limit else candles
        self.misses += 1
        candles = await self._inner.fetch_ohlcv(exchange, symbol, timeframe, limit)
        await self._write(
            key,
            {"limit": limit, "payload": encode_candles(candles)},
            bucket_seconds(timeframe, self._max_age_seconds),
        )
        return candles

    # ----------------------------------------------------------------- metadata

    async def fetch_universe_metadata(
        self,
        exchange: str,
        symbols: list[str],
        *,
        include_listing_dates: bool = False,
    ) -> dict[str, dict[str, Any]]:
        """Stored per symbol, so two monitors sharing half a universe share half the cost.

        Storing the whole answer under one key would only help a monitor whose universe is
        exactly another's. Per symbol, every overlap counts.
        """

        window = bucket_seconds(None, self._max_age_seconds)
        bucket = bucket_of(None, self._max_age_seconds)
        listing = "1" if include_listing_dates else "0"

        def key_for(symbol: str) -> str:
            return f"{_KEY_PREFIX}:meta:{exchange.lower()}:{listing}:{symbol.upper()}:{bucket}"

        found: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        for symbol in symbols:
            stored = await self._read(key_for(symbol))
            if stored is None:
                missing.append(symbol)
                continue
            record = stored.get("record")
            if isinstance(record, dict):
                found[symbol] = record
                self.hits += 1
            else:
                missing.append(symbol)

        if not missing:
            return found

        self.misses += len(missing)
        fetched = await self._inner.fetch_universe_metadata(
            exchange, missing, include_listing_dates=include_listing_dates
        )
        for symbol in missing:
            record = dict(fetched.get(symbol) or {})
            found[symbol] = record
            await self._write(key_for(symbol), {"record": record}, window)
        return found

    # -------------------------------------------------------------------- redis

    async def _read(self, key: str) -> dict[str, Any] | None:
        try:
            raw = await self._redis.get(key)
        except Exception:  # noqa: BLE001 - a cache miss is the safe reading of any failure
            return None
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    async def _write(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        encoded = json.dumps(value)
        if len(encoded) > MAX_ENTRY_BYTES:
            # Not shared, still answered. See MAX_ENTRY_BYTES: this Redis is the Celery
            # broker, and filling it stops the product rather than slowing it.
            self.too_large += 1
            return
        try:
            await self._redis.set(key, encoded, ex=max(1, ttl_seconds))
        except Exception:  # noqa: BLE001 - failing to store is not failing to answer
            return


def cache_market_data(inner: Any, settings: Settings) -> Any:
    """``inner``, wrapped so its answers are shared — or ``inner`` itself.

    Returns the provider unwrapped under tests and whenever the cache is switched off, so
    that a test never depends on a Redis being up and an operator always has a way back to
    the old behaviour without a code change.
    """

    if settings.app_env == "test" or not settings.market_cache_enabled:
        return inner
    try:
        from redis.asyncio import Redis

        return CachedMarketDataProvider(
            inner,
            Redis.from_url(settings.redis_url),
            max_age_seconds=settings.market_cache_max_age_seconds,
        )
    except Exception:  # noqa: BLE001 - no cache is a slower mode, not a broken one
        logger.warning("market_cache_unavailable")
        return inner
