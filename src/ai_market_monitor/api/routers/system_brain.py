import asyncio
import csv
import hashlib
import hmac
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import UserPrincipal
from ai_market_monitor.api.routers.dashboard_api import get_dashboard_principal
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.db.models import (
    AccountEmailDelivery,
    AgentRun,
    AgentToolCall,
    AuditEvent,
    ReviewCase,
    SystemBrainActionProposal,
    SystemBrainArtifact,
    SystemBrainMessage,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, UserRole
from ai_market_monitor.schemas.system_brain import (
    ActionConfirmationRequest,
    ActionProposalRead,
    AdminConversationSource,
    SystemBrainAgentTurnRequest,
    SystemBrainArtifactRead,
    SystemBrainAssistantRequest,
    SystemBrainConversationCreate,
    SystemBrainConversationUpdate,
    SystemBrainRunProgress,
)
from ai_market_monitor.services.account_admin import (
    AccountAdminError,
    SystemBrainUserAdminService,
)
from ai_market_monitor.services.account_emails import AccountEmailOutboxService
from ai_market_monitor.services.sharia_admin_dashboard import (
    ShariaAdminDashboardService,
)
from ai_market_monitor.services.sharia_governance import (
    ShariaGovernanceError,
    ShariaGovernanceService,
)
from ai_market_monitor.services.sharia_review_blockers import explain_error
from ai_market_monitor.services.sharia_screening import ShariaScreeningError
from ai_market_monitor.services.site_analytics import SiteAnalyticsService
from ai_market_monitor.services.system_brain_actions import (
    SystemBrainActionError,
    SystemBrainActionService,
)
from ai_market_monitor.services.system_brain_agent import (
    SystemBrainAgentService,
    SystemBrainAgentUnavailable,
    SystemBrainConversationService,
)
from ai_market_monitor.services.system_brain_bulk_review import (
    MAX_BATCH_SIZE,
    BulkReviewError,
    BulkReviewService,
    CaseOutcome,
)
from ai_market_monitor.services.system_brain_conversations import (
    AdminConversationExplorer,
    AdminConversationNotFound,
)
from ai_market_monitor.services.system_brain_repository_index import (
    RepositoryEvidenceIndexService,
)

PACKAGE_DIR = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))

#: How many cases the Cases page lists at once.
#:
#: This is also the real ceiling on one quick decision, because a reviewer can only tick
#: what the page shows. It is deliberately below the number of form fields one request
#: can carry — ``tests/unit/test_invariant_bulk_case_decision.py`` holds the two
#: together, so raising this cannot silently start returning bare HTTP 400s.
CASES_PAGE_LIMIT = 300

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
    allowed_emails = settings.system_brain_authorized_emails
    if (
        not access_assertion
        or not allowed_emails
        or not any(
            hmac.compare_digest(access_email.strip().casefold(), email) for email in allowed_emails
        )
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


def _request_ip_hash(request: Request, settings: Settings) -> str | None:
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    raw = (forwarded.split(",", 1)[0].strip() if forwarded else "") or (
        request.client.host if request.client else ""
    )
    if not raw:
        return None
    secret = settings.app_secret_key.get_secret_value().encode("utf-8")
    return hmac.new(secret, raw.encode("utf-8"), hashlib.sha256).hexdigest()


def _proposal_read(proposal: SystemBrainActionProposal) -> ActionProposalRead:
    return ActionProposalRead(
        proposal_id=proposal.id,
        action=proposal.action,
        target=f"{proposal.target_type}:{proposal.target_id}",
        exact_changes=proposal.exact_changes,
        reason=proposal.reason,
        evidence=proposal.evidence_refs,
        expected_effect=proposal.expected_effect,
        risks=proposal.risks,
        rollback_path=proposal.rollback_path,
        idempotency_key=proposal.idempotency_key,
        confirmation_token=proposal.confirmation_digest,
        status=proposal.status,
        expires_at=proposal.expires_at,
    )


def _apply_case_view(
    cases: list[dict],
    *,
    view: str | None,
    reviewer_id: UUID,
) -> list[dict]:
    if not view:
        return cases
    if view == "mine":
        return [item for item in cases if item["assigned_reviewer_id"] == reviewer_id]
    if view == "urgent":
        return [item for item in cases if item["priority"] == "urgent" or item["overdue"]]
    if view == "evidence_missing":
        return [
            item
            for item in cases
            if item["evidence_state"] in {"stale", "incomplete", "unavailable"}
        ]
    if view == "evidence_changed":
        return [
            item
            for item in cases
            if item["case_type"]
            in {"Material Source Change", "Evidence Refresh", "Methodology Change"}
        ]
    if view == "safety_hold":
        return [item for item in cases if item["state"] == "safety_hold"]
    if view == "failed_processing":
        return [
            item for item in cases if item["state"] in {"research_failed", "publication_failed"}
        ]
    if view == "unassigned":
        return [item for item in cases if item["assigned_reviewer_id"] is None]
    return cases


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
        identity.display_identifier or identity.normalized_identifier or "Application administrator"
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
        "admin_user_id": principal.user_id,
        "csrf_token": _csrf(settings, principal.user_id),
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    }


@router.get("/system-brain", response_class=HTMLResponse, include_in_schema=False)
@router.get(
    "/dashboard/system-brain",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def system_brain_home(
    request: Request,
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    context = await _base_context(request, session, settings, principal, section="overview")
    data = await ShariaAdminDashboardService(session).reviewer_overview()
    context["data"] = data
    context["assistant_enabled"] = settings.system_brain_ai_enabled
    # The badge used to spell out a model name by hand, so it kept saying "5.4 nano"
    # after the setting moved on. It now reports what the assistant will really call.
    context["assistant_model"] = settings.system_brain_ai_model
    context["assistant_reasoning_effort"] = settings.system_brain_ai_reasoning_effort
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
@router.get(
    "/dashboard/system-brain/cases",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def system_brain_reviews(
    request: Request,
    kind: str | None = None,
    state: str | None = None,
    priority: str | None = None,
    assignee: UUID | None = None,
    deadline: str | None = None,
    asset: str | None = None,
    view: str | None = None,
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
        "user_factual_report",
    }
    if kind is not None and kind not in allowed_kinds:
        raise HTTPException(status_code=404, detail="Review queue not found.")
    section = (
        "cases"
        if kind is None
        else (
            "initial-reviews"
            if kind == "initial_asset_review"
            else "user-reports"
            if kind == "user_factual_report"
            else "change-reviews"
        )
    )
    context = await _base_context(request, session, settings, principal, section=section)
    cases = await ShariaAdminDashboardService(session).list_cases(
        state=state,
        case_type=kind,
        priority=priority,
        assignee_id=assignee,
        deadline=deadline,
        asset_query=asset,
        limit=CASES_PAGE_LIMIT,
    )
    cases = _apply_case_view(cases, view=view, reviewer_id=principal.user_id)
    context.update(
        {
            "review_kind": kind or "",
            "review_state": state,
            "review_priority": priority,
            "review_assignee": assignee,
            "review_deadline": deadline,
            "review_asset": asset or "",
            "review_view": view or "",
            "cases": cases,
            # The ceiling comes from the service that enforces it, never from a number
            # typed into the page. The browser stops the reviewer at the same count the
            # endpoint would refuse, so a selection can no longer be built and lost.
            "bulk_max_batch_size": MAX_BATCH_SIZE,
            # The last quick decision this reviewer can still take back. Offered on the
            # page rather than hidden behind a menu: an undo nobody can find is not one.
            "undo_batch": await BulkReviewService(session, settings).latest_undoable_batch(
                principal.user_id
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
    "/system-brain/stats",
    response_class=HTMLResponse,
    include_in_schema=False,
)
@router.get(
    "/dashboard/system-brain/stats",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def system_brain_stats(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
    tag: str = Query(default="", max_length=40),
    scope: str = Query(default="landing", pattern="^(landing|site)$"),
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    """Who looked at the public site, for how long, and what they did next."""

    context = await _base_context(request, session, settings, principal, section="stats")
    service = SiteAnalyticsService(session, settings)
    # A browser that is force-quit never sends its closing beacon. Stamping those visits
    # closed here keeps the live list honest without changing any measured duration.
    await service.close_stale_visits()
    await session.commit()
    context["stats"] = await service.report(
        days=days,
        tag=tag or None,
        landing_only=scope == "landing",
    )
    context["stats_scope"] = scope
    context["recent_visits"] = await service.recent_visits()
    context["measurement_enabled"] = settings.site_visit_measurement_enabled
    return _protect(
        templates.TemplateResponse(
            request=request,
            name="system_brain.html",
            context=context,
        )
    )


@router.post(
    "/dashboard/system-brain/cases/bulk-decision",
    include_in_schema=False,
)
async def system_brain_bulk_case_decision(
    request: Request,
    action: str = Form(...),
    reason: str = Form(...),
    case_id: list[UUID] = Form(default=[]),
    csrf_token: str = Form(...),
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Record one decision on the selected cases, each through the normal review path."""

    _verify_csrf(settings, principal.user_id, csrf_token)
    try:
        outcome = await BulkReviewService(session, settings).apply(
            case_id,
            action=action,
            reason=reason,
            admin_user_id=principal.user_id,
        )
        await session.commit()
    except BulkReviewError as exc:
        await session.rollback()
        return _cases_redirect(request, error=str(exc))
    refused = _refused_summary(outcome.results)
    if not outcome.applied:
        return _cases_redirect(
            request,
            error=refused or "No case could be decided this way.",
        )
    return _cases_redirect(
        request,
        success=outcome.message() + (f" Not decided — {refused}" if refused else ""),
    )


@router.post(
    "/dashboard/system-brain/cases/bulk-decision/undo",
    include_in_schema=False,
)
async def system_brain_undo_bulk_case_decision(
    request: Request,
    batch_id: UUID = Form(...),
    reason: str = Form(default=""),
    csrf_token: str = Form(...),
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Put the cases from one quick decision back where they were."""

    _verify_csrf(settings, principal.user_id, csrf_token)
    try:
        outcome = await BulkReviewService(session, settings).undo(
            batch_id,
            admin_user_id=principal.user_id,
            reason=reason,
        )
        await session.commit()
    except BulkReviewError as exc:
        await session.rollback()
        return _cases_redirect(request, error=str(exc))
    refused = _refused_summary(outcome.results)
    if not outcome.applied:
        return _cases_redirect(request, error=refused or "Nothing could be put back.")
    return _cases_redirect(
        request,
        success=(
            f"{outcome.applied} case(s) put back. The earlier decision stays in the "
            "history." + (f" Not put back — {refused}" if refused else "")
        ),
    )


#: How many refused cases the message after a quick decision names one by one.
#:
#: The message travels back in the address bar. Naming 300 refusals there builds a web
#: address longer than servers and browsers accept, so the reviewer would get a broken
#: page instead of the outcome — the report of a problem becoming a second, worse
#: problem. Beyond this many, the rest are counted rather than listed.
CASES_NAMED_IN_MESSAGE = 5


def _refused_summary(results: list[CaseOutcome]) -> str:
    """The refused cases, named while that stays a readable, sendable message."""

    refused = [item for item in results if not item.applied]
    if not refused:
        return ""
    named = "; ".join(
        f"{item.reference}: {item.message}" for item in refused[:CASES_NAMED_IN_MESSAGE]
    )
    remaining = len(refused) - CASES_NAMED_IN_MESSAGE
    if remaining > 0:
        return f"{named}; and {remaining} more — open the list to see them."
    return named


def _cases_redirect(
    request: Request,
    *,
    success: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Back to the list the reviewer was looking at, filters and all."""

    filters = {
        key: value
        for key, value in request.query_params.items()
        if key in {"kind", "state", "priority", "assignee", "deadline", "asset", "view"}
    }
    filters.update({"success": success} if success else {"error": error or "Action failed."})
    return RedirectResponse(
        f"/dashboard/system-brain/cases?{urlencode(filters)}",
        status_code=303,
    )


@router.get(
    "/system-brain/reviews/{case_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
@router.get(
    "/dashboard/system-brain/cases/{case_id}",
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
    context = await _base_context(request, session, settings, principal, section="review-detail")
    try:
        context["detail"] = await ShariaAdminDashboardService(session).case_detail(case_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Say what stops this decision *before* the reviewer presses Approve, in the same
    # words the refusal would use. A button that always fails is the thing being fixed.
    case = await session.get(ReviewCase, case_id)
    blocker = (
        await ShariaGovernanceService(session, settings).review_blocker(case)
        if case is not None and case.state == "ready_for_review"
        else None
    )
    context["decision_blocker"] = (
        explain_error(blocker) if blocker is not None else None
    )
    return _protect(
        templates.TemplateResponse(
            request=request,
            name="system_brain.html",
            context=context,
        )
    )


@router.post(
    "/system-brain/sc-malaysia/import",
    include_in_schema=False,
)
@router.post(
    "/dashboard/system-brain/sc-malaysia/import",
    include_in_schema=False,
)
@router.post(
    "/system-brain/authority-sources/import",
    include_in_schema=False,
)
@router.post(
    "/dashboard/system-brain/authority-sources/import",
    include_in_schema=False,
)
async def system_brain_import_authority_sources(
    csrf_token: str = Form(...),
    principal: UserPrincipal = Depends(_require_application_admin),
    settings: Settings = Depends(get_settings),
):
    """Queue all bounded authority imports without publishing a conclusion."""
    _verify_csrf(settings, principal.user_id, csrf_token)
    try:
        from ai_market_monitor.worker import app as worker_app

        worker_app.send_task("ai_market_monitor.process_sharia_authority_imports")
    except Exception:
        query = urlencode(
            {
                "error": (
                    "The official-source import could not be queued. Check worker and Redis health."
                )
            }
        )
        return RedirectResponse(f"/dashboard/system-brain?{query}", status_code=303)
    query = urlencode(
        {
            "success": (
                "SC Malaysia and Fasset source imports were queued. New evidence will appear "
                "in the review Inbox; nothing becomes customer-visible until a reviewer "
                "approves it."
            )
        }
    )
    return RedirectResponse(f"/dashboard/system-brain?{query}", status_code=303)


@router.get(
    "/system-brain/users",
    response_class=HTMLResponse,
    include_in_schema=False,
)
@router.get(
    "/dashboard/system-brain/users",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def system_brain_users(
    request: Request,
    q: str | None = Query(default=None, max_length=160),
    status: str | None = Query(default=None, max_length=24),
    page: int = Query(default=1, ge=1),
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    context = await _base_context(
        request,
        session,
        settings,
        principal,
        section="users",
    )
    context.update(
        await SystemBrainUserAdminService(session, settings).list_users(
            query=q,
            status=status,
            page=page,
        )
    )
    return _protect(
        templates.TemplateResponse(
            request=request,
            name="system_brain.html",
            context=context,
        )
    )


@router.post(
    "/dashboard/system-brain/users/{user_id}/ban",
    include_in_schema=False,
)
async def system_brain_ban_user(
    user_id: UUID,
    reason: str = Form(...),
    idempotency_key: str = Form(...),
    csrf_token: str = Form(...),
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    _verify_csrf(settings, principal.user_id, csrf_token)
    try:
        result = await SystemBrainUserAdminService(session, settings).ban_user(
            actor_user_id=principal.user_id,
            target_user_id=user_id,
            reason=reason,
            idempotency_key=idempotency_key,
        )
        await session.commit()
    except AccountAdminError as exc:
        await session.rollback()
        return _user_admin_redirect(error=str(exc))
    return _user_admin_redirect(success=result.message)


@router.post(
    "/dashboard/system-brain/users/{user_id}/delete",
    include_in_schema=False,
)
async def system_brain_delete_user(
    user_id: UUID,
    reason: str = Form(...),
    idempotency_key: str = Form(...),
    csrf_token: str = Form(...),
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    _verify_csrf(settings, principal.user_id, csrf_token)
    try:
        result = await SystemBrainUserAdminService(session, settings).delete_profile(
            actor_user_id=principal.user_id,
            target_user_id=user_id,
            reason=reason,
            idempotency_key=idempotency_key,
        )
        await session.commit()
    except AccountAdminError as exc:
        await session.rollback()
        return _user_admin_redirect(error=str(exc))
    return _user_admin_redirect(success=result.message)


@router.post(
    "/dashboard/system-brain/users/{user_id}/access",
    include_in_schema=False,
)
async def system_brain_apply_user_access(
    user_id: UUID,
    tier: str = Form(...),
    months: str = Form(default=""),
    reason: str = Form(...),
    idempotency_key: str = Form(...),
    csrf_token: str = Form(...),
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    _verify_csrf(settings, principal.user_id, csrf_token)
    try:
        parsed_months = int(months) if months.strip() else None
    except ValueError:
        return _user_admin_redirect(
            error="Full access requires a whole number of months.",
        )
    try:
        result = await SystemBrainUserAdminService(session, settings).apply_access(
            actor_user_id=principal.user_id,
            target_user_id=user_id,
            tier=tier,
            months=parsed_months,
            reason=reason,
            idempotency_key=idempotency_key,
        )
        await session.commit()
    except AccountAdminError as exc:
        await session.rollback()
        return _user_admin_redirect(error=str(exc))
    delivery_status = None
    if result.email_delivery_id:
        try:
            await AccountEmailOutboxService(
                session,
                settings,
            ).process_due(delivery_id=result.email_delivery_id)
        except Exception:
            await session.rollback()
        delivery = await session.get(AccountEmailDelivery, result.email_delivery_id)
        delivery_status = delivery.status if delivery is not None else None
    message = result.message
    if result.email_delivery_id is None:
        message += " No verified email was available for the access notice."
    elif delivery_status == "sent":
        message += (
            " The access email was already delivered; no duplicate was sent."
            if result.repeated
            else " The user was notified by email."
        )
    elif delivery_status == "failed":
        message += " The access email failed permanently; inspect the email delivery record."
    else:
        message += " The access email is queued for retry."
    return _user_admin_redirect(success=message)


@router.get("/api/v1/system-brain/conversations", include_in_schema=False)
async def system_brain_agent_conversations(
    q: str | None = Query(default=None, max_length=160),
    include_archived: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
):
    data = await SystemBrainConversationService().list(
        session,
        admin_user_id=principal.user_id,
        query=q,
        include_archived=include_archived,
        limit=limit,
    )
    return _protect(JSONResponse({"items": data}))


@router.post("/api/v1/system-brain/conversations", include_in_schema=False)
async def system_brain_create_agent_conversation(
    payload: SystemBrainConversationCreate,
    request: Request,
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    _verify_csrf(settings, principal.user_id, request.headers.get("x-csrf-token", ""))
    conversation = await SystemBrainConversationService().create(
        session, admin_user_id=principal.user_id, title=payload.title
    )
    session.add(
        AuditEvent(
            actor_user_id=principal.user_id,
            actor_type="system_brain_admin",
            action="system_brain.agent_conversation.created",
            target_type="system_brain_conversation",
            target_id=str(conversation.id),
            request_id=request.headers.get("x-request-id"),
            ip_hash=_request_ip_hash(request, settings),
            metadata_redacted={"title_length": len(conversation.title)},
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()
    response = await SystemBrainConversationService().read(
        session, conversation.id, admin_user_id=principal.user_id
    )
    return _protect(JSONResponse(response.model_dump(mode="json"), status_code=201))


@router.get(
    "/api/v1/system-brain/conversations/{conversation_id}",
    include_in_schema=False,
)
async def system_brain_read_agent_conversation(
    conversation_id: UUID,
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        result = await SystemBrainConversationService().read(
            session, conversation_id, admin_user_id=principal.user_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _protect(JSONResponse(result.model_dump(mode="json")))


@router.patch(
    "/api/v1/system-brain/conversations/{conversation_id}",
    include_in_schema=False,
)
async def system_brain_update_agent_conversation(
    conversation_id: UUID,
    payload: SystemBrainConversationUpdate,
    request: Request,
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    _verify_csrf(settings, principal.user_id, request.headers.get("x-csrf-token", ""))
    try:
        await SystemBrainConversationService().update(
            session,
            conversation_id,
            admin_user_id=principal.user_id,
            title=payload.title,
            archived=payload.archived,
        )
        session.add(
            AuditEvent(
                actor_user_id=principal.user_id,
                actor_type="system_brain_admin",
                action="system_brain.agent_conversation.updated",
                target_type="system_brain_conversation",
                target_id=str(conversation_id),
                request_id=request.headers.get("x-request-id"),
                ip_hash=_request_ip_hash(request, settings),
                metadata_redacted={
                    "title_changed": payload.title is not None,
                    "archived": payload.archived,
                },
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
        result = await SystemBrainConversationService().read(
            session, conversation_id, admin_user_id=principal.user_id
        )
    except LookupError as exc:
        await session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _protect(JSONResponse(result.model_dump(mode="json")))


@router.post(
    "/api/v1/system-brain/conversations/{conversation_id}/turns",
    include_in_schema=False,
)
async def system_brain_agent_turn(
    conversation_id: UUID,
    payload: SystemBrainAgentTurnRequest,
    request: Request,
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    _verify_csrf(settings, principal.user_id, request.headers.get("x-csrf-token", ""))
    try:
        result = await SystemBrainAgentService(settings).run_turn(
            session,
            conversation_id,
            admin_user_id=principal.user_id,
            request=payload,
        )
    except LookupError as exc:
        await session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SystemBrainAgentUnavailable as exc:
        await session.rollback()
        return _protect(
            JSONResponse(
                status_code=503,
                content={"detail": str(exc), "retryable": True},
            )
        )
    return _protect(JSONResponse(result.model_dump(mode="json")))


@router.get(
    "/api/v1/system-brain/conversations/{conversation_id}/run-progress",
    include_in_schema=False,
)
async def system_brain_agent_run_progress(
    conversation_id: UUID,
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        await SystemBrainConversationService()._owned(session, conversation_id, principal.user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    source = await session.scalar(
        select(SystemBrainMessage)
        .where(
            SystemBrainMessage.conversation_id == conversation_id,
            SystemBrainMessage.role == "user",
            SystemBrainMessage.agent_run_id.is_not(None),
        )
        .order_by(SystemBrainMessage.sequence.desc())
        .limit(1)
    )
    if source is None or source.agent_run_id is None:
        result = SystemBrainRunProgress(status="idle")
    else:
        run = await session.get(AgentRun, source.agent_run_id)
        assistant = await session.scalar(
            select(SystemBrainMessage).where(
                SystemBrainMessage.conversation_id == conversation_id,
                SystemBrainMessage.agent_run_id == source.agent_run_id,
                SystemBrainMessage.role == "assistant",
            )
        )
        calls = list(
            (
                await session.scalars(
                    select(AgentToolCall)
                    .where(AgentToolCall.agent_run_id == source.agent_run_id)
                    .order_by(AgentToolCall.created_at.asc())
                    .limit(50)
                )
            ).all()
        )
        result = SystemBrainRunProgress(
            run_id=source.agent_run_id,
            status=run.status if run else "unavailable",
            step_count=run.step_count if run else 0,
            tool_call_count=run.tool_call_count if run else len(calls),
            tool_calls=[
                {
                    "tool_name": row.tool_name,
                    "status": row.result_status,
                    "evidence_refs": row.evidence_refs,
                    "duration_ms": row.duration_ms,
                }
                for row in calls
            ],
            assistant_message_id=assistant.id if assistant else None,
            answer=assistant.content if assistant else None,
        )
    return _protect(JSONResponse(result.model_dump(mode="json")))


@router.post(
    "/api/v1/system-brain/conversations/{conversation_id}/cancel-active-run",
    include_in_schema=False,
)
async def system_brain_cancel_agent_run(
    conversation_id: UUID,
    request: Request,
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    _verify_csrf(settings, principal.user_id, request.headers.get("x-csrf-token", ""))
    try:
        await SystemBrainConversationService()._owned(
            session, conversation_id, principal.user_id, lock=True
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    source = await session.scalar(
        select(SystemBrainMessage)
        .where(
            SystemBrainMessage.conversation_id == conversation_id,
            SystemBrainMessage.role == "user",
            SystemBrainMessage.agent_run_id.is_not(None),
        )
        .order_by(SystemBrainMessage.sequence.desc())
        .limit(1)
    )
    run = (
        await session.scalar(
            select(AgentRun).where(AgentRun.id == source.agent_run_id).with_for_update()
        )
        if source and source.agent_run_id
        else None
    )
    if run is not None and run.status == "running":
        run.status = "cancel_requested"
        session.add(
            AuditEvent(
                actor_user_id=principal.user_id,
                actor_type="system_brain_admin",
                action="system_brain.agent.cancel_requested",
                target_type="agent_run",
                target_id=str(run.id),
                request_id=request.headers.get("x-request-id"),
                ip_hash=_request_ip_hash(request, settings),
                metadata_redacted={"conversation_id": str(conversation_id)},
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
        status = "cancel_requested"
    else:
        status = run.status if run else "idle"
    return _protect(JSONResponse({"status": status}))


@router.get("/api/v1/system-brain/artifacts", include_in_schema=False)
async def system_brain_artifacts(
    conversation_id: UUID | None = None,
    include_archived: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
):
    statement = select(SystemBrainArtifact).where(
        SystemBrainArtifact.admin_user_id == principal.user_id
    )
    if conversation_id:
        statement = statement.where(SystemBrainArtifact.conversation_id == conversation_id)
    if not include_archived:
        statement = statement.where(SystemBrainArtifact.archived_at.is_(None))
    rows = list(
        (
            await session.scalars(
                statement.order_by(SystemBrainArtifact.created_at.desc()).limit(limit)
            )
        ).all()
    )
    items = []
    for row in rows:
        content = dict(row.content_redacted or {})
        items.append(
            SystemBrainArtifactRead(
                artifact_id=row.id,
                conversation_id=row.conversation_id,
                artifact_kind=row.artifact_kind,
                title=row.title,
                content=str(content.get("content") or content.get("csv") or ""),
                evidence_refs=row.evidence_refs,
                target_id=content.get("target_id"),
                created_at=row.created_at,
                updated_at=row.updated_at,
                archived=row.archived_at is not None,
                model_authored_draft=bool(content.get("model_authored_draft", True)),
            ).model_dump(mode="json")
        )
    return _protect(JSONResponse({"items": items}))


@router.get("/api/v1/system-brain/customer-conversations", include_in_schema=False)
async def system_brain_customer_conversations(
    request: Request,
    search: str | None = Query(default=None, max_length=500),
    source: AdminConversationSource | None = None,
    lifecycle: str | None = Query(default=None, max_length=80),
    error_only: bool = False,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    identity: Literal["authenticated", "anonymous"] | None = None,
    approval_state: str | None = Query(default=None, max_length=40),
    cursor: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=30, ge=1, le=100),
    seen_event_id: int = Query(default=0, ge=0),
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    result = await AdminConversationExplorer(session).list_conversations(
        search=search,
        source=source,
        lifecycle=lifecycle,
        error_only=error_only,
        date_from=date_from,
        date_to=date_to,
        identity=identity,
        approval_state=approval_state,
        cursor=cursor,
        limit=limit,
        seen_event_id=seen_event_id,
    )
    session.add(
        AuditEvent(
            actor_user_id=principal.user_id,
            actor_type="system_brain_admin",
            action="system_brain.customer_conversation.list",
            target_type="customer_conversation_page",
            target_id=None,
            request_id=request.headers.get("x-request-id"),
            ip_hash=_request_ip_hash(request, settings),
            metadata_redacted={
                "access_category": "conversation_explorer_summary",
                "conversation_ids": [str(item.conversation_id) for item in result.items],
                "source": source,
                "result_count": len(result.items),
            },
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return _protect(JSONResponse(result.model_dump(mode="json")))


@router.get(
    "/api/v1/system-brain/customer-conversations/{source}/{conversation_id}",
    include_in_schema=False,
)
async def system_brain_customer_conversation(
    source: AdminConversationSource,
    conversation_id: UUID,
    request: Request,
    access_reason: str = Query(min_length=3, max_length=120),
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    try:
        result = await AdminConversationExplorer(session).conversation(
            conversation_id,
            source=source,
            admin_user_id=principal.user_id,
            access_reason=access_reason,
            request_id=request.headers.get("x-request-id"),
            ip_hash=_request_ip_hash(request, settings),
        )
        await session.commit()
    except AdminConversationNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=404, detail="Conversation not found.") from exc
    return _protect(JSONResponse(result.model_dump(mode="json")))


@router.get("/api/v1/system-brain/customer-conversation-events", include_in_schema=False)
async def system_brain_customer_conversation_events(
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
):
    items = await AdminConversationExplorer(session).events(after_id=after_id, limit=limit)
    return _protect(JSONResponse({"items": items}))


@router.get("/api/v1/system-brain/customer-conversation-stream", include_in_schema=False)
async def system_brain_customer_conversation_stream(
    request: Request,
    after_id: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
):
    header_cursor = request.headers.get("last-event-id", "").strip()
    cursor = after_id
    if header_cursor.isdigit():
        cursor = max(cursor, int(header_cursor))

    async def generate():
        nonlocal cursor
        deadline = asyncio.get_running_loop().time() + 25.0
        yield "retry: 1500\n\n"
        while asyncio.get_running_loop().time() < deadline:
            if await request.is_disconnected():
                return
            session.expire_all()
            events = await AdminConversationExplorer(session).events(after_id=cursor, limit=100)
            if events:
                for event in events:
                    cursor = int(event["event_id"])
                    payload = json.dumps(event, separators=(",", ":"))
                    yield f"id: {cursor}\nevent: {event['event_type']}\ndata: {payload}\n\n"
            else:
                yield f": keepalive {cursor}\n\n"
            await asyncio.sleep(0.5)

    return _protect(
        StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )
    )


@router.get("/api/v1/system-brain/action-proposals", include_in_schema=False)
async def system_brain_action_proposals(
    conversation_id: UUID | None = None,
    status: str | None = Query(default=None, max_length=24),
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
):
    statement = select(SystemBrainActionProposal).where(
        SystemBrainActionProposal.admin_user_id == principal.user_id
    )

    if conversation_id:
        statement = statement.where(SystemBrainActionProposal.conversation_id == conversation_id)
    if status:
        statement = statement.where(SystemBrainActionProposal.status == status)
    proposals = list(
        (
            await session.scalars(
                statement.order_by(SystemBrainActionProposal.created_at.desc()).limit(100)
            )
        ).all()
    )
    return _protect(
        JSONResponse(
            {"items": [_proposal_read(item).model_dump(mode="json") for item in proposals]}
        )
    )


@router.post("/api/v1/system-brain/repository-index/refresh", include_in_schema=False)
async def system_brain_refresh_repository_index(
    request: Request,
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    """Explicit maintenance action; interactive agent tools never scan the filesystem."""
    _verify_csrf(settings, principal.user_id, request.headers.get("x-csrf-token", ""))
    result = await RepositoryEvidenceIndexService().refresh(session)
    session.add(
        AuditEvent(
            actor_user_id=principal.user_id,
            actor_type="system_brain_admin",
            action="system_brain.repository_index.refreshed",
            target_type="repository_evidence_index",
            target_id=None,
            request_id=request.headers.get("x-request-id"),
            ip_hash=_request_ip_hash(request, settings),
            metadata_redacted=result,
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return _protect(JSONResponse(result))


@router.post(
    "/api/v1/system-brain/action-proposals/{proposal_id}/confirm",
    include_in_schema=False,
)
async def system_brain_confirm_action_proposal(
    proposal_id: UUID,
    payload: ActionConfirmationRequest,
    request: Request,
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    _verify_csrf(settings, principal.user_id, request.headers.get("x-csrf-token", ""))
    try:
        result = await SystemBrainActionService(session, settings).confirm(
            proposal_id,
            admin_user_id=principal.user_id,
            confirmation_token=payload.confirmation_token,
            human_reason=payload.reason,
        )
        await session.commit()
    except SystemBrainActionError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail={"code": exc.code, "message": str(exc)}
        ) from exc
    return _protect(JSONResponse({"status": "executed", "result": result}))


@router.post(
    "/dashboard/system-brain/assistant",
    response_class=JSONResponse,
    include_in_schema=False,
)
async def system_brain_assistant(
    payload: SystemBrainAssistantRequest,
    request: Request,
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    """Compatibility adapter to the persistent bounded agent; no eager context path."""
    _verify_csrf(settings, principal.user_id, request.headers.get("x-csrf-token", ""))
    conversation = await SystemBrainConversationService().create(
        session,
        admin_user_id=principal.user_id,
        title="Legacy assistant request",
    )
    await session.commit()
    try:
        result = await SystemBrainAgentService(settings).run_turn(
            session,
            conversation.id,
            admin_user_id=principal.user_id,
            request=SystemBrainAgentTurnRequest(
                message=payload.message,
                client_message_id=f"legacy-{uuid4().hex}",
            ),
        )
    except SystemBrainAgentUnavailable as exc:
        await session.rollback()
        return _protect(
            JSONResponse(
                status_code=503,
                content={"detail": str(exc), "retryable": True},
            )
        )
    return _protect(JSONResponse(result.model_dump(mode="json")))


@router.get(
    "/dashboard/system-brain/{section_name}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def system_brain_workspace_section(
    request: Request,
    section_name: str,
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    if section_name not in {"operations", "governance", "audit-settings"}:
        raise HTTPException(status_code=404, detail="Admin section not found.")
    context = await _base_context(request, session, settings, principal, section=section_name)
    context.update(await ShariaAdminDashboardService(session).workspace_section(section_name))
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
    target = {
        "published-assets": "governance",
        "rejected-assets": "cases",
        "methodologies": "governance",
        "source-registry": "operations",
        "scraper-runs": "operations",
        "ai-assessments": "operations",
        "delivery-health": "operations",
        "audit-history": "audit-settings",
    }[section_name]
    context = await _base_context(request, session, settings, principal, section=target)
    service = ShariaAdminDashboardService(session)
    if target == "cases":
        context.update(
            {
                "review_kind": "",
                "review_state": "rejected",
                "review_priority": None,
                "review_assignee": None,
                "review_deadline": None,
                "review_asset": "",
                "review_view": "",
                "cases": await service.list_cases(state="rejected"),
            }
        )
    else:
        context.update(await service.workspace_section(target))
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
@router.post(
    "/dashboard/system-brain/cases/{case_id}/decision",
    include_in_schema=False,
)
async def system_brain_review_decision(
    case_id: UUID,
    action: str = Form(...),
    reason: str = Form(...),
    requested_evidence: str = Form(default=""),
    qualifications: str = Form(default=""),
    acknowledged_gaps: str = Form(default=""),
    rights_clearance_reference: str = Form(default=""),
    criterion_key: list[str] = Form(default=[]),
    criterion_label: list[str] = Form(default=[]),
    criterion_outcome: list[str] = Form(default=[]),
    criterion_reason: list[str] = Form(default=[]),
    use_key: list[str] = Form(default=[]),
    use_decision: list[str] = Form(default=[]),
    use_reason: list[str] = Form(default=[]),
    use_scope: list[str] = Form(default=[]),
    csrf_token: str = Form(...),
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    _verify_csrf(settings, principal.user_id, csrf_token)
    service = ShariaGovernanceService(session, settings)
    try:
        criteria = [
            {
                "key": key,
                "outcome": outcome,
                "reviewer_explanation": explanation.strip(),
            }
            for key, _label, outcome, explanation in zip(
                criterion_key,
                criterion_label,
                criterion_outcome,
                criterion_reason,
                strict=False,
            )
            if key and outcome
        ]
        use_cases = [
            {
                "key": key,
                "decision": decision,
                "reason": use_reason_text.strip(),
                "scope": scope.strip() or None,
            }
            for key, decision, use_reason_text, scope in zip(
                use_key,
                use_decision,
                use_reason,
                use_scope,
                strict=False,
            )
            if key and decision
        ]
        gap_rows = [line.strip() for line in acknowledged_gaps.splitlines() if line.strip()]
        # What one Approve press did, in the reviewer's words. Set only by the two
        # approve-and-publish actions; every other action keeps the generic wording.
        outcome_message: str | None = None
        if action == "approve":
            outcome = await service.approve_and_publish(
                case_id,
                admin_user_id=principal.user_id,
                reason=reason,
                criterion_decisions=criteria,
                use_case_decisions=use_cases,
                acknowledged_gaps=gap_rows,
            )
            outcome_message = (
                "Approved and published. Customers can see this Passport now."
                if outcome.published
                else outcome.publication_pending_reason
            )
        elif action == "approve_with_qualification":
            qualification_rows = [
                line.strip() for line in qualifications.splitlines() if line.strip()
            ]
            outcome = await service.approve_and_publish(
                case_id,
                admin_user_id=principal.user_id,
                reason=reason,
                with_qualifications=True,
                qualifications=qualification_rows,
                criterion_decisions=criteria,
                use_case_decisions=use_cases,
                acknowledged_gaps=gap_rows,
            )
            outcome_message = (
                "Approved with a note, and published. Customers can see this Passport now."
                if outcome.published
                else outcome.publication_pending_reason
            )
        elif action == "approve_internal_only":
            qualification_rows = [
                line.strip() for line in qualifications.splitlines() if line.strip()
            ]
            await service.approve_internally(
                case_id,
                admin_user_id=principal.user_id,
                reason=reason,
                qualifications=qualification_rows,
                criterion_decisions=criteria,
                use_case_decisions=use_cases,
                acknowledged_gaps=gap_rows,
            )
        elif action == "record_rights_clearance":
            await service.record_rights_clearance(
                case_id,
                admin_user_id=principal.user_id,
                clearance_reference=rights_clearance_reference or reason,
            )
        elif action == "publish":
            await service.publish_approved(
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
            evidence = [line.strip() for line in requested_evidence.splitlines() if line.strip()]
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
        elif action == "reopen":
            await service.reopen_case(
                case_id,
                admin_user_id=principal.user_id,
                reason=reason,
            )
        elif action == "place_safety_hold":
            await service.place_safety_hold(
                case_id,
                admin_user_id=principal.user_id,
                reason=reason,
            )
        elif action == "request_safety_hold_removal":
            await service.request_safety_hold_removal(
                case_id,
                admin_user_id=principal.user_id,
                reason=reason,
            )
        elif action == "dismiss_false_positive":
            await service.dismiss_false_positive(
                case_id,
                admin_user_id=principal.user_id,
                reason=reason,
            )
        elif action in {"start_research", "retry_research"}:
            await service.start_research(
                case_id,
                admin_user_id=principal.user_id,
                reason=reason,
            )
            try:
                from ai_market_monitor.worker import app as worker_app

                worker_app.send_task("ai_market_monitor.process_sharia_authority_imports")
            except Exception as exc:
                raise ShariaGovernanceError(
                    "research_queue_unavailable",
                    "Research was not queued. Check worker and Redis health, then retry.",
                ) from exc
        elif action == "mark_ready_for_review":
            await service.mark_ready_for_review(
                case_id,
                admin_user_id=principal.user_id,
                reason=reason,
            )
        else:
            raise HTTPException(status_code=400, detail="Unknown review action.")
        await session.commit()
    except (ShariaGovernanceError, ShariaScreeningError) as exc:
        await session.rollback()
        # The same plain wording the Cases page uses for this refusal, so a reviewer
        # never meets two different explanations of one rule. ``ShariaScreeningError``
        # is caught alongside it because it can surface while a Passport is being built,
        # and a reviewer met that one as a blank server error page.
        query = urlencode({"error": explain_error(exc).sentence()[:500]})
        return RedirectResponse(
            f"/dashboard/system-brain/cases/{case_id}?{query}",
            status_code=303,
        )
    query = urlencode(
        {
            "success": outcome_message
            or f"{action.replace('_', ' ').title()} was recorded and audited."
        }
    )
    return RedirectResponse(
        f"/dashboard/system-brain/cases/{case_id}?{query}",
        status_code=303,
    )


def _user_admin_redirect(
    *,
    success: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    query = urlencode({"success": success} if success else {"error": error or "Action failed."})
    return RedirectResponse(
        f"/dashboard/system-brain/users?{query}",
        status_code=303,
    )


@router.post(
    "/dashboard/system-brain/cases/{case_id}/ai-field-review",
    include_in_schema=False,
)
async def system_brain_ai_field_review(
    case_id: UUID,
    field_key: str = Form(...),
    disposition: str = Form(...),
    reviewer_value: str = Form(default=""),
    reason: str = Form(...),
    csrf_token: str = Form(...),
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    _verify_csrf(settings, principal.user_id, csrf_token)
    try:
        detail = await ShariaAdminDashboardService(session).case_detail(case_id)
        field = next(
            (item for item in detail["review_fields"] if item["key"] == field_key),
            None,
        )
        if field is None:
            raise ShariaGovernanceError(
                "review_field_not_found",
                "The requested review field is not part of this evidence package.",
            )
        suggestion = field.get("ai_suggestion")
        await ShariaGovernanceService(session, settings).record_ai_field_review(
            case_id,
            admin_user_id=principal.user_id,
            field_key=field_key,
            disposition=disposition,
            reviewer_value=reviewer_value,
            original_ai_suggestion=json.dumps(
                suggestion,
                sort_keys=True,
                default=str,
            )
            if suggestion is not None
            else "",
            reason=reason,
            source_references=list(field.get("source_refs") or []),
        )
        await session.commit()
    except (LookupError, ShariaGovernanceError) as exc:
        await session.rollback()
        return RedirectResponse(
            f"/dashboard/system-brain/cases/{case_id}?{urlencode({'error': str(exc)[:500]})}",
            status_code=303,
        )
    return RedirectResponse(
        "/dashboard/system-brain/cases/"
        f"{case_id}?{urlencode({'success': 'The field review was recorded and audited.'})}",
        status_code=303,
    )


@router.post(
    "/system-brain/reviews/{case_id}/assignment",
    include_in_schema=False,
)
@router.post(
    "/dashboard/system-brain/cases/{case_id}/assignment",
    include_in_schema=False,
)
async def system_brain_review_assignment(
    case_id: UUID,
    assigned_reviewer_id: str = Form(default=""),
    priority: str = Form(default="normal"),
    reason: str = Form(...),
    csrf_token: str = Form(...),
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    _verify_csrf(settings, principal.user_id, csrf_token)
    try:
        reviewer_id = UUID(assigned_reviewer_id) if assigned_reviewer_id else None
        await ShariaGovernanceService(session, settings).assign_case(
            case_id,
            admin_user_id=principal.user_id,
            assigned_reviewer_id=reviewer_id,
            reason=reason,
            priority=priority,
        )
        await session.commit()
    except (ValueError, ShariaGovernanceError) as exc:
        await session.rollback()
        query = urlencode({"error": str(exc)[:500]})
        return RedirectResponse(f"/dashboard/system-brain/cases/{case_id}?{query}", status_code=303)
    query = urlencode({"success": "The assignment and due date were recorded."})
    return RedirectResponse(
        f"/dashboard/system-brain/cases/{case_id}?{query}",
        status_code=303,
    )


@router.post(
    "/system-brain/notifications/{attempt_id}/retry",
    include_in_schema=False,
)
@router.post(
    "/dashboard/system-brain/notifications/{attempt_id}/retry",
    include_in_schema=False,
)
async def system_brain_retry_notification(
    attempt_id: UUID,
    reason: str = Form(...),
    csrf_token: str = Form(...),
    principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    _verify_csrf(settings, principal.user_id, csrf_token)
    try:
        await ShariaGovernanceService(session, settings).retry_notification(
            attempt_id,
            admin_user_id=principal.user_id,
            reason=reason,
        )
        await session.commit()
    except ShariaGovernanceError as exc:
        await session.rollback()
        return RedirectResponse(
            f"/dashboard/system-brain/operations?{urlencode({'error': str(exc)[:500]})}",
            status_code=303,
        )
    return RedirectResponse(
        "/dashboard/system-brain/operations?success=Delivery+retry+was+queued+and+audited.",
        status_code=303,
    )


@router.get("/system-brain/audit-export", include_in_schema=False)
@router.get("/dashboard/system-brain/audit-export", include_in_schema=False)
async def system_brain_audit_export(
    output: str = Query(default="csv", pattern="^(csv|json)$"),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    actor: UUID | None = None,
    asset: str | None = None,
    methodology: str | None = None,
    action: str | None = None,
    _principal: UserPrincipal = Depends(_require_application_admin),
    session: AsyncSession = Depends(get_db_session),
):
    statement = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(10_000)
    if date_from:
        statement = statement.where(AuditEvent.created_at >= date_from)
    if date_to:
        statement = statement.where(AuditEvent.created_at <= date_to)
    if actor:
        statement = statement.where(AuditEvent.actor_user_id == actor)
    if action:
        statement = statement.where(AuditEvent.action.contains(action))
    rows = list((await session.scalars(statement)).all())
    asset_filter = (asset or "").strip().casefold()
    methodology_filter = (methodology or "").strip().casefold()
    if asset_filter or methodology_filter:
        rows = [
            row
            for row in rows
            if (
                not asset_filter
                or asset_filter in json.dumps(row.metadata_redacted or {}).casefold()
            )
            and (
                not methodology_filter
                or methodology_filter in json.dumps(row.metadata_redacted or {}).casefold()
            )
        ]
    payload = [
        {
            "occurred_at": row.created_at.isoformat(),
            "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
            "actor_type": row.actor_type,
            "action": row.action,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "metadata": row.metadata_redacted or {},
        }
        for row in rows
    ]
    headers = {"Content-Disposition": f'attachment; filename="sharia-audit.{output}"'}
    if output == "json":
        return JSONResponse(payload, headers=headers)
    stream = io.StringIO()
    writer = csv.DictWriter(
        stream,
        fieldnames=[
            "occurred_at",
            "actor_user_id",
            "actor_type",
            "action",
            "target_type",
            "target_id",
            "metadata",
        ],
    )
    writer.writeheader()
    for row in payload:
        writer.writerow({**row, "metadata": json.dumps(row["metadata"], sort_keys=True)})
    return StreamingResponse(iter([stream.getvalue()]), media_type="text/csv", headers=headers)


@router.post("/system-brain/logout", include_in_schema=False)
@router.post("/dashboard/system-brain/logout", include_in_schema=False)
async def system_brain_logout() -> RedirectResponse:
    return RedirectResponse("/dashboard/logout", status_code=303)
