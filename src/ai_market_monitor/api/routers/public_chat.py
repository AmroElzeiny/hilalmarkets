from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.route_security import public_api
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.schemas.public_chat import (
    PublicChatAnswerRequest,
    PublicChatAnswerResponse,
    PublicChatBootstrapResponse,
    PublicChatProfile,
    PublicInquiryRatingRequest,
    PublicInquiryRatingResponse,
    PublicInquiryRequest,
    PublicInquiryResponse,
)
from ai_market_monitor.services.agent_control import (
    AgentResponsesClient,
    OpenAIAgentResponsesClient,
)
from ai_market_monitor.services.public_chat import (
    PUBLIC_CHAT_CSRF_COOKIE,
    PUBLIC_CHAT_PROFILE_STORAGE_KEY,
    PublicChatService,
    PublicChatSessionLimitExceeded,
    issue_public_chat_csrf,
    mask_email,
    public_chat_csrf_matches,
)
from ai_market_monitor.services.web_auth import SESSION_COOKIE_NAME, WebAuthService

router = APIRouter(prefix="/public-chat", tags=["public-chat"])


def get_public_support_ai_client(
    settings: Settings = Depends(get_settings),
) -> AgentResponsesClient:
    return OpenAIAgentResponsesClient(settings)


@router.get("/bootstrap", response_model=PublicChatBootstrapResponse)
@public_api("Issues an anonymous, same-site CSRF boundary for the public product assistant.")
async def public_chat_bootstrap(
    response: Response,
    settings: Settings = Depends(get_settings),
) -> PublicChatBootstrapResponse:
    if not settings.public_chat_enabled:
        raise HTTPException(status_code=404, detail="Public assistant is unavailable")
    nonce, token = issue_public_chat_csrf(settings)
    response.set_cookie(
        PUBLIC_CHAT_CSRF_COOKIE,
        nonce,
        max_age=60 * 60 * 8,
        httponly=True,
        secure=settings.is_deployed,
        samesite="lax",
        path="/",
    )
    return PublicChatBootstrapResponse(
        csrf_token=token,
        profile_storage_key=PUBLIC_CHAT_PROFILE_STORAGE_KEY,
        profile_version=settings.public_chat_profile_version,
        consent_version=settings.cookie_consent_version,
        privacy_url="/privacy",
        max_message_length=settings.public_chat_message_max_length,
        max_inquiry_length=settings.public_chat_inquiry_max_length,
        conversation_retention_days=settings.public_chat_session_retention_days,
    )


@router.post("/profile", response_model=PublicChatProfile)
@public_api("Validates an optional public-chat profile without authenticating or persisting it.")
async def validate_public_chat_profile(
    payload: PublicChatProfile,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> PublicChatProfile:
    _require_public_chat_request(request, settings, x_csrf_token)
    return payload.model_copy(update={"email": str(payload.email).strip().casefold()})


@router.post("/answers", response_model=PublicChatAnswerResponse)
@public_api("Generates a validated AI answer from server-owned product sources and read tools.")
async def answer_public_chat_question(
    payload: PublicChatAnswerRequest,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    ai_client: AgentResponsesClient = Depends(get_public_support_ai_client),
) -> PublicChatAnswerResponse:
    _require_public_chat_request(request, settings, x_csrf_token)
    user = await WebAuthService(session, settings).current_user(
        request.cookies.get(SESSION_COOKIE_NAME)
    )
    if user is not None and user.status.value == "suspended":
        user = None
    try:
        result = await PublicChatService(
            session,
            settings,
            ai_client=ai_client,
        ).answer(payload, user_id=user.id if user is not None else None)
    except PublicChatSessionLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": "conversation_limit_reached", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "message_id_conflict", "message": str(exc)},
        ) from exc
    await session.commit()
    return result


@router.post("/inquiries", response_model=PublicInquiryResponse)
@public_api("Persists a rate-limited public inquiry and queues two idempotent emails.")
async def submit_public_chat_inquiry(
    payload: PublicInquiryRequest,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PublicInquiryResponse:
    _require_public_chat_request(request, settings, x_csrf_token)
    service = PublicChatService(session, settings)
    inquiry = await service.submit_inquiry(payload)
    await session.commit()
    return PublicInquiryResponse(
        reference=inquiry.reference,
        status="received",
        masked_email=mask_email(inquiry.normalized_email),
        feedback_token=service.feedback_token(inquiry),
        email_delivery_status="queued",
        message="Your message was sent successfully 🎉",
    )


@router.post("/ratings", response_model=PublicInquiryRatingResponse)
@public_api("Records at most one token-bound rating for a submitted public inquiry.")
async def rate_public_chat_inquiry(
    payload: PublicInquiryRatingRequest,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PublicInquiryRatingResponse:
    _require_public_chat_request(request, settings, x_csrf_token)
    try:
        await PublicChatService(session, settings).record_rating(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "inquiry_not_found", "message": str(exc)},
        ) from exc
    await session.commit()
    return PublicInquiryRatingResponse(
        status="recorded",
        message="Thank you. Your feedback was recorded.",
    )


@router.delete("/inquiries/{reference}")
@public_api("Allows the holder of an inquiry feedback token to request immediate redaction.")
async def delete_public_chat_inquiry(
    reference: str,
    request: Request,
    feedback_token: str = Header(alias="X-Public-Inquiry-Token"),
    x_csrf_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    from sqlalchemy import select

    from ai_market_monitor.db.models import PublicInquiry

    _require_public_chat_request(request, settings, x_csrf_token)
    inquiry = await session.scalar(
        select(PublicInquiry).where(PublicInquiry.reference == reference[:32])
    )
    service = PublicChatService(session, settings)
    if inquiry is None or not service.feedback_token_matches(inquiry, feedback_token):
        raise HTTPException(status_code=404, detail="Inquiry not found")
    await service.redact_inquiry(
        inquiry,
        reason="Inquiry content removed at the visitor's request.",
    )
    await session.commit()
    return {"status": "redacted"}


def _require_public_chat_request(
    request: Request,
    settings: Settings,
    supplied_csrf: str | None,
) -> None:
    if not settings.public_chat_enabled:
        raise HTTPException(status_code=404, detail="Public assistant is unavailable")
    nonce = request.cookies.get(PUBLIC_CHAT_CSRF_COOKIE)
    if not public_chat_csrf_matches(settings, nonce, supplied_csrf):
        raise HTTPException(
            status_code=403,
            detail={"code": "csrf_rejected", "message": "Refresh the assistant and try again."},
        )
    origin = request.headers.get("origin")
    if not origin:
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
    if _origin(origin) not in allowed:
        raise HTTPException(
            status_code=403,
            detail={"code": "origin_rejected", "message": "Request origin is not allowed."},
        )


def _origin(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
