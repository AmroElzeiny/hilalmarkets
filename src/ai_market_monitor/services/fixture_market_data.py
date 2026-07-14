from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ai_market_monitor.services.interfaces import Candle
from ai_market_monitor.services.market_preview import timeframe_duration


class FixtureMarketDataProvider:
    """Deterministic local market-data provider for tests and smoke scripts.

    Fixture data must never be labelled as live data. Runtime validation blocks this
    mode in staging and production.
    """

    provider_name = "fixture"
    _symbols = (
        "PUMP5/USDT",
        "RSI30/USDT",
        "VOLUME/USDT",
        "EMAUP/USDT",
        "VWAP/USDT",
        "SOL/USDT",
        "LINK/USDT",
        "NOPE/USDT",
        "SHORT/USDT",
    )

    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        quotes = {quote.upper() for quote in quote_currencies}
        return sorted(symbol for symbol in self._symbols if symbol.rsplit("/", 1)[-1] in quotes)

    async def fetch_ohlcv(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        limit: int,
    ) -> list[Candle]:
        candles = self._candles(symbol.upper(), timeframe, max(limit, 300))
        return candles[-limit:]

    async def fetch_ohlcv_range(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[Candle]:
        start = _aware(start)
        end = _aware(end)
        candles = [
            candle
            for candle in self._candles(symbol.upper(), timeframe, max(limit, 3000))
            if start <= candle.timestamp <= end
        ]
        return candles[-limit:]

    async def fetch_universe_metadata(
        self,
        exchange: str,
        symbols: list[str],
        *,
        include_listing_dates: bool = False,
    ) -> dict[str, dict[str, Any]]:
        listed_at = datetime(2024, 1, 1, tzinfo=UTC)
        return {
            symbol: {
                "quote_volume_24h": 25_000_000,
                "average_candle_volume": 2_500,
                "spread_bps": 4,
                "listed_at": listed_at if include_listing_dates else listed_at,
                "data_quality_ok": True,
                "exchange_available": True,
                "provider": self.provider_name,
                "fixture": True,
            }
            for symbol in symbols
        }

    async def fetch_order_book_context(
        self,
        exchange: str,
        symbol: str,
        *,
        depth: int = 50,
    ) -> dict[str, Any]:
        return {
            "spread_bps": 4,
            "depth_quote_notional": 1_000_000,
            "bid_ask_imbalance": 0.04,
            "provider": self.provider_name,
            "fixture": True,
        }

    async def fetch_derivatives_context(self, exchange: str, symbol: str) -> dict[str, Any]:
        return {
            "available": False,
            "provider": self.provider_name,
            "fixture": True,
            "reason": "Derivatives context is intentionally unavailable in fixture mode.",
        }

    def _candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        duration = timeframe_duration(timeframe)
        end = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)
        start = end - duration * (limit - 1)
        candles: list[Candle] = []
        for index in range(limit):
            timestamp = start + duration * index
            close = _close(symbol, index, limit)
            previous_close = _close(symbol, max(0, index - 1), limit)
            open_price = previous_close if index else close * 0.998
            high = max(open_price, close) * 1.003
            low = min(open_price, close) * 0.997
            volume = _volume(symbol, index, limit)
            candles.append(
                Candle(
                    timestamp=timestamp,
                    open=round(open_price, 6),
                    high=round(high, 6),
                    low=round(low, 6),
                    close=round(close, 6),
                    volume=round(volume, 6),
                    is_closed=True,
                    quote_volume=round(close * volume, 6),
                )
            )
        return candles


def _close(symbol: str, index: int, limit: int) -> float:
    progress = index / max(1, limit - 1)
    if symbol.startswith("RSI30"):
        return 130 - progress * 38 - (4 if index >= limit - 5 else 0)
    if symbol.startswith("PUMP5"):
        base = 100 + progress * 2
        return base * 1.06 if index == limit - 1 else base
    if symbol.startswith("EMAUP"):
        return 80 + progress * 40
    if symbol.startswith("VWAP"):
        return 96 + progress * 8 + (2 if index == limit - 1 else 0)
    if symbol.startswith("SHORT"):
        return 120 - progress * 20
    if symbol.startswith("SOL"):
        return 100 + progress * 6
    if symbol.startswith("LINK"):
        return 90 + progress * 3
    if symbol.startswith("VOLUME"):
        return 100 + progress
    return 100


def _volume(symbol: str, index: int, limit: int) -> float:
    if symbol.startswith("VOLUME") and index == limit - 1:
        return 2_400
    if symbol.startswith("SOL") and index == limit - 1:
        return 1_800
    if symbol.startswith("PUMP5") and index == limit - 1:
        return 1_500
    return 1_000


def _aware(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)
