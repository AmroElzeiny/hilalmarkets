"""One market-data adapter for every test that asks what a scan can read.

Two invariant files build a condition context the way a scan does, and both need an
adapter to build it from. Written twice, the two would answer differently — which is the
failure this repository keeps meeting: one stub returns a full order book, the other
returns ``{}``, and the same card passes in one file and reports "unavailable" in the
other with nothing to say which is right.

The important rule here is what this adapter refuses to do. **It never returns a card's
answer.** It returns candles, universe metadata, and a raw order book of the shape ccxt
hands back — and the product's own readers turn those into card values. The order book in
particular runs through `CcxtMarketDataProvider.fetch_order_book_context`, the real
spread, depth, wall and trade-flow maths, so an order-book card passes only when that
code works.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.interfaces import Candle
from ai_market_monitor.services.market_preview import CcxtMarketDataProvider

TEST_SETTINGS = Settings(app_secret_key="test-secret-key-with-at-least-thirty-two-characters")


def wavy_candles(
    count: int = 400,
    *,
    seed: int = 7,
    drift: float = 0.9,
    start: datetime | None = None,
) -> list[Candle]:
    """A long, closed, wavy history — enough warm-up for every card's lookback.

    Each step is a fraction of the current price, so the series never reaches a floor
    and flattens; a flat series makes unrelated cards look broken.
    """

    generator = random.Random(seed)
    origin = start or datetime(2026, 1, 1, tzinfo=UTC)
    price = 100.0
    rows: list[Candle] = []
    for index in range(count):
        rate = (math.sin(index / 9) * 1.5 + generator.uniform(-0.8, 0.8) + drift) / 100
        open_price = price
        close = price * (1 + rate)
        volume = 1000 + generator.uniform(0, 500) + index
        rows.append(
            Candle(
                timestamp=origin + timedelta(minutes=15 * index),
                open=open_price,
                high=max(open_price, close) * 1.004,
                low=min(open_price, close) * 0.996,
                close=close,
                volume=volume,
                is_closed=True,
                quote_volume=volume * close,
            )
        )
        price = close
    return rows


class FakeExchangeClient:
    """The two calls the order-book reader makes, in the shape ccxt returns them.

    One deep bid wall, so the wall and imbalance cards have something real to find, and
    the wall grows between calls, because "a wall was added" is a card about a *change*
    and a single snapshot cannot answer a change.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def fetch_order_book(self, symbol: str, limit: int = 100) -> dict[str, Any]:
        self.calls += 1
        wall = 400.0 * self.calls
        bids: list[list[float]] = [[100.0 - index * 0.01, 5.0] for index in range(50)]
        bids[2] = [99.98, wall]
        asks: list[list[float]] = [[100.02 + index * 0.01, 5.0] for index in range(50)]
        return {"bids": bids, "asks": asks}

    async def fetch_trades(self, symbol: str, limit: int = 200) -> list[dict[str, Any]]:
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        return [
            {
                "side": "buy" if index % 4 else "sell",
                "cost": 900.0 + index,
                "price": 100.0,
                "amount": 9.0,
                "timestamp": now_ms - index * 100,
            }
            for index in range(120)
        ]


async def real_order_book_context(exchange: str, symbol: str, depth: int = 100) -> dict[str, Any]:
    """Run the product's own order-book reader over a fake raw book.

    Pre-seeding the client cache is what keeps the real maths in the path without
    reaching a network. Called twice: the second reading is the one with a previous
    snapshot behind it, which the two "wall added / wall pulled" cards compare against.
    """

    provider = CcxtMarketDataProvider(TEST_SETTINGS)
    provider._clients[exchange.lower()] = FakeExchangeClient()
    await provider.fetch_order_book_context(exchange, symbol, depth=depth)
    return await provider.fetch_order_book_context(exchange, symbol, depth=depth)


class ScanMarketDataAdapter:
    """Everything `ProviderContextService` asks a market-data adapter for, and no more."""

    def __init__(
        self,
        *,
        symbol: str = "SOL/USDT",
        universe: tuple[str, ...] = ("SOL/USDT", "BTC/USDT", "ETH/USDT", "LINK/USDT"),
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.symbol = symbol
        self.universe = universe
        self.metadata = metadata or {}

    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        return list(self.universe)

    async def fetch_ohlcv(
        self, exchange: str, symbol: str, timeframe: str, limit: int
    ) -> list[Candle]:
        return self._history(symbol, max(limit, 400))[-limit:]

    async def fetch_ohlcv_range(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[Candle]:
        return self._history(symbol, max(limit, 400))[-limit:]

    async def fetch_universe_metadata(
        self,
        exchange: str,
        symbols: list[str],
        *,
        include_listing_dates: bool = False,
    ) -> dict[str, dict[str, Any]]:
        volumes = {
            "BTC/USDT": 1_000_000_000.0,
            "ETH/USDT": 500_000_000.0,
            "SOL/USDT": 900_000_000.0,
            "LINK/USDT": 50_000_000.0,
        }
        return {
            symbol: {
                **self.metadata,
                "quote_volume_24h": volumes.get(symbol, 10_000_000.0),
                "relative_strength_btc": 4.0 if symbol == self.symbol else 1.0,
            }
            for symbol in symbols
        }

    async def fetch_order_book_context(
        self, exchange: str, symbol: str, *, depth: int = 50
    ) -> dict[str, Any]:
        return await real_order_book_context(exchange, symbol, depth)

    async def fetch_derivatives_context(self, exchange: str, symbol: str) -> dict[str, Any]:
        # Deliberately empty. Futures context is switched off for a spot product, and a
        # test that quietly supplied it would hide that.
        return {}

    @staticmethod
    def _history(symbol: str, count: int) -> list[Candle]:
        drift = {"SOL/USDT": 1.2, "ETH/USDT": 0.9, "BTC/USDT": 0.7}.get(symbol, 0.4)
        seed = {"BTC/USDT": 11, "ETH/USDT": 13, "LINK/USDT": 17}.get(symbol, 7)
        return wavy_candles(count, seed=seed, drift=drift)
