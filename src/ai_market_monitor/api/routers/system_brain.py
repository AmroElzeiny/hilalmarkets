from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.services.email_delivery import EmailDeliveryError
from ai_market_monitor.services.system_brain import (
    SYSTEM_BRAIN_PENDING_COOKIE,
    SYSTEM_BRAIN_SESSION_COOKIE,
    CapabilityCoverageService,
    SystemBrainAccessError,
    SystemBrainAuthService,
)

PACKAGE_DIR = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
router = APIRouter(tags=["system-brain"])


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    return forwarded or (request.client.host if request.client else None)


def _secure(settings: Settings) -> bool:
    # Local/test servers commonly advertise a public HTTPS URL while still being opened over
    # localhost HTTP. Deployment mode is the reliable boundary for Secure cookies.
    return settings.is_deployed


def _protect(response):
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def _auth_page(request: Request, *, step: str, error: str | None = None, status_code: int = 200):
    return _protect(
        templates.TemplateResponse(
            request=request,
            name="system_brain_auth.html",
            context={"step": step, "error": error},
            status_code=status_code,
        )
    )


@router.get("/system-brain", response_class=HTMLResponse, include_in_schema=False)
async def system_brain_home(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    auth = SystemBrainAuthService(settings)
    principal = await auth.current_session(
        session, request.cookies.get(SYSTEM_BRAIN_SESSION_COOKIE)
    )
    if principal is None:
        return _auth_page(request, step="login")
    data = await CapabilityCoverageService(settings).overview(session)
    response = templates.TemplateResponse(
        request=request,
        name="system_brain.html",
        context={
            "data": data,
            "admin_email": principal.email,
            "csrf_token": auth._private_hash(f"csrf:{principal.id}"),
        },
    )
    return _protect(response)


@router.post("/system-brain/login", include_in_schema=False)
async def system_brain_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    auth = SystemBrainAuthService(settings)
    try:
        pending = await auth.begin_login(
            session,
            username=username,
            password=password,
            remote_ip=_client_ip(request),
        )
    except (SystemBrainAccessError, EmailDeliveryError) as exc:
        message = str(exc)
        status_code = getattr(exc, "status_code", 503)
        return _auth_page(request, step="login", error=message, status_code=status_code)
    response = RedirectResponse("/system-brain/verify", status_code=303)
    response.set_cookie(
        SYSTEM_BRAIN_PENDING_COOKIE,
        pending,
        max_age=settings.system_brain_otp_ttl_minutes * 60,
        httponly=True,
        secure=_secure(settings),
        samesite="strict",
        path="/system-brain",
    )
    return _protect(response)


@router.get("/system-brain/verify", response_class=HTMLResponse, include_in_schema=False)
async def system_brain_verify_page(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    if not request.cookies.get(SYSTEM_BRAIN_PENDING_COOKIE):
        return RedirectResponse("/system-brain", status_code=303)
    return _auth_page(request, step="verify")


@router.post("/system-brain/verify", include_in_schema=False)
async def system_brain_verify(
    request: Request,
    code: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    auth = SystemBrainAuthService(settings)
    pending = request.cookies.get(SYSTEM_BRAIN_PENDING_COOKIE, "")
    try:
        cookie = await auth.verify_otp(
            session,
            pending_cookie=pending,
            code=code,
            remote_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except SystemBrainAccessError as exc:
        return _auth_page(
            request,
            step="verify",
            error=str(exc),
            status_code=exc.status_code,
        )
    response = RedirectResponse("/system-brain", status_code=303)
    response.delete_cookie(SYSTEM_BRAIN_PENDING_COOKIE, path="/system-brain")
    response.set_cookie(
        SYSTEM_BRAIN_SESSION_COOKIE,
        cookie,
        max_age=settings.system_brain_session_hours * 60 * 60,
        httponly=True,
        secure=_secure(settings),
        samesite="strict",
        path="/system-brain",
    )
    return _protect(response)


@router.post("/system-brain/logout", include_in_schema=False)
async def system_brain_logout(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    await SystemBrainAuthService(settings).logout(
        session, request.cookies.get(SYSTEM_BRAIN_SESSION_COOKIE)
    )
    response = RedirectResponse("/system-brain", status_code=303)
    response.delete_cookie(SYSTEM_BRAIN_SESSION_COOKIE, path="/system-brain")
    return _protect(response)


@router.post("/system-brain/aliases/{proposal_id}/{action}", include_in_schema=False)
async def system_brain_review_alias(
    request: Request,
    proposal_id: UUID,
    action: str,
    csrf_token: str = Form(...),
    review_note: str = Form(default=""),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    auth = SystemBrainAuthService(settings)
    principal = await auth.current_session(
        session, request.cookies.get(SYSTEM_BRAIN_SESSION_COOKIE)
    )
    if principal is None:
        return RedirectResponse("/system-brain", status_code=303)
    expected = auth._private_hash(f"csrf:{principal.id}")
    if not __import__("hmac").compare_digest(csrf_token, expected):
        return _auth_page(request, step="login", error="Invalid form token.", status_code=403)
    await CapabilityCoverageService(settings).review_alias(
        session,
        proposal_id=proposal_id,
        action=action,
        note=review_note,
        admin_session=principal,
    )
    return RedirectResponse("/system-brain#alias-proposals", status_code=303)
