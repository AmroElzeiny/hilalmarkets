from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import AdminPrincipal, get_admin_principal, require_admin
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.db.models import AuditEvent, Incident, SupportRequest
from ai_market_monitor.db.models.enums import IncidentSeverity
from ai_market_monitor.services.admin import AdminError
from ai_market_monitor.services.admin_dashboard import (
    AdminDashboardError,
    AdminDashboardService,
)
from ai_market_monitor.services.billing import BillingError
from ai_market_monitor.services.reliability import ReliabilityError
from ai_market_monitor.services.support import SupportError
from ai_market_monitor.services.trials import TrialError

router = APIRouter(prefix="/admin", tags=["admin"])


class ReasonRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class TrialExtensionRequest(BaseModel):
    days: int = Field(gt=0, le=90)
    reason: str = Field(min_length=3, max_length=1000)


class IncidentCreateRequest(BaseModel):
    title: str = Field(min_length=3, max_length=240)
    description: str = Field(min_length=3, max_length=4000)
    incident_type: str = Field(min_length=2, max_length=80)
    severity: IncidentSeverity
    affected_users: list[UUID] = Field(default_factory=list)
    affected_strategy_ids: list[UUID] = Field(default_factory=list)


class IncidentResolveRequest(BaseModel):
    resolution: str = Field(min_length=3, max_length=4000)


class SupportResolveRequest(BaseModel):
    resolution: str = Field(min_length=3, max_length=4000)


def dashboard(
    session: AsyncSession,
    settings: Settings,
) -> AdminDashboardService:
    return AdminDashboardService(session, settings)


@router.get("/overview")
async def overview(
    _: AdminPrincipal = Depends(get_admin_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    return await dashboard(session, settings).overview()


@router.get("/users")
async def users(
    q: str | None = Query(default=None, max_length=200),
    _: AdminPrincipal = Depends(get_admin_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    return {"users": await dashboard(session, settings).user_search(q)}


@router.get("/users/{user_id}")
async def user_detail(
    user_id: UUID,
    _: AdminPrincipal = Depends(get_admin_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        return await dashboard(session, settings).user_detail(user_id)
    except AdminDashboardError as exc:
        raise HTTPException(
            status_code=404, detail={"code": exc.code, "message": str(exc)}
        ) from exc


@router.get("/health")
async def health_dashboard(
    _: AdminPrincipal = Depends(get_admin_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    return await dashboard(session, settings).health_dashboard()


@router.get("/activity")
async def recent_activity(
    _: AdminPrincipal = Depends(get_admin_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    return await dashboard(session, settings).recent_activity()


@router.get("/incidents")
async def list_incidents(
    _: AdminPrincipal = Depends(get_admin_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    rows = (
        await session.scalars(select(Incident).order_by(Incident.detected_at.desc()).limit(100))
    ).all()
    return {
        "incidents": [
            {
                "id": row.id,
                "status": row.status,
                "severity": row.severity,
                "incident_type": row.incident_type,
                "title": row.title,
                "affected_users_count": row.affected_users_count,
                "affected_strategies_count": row.affected_strategies_count,
                "detected_at": row.detected_at,
                "resolved_at": row.resolved_at,
            }
            for row in rows
        ]
    }


@router.get("/support")
async def list_support_requests(
    _: AdminPrincipal = Depends(get_admin_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    rows = (
        await session.scalars(
            select(SupportRequest).order_by(SupportRequest.created_at.desc()).limit(100)
        )
    ).all()
    return {
        "support_requests": [
            {
                "id": row.id,
                "user_id": row.user_id,
                "category": row.category,
                "priority": row.priority,
                "status": row.status,
                "subject": row.subject,
                "created_at": row.created_at,
                "resolved_at": row.resolved_at,
            }
            for row in rows
        ]
    }


@router.get("/audit-events")
async def audit_events(
    _: AdminPrincipal = Depends(get_admin_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    rows = (
        await session.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(100))
    ).all()
    return {
        "audit_events": [
            {
                "id": row.id,
                "actor_user_id": row.actor_user_id,
                "actor_type": row.actor_type,
                "action": row.action,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "created_at": row.created_at,
                "metadata_redacted": row.metadata_redacted,
            }
            for row in rows
        ]
    }


@router.post("/strategies/{strategy_id}/pause")
async def pause_strategy(
    strategy_id: UUID,
    request: ReasonRequest,
    principal: AdminPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        strategy = await dashboard(session, settings).pause_strategy(
            strategy_id=strategy_id,
            admin_user_id=principal.user_id,
            reason=request.reason,
        )
        await session.commit()
        return {"strategy_id": strategy.id, "status": strategy.status}
    except AdminDashboardError as exc:
        raise HTTPException(
            status_code=404, detail={"code": exc.code, "message": str(exc)}
        ) from exc


@router.post("/strategies/{strategy_id}/resume")
async def resume_strategy(
    strategy_id: UUID,
    request: ReasonRequest,
    principal: AdminPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        strategy = await dashboard(session, settings).resume_strategy(
            strategy_id=strategy_id,
            admin_user_id=principal.user_id,
            reason=request.reason,
        )
        await session.commit()
        return {"strategy_id": strategy.id, "status": strategy.status}
    except AdminDashboardError as exc:
        raise HTTPException(
            status_code=404, detail={"code": exc.code, "message": str(exc)}
        ) from exc


@router.post("/users/{user_id}/trial-extension")
async def extend_trial(
    user_id: UUID,
    request: TrialExtensionRequest,
    principal: AdminPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        trial = await dashboard(session, settings).extend_trial(
            user_id=user_id,
            admin_user_id=principal.user_id,
            days=request.days,
            reason=request.reason,
        )
        await session.commit()
        return {"trial_id": trial.id, "status": trial.status, "ends_at": trial.ends_at}
    except (AdminError, TrialError) as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "message": str(exc)}
        ) from exc


@router.post("/incidents")
async def open_incident(
    request: IncidentCreateRequest,
    principal: AdminPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    incident = await dashboard(session, settings).open_incident(
        admin_user_id=principal.user_id,
        title=request.title,
        description=request.description,
        incident_type=request.incident_type,
        severity=request.severity,
        affected_users=request.affected_users,
        affected_strategy_ids=request.affected_strategy_ids,
    )
    await session.commit()
    return {"incident_id": incident.id, "status": incident.status}


@router.post("/incidents/{incident_id}/resolve")
async def resolve_incident(
    incident_id: UUID,
    request: IncidentResolveRequest,
    principal: AdminPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        incident = await dashboard(session, settings).resolve_incident(
            incident_id=incident_id,
            admin_user_id=principal.user_id,
            resolution=request.resolution,
        )
        await session.commit()
        return {"incident_id": incident.id, "status": incident.status}
    except ReliabilityError as exc:
        raise HTTPException(
            status_code=404, detail={"code": exc.code, "message": str(exc)}
        ) from exc


@router.post("/support/{support_request_id}/resolve")
async def resolve_support(
    support_request_id: UUID,
    request: SupportResolveRequest,
    principal: AdminPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        ticket = await dashboard(session, settings).resolve_support(
            support_request_id=support_request_id,
            admin_user_id=principal.user_id,
            resolution=request.resolution,
        )
        await session.commit()
        return {"support_request_id": ticket.id, "status": ticket.status}
    except SupportError as exc:
        raise HTTPException(
            status_code=404, detail={"code": exc.code, "message": str(exc)}
        ) from exc


@router.post("/billing-events/{provider_event_id}/reprocess")
async def reprocess_webhook(
    provider_event_id: str,
    _: AdminPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        result = await dashboard(session, settings).reprocess_webhook(
            provider_event_id=provider_event_id
        )
        await session.commit()
        return {
            "event_id": result.event_id,
            "event_type": result.event_type,
            "processing_status": result.processing_status,
            "replayed": result.replayed,
        }
    except BillingError as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "message": str(exc)}
        ) from exc
