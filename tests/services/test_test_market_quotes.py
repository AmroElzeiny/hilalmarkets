from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.test_market_quotes import (
    TEST_METHODOLOGY_NOTICE,
)
from ai_market_monitor.services.test_market_quotes import (
    TestMarketQuoteService as LiveTestMarketQuoteService,
)


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
        sharia_test_market_enabled=True,
        sharia_live_quote_cache_seconds=0.5,
    )


async def test_test_market_marks_every_active_pair_and_preserves_quote_evidence():
    LiveTestMarketQuoteService.clear_cache()
    provider = QuoteProvider()
    result = await LiveTestMarketQuoteService(provider, _settings()).snapshot(
        exchange="binance",
        quote_asset="usdt",
    )

    assert result.methodology.name == "Test"
    assert result.methodology.development_only is True
    assert result.methodology.notice == TEST_METHODOLOGY_NOTICE
    assert result.total == 2
    assert [item.symbol for item in result.items] == ["BTC/USDT", "ETH/USDT"]
    assert all(item.status_label == "Halal (test)" for item in result.items)
    assert result.items[0].bid == 100.0
    assert result.items[0].ask == 100.1
    assert result.items[0].bid_size == 4.0
    assert result.items[0].quote_volume_24h == 2_000_000.0
    assert result.items[0].logo_module_url is not None
    assert "@web3icons/core@4.0.53" in result.items[0].logo_module_url


async def test_test_market_cache_coalesces_requests_and_returns_stale_last_good_snapshot():
    LiveTestMarketQuoteService.clear_cache()
    provider = QuoteProvider()
    service = LiveTestMarketQuoteService(provider, _settings())

    first = await service.snapshot(exchange="bybit", quote_asset="USDT")
    second = await service.snapshot(exchange="bybit", quote_asset="USDT")

    assert second == first
    assert provider.symbol_calls == 1
    assert provider.metadata_calls == 1

    for entry in LiveTestMarketQuoteService._cache.values():
        entry.expires_at = 0
    provider.fail = True
    stale = await service.snapshot(exchange="bybit", quote_asset="USDT")

    assert stale.stale is True
    assert stale.warning is not None
    assert stale.items == first.items
    assert provider.symbol_calls == 2


def test_logo_catalog_path_rejects_unsafe_asset_names():
    assert LiveTestMarketQuoteService._logo_module_url("BTC") is not None
    assert LiveTestMarketQuoteService._logo_module_url("BTC/../../secret") is None
