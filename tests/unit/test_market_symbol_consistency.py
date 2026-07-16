from types import SimpleNamespace
from uuid import uuid4

from ai_market_monitor.db.models import CanonicalAsset, ExchangeMarket
from ai_market_monitor.services.sharia_universe import ShariaUniverseResolver
from tests.services.test_sharia_screening import ScreeningProvider, screening_settings


async def test_exchange_ticker_never_overrides_canonical_asset_identity(test_context):
    async with test_context["session_factory"]() as session:
        reviewed_asset = CanonicalAsset(
            symbol="SOL",
            name="Solana",
            asset_type="native_coin",
            native_chain="Solana",
            contract_addresses={},
            official_website="https://solana.com/",
            official_documentation="https://solana.com/docs",
            provider_ids={"coingecko": "solana"},
            identity_hash="1" * 64,
            mapping_state="verified",
            mapping_evidence={"source": "official identity evidence"},
        )
        different_asset = CanonicalAsset(
            symbol="WSOL",
            name="Wrapped SOL on another identity",
            asset_type="token",
            native_chain="Example Chain",
            contract_addresses={"example": "contract-1"},
            official_website="https://example.com/",
            official_documentation="https://example.com/docs",
            provider_ids={"coingecko": "wrapped-solana"},
            identity_hash="2" * 64,
            mapping_state="verified",
            mapping_evidence={"source": "separate official identity evidence"},
        )
        session.add_all([reviewed_asset, different_asset])
        await session.flush()
        market = ExchangeMarket(
            canonical_asset_id=different_asset.id,
            exchange="binance",
            market_symbol="SOL/USDT",
            base_asset="SOL",
            quote_asset="USDT",
            market_type="spot",
            is_active=True,
            metadata_hash="3" * 64,
        )
        session.add(market)
        await session.flush()
        resolver = ShariaUniverseResolver(
            session,
            ScreeningProvider(),
            screening_settings(),
        )
        publications = {
            uuid4(): SimpleNamespace(canonical_asset_id=reviewed_asset.id)
        }

        assets, markets = await resolver._market_mappings(
            publications,
            exchange="binance",
            symbols=["SOL/USDT"],
        )

        assert assets[reviewed_asset.id].mapping_state == "verified"
        assert markets == {}

        market.canonical_asset_id = reviewed_asset.id
        await session.flush()
        _, verified_markets = await resolver._market_mappings(
            publications,
            exchange="binance",
            symbols=["SOL/USDT"],
        )

    assert verified_markets["SOL/USDT"].canonical_asset_id == reviewed_asset.id
    assert verified_markets["SOL/USDT"].market_type == "spot"
