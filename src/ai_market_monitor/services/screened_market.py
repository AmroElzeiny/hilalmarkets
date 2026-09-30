"""The screened market, with live prices: one reader for every page that lists it.

Two pages list the screened coins with prices — the dashboard's Halal Assets and the
public Market page — and they must agree on which coins are listed, in which order, and
what each one says. The order in particular is the whole of the public page's promise:
it shows "the first 20", and "first" means the order the dashboard shows them in. A
second copy of this function would be a second opinion about that order.

Nothing here estimates a price or lets a price change a status. The statuses are read
from the published review; the prices come from the exchange, through the cached
snapshot in :mod:`live_market_quotes`.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.schemas.sharia import LiveSpotMarketResponse
from ai_market_monitor.services.interfaces import MarketDataProvider
from ai_market_monitor.services.live_market_quotes import LiveMarketQuoteService
from ai_market_monitor.services.market_numbers import MarketNumbersService
from ai_market_monitor.services.sharia_screening import (
    DEFAULT_ALLOWED_STATUSES,
    ShariaScreeningService,
)


async def screened_market_snapshot(
    *,
    session: AsyncSession,
    settings: Settings,
    provider: MarketDataProvider,
    methodology_id: UUID,
    exchange: str,
    quote_asset: str,
) -> LiveSpotMarketResponse:
    """Every coin that passed one standard, with its live quote, biggest market first.

    Raises :class:`ShariaScreeningError` when the standard does not exist, and lets a
    provider failure through untouched — each caller says in its own words that no
    price was invented.
    """

    screening = ShariaScreeningService(session, settings)
    methodology = await screening.methodology(methodology_id)
    screened = await screening.list_screened_assets(
        methodology_id=methodology.id,
        statuses=DEFAULT_ALLOWED_STATUSES,
        limit=10_000,
    )
    # Size, rank and how the coin moved over weeks. Read from the database, where a
    # scheduled task put them — never fetched while this page is loading, because none
    # of these numbers changes fast enough to be worth a provider call per view.
    market_numbers = await MarketNumbersService(session, settings).read(
        [item.canonical_asset for item in screened.items]
    )
    return await LiveMarketQuoteService(provider, settings).screened_snapshot(
        exchange=exchange,
        quote_asset=quote_asset,
        methodology=screening.methodology_summary(methodology),
        assessments=screened.items,
        warning=screened.warning,
        market_numbers=market_numbers,
    )
