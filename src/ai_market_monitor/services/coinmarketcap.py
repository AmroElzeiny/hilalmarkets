"""One owner for everything this product asks CoinMarketCap.

CoinMarketCap is a **provider record**, exactly like CoinGecko: it answers what a coin
is, where its project publishes, and where it ranks. It is **never** a Shariah
authority, and no status, ruling or eligibility is ever read from it — not from a tag,
not from a category, not from a rank.

Why it was added when CoinGecko already exists: CoinGecko's record frequently has no
whitepaper. CoinMarketCap's ``urls.technical_doc`` is the whitepaper, published per
coin, for essentially every listed asset. Before this, the product went looking for
whitepapers and official pages on the open web — searching, guessing conventional
paths like ``/blog``, and finally paying a model to recall an address. A provider that
already holds the answer replaces the weakest three of those layers for most coins.

**What this module does and does not decide.** It fetches and it validates the shape of
what came back. It does *not* decide whether an address is really the project's own or
which category a link belongs to — ``sharia_source_catalog`` is the single owner of
that, and it decides for a search engine's answers, a model's answers and this
provider's answers with the same rules. Splitting it the other way round is how the
duplicate-vocabulary failure starts.

**Plan awareness is a feature, not a nicety.** The account's plan carries only some
endpoints. Asking for one it does not carry returns error 1006, and while that costs no
credit it does cost a request against the per-minute limit and a round trip. Every
endpoint here declares the plans that carry it, so a call the account cannot make is
refused locally, with a message that says what to upgrade.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

import httpx

from ai_market_monitor.core.config import Settings

#: Sent on every request. CoinMarketCap authenticates by header, never by query string,
#: so the key cannot end up in a log line or a stored URL.
API_KEY_HEADER: Final = "X-CMC_PRO_API_KEY"

#: Returned when the account's plan does not carry the endpoint. Distinguished from a
#: bad key (1001/1002) because the operator action is completely different: upgrade the
#: plan, versus fix the secret.
PLAN_NOT_ENTITLED: Final = 1006


class Plan(StrEnum):
    """CoinMarketCap's plan tiers, weakest first."""

    BASIC = "basic"
    HOBBYIST = "hobbyist"
    STARTUP = "startup"
    STANDARD = "standard"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


#: Plans in order, so "does this plan carry that endpoint" is a comparison rather than
#: a set membership test that has to list every tier on every endpoint.
PLAN_ORDER: Final[tuple[Plan, ...]] = (
    Plan.BASIC,
    Plan.HOBBYIST,
    Plan.STARTUP,
    Plan.STANDARD,
    Plan.PROFESSIONAL,
    Plan.ENTERPRISE,
)


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One CoinMarketCap call, and the smallest plan that carries it.

    ``verified_on_basic`` records what was actually observed against a live Basic key
    on 30 August 2026, rather than what the documentation implies. The two disagree in
    both directions, and only the observation can be trusted.
    """

    path: str
    minimum_plan: Plan
    #: Credits one call costs. Free calls are free at any size.
    credits: int = 1
    verified_on_basic: bool = False


#: Every endpoint the product may call. Adding one here is the only way to reach a new
#: CoinMarketCap capability, so the plan gate can never be bypassed by a new caller.
ENDPOINTS: Final[Mapping[str, Endpoint]] = {
    "key_info": Endpoint("/v1/key/info", Plan.BASIC, credits=0, verified_on_basic=True),
    "id_map": Endpoint(
        "/v1/cryptocurrency/map", Plan.BASIC, credits=0, verified_on_basic=True
    ),
    #: The reason this integration exists: official website, whitepaper, source code,
    #: explorers, announcements, forums and the project logo, per coin.
    "metadata": Endpoint(
        "/v2/cryptocurrency/info", Plan.BASIC, verified_on_basic=True
    ),
    "listings": Endpoint(
        "/v1/cryptocurrency/listings/latest", Plan.BASIC, verified_on_basic=True
    ),
    "quotes": Endpoint(
        "/v2/cryptocurrency/quotes/latest", Plan.BASIC, verified_on_basic=True
    ),
    "global_metrics": Endpoint(
        "/v1/global-metrics/quotes/latest", Plan.BASIC, verified_on_basic=True
    ),
    "categories": Endpoint(
        "/v1/cryptocurrency/categories", Plan.BASIC, verified_on_basic=True
    ),
    "trending": Endpoint(
        "/v1/cryptocurrency/trending/latest", Plan.BASIC, verified_on_basic=True
    ),
    "fear_and_greed": Endpoint(
        "/v3/fear-and-greed/latest", Plan.BASIC, verified_on_basic=True
    ),
    "airdrops": Endpoint(
        "/v1/cryptocurrency/airdrops", Plan.BASIC, verified_on_basic=True
    ),
    "price_conversion": Endpoint(
        "/v1/tools/price-conversion", Plan.BASIC, verified_on_basic=True
    ),
    "exchange_map": Endpoint("/v1/exchange/map", Plan.BASIC, verified_on_basic=True),
    # --- Observed as NOT carried by a Basic key. Declared so a caller is refused
    # --- locally with a useful message instead of spending a request to be told no.
    "gainers_losers": Endpoint(
        "/v1/cryptocurrency/trending/gainers-losers", Plan.STANDARD
    ),
    "most_visited": Endpoint(
        "/v1/cryptocurrency/trending/most-visited", Plan.STANDARD
    ),
    "ohlcv_latest": Endpoint("/v2/cryptocurrency/ohlcv/latest", Plan.STARTUP),
    "ohlcv_historical": Endpoint("/v2/cryptocurrency/ohlcv/historical", Plan.STARTUP),
    "price_performance": Endpoint(
        "/v2/cryptocurrency/price-performance-stats/latest", Plan.STARTUP
    ),
    "market_pairs": Endpoint("/v2/cryptocurrency/market-pairs/latest", Plan.STARTUP),
}


class CoinMarketCapError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CoinMarketCapUnavailable(CoinMarketCapError):
    """The provider could not answer. Callers fall back; they never guess."""


class CoinMarketCapNotEntitled(CoinMarketCapError):
    """The account's plan does not carry this endpoint."""


@dataclass(frozen=True, slots=True)
class CoinLinks:
    """The addresses a provider holds for one coin, exactly as published.

    Nothing here has been judged official yet. ``sharia_source_catalog`` decides that,
    and ``sharia_source_resolution`` still has to fetch each one and prove it is alive,
    permitted and readable before it can stand as evidence.
    """

    symbol: str
    cmc_id: int
    name: str
    slug: str = ""
    logo: str | None = None
    website: tuple[str, ...] = ()
    #: The whitepaper. CoinMarketCap calls it ``technical_doc``.
    whitepaper: tuple[str, ...] = ()
    source_code: tuple[str, ...] = ()
    announcement: tuple[str, ...] = ()
    message_board: tuple[str, ...] = ()
    chat: tuple[str, ...] = ()
    reddit: tuple[str, ...] = ()
    twitter: tuple[str, ...] = ()
    explorer: tuple[str, ...] = ()
    category: str | None = None
    tags: tuple[str, ...] = ()
    date_added: datetime | None = None
    platform: str | None = None
    contract_address: tuple[str, ...] = ()
    description: str | None = None
    #: True when the provider itself flags the listing as untrustworthy or delisted.
    is_hidden: bool = False
    notice: str | None = None

    @property
    def has_any_link(self) -> bool:
        return bool(
            self.website
            or self.whitepaper
            or self.source_code
            or self.announcement
            or self.message_board
            or self.chat
            or self.reddit
            or self.twitter
        )


@dataclass(frozen=True, slots=True)
class MarketRow:
    """One coin's market record. Numbers only; nothing here implies eligibility."""

    symbol: str
    cmc_id: int
    name: str
    rank: int | None = None
    price_usd: float | None = None
    market_cap_usd: float | None = None
    fully_diluted_market_cap_usd: float | None = None
    volume_24h_usd: float | None = None
    volume_change_24h: float | None = None
    percent_change_1h: float | None = None
    percent_change_24h: float | None = None
    percent_change_7d: float | None = None
    percent_change_30d: float | None = None
    percent_change_60d: float | None = None
    percent_change_90d: float | None = None
    circulating_supply: float | None = None
    total_supply: float | None = None
    max_supply: float | None = None
    market_cap_dominance: float | None = None
    infinite_supply: bool = False
    tags: tuple[str, ...] = ()
    last_updated: datetime | None = None


@dataclass(slots=True)
class CreditUsage:
    """What the last call cost, so a sweep can be reported honestly."""

    calls: int = 0
    credits: int = 0
    endpoints: dict[str, int] = field(default_factory=dict)

    def record(self, name: str, credits: int) -> None:
        self.calls += 1
        self.credits += credits
        self.endpoints[name] = self.endpoints.get(name, 0) + credits


class CoinMarketCapClient:
    """Fetches. Does not interpret, and never decides a Shariah status."""

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client
        self.usage = CreditUsage()

    # -- plan ---------------------------------------------------------------

    @property
    def plan(self) -> Plan:
        try:
            return Plan(str(self.settings.coinmarketcap_plan or "basic").lower())
        except ValueError:
            return Plan.BASIC

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.coinmarketcap_enabled and self.settings.coinmarketcap_api_key
        )

    def carries(self, name: str) -> bool:
        """Does this account's plan carry that endpoint?"""

        endpoint = ENDPOINTS.get(name)
        if endpoint is None:
            return False
        return PLAN_ORDER.index(self.plan) >= PLAN_ORDER.index(endpoint.minimum_plan)

    def available_endpoints(self) -> tuple[str, ...]:
        return tuple(sorted(name for name in ENDPOINTS if self.carries(name)))

    # -- transport ----------------------------------------------------------

    async def _get(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        endpoint = ENDPOINTS.get(name)
        if endpoint is None:
            raise CoinMarketCapError(
                "cmc_endpoint_unknown", f"No CoinMarketCap endpoint named {name}."
            )
        if not self.enabled:
            raise CoinMarketCapUnavailable(
                "cmc_disabled",
                "CoinMarketCap is switched off or has no key configured.",
            )
        if not self.carries(name):
            raise CoinMarketCapNotEntitled(
                "cmc_plan_not_entitled",
                f"The {self.plan.value} plan does not carry {name}; "
                f"it needs {endpoint.minimum_plan.value} or higher.",
            )

        key = self.settings.coinmarketcap_api_key
        headers = {
            API_KEY_HEADER: key.get_secret_value() if key else "",
            "Accept": "application/json",
        }
        url = f"{str(self.settings.coinmarketcap_api_base).rstrip('/')}{endpoint.path}"
        timeout = self.settings.coinmarketcap_timeout_seconds

        owned = self._client is None
        client = self._client or httpx.AsyncClient(timeout=timeout)
        try:
            response = await client.get(url, headers=headers, params=params)
        except httpx.HTTPError as exc:
            raise CoinMarketCapUnavailable(
                "cmc_unreachable", f"CoinMarketCap did not answer: {exc}"
            ) from exc
        finally:
            if owned:
                await client.aclose()

        try:
            payload = response.json()
        except ValueError as exc:
            raise CoinMarketCapUnavailable(
                "cmc_bad_payload", "CoinMarketCap returned a body that is not JSON."
            ) from exc

        status = payload.get("status") or {}
        error_code = int(status.get("error_code") or 0)
        self.usage.record(name, int(status.get("credit_count") or 0))
        if error_code == PLAN_NOT_ENTITLED:
            raise CoinMarketCapNotEntitled(
                "cmc_plan_not_entitled",
                f"CoinMarketCap refused {name}: the plan does not carry it.",
            )
        if error_code:
            raise CoinMarketCapUnavailable(
                "cmc_error",
                f"CoinMarketCap refused {name} ({error_code}): "
                f"{status.get('error_message') or 'no reason given'}",
            )
        return payload

    # -- reads --------------------------------------------------------------

    async def coin_links(self, symbols: Sequence[str]) -> dict[str, CoinLinks]:
        """Provider records for many coins, in batches of one call each.

        One call carries up to the configured batch size, so a sweep over five hundred
        coins costs single-figure credits rather than five hundred.
        """

        wanted = [s.strip().upper() for s in symbols if s and s.strip()]
        if not wanted:
            return {}
        size = max(1, int(self.settings.coinmarketcap_batch_size))
        found: dict[str, CoinLinks] = {}
        for start in range(0, len(wanted), size):
            batch = wanted[start : start + size]
            payload = await self._get(
                "metadata", {"symbol": ",".join(batch), "skip_invalid": "true"}
            )
            for symbol, records in (payload.get("data") or {}).items():
                record = records[0] if isinstance(records, list) and records else records
                if isinstance(record, dict):
                    found[symbol.upper()] = _links_from(symbol.upper(), record)
        return found

    async def market_rows(
        self,
        *,
        limit: int = 200,
        start: int = 1,
        convert: str = "USD",
        sort: str = "market_cap",
    ) -> list[MarketRow]:
        payload = await self._get(
            "listings",
            {"start": start, "limit": limit, "convert": convert, "sort": sort},
        )
        return [
            _market_row_from(row, convert)
            for row in (payload.get("data") or [])
            if isinstance(row, dict)
        ]

    async def quotes(self, symbols: Sequence[str], *, convert: str = "USD") -> dict[str, MarketRow]:
        wanted = [s.strip().upper() for s in symbols if s and s.strip()]
        if not wanted:
            return {}
        size = max(1, int(self.settings.coinmarketcap_batch_size))
        out: dict[str, MarketRow] = {}
        for start in range(0, len(wanted), size):
            batch = wanted[start : start + size]
            payload = await self._get(
                "quotes",
                {"symbol": ",".join(batch), "convert": convert, "skip_invalid": "true"},
            )
            for symbol, records in (payload.get("data") or {}).items():
                record = records[0] if isinstance(records, list) and records else records
                if isinstance(record, dict):
                    out[symbol.upper()] = _market_row_from(record, convert)
        return out

    async def global_metrics(self, *, convert: str = "USD") -> dict[str, Any]:
        payload = await self._get("global_metrics", {"convert": convert})
        data = payload.get("data") or {}
        quote = (data.get("quote") or {}).get(convert) or {}
        return {
            "btc_dominance": data.get("btc_dominance"),
            "eth_dominance": data.get("eth_dominance"),
            "active_cryptocurrencies": data.get("active_cryptocurrencies"),
            "total_market_cap": quote.get("total_market_cap"),
            "total_volume_24h": quote.get("total_volume_24h"),
            "altcoin_market_cap": quote.get("altcoin_market_cap"),
            "last_updated": _timestamp(data.get("last_updated")),
        }

    async def fear_and_greed(self) -> dict[str, Any]:
        """The market's mood, 0-100. A sentiment reading, never a signal to act on."""

        payload = await self._get("fear_and_greed", {})
        data = payload.get("data") or {}
        return {
            "value": data.get("value"),
            "classification": data.get("value_classification"),
            "updated_at": _timestamp(data.get("update_time")),
        }

    async def key_status(self) -> dict[str, Any]:
        payload = await self._get("key_info", {})
        data = payload.get("data") or {}
        plan = data.get("plan") or {}
        usage = data.get("usage") or {}
        month = usage.get("current_month") or {}
        return {
            "credit_limit_monthly": plan.get("credit_limit_monthly"),
            "rate_limit_minute": plan.get("rate_limit_minute"),
            "credits_used_this_month": month.get("credits_used"),
            "credits_left_this_month": month.get("credits_left"),
            "resets_in": plan.get("credit_limit_monthly_reset"),
        }


# -- shaping -------------------------------------------------------------------


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, Iterable):
        return tuple(
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        )
    return ()


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _links_from(symbol: str, record: Mapping[str, Any]) -> CoinLinks:
    urls = record.get("urls") or {}
    platform = record.get("platform") or {}
    return CoinLinks(
        symbol=symbol,
        cmc_id=int(record.get("id") or 0),
        name=str(record.get("name") or "").strip(),
        slug=str(record.get("slug") or "").strip(),
        logo=(str(record.get("logo")).strip() or None) if record.get("logo") else None,
        website=_strings(urls.get("website")),
        whitepaper=_strings(urls.get("technical_doc")),
        source_code=_strings(urls.get("source_code")),
        announcement=_strings(urls.get("announcement")),
        message_board=_strings(urls.get("message_board")),
        chat=_strings(urls.get("chat")),
        reddit=_strings(urls.get("reddit")),
        twitter=_strings(urls.get("twitter")),
        explorer=_strings(urls.get("explorer")),
        category=(str(record.get("category")).strip() or None)
        if record.get("category")
        else None,
        tags=_strings(record.get("tags")),
        date_added=_timestamp(record.get("date_added")),
        platform=(str(platform.get("name")).strip() or None)
        if isinstance(platform, Mapping) and platform.get("name")
        else None,
        contract_address=_strings(
            [
                entry.get("contract_address")
                for entry in (record.get("contract_address") or [])
                if isinstance(entry, Mapping)
            ]
        ),
        description=(str(record.get("description")).strip() or None)
        if record.get("description")
        else None,
        is_hidden=bool(record.get("is_hidden")),
        notice=(str(record.get("notice")).strip() or None)
        if record.get("notice")
        else None,
    )


def _market_row_from(record: Mapping[str, Any], convert: str) -> MarketRow:
    quote = ((record.get("quote") or {}).get(convert)) or {}
    return MarketRow(
        symbol=str(record.get("symbol") or "").upper(),
        cmc_id=int(record.get("id") or 0),
        name=str(record.get("name") or "").strip(),
        rank=record.get("cmc_rank") if isinstance(record.get("cmc_rank"), int) else None,
        price_usd=_number(quote.get("price")),
        market_cap_usd=_number(quote.get("market_cap")),
        fully_diluted_market_cap_usd=_number(quote.get("fully_diluted_market_cap")),
        volume_24h_usd=_number(quote.get("volume_24h")),
        volume_change_24h=_number(quote.get("volume_change_24h")),
        percent_change_1h=_number(quote.get("percent_change_1h")),
        percent_change_24h=_number(quote.get("percent_change_24h")),
        percent_change_7d=_number(quote.get("percent_change_7d")),
        percent_change_30d=_number(quote.get("percent_change_30d")),
        percent_change_60d=_number(quote.get("percent_change_60d")),
        percent_change_90d=_number(quote.get("percent_change_90d")),
        circulating_supply=_number(record.get("circulating_supply")),
        total_supply=_number(record.get("total_supply")),
        max_supply=_number(record.get("max_supply")),
        market_cap_dominance=_number(quote.get("market_cap_dominance")),
        infinite_supply=bool(record.get("infinite_supply")),
        tags=_strings(record.get("tags")),
        last_updated=_timestamp(record.get("last_updated")),
    )


__all__ = [
    "API_KEY_HEADER",
    "ENDPOINTS",
    "CoinLinks",
    "CoinMarketCapClient",
    "CoinMarketCapError",
    "CoinMarketCapNotEntitled",
    "CoinMarketCapUnavailable",
    "CreditUsage",
    "MarketRow",
    "Plan",
]
