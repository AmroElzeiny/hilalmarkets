from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import (
    UserPrincipal,
    get_market_data_provider,
    get_user_principal,
)
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.schemas.on_demand import OnDemandScanRequest, OnDemandScanResponse
from ai_market_monitor.services.interfaces import MarketDataProvider
from ai_market_monitor.services.on_demand_scans import OnDemandScanError, OnDemandScanService

router = APIRouter(prefix="/on-demand-scans", tags=["on-demand-scans"])


@router.post("", response_model=OnDemandScanResponse, status_code=201)
async def scan_market_now(
    request: OnDemandScanRequest,
    principal: UserPrincipal = Depends(get_user_principal),
    session: AsyncSession = Depends(get_db_session),
    provider: MarketDataProvider = Depends(get_market_data_provider),
) -> OnDemandScanResponse:
    try:
        response = await OnDemandScanService(session, provider).run(principal.user_id, request)
        await session.commit()
        return response
    except OnDemandScanError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
