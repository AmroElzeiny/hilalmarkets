import csv
import hashlib
import hmac
import io
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import UserPrincipal
from ai_market_monitor.api.routers.dashboard_api import get_dashboard_principal
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.db.models import AccountEmailDelivery, AuditEvent, UserIdentity
from ai_market_monitor.db.models.enums import IdentityProvider, UserRole
from ai_market_monitor.schemas.system_brain import SystemBrainAssistantRequest
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
from ai_market_monitor.services.system_brain_assistant import (
    SystemBrainAssistantService,
    SystemBrainAssistantUnavailable,
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
        return [
            item
            for item in cases
            if item["priority"] == "urgent" or item["overdue"]
        ]
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
            item
            for item in cases
            if item["state"] in {"research_failed", "publication_failed"}
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
    context = await _base_context(
        request, session, settings, principal, section="overview"
    )
    data = await ShariaAdminDashboardService(session).reviewer_overview()
    context["data"] = data
    context["assistant_enabled"] = settings.system_brain_ai_enabled
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
    section = "cases" if kind is None else (
        "initial-reviews"
        if kind == "initial_asset_review"
        else "user-reports"
        if kind == "user_factual_report"
        else "change-reviews"
    )
    context = await _base_context(request, session, settings, principal, section=section)
    cases = await ShariaAdminDashboardService(session).list_cases(
        state=state,
        case_type=kind,
        priority=priority,
        assignee_id=assignee,
        deadline=deadline,
        asset_query=asset,
        limit=300,
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
                    "The official-source import could not be queued. "
                    "Check worker and Redis health."
                )
            }
        )
        return RedirectResponse(f"/dashboard/system-brain?{query}", status_code=303)
    query = urlencode(
        {
            "success": (
                "SC Malaysia and Fasset source imports were queued. New evidence will appear "
                "in the review Inbox; nothing becomes customer-visible until a reviewer "
                "approves it and a publisher completes publication."
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
    context = await _base_context(
        request, session, settings, principal, section=section_name
    )
    context.update(
        await ShariaAdminDashboardService(session).workspace_section(section_name)
    )
    return _protect(
        templates.TemplateResponse(
            request=request,
            name="system_brain.html",
            context=context,
        )
    )


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
    _verify_csrf(
        settings,
        principal.user_id,
        request.headers.get("x-csrf-token", ""),
    )
    try:
        response = await SystemBrainAssistantService(settings).answer(
            session,
            admin_user_id=principal.user_id,
            request=payload,
        )
        await session.commit()
    except SystemBrainAssistantUnavailable as exc:
        await session.rollback()
        return _protect(
            JSONResponse(
                status_code=503,
                content={
                    "detail": str(exc),
                    "retryable": True,
                },
            )
        )
    return _protect(JSONResponse(response.model_dump(mode="json")))


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
    context = await _base_context(
        request, session, settings, principal, section=target
    )
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
        if action == "approve":
            await service.approve_for_publication(
                case_id,
                admin_user_id=principal.user_id,
                reason=reason,
                criterion_decisions=criteria,
                use_case_decisions=use_cases,
                acknowledged_gaps=gap_rows,
            )
        elif action == "approve_with_qualification":
            qualification_rows = [
                line.strip() for line in qualifications.splitlines() if line.strip()
            ]
            await service.approve_for_publication(
                case_id,
                admin_user_id=principal.user_id,
                reason=reason,
                with_qualifications=True,
                qualifications=qualification_rows,
                criterion_decisions=criteria,
                use_case_decisions=use_cases,
                acknowledged_gaps=gap_rows,
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

                worker_app.send_task(
                    "ai_market_monitor.process_sharia_authority_imports"
                )
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
    except ShariaGovernanceError as exc:
        await session.rollback()
        query = urlencode({"error": str(exc)[:500]})
        return RedirectResponse(
            f"/dashboard/system-brain/cases/{case_id}?{query}",
            status_code=303,
        )
    query = urlencode(
        {"success": f"{action.replace('_', ' ').title()} was recorded and audited."}
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
            (
                item
                for item in detail["review_fields"]
                if item["key"] == field_key
            ),
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
            "/dashboard/system-brain/cases/"
            f"{case_id}?{urlencode({'error': str(exc)[:500]})}",
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
        return RedirectResponse(
            f"/dashboard/system-brain/cases/{case_id}?{query}", status_code=303
        )
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
            "/dashboard/system-brain/operations?"
            f"{urlencode({'error': str(exc)[:500]})}",
            status_code=303,
        )
    return RedirectResponse(
        "/dashboard/system-brain/operations?"
        "success=Delivery+retry+was+queued+and+audited.",
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
                or methodology_filter
                in json.dumps(row.metadata_redacted or {}).casefold()
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
