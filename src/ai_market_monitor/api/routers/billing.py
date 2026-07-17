from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import UserPrincipal, get_user_principal
from ai_market_monitor.api.route_security import public_api, signed_webhook
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.core.plans import PLAN_DEFINITIONS, visible_public_plan_codes
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
    model_config = {"extra": "forbid"}

    plan_code: str = Field(min_length=1, max_length=50)


class PortalRequest(BaseModel):
    model_config = {"extra": "forbid"}


@router.get("/plans")
@public_api("Publishes only the explicitly allowlisted customer pricing catalog.")
async def list_plans(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    await PlanCatalogService(session).sync_defaults()
    await session.commit()
    billing = BillingService(session, settings)
    capabilities = billing.provider_capabilities
    visible_codes = visible_public_plan_codes(billing_enabled=settings.billing_enabled)
    return {
        "billing_enabled": settings.billing_enabled,
        "provider": billing.provider.provider_name,
        "provider_capabilities": asdict(capabilities),
        "billing_mode": (
            "disabled_private_beta"
            if not settings.billing_enabled
            else (
                "monthly_auto_renewal"
                if capabilities.supports_recurring_billing
                else "manual_30_day_access"
            )
        ),
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
            for code in visible_codes
            for plan in [PLAN_DEFINITIONS[code]]
        ]
    }


@router.get("/users/{user_id}/entitlement")
async def get_entitlement(
    user_id: UUID,
    principal: UserPrincipal = Depends(get_user_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    if user_id != principal.user_id:
        raise HTTPException(status_code=403, detail="Entitlement access denied")
    context = await EntitlementService(session).current(principal.user_id)
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
    principal: UserPrincipal = Depends(get_user_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    if user_id != principal.user_id:
        raise HTTPException(status_code=403, detail="Usage access denied")
    now = datetime.now(UTC)
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    summary = await UsageService(session).summary(
        principal.user_id, period_start, period_start + timedelta(days=32)
    )
    return {"period_start": period_start, "usage": summary}


@router.post("/checkout")
async def create_checkout(
    request: CheckoutRequest,
    principal: UserPrincipal = Depends(get_user_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not settings.billing_enabled:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "billing_disabled",
                "message": "Paid checkout is disabled during the private beta.",
            },
        )
    base_url = str(settings.app_base_url or settings.public_base_url).rstrip("/")
    try:
        result = await BillingService(session, settings).checkout_session(
            user_id=principal.user_id,
            plan_code=request.plan_code,
            success_url=f"{base_url}/billing/success",
            cancel_url=f"{base_url}/billing/cancel",
        )
        await session.commit()
        return {
            "provider": result.provider,
            "checkout_url": result.checkout_url,
            "provider_session_id": result.provider_session_id,
        }
    except (BillingError, EntitlementError) as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "message": str(exc)}
        ) from exc


@router.post("/portal")
async def create_portal(
    _request: PortalRequest,
    principal: UserPrincipal = Depends(get_user_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not settings.billing_enabled:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "billing_disabled",
                "message": "The billing portal is unavailable during the private beta.",
            },
        )
    base_url = str(settings.app_base_url or settings.public_base_url).rstrip("/")
    try:
        result = await BillingService(session, settings).billing_portal(
            user_id=principal.user_id,
            return_url=f"{base_url}/dashboard/billing",
        )
        return {"provider": result.provider, "portal_url": result.portal_url}
    except BillingError as exc:
        raise HTTPException(
            status_code=409, detail={"code": exc.code, "message": str(exc)}
        ) from exc


@router.post("/webhooks/{provider}")
@signed_webhook("Authenticates billing events with the configured provider signature.")
async def receive_billing_webhook(
    provider: str,
    request: Request,
    x_billing_signature: str | None = Header(default=None),
    x_nowpayments_sig: str | None = Header(default=None, alias="x-nowpayments-sig"),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not settings.billing_enabled:
        raise HTTPException(status_code=404, detail="Not found")
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
