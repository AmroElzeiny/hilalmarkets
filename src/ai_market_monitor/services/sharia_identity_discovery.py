from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import ExternalAssessment
from ai_market_monitor.services.sharia_identity import (
    CanonicalAssetCandidate,
    ExchangeMarketIdentity,
)

_DISAMBIGUATED_COINGECKO_IDS = {
    ("DASH", "dash"): "dash",
    ("CORE", "core"): "coredaoorg",
    ("BEAM", "beam"): "beam-2",
}


@dataclass(frozen=True, slots=True)
class ReviewedSourceIdentity:
    source_family: str
    source_row_id: str
    source_name: str
    source_symbol: str
    coingecko_id: str | None = None
    static_identity: str | None = None
    official_website: str | None = None
    official_documentation: str | None = None

    @property
    def reference(self) -> str:
        return f"{self.source_family}:{self.source_row_id}"


def _binding(
    source_family: str,
    source_row_id: str,
    source_name: str,
    source_symbol: str,
    coingecko_id: str | None = None,
    *,
    static_identity: str | None = None,
    official_website: str | None = None,
    official_documentation: str | None = None,
) -> ReviewedSourceIdentity:
    return ReviewedSourceIdentity(
        source_family=source_family,
        source_row_id=source_row_id,
        source_name=source_name,
        source_symbol=source_symbol,
        coingecko_id=coingecko_id,
        static_identity=static_identity,
        official_website=official_website,
        official_documentation=official_documentation,
    )


_FASSET = "fasset_shariah_reports"
_SRB = "shariah_review_bureau"
_REVIEWED_SOURCE_IDENTITIES = {
    (binding.source_family, binding.source_row_id): binding
    for binding in (
        _binding(
            _FASSET,
            "FASSET-006-dogecoin",
            "Dogecoin",
            "DOGE",
            "dogecoin",
            official_website="https://dogecoin.com/",
            official_documentation="https://github.com/dogecoin/dogecoin",
        ),
        _binding(_FASSET, "FASSET-014-polygon", "Polygon", "MATIC", "matic-network"),
        _binding(
            _FASSET,
            "FASSET-017-ethereum-classic",
            "Ethereum Classic",
            "ETC",
            "ethereum-classic",
            official_website="https://ethereumclassic.org/",
            official_documentation="https://ethereumclassic.org/development/",
        ),
        _binding(_FASSET, "FASSET-021-tether", "Tether", "USDT", "tether"),
        _binding(_FASSET, "FASSET-022-iota", "IOTA", "MIOTA", "iota"),
        _binding(_FASSET, "FASSET-024-neo", "Neo", "NEO", "neo"),
        _binding(_FASSET, "FASSET-025-tezos", "Tezos", "XTZ", "tezos"),
        _binding(_FASSET, "FASSET-028-usd-coin", "USD Coin", "USDC", "usd-coin"),
        _binding(_FASSET, "FASSET-029-binance-coin", "Binance Coin", "BNB", "binancecoin"),
        _binding(_FASSET, "FASSET-030-toncoin", "Toncoin", "TON", static_identity="toncoin"),
        _binding(
            _FASSET,
            "FASSET-031-artificial-superintelligence-alliance",
            "Artificial Superintelligence Alliance",
            "ASI",
            "fetch-ai",
        ),
        _binding(
            _FASSET,
            "FASSET-032-render",
            "Render",
            "RENDER",
            "render-token",
            official_website="https://rendernetwork.com/",
            official_documentation="https://docs.rendernetwork.com/",
        ),
        _binding(
            _FASSET,
            "FASSET-035-stacks",
            "Stacks",
            "STX",
            "blockstack",
            official_website="https://www.stacks.co/",
            official_documentation="https://docs.stacks.co/",
        ),
        _binding(
            _FASSET,
            "FASSET-039-arkham",
            "Arkham",
            "ARKM",
            "arkham",
            official_website="https://arkm.com/",
            official_documentation="https://arkm.com/api/docs",
        ),
        _binding(_FASSET, "FASSET-045-polygon", "Polygon", "POL", "polygon-ecosystem-token"),
        _binding(
            _FASSET,
            "FASSET-051-render",
            "Render",
            "RENDER",
            "render-token",
            official_website="https://rendernetwork.com/",
            official_documentation="https://docs.rendernetwork.com/",
        ),
        _binding(_FASSET, "FASSET-052-gatetoken", "GateToken", "GT", "gatechain-token"),
        _binding(_FASSET, "FASSET-054-unus-sed-leo", "UNUS SED LEO", "LEO", "leo-token"),
        _binding(
            _FASSET, "FASSET-055-okx-wrapped-btc", "OKX Wrapped BTC", "OKXBTC", "okx-wrapped-btc"
        ),
        _binding(_FASSET, "FASSET-057-staked-ether", "Staked Ether", "STETH", "staked-ether"),
        _binding(_FASSET, "FASSET-059-frax", "Frax", "FRAX", "frax"),
        _binding(_FASSET, "FASSET-060-frax-share", "Frax Share", "FXS", "frax-share"),
        _binding(
            _FASSET,
            "FASSET-077-fantom",
            "Fantom",
            "FTM",
            "fantom",
            official_website="https://fantom.foundation/",
            official_documentation="https://docs.fantom.foundation/",
        ),
        _binding(_FASSET, "FASSET-100-toncoin", "Toncoin", "TON", static_identity="toncoin"),
        _binding(_FASSET, "FASSET-103-usdp", "USDP", "USDP", "paxos-standard"),
        _binding(_FASSET, "FASSET-105-susd", "sUSD", "SUSD", static_identity="susd"),
        _binding(
            _FASSET, "FASSET-106-binance-usd-legacy", "Binance USD (legacy)", "BUSD", "binance-usd"
        ),
        _binding(_FASSET, "FASSET-109-klaytn", "Klaytn", "KLAY", static_identity="klaytn"),
        _binding(_FASSET, "FASSET-113-fetch-ai", "Fetch.ai", "FET", "fetch-ai"),
        _binding(
            _FASSET,
            "FASSET-116-world-mobile-token",
            "World Mobile Token",
            "WMT",
            "world-mobile-token",
        ),
        _binding(_FASSET, "FASSET-119-eigenlayer", "EigenLayer", "EIGEN", "eigenlayer"),
        _binding(_FASSET, "FASSET-126-aerodrome", "Aerodrome", "AERO", "aerodrome-finance"),
        _binding(
            _FASSET, "FASSET-127-base", "Base", "BASE_NETWORK", static_identity="base_network"
        ),
        _binding(_FASSET, "FASSET-134-ankr", "Ankr", "ANKR", "ankr"),
        _binding(_FASSET, "FASSET-135-lpt", "LPT", "LPT", "livepeer"),
        _binding(
            _FASSET,
            "FASSET-136-basic-attention-token",
            "Basic Attention Token",
            "BAT",
            "basic-attention-token",
        ),
        _binding(_FASSET, "FASSET-137-0x", "0x", "ZRX", "0x"),
        _binding(
            _FASSET,
            "FASSET-142-nervos-network",
            "Nervos Network",
            "CKB",
            "nervos-network",
            official_website="https://www.nervos.org/",
            official_documentation="https://docs.nervos.org/",
        ),
        _binding(_FASSET, "FASSET-145-oasis-network", "Oasis Network", "ROSE", "oasis-network"),
        _binding(_FASSET, "FASSET-146-sushiswap", "SushiSwap", "SUSHI", "sushi"),
        _binding(_FASSET, "FASSET-149-knc", "KNC", "KNC", "kyber-network-crystal"),
        _binding(_FASSET, "FASSET-151-luna-classic", "Luna Classic", "LUNC", "terra-luna"),
        _binding(_FASSET, "FASSET-155-bancor", "Bancor", "BNT", "bancor"),
        _binding(_FASSET, "FASSET-159-terrausd-classic", "TerraUSD Classic", "USTC", "terrausd"),
        _binding(_FASSET, "FASSET-160-sats", "Sats", "SATS", "sats-ordinals"),
        _binding(
            _FASSET,
            "FASSET-161-ordi",
            "ORDI",
            "ORDI",
            "ordinals",
            official_website="https://ordinals.com/",
            official_documentation="https://docs.ordinals.com/",
        ),
        _binding(_FASSET, "FASSET-169-kucoin-token", "KuCoin Token", "KCS", "kucoin-shares"),
        _binding(
            _FASSET,
            "FASSET-170-htx",
            "HTX",
            "HTX",
            "htx-dao",
            official_website="https://www.htxdao.com/en-us",
            official_documentation="https://www.htxdao.com/en-us",
        ),
        _binding(_FASSET, "FASSET-175-yooldo", "Yooldo", "ESPORTS", "yooldo-games"),
        _binding(_FASSET, "FASSET-181-gomining", "GoMining", "GOMINING", "gmt-token"),
        _binding(_FASSET, "FASSET-182-ailey", "Ailey", "ALE", "project-ailey"),
        _binding(
            _FASSET,
            "FASSET-186-impossible-cloud-network",
            "Impossible Cloud Network",
            "ICNT",
            "impossible-cloud-network-token",
        ),
        _binding(_SRB, "SRB-04-USDC", "USD Coin", "USDC", "usd-coin"),
        _binding(_SRB, "SRB-08-TON", "Toncoin", "TON", static_identity="toncoin"),
        _binding(
            _SRB,
            "SRB-20-ETC",
            "Ethereum Classic",
            "ETC",
            "ethereum-classic",
            official_website="https://ethereumclassic.org/",
            official_documentation="https://ethereumclassic.org/development/",
        ),
        _binding(_SRB, "SRB-21-BUSD", "Binance USD", "BUSD", "binance-usd"),
        _binding(
            _SRB,
            "SRB-24-DOGE",
            "Dogecoin",
            "DOGE",
            "dogecoin",
            official_website="https://dogecoin.com/",
            official_documentation="https://github.com/dogecoin/dogecoin",
        ),
    )
}

_STATIC_IDENTITIES = {
    "base_network": CanonicalAssetCandidate(
        name="Base",
        symbol="BASE_NETWORK",
        asset_type="network",
        native_chain="Base",
        official_website="https://www.base.org/",
        official_documentation="https://docs.base.org/",
    ),
    "klaytn": CanonicalAssetCandidate(
        name="Klaytn",
        symbol="KLAY",
        asset_type="native_coin",
        native_chain="Klaytn",
        official_website="https://www.kaia.io/",
        official_documentation="https://docs.kaia.io/",
    ),
    "susd": CanonicalAssetCandidate(
        name="sUSD",
        symbol="SUSD",
        asset_type="token",
        native_chain="Ethereum",
        official_website="https://synthetix.io/",
        official_documentation="https://docs.synthetix.io/",
        contract_addresses={"ethereum": "0x57ab1ec28d129707052df4df418d58a2d46d5f51"},
    ),
    "toncoin": CanonicalAssetCandidate(
        name="Toncoin",
        symbol="TON",
        asset_type="native_coin",
        native_chain="The Open Network",
        official_website="https://ton.org/",
        official_documentation="https://docs.ton.org/",
    ),
}


class IdentityDiscoveryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CoinGeckoCatalogRow:
    coin_id: str
    name: str
    symbol: str
    platforms: dict[str, str]


class CoinGeckoIdentityDiscovery:
    """Resolve imported rows without ever treating a ticker as a unique identity."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self._catalog: dict[tuple[str, str], list[CoinGeckoCatalogRow]] | None = None
        self._details: dict[str, dict[str, Any]] = {}
        self._platform_native_coin_ids: dict[str, str] | None = None
        self._request_lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def candidate_for(
        self,
        external: ExternalAssessment,
        *,
        exchange_symbols: dict[str, set[str]],
    ) -> CanonicalAssetCandidate:
        binding = _REVIEWED_SOURCE_IDENTITIES.get(
            (str(external.source_family or ""), str(external.source_row_id or ""))
        )
        if binding is not None:
            self._validate_reviewed_binding(external, binding)
            if binding.static_identity is not None:
                candidate = _STATIC_IDENTITIES[binding.static_identity]
                return replace(
                    candidate,
                    exchange_markets=_exchange_markets(
                        candidate.symbol,
                        exchange_symbols,
                    ),
                    accepted_source_names=(binding.source_name,),
                    accepted_source_symbols=(binding.source_symbol,),
                    source_binding_ref=binding.reference,
                )
        matches = (await self._catalog_index()).get(
            (
                external.asset_symbol.strip().upper(),
                _identity_text(external.asset_name),
            ),
            [],
        )
        coin_id = binding.coingecko_id if binding is not None else None
        coin_id = coin_id or _DISAMBIGUATED_COINGECKO_IDS.get(
            (
                external.asset_symbol.strip().upper(),
                _identity_text(external.asset_name),
            )
        )
        if coin_id is None:
            if not matches:
                raise IdentityDiscoveryError(
                    "canonical_identity_not_found",
                    "No provider identity matched both the imported asset name and symbol.",
                )
            if len(matches) != 1:
                raise IdentityDiscoveryError(
                    "canonical_identity_ambiguous",
                    "More than one provider identity matched the imported name and symbol.",
                )
            coin_id = matches[0].coin_id

        detail = await self._coin_detail(coin_id)
        name = _required_text(detail, "name")
        symbol = _required_text(detail, "symbol").upper()
        if binding is None and (
            _identity_text(name) != _identity_text(external.asset_name)
            or symbol != external.asset_symbol.strip().upper()
        ):
            raise IdentityDiscoveryError(
                "canonical_identity_detail_mismatch",
                "The provider detail no longer matches the imported name and symbol.",
            )

        links = dict(detail.get("links") or {})
        homepage = (
            binding.official_website
            if binding is not None and binding.official_website
            else _first_https(links.get("homepage"))
        )
        documentation = (
            (
                binding.official_documentation
                if binding is not None and binding.official_documentation
                else None
            )
            or _first_https([links.get("whitepaper")])
            or _first_https((links.get("repos_url") or {}).get("github"))
            or homepage
        )
        if homepage is None or documentation is None:
            raise IdentityDiscoveryError(
                "official_source_missing",
                "The provider did not return a verifiable official project source.",
            )

        platforms = {
            str(chain).strip(): str(address).strip()
            for chain, address in dict(detail.get("platforms") or {}).items()
            if str(chain).strip() and str(address).strip()
        }
        platform_id = str(detail.get("asset_platform_id") or "").strip()
        native_coin_ids = await self._native_coin_ids() if platform_id else {}
        # Some native assets, including XLM, expose their own platform ID and
        # token representations. CoinGecko's platform-native relationship is
        # authoritative for distinguishing those assets from issued tokens.
        is_token = bool(platform_id and native_coin_ids.get(platform_id) != coin_id)
        if is_token and not platforms:
            raise IdentityDiscoveryError(
                "token_contract_missing",
                "A token identity requires at least one provider-listed contract address.",
            )
        native_chain = platform_id if is_token else name
        image = _first_https(
            [
                (detail.get("image") or {}).get("large"),
                (detail.get("image") or {}).get("small"),
            ]
        )
        provider_ids = {"coingecko": coin_id}
        if image is not None:
            provider_ids["logo_url"] = image

        markets = _exchange_markets(symbol, exchange_symbols)
        return CanonicalAssetCandidate(
            name=name,
            symbol=symbol,
            asset_type="token" if is_token else "native_coin",
            native_chain=native_chain,
            official_website=homepage,
            official_documentation=documentation,
            contract_addresses=platforms if is_token else {},
            provider_ids=provider_ids,
            exchange_markets=markets,
            accepted_source_names=(binding.source_name,) if binding else (),
            accepted_source_symbols=(binding.source_symbol,) if binding else (),
            source_binding_ref=binding.reference if binding else None,
        )

    @staticmethod
    def _validate_reviewed_binding(
        external: ExternalAssessment,
        binding: ReviewedSourceIdentity,
    ) -> None:
        if (
            _identity_text(external.asset_name) != _identity_text(binding.source_name)
            or external.asset_symbol.strip().upper() != binding.source_symbol.upper()
        ):
            raise IdentityDiscoveryError(
                "reviewed_identity_binding_mismatch",
                "The imported row no longer matches its reviewed identity binding.",
            )

    async def _catalog_index(
        self,
    ) -> dict[tuple[str, str], list[CoinGeckoCatalogRow]]:
        if self._catalog is not None:
            return self._catalog
        payload = await self._get_json(
            "/coins/list",
            params={"include_platform": "true"},
        )
        if not isinstance(payload, list):
            raise IdentityDiscoveryError(
                "identity_catalog_invalid",
                "The identity provider returned an invalid catalog.",
            )
        index: dict[tuple[str, str], list[CoinGeckoCatalogRow]] = {}
        for value in payload:
            row = dict(value or {})
            coin_id = str(row.get("id") or "").strip()
            name = str(row.get("name") or "").strip()
            symbol = str(row.get("symbol") or "").strip().upper()
            if not coin_id or not name or not symbol:
                continue
            item = CoinGeckoCatalogRow(
                coin_id=coin_id,
                name=name,
                symbol=symbol,
                platforms={
                    str(key): str(address)
                    for key, address in dict(row.get("platforms") or {}).items()
                    if address
                },
            )
            index.setdefault((symbol, _identity_text(name)), []).append(item)
        self._catalog = index
        return index

    async def _coin_detail(self, coin_id: str) -> dict[str, Any]:
        cached = self._details.get(coin_id)
        if cached is not None:
            return cached
        payload = await self._get_json(
            f"/coins/{coin_id}",
            params={
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "false",
                "developer_data": "false",
                "sparkline": "false",
            },
        )
        if not isinstance(payload, dict):
            raise IdentityDiscoveryError(
                "identity_detail_invalid",
                "The identity provider returned an invalid asset profile.",
            )
        self._details[coin_id] = payload
        return payload

    async def _native_coin_ids(self) -> dict[str, str]:
        if self._platform_native_coin_ids is not None:
            return self._platform_native_coin_ids
        payload = await self._get_json("/asset_platforms", params={})
        if not isinstance(payload, list):
            raise IdentityDiscoveryError(
                "identity_platform_catalog_invalid",
                "The identity provider returned an invalid platform catalog.",
            )
        self._platform_native_coin_ids = {
            str(row.get("id") or "").strip(): str(row.get("native_coin_id") or "").strip()
            for raw in payload
            if isinstance(raw, dict)
            for row in [raw]
            if str(row.get("id") or "").strip() and str(row.get("native_coin_id") or "").strip()
        }
        return self._platform_native_coin_ids

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, str],
    ) -> Any:
        headers = {"Accept": "application/json"}
        if self.settings.coingecko_api_key is not None:
            header = (
                "x-cg-pro-api-key"
                if self.settings.coingecko_plan.strip().casefold() == "pro"
                else "x-cg-demo-api-key"
            )
            headers[header] = self.settings.coingecko_api_key.get_secret_value()
        last_status = 0
        last_transport_error = False
        retry_delay_seconds = 0.0
        for attempt in range(4):
            if attempt:
                await asyncio.sleep(
                    retry_delay_seconds or min(8.0, 1.5 * (2**attempt))
                )
                retry_delay_seconds = 0.0
            try:
                async with self._request_lock:
                    await self._wait_for_provider_slot()
                    async with httpx.AsyncClient(
                        base_url=str(self.settings.coingecko_api_base).rstrip("/"),
                        timeout=30,
                        follow_redirects=True,
                        transport=self.transport,
                        headers=headers,
                    ) as client:
                        response = await client.get(
                            path.lstrip("/"),
                            params=params,
                        )
                    self._last_request_at = asyncio.get_running_loop().time()
            except httpx.HTTPError:
                last_transport_error = True
                continue
            last_status = response.status_code
            last_transport_error = False
            if response.status_code < 400:
                return response.json()
            if response.status_code == 429:
                retry_delay_seconds = _retry_after_seconds(response)
            if response.status_code not in {408, 429} and response.status_code < 500:
                break
        detail = (
            "after repeated secure connection failures"
            if last_transport_error
            else f"(HTTP {last_status})"
        )
        raise IdentityDiscoveryError(
            "identity_provider_unavailable",
            f"The identity provider could not complete the request {detail}.",
        )

    async def _wait_for_provider_slot(self) -> None:
        if self.transport is not None:
            return
        minimum_interval = 0.2 if self.settings.coingecko_api_key is not None else 2.1
        elapsed = asyncio.get_running_loop().time() - self._last_request_at
        if elapsed < minimum_interval:
            await asyncio.sleep(minimum_interval - elapsed)


def _identity_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _retry_after_seconds(response: httpx.Response) -> float:
    value = response.headers.get("Retry-After", "").strip()
    if value:
        try:
            return min(120.0, max(0.0, float(value)))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                now = response.request.extensions.get("now")
                if now is None:
                    now = datetime.now(UTC)
                return min(120.0, max(0.0, (retry_at - now).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                pass
    # Anonymous CoinGecko quota windows are longer than ordinary transport
    # backoff. Waiting here avoids repeatedly exhausting the same window.
    return 60.0


def _required_text(value: dict[str, Any], key: str) -> str:
    text = str(value.get(key) or "").strip()
    if not text:
        raise IdentityDiscoveryError(
            "identity_detail_incomplete",
            f"The provider profile is missing {key}.",
        )
    return text


def _first_https(values: Any) -> str | None:
    if isinstance(values, str):
        candidates = [values]
    elif isinstance(values, list):
        candidates = values
    else:
        candidates = []
    for value in candidates:
        text = str(value or "").strip()
        if text.startswith("https://") and "@" not in text.partition("://")[2].partition("/")[0]:
            return text
    return None


def _exchange_markets(
    symbol: str,
    exchange_symbols: dict[str, set[str]],
) -> tuple[ExchangeMarketIdentity, ...]:
    market_symbol = f"{symbol.upper()}/USDT"
    return tuple(
        ExchangeMarketIdentity(
            exchange=exchange,
            market_symbol=market_symbol,
            base_asset=symbol.upper(),
            quote_asset="USDT",
        )
        for exchange, symbols in sorted(exchange_symbols.items())
        if market_symbol in symbols
    )
