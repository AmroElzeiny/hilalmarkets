from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import UserPrincipal
from ai_market_monitor.api.route_security import signed_webhook
from ai_market_monitor.api.routers.dashboard_api import get_dashboard_principal
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.csrf import csrf_token_matches
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.db.models import (
    IntegrationHealth,
    IntegrationTestResult,
    WhatsAppConnection,
    WhatsAppWebhookReceipt,
)
from ai_market_monitor.db.models.enums import DeliveryChannel, HealthStatus
from ai_market_monitor.services.entitlements import EntitlementService
from ai_market_monitor.whatsapp.adapter import WhatsAppCloudAdapter, WhatsAppDeliveryError
from ai_market_monitor.whatsapp.security import (
    payload_digest,
    verify_webhook_signature,
    verify_webhook_token,
)
from ai_market_monitor.whatsapp.service import (
    WhatsAppAccountService,
    WhatsAppIntegrationTestService,
    WhatsAppServiceError,
    connection_payload,
)
from ai_market_monitor.whatsapp.types import WhatsAppLinkRequest, WhatsAppPreferencesUpdate
from ai_market_monitor.whatsapp.webhook import (
    WhatsAppWebhookPayloadError,
    canonical_event_payload,
    extract_whatsapp_events,
)

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])
logger = logging.getLogger(__name__)


class WhatsAppAdapterFactory(Protocol):
    def __call__(self, settings: Settings) -> WhatsAppCloudAdapter: ...


class WhatsAppReceiptEnqueuer(Protocol):
    def __call__(self, receipt_id: str) -> None: ...


def get_whatsapp_adapter_factory() -> WhatsAppAdapterFactory:
    return WhatsAppCloudAdapter


def get_whatsapp_receipt_enqueuer() -> WhatsAppReceiptEnqueuer:
    def enqueue(receipt_id: str) -> None:
        try:
            from ai_market_monitor.worker import app as worker_app

            worker_app.send_task(
                "ai_market_monitor.process_whatsapp_webhook_event", args=[receipt_id]
            )
        except Exception:
            logger.exception(
                "WhatsApp receipt %s remains pending for scheduled processing", receipt_id
            )

    return enqueue


@router.get("/webhook", response_class=PlainTextResponse)
@signed_webhook("Verifies Meta WhatsApp webhook ownership with the configured verify token.")
async def verify_whatsapp_webhook(
    mode: str | None = Query(default=None, alias="hub.mode"),
    verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
    settings: Settings = Depends(get_settings),
) -> PlainTextResponse:
    if not settings.whatsapp_enabled:
        raise HTTPException(status_code=503, detail="WhatsApp is disabled")
    expected = settings.whatsapp_verify_token
    if (
        mode != "subscribe"
        or challenge is None
        or expected is None
        or not verify_webhook_token(
            supplied=verify_token, expected=expected.get_secret_value()
        )
    ):
        raise HTTPException(status_code=403, detail="WhatsApp webhook verification failed")
    return PlainTextResponse(challenge, status_code=200)


@router.post("/webhook")
@signed_webhook("Authenticates Meta WhatsApp events with X-Hub-Signature-256.")
async def receive_whatsapp_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    enqueue: WhatsAppReceiptEnqueuer = Depends(get_whatsapp_receipt_enqueuer),
) -> dict[str, Any]:
    if not settings.whatsapp_enabled:
        raise HTTPException(status_code=503, detail="WhatsApp is disabled")
    app_secret = settings.whatsapp_app_secret
    if app_secret is None:
        raise HTTPException(status_code=503, detail="WhatsApp webhook is not configured")
    raw_body = await request.body()
    if not verify_webhook_signature(
        raw_body=raw_body,
        signature_header=x_hub_signature_256,
        app_secret=app_secret.get_secret_value(),
    ):
        raise HTTPException(status_code=401, detail="Invalid WhatsApp webhook signature")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid WhatsApp webhook JSON") from exc
    if not settings.whatsapp_business_account_id or not settings.whatsapp_phone_number_id:
        raise HTTPException(status_code=503, detail="WhatsApp webhook is not configured")
    try:
        events = extract_whatsapp_events(
            payload,
            expected_waba_id=settings.whatsapp_business_account_id,
            expected_phone_number_id=settings.whatsapp_phone_number_id,
        )
    except WhatsAppWebhookPayloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    now = datetime.now(UTC)
    receipt_ids: list[str] = []
    duplicates = 0
    for event in events:
        exists = await session.scalar(
            select(WhatsAppWebhookReceipt.id).where(
                WhatsAppWebhookReceipt.event_key == event.event_key
            )
        )
        if exists is not None:
            duplicates += 1
            continue
        receipt = WhatsAppWebhookReceipt(
            event_key=event.event_key,
            event_type=event.event_type,
            provider_message_id=event.provider_message_id,
            provider_status=event.provider_status,
            payload_hash=payload_digest(canonical_event_payload(event.payload)),
            payload_redacted=event.payload,
            processing_status="pending",
            attempt_count=0,
            response_payload={},
            received_at=now,
            event_at=event.event_at,
            retain_until=now
            + timedelta(days=settings.whatsapp_webhook_receipt_retention_days),
        )
        try:
            async with session.begin_nested():
                session.add(receipt)
                await session.flush()
        except IntegrityError:
            duplicates += 1
            continue
        receipt_ids.append(str(receipt.id))
    await _record_webhook_health(session, now)
    await session.commit()
    for receipt_id in receipt_ids:
        enqueue(receipt_id)
    return {
        "ok": True,
        "accepted": len(receipt_ids),
        "duplicates": duplicates,
        "payload_hash": hashlib.sha256(raw_body).hexdigest()[:16],
    }


@router.get("/status")
async def whatsapp_status(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    connection = await session.scalar(
        select(WhatsAppConnection).where(
            WhatsAppConnection.user_id == principal.user_id
        )
    )
    latest_test = await session.scalar(
        select(IntegrationTestResult)
        .where(
            IntegrationTestResult.user_id == principal.user_id,
            IntegrationTestResult.integration == DeliveryChannel.WHATSAPP.value,
        )
        .order_by(IntegrationTestResult.created_at.desc())
        .limit(1)
    )
    return {
        "enabled": settings.whatsapp_enabled,
        "configured": _configured(settings),
        "connection": connection_payload(connection),
        "latest_test": _test_payload(latest_test),
    }


@router.post("/link", status_code=status.HTTP_201_CREATED)
async def create_whatsapp_link(
    payload: WhatsAppLinkRequest,
    x_csrf_token: str | None = Header(default=None),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _require_csrf(settings, principal.user_id, x_csrf_token)
    await _require_whatsapp_plan(session, principal.user_id)
    try:
        result = await WhatsAppAccountService(session, settings).create_link(
            user_id=principal.user_id, request=payload
        )
        await session.commit()
    except WhatsAppServiceError as exc:
        await session.rollback()
        raise _service_error(exc) from exc
    return {
        "link_url": result.url,
        "expires_at": result.expires_at,
        "phone": result.masked_phone,
        "categories": result.categories,
    }


@router.post("/test")
async def test_whatsapp_connection(
    x_csrf_token: str | None = Header(default=None),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    adapter_factory: WhatsAppAdapterFactory = Depends(get_whatsapp_adapter_factory),
) -> dict[str, Any]:
    _require_csrf(settings, principal.user_id, x_csrf_token)
    await _require_whatsapp_plan(session, principal.user_id)
    try:
        result = await WhatsAppIntegrationTestService(
            session, settings, adapter_factory(settings)
        ).send(principal.user_id)
        await session.commit()
    except (WhatsAppServiceError, WhatsAppDeliveryError) as exc:
        await session.commit()
        code = getattr(exc, "code", "whatsapp_test_failed")
        raise HTTPException(
            status_code=422,
            detail={"code": code, "message": str(exc)},
        ) from exc
    return {"ok": True, "test": _test_payload(result)}


@router.post("/pause")
async def pause_whatsapp(
    x_csrf_token: str | None = Header(default=None),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return await _account_mutation(
        "pause", principal, x_csrf_token, session, settings
    )


@router.post("/resume")
async def resume_whatsapp(
    x_csrf_token: str | None = Header(default=None),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    await _require_whatsapp_plan(session, principal.user_id)
    return await _account_mutation(
        "resume", principal, x_csrf_token, session, settings
    )


@router.post("/clear-error")
async def clear_whatsapp_error(
    x_csrf_token: str | None = Header(default=None),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return await _account_mutation(
        "clear_error", principal, x_csrf_token, session, settings
    )


@router.patch("/preferences")
async def update_whatsapp_preferences(
    payload: WhatsAppPreferencesUpdate,
    x_csrf_token: str | None = Header(default=None),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _require_csrf(settings, principal.user_id, x_csrf_token)
    await _require_whatsapp_plan(session, principal.user_id)
    try:
        connection = await WhatsAppAccountService(
            session, settings
        ).update_preferences(principal.user_id, payload)
        await session.commit()
    except WhatsAppServiceError as exc:
        await session.rollback()
        raise _service_error(exc) from exc
    return {"ok": True, "connection": connection_payload(connection)}


@router.delete("/connection")
async def disconnect_whatsapp(
    x_csrf_token: str | None = Header(default=None),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _require_csrf(settings, principal.user_id, x_csrf_token)
    try:
        connection = await WhatsAppAccountService(session, settings).disconnect(
            principal.user_id
        )
        await session.commit()
    except WhatsAppServiceError as exc:
        await session.rollback()
        raise _service_error(exc) from exc
    return {"ok": True, "connection": connection_payload(connection)}


async def _account_mutation(
    operation: str,
    principal: UserPrincipal,
    csrf_value: str | None,
    session: AsyncSession,
    settings: Settings,
) -> dict[str, Any]:
    _require_csrf(settings, principal.user_id, csrf_value)
    service = WhatsAppAccountService(session, settings)
    try:
        method = getattr(service, operation)
        connection = await method(principal.user_id)
        await session.commit()
    except WhatsAppServiceError as exc:
        await session.rollback()
        raise _service_error(exc) from exc
    return {"ok": True, "connection": connection_payload(connection)}


def _require_csrf(settings: Settings, user_id: Any, supplied: str | None) -> None:
    if not csrf_token_matches(settings, user_id, supplied):
        raise HTTPException(status_code=403, detail="Invalid form token.")


async def _require_whatsapp_plan(session: AsyncSession, user_id: Any) -> None:
    entitlement = await EntitlementService(session).current(user_id)
    if not entitlement.feature_enabled("whatsapp"):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "whatsapp_plan_required",
                "message": "WhatsApp is not included in the current plan.",
            },
        )


def _service_error(exc: WhatsAppServiceError) -> HTTPException:
    status_code = 409 if "assigned" in exc.code or "used" in exc.code else 422
    if exc.code in {"whatsapp_disabled", "whatsapp_not_configured"}:
        status_code = 503
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _configured(settings: Settings) -> bool:
    return bool(
        settings.whatsapp_adapter == "http"
        and settings.whatsapp_access_token is not None
        and settings.whatsapp_app_secret is not None
        and settings.whatsapp_verify_token is not None
        and settings.whatsapp_phone_number_id
        and settings.whatsapp_business_account_id
        and settings.whatsapp_business_phone_e164
        and settings.whatsapp_graph_api_version
    )


def _test_payload(result: IntegrationTestResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "id": result.id,
        "status": result.status,
        "destination": result.destination,
        "error_code": result.error_code,
        "created_at": result.created_at,
    }


async def _record_webhook_health(session: AsyncSession, now: datetime) -> None:
    health = await session.scalar(
        select(IntegrationHealth).where(
            IntegrationHealth.integration == DeliveryChannel.WHATSAPP.value,
            IntegrationHealth.scope_key == "webhook",
        )
    )
    if health is None:
        health = IntegrationHealth(
            integration=DeliveryChannel.WHATSAPP.value,
            scope_key="webhook",
            status=HealthStatus.HEALTHY,
            consecutive_failures=0,
            checked_at=now,
        )
        session.add(health)
    health.status = HealthStatus.HEALTHY
    health.consecutive_failures = 0
    health.last_success_at = now
    health.last_error_code = None
    health.checked_at = now
