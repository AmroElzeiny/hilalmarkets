"""What a signed-out visitor may see of the screened market.

The public Market page (``/markets``) is the dashboard's Halal Assets list with one
difference: a visitor sees the first :data:`PUBLIC_MARKET_VISIBLE_COUNT` coins, and the
rest ask them to open a free account. This module is the one owner of that line.

Somebody who is already signed in sees the same page with nothing behind the line: they
already have the account the line asks for. ``unlocked`` is how the caller says so, and
only the caller can, because only it has read the session.

Three things read it, and all three must draw it in the same place:

* the page's price feed, which sends only the visible coins (all of them to a reader
  who is signed in);
* the public assistant, which asks a visitor to sign up before it talks about a coin
  that is not on the page (:meth:`PublicMarketService.visible_symbols`);
* the page itself, which says how many coins are behind the line.

"The first 20" means the first 20 in the dashboard's own order — biggest 24-hour trading
volume first — because both lists come from :func:`screened_market_snapshot`. Nothing
here sorts a second time.

**Fail closed.** When the list cannot be read, no coin is treated as public. The page
says the prices could not be read, and the assistant asks the visitor to sign in rather
than guess which coins the page would have shown.
"""

from __future__ import annotations

import re
from collections import Counter
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

#: How many coins a signed-out visitor sees. The page, the feed and the assistant all
#: read this one number.
PUBLIC_MARKET_VISIBLE_COUNT: Final[int] = 20

#: The exchanges the Market page offers, in the order its switch shows them. The first
#: one is where the page opens.
MARKET_EXCHANGES: Final[tuple[str, ...]] = ("binance", "bybit")

#: The same list, as the pattern a query parameter is checked against.
MARKET_EXCHANGE_PATTERN: Final[str] = "^(" + "|".join(map(re.escape, MARKET_EXCHANGES)) + ")$"

#: The quote currency the public page lists prices in.
PUBLIC_MARKET_QUOTE: Final[str] = "USDT"

#: How long the list of coins visible to the public is trusted by the assistant.
#:
#: Longer than a price refresh on purpose. Which coins are in the first twenty changes
#: only when trading volume reorders the list, and the assistant asks for this on every
#: coin question — reading every standard on both exchanges each time would put a price
#: call in front of every answer.
_VISIBLE_SYMBOLS_SECONDS: Final[float] = 60.0


def market_account_links(settings: Settings, destination: str = MARKET_PATH) -> dict[str, str]:
    """Sign-up and sign-in addresses that bring the visitor back to what they wanted.

    Both carry ``next``, so a visitor who signs up from the Market page — or from the
    assistant, when it asks them to — lands on the full list in the dashboard rather than
    on the dashboard's front page, and one who asked for a coin's Passport lands on it.
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


@dataclass(slots=True)
class _CachedSymbols:
    value: frozenset[str]
    expires_at: float


def _cut(whole: PublicMarketResponse, *, unlocked: bool) -> PublicMarketResponse:
    """The list as one reader may see it. The only place the line is drawn.

    A copy every time: the cached list is shared by every reader, and a visitor's cut
    must never be able to change what the next signed-in reader is sent.
    """

    if unlocked:
        return whole.model_copy()
    shown = whole.items[:PUBLIC_MARKET_VISIBLE_COUNT]
    return whole.model_copy(
        update={
            "items": shown,
            "visible_limit": PUBLIC_MARKET_VISIBLE_COUNT,
            "hidden_count": whole.total - len(shown),
        }
    )


class PublicMarketUnavailable(RuntimeError):
    """The standard asked for is not one the public page offers."""


class PublicMarketService:
    """The public Market page's list, cut at the visible line."""

    _views: dict[tuple[int, UUID, str, str], _Cached] = {}
    _symbols: dict[int, _CachedSymbols] = {}

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
        cls._symbols.clear()

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
        unlocked: bool = False,
    ) -> PublicMarketResponse:
        """The visible coins for one standard on one exchange, and a count of the rest.

        ``unlocked`` is for a signed-in reader: every coin, and nothing behind the line.
        Both answers are cut from one cached list, so a visitor and a member can never be
        shown two different orders of the same coins.

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
        return _cut(cached.value, unlocked=unlocked)

    async def _snapshot(
        self,
        key: tuple[int, UUID, str, str],
        methodology_id: UUID,
        exchange_key: str,
        quote_key: str,
    ) -> _Cached:
        """The whole screened list for one view, cached; :func:`_cut` draws the line."""

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
            visible_limit=len(everything),
            hidden_count=0,
            status_counts=dict(Counter(str(item.status) for item in everything)),
        )
        # Never trusted for longer than the price snapshot underneath it, so the public
        # page can never show a price older than the dashboard's.
        cached = _Cached(
            value=whole,
            expires_at=monotonic() + self.settings.sharia_live_quote_cache_seconds,
        )
        self._views[key] = cached
        return cached

    async def visible_symbols(self) -> frozenset[str]:
        """Every coin a signed-out visitor can see on the Market page, in any view of it.

        The union over every standard the picker offers and every exchange the switch
        offers: a visitor who switched to Bybit and read a coin there has seen it, and
        the assistant must not then ask them to sign up to hear about it.

        A view that cannot be read adds nothing. That is the fail-closed direction — a
        coin is only public when the page could really have shown it.
        """

        key = id(self.provider)
        cached = self._symbols.get(key)
        if cached is not None and cached.expires_at > monotonic():
            return cached.value
        # No lock. Two visitors asking at the same moment both read the list, which
        # costs one extra read and cannot give either of them a wrong answer.
        found: set[str] = set()
        for methodology in await self.methodologies():
            for exchange in MARKET_EXCHANGES:
                try:
                    view = await self.view(methodology_id=methodology.id, exchange=exchange)
                except Exception:
                    # A view that cannot be read shows a visitor nothing, so it makes
                    # nothing public either.
                    continue
                found.update(str(item.canonical_asset).upper() for item in view.items)
        value = frozenset(found)
        self._symbols[key] = _CachedSymbols(
            value=value, expires_at=monotonic() + _VISIBLE_SYMBOLS_SECONDS
        )
        return value
