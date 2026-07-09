import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.cockpit_service import StrategyCockpitService
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.core.plans import PLAN_DEFINITIONS
from ai_market_monitor.db.models import (
    Alert,
    DashboardPreference,
    DiscordConnection,
    NearMissSnapshot,
    ReferralRelationship,
    ScanJob,
    SetupInstance,
    Strategy,
    StrategyTemplate,
    StrategyVersion,
    SupportRequest,
    TelegramConnection,
    Trial,
    TrialCycle,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, StrategyStatus, UserRole
from ai_market_monitor.engine.quality import alert_trust_score_from_proof
from ai_market_monitor.services.admin_dashboard import AdminDashboardService
from ai_market_monitor.services.admin_notifications import AdminNotificationService
from ai_market_monitor.services.billing import BillingError, BillingService
from ai_market_monitor.services.coverage import market_coverage_for_user
from ai_market_monitor.services.dashboard_links import DashboardLinkError, DashboardLinkService
from ai_market_monitor.services.email_delivery import EmailDeliveryError
from ai_market_monitor.services.entitlements import EntitlementService, PlanCatalogService
from ai_market_monitor.services.lifecycle_dashboard import lifecycle_cards
from ai_market_monitor.services.monitor_operations import (
    MonitorOperationError,
    MonitorOperationService,
)
from ai_market_monitor.services.telegram_account_links import (
    TelegramAccountLinkError,
    TelegramAccountLinkService,
)
from ai_market_monitor.services.template_catalog import builtin_template_payloads
from ai_market_monitor.services.trials import TrialError, TrialLifecycleService
from ai_market_monitor.services.web_auth import (
    SESSION_COOKIE_NAME,
    WebAuthError,
    WebAuthService,
    normalize_email,
)
from ai_market_monitor.telegram.adapter import TelegramDeliveryError, TelegramHttpAdapter
from ai_market_monitor.telegram.types import TelegramButton, TelegramOutboundMessage

PACKAGE_DIR = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
router = APIRouter(tags=["dashboard"])
_signup_lock_guard = asyncio.Lock()
_signup_locks: dict[str, asyncio.Lock] = {}


async def _signup_lock_for(email: str) -> asyncio.Lock:
    key = normalize_email(email) or email.strip().casefold()
    async with _signup_lock_guard:
        lock = _signup_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _signup_locks[key] = lock
        return lock


def _short_datetime(value: datetime | None, timezone_name: str = "UTC") -> str:
    if value is None:
        return "-"
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("UTC")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(timezone).strftime("%Y-%m-%d %H:%M:%S %Z")


def _reward_amount(value: Decimal) -> str:
    value = value.quantize(Decimal("0.01"))
    if value == Decimal("0.00"):
        return "$ 0.00"
    return f"${value:.2f}"


templates.env.filters["short_dt"] = _short_datetime
templates.env.filters["reward_amount"] = _reward_amount

SUPPORTED_TIMEZONES = [
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "Europe/London",
    "Europe/Moscow",
    "Europe/Berlin",
    "Asia/Dubai",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Australia/Sydney",
]
ALERT_DAYS = [
    "Every Day",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
ALERT_HOURS = [f"{hour:02d}:00" for hour in range(24)]
SUPPORTED_THEMES = ["light"]


async def _current_user(
    request: Request,
    session: AsyncSession,
    settings: Settings,
) -> User | None:
    return await WebAuthService(session, settings).current_user(
        request.cookies.get(SESSION_COOKIE_NAME)
    )


async def _require_user(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> User:
    user = await _current_user(request, session, settings)
    if user is None:
        next_path = request.url.path
        return_url = f"/signin?next={next_path}&message=session_required"
        raise HTTPException(status_code=303, headers={"Location": return_url})
    return user


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _no_store(response: HTMLResponse) -> HTMLResponse:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _near_miss_view(item: NearMissSnapshot) -> dict:
    missing = item.missing_conditions or []
    closest = max(
        missing,
        key=lambda condition: float(condition.get("proximity_score") or 0),
        default=None,
    )
    score = float(item.completion_score)
    previous = float(item.previous_score) if item.previous_score is not None else None
    return {
        "id": item.id,
        "scan_result_id": item.scan_result_id,
        "symbol": item.symbol,
        "exchange": item.exchange,
        "timeframe": item.timeframe,
        "completion_score": score,
        "previous_score": previous,
        "trend": item.trend,
        "passed_condition_keys": item.passed_condition_keys or [],
        "missing_conditions": missing,
        "closest_missing_condition": closest,
        "one_condition_remaining": len(missing) == 1 and score < 100,
        "captured_at": item.captured_at,
    }


async def _monitor_cards_context(session: AsyncSession, user: User) -> list[dict]:
    strategies = (
        await session.scalars(
            select(Strategy)
            .where(
                Strategy.user_id == user.id,
                Strategy.archived_at.is_(None),
                Strategy.status != StrategyStatus.ARCHIVED,
            )
            .order_by(Strategy.created_at.desc())
        )
    ).all()
    cockpit_service = StrategyCockpitService(session)
    monitor_cards = []
    for strategy in strategies:
        health = await cockpit_service.edge_health(strategy, persist=False)
        bottlenecks = await cockpit_service.condition_bottlenecks(
            strategy,
            limit=300,
            persist=False,
        )
        latest_scan = None
        if strategy.active_version_id:
            latest_scan = await session.scalar(
                select(ScanJob)
                .where(ScanJob.strategy_version_id == strategy.active_version_id)
                .order_by(ScanJob.created_at.desc())
                .limit(1)
            )
        latency_label = "No completed scan yet"
        if latest_scan and latest_scan.completed_at and latest_scan.scheduled_for:
            seconds = max(
                0,
                int((latest_scan.completed_at - latest_scan.scheduled_for).total_seconds()),
            )
            latency_label = f"{seconds}s scan latency"
        elif latest_scan:
            latency_label = latest_scan.status.value.replace("_", " ").title()
        monitor_cards.append(
            {
                "strategy": strategy,
                "health": health,
                "main_bottleneck": bottlenecks.get("main_bottleneck"),
                "latest_scan": latest_scan,
                "latency_label": latency_label,
            }
        )
    return monitor_cards


async def _analytics_context(session: AsyncSession, user: User) -> dict:
    setup_count = await session.scalar(
        select(func.count(SetupInstance.id)).where(SetupInstance.user_id == user.id)
    )
    alert_count = await session.scalar(select(func.count(Alert.id)).where(Alert.user_id == user.id))
    near_miss_count = await session.scalar(
        select(func.count(NearMissSnapshot.id))
        .join(StrategyVersion, NearMissSnapshot.strategy_version_id == StrategyVersion.id)
        .join(Strategy, StrategyVersion.strategy_id == Strategy.id)
        .where(
            Strategy.user_id == user.id,
            NearMissSnapshot.completion_score < 100,
        )
    )
    state_rows = (
        await session.execute(
            select(SetupInstance.state, func.count(SetupInstance.id))
            .where(SetupInstance.user_id == user.id)
            .group_by(SetupInstance.state)
        )
    ).all()
    symbol_rows = (
        await session.execute(
            select(
                SetupInstance.symbol,
                func.count(SetupInstance.id),
                func.max(SetupInstance.last_evaluated_at),
            )
            .where(SetupInstance.user_id == user.id)
            .group_by(SetupInstance.symbol)
            .order_by(func.count(SetupInstance.id).desc())
            .limit(12)
        )
    ).all()
    return {
        "setup_count": setup_count or 0,
        "alert_count": alert_count or 0,
        "near_miss_count": near_miss_count or 0,
        "coverage": await market_coverage_for_user(session, user.id),
        "state_rows": state_rows,
        "symbol_rows": symbol_rows,
    }


def _alert_view(alert: Alert) -> dict:
    trust = alert_trust_score_from_proof(alert.proof_receipt or {})
    return {
        "id": alert.id,
        "title": alert.title,
        "body": alert.body,
        "alert_type": alert.alert_type.value,
        "created_at": alert.created_at,
        "symbol": (alert.proof_receipt or {}).get("symbol", "Market"),
        "completion_score": (alert.proof_receipt or {}).get("setup_completion_score"),
        "trust_score": trust,
    }


async def _send_telegram_connected_notification(
    session: AsyncSession,
    settings: Settings,
    telegram_user_id: str | None,
) -> None:
    if (
        not telegram_user_id
        or settings.telegram_adapter != "http"
        or settings.telegram_bot_token is None
    ):
        return
    connection = await session.scalar(
        select(TelegramConnection).where(TelegramConnection.telegram_user_id == telegram_user_id)
    )
    if connection is None or not connection.chat_id:
        return
    try:
        await TelegramHttpAdapter(settings).deliver(
            TelegramOutboundMessage(
                chat_id=connection.chat_id,
                text=(
                    "Dashboard account connected.\n\n"
                    "Telegram can now show your trial status, subscription dates, monitor "
                    "counts and account-linked alerts."
                ),
                buttons=[
                    TelegramButton(
                        "Dashboard",
                        "external:dashboard",
                        url=f"{str(settings.public_base_url).rstrip('/')}/dashboard",
                    ),
                    TelegramButton("Lifecycles", "menu:latest_setups"),
                    TelegramButton("Pricing", "pricing"),
                ],
                menu=["My Monitors", "Lifecycles", "Trial", "Pricing", "Settings", "Support", "About"],
            )
        )
    except TelegramDeliveryError:
        return


async def _context(
    *,
    request: Request,
    session: AsyncSession,
    settings: Settings,
    user: User | None,
    page: str,
    title: str,
    message: str | None = None,
    error: str | None = None,
    **extra,
) -> dict:
    dashboard_preference = None
    if user is not None:
        dashboard_preference = await session.scalar(
            select(DashboardPreference).where(DashboardPreference.user_id == user.id)
        )
    dashboard_theme = "light"
    if dashboard_theme not in SUPPORTED_THEMES:
        dashboard_theme = "light"
    telegram_username = (
        settings.telegram_bot_username.lstrip("@").strip()
        if settings.telegram_bot_username
        else None
    )
    telegram_url = (
        f"https://t.me/{telegram_username}?start=dashboard"
        if telegram_username
        else "#telegram-not-configured"
    )
    return {
        "request": request,
        "user": user,
        "settings": settings,
        "page": page,
        "title": title,
        "message": message or request.query_params.get("message"),
        "error": error or request.query_params.get("error"),
        "telegram_url": telegram_url,
        "plans": PLAN_DEFINITIONS,
        "dashboard_preference": dashboard_preference,
        "dashboard_theme": dashboard_theme,
        **extra,
    }


@router.get("/how-it-works", response_class=HTMLResponse, include_in_schema=False)
async def how_it_works(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dashboard_public.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=await _current_user(request, session, settings),
            page="how",
            title="How It Works",
        ),
    )


@router.get("/pricing", response_class=HTMLResponse, include_in_schema=False)
async def pricing(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    await PlanCatalogService(session).sync_defaults()
    await session.commit()
    return templates.TemplateResponse(
        request,
        "dashboard_public.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=await _current_user(request, session, settings),
            page="pricing",
            title="Pricing",
        ),
    )


@router.get("/about", response_class=HTMLResponse, include_in_schema=False)
async def about(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dashboard_public.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=await _current_user(request, session, settings),
            page="about",
            title="About",
        ),
    )


@router.get("/faq", response_class=HTMLResponse, include_in_schema=False)
async def faq(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dashboard_public.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=await _current_user(request, session, settings),
            page="faq",
            title="FAQ",
        ),
    )


@router.get("/risk", response_class=HTMLResponse, include_in_schema=False)
async def risk(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dashboard_public.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=await _current_user(request, session, settings),
            page="risk",
            title="Risk Disclaimer",
        ),
    )


@router.get("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return _no_store(templates.TemplateResponse(
        request,
        "auth.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=await _current_user(request, session, settings),
            page="signup",
            title="Sign Up",
        ),
    ))


@router.post("/signup", include_in_schema=False)
async def signup_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    repeat_password: str = Form(...),
    telegram_link: str | None = Form(None),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    signup_lock = await _signup_lock_for(email)
    async with signup_lock:
        service = WebAuthService(session, settings)
        try:
            if password != repeat_password:
                raise WebAuthError("password_mismatch", "Password fields must match.")
            await service.request_signup_email_code(
                email=email,
                password=password,
                display_name=None,
                telegram_link=telegram_link,
                requested_ip=request.client.host if request.client else None,
            )
            await session.commit()
        except (WebAuthError, EmailDeliveryError) as exc:
            await session.rollback()
            code = getattr(exc, "code", "signup_failed")
            if code == "code_recently_sent":
                query = {"message": "code_sent", "email": email}
                if telegram_link:
                    query["telegram_link"] = telegram_link
                return _redirect(f"/signup/verify?{urlencode(query)}")
            return _redirect(f"/signup?error={code}")

    query = {"message": "code_sent", "email": email}
    if telegram_link:
        query["telegram_link"] = telegram_link
    return _redirect(f"/signup/verify?{urlencode(query)}")


@router.get("/signup/verify", response_class=HTMLResponse, include_in_schema=False)
async def signup_verify_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return _no_store(templates.TemplateResponse(
        request,
        "auth.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=await _current_user(request, session, settings),
            page="signup_verify",
            title="Verify Your Email",
        ),
    ))


@router.post("/signup/verify", include_in_schema=False)
async def signup_verify_submit(
    request: Request,
    email: str = Form(...),
    code: str = Form(...),
    telegram_link: str | None = Form(None),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    service = WebAuthService(session, settings)
    linked_telegram_user_id: str | None = None
    try:
        user = await service.complete_signup_with_email_code(email=email, code=code)
        telegram_connected = False
        if telegram_link:
            linked_telegram_user_id = await TelegramAccountLinkService(session, settings).complete(
                telegram_link, user=user
            )
            telegram_connected = True
        cookie = await service.create_session(user, user_agent=request.headers.get("user-agent"))
        await session.commit()
    except (WebAuthError, TelegramAccountLinkError) as exc:
        await session.rollback()
        query = {"error": getattr(exc, "code", "invalid_code"), "email": email}
        if telegram_link:
            query["telegram_link"] = telegram_link
        return _redirect(f"/signup/verify?{urlencode(query)}")

    message = "telegram_connected" if telegram_connected else "account_created"
    await AdminNotificationService(settings).send_signup_created(
        user_id=user.id,
        email=email,
        source="dashboard",
    )
    await _send_telegram_connected_notification(session, settings, linked_telegram_user_id)
    response = _redirect(f"/dashboard?message={message}")
    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie,
        httponly=True,
        secure=settings.is_deployed,
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
    )
    return response


@router.post("/signin", include_in_schema=False)
async def signin_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    telegram_link: str | None = Form(None),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    service = WebAuthService(session, settings)
    linked_telegram_user_id: str | None = None
    try:
        user = await service.signin_email(email=email, password=password)
        telegram_connected = False
        if telegram_link:
            linked_telegram_user_id = await TelegramAccountLinkService(session, settings).complete(
                telegram_link, user=user
            )
            telegram_connected = True
        cookie = await service.create_session(user, user_agent=request.headers.get("user-agent"))
        await session.commit()
    except (ValueError, TelegramAccountLinkError) as exc:
        await session.rollback()
        code = getattr(exc, "code", "invalid_login")
        return _redirect(f"/signin?error={code}")
    response = _redirect(
        "/dashboard?message=telegram_connected"
        if telegram_connected
        else "/dashboard?message=login_successful"
    )
    await _send_telegram_connected_notification(session, settings, linked_telegram_user_id)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie,
        httponly=True,
        secure=settings.is_deployed,
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
    )
    return response


@router.get("/signin", response_class=HTMLResponse, include_in_schema=False)
async def signin_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return _no_store(templates.TemplateResponse(
        request,
        "auth.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=await _current_user(request, session, settings),
            page="signin",
            title="Sign In",
        ),
    ))


@router.get("/signin/code", response_class=HTMLResponse, include_in_schema=False)
async def signin_code_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return _no_store(templates.TemplateResponse(
        request,
        "auth.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=await _current_user(request, session, settings),
            page="signin_code",
            title="Login With One-Time Code",
        ),
    ))


@router.post("/signin/code/request", include_in_schema=False)
async def signin_code_request(
    request: Request,
    email: str = Form(...),
    telegram_link: str | None = Form(None),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    try:
        await WebAuthService(session, settings).request_email_code(
            email=email,
            purpose="login",
            requested_ip=request.client.host if request.client else None,
        )
        await session.commit()
    except (WebAuthError, EmailDeliveryError) as exc:
        await session.rollback()
        return _redirect(f"/signin/code?error={getattr(exc, 'code', 'email_unavailable')}")
    query = {"message": "code_sent", "email": email}
    if telegram_link:
        query["telegram_link"] = telegram_link
    return _redirect(f"/signin/code?{urlencode(query)}")


@router.post("/signin/code/verify", include_in_schema=False)
async def signin_code_verify(
    request: Request,
    email: str = Form(...),
    code: str = Form(...),
    telegram_link: str | None = Form(None),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    service = WebAuthService(session, settings)
    linked_telegram_user_id: str | None = None
    try:
        user = await service.signin_with_email_code(email=email, code=code)
        telegram_connected = False
        if telegram_link:
            linked_telegram_user_id = await TelegramAccountLinkService(session, settings).complete(
                telegram_link,
                user=user,
            )
            telegram_connected = True
        cookie = await service.create_session(user, user_agent=request.headers.get("user-agent"))
        await session.commit()
    except (WebAuthError, TelegramAccountLinkError) as exc:
        await session.rollback()
        query = {
            "error": getattr(exc, "code", "invalid_code"),
            "email": email,
        }
        return _redirect(
            f"/signin/code?{urlencode(query)}"
        )
    await _send_telegram_connected_notification(session, settings, linked_telegram_user_id)
    response = _redirect(
        "/dashboard?message=telegram_connected"
        if telegram_connected
        else "/dashboard?message=login_successful"
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie,
        httponly=True,
        secure=settings.is_deployed,
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
    )
    return response


@router.get("/reset-password", response_class=HTMLResponse, include_in_schema=False)
async def reset_password_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return _no_store(templates.TemplateResponse(
        request,
        "auth.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=await _current_user(request, session, settings),
            page="reset_password",
            title="Reset Password",
        ),
    ))


@router.post("/reset-password/request", include_in_schema=False)
async def reset_password_request(
    request: Request,
    email: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    try:
        sent = await WebAuthService(session, settings).request_email_code(
            email=email,
            purpose="password_reset",
            requested_ip=request.client.host if request.client else None,
        )
        if not sent:
            raise WebAuthError(
                "account_not_registered",
                "No registered account exists for that email.",
            )
        await session.commit()
    except (WebAuthError, EmailDeliveryError) as exc:
        await session.rollback()
        return _redirect(f"/reset-password?error={getattr(exc, 'code', 'email_unavailable')}")
    return _redirect(f"/reset-password?{urlencode({'message': 'code_sent', 'email': email})}")


@router.post("/reset-password/verify", include_in_schema=False)
async def reset_password_verify(
    email: str = Form(...),
    code: str = Form(...),
    password: str = Form(...),
    repeat_password: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if password != repeat_password:
        return _redirect(
            f"/reset-password?{urlencode({'error': 'password_mismatch', 'email': email})}"
        )
    try:
        await WebAuthService(session, settings).reset_password_with_email_code(
            email=email,
            code=code,
            password=password,
        )
        await session.commit()
    except WebAuthError as exc:
        await session.rollback()
        return _redirect(
            f"/reset-password?{urlencode({'error': exc.code, 'email': email})}"
        )
    return _redirect("/signin?message=password_reset_successful")


@router.post("/logout", include_in_schema=False)
async def logout(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    await WebAuthService(session, settings).revoke(request.cookies.get(SESSION_COOKIE_NAME))
    await session.commit()
    response = _redirect("/signin?message=logout_successful")
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@router.get("/dashboard/link/{token}", include_in_schema=False)
async def dashboard_signed_link(
    token: str,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    try:
        _, target_path, cookie = await DashboardLinkService(session, settings).consume(token)
        await session.commit()
    except DashboardLinkError as exc:
        await session.rollback()
        return _redirect(f"/signin?error={exc.code}")
    response = _redirect(target_path)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie,
        httponly=True,
        secure=settings.is_deployed,
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
    )
    return response


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_home(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    counts = {
        "strategies": await session.scalar(
            select(func.count(Strategy.id)).where(Strategy.user_id == user.id)
        )
        or 0,
        "active": await session.scalar(
            select(func.count(Strategy.id)).where(
                Strategy.user_id == user.id, Strategy.status == StrategyStatus.ACTIVE
            )
        )
        or 0,
        "alerts": await session.scalar(select(func.count(Alert.id)).where(Alert.user_id == user.id))
        or 0,
        "setups": await session.scalar(
            select(func.count(SetupInstance.id)).where(SetupInstance.user_id == user.id)
        )
        or 0,
    }
    alerts_today = (
        await session.scalar(
            select(func.count(Alert.id)).where(
                Alert.user_id == user.id,
                Alert.created_at >= today_start,
            )
        )
        or 0
    )
    active_lifecycle_count = (
        await session.scalar(
            select(func.count(SetupInstance.id)).where(
                SetupInstance.user_id == user.id,
                SetupInstance.state.not_in(
                    [
                        "expired",
                        "invalidated",
                        "completed",
                        "closed",
                        "manually_closed",
                    ]
                ),
            )
        )
        or 0
    )
    latest_setup = await session.scalar(
        select(SetupInstance)
        .where(SetupInstance.user_id == user.id)
        .order_by(SetupInstance.updated_at.desc())
        .limit(1)
    )
    latest_alert = await session.scalar(
        select(Alert).where(Alert.user_id == user.id).order_by(Alert.created_at.desc()).limit(1)
    )
    telegram = await session.scalar(
        select(TelegramConnection).where(TelegramConnection.user_id == user.id)
    )
    discord = await session.scalar(
        select(DiscordConnection).where(DiscordConnection.user_id == user.id)
    )
    coverage = await market_coverage_for_user(session, user.id)
    trial = await session.scalar(select(Trial).where(Trial.user_id == user.id))
    entitlement = await EntitlementService(session).current(user.id)
    overview = {
        "alerts_today": alerts_today,
        "active_lifecycle_count": active_lifecycle_count,
        "latest_setup": latest_setup,
        "latest_alert": _alert_view(latest_alert) if latest_alert else None,
        "coverage": coverage,
        "telegram_connected": telegram is not None,
        "discord_connected": discord is not None,
    }
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="home",
            title="Dashboard",
            counts=counts,
            trial=trial,
            entitlement=entitlement,
            overview=overview,
            analytics=await _analytics_context(session, user),
        ),
    )


@router.get("/dashboard/strategies", response_class=HTMLResponse, include_in_schema=False)
@router.get("/dashboard/monitors", response_class=HTMLResponse, include_in_schema=False)
async def monitors_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    return _redirect("/dashboard/strategies/new?message=monitors_moved_to_create_monitor#monitors")


@router.get("/dashboard/strategies/new", response_class=HTMLResponse, include_in_schema=False)
async def new_strategy_builder_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    templates_list = (
        await session.scalars(
            select(StrategyTemplate)
            .where(StrategyTemplate.user_id == user.id, StrategyTemplate.archived_at.is_(None))
            .order_by(StrategyTemplate.category.asc(), StrategyTemplate.name.asc())
        )
    ).all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="strategy_builder",
            title="Strategy Builder",
            strategy=None,
            version=None,
            templates=templates_list,
            builtin_templates=builtin_template_payloads(),
            monitor_cards=await _monitor_cards_context(session, user),
        ),
    )


@router.get(
    "/dashboard/strategies/{strategy_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def strategy_detail_page(
    request: Request,
    strategy_id: UUID,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    strategy = await session.get(Strategy, strategy_id)
    if strategy is None or strategy.user_id != user.id:
        raise HTTPException(status_code=404, detail="Strategy not found")
    versions = (
        await session.scalars(
            select(StrategyVersion)
            .where(StrategyVersion.strategy_id == strategy.id)
            .order_by(StrategyVersion.version_number.desc())
        )
    ).all()
    setups_count = 0
    if versions:
        setups_count = await session.scalar(
            select(func.count(SetupInstance.id)).where(
                SetupInstance.strategy_version_id.in_([version.id for version in versions])
            )
        )
    cockpit_service = StrategyCockpitService(session)
    monitor_health = await cockpit_service.edge_health(strategy, persist=False)
    monitor_bottlenecks = await cockpit_service.condition_bottlenecks(
        strategy,
        limit=500,
        persist=False,
    )
    monitor_decay = await cockpit_service.detect_decay(strategy, persist=False)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="strategy_detail",
            title=strategy.name,
            strategy=strategy,
            versions=versions,
            setups_count=setups_count or 0,
            monitor_health=monitor_health,
            monitor_bottlenecks=monitor_bottlenecks,
            monitor_decay=monitor_decay,
        ),
    )


@router.get(
    "/dashboard/strategies/{strategy_id}/builder",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def strategy_builder_edit_page(
    request: Request,
    strategy_id: UUID,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    strategy = await session.get(Strategy, strategy_id)
    if strategy is None or strategy.user_id != user.id:
        raise HTTPException(status_code=404, detail="Strategy not found")
    version = await session.scalar(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy.id)
        .order_by(StrategyVersion.version_number.desc())
        .limit(1)
    )
    templates_list = (
        await session.scalars(
            select(StrategyTemplate)
            .where(StrategyTemplate.user_id == user.id, StrategyTemplate.archived_at.is_(None))
            .order_by(StrategyTemplate.category.asc(), StrategyTemplate.name.asc())
        )
    ).all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="strategy_builder",
            title=f"Edit {strategy.name}",
            strategy=strategy,
            version=version,
            templates=templates_list,
            builtin_templates=builtin_template_payloads(),
            monitor_cards=await _monitor_cards_context(session, user),
        ),
    )


@router.get(
    "/dashboard/strategies/{strategy_id}/versions",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def strategy_versions_page(
    request: Request,
    strategy_id: UUID,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    strategy = await session.get(Strategy, strategy_id)
    if strategy is None or strategy.user_id != user.id:
        raise HTTPException(status_code=404, detail="Strategy not found")
    versions = (
        await session.scalars(
            select(StrategyVersion)
            .where(StrategyVersion.strategy_id == strategy.id)
            .order_by(StrategyVersion.version_number.desc())
        )
    ).all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="strategy_versions",
            title=f"{strategy.name} Versions",
            strategy=strategy,
            versions=versions,
        ),
    )


@router.post("/dashboard/monitors/{strategy_id}/pause", include_in_schema=False)
async def pause_monitor(
    strategy_id: UUID,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    try:
        await MonitorOperationService(session).pause(
            user_id=user.id,
            strategy_id=strategy_id,
            actor_type="dashboard_user",
        )
        await session.commit()
        return _redirect("/dashboard/strategies/new?message=monitor_paused#monitors")
    except MonitorOperationError as exc:
        await session.rollback()
        return _redirect(f"/dashboard/strategies/new?error={exc.code}#monitors")


@router.post("/dashboard/monitors/{strategy_id}/resume", include_in_schema=False)
async def resume_monitor(
    strategy_id: UUID,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    try:
        await MonitorOperationService(session).resume(
            user_id=user.id,
            strategy_id=strategy_id,
            actor_type="dashboard_user",
        )
        await session.commit()
        return _redirect("/dashboard/strategies/new?message=monitor_resumed#monitors")
    except MonitorOperationError as exc:
        await session.rollback()
        return _redirect(f"/dashboard/strategies/new?error={exc.code}#monitors")


@router.post("/dashboard/monitors/{strategy_id}/delete", include_in_schema=False)
async def delete_monitor(
    strategy_id: UUID,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    try:
        await MonitorOperationService(session).delete(
            user_id=user.id,
            strategy_id=strategy_id,
            actor_type="dashboard_user",
        )
        await session.commit()
        return _redirect("/dashboard/strategies/new?message=monitor_deleted#monitors")
    except MonitorOperationError as exc:
        await session.rollback()
        return _redirect(f"/dashboard/strategies/new?error={exc.code}#monitors")


@router.get("/dashboard/create-monitor", response_class=HTMLResponse, include_in_schema=False)
async def create_monitor_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return await new_strategy_builder_page(request, user, session, settings)


@router.get("/dashboard/scan-now", response_class=HTMLResponse, include_in_schema=False)
async def scan_now_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    strategies = (
        await session.scalars(
            select(Strategy).where(Strategy.user_id == user.id).order_by(Strategy.created_at.desc())
        )
    ).all()
    templates_list = (
        await session.scalars(
            select(StrategyTemplate)
            .where(StrategyTemplate.user_id == user.id, StrategyTemplate.archived_at.is_(None))
            .order_by(StrategyTemplate.category.asc(), StrategyTemplate.name.asc())
        )
    ).all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="scan",
            title="Quick Scan",
            strategies=strategies,
            templates=templates_list,
        ),
    )


async def near_miss_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    raise HTTPException(status_code=404, detail="This dashboard section is not available")


@router.get("/dashboard/setups", response_class=HTMLResponse, include_in_schema=False)
async def setups_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    raise HTTPException(status_code=404, detail="Latest Setups was removed. Use Lifecycles.")


@router.get("/dashboard/lifecycles", response_class=HTMLResponse, include_in_schema=False)
async def lifecycles_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    await StrategyCockpitService(session).sync_inbox(user.id)
    await session.commit()
    preference = await session.scalar(
        select(DashboardPreference).where(DashboardPreference.user_id == user.id)
    )
    muted_setup_ids = set(
        map(str, ((preference.notification_preferences or {}).get("muted_setup_instance_ids", [])))
    ) if preference is not None else set()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="lifecycles",
            title="Lifecycles",
            lifecycle_cards=await lifecycle_cards(
                session,
                user.id,
                muted_setup_ids=muted_setup_ids,
            ),
        ),
    )


@router.get("/dashboard/alerts", response_class=RedirectResponse, include_in_schema=False)
async def alerts_page(
    user: User = Depends(_require_user),
) -> RedirectResponse:
    return _redirect("/dashboard/lifecycles?message=alerts_moved_to_lifecycles")


@router.get(
    "/dashboard/alerts/{alert_id}",
    response_class=RedirectResponse,
    include_in_schema=False,
)
async def alert_detail_page(
    alert_id: UUID,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    alert = await session.get(Alert, alert_id)
    if alert is None or alert.user_id != user.id:
        raise HTTPException(status_code=404, detail="Alert not found")
    return _redirect("/dashboard/lifecycles?message=alert_context_moved_to_lifecycles")


@router.get(
    "/dashboard/alerts/{alert_id}/proof",
    response_class=RedirectResponse,
    include_in_schema=False,
)
async def alert_proof_page(
    alert_id: UUID,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    return await alert_detail_page(alert_id, user, session)


@router.get("/dashboard/why-no-alert", response_class=HTMLResponse, include_in_schema=False)
async def why_no_alert_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return await setup_replay_page(request, user, session, settings, title="Why No Alert?")


async def setup_replay_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    title: str = "Setup Replay",
) -> HTMLResponse:
    raise HTTPException(status_code=404, detail=f"{title} is not available")


@router.get("/dashboard/cockpit", response_class=HTMLResponse, include_in_schema=False)
async def strategy_cockpit_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    raise HTTPException(status_code=404, detail="Strategy Cockpit was removed. Use Monitors and Lifecycles.")


@router.get("/dashboard/analytics", response_class=HTMLResponse, include_in_schema=False)
async def analytics_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    return _redirect("/dashboard?message=analytics_moved_to_overview")


@router.get("/dashboard/trial", response_class=HTMLResponse, include_in_schema=False)
async def trial_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    trial = await session.scalar(select(Trial).where(Trial.user_id == user.id))
    cycle = None
    if trial is not None:
        cycle = await session.scalar(
            select(TrialCycle)
            .where(TrialCycle.trial_id == trial.id)
            .order_by(TrialCycle.cycle_number.desc())
        )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="trial",
            title="Trial Status",
            trial=trial,
            cycle=cycle,
        ),
    )


@router.post("/dashboard/trial/claim", include_in_schema=False)
async def claim_trial(
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    try:
        await TrialLifecycleService(session, settings).activate(user.id)
        await session.commit()
        await AdminNotificationService(settings).send(
            f"Trial claimed: {user.display_name or user.id}"
        )
        return _redirect("/dashboard/trial?message=trial_claimed")
    except TrialError as exc:
        await session.rollback()
        return _redirect(f"/dashboard/trial?error={exc.code}")


@router.get("/dashboard/billing", response_class=HTMLResponse, include_in_schema=False)
@router.get("/dashboard/subscription", response_class=HTMLResponse, include_in_schema=False)
async def billing_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    await PlanCatalogService(session).sync_defaults()
    entitlement = await EntitlementService(session).current(user.id)
    trial = await session.scalar(select(Trial).where(Trial.user_id == user.id))
    await session.commit()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="billing",
            title="Subscription and Billing",
            entitlement=entitlement,
            trial=trial,
        ),
    )


@router.post("/dashboard/billing/checkout", include_in_schema=False)
async def billing_checkout(
    request: Request,
    plan_code: str = Form(...),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    base = str(settings.public_base_url).rstrip("/")
    try:
        plan = PLAN_DEFINITIONS.get(plan_code)
        if plan is not None and plan.monthly_price == 0:
            await BillingService(session, settings).activate_free_plan(
                user_id=user.id,
                plan_code=plan_code,
            )
            await session.commit()
            await AdminNotificationService(settings).send(
                f"Free plan: {user.display_name or user.id} {plan_code}"
            )
            return _redirect("/dashboard/billing?message=free_plan_activated")
        checkout = await BillingService(session, settings).checkout_session(
            user_id=user.id,
            plan_code=plan_code,
            success_url=f"{base}/billing/success",
            cancel_url=f"{base}/billing/cancel",
        )
        await session.commit()
        await AdminNotificationService(settings).send(
            f"Payment link: {user.display_name or user.id} {plan_code}"
        )
        return _redirect(checkout.checkout_url)
    except BillingError as exc:
        await session.rollback()
        return _redirect(f"/dashboard/billing?error={exc.code}")


@router.get("/billing/success", response_class=HTMLResponse, include_in_schema=False)
async def billing_success(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    message = "payment_successful"
    if request.query_params.get("checkout") == "pending":
        user_id = request.query_params.get("user")
        plan_code = request.query_params.get("plan")
        session_id = request.query_params.get("session") or f"static:{user_id}:{plan_code}"
        if user_id and plan_code:
            try:
                await BillingService(session, settings).process_event(
                    provider="static",
                    payload={
                        "id": f"static_checkout:{session_id}",
                        "type": "checkout.session.completed",
                        "data": {
                            "user_id": user_id,
                            "plan_code": plan_code,
                            "provider_subscription_id": session_id,
                            "status": "active",
                            "current_period_start": datetime.now(UTC).isoformat(),
                            "current_period_end": (
                                datetime.now(UTC) + timedelta(days=30)
                            ).isoformat(),
                            "cancel_at_period_end": False,
                        },
                    },
                )
                await session.commit()
                await AdminNotificationService(settings).send(
                    f"Payment success: user:{user_id} {plan_code}"
                )
            except BillingError:
                await session.rollback()
                message = "payment_pending_provider_confirmation"
    return templates.TemplateResponse(
        request,
        "billing_result.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=await _current_user(request, session, settings),
            page="billing_success",
            title="Payment Successful",
            message=message,
        ),
    )


@router.get("/billing/cancel", response_class=HTMLResponse, include_in_schema=False)
async def billing_cancel(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "billing_result.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=await _current_user(request, session, settings),
            page="billing_cancel",
            title="Payment Canceled",
            error="payment_canceled",
        ),
    )


@router.get("/billing/error", response_class=HTMLResponse, include_in_schema=False)
async def billing_error(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "billing_result.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=await _current_user(request, session, settings),
            page="billing_error",
            title="Payment Error",
            error="payment_failed",
        ),
    )


@router.get("/dashboard/integrations", response_class=HTMLResponse, include_in_schema=False)
@router.get("/dashboard/connections", response_class=HTMLResponse, include_in_schema=False)
async def connections_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    telegram = await session.scalar(
        select(TelegramConnection).where(TelegramConnection.user_id == user.id)
    )
    discord = await session.scalar(
        select(DiscordConnection).where(DiscordConnection.user_id == user.id)
    )
    telegram_connect_url = None
    telegram_start_command = None
    try:
        telegram_connect_url = await TelegramAccountLinkService(
            session,
            settings,
        ).create_dashboard_start_link(user_id=user.id)
        if telegram_connect_url and "?start=" in telegram_connect_url:
            telegram_start_command = (
                "/start " + telegram_connect_url.split("?start=", 1)[1].split("&", 1)[0]
            )
        await session.commit()
    except TelegramAccountLinkError:
        await session.rollback()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="integrations",
            title="Integrations",
            telegram=telegram,
            discord=discord,
            telegram_connect_url=telegram_connect_url,
            telegram_start_command=telegram_start_command,
        ),
    )


@router.get("/dashboard/exports", response_class=HTMLResponse, include_in_schema=False)
async def exports_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    return _redirect("/dashboard?message=exports_hidden")


@router.get("/dashboard/settings", response_class=HTMLResponse, include_in_schema=False)
async def settings_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    preference = await session.scalar(
        select(DashboardPreference).where(DashboardPreference.user_id == user.id)
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="settings",
            title="Settings",
            preference=preference,
            supported_timezones=SUPPORTED_TIMEZONES,
            supported_themes=SUPPORTED_THEMES,
            alert_days=ALERT_DAYS,
            alert_hours=ALERT_HOURS,
        ),
    )


@router.post("/dashboard/settings", include_in_schema=False)
async def settings_submit(
    timezone: str = Form(...),
    near_miss_enabled: str = Form("true"),
    near_miss_threshold: int = Form(70),
    maximum_alerts_per_hour: int = Form(50),
    maximum_alerts_per_day: int = Form(500),
    alert_channels: list[str] = Form(default=["telegram"]),
    providers: list[str] = Form(default=["binance", "bybit"]),
    alert_days: list[str] = Form(default=["Every Day"]),
    alert_hours: list[str] = Form(default=ALERT_HOURS),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    if timezone not in SUPPORTED_TIMEZONES:
        return _redirect("/dashboard/settings?error=unsupported_timezone")
    allowed_channels = {"telegram", "discord"}
    channels = [channel for channel in alert_channels if channel in allowed_channels]
    if not channels:
        channels = ["telegram"]
    allowed_providers = {"binance", "bybit"}
    selected_providers = [
        provider for provider in providers if provider in allowed_providers
    ]
    if not selected_providers:
        selected_providers = ["binance", "bybit"]
    days = [day for day in alert_days if day in ALERT_DAYS]
    if not days:
        days = ["Every Day"]
    if "Every Day" in days:
        days = ["Every Day"]
    hours = [hour for hour in alert_hours if hour in ALERT_HOURS]
    user.timezone = timezone
    preference = await session.scalar(
        select(DashboardPreference).where(DashboardPreference.user_id == user.id)
    )
    if preference is None:
        preference = DashboardPreference(
            user_id=user.id,
            default_timezone=timezone,
            theme="light",
        )
        session.add(preference)
    else:
        preference.default_timezone = timezone
    prefs = dict(preference.notification_preferences or {})
    prefs.update(
        {
            "timezone": timezone,
            "near_miss_enabled": near_miss_enabled == "true",
            "near_miss_threshold": max(1, min(100, near_miss_threshold)),
            "maximum_alerts_per_hour": max(1, min(1000, maximum_alerts_per_hour)),
            "maximum_alerts_per_day": max(1, min(10000, maximum_alerts_per_day)),
            "alert_channels": channels,
            "channels": channels,
            "providers": selected_providers,
            "alert_days": days,
            "alert_hours": hours,
        }
    )
    preference.notification_preferences = prefs
    await session.commit()
    return _redirect("/dashboard/settings?message=settings_saved")


@router.get("/dashboard/support", response_class=HTMLResponse, include_in_schema=False)
async def support_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    tickets = (
        await session.scalars(
            select(SupportRequest)
            .where(SupportRequest.user_id == user.id)
            .order_by(SupportRequest.created_at.desc())
            .limit(20)
        )
    ).all()
    email_identity = await session.scalar(
        select(UserIdentity)
        .where(
            UserIdentity.user_id == user.id,
            UserIdentity.provider == IdentityProvider.EMAIL,
        )
        .order_by(UserIdentity.is_primary.desc(), UserIdentity.created_at.asc())
        .limit(1)
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="support",
            title="Support",
            tickets=tickets,
            support_email=(
                email_identity.display_identifier
                or email_identity.normalized_identifier
                if email_identity
                else ""
            ),
        ),
    )


@router.get("/dashboard/admin", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_admin_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Administrator role required")
    admin_service = AdminDashboardService(session, settings)
    overview = await admin_service.overview()
    health = await admin_service.health_dashboard()
    activity = await admin_service.recent_activity(limit=12)
    users = await admin_service.user_search(limit=12)
    counts = {
        "users": await session.scalar(select(func.count(User.id))) or 0,
        "strategies": await session.scalar(select(func.count(Strategy.id))) or 0,
        "alerts": await session.scalar(select(func.count(Alert.id))) or 0,
        "tickets": await session.scalar(select(func.count(SupportRequest.id))) or 0,
    }
    tickets = (
        await session.scalars(
            select(SupportRequest).order_by(SupportRequest.created_at.desc()).limit(20)
        )
    ).all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="admin",
            title="Admin",
            counts=counts,
            overview=overview,
            health=health,
            activity=activity,
            users=users,
            tickets=tickets,
        ),
    )


@router.get("/dashboard/referrals", response_class=HTMLResponse, include_in_schema=False)
async def referrals_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    relationships = (
        await session.scalars(
            select(ReferralRelationship).where(
                ReferralRelationship.referrer_user_id == user.id,
                ReferralRelationship.reward_status == "eligible_after_first_paid_month",
            )
        )
    ).all()
    reward_balance = Decimal("0")
    for relationship in relationships:
        raw_amount = (relationship.metadata_json or {}).get("reward_amount_usd", 0)
        try:
            reward_balance += Decimal(str(raw_amount))
        except InvalidOperation:
            continue
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="referrals",
            title="Referrals",
            referral_url=f"{settings.public_base_url}signup?ref={user.id}",
            reward_balance=reward_balance,
        ),
    )
