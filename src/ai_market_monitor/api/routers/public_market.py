"""The price feed behind the public Market page.

Anonymous on purpose. It answers with every screened coin, the same list for a visitor
and for a member: a visitor is treated like a member on the free plan, who always saw
every coin.

Every reply is served from a short in-process cache in :class:`PublicMarketService`, so
however many visitors keep the page open, the database and the exchange are asked at
most once per refresh interval for each view.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import get_market_data_provider
from ai_market_monitor.api.route_security import public_api
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.schemas.sharia import PassportQuickViewResponse, PublicMarketResponse
from ai_market_monitor.services.interfaces import MarketDataProvider
from ai_market_monitor.services.public_market import (
    MARKET_EXCHANGE_PATTERN,
    MARKET_EXCHANGES,
    PublicMarketService,
    PublicMarketUnavailable,
)
from ai_market_monitor.services.sharia_passports import ShariaPassportReadService
from ai_market_monitor.services.sharia_screening import ShariaScreeningError

#: The page key the launch stage hides before accounts can be opened. Read here so the
#: feed is closed whenever the page is.
PUBLIC_MARKET_PAGE = "market"

router = APIRouter(prefix="/public-market", tags=["public-market"])


@router.get("/quotes", response_model=PublicMarketResponse)
@public_api(
    "Publishes every screened coin of the public Market page, the same list for "
    "visitors and members."
)
async def public_market_quotes(
    response: Response,
    methodology_id: UUID | None = None,
    exchange: str = Query(default=MARKET_EXCHANGES[0], pattern=MARKET_EXCHANGE_PATTERN),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    provider: MarketDataProvider = Depends(get_market_data_provider),
) -> PublicMarketResponse:
    if PUBLIC_MARKET_PAGE in settings.stage_exposure.hidden_pages:
        raise HTTPException(status_code=404, detail="Not found")
    # Live prices: no cache in front of the site may keep an old copy.
    response.headers["Cache-Control"] = "private, no-store"
    service = PublicMarketService(session, settings, provider)
    methodologies = await service.methodologies()
    if methodology_id is None:
        chosen = await service.choose(methodologies, None)
        if chosen is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "no_screening_standard",
                    "message": "No Shariah standard is published yet, so no coin is listed.",
                },
            )
        methodology_id = chosen.id
    try:
        return await service.view(methodology_id=methodology_id, exchange=exchange)
    except (PublicMarketUnavailable, ShariaScreeningError) as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "standard_not_offered",
                "message": "That Shariah standard is not offered on this page.",
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "live_market_unavailable",
                "message": "Live spot quotes are unavailable; no prices were invented.",
            },
        ) from exc


@router.get("/passports/{asset}/quick-view", response_model=PassportQuickViewResponse)
@public_api(
    "The short Passport popup behind \"See the evidence\" on the public Market page; "
    "the same published record the public Passport page shows to everyone."
)
async def public_passport_quick_view(
    asset: str,
    methodology: UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PassportQuickViewResponse:
    """A coin's Passport, short, for the popup on the public Market page.

    The Passport page itself (`/passports/<coin>`) is open to everybody, so its summary
    is too. Read with no account, exactly as that page reads it: the current published
    record only. The dashboard's own popup can also open the version used for one alert;
    that needs the alert, so it stays on the signed-in route
    (`/api/v1/sharia/assets/<coin>/passport/quick-view`).
    """

    if PUBLIC_MARKET_PAGE in settings.stage_exposure.hidden_pages:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        return await ShariaPassportReadService(session, settings).quick_view(
            asset, methodology_id=methodology
        )
    except ShariaScreeningError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
