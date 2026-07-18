import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models.enums import ScanOutcome
from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.engine.models import MarketSnapshot, ensure_aware
from ai_market_monitor.provider_context import ProviderContextService
from ai_market_monitor.schemas.onboarding import MarketPreviewResponse
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.interfaces import Candle, MarketDataProvider

DEFAULT_PREVIEW_SYMBOLS_BY_QUOTE = {
    "USDT": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"],
    "USDC": ["BTC/USDC", "ETH/USDC", "SOL/USDC"],
}


def timeframe_duration(timeframe: str) -> timedelta:
    value = int(timeframe[:-1])
    unit = timeframe[-1]
    if unit == "m":
        return timedelta(minutes=value)
    if unit == "h":
        return timedelta(hours=value)
    if unit == "d":
        return timedelta(days=value)
    raise ValueError(f"Unsupported timeframe: {timeframe}")


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

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings
        self._clients: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}
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
        client = await self._client(exchange)
        start = ensure_aware(start)
        end = ensure_aware(end)
        duration = timeframe_duration(timeframe)
        duration_ms = max(1, int(duration.total_seconds() * 1000))
        cursor_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        maximum = max(1, min(50_000, limit))
        rows_by_timestamp: dict[int, list[Any]] = {}
        while cursor_ms < end_ms and len(rows_by_timestamp) < maximum:
            page_limit = min(1000, maximum - len(rows_by_timestamp))
            rows = await client.fetch_ohlcv(
                symbol,
                timeframe=timeframe,
                since=cursor_ms,
                limit=page_limit,
            )
            if not rows:
                break
            previous_cursor = cursor_ms
            for row in rows:
                timestamp_ms = int(row[0])
                if cursor_ms <= timestamp_ms <= end_ms:
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
        benchmark_symbols = {
            f"BTC/{symbol.partition('/')[2].upper()}"
            for symbol in normalized_symbols
            if "/" in symbol
        }
        ticker_symbols = sorted({*normalized_symbols, *benchmark_symbols})
        tickers: dict[str, Any] = {}
        try:
            raw_tickers = await client.fetch_tickers(ticker_symbols)
            tickers = {
                _canonical_symbol(symbol): value
                for symbol, value in raw_tickers.items()
                if isinstance(value, dict)
            }
        except Exception:
            if len(ticker_symbols) <= 20:
                for symbol in ticker_symbols:
                    try:
                        ticker = await client.fetch_ticker(symbol)
                    except Exception:
                        continue
                    if isinstance(ticker, dict):
                        tickers[symbol] = ticker

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
        self._order_book_snapshots[key] = result
        return result

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
            async with httpx.AsyncClient(
                timeout=self.settings.market_metadata_timeout_seconds
            ) as client:
                response = await client.post(
                    str(self.settings.market_metadata_api_url),
                    headers=headers,
                    json={"exchange": exchange, "symbols": symbols},
                )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
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
                "options": {"defaultType": "spot"},
            }
            if key == "binance" and self.settings is not None:
                api_key = self.settings.binance_api_key
                api_secret = self.settings.binance_api_secret
                if api_key is not None and api_secret is not None:
                    config["apiKey"] = api_key.get_secret_value()
                    config["secret"] = api_secret.get_secret_value()
                base_url = str(self.settings.binance_rest_base_url).rstrip("/")
                if base_url != "https://api.binance.com":
                    config["urls"] = {"api": {"public": f"{base_url}/api/v3"}}
            if key == "bybit" and self.settings is not None:
                api_key = self.settings.bybit_api_key
                api_secret = self.settings.bybit_api_secret
                if api_key is not None and api_secret is not None:
                    config["apiKey"] = api_key.get_secret_value()
                    config["secret"] = api_secret.get_secret_value()
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
