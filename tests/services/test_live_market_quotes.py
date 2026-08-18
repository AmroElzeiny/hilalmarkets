from datetime import UTC, datetime
from uuid import uuid4

from ai_market_monitor.core.asset_logos import asset_logo_module_url
from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models.enums import ShariaAssetStatus, ShariaMethodologyStatus
from ai_market_monitor.schemas.sharia import (
    AssetAssessmentSummary,
    MethodologySummary,
)
from ai_market_monitor.services.live_market_quotes import LiveMarketQuoteService


class QuoteProvider:
    def __init__(self) -> None:
        self.symbol_calls = 0
        self.metadata_calls = 0
        self.fail = False

    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        self.symbol_calls += 1
        if self.fail:
            raise RuntimeError("provider unavailable")
        return ["ETH/USDT", "BTC/USDT", "ETH/USDT"]

    async def fetch_universe_metadata(
        self,
        exchange: str,
        symbols: list[str],
        *,
        include_listing_dates: bool = False,
    ) -> dict[str, dict[str, object]]:
        del exchange, include_listing_dates
        self.metadata_calls += 1
        return {
            symbol: {
                "asset_name": symbol.partition("/")[0],
                "bid": 100.0 if symbol == "BTC/USDT" else 20.0,
                "ask": 100.1 if symbol == "BTC/USDT" else 20.1,
                "last": 100.05 if symbol == "BTC/USDT" else 20.05,
                "bid_size": 4.0,
                "ask_size": 5.0,
                "spread_bps": 9.995,
                "percentage_24h": 2.4,
                "high_24h": 110.0,
                "low_24h": 90.0,
                "base_volume_24h": 1_000.0,
                "quote_volume_24h": 2_000_000.0 if symbol == "BTC/USDT" else 1_000.0,
                "data_quality_ok": True,
            }
            for symbol in symbols
        }


def _settings() -> Settings:
    return Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        database_url="sqlite+aiosqlite://",
        sharia_live_quote_cache_seconds=0.5,
    )


async def test_provider_snapshot_preserves_quote_evidence_without_implying_screening():
    LiveMarketQuoteService.clear_cache()
    provider = QuoteProvider()
    result = await LiveMarketQuoteService(provider, _settings()).snapshot(
        exchange="binance",
        quote_asset="usdt",
    )

    assert result.methodology.name == "Provider quote snapshot"
    assert result.methodology.development_only is False
    assert result.total == 2
    assert [item.symbol for item in result.items] == ["BTC/USDT", "ETH/USDT"]
    assert all(item.status_label == "Unavailable" for item in result.items)
    assert result.items[0].bid == 100.0
    assert result.items[0].ask == 100.1
    assert result.items[0].bid_size == 4.0
    assert result.items[0].quote_volume_24h == 2_000_000.0
    assert result.items[0].logo_module_url is not None
    assert "@web3icons/core@4.0.53" in result.items[0].logo_module_url


async def test_live_market_cache_coalesces_requests_and_returns_stale_last_good_snapshot():
    LiveMarketQuoteService.clear_cache()
    provider = QuoteProvider()
    service = LiveMarketQuoteService(provider, _settings())

    first = await service.snapshot(exchange="bybit", quote_asset="USDT")
    second = await service.snapshot(exchange="bybit", quote_asset="USDT")

    assert second == first
    assert provider.symbol_calls == 1
    assert provider.metadata_calls == 1

    for entry in LiveMarketQuoteService._cache.values():
        entry.expires_at = 0
    provider.fail = True
    stale = await service.snapshot(exchange="bybit", quote_asset="USDT")

    assert stale.stale is True
    assert stale.warning is not None
    assert stale.items == first.items
    assert provider.symbol_calls == 2


def test_logo_catalog_path_rejects_unsafe_asset_names():
    """This service used to carry its own copy of the check, and its own idea of which
    pictures a coin has — the catalogue only. Both now come from `core.asset_logos`, so
    the rejection is tested where it lives and every reader gets the same answer."""

    assert asset_logo_module_url("BTC") is not None
    assert asset_logo_module_url("BTC/../../secret") is None


async def test_screened_snapshot_omits_assets_absent_from_selected_exchange():
    LiveMarketQuoteService.clear_cache()
    provider = QuoteProvider()
    service = LiveMarketQuoteService(provider, _settings())
    now = datetime.now(UTC)
    aggregate_id = uuid4()
    source_id = uuid4()
    assessment = AssetAssessmentSummary(
        id=uuid4(),
        canonical_asset="SOL",
        asset_name="Solana",
        methodology_id=source_id,
        methodology_name="Fasset",
        methodology_version="2026.07",
        status=ShariaAssetStatus.ELIGIBLE,
        status_label="Eligible",
        summary="Reviewed source assessment.",
        qualifications=[],
        reviewed_by="Reviewer",
        reviewed_at=now,
        valid_from=now,
        valid_until=None,
    )
    result = await service.screened_snapshot(
        exchange="binance",
        quote_asset="USDT",
        methodology=MethodologySummary(
            id=aggregate_id,
            code="ALL_APPROVED_METHODOLOGIES",
            name="All",
            version="2026.07",
            description="Aggregate view.",
            status=ShariaMethodologyStatus.ACTIVE,
            governing_body=None,
            reviewer_group=None,
            published_at=now,
            effective_from=now,
            effective_to=None,
        ),
        assessments=[assessment],
    )

    assert result.methodology.id == aggregate_id
    assert result.items == []
    assert result.total == 0
