from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID

from ai_market_monitor.core.config import Settings
from ai_market_monitor.schemas.sharia import (
    LiveSpotMarketQuote,
    LiveSpotMarketResponse,
    TestMethodologySummary,
)
from ai_market_monitor.services.interfaces import MarketDataProvider
from ai_market_monitor.services.sharia_screening import canonical_asset, canonical_symbol

TEST_METHODOLOGY_CODE = "TRACEDGE_DEV_TEST_V1"
TEST_METHODOLOGY_NOTICE = (
    "Test is a permissive development methodology: every active spot pair is marked eligible "
    "only to test the product workflow. It is not a fatwa, Sharia assessment, or production "
    "screening conclusion."
)
_LOGO_CDN_BASE = "https://cdn.jsdelivr.net/npm/@web3icons/core@4.0.53/dist/svgs/tokens/branded"
_SAFE_ASSET = re.compile(r"^[A-Z0-9]{1,24}$")


@dataclass(slots=True)
class _QuoteCacheEntry:
    snapshot: LiveSpotMarketResponse
    expires_at: float


class TestMarketQuoteService:
    """Provider-backed quotes for the explicit development-only Test methodology."""

    _cache: dict[tuple[int, str, str], _QuoteCacheEntry] = {}
    _locks: dict[tuple[int, str, str], asyncio.Lock] = {}

    def __init__(self, provider: MarketDataProvider, settings: Settings):
        self.provider = provider
        self.settings = settings

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache.clear()
        cls._locks.clear()

    async def snapshot(
        self,
        *,
        exchange: str,
        quote_asset: str,
        methodology_id: UUID | None = None,
    ) -> LiveSpotMarketResponse:
        exchange_key = exchange.strip().lower()
        quote_key = quote_asset.strip().upper()
        cache_key = (id(self.provider), exchange_key, quote_key)
        now = monotonic()
        cached = self._cache.get(cache_key)
        if cached is not None and cached.expires_at > now:
            return cached.snapshot

        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            now = monotonic()
            cached = self._cache.get(cache_key)
            if cached is not None and cached.expires_at > now:
                return cached.snapshot
            try:
                snapshot = await self._load_snapshot(
                    exchange=exchange_key,
                    quote_asset=quote_key,
                    methodology_id=methodology_id,
                )
            except Exception:
                if cached is None:
                    raise
                return cached.snapshot.model_copy(
                    update={
                        "stale": True,
                        "warning": (
                            "The provider refresh failed. The last successful quote snapshot is "
                            "still shown and clearly marked stale."
                        ),
                    }
                )
            self._cache[cache_key] = _QuoteCacheEntry(
                snapshot=snapshot,
                expires_at=monotonic() + self.settings.sharia_live_quote_cache_seconds,
            )
            return snapshot

    async def _load_snapshot(
        self,
        *,
        exchange: str,
        quote_asset: str,
        methodology_id: UUID | None,
    ) -> LiveSpotMarketResponse:
        symbols = await self.provider.list_symbols(exchange, [quote_asset])
        normalized_symbols = sorted({canonical_symbol(symbol) for symbol in symbols})
        metadata_loader = getattr(self.provider, "fetch_universe_metadata", None)
        if not callable(metadata_loader):
            raise RuntimeError("The configured market provider cannot load live ticker metadata.")
        metadata = await metadata_loader(exchange, normalized_symbols)
        captured_at = datetime.now(UTC)
        items: list[LiveSpotMarketQuote] = []
        for symbol in normalized_symbols:
            values = metadata.get(symbol, {})
            asset = canonical_asset(symbol)
            items.append(
                LiveSpotMarketQuote(
                    symbol=symbol,
                    canonical_asset=asset,
                    asset_name=str(values.get("asset_name") or asset),
                    exchange=exchange,
                    quote_asset=quote_asset,
                    bid=values.get("bid"),
                    ask=values.get("ask"),
                    last=values.get("last"),
                    bid_size=values.get("bid_size"),
                    ask_size=values.get("ask_size"),
                    spread_bps=values.get("spread_bps"),
                    percentage_24h=values.get("percentage_24h"),
                    high_24h=values.get("high_24h"),
                    low_24h=values.get("low_24h"),
                    base_volume_24h=values.get("base_volume_24h"),
                    quote_volume_24h=values.get("quote_volume_24h"),
                    logo_module_url=self._logo_module_url(asset),
                    data_available=bool(values.get("data_quality_ok")),
                    updated_at=captured_at,
                )
            )
        items.sort(key=lambda item: (-(item.quote_volume_24h or 0), item.symbol))
        return LiveSpotMarketResponse(
            methodology=TestMethodologySummary(
                id=methodology_id,
                notice=TEST_METHODOLOGY_NOTICE,
            ),
            items=items,
            total=len(items),
            exchange=exchange,
            quote_asset=quote_asset,
            provider=f"{exchange} via CCXT",
            captured_at=captured_at,
        )

    @staticmethod
    def _logo_module_url(asset: str) -> str | None:
        normalized = asset.strip().upper()
        if not _SAFE_ASSET.fullmatch(normalized):
            return None
        return f"{_LOGO_CDN_BASE}/{normalized}.svg.js"
