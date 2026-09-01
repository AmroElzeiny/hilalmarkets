from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from uuid import UUID

from ai_market_monitor.core.asset_logos import asset_logo_module_url
from ai_market_monitor.core.config import Settings
from ai_market_monitor.schemas.sharia import (
    AssetAssessmentSummary,
    LiveMarketMethodologySummary,
    LiveSpotMarketQuote,
    LiveSpotMarketResponse,
    MethodologySummary,
)
from ai_market_monitor.services.interfaces import MarketDataProvider
from ai_market_monitor.services.sharia_screening import canonical_asset, canonical_symbol


@dataclass(slots=True)
class _QuoteCacheEntry:
    snapshot: LiveSpotMarketResponse
    expires_at: float


class LiveMarketQuoteService:
    """Provider-backed quotes attached to reviewed screening assessments."""

    _cache: dict[tuple[int, str, str, UUID | None], _QuoteCacheEntry] = {}
    _locks: dict[tuple[int, str, str, UUID | None], asyncio.Lock] = {}

    def __init__(self, provider: MarketDataProvider, settings: Settings):
        self.provider = provider
        self.settings = settings

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache.clear()
        cls._locks.clear()

    def refresh_after_ms(self) -> int:
        """How long a page should wait before asking for prices again.

        Derived from the snapshot cache, never written down a second time: asking faster
        than the cache is refilled can only return the same answer, at the cost of a full
        round trip through the API for every asking page.
        """
        return max(1000, int(self.settings.sharia_live_quote_cache_seconds * 1000))

    async def snapshot(
        self,
        *,
        exchange: str,
        quote_asset: str,
        methodology_id: UUID | None = None,
    ) -> LiveSpotMarketResponse:
        exchange_key = exchange.strip().lower()
        quote_key = quote_asset.strip().upper()
        cache_key = (id(self.provider), exchange_key, quote_key, methodology_id)
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

    async def screened_snapshot(
        self,
        *,
        exchange: str,
        quote_asset: str,
        methodology: MethodologySummary,
        assessments: list[AssetAssessmentSummary],
        warning: str | None = None,
        market_numbers: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> LiveSpotMarketResponse:
        """Attach live quotes only to evidence-backed assets for one methodology.

        ``market_numbers`` is what the exchange ticker cannot answer — size, rank, and
        movement over weeks — already read from the database by the caller. Passed in
        rather than fetched here because this snapshot is cached across every reader in
        the process and has no database session of its own: reaching for one from inside
        a shared cache is how one page's request ends up serving another page's data.
        """
        snapshot = await self.snapshot(
            exchange=exchange,
            quote_asset=quote_asset,
            methodology_id=methodology.id,
        )
        by_asset = {item.canonical_asset: item for item in assessments}
        numbers = market_numbers or {}
        items = []
        for quote in snapshot.items:
            assessment = by_asset.get(quote.canonical_asset)
            if assessment is None:
                continue
            items.append(
                quote.model_copy(
                    update={
                        "asset_name": assessment.asset_name or quote.asset_name,
                        "methodology_id": assessment.methodology_id,
                        "methodology_name": assessment.methodology_name,
                        "methodology_version": assessment.methodology_version,
                        "status": assessment.status.value,
                        "status_label": assessment.status_label,
                        "reviewed_at": assessment.reviewed_at,
                        "logo_url": assessment.logo_url,
                        "passport_url": (
                            f"/dashboard/market/{assessment.canonical_asset}"
                            f"?methodology_id={assessment.methodology_id}"
                        ),
                        # Only the fields actually stored. A coin the provider has never
                        # heard of contributes nothing here and keeps the schema's
                        # `None`, which the page draws as "not known" rather than zero.
                        **dict(numbers.get(quote.canonical_asset, {})),
                    }
                )
            )
        items.sort(
            key=lambda item: (
                not item.data_available,
                -(item.quote_volume_24h or 0),
                item.symbol,
            )
        )
        return snapshot.model_copy(
            update={
                "methodology": LiveMarketMethodologySummary(
                    id=methodology.id,
                    code=methodology.code,
                    name=methodology.name,
                    version=methodology.version,
                    notice=(
                        "Statuses come from stored, reviewed methodology evidence. Live prices "
                        "do not change the published screening decision."
                    ),
                ),
                "items": items,
                "total": len(items),
                "warning": warning or snapshot.warning,
            }
        )

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
                    methodology_id=methodology_id,
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
                    logo_module_url=asset_logo_module_url(asset),
                    data_available=bool(values.get("data_quality_ok")),
                    updated_at=captured_at,
                )
            )
        items.sort(key=lambda item: (-(item.quote_volume_24h or 0), item.symbol))
        # How soon the page should come back, decided by how soon there can be anything
        # new: this snapshot is reused until the cache above expires, so a page asking
        # sooner is answered with bytes it already has. The two numbers used to be
        # unrelated — the page was told "one second" while the data was rebuilt every
        # 0.75, and the browser's own floor made it ask every two seconds for ever. One
        # open Market tab was therefore thirty provider round-trips a minute.
        return LiveSpotMarketResponse(
            refresh_after_ms=self.refresh_after_ms(),
            methodology=LiveMarketMethodologySummary(
                id=methodology_id,
                code="PROVIDER_QUOTE_SNAPSHOT",
                name="Provider quote snapshot",
                version="runtime",
                notice="Internal unfiltered quote snapshot; no screening status is implied.",
            ),
            items=items,
            total=len(items),
            exchange=exchange,
            quote_asset=quote_asset,
            provider=f"{exchange} via CCXT",
            captured_at=captured_at,
        )

