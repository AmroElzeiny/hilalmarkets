import asyncio
import hashlib
import json
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models.enums import ScanOutcome
from ai_market_monitor.engine.data_freshness import timeframe_duration
from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.engine.models import MarketSnapshot, ensure_aware
from ai_market_monitor.provider_context import ProviderContextService
from ai_market_monitor.schemas.onboarding import MarketPreviewResponse
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.interfaces import Candle, MarketDataProvider
from ai_market_monitor.services.provider_reliability import ProviderCallError
from ai_market_monitor.services.provider_runtime import provider_request

DEFAULT_PREVIEW_SYMBOLS_BY_QUOTE = {
    "USDT": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"],
    "USDC": ["BTC/USDC", "ETH/USDC", "SOL/USDC"],
}

#: How many symbols one ticker request may name.
#:
#: Some exchanges put the requested symbols in the query string. Binance does:
#: ccxt sends ``GET /api/v3/ticker/24hr?symbols=["BTCUSDT",...]``. Naming a whole
#: USDT spot universe there builds an 8.3 KB URL for 490 pairs and Binance answers
#: HTTP 414 Request-URI Too Large, so *every* symbol lost its ticker at once and the
#: market rendered as if no coin were trading. Bybit hid the fault because ccxt sends
#: it no symbol list at all. 100 is Binance's own request-weight tier boundary and
#: builds a ~1.7 KB URL, which leaves ample headroom under the 8 KB limit.
_TICKER_BATCH_SIZE = 100

#: How many symbols may be probed one at a time once the batched and unfiltered
#: reads have both failed. A bound keeps a provider outage from turning into
#: hundreds of sequential requests.
_TICKER_PROBE_LIMIT = 20


@dataclass(frozen=True)
class CandleDataQuality:
    usable: bool
    code: str | None
    safe_message: str | None
    manifest: dict[str, Any]
    manifest_hash: str


#: Re-exported so existing importers are unchanged. The length of a candle has one owner
#: — :mod:`ai_market_monitor.engine.data_freshness` — because it was written out by hand
#: in four modules, and one of those copies quietly treated any unrecognised period as a
#: day rather than refusing it.
__all__ = ["CandleDataQuality", "timeframe_duration"]


#: Rows to ask an exchange for in one call. The upper bound is what public OHLCV
#: endpoints allow; the lower one keeps a small ``limit`` over a wide window from
#: turning into thousands of one-row calls.
_MAXIMUM_PAGE_ROWS = 1000
_MINIMUM_PAGE_ROWS = 200
#: A hard stop on how many calls one window may cost, whatever the arithmetic says.
_MAXIMUM_PAGES = 64


def _page_budget(start_ms: int, end_ms: int, duration_ms: int, page_rows: int) -> int:
    """How many calls it takes to walk this window, plus one for the moving end."""

    candles = max(0, end_ms - start_ms) // max(1, duration_ms) + 1
    needed = -(-candles // max(1, page_rows)) + 1
    return max(1, min(_MAXIMUM_PAGES, needed))


def assess_candle_data_quality(
    definition: StrategyDefinition,
    candle_sets: dict[str, list[Candle]],
    evaluation_time: datetime,
) -> CandleDataQuality:
    """Apply one freshness/completeness contract to Scanner and Monitor."""

    evaluated_at = ensure_aware(evaluation_time)
    required_timeframes = list(
        dict.fromkeys([definition.base_timeframe, *definition.supporting_timeframes])
    )
    manifest: dict[str, Any] = {}
    error_code: str | None = None
    safe_message: str | None = None
    for timeframe in required_timeframes:
        candles = list(candle_sets.get(timeframe) or [])
        ordered = all(
            ensure_aware(left.timestamp) < ensure_aware(right.timestamp)
            for left, right in zip(candles[:-1], candles[1:], strict=True)
        )
        eligible = [
            candle
            for candle in candles
            if ensure_aware(candle.timestamp) <= evaluated_at
            and (
                definition.trigger_mode.value != "candle_close"
                or timeframe != definition.base_timeframe
                or candle.is_closed
            )
        ]
        observed_through: datetime | None = None
        age_seconds: int | None = None
        if eligible:
            latest = eligible[-1]
            observed_through = ensure_aware(latest.timestamp)
            if latest.is_closed:
                observed_through += timeframe_duration(timeframe)
            age_seconds = max(0, int((evaluated_at - observed_through).total_seconds()))
        maximum_age = max(
            300,
            int(timeframe_duration(timeframe).total_seconds()) * 2,
        )
        payload = [
            {
                "timestamp": ensure_aware(item.timestamp).isoformat(),
                "open": item.open,
                "high": item.high,
                "low": item.low,
                "close": item.close,
                "volume": item.volume,
                "is_closed": item.is_closed,
            }
            for item in candles
        ]
        timeframe_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        manifest[timeframe] = {
            "count": len(candles),
            "first_timestamp": (
                ensure_aware(candles[0].timestamp).isoformat() if candles else None
            ),
            "last_timestamp": (
                ensure_aware(candles[-1].timestamp).isoformat() if candles else None
            ),
            "observed_through": (
                observed_through.isoformat() if observed_through is not None else None
            ),
            "age_seconds": age_seconds,
            "maximum_age_seconds": maximum_age,
            "ordered_unique": ordered,
            "snapshot_hash": timeframe_hash,
        }
        if not candles or not eligible:
            error_code = error_code or "candle_data_missing"
            safe_message = safe_message or f"{timeframe} candle data is unavailable."
        elif not ordered:
            error_code = error_code or "candle_data_invalid"
            safe_message = safe_message or f"{timeframe} candle data is not ordered."
        elif len(candles) < definition.universe.min_historical_candles:
            error_code = error_code or "candle_history_incomplete"
            safe_message = safe_message or (
                f"{timeframe} needs at least "
                f"{definition.universe.min_historical_candles} candles."
            )
        elif age_seconds is not None and age_seconds > maximum_age:
            error_code = error_code or "candle_data_stale"
            safe_message = safe_message or f"{timeframe} candle data is stale."
    manifest_hash = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return CandleDataQuality(
        usable=error_code is None,
        code=error_code,
        safe_message=safe_message,
        manifest=manifest,
        manifest_hash=manifest_hash,
    )


def market_snapshot_from_candles(
    strategy: StrategyDefinition,
    symbol: str,
    candle_sets: dict[str, list[Candle]],
    evaluation_time: datetime,
    provider_metadata: dict[str, Any] | None = None,
) -> MarketSnapshot:
    evaluation_time = ensure_aware(evaluation_time)
    base_history = [
        candle
        for candle in candle_sets[strategy.base_timeframe]
        if ensure_aware(candle.timestamp) <= evaluation_time
    ]
    average_window = base_history[-20:]
    average_volume = (
        sum(candle.volume for candle in average_window) / len(average_window)
        if average_window
        else None
    )
    day_start = evaluation_time - timedelta(days=1)
    day_candles = [candle for candle in base_history if ensure_aware(candle.timestamp) >= day_start]
    quote_volume = sum(
        candle.quote_volume if candle.quote_volume is not None else candle.close * candle.volume
        for candle in day_candles
    )
    all_candles = [
        candle
        for candles in candle_sets.values()
        for candle in candles
        if ensure_aware(candle.timestamp) <= evaluation_time
    ]
    ordered = all(
        left.timestamp < right.timestamp
        for candles in candle_sets.values()
        for left, right in zip(candles[:-1], candles[1:], strict=True)
    )
    unique = all(
        len(candles) == len({candle.timestamp for candle in candles})
        for candles in candle_sets.values()
    )
    timestamps = [ensure_aware(candle.timestamp) for candle in all_candles]
    base_asset, _, quote_asset = symbol.partition("/")
    provider_metadata = provider_metadata or {}
    return MarketSnapshot(
        exchange=strategy.universe.exchange,
        symbol=symbol,
        base_asset=base_asset,
        quote_asset=quote_asset,
        quote_volume_24h=provider_metadata.get("quote_volume_24h", quote_volume),
        average_candle_volume=average_volume,
        spread_bps=provider_metadata.get("spread_bps"),
        listed_at=provider_metadata.get("listed_at") or (min(timestamps) if timestamps else None),
        market_cap=provider_metadata.get("market_cap"),
        data_quality_ok=(ordered and unique and provider_metadata.get("data_quality_ok", True)),
        exchange_available=provider_metadata.get("exchange_available", True),
        metadata={
            "snapshot_source": "historical_candles_and_provider_metadata",
            **provider_metadata,
        },
    )


class CcxtMarketDataProvider:
    """Shared, rate-limited CCXT clients for public spot-market data."""

    #: How many previous order-book snapshots to keep.
    #:
    #: The snapshot of one symbol exists only to compare against the *next* reading of the
    #: same symbol, so nothing needs the whole history. In the API this object is a
    #: process-wide singleton (`@lru_cache` on `api/dependencies.get_market_data_provider`),
    #: so before this cap the dictionary was never cleared for the life of the process: one
    #: entry per symbol ever seen, kept for ever. The worker did not suffer from it because
    #: every task closes its own provider.
    #:
    #: 4000 is comfortably above the number of spot symbols on any one exchange, so in
    #: normal running nothing is ever evicted and the comparison always finds its previous
    #: reading. The cap exists to make the growth *bounded*, not to make it small.
    _MAX_ORDER_BOOK_SNAPSHOTS = 4000

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings
        self._clients: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Insertion-ordered, and re-inserted on every write, so the first key is always the
        # least recently written one. A plain dict is enough for that in Python 3.7+.
        self._order_book_snapshots: dict[tuple[str, str], dict[str, Any]] = {}

    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        client = await self._client(exchange)
        markets = await client.load_markets()
        quotes = {quote.upper() for quote in quote_currencies}
        return sorted(
            {
                str(market["symbol"]).upper().split(":", 1)[0]
                for market in markets.values()
                if market.get("spot")
                and market.get("active", True)
                and str(market.get("quote", "")).upper() in quotes
            }
        )

    async def fetch_ohlcv(
        self, exchange: str, symbol: str, timeframe: str, limit: int
    ) -> list[Candle]:
        end = datetime.now(UTC)
        start = end - timeframe_duration(timeframe) * max(2, limit + 2)
        return await self.fetch_ohlcv_range(
            exchange,
            symbol,
            timeframe,
            start,
            end,
            limit,
        )

    async def fetch_ohlcv_range(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[Candle]:
        """The newest up-to ``limit`` candles inside ``[start, end]``.

        The paging used to stop as soon as ``limit`` rows had been collected. An
        exchange returns candles *forward* from ``since``, so stopping on a count
        stopped at the **oldest** end of the window and the walk never reached ``end``.
        Every reading therefore arrived about three candles stale — three hours behind
        on an hourly monitor — while the scan itself had run seconds earlier. The walk
        now ends where the caller asked it to end, and the newest ``limit`` are kept.
        """

        client = await self._client(exchange)
        start = ensure_aware(start)
        end = ensure_aware(end)
        duration = timeframe_duration(timeframe)
        duration_ms = max(1, int(duration.total_seconds() * 1000))
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        maximum = max(1, min(50_000, limit))
        # How many rows to ask for per call. Deliberately **not** "however many are
        # still missing": that is what tied the walk's reach to the caller's count. The
        # small headroom lets a window drawn as "the last N candles" finish in one call
        # instead of two, which matters when a scan does this once per market.
        page_rows = min(_MAXIMUM_PAGE_ROWS, max(maximum + 2, _MINIMUM_PAGE_ROWS))
        # A window wider than the call budget can walk is started nearer its end. The
        # caller asked for the newest candles up to `end`; spending the whole budget at
        # the far side of a very long stretch would answer with the oldest instead, and
        # answer confidently. No real caller draws such a window — every one of them
        # sizes it to the count it wants — and this makes the promise true anyway.
        reach_ms = _MAXIMUM_PAGES * page_rows * duration_ms
        if end_ms - start_ms > reach_ms:
            start_ms = end_ms - reach_ms
        cursor_ms = start_ms
        remaining_pages = _page_budget(start_ms, end_ms, duration_ms, page_rows)
        rows_by_timestamp: dict[int, list[Any]] = {}
        while cursor_ms <= end_ms and remaining_pages > 0:
            remaining_pages -= 1
            rows = await client.fetch_ohlcv(
                symbol,
                timeframe=timeframe,
                since=cursor_ms,
                limit=page_rows,
            )
            if not rows:
                break
            previous_cursor = cursor_ms
            for row in rows:
                timestamp_ms = int(row[0])
                if start_ms <= timestamp_ms <= end_ms:
                    rows_by_timestamp[timestamp_ms] = row
            cursor_ms = int(rows[-1][0]) + duration_ms
            if cursor_ms <= previous_cursor:
                break
        now = datetime.now(UTC)
        candles: list[Candle] = []
        for row in sorted(rows_by_timestamp.values(), key=lambda item: item[0])[-maximum:]:
            timestamp = datetime.fromtimestamp(row[0] / 1000, tz=UTC)
            candles.append(
                Candle(
                    timestamp=timestamp,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    is_closed=timestamp + duration <= now,
                    quote_volume=float(row[4]) * float(row[5]),
                )
            )
        return candles

    async def fetch_universe_metadata(
        self,
        exchange: str,
        symbols: list[str],
        *,
        include_listing_dates: bool = False,
    ) -> dict[str, dict[str, Any]]:
        client = await self._client(exchange)
        markets = await client.load_markets()
        normalized_symbols = sorted({_canonical_symbol(symbol) for symbol in symbols})
        # Every quote currency in the universe gets a BTC benchmark to compare against —
        # except a BTC-quoted pair, whose benchmark would be `BTC/BTC`.
        #
        # `BTC/BTC` is not listed on any exchange, so asking for it left `still_missing()`
        # permanently non-empty. That is the condition that reaches for the unfiltered
        # `fetch_tickers()` in `_fetch_tickers`, which costs 4 seconds of rate-limit sleep
        # (weight 80) and downloads about 1.9 MB covering every market on the exchange.
        # A single `*/BTC` market in a universe therefore paid that price on every symbol
        # of every scan, for ever, to look up a price that is 1 by definition.
        benchmark_symbols = {
            f"BTC/{quote}"
            for quote in {
                symbol.partition("/")[2].upper() for symbol in normalized_symbols if "/" in symbol
            }
            if quote != "BTC"
        }
        ticker_symbols = sorted({*normalized_symbols, *benchmark_symbols})
        tickers = await self._fetch_tickers(client, ticker_symbols)

        benchmark_percentages: dict[str, float] = {}
        for quote in {
            symbol.partition("/")[2].upper() for symbol in normalized_symbols if "/" in symbol
        }:
            benchmark = tickers.get(f"BTC/{quote}") or {}
            percentage = _number(benchmark.get("percentage"))
            if percentage is not None:
                benchmark_percentages[quote] = percentage

        metadata: dict[str, dict[str, Any]] = {}
        missing_listing_dates: list[str] = []
        for symbol in normalized_symbols:
            market = markets.get(symbol) or markets.get(symbol.replace("/", ""))
            market = market if isinstance(market, dict) else {}
            ticker = tickers.get(symbol, {})
            ticker_info = ticker.get("info") if isinstance(ticker.get("info"), dict) else {}
            bid = _number(ticker.get("bid"))
            ask = _number(ticker.get("ask"))
            midpoint = (
                (bid + ask) / 2
                if bid is not None and ask is not None and bid > 0 and ask > 0
                else None
            )
            spread_bps = (
                ((ask - bid) / midpoint) * 10_000
                if midpoint is not None
                and bid is not None
                and ask is not None
                and ask >= bid
                else None
            )
            quote = symbol.partition("/")[2].upper()
            percentage = _number(ticker.get("percentage"))
            benchmark_percentage = benchmark_percentages.get(quote)
            listed_at = _listing_datetime(market)
            if include_listing_dates and listed_at is None:
                missing_listing_dates.append(symbol)
            metadata[symbol] = {
                "asset_name": str(market.get("base") or symbol.partition("/")[0]).upper(),
                "quote_volume_24h": _number(
                    ticker.get("quoteVolume")
                    or ticker.get("quote_volume")
                    or ticker_info.get("turnover24h")
                ),
                "base_volume_24h": _number(ticker.get("baseVolume")),
                "bid": bid,
                "ask": ask,
                "last": _number(ticker.get("last")),
                "bid_size": _number(ticker.get("bidVolume") or ticker_info.get("bid1Size")),
                "ask_size": _number(ticker.get("askVolume") or ticker_info.get("ask1Size")),
                "spread_bps": round(spread_bps, 6) if spread_bps is not None else None,
                "percentage_24h": percentage,
                "high_24h": _number(ticker.get("high")),
                "low_24h": _number(ticker.get("low")),
                "relative_strength_btc": (
                    round(percentage - benchmark_percentage, 6)
                    if percentage is not None and benchmark_percentage is not None
                    else None
                ),
                "listed_at": listed_at,
                "category": None,
                "market_cap": None,
                "data_quality_ok": bool(ticker) and market.get("active", True),
                "exchange_available": True,
                "metadata_source": "ccxt",
            }

        if missing_listing_dates:
            semaphore = asyncio.Semaphore(
                self.settings.universe_metadata_concurrency if self.settings else 8
            )

            async def probe(symbol: str) -> tuple[str, datetime | None]:
                async with semaphore:
                    try:
                        rows = await client.fetch_ohlcv(
                            symbol,
                            timeframe="1d",
                            since=0,
                            limit=1,
                        )
                        if rows:
                            return (
                                symbol,
                                datetime.fromtimestamp(int(rows[0][0]) / 1000, tz=UTC),
                            )
                    except Exception:
                        pass
                    return symbol, None

            for symbol, listed_at in await asyncio.gather(
                *(probe(symbol) for symbol in missing_listing_dates)
            ):
                metadata[symbol]["listed_at"] = listed_at
                if listed_at is not None:
                    metadata[symbol]["listing_source"] = "earliest_available_daily_candle"

        enrichment = await self._external_market_metadata(exchange, normalized_symbols)
        for symbol, values in enrichment.items():
            if symbol in metadata:
                metadata[symbol].update(
                    {
                        key: value
                        for key, value in values.items()
                        if key in {"category", "market_cap"} and value is not None
                    }
                )
                metadata[symbol]["metadata_source"] = "ccxt+configured_market_metadata"
        return metadata

    async def _fetch_tickers(self, client: Any, symbols: list[str]) -> dict[str, Any]:
        """Read tickers for a symbol universe of any size.

        One request that names every symbol is refused once the universe grows past
        a few hundred pairs (see ``_TICKER_BATCH_SIZE``). Three reads are tried in
        order, each one narrower than the last, and every symbol a read does deliver
        is kept:

        1. batches of ``_TICKER_BATCH_SIZE`` symbols, so one refused batch costs only
           the symbols it named rather than the whole market;
        2. one unfiltered read, which names nothing in the query string and therefore
           covers whatever the batches could not deliver;
        3. a bounded per-symbol probe for whatever is still missing.

        A symbol that no read returns keeps no ticker. The caller marks it as having
        no verified data instead of substituting a value from somewhere else.
        """

        tickers: dict[str, Any] = {}

        def absorb(raw: Any) -> None:
            if not isinstance(raw, dict):
                return
            for symbol, value in raw.items():
                if isinstance(value, dict):
                    tickers[_canonical_symbol(symbol)] = value

        def still_missing() -> list[str]:
            return [symbol for symbol in symbols if symbol not in tickers]

        for start in range(0, len(symbols), _TICKER_BATCH_SIZE):
            try:
                absorb(await client.fetch_tickers(symbols[start : start + _TICKER_BATCH_SIZE]))
            except Exception:
                continue

        if still_missing():
            with suppress(Exception):
                absorb(await client.fetch_tickers())

        for symbol in still_missing()[:_TICKER_PROBE_LIMIT]:
            try:
                absorb({symbol: await client.fetch_ticker(symbol)})
            except Exception:
                continue

        return tickers

    async def fetch_order_book_context(
        self,
        exchange: str,
        symbol: str,
        *,
        depth: int = 50,
    ) -> dict[str, Any]:
        client = await self._client(exchange)
        order_book, trades = await asyncio.gather(
            client.fetch_order_book(symbol, limit=max(10, min(depth, 500))),
            self._fetch_recent_trades(client, symbol),
        )
        bids = [
            (float(price), float(amount))
            for price, amount, *_ in order_book.get("bids", [])
            if float(price) > 0 and float(amount) > 0
        ]
        asks = [
            (float(price), float(amount))
            for price, amount, *_ in order_book.get("asks", [])
            if float(price) > 0 and float(amount) > 0
        ]
        if not bids or not asks:
            raise ValueError("Order book did not contain both bid and ask levels")
        best_bid, best_ask = bids[0][0], asks[0][0]
        midpoint = (best_bid + best_ask) / 2
        bid_depth = sum(price * amount for price, amount in bids)
        ask_depth = sum(price * amount for price, amount in asks)
        total_depth = bid_depth + ask_depth
        imbalance = (bid_depth - ask_depth) / total_depth if total_depth else 0
        bid_average = bid_depth / len(bids)
        ask_average = ask_depth / len(asks)
        largest_bid = max(bids, key=lambda item: item[0] * item[1])
        largest_ask = max(asks, key=lambda item: item[0] * item[1])
        large_wall_below = largest_bid[0] * largest_bid[1] >= bid_average * 5
        large_wall_above = largest_ask[0] * largest_ask[1] >= ask_average * 5
        wall_distance = min(
            abs(midpoint - largest_bid[0]) / midpoint * 100,
            abs(largest_ask[0] - midpoint) / midpoint * 100,
        )
        trade_values: list[dict[str, Any]] = [
            {
                "side": str(item.get("side") or "").lower(),
                "quote": float(item.get("cost") or 0)
                or float(item.get("price") or 0) * float(item.get("amount") or 0),
                "timestamp": int(item.get("timestamp") or 0),
            }
            for item in trades
            if isinstance(item, dict)
        ]
        buy_volume = sum(item["quote"] for item in trade_values if item["side"] == "buy")
        sell_volume = sum(item["quote"] for item in trade_values if item["side"] == "sell")
        trade_volume = buy_volume + sell_volume
        average_trade_size = trade_volume / len(trade_values) if trade_values else 0
        latest_timestamp = max(
            (item["timestamp"] for item in trade_values),
            default=int(datetime.now(UTC).timestamp() * 1000),
        )
        recent_trade_volume = sum(
            item["quote"] for item in trade_values if latest_timestamp - item["timestamp"] <= 60_000
        )
        key = (exchange.lower(), _canonical_symbol(symbol))
        previous = self._order_book_snapshots.get(key, {})
        current_wall_quote = max(
            largest_bid[0] * largest_bid[1],
            largest_ask[0] * largest_ask[1],
        )
        previous_wall_quote = _number(previous.get("largest_wall_quote"))
        result = {
            "spread_bps": ((best_ask - best_bid) / midpoint) * 10_000,
            "bid_depth_quote": bid_depth,
            "ask_depth_quote": ask_depth,
            "total_depth_quote": total_depth,
            "depth_imbalance": imbalance,
            "large_wall_above": large_wall_above,
            "large_wall_below": large_wall_below,
            "largest_wall_quote": current_wall_quote,
            "approaching_liquidity_wall": wall_distance <= 0.5,
            "liquidity_wall_added": (
                previous_wall_quote is not None and current_wall_quote >= previous_wall_quote * 1.5
            ),
            "liquidity_wall_pulled": (
                previous_wall_quote is not None and current_wall_quote <= previous_wall_quote * 0.5
            ),
            "slippage_bps": _estimated_slippage_bps(asks, midpoint, 10_000),
            "trade_count": len(trade_values),
            "trade_count_ratio": (
                len(trade_values) / float(previous.get("trade_count") or len(trade_values) or 1)
            ),
            "average_trade_size": average_trade_size,
            "average_trade_size_ratio": (
                average_trade_size
                / float(previous.get("average_trade_size") or average_trade_size or 1)
            ),
            "buy_volume_ratio": buy_volume / trade_volume if trade_volume else 0,
            "sell_volume_ratio": sell_volume / trade_volume if trade_volume else 0,
            "trade_imbalance": ((buy_volume - sell_volume) / trade_volume if trade_volume else 0),
            "recent_trade_volume": recent_trade_volume,
            "captured_at": datetime.now(UTC).isoformat(),
        }
        self._remember_order_book_snapshot(key, result)
        return result

    def _remember_order_book_snapshot(
        self, key: tuple[str, str], snapshot: dict[str, Any]
    ) -> None:
        """Store one symbol's snapshot, keeping the store bounded.

        Re-inserting moves the key to the end, so the oldest write is always first and
        eviction takes it. See ``_MAX_ORDER_BOOK_SNAPSHOTS``.
        """
        self._order_book_snapshots.pop(key, None)
        self._order_book_snapshots[key] = snapshot
        while len(self._order_book_snapshots) > self._MAX_ORDER_BOOK_SNAPSHOTS:
            oldest = next(iter(self._order_book_snapshots))
            del self._order_book_snapshots[oldest]

    async def fetch_derivatives_context(
        self,
        exchange: str,
        symbol: str,
    ) -> dict[str, Any]:
        client = await self._client(exchange)
        markets = await client.load_markets()
        spot = markets.get(symbol) or {}
        base = str(spot.get("base") or symbol.partition("/")[0]).upper()
        quote = str(spot.get("quote") or symbol.partition("/")[2]).upper()
        contract_symbol = next(
            (
                str(market.get("symbol"))
                for market in markets.values()
                if market.get("contract")
                and market.get("linear", True)
                and str(market.get("base") or "").upper() == base
                and str(market.get("quote") or "").upper() == quote
                and market.get("active", True)
            ),
            None,
        )
        if not contract_symbol:
            return {}
        result: dict[str, Any] = {"contract_symbol": contract_symbol}
        try:
            funding = await client.fetch_funding_rate(contract_symbol)
            result["funding_rate"] = _number(funding.get("fundingRate"))
        except Exception:
            pass
        try:
            current = await client.fetch_open_interest(contract_symbol)
            result["open_interest"] = _number(
                current.get("openInterestAmount")
                or current.get("openInterestValue")
                or (current.get("info") or {}).get("openInterest")
            )
        except Exception:
            pass
        history_loader = getattr(client, "fetch_open_interest_history", None)
        if callable(history_loader):
            try:
                history = await history_loader(contract_symbol, timeframe="1h", limit=2)
                if history:
                    previous = history[-2] if len(history) > 1 else history[0]
                    result["previous_open_interest"] = _number(
                        previous.get("openInterestAmount")
                        or previous.get("openInterestValue")
                        or (previous.get("info") or {}).get("openInterest")
                    )
            except Exception:
                pass
        return result

    async def close(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            await client.close()

    async def _external_market_metadata(
        self,
        exchange: str,
        symbols: list[str],
    ) -> dict[str, dict[str, Any]]:
        if self.settings is None or self.settings.market_metadata_api_url is None:
            return {}
        headers = {"Content-Type": "application/json"}
        if self.settings.market_metadata_api_key is not None:
            headers["Authorization"] = (
                f"Bearer {self.settings.market_metadata_api_key.get_secret_value()}"
            )
        try:
            response = await provider_request(
                self.settings,
                "POST",
                str(self.settings.market_metadata_api_url),
                provider="market_metadata",
                operation="symbol_metadata",
                timeout=self.settings.market_metadata_timeout_seconds,
                # A metadata lookup changes nothing, so a transient failure is worth
                # another attempt.
                mutation_committed=False,
                headers=headers,
                json={"exchange": exchange, "symbols": symbols},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ProviderCallError, ValueError, TypeError):
            return {}
        rows = payload.get("symbols", {}) if isinstance(payload, dict) else {}
        if not isinstance(rows, dict):
            return {}
        return {
            _canonical_symbol(symbol): values
            for symbol, values in rows.items()
            if isinstance(values, dict)
        }

    @staticmethod
    async def _fetch_recent_trades(client, symbol: str) -> list[dict[str, Any]]:
        try:
            rows = await client.fetch_trades(symbol, limit=200)
            return [item for item in rows if isinstance(item, dict)]
        except Exception:
            return []

    async def _client(self, exchange: str):
        key = exchange.lower()
        existing = self._clients.get(key)
        if existing is not None:
            return existing
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            existing = self._clients.get(key)
            if existing is not None:
                return existing
            import ccxt.async_support as ccxt

            exchange_class = getattr(ccxt, key, None)
            if exchange_class is None:
                raise ValueError(f"Unsupported exchange: {exchange}")
            config: dict[str, Any] = {
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                    "fetchMarkets": ["spot"],
                },
            }
            if key == "binance" and self.settings is not None:
                base_url = str(self.settings.binance_rest_base_url).rstrip("/")
                if base_url != "https://api.binance.com":
                    config["urls"] = {"api": {"public": f"{base_url}/api/v3"}}
            if key == "bybit" and self.settings is not None:
                base_url = str(self.settings.bybit_rest_base_url).rstrip("/")
                if base_url != "https://api.bybit.com":
                    config["urls"] = {"api": {"public": base_url, "private": base_url}}
            client = exchange_class(config)
            self._clients[key] = client
            return client


def _canonical_symbol(symbol: str) -> str:
    return str(symbol).upper().replace("-", "/").strip().split(":", 1)[0]


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _listing_datetime(market: dict[str, Any]) -> datetime | None:
    raw_info = market.get("info")
    info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
    for key in (
        "onboardDate",
        "launchTime",
        "launch_time",
        "createdTime",
        "created_at",
        "listingTime",
    ):
        raw = info.get(key) or market.get(key)
        if raw is None or raw == "":
            continue
        try:
            numeric = float(raw)
            if numeric > 10_000_000_000:
                numeric /= 1000
            return datetime.fromtimestamp(numeric, tz=UTC)
        except (TypeError, ValueError, OSError):
            try:
                parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                return ensure_aware(parsed)
            except ValueError:
                continue
    return None


def _estimated_slippage_bps(
    levels: list[tuple[float, float]],
    midpoint: float,
    quote_notional: float,
) -> float:
    remaining = quote_notional
    acquired = 0.0
    spent = 0.0
    for price, amount in levels:
        level_quote = price * amount
        used_quote = min(remaining, level_quote)
        acquired += used_quote / price
        spent += used_quote
        remaining -= used_quote
        if remaining <= 0:
            break
    if acquired <= 0 or remaining > 0:
        return float("inf")
    average_price = spent / acquired
    return abs(average_price - midpoint) / midpoint * 10_000


class MarketPreviewService:
    def __init__(
        self,
        provider: MarketDataProvider,
        *,
        candle_limit: int = 300,
        symbol_limit: int = 5,
        engine: StrategyRuleEngine | None = None,
        settings: Settings | None = None,
    ):
        self.provider = provider
        self.candle_limit = candle_limit
        self.symbol_limit = symbol_limit
        self.engine = engine or StrategyRuleEngine()
        self.context = ProviderContextService(provider, settings)

    async def run(self, strategy: StrategyDefinition) -> MarketPreviewResponse:
        warnings: list[str] = []
        try:
            if strategy.universe.include_symbols:
                symbols = strategy.universe.include_symbols
            else:
                try:
                    symbols = await self.provider.list_symbols(
                        strategy.universe.exchange, strategy.universe.quote_currencies
                    )
                except Exception as exc:
                    symbols = self._fallback_symbols(strategy)
                    warnings.append(
                        "Could not load the full exchange universe for preview; "
                        f"used common {', '.join(strategy.universe.quote_currencies)} pairs "
                        f"instead ({type(exc).__name__})."
                    )
            if not symbols:
                symbols = self._fallback_symbols(strategy)
            symbols = symbols[: self.symbol_limit]
            if not symbols:
                raise ValueError("No symbols matched the selected universe")

            timeframes = {strategy.base_timeframe, *strategy.supporting_timeframes}
            matches: list[dict[str, Any]] = []
            candles_checked = 0
            latest_timestamp: datetime | None = None
            for symbol in symbols:
                try:
                    candle_sets = {
                        timeframe: await self.provider.fetch_ohlcv(
                            strategy.universe.exchange, symbol, timeframe, self.candle_limit
                        )
                        for timeframe in timeframes
                    }
                except Exception as exc:
                    warnings.append(
                        f"Skipped {symbol}: recent candles were unavailable ({type(exc).__name__})."
                    )
                    continue
                base = candle_sets[strategy.base_timeframe]
                if not base:
                    warnings.append(f"Skipped {symbol}: no base-timeframe candles returned.")
                    continue
                candles_checked += sum(len(candles) for candles in candle_sets.values())
                if base:
                    latest_timestamp = max(
                        latest_timestamp or base[-1].timestamp, base[-1].timestamp
                    )
                previous_score: float | None = None
                metadata_loader = getattr(self.provider, "fetch_universe_metadata", None)
                metadata = {}
                if callable(metadata_loader):
                    try:
                        metadata = (
                            await metadata_loader(strategy.universe.exchange, [symbol])
                        ).get(symbol, {})
                    except Exception:
                        metadata = {}
                for candle in base[-50:]:
                    market = market_snapshot_from_candles(
                        strategy,
                        symbol,
                        candle_sets,
                        candle.timestamp,
                        metadata,
                    )
                    condition_context = await self.context.build(
                        strategy,
                        symbol,
                        candle_sets,
                        candle.timestamp,
                        universe_symbols=symbols,
                    )
                    result = self.engine.evaluate(
                        strategy,
                        market,
                        candle_sets,
                        evaluation_time=candle.timestamp,
                        strategy_version=f"preview:{strategy.canonical_hash()[:12]}",
                        market_data_provider=type(self.provider).__name__,
                        previous_score=previous_score,
                        condition_context=condition_context,
                    )
                    previous_score = result.near_miss.current_score
                    if result.outcome in {ScanOutcome.CONFIRMED, ScanOutcome.NEAR_MISS}:
                        matches.append(
                            {
                                "exchange": strategy.universe.exchange,
                                "symbol": symbol,
                                "timeframe": strategy.base_timeframe,
                                "candle_timestamp": candle.timestamp.isoformat(),
                                "completion_score": round(result.near_miss.current_score, 2),
                                "outcome": result.outcome.value,
                                "proof": result.proof_receipt(),
                            }
                        )
            if not matches:
                warnings.append(
                    "No recent complete or Near-Miss examples were found. "
                    "This is not a validation failure."
                )
            if candles_checked == 0:
                raise ValueError("No recent candles were available for preview symbols")
            return MarketPreviewResponse(
                status="succeeded",
                symbols_checked=len(symbols),
                candles_checked=candles_checked,
                sample_matches=matches[-10:],
                warnings=warnings,
                data_as_of=latest_timestamp.isoformat() if latest_timestamp else None,
            )
        except Exception as exc:
            return MarketPreviewResponse(
                status="failed",
                symbols_checked=0,
                candles_checked=0,
                sample_matches=[],
                warnings=[f"Recent-market preview failed: {type(exc).__name__}: {exc}"],
                data_as_of=None,
            )

    @staticmethod
    def _fallback_symbols(strategy: StrategyDefinition) -> list[str]:
        symbols: list[str] = []
        for quote in strategy.universe.quote_currencies:
            symbols.extend(DEFAULT_PREVIEW_SYMBOLS_BY_QUOTE.get(quote.upper(), []))
        excluded = {symbol.upper() for symbol in strategy.universe.exclude_symbols}
        return [symbol for symbol in symbols if symbol.upper() not in excluded]
