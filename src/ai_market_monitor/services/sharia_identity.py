import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import (
    AuditEvent,
    CanonicalAsset,
    ExchangeMarket,
    ExternalAssessment,
    OfficialSource,
    ReviewCase,
)


class AssetIdentityError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ExchangeMarketIdentity:
    exchange: str
    market_symbol: str
    base_asset: str
    quote_asset: str


@dataclass(frozen=True, slots=True)
class CanonicalAssetCandidate:
    name: str
    symbol: str
    asset_type: str
    native_chain: str | None
    official_website: str
    official_documentation: str
    contract_addresses: dict[str, str] = field(default_factory=dict)
    provider_ids: dict[str, str] = field(default_factory=dict)
    exchange_markets: tuple[ExchangeMarketIdentity, ...] = ()


REVIEWED_ASSET_CANDIDATES: dict[str, CanonicalAssetCandidate] = {
    "BTC": CanonicalAssetCandidate(
        name="Bitcoin",
        symbol="BTC",
        asset_type="native_coin",
        native_chain="Bitcoin",
        official_website="https://bitcoin.org/",
        official_documentation="https://developer.bitcoin.org/",
        provider_ids={"coingecko": "bitcoin"},
        exchange_markets=(
            ExchangeMarketIdentity("binance", "BTC/USDT", "BTC", "USDT"),
        ),
    ),
    "ETH": CanonicalAssetCandidate(
        name="Ethereum",
        symbol="ETH",
        asset_type="native_coin",
        native_chain="Ethereum",
        official_website="https://ethereum.org/",
        official_documentation="https://ethereum.org/en/developers/docs/",
        provider_ids={"coingecko": "ethereum"},
        exchange_markets=(
            ExchangeMarketIdentity("binance", "ETH/USDT", "ETH", "USDT"),
        ),
    ),
    "SOL": CanonicalAssetCandidate(
        name="Solana",
        symbol="SOL",
        asset_type="native_coin",
        native_chain="Solana",
        official_website="https://solana.com/",
        official_documentation="https://solana.com/docs",
        provider_ids={"coingecko": "solana"},
        exchange_markets=(
            ExchangeMarketIdentity("binance", "SOL/USDT", "SOL", "USDT"),
        ),
    ),
    "XRP": CanonicalAssetCandidate(
        name="Ripple",
        symbol="XRP",
        asset_type="native_coin",
        native_chain="XRP Ledger",
        official_website="https://xrpl.org/",
        official_documentation="https://xrpl.org/docs/",
        provider_ids={"coingecko": "ripple"},
        exchange_markets=(
            ExchangeMarketIdentity("binance", "XRP/USDT", "XRP", "USDT"),
        ),
    ),
    "LTC": CanonicalAssetCandidate(
        name="Litecoin",
        symbol="LTC",
        asset_type="native_coin",
        native_chain="Litecoin",
        official_website="https://litecoin.org/",
        official_documentation="https://litecoin.info/",
        provider_ids={"coingecko": "litecoin"},
        exchange_markets=(
            ExchangeMarketIdentity("binance", "LTC/USDT", "LTC", "USDT"),
        ),
    ),
    "BCH": CanonicalAssetCandidate(
        name="Bitcoin Cash",
        symbol="BCH",
        asset_type="native_coin",
        native_chain="Bitcoin Cash",
        official_website="https://bitcoincash.org/",
        official_documentation="https://documentation.cash/",
        provider_ids={"coingecko": "bitcoin-cash"},
        exchange_markets=(
            ExchangeMarketIdentity("binance", "BCH/USDT", "BCH", "USDT"),
        ),
    ),
    "ADA": CanonicalAssetCandidate(
        name="Cardano",
        symbol="ADA",
        asset_type="native_coin",
        native_chain="Cardano",
        official_website="https://cardano.org/",
        official_documentation="https://docs.cardano.org/",
        provider_ids={"coingecko": "cardano"},
        exchange_markets=(
            ExchangeMarketIdentity("binance", "ADA/USDT", "ADA", "USDT"),
        ),
    ),
    "LINK": CanonicalAssetCandidate(
        name="Chainlink",
        symbol="LINK",
        asset_type="token",
        native_chain="Ethereum",
        official_website="https://chain.link/",
        official_documentation="https://docs.chain.link/",
        contract_addresses={
            "ethereum": "0x514910771af9ca656af840dff83e8264ecf986ca"
        },
        provider_ids={"coingecko": "chainlink"},
        exchange_markets=(
            ExchangeMarketIdentity("binance", "LINK/USDT", "LINK", "USDT"),
        ),
    ),
    "UNI": CanonicalAssetCandidate(
        name="Uniswap",
        symbol="UNI",
        asset_type="token",
        native_chain="Ethereum",
        official_website="https://uniswap.org/",
        official_documentation="https://docs.uniswap.org/",
        contract_addresses={
            "ethereum": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"
        },
        provider_ids={"coingecko": "uniswap"},
        exchange_markets=(
            ExchangeMarketIdentity("binance", "UNI/USDT", "UNI", "USDT"),
        ),
    ),
    "MATIC": CanonicalAssetCandidate(
        name="Polygon",
        symbol="MATIC",
        asset_type="token",
        native_chain="Ethereum and Polygon PoS",
        official_website="https://polygon.technology/",
        official_documentation="https://docs.polygon.technology/pos/concepts/tokens/matic",
        contract_addresses={
            "ethereum": "0x7d1afa7b718fb893db30a3abc0cfc608aacfebb0"
        },
        provider_ids={"coingecko": "matic-network"},
        exchange_markets=(
            ExchangeMarketIdentity("binance", "MATIC/USDT", "MATIC", "USDT"),
        ),
    ),
    "AVAX": CanonicalAssetCandidate(
        name="Avalanche",
        symbol="AVAX",
        asset_type="native_coin",
        native_chain="Avalanche",
        official_website="https://www.avax.network/",
        official_documentation="https://build.avax.network/docs/",
        provider_ids={"coingecko": "avalanche-2"},
        exchange_markets=(
            ExchangeMarketIdentity("binance", "AVAX/USDT", "AVAX", "USDT"),
        ),
    ),
    "DOT": CanonicalAssetCandidate(
        name="Polkadot",
        symbol="DOT",
        asset_type="native_coin",
        native_chain="Polkadot",
        official_website="https://polkadot.com/",
        official_documentation="https://wiki.polkadot.network/",
        provider_ids={"coingecko": "polkadot"},
        exchange_markets=(
            ExchangeMarketIdentity("binance", "DOT/USDT", "DOT", "USDT"),
        ),
    ),
    "ATOM": CanonicalAssetCandidate(
        name="Cosmos",
        symbol="ATOM",
        asset_type="native_coin",
        native_chain="Cosmos Hub",
        official_website="https://cosmos.network/",
        official_documentation="https://docs.cosmos.network/",
        provider_ids={"coingecko": "cosmos"},
        exchange_markets=(
            ExchangeMarketIdentity("binance", "ATOM/USDT", "ATOM", "USDT"),
        ),
    ),
    "WLD": CanonicalAssetCandidate(
        name="Worldcoin",
        symbol="WLD",
        asset_type="token",
        native_chain="World Chain",
        official_website="https://world.org/",
        official_documentation="https://docs.world.org/",
        contract_addresses={
            "world_chain": "0x2cfc85d8e48f8eab294be644d9e25c3030863003"
        },
        provider_ids={"coingecko": "worldcoin-wld"},
        exchange_markets=(
            ExchangeMarketIdentity("binance", "WLD/USDT", "WLD", "USDT"),
        ),
    ),
    "XLM": CanonicalAssetCandidate(
        name="Stellar",
        symbol="XLM",
        asset_type="native_coin",
        native_chain="Stellar",
        official_website="https://stellar.org/",
        official_documentation="https://developers.stellar.org/docs/",
        provider_ids={"coingecko": "stellar"},
        exchange_markets=(
            ExchangeMarketIdentity("binance", "XLM/USDT", "XLM", "USDT"),
        ),
    ),
}

PILOT_ASSET_CANDIDATES = {
    symbol: REVIEWED_ASSET_CANDIDATES[symbol] for symbol in ("BTC", "ETH", "SOL")
}


class CanonicalAssetMappingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def map_candidate(
        self,
        external: ExternalAssessment,
        candidate: CanonicalAssetCandidate,
        *,
        actor_user_id=None,
        verified_exchange_symbols: set[str] | None = None,
    ) -> CanonicalAsset:
        problems = self._validate(external, candidate, verified_exchange_symbols)
        if problems:
            external.mapping_state = "conflict"
            external.mapping_notes = problems
            await self._create_conflict_case(external, problems)
            raise AssetIdentityError(
                "source_identity_conflict",
                "Canonical identity is ambiguous; publication remains blocked.",
            )

        identity_payload = {
            "name": _identity_text(candidate.name),
            "symbol": candidate.symbol.upper(),
            "asset_type": candidate.asset_type,
            "native_chain": _identity_text(candidate.native_chain or ""),
            "contracts": {
                key.casefold(): value.casefold()
                for key, value in sorted(candidate.contract_addresses.items())
            },
            "official_website": _normalized_url(candidate.official_website),
        }
        identity_hash = _hash_json(identity_payload)
        asset = await self.session.scalar(
            select(CanonicalAsset).where(CanonicalAsset.identity_hash == identity_hash)
        )
        if asset is None:
            asset = CanonicalAsset(
                name=candidate.name,
                symbol=candidate.symbol.upper(),
                asset_type=candidate.asset_type,
                native_chain=candidate.native_chain,
                contract_addresses=candidate.contract_addresses,
                official_website=_normalized_url(candidate.official_website),
                official_documentation=_normalized_url(candidate.official_documentation),
                provider_ids=candidate.provider_ids,
                identity_hash=identity_hash,
                mapping_state="verified",
                mapping_evidence={
                    "matched_fields": [
                        "name",
                        "symbol",
                        "native_or_token_status",
                        "native_chain_or_contract",
                        "official_website",
                    ],
                    "source": "curated_official_metadata",
                },
            )
            self.session.add(asset)
            await self.session.flush()
        external.canonical_asset_id = asset.id
        external.mapping_state = "mapped"
        external.mapping_notes = [
            "Identity matched across name, symbol, native/token status, chain, and official site."
        ]

        for market in candidate.exchange_markets:
            existing_market = await self.session.scalar(
                select(ExchangeMarket).where(
                    ExchangeMarket.exchange == market.exchange,
                    ExchangeMarket.market_symbol == market.market_symbol,
                )
            )
            if existing_market is None:
                self.session.add(
                    ExchangeMarket(
                        canonical_asset_id=asset.id,
                        exchange=market.exchange,
                        market_symbol=market.market_symbol,
                        base_asset=market.base_asset,
                        quote_asset=market.quote_asset,
                        market_type="spot",
                        is_active=True,
                        metadata_hash=_hash_json(
                            {
                                "exchange": market.exchange,
                                "symbol": market.market_symbol,
                                "base": market.base_asset,
                                "quote": market.quote_asset,
                            }
                        ),
                    )
                )
        await self._register_source(
            asset.id,
            "official_website",
            f"{candidate.name} official website",
            candidate.official_website,
            10,
        )
        await self._register_source(
            asset.id,
            "official_documentation",
            f"{candidate.name} official documentation",
            candidate.official_documentation,
            20,
        )
        self.session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                actor_type="admin" if actor_user_id else "worker",
                action="sharia.canonical_asset_mapped",
                target_type="canonical_asset",
                target_id=str(asset.id),
                metadata_redacted={
                    "symbol": asset.symbol,
                    "external_assessment_id": str(external.id),
                    "identity_hash": identity_hash,
                },
                created_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        return asset

    async def map_pilot(
        self,
        external: ExternalAssessment,
        *,
        actor_user_id=None,
        verified_exchange_symbols: set[str] | None = None,
    ) -> CanonicalAsset:
        candidate = PILOT_ASSET_CANDIDATES.get(external.asset_symbol.upper())
        if candidate is None:
            raise AssetIdentityError(
                "pilot_metadata_unavailable",
                "This asset is not in the reviewed pilot identity registry.",
            )
        return await self.map_candidate(
            external,
            candidate,
            actor_user_id=actor_user_id,
            verified_exchange_symbols=verified_exchange_symbols,
        )

    async def map_registered(
        self,
        external: ExternalAssessment,
        *,
        actor_user_id=None,
        verified_exchange_symbols: set[str] | None = None,
    ) -> CanonicalAsset:
        candidate = REVIEWED_ASSET_CANDIDATES.get(external.asset_symbol.upper())
        if candidate is None:
            problems = [
                "No reviewed canonical metadata exists for this imported authority record."
            ]
            external.mapping_state = "conflict"
            external.mapping_notes = problems
            await self._create_conflict_case(external, problems)
            raise AssetIdentityError(
                "canonical_metadata_unavailable",
                "Reviewed canonical metadata is required before research or publication.",
            )
        return await self.map_candidate(
            external,
            candidate,
            actor_user_id=actor_user_id,
            verified_exchange_symbols=verified_exchange_symbols,
        )

    @staticmethod
    def _validate(
        external: ExternalAssessment,
        candidate: CanonicalAssetCandidate,
        verified_exchange_symbols: set[str] | None,
    ) -> list[str]:
        problems: list[str] = []
        if _identity_text(external.asset_name) != _identity_text(candidate.name):
            problems.append("Imported name does not match the canonical name.")
        if external.asset_symbol.upper() != candidate.symbol.upper():
            problems.append("Imported symbol does not match the canonical symbol.")
        if candidate.asset_type not in {"native_coin", "token"}:
            problems.append("Native coin versus token status is missing.")
        if candidate.asset_type == "native_coin" and not candidate.native_chain:
            problems.append("Native chain is missing.")
        if candidate.asset_type == "token" and not candidate.contract_addresses:
            problems.append("Token contract addresses are missing.")
        if not _valid_official_url(candidate.official_website):
            problems.append("A valid HTTPS official website is required.")
        if not _valid_official_url(candidate.official_documentation):
            problems.append("A valid HTTPS official documentation URL is required.")
        if verified_exchange_symbols is not None:
            for market in candidate.exchange_markets:
                if market.market_symbol.upper() not in verified_exchange_symbols:
                    problems.append(
                        f"{market.exchange} spot market {market.market_symbol} was not verified."
                    )
        return problems

    async def _create_conflict_case(
        self, external: ExternalAssessment, problems: list[str]
    ) -> None:
        key = f"identity-conflict:{external.id}"
        existing = await self.session.scalar(
            select(ReviewCase).where(ReviewCase.idempotency_key == key)
        )
        if existing is None:
            self.session.add(
                ReviewCase(
                    case_reference=f"ID-{str(external.id)[:8].upper()}",
                    case_type="source_identity_conflict",
                    state="needs_evidence",
                    publication_state="unpublished",
                    canonical_asset_id=external.canonical_asset_id,
                    external_assessment_id=external.id,
                    title=f"Resolve identity for {external.asset_name}",
                    priority="high",
                    risk_severity="high",
                    human_review_reason=" ".join(problems),
                    requested_evidence=problems,
                    idempotency_key=key,
                )
            )

    async def _register_source(
        self,
        asset_id,
        category: str,
        title: str,
        url: str,
        priority: int,
    ) -> None:
        normalized = _normalized_url(url)
        existing = await self.session.scalar(
            select(OfficialSource).where(
                OfficialSource.canonical_asset_id == asset_id,
                OfficialSource.normalized_url == normalized,
            )
        )
        if existing is None:
            self.session.add(
                OfficialSource(
                    canonical_asset_id=asset_id,
                    category=category,
                    title=title,
                    source_url=url,
                    normalized_url=normalized,
                    priority=priority,
                    verification_state="verified",
                    verified_at=datetime.now(UTC),
                    is_active=True,
                )
            )


def _identity_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _normalized_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), path, "", ""))


def _valid_official_url(value: str) -> bool:
    parsed = urlsplit(value.strip())
    return parsed.scheme == "https" and bool(parsed.netloc) and "@" not in parsed.netloc


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
