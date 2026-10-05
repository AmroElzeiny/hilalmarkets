"""The provider's own picture for coins that have no verified identity record yet.

``core/asset_logos.asset_logo`` reads a coin's pictures from its ``CanonicalAsset``. A new
coin the researcher has read usually has no such record yet, so a page drawing it from
its ticker alone got the letters — even though the coin researcher had already saved
CoinMarketCap's picture of it on the coin's provider profile.

This reads those saved pictures for a set of tickers in one query, and hands each back
through the same ``https``-only check ``asset_logos`` uses, under the same key, so the
picture a page asks for here is accepted or refused by exactly the rule every other page
uses.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.asset_logos import PROVIDER_LOGO_FIELD, provider_logo_url
from ai_market_monitor.db.models import ProviderCoinProfile


async def provider_logos(
    session: AsyncSession, symbols: Iterable[str]
) -> dict[str, str]:
    """``{TICKER: https picture}`` for the tickers that have one. One query."""

    wanted = sorted({str(item).strip().upper() for item in symbols if str(item).strip()})
    if not wanted:
        return {}
    rows = await session.execute(
        select(ProviderCoinProfile.symbol, ProviderCoinProfile.logo_url).where(
            ProviderCoinProfile.symbol.in_(wanted),
            ProviderCoinProfile.logo_url.is_not(None),
        )
    )
    found: dict[str, str] = {}
    for symbol, url in rows:
        checked = provider_logo_url({PROVIDER_LOGO_FIELD: url})
        if checked and symbol.upper() not in found:
            found[symbol.upper()] = checked
    return found


__all__ = ["provider_logos"]
