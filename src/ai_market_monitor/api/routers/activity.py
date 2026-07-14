from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import UserPrincipal
from ai_market_monitor.api.routers.dashboard_api import get_dashboard_principal
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.db.models.enums import ShariaAssetStatus
from ai_market_monitor.schemas.sharia import ActivityResponse
from ai_market_monitor.services.activity import ActivityReadService

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("", response_model=ActivityResponse)
async def activity_items(
    tab: Literal[
        "all", "forming", "alerts", "ended", "compliance_changes", "investigations"
    ] = "all",
    monitor_id: UUID | None = None,
    symbol: str | None = Query(default=None, max_length=40),
    sharia_status: ShariaAssetStatus | None = None,
    opportunity_state: str | None = Query(default=None, max_length=60),
    methodology_id: UUID | None = None,
    delivery_status: str | None = Query(default=None, max_length=40),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=30, ge=1, le=100),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ActivityResponse:
    try:
        return await ActivityReadService(session, settings).list_items(
            principal.user_id,
            tab=tab,
            monitor_id=monitor_id,
            symbol=symbol,
            sharia_status=sharia_status,
            opportunity_state=opportunity_state,
            methodology_id=methodology_id,
            delivery_status=delivery_status,
            date_from=date_from,
            date_to=date_to,
            page=page,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
