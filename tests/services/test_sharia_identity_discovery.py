import json

import httpx

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import ExternalAssessment
from ai_market_monitor.services.sharia_identity_discovery import (
    CoinGeckoIdentityDiscovery,
    IdentityDiscoveryError,
)


def _settings() -> Settings:
    return Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        database_url="sqlite+aiosqlite://",
        coingecko_enabled=True,
    )


def _response(value, *, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(value).encode(),
        headers={"Content-Type": "application/json"},
    )


async def test_identity_discovery_requires_name_and_symbol_and_maps_exact_exchanges():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/coins/list"):
            return _response(
                [
                    {
                        "id": "alpha-network",
                        "name": "Alpha Network",
                        "symbol": "alpha",
                        "platforms": {},
                    },
                    {
                        "id": "unrelated-alpha",
                        "name": "Unrelated",
                        "symbol": "alpha",
                        "platforms": {},
                    },
                ]
            )
        if request.url.path.endswith("/asset_platforms"):
            return _response(
                [
                    {
                        "id": "alpha-chain",
                        "name": "Alpha Chain",
                        "native_coin_id": "alpha-network",
                    }
                ]
            )
        assert request.url.path.endswith("/coins/alpha-network")
        return _response(
            {
                "id": "alpha-network",
                "name": "Alpha Network",
                "symbol": "alpha",
                "asset_platform_id": "alpha-chain",
                "platforms": {"ethereum": "0xwrapped-representation-is-not-canonical"},
                "links": {
                    "homepage": ["https://alpha.example/"],
                    "whitepaper": "https://alpha.example/whitepaper",
                    "repos_url": {"github": ["https://github.com/alpha"]},
                },
                "image": {"large": "https://assets.coingecko.com/alpha.png"},
            }
        )

    external = ExternalAssessment(
        asset_name="Alpha Network",
        asset_symbol="ALPHA",
    )
    candidate = await CoinGeckoIdentityDiscovery(
        _settings(),
        transport=httpx.MockTransport(handler),
    ).candidate_for(
        external,
        exchange_symbols={
            "binance": {"ALPHA/USDT"},
            "bybit": {"BTC/USDT"},
        },
    )

    assert candidate.name == "Alpha Network"
    assert candidate.symbol == "ALPHA"
    assert candidate.asset_type == "native_coin"
    assert candidate.contract_addresses == {}
    assert candidate.native_chain == "Alpha Network"
    assert candidate.official_website == "https://alpha.example/"
    assert candidate.provider_ids == {
        "coingecko": "alpha-network",
        "logo_url": "https://assets.coingecko.com/alpha.png",
    }
    assert [(market.exchange, market.market_symbol) for market in candidate.exchange_markets] == [
        ("binance", "ALPHA/USDT")
    ]


async def test_identity_discovery_rejects_ticker_only_and_ambiguous_matches():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/coins/list")
        return _response(
            [
                {"id": "one", "name": "Twin", "symbol": "twin", "platforms": {}},
                {"id": "two", "name": "Twin", "symbol": "twin", "platforms": {}},
                {"id": "other", "name": "Other", "symbol": "same", "platforms": {}},
            ]
        )

    service = CoinGeckoIdentityDiscovery(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    try:
        await service.candidate_for(
            ExternalAssessment(asset_name="Twin", asset_symbol="TWIN"),
            exchange_symbols={},
        )
    except IdentityDiscoveryError as exc:
        assert exc.code == "canonical_identity_ambiguous"
    else:
        raise AssertionError("Ambiguous provider identities must fail closed.")

    try:
        await service.candidate_for(
            ExternalAssessment(asset_name="Not Other", asset_symbol="SAME"),
            exchange_symbols={},
        )
    except IdentityDiscoveryError as exc:
        assert exc.code == "canonical_identity_not_found"
    else:
        raise AssertionError("Ticker-only identity matches must fail closed.")


async def test_identity_discovery_retries_temporary_transport_failure():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError(
                "temporary TLS connection failure",
                request=request,
            )
        return _response([])

    service = CoinGeckoIdentityDiscovery(
        _settings(),
        transport=httpx.MockTransport(handler),
    )

    assert await service._get_json("/asset_platforms", params={}) == []
    assert attempts == 2


async def test_identity_discovery_honors_rate_limit_retry_after():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                request=request,
            )
        return _response([])

    service = CoinGeckoIdentityDiscovery(
        _settings(),
        transport=httpx.MockTransport(handler),
    )

    assert await service._get_json("/asset_platforms", params={}) == []
    assert attempts == 2


async def test_reviewed_source_binding_accepts_current_provider_alias():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/coins/list"):
            return _response([])
        if request.url.path.endswith("/asset_platforms"):
            return _response([])
        assert request.url.path.endswith("/coins/world-mobile-token")
        return _response(
            {
                "id": "world-mobile-token",
                "name": "World Mobile Token",
                "symbol": "wmtx",
                "asset_platform_id": "cardano",
                "platforms": {"cardano": "1d7f33bd23d85e1a8c0c49c08f10d7f5927713c3"},
                "links": {
                    "homepage": ["https://worldmobile.io/"],
                    "whitepaper": "https://worldmobile.io/whitepaper",
                },
                "image": {"large": "https://assets.coingecko.com/wmtx.png"},
            }
        )

    external = ExternalAssessment(
        source_family="fasset_shariah_reports",
        source_row_id="FASSET-116-world-mobile-token",
        asset_name="World Mobile Token",
        asset_symbol="WMT",
    )
    candidate = await CoinGeckoIdentityDiscovery(
        _settings(),
        transport=httpx.MockTransport(handler),
    ).candidate_for(
        external,
        exchange_symbols={
            "binance": {"WMTX/USDT"},
            "bybit": {"WMT/USDT"},
        },
    )

    assert candidate.symbol == "WMTX"
    assert candidate.accepted_source_symbols == ("WMT",)
    assert candidate.source_binding_ref.endswith("FASSET-116-world-mobile-token")
    assert [(market.exchange, market.market_symbol) for market in candidate.exchange_markets] == [
        ("binance", "WMTX/USDT")
    ]


async def test_reviewed_source_binding_supplies_verified_official_sources():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/coins/list"):
            return _response([])
        if request.url.path.endswith("/asset_platforms"):
            return _response(
                [
                    {
                        "id": "ethereum",
                        "name": "Ethereum",
                        "native_coin_id": "ethereum",
                    }
                ]
            )
        assert request.url.path.endswith("/coins/arkham")
        return _response(
            {
                "id": "arkham",
                "name": "Arkham",
                "symbol": "arkm",
                "asset_platform_id": "ethereum",
                "platforms": {"ethereum": "0x6e2a43be0b1d33b726f0ca3b8de60b3482b8b050"},
                "links": {},
                "image": {"large": "https://assets.coingecko.com/arkm.png"},
            }
        )

    external = ExternalAssessment(
        source_family="fasset_shariah_reports",
        source_row_id="FASSET-039-arkham",
        asset_name="Arkham",
        asset_symbol="ARKM",
    )
    candidate = await CoinGeckoIdentityDiscovery(
        _settings(),
        transport=httpx.MockTransport(handler),
    ).candidate_for(
        external,
        exchange_symbols={"binance": {"ARKM/USDT"}},
    )

    assert candidate.official_website == "https://arkm.com/"
    assert candidate.official_documentation == "https://arkm.com/api/docs"
    assert candidate.provider_ids["logo_url"].endswith("/arkm.png")


async def test_reviewed_network_binding_never_invents_exchange_market():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Static reviewed identities must not call a provider.")

    external = ExternalAssessment(
        source_family="fasset_shariah_reports",
        source_row_id="FASSET-127-base",
        asset_name="Base",
        asset_symbol="BASE_NETWORK",
    )
    candidate = await CoinGeckoIdentityDiscovery(
        _settings(),
        transport=httpx.MockTransport(handler),
    ).candidate_for(
        external,
        exchange_symbols={
            "binance": {"ETH/USDT"},
            "bybit": {"BASE/USDT"},
        },
    )

    assert candidate.asset_type == "network"
    assert candidate.symbol == "BASE_NETWORK"
    assert candidate.exchange_markets == ()
