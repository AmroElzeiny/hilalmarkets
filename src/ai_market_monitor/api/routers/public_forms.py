from __future__ import annotations

import json
import re
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.request_guards import client_fingerprint
from ai_market_monitor.api.route_security import public_api
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.schemas.public_forms import (
    ContactSubmissionRequest,
    ContactSubmissionResponse,
    WaitlistSignupRequest,
    WaitlistSignupResponse,
)
from ai_market_monitor.services.public_forms import (
    PUBLIC_FORMS_CSRF_COOKIE,
    PublicFormConflict,
    PublicFormsService,
    issue_public_forms_csrf,
    public_forms_csrf_matches,
)
from ai_market_monitor.services.support_intake import (
    SupportIntakeGuard,
    support_intake_limits,
)

router = APIRouter(prefix="/public-forms", tags=["public-forms"])


def get_public_forms_sheet_transport() -> httpx.AsyncBaseTransport | None:
    return None


@router.get("/bootstrap")
@public_api("Issues an anonymous same-site CSRF boundary and public form endpoints.")
async def public_forms_bootstrap(
    response: Response,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    if not settings.public_forms_enabled:
        raise HTTPException(status_code=404, detail="Public forms are unavailable")
    nonce, token = issue_public_forms_csrf(settings)
    response.set_cookie(
        PUBLIC_FORMS_CSRF_COOKIE,
        nonce,
        max_age=60 * 60 * 8,
        httponly=True,
        secure=settings.is_deployed,
        samesite="lax",
        path="/",
    )
    limits = support_intake_limits(settings)
    return {
        "csrf_token": token,
        "waitlist_endpoint": "/api/v1/public-forms/waitlist",
        "contact_endpoint": "/api/v1/public-forms/contact",
        # The page states the limit before somebody writes a message, rather than after
        # they have written one and pressed Send. The numbers come from the server so the
        # page can never advertise a limit different from the one being enforced.
        "contact_limits": {
            "per_email": limits.per_email,
            "per_client": limits.per_client,
            "window_hours": round(limits.window_hours, 2),
        },
    }


@router.post("/waitlist", response_model=WaitlistSignupResponse)
@public_api("Creates one idempotent waitlist record and queues its server-only Sheet delivery.")
async def submit_waitlist(
    payload: WaitlistSignupRequest,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    sheet_transport: httpx.AsyncBaseTransport | None = Depends(
        get_public_forms_sheet_transport
    ),
) -> WaitlistSignupResponse:
    _require_public_form_request(request, settings, x_csrf_token)
    service = PublicFormsService(
        session,
        settings,
        sheet_transport=sheet_transport,
    )
    try:
        signup, created = await service.register_waitlist(
            payload,
            country_code=_country_code(request, settings),
            attribution_allowed=_analytics_consent(request, settings),
        )
    except PublicFormConflict as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "idempotency_conflict", "message": str(exc)},
        ) from exc
    await session.commit()
    if created and settings.waitlist_google_sheets_enabled:
        await service.process_waitlist_due(signup_id=signup.id, limit=1)
    delivery_status = await service.waitlist_delivery_status(signup.id)
    return WaitlistSignupResponse(
        status="created" if created else "already_registered",
        created=created,
        code="waitlist_created" if created else "duplicate_email",
        sheet_delivery_status=delivery_status,
        message=(
            "You are on the waitlist. We will contact you as access becomes available."
            if created
            else "That email is already on the waitlist."
        ),
    )


@router.post("/contact", response_model=ContactSubmissionResponse)
@public_api("Persists one idempotent contact message and delivers one office email.")
async def submit_contact(
    payload: ContactSubmissionRequest,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ContactSubmissionResponse:
    _require_public_form_request(request, settings, x_csrf_token)
    service = PublicFormsService(session, settings)
    guard = SupportIntakeGuard(session, settings)
    fingerprint = client_fingerprint(request, settings)

    # Asked before anything is written or sent. A refused message must cost nothing:
    # no row, no office email, no provider call.
    existing = await service.find_contact_by_idempotency_key(payload.idempotency_key)
    if existing is None:
        decision = await guard.check(
            email=str(payload.email),
            client_fingerprint=fingerprint,
        )
        if not decision.allowed:
            raise HTTPException(
                status_code=429,
                headers={"Retry-After": str(max(1, decision.retry_after_seconds))},
                detail={"code": decision.code, "message": decision.message()},
            )

    try:
        submission, created = await service.submit_contact(payload)
    except PublicFormConflict as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "idempotency_conflict", "message": str(exc)},
        ) from exc
    # A retry of the same message is the same message. Counting it again would spend a
    # person's allowance on a flaky network rather than on a second question.
    if created:
        await guard.record(
            door="contact",
            email=str(payload.email),
            client_fingerprint=fingerprint,
        )
    await session.commit()
    await service.process_contact_due(submission_id=submission.id, limit=1)
    if await service.contact_delivery_status(submission.id) != "sent":
        raise HTTPException(
            status_code=503,
            detail={
                "code": "contact_delivery_unavailable",
                "message": "We could not send your message just now. Please try again.",
            },
        )
    remaining = await guard.check(
        email=str(payload.email),
        client_fingerprint=fingerprint,
    )
    return ContactSubmissionResponse(
        status="sent",
        message="Your message was sent successfully.",
        # The stricter of the two personal allowances, because that is the one the
        # person will actually meet next.
        remaining_messages=min(
            remaining.remaining_for_email, remaining.remaining_for_client
        ),
        window_hours=round(guard.limits.window_hours, 2),
    )


def _require_public_form_request(
    request: Request,
    settings: Settings,
    supplied_csrf: str | None,
) -> None:
    if not settings.public_forms_enabled:
        raise HTTPException(status_code=404, detail="Public forms are unavailable")
    nonce = request.cookies.get(PUBLIC_FORMS_CSRF_COOKIE)
    if not public_forms_csrf_matches(settings, nonce, supplied_csrf):
        raise HTTPException(
            status_code=403,
            detail={"code": "csrf_rejected", "message": "Refresh the page and try again."},
        )
    supplied_origin = request.headers.get("origin")
    if not supplied_origin:
        if settings.is_deployed:
            raise HTTPException(
                status_code=403,
                detail={"code": "origin_required", "message": "Request origin is required."},
            )
        return
    allowed = {
        _origin(str(settings.public_base_url)),
        _origin(str(settings.app_base_url)) if settings.app_base_url else None,
        _origin(str(request.base_url)),
    }
    if _origin(supplied_origin) not in allowed:
        raise HTTPException(
            status_code=403,
            detail={"code": "origin_rejected", "message": "Request origin is not allowed."},
        )


def _analytics_consent(request: Request, settings: Settings) -> bool:
    raw = request.cookies.get("hm_cookie_consent")
    if not raw:
        return False
    try:
        parsed = json.loads(unquote(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(parsed, dict)
        and parsed.get("version") == settings.cookie_consent_version
        and parsed.get("analytics") is True
    )


def _country_code(request: Request, settings: Settings) -> str | None:
    if not settings.waitlist_trust_cloudflare_country_header:
        return None
    value = (request.headers.get("CF-IPCountry") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", value) or value in {"XX", "T1"}:
        return None
    return value


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
