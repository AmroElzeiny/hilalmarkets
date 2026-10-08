"""The screened market, as everyone sees it on the public Market page.

The public Market page (``/markets``) is the dashboard's Halal Assets list, open to
everyone: every screened coin, in the dashboard's own order — biggest 24-hour trading
volume first — because both lists come from :func:`screened_market_snapshot`. Nothing
here sorts a second time, and nothing is held back from a visitor without an account.
Since 8 October 2026 a visitor is treated like a member on the free plan, who always
saw every coin; the page used to stop after twenty and ask for an account.

What still needs an account is what is stored on one: following a coin and Favorites.

**Fail closed.** When the list cannot be read, the page says the prices could not be
read. Nothing is guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from time import monotonic
from typing import Final
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.app_links import app_link
from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.dashboard_paths import MARKET_PATH
from ai_market_monitor.db.models import ShariaMethodology
from ai_market_monitor.schemas.sharia import PublicMarketResponse
from ai_market_monitor.services.interfaces import MarketDataProvider
from ai_market_monitor.services.screened_market import screened_market_snapshot
from ai_market_monitor.services.sharia_screening import (
    ShariaScreeningService,
    market_default_methodology,
)

#: The exchanges the Market page offers, in the order its switch shows them. The first
#: one is where the page opens.
MARKET_EXCHANGES: Final[tuple[str, ...]] = ("binance", "bybit")

#: The same list, as the pattern a query parameter is checked against.
MARKET_EXCHANGE_PATTERN: Final[str] = "^(" + "|".join(map(re.escape, MARKET_EXCHANGES)) + ")$"

#: The quote currency the public page lists prices in.
PUBLIC_MARKET_QUOTE: Final[str] = "USDT"


def market_account_links(settings: Settings, destination: str = MARKET_PATH) -> dict[str, str]:
    """Sign-up and sign-in addresses that bring the visitor back to what they wanted.

    Both carry ``next``, so a visitor who signs up from the Market page to follow a coin
    lands on the list in the dashboard rather than on the dashboard's front page.
    """

    query = urlencode({"next": destination})
    return {
        "signup": app_link(settings, f"/signup?{query}"),
        "signin": app_link(settings, f"/signin?{query}"),
    }


@dataclass(slots=True)
class _Cached:
    value: PublicMarketResponse
    expires_at: float


class PublicMarketUnavailable(RuntimeError):
    """The standard asked for is not one the public page offers."""


class PublicMarketService:
    """The public Market page's list: every screened coin, for everyone."""

    _views: dict[tuple[int, UUID, str, str], _Cached] = {}

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        provider: MarketDataProvider,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider = provider

    @classmethod
    def clear_cache(cls) -> None:
        cls._views.clear()

    # -- which standard ------------------------------------------------------

    async def methodologies(self) -> list[ShariaMethodology]:
        """The standards the page's picker offers — the dashboard's own list."""

        screening = ShariaScreeningService(self.session, self.settings)
        return await screening.selectable_market_methodologies()

    async def choose(
        self,
        methodologies: list[ShariaMethodology],
        requested: UUID | None,
    ) -> ShariaMethodology | None:
        """The standard to show: the one asked for if the page offers it, else the default.

        A standard the picker does not offer is never shown, whatever the address says.
        Development standards and retired ones are exactly the ones missing from the
        picker, and a hand-typed address must not be a way round that.
        """

        if requested is not None:
            chosen = next((item for item in methodologies if item.id == requested), None)
            if chosen is not None:
                return chosen
        default = market_default_methodology(methodologies)
        if default is not None:
            return default
        screening = ShariaScreeningService(self.session, self.settings)
        fallback = await screening.default_methodology()
        if fallback is not None and any(item.id == fallback.id for item in methodologies):
            return fallback
        return methodologies[0] if methodologies else None

    # -- the list ------------------------------------------------------------

    async def view(
        self,
        *,
        methodology_id: UUID,
        exchange: str,
        quote_asset: str = PUBLIC_MARKET_QUOTE,
    ) -> PublicMarketResponse:
        """Every screened coin for one standard on one exchange.

        The same answer for a visitor and a member, from one cached list.

        Raises :class:`PublicMarketUnavailable` for a standard the page does not offer.
        A price failure is raised as it came, for the caller to report honestly.
        """

        exchange_key = exchange.strip().lower()
        if exchange_key not in MARKET_EXCHANGES:
            raise PublicMarketUnavailable("That exchange is not offered on this page.")
        quote_key = quote_asset.strip().upper()
        key = (id(self.provider), methodology_id, exchange_key, quote_key)
        cached = self._views.get(key)
        if cached is None or cached.expires_at <= monotonic():
            cached = await self._snapshot(key, methodology_id, exchange_key, quote_key)
        # A copy: the cached list is shared by every reader.
        return cached.value.model_copy()

    async def _snapshot(
        self,
        key: tuple[int, UUID, str, str],
        methodology_id: UUID,
        exchange_key: str,
        quote_key: str,
    ) -> _Cached:
        """The whole screened list for one view, cached."""

        offered = await self.methodologies()
        if not any(item.id == methodology_id for item in offered):
            raise PublicMarketUnavailable("That Shariah standard is not offered on this page.")

        snapshot = await screened_market_snapshot(
            session=self.session,
            settings=self.settings,
            provider=self.provider,
            methodology_id=methodology_id,
            exchange=exchange_key,
            quote_asset=quote_key,
        )
        everything = list(snapshot.items)
        whole = PublicMarketResponse(
            **snapshot.model_dump(exclude={"items", "total"}),
            items=everything,
            total=len(everything),
        )
        # Never trusted for longer than the price snapshot underneath it, so the public
        # page can never show a price older than the dashboard's.
        cached = _Cached(
            value=whole,
            expires_at=monotonic() + self.settings.sharia_live_quote_cache_seconds,
        )
        self._views[key] = cached
        return cached
