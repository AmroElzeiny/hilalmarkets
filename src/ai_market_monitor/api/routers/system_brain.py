import hashlib
import hmac
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import UserPrincipal
from ai_market_monitor.api.routers.dashboard_api import get_dashboard_principal
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.db.models import UserIdentity
from ai_market_monitor.db.models.enums import IdentityProvider, UserRole
from ai_market_monitor.services.sharia_admin_dashboard import (
    ShariaAdminDashboardService,
)
from ai_market_monitor.services.sharia_governance import (
    ShariaGovernanceError,
    ShariaGovernanceService,
)

PACKAGE_DIR = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))

SECTIONS = {
    "published-assets",
    "rejected-assets",
    "methodologies",
    "source-registry",
    "scraper-runs",
    "ai-assessments",
    "delivery-health",
    "audit-history",
}


async def _require_cloudflare_access(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.system_brain_cloudflare_access_required:
        return
    access_email = request.headers.get("cf-access-authenticated-user-email", "")
    access_assertion = request.headers.get("cf-access-jwt-assertion", "")
    expected_email = settings.system_brain_username or ""
    if (
        not access_assertion
        or not expected_email
        or not hmac.compare_digest(access_email.strip().casefold(), expected_email)
    ):
        raise HTTPException(status_code=403, detail="Cloudflare Access is required.")


async def _require_application_admin(
    principal: UserPrincipal = Depends(get_dashboard_principal),
) -> UserPrincipal:
    if principal.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Administrator role required.")
    return principal


router = APIRouter(
    tags=["system-brain"],
    dependencies=[
        Depends(_require_cloudflare_access),
        Depends(_require_application_admin),
    ],
)


def _protect(response):
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def _csrf(settings: Settings, user_id: UUID) -> str:
    secret = settings.app_secret_key.get_secret_value().encode("utf-8")
    return hmac.new(secret, f"sharia-admin:{user_id}".encode(), hashlib.sha256).hexdigest()


def _verify_csrf(settings: Settings, user_id: UUID, supplied: str) -> None:
    if not hmac.compare_digest(_csrf(settings, user_id), supplied):
        raise HTTPException(status_code=403, detail="Invalid form token.")


async def _admin_email(session: AsyncSession, user_id: UUID) -> str:
    identity = await session.scalar(
        select(UserIdentity)
        .where(
            UserIdentity.user_id == user_id,
            UserIdentity.provider == IdentityProvider.EMAIL,
        )
        .order_by(UserIdentity.is_primary.desc(), UserIdentity.created_at.asc())
        .limit(1)
    )
    return (
        identity.display_identifier
        or identity.normalized_identifier
        or "Application administrator"
        if identity
        else "Application administrator"
    )


async def _base_context(
    request: Request,
    session: AsyncSession,
    settings: Settings,
    principal: UserPrincipal,
    *,
    section: str,
) -> dict:
    return {
        "request": request,
        "section": section,
        "admin_email": await _admin_email(session, principal.user_id),
        "csrf_token": _csrf(settings, principal.user_id),
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    }


@router.get("/system-brain", response_class=HTMLResponse, include_in_schema=False)
async def system_brain_home(
    request: Request,
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    context = await _base_context(
        request, session, settings, principal, section="overview"
    )
    context["data"] = await ShariaAdminDashboardService(session).overview()
    return _protect(
        templates.TemplateResponse(
            request=request,
            name="system_brain.html",
            context=context,
        )
    )


@router.get(
    "/system-brain/reviews",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def system_brain_reviews(
    request: Request,
    kind: str = "initial_asset_review",
    state: str | None = None,
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    allowed_kinds = {
        "initial_asset_review",
        "material_source_change",
        "evidence_refresh",
        "source_identity_conflict",
        "methodology_change",
    }
    if kind not in allowed_kinds:
        raise HTTPException(status_code=404, detail="Review queue not found.")
    section = "initial-reviews" if kind == "initial_asset_review" else "change-reviews"
    context = await _base_context(request, session, settings, principal, section=section)
    context.update(
        {
            "review_kind": kind,
            "review_state": state,
            "cases": await ShariaAdminDashboardService(session).list_cases(
                state=state,
                case_type=kind,
            ),
        }
    )
    return _protect(
        templates.TemplateResponse(
            request=request,
            name="system_brain.html",
            context=context,
        )
    )


@router.get(
    "/system-brain/reviews/{case_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def system_brain_review_detail(
    request: Request,
    case_id: UUID,
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    context = await _base_context(
        request, session, settings, principal, section="review-detail"
    )
    try:
        context["detail"] = await ShariaAdminDashboardService(session).case_detail(case_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _protect(
        templates.TemplateResponse(
            request=request,
            name="system_brain.html",
            context=context,
        )
    )


@router.get(
    "/system-brain/{section_name}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def system_brain_section(
    request: Request,
    section_name: str,
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    if section_name not in SECTIONS:
        raise HTTPException(status_code=404, detail="Admin section not found.")
    context = await _base_context(
        request, session, settings, principal, section=section_name
    )
    context.update(await ShariaAdminDashboardService(session).section(section_name))
    return _protect(
        templates.TemplateResponse(
            request=request,
            name="system_brain.html",
            context=context,
        )
    )


@router.post(
    "/system-brain/reviews/{case_id}/decision",
    include_in_schema=False,
)
async def system_brain_review_decision(
    case_id: UUID,
    action: str = Form(...),
    reason: str = Form(...),
    requested_evidence: str = Form(default=""),
    csrf_token: str = Form(...),
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    _verify_csrf(settings, principal.user_id, csrf_token)
    service = ShariaGovernanceService(session, settings)
    try:
        if action == "approve_and_publish":
            await service.approve_and_publish(
                case_id,
                admin_user_id=principal.user_id,
                reason=reason,
            )
        elif action == "reject_and_store":
            await service.reject_and_store(
                case_id,
                admin_user_id=principal.user_id,
                reason=reason,
            )
        elif action == "request_more_evidence":
            evidence = [
                line.strip()
                for line in requested_evidence.splitlines()
                if line.strip()
            ]
            await service.request_more_evidence(
                case_id,
                admin_user_id=principal.user_id,
                reason=reason,
                requested_evidence=evidence,
            )
        elif action == "return_to_research":
            await service.return_to_research(
                case_id,
                admin_user_id=principal.user_id,
                reason=reason,
            )
        elif action == "add_admin_note":
            await service.add_admin_note(
                case_id,
                admin_user_id=principal.user_id,
                note=reason,
            )
        else:
            raise HTTPException(status_code=400, detail="Unknown review action.")
        await session.commit()
    except ShariaGovernanceError as exc:
        await session.rollback()
        query = urlencode({"error": str(exc)[:500]})
        return RedirectResponse(
            f"/system-brain/reviews/{case_id}?{query}",
            status_code=303,
        )
    query = urlencode({"success": "The decision was recorded and audited."})
    return RedirectResponse(
        f"/system-brain/reviews/{case_id}?{query}",
        status_code=303,
    )


@router.post("/system-brain/logout", include_in_schema=False)
async def system_brain_logout() -> RedirectResponse:
    return RedirectResponse("/dashboard/logout", status_code=303)
