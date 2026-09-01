"""Research the coins nobody has screened yet — the ones a user can already buy.

There is a gap between what an exchange lists and what this product has an answer for.
Binance and Bybit list hundreds of spot assets; three authorities have ruled on 184 of
them. Every other listed coin is one a user can search for, find nothing about, and
draw their own conclusion from the silence.

This service does not close that gap — only an authority can — but it removes the
excuse for it. It finds every coin that is **tradeable and unscreened**, and gathers
what a market-data provider already publishes about each one: the project's own
website, its whitepaper, its repository, its logo, what it is and when it launched.
That is the factual half of a Passport, assembled before anybody asks for it, so the
first hour of reviewing a coin is not spent hunting for its whitepaper.

**Three rules this module will not bend.**

*No status, ever.* Nothing here writes, infers or implies halal or haram. A coin with a
gathered profile is still an unscreened coin. The absence of a ruling is the honest
answer and it stays the answer until an authority gives another one.

*Ranked by usefulness, not alphabetically.* A coin with real volume affects real people;
one with none can wait. The queue is worked in market-cap order so the effort lands
where users actually are.

*Cheap by construction.* One provider call carries a hundred coins. A sweep over the
entire unscreened universe costs single-figure credits, which is why this can run on a
schedule instead of being a special occasion.

Planning and running are separate on purpose. :meth:`plan` answers "what would this do?"
and touches no network at all, so it is safe to call anywhere — an operator screen, a
test, a dry run before spending anything.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    ProviderCoinProfile,
)
from ai_market_monitor.services.coinmarketcap import (
    CoinLinks,
    CoinMarketCapClient,
    CoinMarketCapError,
)

logger = logging.getLogger(__name__)

#: National currencies. These are the quote side of a pair, never a project to research.
#:
#: Stablecoins are deliberately **not** here. Dai and USDC are projects with a company,
#: a mechanism and a whitepaper, and one of them is on Fasset's refused list while
#: another is on its accepted one — precisely the distinction a reviewer needs the
#: facts for. Excluding them would hide the coins where the question is most alive.
_FIAT_CODES: frozenset[str] = frozenset(
    {"USD", "EUR", "GBP", "TRY", "JPY", "AUD", "CAD", "CHF", "BRL", "ARS", "ZAR", "NGN"}
)

#: Leveraged tokens an exchange lists beside its spot pairs — ``BTCUP``, ``ETHBULL``.
#: They are not separate projects and must never become rows here.
_DERIVATIVE_SUFFIXES: tuple[str, ...] = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S")

#: What has to be left once a leveraged suffix is removed for the match to be real.
#:
#: Matching the suffix alone silently deleted real coins: ``JUP`` ends with ``UP``, so
#: Jupiter — a top-100 asset — was being dropped as a leveraged token and would never
#: have been researched. A leveraged ticker is always another symbol plus the suffix
#: (``BTC`` + ``UP``), so the remainder has to look like a symbol in its own right.
_MINIMUM_BASE_LENGTH = 3


@dataclass(slots=True)
class ResearchPlan:
    """What a run would do, worked out without spending anything."""

    tradeable: int = 0
    already_screened: int = 0
    already_profiled: int = 0
    to_research: list[str] = field(default_factory=list)
    exchanges: dict[str, int] = field(default_factory=dict)

    @property
    def estimated_provider_calls(self) -> int:
        return 0 if not self.to_research else (len(self.to_research) + 99) // 100

    def as_dict(self) -> dict[str, object]:
        return {
            "tradeable": self.tradeable,
            "already_screened": self.already_screened,
            "already_profiled": self.already_profiled,
            "to_research": len(self.to_research),
            "sample": self.to_research[:25],
            "exchanges": dict(self.exchanges),
            "estimated_provider_calls": self.estimated_provider_calls,
            "estimated_credits": self.estimated_provider_calls,
        }


@dataclass(slots=True)
class ResearchResult:
    profiles_created: int = 0
    profiles_updated: int = 0
    provider_answered: int = 0
    provider_silent: list[str] = field(default_factory=list)
    with_whitepaper: int = 0
    with_website: int = 0
    credits_spent: int = 0
    status: str = "completed"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "profiles_created": self.profiles_created,
            "profiles_updated": self.profiles_updated,
            "provider_answered": self.provider_answered,
            "provider_silent": len(self.provider_silent),
            "with_whitepaper": self.with_whitepaper,
            "with_website": self.with_website,
            "credits_spent": self.credits_spent,
        }


def _is_researchable(symbol: str) -> bool:
    """Is this a coin worth gathering facts about, or exchange plumbing?"""

    upper = symbol.strip().upper()
    if not upper or len(upper) > 32 or upper in _FIAT_CODES:
        return False
    return not any(
        upper.endswith(suffix)
        and len(upper) - len(suffix) >= _MINIMUM_BASE_LENGTH
        for suffix in _DERIVATIVE_SUFFIXES
    )


class UnscreenedCoinResearchService:
    """Finds tradeable-but-unscreened coins and gathers provider facts about them."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        market_data_provider: object | None = None,
        coinmarketcap: CoinMarketCapClient | None = None,
    ):
        self.session = session
        self.settings = settings
        self.provider = market_data_provider
        self.coinmarketcap = coinmarketcap or CoinMarketCapClient(settings)

    # -- planning: no network, no credits ------------------------------------

    async def plan(self, *, tradeable_symbols: Sequence[str] | None = None) -> ResearchPlan:
        """What a run would research. Reads the database; spends nothing.

        ``tradeable_symbols`` lets a caller supply the exchange listing it already has.
        Passing nothing asks the configured market-data provider for it, which is the
        only part of planning that touches a network — and it is the exchange's own
        public listing, not a paid call.
        """

        plan = ResearchPlan()
        listings = await self._tradeable(tradeable_symbols)
        for exchange, symbols in listings.items():
            plan.exchanges[exchange] = len(symbols)
        universe = {s for symbols in listings.values() for s in symbols}
        plan.tradeable = len(universe)

        screened = await self._screened_symbols()
        profiled = await self._profiled_symbols()
        plan.already_screened = len(universe & screened)
        plan.already_profiled = len(universe & profiled)
        plan.to_research = sorted(universe - screened - profiled)
        return plan

    async def _tradeable(
        self, supplied: Sequence[str] | None
    ) -> dict[str, set[str]]:
        if supplied is not None:
            return {"supplied": {s.upper() for s in supplied if _is_researchable(s)}}
        if self.provider is None:
            return {}
        quotes = ["USDT"]
        out: dict[str, set[str]] = {}
        for exchange in ("binance", "bybit"):
            try:
                pairs = await self.provider.list_symbols(exchange, quotes)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - an exchange being down is not our failure
                logger.info("exchange_listing_unavailable", extra={"exchange": exchange})
                continue
            bases: set[str] = set()
            for pair in pairs:
                base = str(pair).split("/")[0].strip().upper()
                if _is_researchable(base):
                    bases.add(base)
            out[exchange] = bases
        return out

    async def _screened_symbols(self) -> set[str]:
        """Every coin that already carries a Shariah assessment, whatever it says.

        Halal and haram are equally "already answered" here: this service exists for
        the coins with **no** answer at all.

        Read straight from the assessments, deliberately **not** joined to
        ``CanonicalAsset``. An inner join would silently drop any assessment whose
        canonical asset row is missing or was renamed, and a dropped assessment reads
        as "nobody has screened this" — so the coin would be queued for research it
        does not need, and its existing ruling would be invisible to this service.
        The assessment is the authority on whether an answer exists.
        """

        rows = await self.session.scalars(
            select(AssetShariaAssessment.canonical_asset).distinct()
        )
        return {str(symbol).upper() for symbol in rows if symbol}

    async def _profiled_symbols(self) -> set[str]:
        rows = await self.session.scalars(
            select(ProviderCoinProfile.symbol).where(
                ProviderCoinProfile.research_state != "pending"
            )
        )
        return {str(symbol).upper() for symbol in rows if symbol}

    # -- running: this is the part that spends credits -----------------------

    async def research(
        self,
        *,
        symbols: Sequence[str] | None = None,
        limit: int | None = None,
        tradeable_symbols: Sequence[str] | None = None,
    ) -> ResearchResult:
        """Gather provider facts for unscreened coins and store them.

        Never raises for a provider problem: the provider being off, unreachable or not
        entitled all leave the queue exactly as it was, to be picked up next run.
        """

        result = ResearchResult()
        if not self.settings.coinmarketcap_enabled:
            result.status = "provider_disabled"
            return result

        wanted = list(symbols or [])
        if not wanted:
            plan = await self.plan(tradeable_symbols=tradeable_symbols)
            wanted = plan.to_research
        cap = limit or self.settings.unscreened_research_batch_limit
        wanted = [s for s in wanted if _is_researchable(s)][: max(0, cap)]
        if not wanted:
            result.status = "nothing_to_research"
            return result

        try:
            found = await self.coinmarketcap.coin_links(wanted)
        except CoinMarketCapError as exc:
            logger.info("unscreened_research_provider_unavailable", extra={"reason": exc.code})
            result.status = exc.code
            return result

        result.credits_spent = self.coinmarketcap.usage.credits
        result.provider_answered = len(found)
        result.provider_silent = sorted(set(wanted) - set(found))

        existing = {
            row.symbol: row
            for row in await self.session.scalars(
                select(ProviderCoinProfile).where(
                    ProviderCoinProfile.symbol.in_(wanted),
                    ProviderCoinProfile.provider == "coinmarketcap",
                )
            )
        }
        now = datetime.now(UTC)
        for symbol, record in found.items():
            row = existing.get(symbol)
            if row is None:
                row = ProviderCoinProfile(provider="coinmarketcap", symbol=symbol)
                self.session.add(row)
                result.profiles_created += 1
            else:
                result.profiles_updated += 1
            _apply(row, record, now)
            if row.whitepaper_url:
                result.with_whitepaper += 1
            if row.official_website:
                result.with_website += 1

        # A coin the provider does not know is recorded too, so the next run does not
        # ask about it again and again. Silence is an answer worth storing.
        for symbol in result.provider_silent:
            row = existing.get(symbol)
            if row is None:
                row = ProviderCoinProfile(provider="coinmarketcap", symbol=symbol)
                self.session.add(row)
                result.profiles_created += 1
            row.research_state = "skipped"
            row.skip_reason = "The market-data provider has no record for this symbol."
            row.refreshed_at = now

        await self.session.flush()
        return result


def _apply(row: ProviderCoinProfile, record: CoinLinks, now: datetime) -> None:
    row.provider_id = record.cmc_id or None
    row.name = record.name or row.name
    row.slug = record.slug or row.slug
    row.official_website = record.website[0] if record.website else None
    row.whitepaper_url = record.whitepaper[0] if record.whitepaper else None
    row.source_code_url = record.source_code[0] if record.source_code else None
    row.logo_url = record.logo
    row.links = {
        "website": list(record.website),
        "whitepaper": list(record.whitepaper),
        "source_code": list(record.source_code),
        "announcement": list(record.announcement),
        "message_board": list(record.message_board),
        "chat": list(record.chat),
        "reddit": list(record.reddit),
        "twitter": list(record.twitter),
        "explorer": list(record.explorer),
    }
    row.category = record.category
    row.tags = list(record.tags)
    row.platform = record.platform
    row.description = record.description
    row.date_added = record.date_added
    row.provider_flagged = record.is_hidden
    row.provider_notice = record.notice
    row.research_state = "researched"
    row.skip_reason = None
    row.refreshed_at = now


__all__ = [
    "ResearchPlan",
    "ResearchResult",
    "UnscreenedCoinResearchService",
]
