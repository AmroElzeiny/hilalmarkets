from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.db.models import Incident, IntegrationHealth, MarketDataHealth
from ai_market_monitor.db.models.enums import IncidentStatus
from ai_market_monitor.services.reliability import ReliabilityService

router = APIRouter(prefix="/status", tags=["status"])


@router.get("/summary")
async def status_summary(session: AsyncSession = Depends(get_db_session)) -> dict:
    summary = await ReliabilityService(session).status_summary()
    return {
        "overall_status": summary.overall_status,
        "market_data": summary.market_data,
        "integrations": summary.integrations,
        "open_incidents": summary.open_incidents,
        "failed_jobs": summary.failed_jobs,
        "generated_at": summary.generated_at,
    }


@router.get("/market-data")
async def market_data_status(session: AsyncSession = Depends(get_db_session)) -> dict:
    rows = (
        await session.scalars(
            select(MarketDataHealth).order_by(MarketDataHealth.checked_at.desc()).limit(100)
        )
    ).all()
    return {
        "markets": [
            {
                "provider": row.provider,
                "exchange": row.exchange,
                "symbol": row.symbol,
                "timeframe": row.timeframe,
                "status": row.status,
                "latest_candle_at": row.last_candle_at,
                "retrieved_at": row.retrieved_at,
                "data_age_seconds": row.data_age_seconds,
                "candle_count": row.candle_count,
                "missing_candle_count": row.missing_candle_count,
                "source_status": row.source_status,
            }
            for row in rows
        ]
    }


@router.get("/integrations")
async def integration_status(session: AsyncSession = Depends(get_db_session)) -> dict:
    rows = (
        await session.scalars(
            select(IntegrationHealth).order_by(IntegrationHealth.checked_at.desc()).limit(100)
        )
    ).all()
    return {
        "integrations": [
            {
                "integration": row.integration,
                "scope_key": row.scope_key,
                "status": row.status,
                "consecutive_failures": row.consecutive_failures,
                "latency_ms": row.latency_ms,
                "last_error_code": row.last_error_code,
                "checked_at": row.checked_at,
            }
            for row in rows
        ]
    }


@router.get("/incidents")
async def incidents(session: AsyncSession = Depends(get_db_session)) -> dict:
    rows = (
        await session.scalars(
            select(Incident)
            .where(Incident.status != IncidentStatus.CANCELED)
            .order_by(Incident.detected_at.desc())
            .limit(50)
        )
    ).all()
    return {
        "incidents": [
            {
                "id": row.id,
                "status": row.status,
                "severity": row.severity,
                "incident_type": row.incident_type,
                "title": row.title,
                "description": row.description,
                "detected_at": row.detected_at,
                "resolved_at": row.resolved_at,
                "material_user_impact": row.material_user_impact,
            }
            for row in rows
        ]
    }
