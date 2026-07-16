from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.core.plans import PLAN_DEFINITIONS
from ai_market_monitor.db.models import BillingEvent, UserIdentity
from ai_market_monitor.db.models.enums import IdentityProvider
from ai_market_monitor.services.admin_notifications import AdminNotificationService
from ai_market_monitor.services.billing import BillingError, BillingService
from ai_market_monitor.services.entitlements import (
    EntitlementError,
    EntitlementService,
    PlanCatalogService,
    UsageService,
)
from ai_market_monitor.services.payment_emails import PaymentEmailOutboxService

router = APIRouter(prefix="/billing", tags=["billing"])

PAYMENT_SUCCESS_EVENT_TYPES = {
    "checkout.session.completed",
    "invoice.payment_succeeded",
    "payment.finished",
}


class CheckoutRequest(BaseModel):
    user_id: UUID
    plan_code: str = Field(min_length=1, max_length=50)
    success_url: str
    cancel_url: str


class PortalRequest(BaseModel):
    user_id: UUID
    return_url: str


@router.get("/plans")
async def list_plans(session: AsyncSession = Depends(get_db_session)) -> dict:
    await PlanCatalogService(session).sync_defaults()
    await session.commit()
    return {
        "plans": [
            {
                "code": plan.code,
                "name": plan.name,
                "monthly_price": str(plan.monthly_price),
                "currency": plan.currency,
                "description": plan.description,
                "limits": plan.limits,
                "features": plan.features,
            }
            for plan in PLAN_DEFINITIONS.values()
        ]
    }


@router.get("/users/{user_id}/entitlement")
async def get_entitlement(
    user_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    context = await EntitlementService(session).current(user_id)
    return {
        "plan": context.plan.code,
        "plan_name": context.plan.name,
        "source": context.source,
        "ends_at": context.ends_at,
        "limits": context.plan.limits,
        "features": context.plan.features,
    }


@router.get("/users/{user_id}/usage")
async def get_usage(
    user_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    now = datetime.now(UTC)
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    summary = await UsageService(session).summary(
        user_id, period_start, period_start + timedelta(days=32)
    )
    return {"period_start": period_start, "usage": summary}


@router.post("/checkout")
async def create_checkout(
    request: CheckoutRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        result = await BillingService(session, settings).checkout_session(
            user_id=request.user_id,
            plan_code=request.plan_code,
            success_url=request.success_url,
            cancel_url=request.cancel_url,
        )
        await session.commit()
        return {
            "provider": result.provider,
            "checkout_url": result.checkout_url,
            "provider_session_id": result.provider_session_id,
        }
    except EntitlementError as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "message": str(exc)}
        ) from exc


@router.post("/portal")
async def create_portal(
    request: PortalRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    result = await BillingService(session, settings).billing_portal(
        user_id=request.user_id, return_url=request.return_url
    )
    return {"provider": result.provider, "portal_url": result.portal_url}


@router.post("/webhooks/{provider}")
async def receive_billing_webhook(
    provider: str,
    request: Request,
    x_billing_signature: str | None = Header(default=None),
    x_nowpayments_sig: str | None = Header(default=None, alias="x-nowpayments-sig"),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    body = await request.body()
    try:
        result = await BillingService(session, settings).process_verified_webhook(
            provider=provider,
            body=body,
            signature=x_nowpayments_sig if provider == "nowpayments" else x_billing_signature,
        )
        await session.commit()
        if not result.replayed and result.processing_status == "processed":
            await PaymentEmailOutboxService(session, settings).process_due(limit=5)
        await _notify_admin_payment_received(
            session=session,
            settings=settings,
            provider=provider,
            event_id=result.event_id,
            event_type=result.event_type,
            user_id=result.user_id,
            replayed=result.replayed,
            processing_status=result.processing_status,
        )
        return {
            "event_id": result.event_id,
            "event_type": result.event_type,
            "processing_status": result.processing_status,
            "replayed": result.replayed,
            "user_id": result.user_id,
        }
    except BillingError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "message": str(exc)}
        ) from exc


async def _notify_admin_payment_received(
    *,
    session: AsyncSession,
    settings: Settings,
    provider: str,
    event_id: str,
    event_type: str,
    user_id,
    replayed: bool,
    processing_status: str,
) -> None:
    if (
        replayed
        or processing_status != "processed"
        or event_type not in PAYMENT_SUCCESS_EVENT_TYPES
        or user_id is None
    ):
        return
    event = await session.scalar(
        select(BillingEvent).where(BillingEvent.provider_event_id == event_id)
    )
    data = dict((event.payload_redacted if event else {}).get("data") or {})
    identity = await session.scalar(
        select(UserIdentity).where(
            UserIdentity.user_id == user_id,
            UserIdentity.provider == IdentityProvider.EMAIL,
            UserIdentity.is_primary.is_(True),
        )
    )
    await AdminNotificationService(settings).send_payment_received(
        user_id=user_id,
        email=identity.normalized_identifier if identity else None,
        plan_code=data.get("plan_code"),
        provider=provider,
        event_type=event_type,
    )
