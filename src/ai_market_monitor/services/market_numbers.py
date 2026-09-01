"""How big a coin is and how it has moved over weeks. Read once a day, not once a page.

An exchange ticker knows one market and one day: the last price, today's high and low,
what changed since yesterday. It cannot say how large the coin is against every other
coin, where it ranks, or whether this week's fall follows three months of rising. Those
are the questions a beginner actually asks — *is this a big coin or a small one, and is
today unusual?* — and answering them needs a market-data provider.

**Read on a schedule, stored, and served from the database.** The whole screened list is
two provider calls, so a daily refresh costs about sixty credits a month. Fetching the
same numbers while a page loads, at the cadence prices refresh at, would cost seventeen
thousand — and none of these numbers changes fast enough for that to buy anything. A
ninety-day price change does not need a five-second cache.

**Absent is a normal answer.** A coin the provider has never heard of keeps its price,
its Shariah status and its Passport; the long-range numbers are simply missing, and the
page says so rather than drawing a zero.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import ProviderCoinProfile
from ai_market_monitor.services.coinmarketcap import (
    CoinMarketCapClient,
    CoinMarketCapError,
    MarketRow,
)

logger = logging.getLogger(__name__)

#: The fields carried out of a provider row, and the names the product uses for them.
#:
#: Named here once. The provider's own spelling (``percent_change_30d``) stops at this
#: module; everything downstream — the schema, the payload, the browser — reads the
#: product's spelling (``percentage_30d``), which is the one the 24-hour field already
#: used. Two spellings of the same number is how a filter ends up reading a field that
#: is always empty.
FIELD_NAMES: dict[str, str] = {
    "market_cap_usd": "market_cap_usd",
    "fully_diluted_market_cap_usd": "fully_diluted_usd",
    "rank": "market_rank",
    "percent_change_7d": "percentage_7d",
    "percent_change_30d": "percentage_30d",
    "percent_change_90d": "percentage_90d",
    "circulating_supply": "circulating_supply",
    "max_supply": "max_supply",
}


@dataclass(slots=True)
class RefreshResult:
    updated: int = 0
    unknown: list[str] = None  # type: ignore[assignment]
    credits_spent: int = 0
    status: str = "completed"

    def __post_init__(self) -> None:
        if self.unknown is None:
            self.unknown = []

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "updated": self.updated,
            "unknown": len(self.unknown),
            "credits_spent": self.credits_spent,
        }


def numbers_from(row: MarketRow) -> dict[str, Any]:
    """One provider row as the product's own field names."""

    return {
        product_name: getattr(row, provider_name, None)
        for provider_name, product_name in FIELD_NAMES.items()
    }


class MarketNumbersService:
    """Refreshes the long-range numbers, and hands them back to whoever draws a page."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        coinmarketcap: CoinMarketCapClient | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.coinmarketcap = coinmarketcap or CoinMarketCapClient(settings)

    async def refresh(self, symbols: Sequence[str]) -> RefreshResult:
        """Read the numbers for these coins and store them. Spends credits."""

        result = RefreshResult()
        wanted = sorted({str(s).strip().upper() for s in symbols if str(s).strip()})
        if not self.settings.coinmarketcap_enabled:
            result.status = "provider_disabled"
            return result
        if not wanted:
            result.status = "nothing_to_refresh"
            return result

        try:
            rows = await self.coinmarketcap.quotes(wanted)
        except CoinMarketCapError as exc:
            logger.info("market_numbers_unavailable", extra={"reason": exc.code})
            result.status = exc.code
            return result
        result.credits_spent = self.coinmarketcap.usage.credits
        result.unknown = sorted(set(wanted) - set(rows))

        existing = {
            profile.symbol: profile
            for profile in await self.session.scalars(
                select(ProviderCoinProfile).where(
                    ProviderCoinProfile.symbol.in_(wanted),
                    ProviderCoinProfile.provider == "coinmarketcap",
                )
            )
        }
        now = datetime.now(UTC)
        for symbol, row in rows.items():
            profile = existing.get(symbol)
            if profile is None:
                profile = ProviderCoinProfile(provider="coinmarketcap", symbol=symbol)
                self.session.add(profile)
            profile.market_cap_usd = row.market_cap_usd
            profile.volume_24h_usd = row.volume_24h_usd
            profile.provider_rank = row.rank
            profile.market_numbers = numbers_from(row)
            profile.market_numbers_at = now
            result.updated += 1

        await self.session.flush()
        return result

    async def read(self, symbols: Sequence[str]) -> dict[str, dict[str, Any]]:
        """The stored numbers for these coins. Reads the database; spends nothing.

        Returns only what has actually been read from the provider. A symbol with no row,
        or a row whose refresh has never run, is simply absent from the answer — never a
        dictionary of zeros, which a page would draw as "this coin is worth nothing".
        """

        wanted = {str(s).strip().upper() for s in symbols if str(s).strip()}
        if not wanted:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for profile in await self.session.scalars(
            select(ProviderCoinProfile).where(
                ProviderCoinProfile.symbol.in_(wanted),
                ProviderCoinProfile.provider == "coinmarketcap",
            )
        ):
            if not profile.market_numbers or profile.market_numbers_at is None:
                continue
            out[profile.symbol] = {
                **dict(profile.market_numbers),
                "market_numbers_at": profile.market_numbers_at,
            }
        return out


__all__ = ["FIELD_NAMES", "MarketNumbersService", "RefreshResult", "numbers_from"]
