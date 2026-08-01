import asyncio
import hmac
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlencode
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import get_market_data_provider, get_market_previewer
from ai_market_monitor.cockpit_service import StrategyCockpitService
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.csrf import csrf_token, csrf_token_matches
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.core.plans import (
    COMING_SOON_LABEL,
    PLAN_DEFINITIONS,
    PROMOTION_ENDS_AT,
    PUBLIC_PLAN_CODES,
    PUBLIC_PLAN_PRESENTATIONS,
    PURCHASABLE_PLAN_CODES,
    plan_offer_payload,
    promotion_is_active,
    visible_plan_comparison,
    visible_plan_comparison_headers,
    visible_public_plan_codes,
)
from ai_market_monitor.core.site_content import DASHBOARD_NAVIGATION
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    ApprovedWatchlist,
    ApprovedWatchlistAsset,
    AssetShariaStatusHistory,
    BillingCheckoutAttempt,
    CanonicalAsset,
    CapabilityExtension,
    ComplianceDriftNotification,
    DashboardNotification,
    DashboardPreference,
    MonitorShariaAssetState,
    NearMissSnapshot,
    PaymentEmailDelivery,
    Plan,
    PublishedAssetAssessment,
    ReferralRelationship,
    ScanJob,
    SetupInstance,
    ShariaMethodology,
    Strategy,
    StrategyTemplate,
    StrategyUniverse,
    StrategyVersion,
    Subscription,
    SupportRequest,
    TelegramConnection,
    Trial,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import (
    ComplianceChangeBehavior,
    ConnectionStatus,
    DeliveryChannel,
    DeliveryStatus,
    IdentityProvider,
    MonitorShariaAssetStatus,
    ShariaAssetStatus,
    StrategyStatus,
    SubscriptionStatus,
    UserRole,
)
from ai_market_monitor.engine.quality import alert_trust_score_from_proof
from ai_market_monitor.services.activity import ActivityReadService
from ai_market_monitor.services.admin_notifications import AdminNotificationService
from ai_market_monitor.services.billing import (
    BillingError,
    BillingService,
    billing_provider_capabilities,
    configured_billing_provider,
)
from ai_market_monitor.services.capability_extensions import CapabilityExtensionService
from ai_market_monitor.services.coverage import market_coverage_for_user
from ai_market_monitor.services.dashboard_links import DashboardLinkError, DashboardLinkService
from ai_market_monitor.services.email_delivery import EmailDeliveryError
from ai_market_monitor.services.entitlements import EntitlementService, PlanCatalogService
from ai_market_monitor.services.interfaces import MarketDataProvider, RecentMarketPreviewer
from ai_market_monitor.services.lifecycle_dashboard import lifecycle_cards
from ai_market_monitor.services.monitor_operations import (
    MonitorOperationError,
    MonitorOperationService,
)
from ai_market_monitor.services.payment_emails import PaymentEmailRenderer
from ai_market_monitor.services.sharia_passports import ShariaPassportReadService
from ai_market_monitor.services.sharia_screening import (
    AGGREGATE_METHODOLOGY_CODE,
    DEFAULT_ALLOWED_STATUSES,
    ShariaScreeningError,
    ShariaScreeningService,
    canonical_asset,
    methodology_is_development_only,
    sharia_evidence_from_proof,
)
from ai_market_monitor.services.telegram_account_links import (
    TelegramAccountLinkError,
    TelegramAccountLinkService,
)
from ai_market_monitor.services.template_catalog import builtin_template_payloads
from ai_market_monitor.services.trials import TrialError, TrialLifecycleService
from ai_market_monitor.services.verified_strategy import seal_alert_proof
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


def _plan_limit(value: object) -> str:
    if isinstance(value, int) and value >= 100_000:
        return "Unlimited"
    return str(value)


templates.env.filters["short_dt"] = _short_datetime
templates.env.filters["reward_amount"] = _reward_amount
templates.env.filters["plan_limit"] = _plan_limit

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


def _timezone_options(at: datetime | None = None) -> list[dict[str, str]]:
    instant = at or datetime.now(UTC)
    options: list[dict[str, str]] = []
    for timezone_name in SUPPORTED_TIMEZONES:
        offset = instant.astimezone(ZoneInfo(timezone_name)).utcoffset() or timedelta()
        total_minutes = int(offset.total_seconds() // 60)
        sign = "+" if total_minutes >= 0 else "-"
        hours, minutes = divmod(abs(total_minutes), 60)
        options.append(
            {
                "value": timezone_name,
                "label": f"{timezone_name} (UTC{sign}{hours:02d}:{minutes:02d})",
            }
        )
    return options


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


def _subscription_selection(
    plan_code: str | None,
    billing_interval: str | None,
) -> tuple[str | None, Literal["monthly", "annual"]]:
    selected_plan = plan_code if plan_code in PUBLIC_PLAN_CODES else None
    selected_interval: Literal["monthly", "annual"] = (
        "annual" if billing_interval == "annual" else "monthly"
    )
    return selected_plan, selected_interval


def _subscription_query(
    plan_code: str | None,
    billing_interval: str | None,
) -> dict[str, str]:
    selected_plan, selected_interval = _subscription_selection(
        plan_code,
        billing_interval,
    )
    if selected_plan is None:
        return {}
    return {
        "plan_code": selected_plan,
        "billing_interval": selected_interval,
    }


def _subscription_destination(
    settings: Settings,
    *,
    plan_code: str | None,
    billing_interval: str | None,
    default_message: str,
) -> str:
    selected_plan, selected_interval = _subscription_selection(
        plan_code,
        billing_interval,
    )
    if selected_plan is None or selected_plan == "demo":
        return f"/dashboard?message={default_message}"
    query = {
        "selected_plan": selected_plan,
        "billing_interval": selected_interval,
        "checkout": "1",
    }
    if (
        selected_plan == "trader"
        and selected_interval == "monthly"
        and _billing_selection_available(
            settings,
            provider=_billing_method_provider(settings, "card"),
            plan_code="trader",
            billing_cycle="trial_7_day",
        )
    ):
        query["trial"] = "1"
        return f"/dashboard/billing?{urlencode(query)}"
    if not settings.billing_enabled:
        query["error"] = "billing_disabled"
        return f"/dashboard/billing?{urlencode(query)}"
    return f"/dashboard/billing?{urlencode(query)}"


def _optional_uuid(value: str | None, *, label: str) -> UUID | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    try:
        return UUID(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Choose a valid {label}.") from exc


def _billing_method_provider(settings: Settings, payment_method: str) -> str | None:
    try:
        return configured_billing_provider(settings, payment_method)
    except BillingError:
        return None


def _billing_selection_available(
    settings: Settings,
    *,
    provider: str | None,
    plan_code: str,
    billing_cycle: str,
) -> bool:
    if not settings.billing_enabled or provider is None:
        return False
    if billing_cycle == "trial_7_day" and provider != "creem":
        return False
    if provider == "creem":
        if settings.creem_api_key is None or settings.creem_webhook_secret is None:
            return False
        if (
            not settings.creem_api_key.get_secret_value().strip()
            or not settings.creem_webhook_secret.get_secret_value().strip()
        ):
            return False
        key = (
            f"{plan_code}_trial"
            if billing_cycle == "trial_7_day"
            else f"{plan_code}_{billing_cycle}"
        )
        return key in settings.creem_product_ids
    if provider == "stripe":
        if settings.stripe_secret_key is None:
            return False
        return (
            f"{plan_code}_{billing_cycle}" in settings.stripe_price_ids
            or plan_code in settings.stripe_price_ids
        )
    if provider == "nowpayments":
        return (
            billing_cycle == "monthly"
            and settings.nowpayments_api_key is not None
            and settings.nowpayments_ipn_secret is not None
            and bool(settings.nowpayments_api_key.get_secret_value().strip())
            and bool(settings.nowpayments_ipn_secret.get_secret_value().strip())
        )
    return provider == "static" and not settings.is_deployed


async def _active_paid_plan_codes(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> frozenset[str]:
    """Return provider-backed active plans, never administrative access."""
    active_codes = list(
        (
            await session.scalars(
                select(Plan.code)
                .join(Subscription, Subscription.plan_id == Plan.id)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.provider.notin_(("admin", "free", "trial")),
                    Subscription.status.in_(
                        (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING)
                    ),
                    (Subscription.current_period_end.is_(None))
                    | (Subscription.current_period_end > datetime.now(UTC)),
                )
                .order_by(Subscription.updated_at.desc())
            )
        ).all()
    )
    return frozenset(active_codes)


def _plan_checkout_allowed(
    *, plan_code: str, active_paid_plan_codes: frozenset[str]
) -> bool:
    return plan_code not in active_paid_plan_codes


def _billing_history_rows(
    attempts: list[BillingCheckoutAttempt],
    plans: dict[UUID, Plan],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for attempt in attempts:
        expires_at = attempt.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        can_resume = (
            attempt.status == "pending"
            and bool(attempt.checkout_url)
            and bool(attempt.provider_session_id)
            and expires_at > now
        )
        plan = plans.get(attempt.plan_id)
        rows.append(
            {
                "attempt": attempt,
                "plan_name": plan.name if plan is not None else "Unavailable plan",
                "can_resume": can_resume,
                "resume_url": f"/dashboard/billing/checkout/{attempt.id}/resume"
                if can_resume
                else None,
            }
        )
    return rows


def _billing_profile(
    *,
    first_name: str,
    last_name: str,
    address_line1: str,
    address_line2: str,
    city: str,
    region: str,
    postal_code: str,
    country: str,
) -> dict[str, str]:
    raw = {
        "first_name": (first_name, 60, True),
        "last_name": (last_name, 60, True),
        "address_line1": (address_line1, 200, True),
        "address_line2": (address_line2, 200, False),
        "city": (city, 100, False),
        "region": (region, 100, False),
        "postal_code": (postal_code, 24, False),
        "country": (country, 80, True),
    }
    profile: dict[str, str] = {}
    for key, (value, limit, required) in raw.items():
        normalized = " ".join(value.strip().split())
        if required and not normalized:
            raise BillingError(
                "billing_profile_incomplete",
                "Complete your name, billing address, and country.",
            )
        if len(normalized) > limit or any(ord(character) < 32 for character in normalized):
            raise BillingError(
                "billing_profile_invalid",
                "One or more billing details are invalid.",
            )
        if normalized:
            profile[key] = normalized
    return profile


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
        pending_repair = None
        dynamic_extensions = []
        if strategy.active_version_id:
            dynamic_extensions = list(
                (
                    await session.scalars(
                        select(CapabilityExtension)
                        .where(
                            CapabilityExtension.strategy_version_id
                            == strategy.active_version_id,
                            CapabilityExtension.artifact_hash.is_not(None),
                        )
                        .order_by(CapabilityExtension.created_at)
                    )
                ).all()
            )
            pending_repair = await session.scalar(
                select(CapabilityExtension).where(
                    CapabilityExtension.strategy_version_id == strategy.active_version_id,
                    CapabilityExtension.status == "repair_ready",
                )
            )
        sharia_universe = None
        methodology = None
        eligible_asset_count = 0
        policy_version_id = strategy.active_version_id
        if policy_version_id is None:
            policy_version_id = await session.scalar(
                select(StrategyVersion.id)
                .where(StrategyVersion.strategy_id == strategy.id)
                .order_by(StrategyVersion.version_number.desc())
                .limit(1)
            )
        if policy_version_id:
            sharia_universe = await session.scalar(
                select(StrategyUniverse).where(
                    StrategyUniverse.strategy_version_id == policy_version_id
                )
            )
            if sharia_universe and sharia_universe.methodology_id:
                methodology = await session.get(ShariaMethodology, sharia_universe.methodology_id)
                eligible_asset_count = int(
                    await session.scalar(
                        select(func.count(MonitorShariaAssetState.id)).where(
                            MonitorShariaAssetState.strategy_version_id == policy_version_id,
                            MonitorShariaAssetState.state == MonitorShariaAssetStatus.ACTIVE,
                        )
                    )
                    or 0
                )
        monitor_cards.append(
            {
                "strategy": strategy,
                "health": health,
                "main_bottleneck": bottlenecks.get("main_bottleneck"),
                "latest_scan": latest_scan,
                "latency_label": latency_label,
                "pending_repair": pending_repair,
                "dynamic_extensions": dynamic_extensions,
                "sharia_universe": sharia_universe,
                "methodology": methodology,
                "eligible_asset_count": eligible_asset_count,
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
                menu=[
                    "My Monitors",
                    "Lifecycles",
                    "Trial",
                    "Pricing",
                    "Settings",
                    "Support",
                    "About",
                ],
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
    entitlement = None
    unread_notification_count = 0
    if user is not None:
        dashboard_preference = await session.scalar(
            select(DashboardPreference).where(DashboardPreference.user_id == user.id)
        )
        entitlement = await EntitlementService(session).current(user.id)
        dashboard_unread = int(
            await session.scalar(
                select(func.count(DashboardNotification.id)).where(
                    DashboardNotification.user_id == user.id,
                    DashboardNotification.read_at.is_(None),
                )
            )
            or 0
        )
        pending_alerts = int(
            await session.scalar(
                select(func.count(AlertDelivery.id))
                .join(Alert, Alert.id == AlertDelivery.alert_id)
                .where(
                    Alert.user_id == user.id,
                    AlertDelivery.channel == DeliveryChannel.WEB,
                    AlertDelivery.status == DeliveryStatus.PENDING,
                )
            )
            or 0
        )
        unread_notification_count = dashboard_unread + pending_alerts
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
    selected_plan_code, selected_billing_interval = _subscription_selection(
        request.query_params.get("plan_code"),
        request.query_params.get("billing_interval"),
    )
    auth_query = _subscription_query(selected_plan_code, selected_billing_interval)
    if request.query_params.get("telegram_link"):
        auth_query["telegram_link"] = request.query_params["telegram_link"]
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
        "dashboard_navigation": DASHBOARD_NAVIGATION,
        "dashboard_preference": dashboard_preference,
        "entitlement": entitlement,
        "whatsapp_plan_included": bool(
            entitlement and entitlement.feature_enabled("whatsapp")
        ),
        "whatsapp_available": bool(
            entitlement
            and entitlement.feature_enabled("whatsapp")
            and settings.whatsapp_enabled
        ),
        "unread_notification_count": unread_notification_count,
        "dashboard_theme": dashboard_theme,
        "dashboard_csrf_token": csrf_token(settings, user.id) if user else None,
        "selected_plan_code": selected_plan_code,
        "selected_billing_interval": selected_billing_interval,
        "auth_link_suffix": f"?{urlencode(auth_query)}" if auth_query else "",
        **extra,
    }


async def _builder_screening_context(
    session: AsyncSession,
    user: User,
    settings: Settings,
) -> dict:
    preference = await session.scalar(
        select(DashboardPreference).where(DashboardPreference.user_id == user.id)
    )
    values = dict(preference.notification_preferences or {}) if preference else {}
    raw_stored_policy = values.get("sharia")
    stored_policy: dict[str, Any] = (
        raw_stored_policy if isinstance(raw_stored_policy, dict) else {}
    )
    screening = ShariaScreeningService(session, settings)
    methodology = None
    configured_id = stored_policy.get("default_methodology_id") or values.get(
        "default_sharia_methodology_id"
    )
    if configured_id:
        try:
            methodology = await screening.methodology(
                UUID(str(configured_id)),
                require_active=True,
            )
        except (ValueError, ShariaScreeningError):
            methodology = None
    if methodology is None:
        candidate = await screening.default_methodology()
        if candidate is not None:
            try:
                methodology = await screening.methodology(
                    candidate.id,
                    require_active=True,
                )
            except ShariaScreeningError:
                methodology = None
    valid_statuses = {item.value for item in ShariaAssetStatus}
    allowed_statuses = [
        value
        for value in stored_policy.get(
            "allowed_statuses",
            values.get(
                "allowed_sharia_statuses",
                [
                    ShariaAssetStatus.ELIGIBLE.value,
                    ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS.value,
                ],
            ),
        )
        if value in valid_statuses
    ]
    advanced_ack = bool(
        stored_policy.get(
            "advanced_override_acknowledged",
            values.get("advanced_sharia_override_acknowledged", False),
        )
    )
    default_statuses = {
        ShariaAssetStatus.ELIGIBLE.value,
        ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS.value,
    }
    if not allowed_statuses or (
        not set(allowed_statuses).issubset(default_statuses) and not advanced_ack
    ):
        allowed_statuses = sorted(default_statuses)
        advanced_ack = False
    try:
        behavior = ComplianceChangeBehavior(
            stored_policy.get(
                "compliance_change_behavior",
                values.get(
                    "compliance_change_behavior",
                    ComplianceChangeBehavior.PAUSE_ASSET.value,
                ),
            )
        )
    except ValueError:
        behavior = ComplianceChangeBehavior.PAUSE_ASSET
    return {
        "enforced": settings.sharia_screening_enforced,
        "configured": methodology is not None,
        "methodology_name": methodology.name if methodology else None,
        "methodology_version": methodology.version if methodology else None,
        "policy": (
            {
                "universe_mode": "eligible_market",
                "methodology_id": str(methodology.id) if methodology else None,
                "allowed_statuses": allowed_statuses,
                "qualification_policy": "include_with_warning",
                "disputed_asset_policy": "exclude",
                "compliance_change_behavior": behavior.value,
                "approved_watchlist_id": None,
                "universe_snapshot_version": None,
                "universe_last_resolved_at": None,
                "advanced_override_acknowledged": advanced_ack,
            }
            if settings.sharia_screening_enforced
            else None
        ),
    }


@router.get("/subscribe", include_in_schema=False)
async def subscribe(
    request: Request,
    plan_code: str = Query(..., min_length=1, max_length=20),
    billing_interval: str = Query(default="monthly", min_length=1, max_length=20),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    selected_plan, selected_interval = _subscription_selection(
        plan_code,
        billing_interval,
    )
    if selected_plan is None:
        return _redirect("/#pricing")
    user = await _current_user(request, session, settings)
    if user is None:
        selection_query = urlencode(
            {
                "plan_code": selected_plan,
                "billing_interval": selected_interval,
            }
        )
        return _redirect(f"/signup?{selection_query}")
    return _redirect(
        _subscription_destination(
            settings,
            plan_code=selected_plan,
            billing_interval=selected_interval,
            default_message="plan_selected",
        )
    )


@router.get("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return _no_store(
        templates.TemplateResponse(
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
        )
    )


@router.post("/signup", include_in_schema=False)
async def signup_submit(
    request: Request,
    first_name: str = Form(default=""),
    last_name: str = Form(default=""),
    display_name: str | None = Form(default=None),
    email: str = Form(...),
    password: str = Form(...),
    repeat_password: str = Form(...),
    telegram_link: str | None = Form(None),
    plan_code: str | None = Form(None),
    billing_interval: str | None = Form(None),
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
                first_name=first_name,
                last_name=last_name,
                display_name=display_name,
                telegram_link=telegram_link,
                requested_ip=request.client.host if request.client else None,
            )
            await session.commit()
        except (WebAuthError, EmailDeliveryError) as exc:
            await session.rollback()
            code = getattr(exc, "code", "signup_failed")
            if code == "code_recently_sent":
                query = {"message": "code_sent", "email": email}
                query.update(_subscription_query(plan_code, billing_interval))
                if telegram_link:
                    query["telegram_link"] = telegram_link
                return _redirect(f"/signup/verify?{urlencode(query)}")
            query = {"error": code}
            query.update(_subscription_query(plan_code, billing_interval))
            if telegram_link:
                query["telegram_link"] = telegram_link
            return _redirect(f"/signup?{urlencode(query)}")

    query = {"message": "code_sent", "email": email}
    query.update(_subscription_query(plan_code, billing_interval))
    if telegram_link:
        query["telegram_link"] = telegram_link
    return _redirect(f"/signup/verify?{urlencode(query)}")


@router.get("/signup/verify", response_class=HTMLResponse, include_in_schema=False)
async def signup_verify_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return _no_store(
        templates.TemplateResponse(
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
        )
    )


@router.post("/signup/verify", include_in_schema=False)
async def signup_verify_submit(
    request: Request,
    email: str = Form(...),
    code: str = Form(...),
    telegram_link: str | None = Form(None),
    plan_code: str | None = Form(None),
    billing_interval: str | None = Form(None),
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
        query.update(_subscription_query(plan_code, billing_interval))
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
    response = _redirect(
        _subscription_destination(
            settings,
            plan_code=plan_code,
            billing_interval=billing_interval,
            default_message=message,
        )
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


@router.post("/signin", include_in_schema=False)
async def signin_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    telegram_link: str | None = Form(None),
    plan_code: str | None = Form(None),
    billing_interval: str | None = Form(None),
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
        query = {"error": code}
        query.update(_subscription_query(plan_code, billing_interval))
        if telegram_link:
            query["telegram_link"] = telegram_link
        return _redirect(f"/signin?{urlencode(query)}")
    response = _redirect(
        _subscription_destination(
            settings,
            plan_code=plan_code,
            billing_interval=billing_interval,
            default_message=(
                "telegram_connected" if telegram_connected else "login_successful"
            ),
        )
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
    return _no_store(
        templates.TemplateResponse(
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
        )
    )


@router.get("/signin/code", response_class=HTMLResponse, include_in_schema=False)
async def signin_code_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return _no_store(
        templates.TemplateResponse(
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
        )
    )


@router.post("/signin/code/request", include_in_schema=False)
async def signin_code_request(
    request: Request,
    email: str = Form(...),
    telegram_link: str | None = Form(None),
    plan_code: str | None = Form(None),
    billing_interval: str | None = Form(None),
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
        query = {"error": getattr(exc, "code", "email_unavailable")}
        query.update(_subscription_query(plan_code, billing_interval))
        if telegram_link:
            query["telegram_link"] = telegram_link
        return _redirect(f"/signin/code?{urlencode(query)}")
    query = {"message": "code_sent", "email": email}
    query.update(_subscription_query(plan_code, billing_interval))
    if telegram_link:
        query["telegram_link"] = telegram_link
    return _redirect(f"/signin/code?{urlencode(query)}")


@router.post("/signin/code/verify", include_in_schema=False)
async def signin_code_verify(
    request: Request,
    email: str = Form(...),
    code: str = Form(...),
    telegram_link: str | None = Form(None),
    plan_code: str | None = Form(None),
    billing_interval: str | None = Form(None),
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
        query.update(_subscription_query(plan_code, billing_interval))
        if telegram_link:
            query["telegram_link"] = telegram_link
        return _redirect(f"/signin/code?{urlencode(query)}")
    await _send_telegram_connected_notification(session, settings, linked_telegram_user_id)
    response = _redirect(
        _subscription_destination(
            settings,
            plan_code=plan_code,
            billing_interval=billing_interval,
            default_message=(
                "telegram_connected" if telegram_connected else "login_successful"
            ),
        )
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
    return _no_store(
        templates.TemplateResponse(
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
        )
    )


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
        return _redirect(f"/reset-password?{urlencode({'error': exc.code, 'email': email})}")
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
    latest_setup_asset = None
    if latest_setup is not None:
        latest_setup_asset = await session.scalar(
            select(CanonicalAsset)
            .where(CanonicalAsset.symbol == canonical_asset(latest_setup.symbol))
            .order_by(CanonicalAsset.created_at.desc())
            .limit(1)
        )
    telegram = await session.scalar(
        select(TelegramConnection).where(TelegramConnection.user_id == user.id)
    )
    coverage = await market_coverage_for_user(session, user.id)
    trial = await session.scalar(select(Trial).where(Trial.user_id == user.id))
    entitlement = await EntitlementService(session).current(user.id)
    screening = ShariaScreeningService(session, settings)
    screened_home = await screening.list_screened_assets(
        methodology_id=None,
        statuses=DEFAULT_ALLOWED_STATUSES,
        page=1,
        limit=1,
    )
    forming_screened = int(
        await session.scalar(
            select(func.count(SetupInstance.id)).where(
                SetupInstance.user_id == user.id,
                SetupInstance.state.not_in(
                    ["expired", "invalidated", "completed", "closed", "manually_closed"]
                ),
                SetupInstance.sharia_status_at_detection.in_(
                    [
                        ShariaAssetStatus.ELIGIBLE.value,
                        ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS.value,
                    ]
                ),
            )
        )
        or 0
    )
    compliance_attention = int(
        await session.scalar(
            select(func.count(ComplianceDriftNotification.id)).where(
                ComplianceDriftNotification.user_id == user.id,
                ComplianceDriftNotification.created_at >= datetime.now(UTC) - timedelta(days=30),
                ComplianceDriftNotification.new_status.in_(
                    [ShariaAssetStatus.UNDER_REVIEW, ShariaAssetStatus.EXCLUDED]
                ),
            )
        )
        or 0
    )
    overview = {
        "alerts_today": alerts_today,
        "active_lifecycle_count": active_lifecycle_count,
        "latest_setup": latest_setup,
        "latest_setup_asset_symbol": (
            latest_setup_asset.symbol if latest_setup_asset is not None else None
        ),
        "latest_setup_logo_module_url": (
            "https://cdn.jsdelivr.net/npm/@web3icons/core@4.0.53/"
            f"dist/svgs/tokens/branded/{latest_setup_asset.symbol.upper()}.svg.js"
            if latest_setup_asset is not None
            else None
        ),
        "latest_setup_logo_url": (
            str((latest_setup_asset.provider_ids or {}).get("logo_url") or "").strip()
            if latest_setup_asset is not None
            else None
        ),
        "latest_alert": _alert_view(latest_alert) if latest_alert else None,
        "coverage": coverage,
        "telegram_connected": bool(
            telegram
            and telegram.status == ConnectionStatus.ACTIVE
            and telegram.alerts_enabled
        ),
        "eligible_market_count": screened_home.total,
        "screening_methodology": screened_home.methodology,
        "forming_screened_count": forming_screened,
        "compliance_attention_count": compliance_attention,
    }
    return templates.TemplateResponse(
        request,
        "hilal/dashboard/home.html",
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


@router.get("/dashboard/market", response_class=HTMLResponse, include_in_schema=False)
async def screened_market_page(
    request: Request,
    methodology_id_input: str | None = Query(
        default=None,
        alias="methodology_id",
        max_length=64,
    ),
    status_filter: list[ShariaAssetStatus] | None = Query(default=None, alias="status"),
    exchange: str | None = Query(default=None, max_length=40),
    quote_asset: str = Query(default="USDT", max_length=12),
    liquidity: float | None = Query(default=None, ge=0),
    search: str | None = Query(default=None, max_length=120),
    view: str = Query(default="assets", pattern="^(opportunities|assets)$"),
    page_number: int = Query(default=1, ge=1, alias="page"),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    provider: MarketDataProvider = Depends(get_market_data_provider),
) -> HTMLResponse:
    # Opportunities have one authoritative home in Opportunities & Evidence.
    # Retain the old query value for compatible links, but render the market itself.
    view = "assets"
    methodology_id = _optional_uuid(methodology_id_input, label="methodology")
    selected_live_exchange = (exchange or settings.market_data_exchange).lower()
    if selected_live_exchange not in {"binance", "bybit"}:
        selected_live_exchange = "binance"
    screening = ShariaScreeningService(session, settings)
    methodologies = await screening.selectable_market_methodologies()
    preference = await session.scalar(
        select(DashboardPreference).where(DashboardPreference.user_id == user.id)
    )
    raw_preference_values = preference.notification_preferences if preference else None
    preference_values = dict(raw_preference_values or {})
    raw_stored_policy = preference_values.get("sharia")
    stored_policy: dict[str, Any] = (
        raw_stored_policy if isinstance(raw_stored_policy, dict) else {}
    )
    preference_methodology = stored_policy.get("default_methodology_id") or preference_values.get(
        "default_sharia_methodology_id"
    )
    # The Halal Market starts with the explicit All methodology. A saved
    # user preference remains useful after a user deliberately picks another
    # methodology, but it must not silently replace the product default.
    if methodology_id is None and not (methodology_id_input or "").strip():
        aggregate = next(
            (
                item
                for item in methodologies
                if item.code == AGGREGATE_METHODOLOGY_CODE
            ),
            None,
        )
        methodology_id = aggregate.id if aggregate is not None else None
    if methodology_id is None and preference_methodology:
        try:
            methodology_id = UUID(str(preference_methodology))
        except ValueError:
            methodology_id = None
    if methodology_id is not None:
        selected_row = await session.get(ShariaMethodology, methodology_id)
        if selected_row is not None and methodology_is_development_only(selected_row):
            methodology_id = None
    asset_scope: set[str] | None = None
    market_data_warning: str | None = None
    try:
        symbols = await provider.list_symbols(
            selected_live_exchange,
            [quote_asset.upper()],
        )
        if liquidity is not None:
            metadata_loader = getattr(provider, "fetch_universe_metadata", None)
            if not callable(metadata_loader):
                market_data_warning = (
                    "The configured provider cannot verify the selected liquidity filter."
                )
                symbols = []
            else:
                metadata = await metadata_loader(
                    selected_live_exchange,
                    symbols,
                )
                symbols = [
                    symbol
                    for symbol in symbols
                    if (metadata.get(symbol.upper(), {}).get("quote_volume_24h") or 0)
                    >= liquidity
                ]
        asset_scope = {canonical_asset(symbol) for symbol in symbols}
    except Exception:
        asset_scope = set()
        market_data_warning = (
            "Exchange market data is currently unavailable. No asset was guessed or "
            "silently included."
        )
    screened = await screening.list_screened_assets(
        methodology_id=methodology_id,
        statuses=set(status_filter) if status_filter else DEFAULT_ALLOWED_STATUSES,
        search=search,
        asset_scope=asset_scope,
        page=page_number,
        limit=30,
    )
    active_watch_plans = int(
        await session.scalar(
            select(func.count(Strategy.id)).where(
                Strategy.user_id == user.id,
                Strategy.status == StrategyStatus.ACTIVE,
                Strategy.archived_at.is_(None),
            )
        )
        or 0
    )
    selected_methodology_id = screened.methodology.id if screened.methodology else methodology_id
    status_changes = 0
    if selected_methodology_id:
        status_changes = int(
            await session.scalar(
                select(func.count(AssetShariaStatusHistory.id)).where(
                    AssetShariaStatusHistory.methodology_id == selected_methodology_id,
                    AssetShariaStatusHistory.changed_at >= datetime.now(UTC) - timedelta(days=7),
                )
            )
            or 0
        )
    watchlists = list(
        (
            await session.scalars(
                select(ApprovedWatchlist)
                .where(ApprovedWatchlist.user_id == user.id)
                .order_by(ApprovedWatchlist.is_default.desc(), ApprovedWatchlist.name.asc())
            )
        ).all()
    )
    saved_asset_rows = list(
        (
            await session.execute(
                select(ApprovedWatchlistAsset, ApprovedWatchlist.name)
                .join(
                    ApprovedWatchlist,
                    ApprovedWatchlist.id == ApprovedWatchlistAsset.watchlist_id,
                )
                .where(ApprovedWatchlist.user_id == user.id)
                .order_by(ApprovedWatchlistAsset.added_at.desc())
            )
        ).all()
    )
    saved_assets = [
        {
            "watchlist_id": row.watchlist_id,
            "canonical_asset": row.canonical_asset,
            "added_at": row.added_at,
            "watchlist_name": watchlist_name,
        }
        for row, watchlist_name in saved_asset_rows
    ]
    default_watchlist = next((item for item in watchlists if item.is_default), None)
    favorite_assets = sorted(
        {
            item["canonical_asset"]
            for item in saved_assets
            if default_watchlist is not None
            and item["watchlist_id"] == default_watchlist.id
        }
    )
    market_query: list[tuple[str, str]] = [("view", view), ("quote_asset", quote_asset)]
    if methodology_id:
        market_query.append(("methodology_id", str(methodology_id)))
    for selected_status in status_filter or []:
        market_query.append(("status", selected_status.value))
    if exchange:
        market_query.append(("exchange", exchange))
    if liquidity is not None:
        market_query.append(("liquidity", str(liquidity)))
    if search:
        market_query.append(("search", search))
    maximum_page = max(1, (screened.total + screened.limit - 1) // screened.limit)

    def market_page_url(target_page: int) -> str:
        return "/dashboard/market?" + urlencode([*market_query, ("page", str(target_page))])

    return templates.TemplateResponse(
        request,
        "hilal/dashboard/market.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="screened_market",
            title="Halal Market",
            screened=screened,
            methodologies=methodologies,
            selected_methodology_id=selected_methodology_id,
            opportunity_cards=[],
            active_watch_plans=active_watch_plans,
            status_changes=status_changes,
            selected_statuses={
                item.value for item in (status_filter or list(DEFAULT_ALLOWED_STATUSES))
            },
            selected_exchange=selected_live_exchange,
            selected_quote_asset=quote_asset.upper(),
            selected_liquidity=liquidity,
            selected_view=view,
            market_search=search or "",
            market_data_warning=market_data_warning,
            watchlists=watchlists,
            saved_assets=saved_assets,
            favorite_assets=favorite_assets,
            favorite_watchlist_id=(default_watchlist.id if default_watchlist else None),
            market_previous_url=(market_page_url(screened.page - 1) if screened.page > 1 else None),
            market_next_url=(
                market_page_url(screened.page + 1) if screened.page < maximum_page else None
            ),
            market_maximum_page=maximum_page,
        ),
    )


@router.get(
    "/dashboard/market/{asset_slug}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def screened_asset_passport_page(
    request: Request,
    asset_slug: str,
    methodology_id: UUID | None = Query(default=None),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    screening = ShariaScreeningService(session, settings)
    try:
        passport = await ShariaPassportReadService(session, settings).current(
            asset_slug,
            methodology_id=methodology_id,
            user_id=user.id,
        )
    except ShariaScreeningError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    comparison = await screening.methodology_comparison(asset_slug)
    watchlists = list(
        (
            await session.scalars(
                select(ApprovedWatchlist)
                .where(ApprovedWatchlist.user_id == user.id)
                .order_by(ApprovedWatchlist.is_default.desc(), ApprovedWatchlist.name.asc())
            )
        ).all()
    )
    return templates.TemplateResponse(
        request,
        "hilal/dashboard/passport.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="asset_passport",
            title=f"{passport.assessment.canonical_asset} Evidence Passport",
            passport=passport,
            methodology_comparison=comparison,
            watchlists=watchlists,
        ),
    )


@router.get(
    "/passports/{canonical_asset_id}/versions/{passport_version_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def historical_asset_passport_page(
    request: Request,
    canonical_asset_id: UUID,
    passport_version_id: UUID,
    event_time: datetime | None = Query(default=None),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    screening = ShariaScreeningService(session, settings)
    try:
        passport = await ShariaPassportReadService(session, settings).historical(
            canonical_asset_id=canonical_asset_id,
            passport_version_id=passport_version_id,
            event_time=event_time,
            user_id=user.id,
        )
    except ShariaScreeningError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    comparison = await screening.methodology_comparison(passport.assessment.canonical_asset)
    watchlists = list(
        (
            await session.scalars(
                select(ApprovedWatchlist)
                .where(ApprovedWatchlist.user_id == user.id)
                .order_by(
                    ApprovedWatchlist.is_default.desc(),
                    ApprovedWatchlist.name.asc(),
                )
            )
        ).all()
    )
    return templates.TemplateResponse(
        request,
        "hilal/dashboard/passport.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="asset_passport",
            title=f"{passport.assessment.canonical_asset} Historical Evidence Passport",
            passport=passport,
            methodology_comparison=comparison,
            watchlists=watchlists,
        ),
    )


@router.post("/dashboard/market/{asset_slug}/watchlist", include_in_schema=False)
async def add_screened_asset_to_watchlist(
    asset_slug: str,
    watchlist_id: UUID | None = Form(default=None),
    methodology_id: UUID | None = Form(default=None),
    response_format: Literal["html", "json"] = Query(default="html", alias="format"),
    x_csrf_token: str | None = Header(default=None),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    if response_format == "json" and not csrf_token_matches(
        settings,
        user.id,
        x_csrf_token,
    ):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    screening = ShariaScreeningService(session, settings)
    try:
        methodology = await screening.resolve_methodology(methodology_id)
    except ShariaScreeningError:
        if response_format == "json":
            return JSONResponse(
                status_code=409,
                content={
                    "detail": {
                        "code": "approved_methodology_required",
                        "message": (
                            "A current approved methodology is required to follow this asset."
                        ),
                    }
                },
            )
        return _redirect(f"/dashboard/market/{asset_slug}?error=approved_methodology_required")
    assessment = await screening.effective_assessment(methodology.id, asset_slug)
    if assessment is None or assessment.status not in DEFAULT_ALLOWED_STATUSES:
        if response_format == "json":
            return JSONResponse(
                status_code=409,
                content={
                    "detail": {
                        "code": "asset_not_eligible",
                        "message": (
                            "This asset is not currently available to follow under the selected "
                            "methodology."
                        ),
                    }
                },
            )
        return _redirect(f"/dashboard/market/{asset_slug}?error=asset_not_eligible")
    watchlist = await session.get(ApprovedWatchlist, watchlist_id) if watchlist_id else None
    if watchlist is not None and watchlist.user_id != user.id:
        raise HTTPException(status_code=404, detail="Approved watchlist not found")
    if watchlist is None:
        watchlist = await session.scalar(
            select(ApprovedWatchlist)
            .where(
                ApprovedWatchlist.user_id == user.id,
                ApprovedWatchlist.is_default.is_(True),
            )
            .limit(1)
        )
    if watchlist is None:
        watchlist = ApprovedWatchlist(
            user_id=user.id,
            name="My approved watchlist",
            is_default=True,
        )
        session.add(watchlist)
        await session.flush()
    asset = canonical_asset(asset_slug)
    existing = await session.scalar(
        select(ApprovedWatchlistAsset.id).where(
            ApprovedWatchlistAsset.watchlist_id == watchlist.id,
            ApprovedWatchlistAsset.canonical_asset == asset,
        )
    )
    if existing is None:
        session.add(
            ApprovedWatchlistAsset(
                watchlist_id=watchlist.id,
                canonical_asset=asset,
                added_at=datetime.now(UTC),
            )
        )
    await session.commit()
    if response_format == "json":
        return JSONResponse(
            {
                "canonical_asset": asset,
                "watchlist_id": str(watchlist.id),
                "favorite": True,
                "status_change_following": True,
            }
        )
    return _redirect(f"/dashboard/market/{asset}?message=added_to_approved_watchlist")


@router.get(
    "/dashboard/saved-assets",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="saved_assets_page",
)
@router.get(
    "/dashboard/watchlist",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="approved_watchlist_page",
)
async def approved_watchlist_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Render the user's persisted screened-asset watchlists.

    The page intentionally reads only user-owned saved assets. Eligibility is
    shown from the linked evidence passport instead of being recreated from
    display-only client data.
    """
    del request, user, session, settings
    return _redirect("/dashboard/market?saved_assets=1")


@router.get("/dashboard/compliance", response_class=HTMLResponse, include_in_schema=False)
async def compliance_changes_page(
    request: Request,
    asset: str | None = None,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Show persisted, user-scoped screening status changes."""
    del request, user, session, settings
    query_values = [("tab", "compliance_changes")]
    if asset:
        query_values.append(("symbol", canonical_asset(asset)))
    return _redirect(f"/dashboard/activity?{urlencode(query_values)}")


@router.get("/dashboard/methodology", response_class=HTMLResponse, include_in_schema=False)
async def methodology_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    del request, user, session, settings
    return _redirect("/how-we-screen")


@router.get(
    "/dashboard/watchlists",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="watchlists_page",
)
@router.get(
    "/dashboard/strategies",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="monitors_page",
)
@router.get(
    "/dashboard/monitors",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="legacy_monitors_page",
)
async def monitors_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "hilal/dashboard/watch_plans.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="watchlists",
            title="Watchlists",
            monitor_cards=await _monitor_cards_context(session, user),
        ),
    )


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
        "hilal/dashboard/builder.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page=(
                "check_market"
                if request.query_params.get("mode") == "scanner"
                else "strategy_builder"
            ),
            title="Strategy Builder",
            strategy=None,
            version=None,
            templates=templates_list,
            builtin_templates=builtin_template_payloads(),
            monitor_cards=await _monitor_cards_context(session, user),
            builder_screening=await _builder_screening_context(session, user, settings),
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
        setups_count = int(
            await session.scalar(
                select(func.count(SetupInstance.id)).where(
                    SetupInstance.strategy_version_id.in_([version.id for version in versions])
                )
            )
            or 0
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
        "hilal/dashboard/strategy_detail.html",
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
            builder_screening=await _builder_screening_context(session, user, settings),
        ),
    )


@router.get(
    "/dashboard/strategies/{strategy_id}/verify",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def verified_strategy_page(
    request: Request,
    strategy_id: UUID,
    version_id: UUID | None = Query(default=None, alias="version"),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    strategy = await session.get(Strategy, strategy_id)
    if strategy is None or strategy.user_id != user.id:
        raise HTTPException(status_code=404, detail="Strategy not found")
    version = (
        await session.get(StrategyVersion, version_id)
        if version_id
        else await session.scalar(
            select(StrategyVersion)
            .where(StrategyVersion.strategy_id == strategy.id)
            .order_by(StrategyVersion.version_number.desc())
            .limit(1)
        )
    )
    if version is None or version.strategy_id != strategy.id:
        raise HTTPException(status_code=404, detail="Strategy version not found")
    return templates.TemplateResponse(
        request,
        "hilal/dashboard/strategy_verify.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="strategy_verify",
            title=f"Verify {strategy.name}",
            strategy=strategy,
            version=version,
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
        "hilal/dashboard/builder.html",
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
            builder_screening=await _builder_screening_context(session, user, settings),
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
        "hilal/dashboard/strategy_versions.html",
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
    settings: Settings = Depends(get_settings),
    previewer: RecentMarketPreviewer = Depends(get_market_previewer),
) -> RedirectResponse:
    try:
        await MonitorOperationService(
            session,
            settings=settings,
            previewer=previewer,
        ).resume(
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


@router.post(
    "/dashboard/capability-extensions/{extension_id}/prepare-repair",
    include_in_schema=False,
)
async def prepare_capability_repair(
    extension_id: UUID,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    extension = await session.get(CapabilityExtension, extension_id)
    if extension is None or extension.user_id != user.id:
        raise HTTPException(status_code=404, detail="Certified repair not found")
    try:
        strategy, _version = await CapabilityExtensionService(
            settings
        ).materialize_pending_revision(
            session,
            extension=extension,
            user_id=user.id,
        )
        await session.commit()
    except ValueError:
        await session.rollback()
        return _redirect("/dashboard/strategies/new?error=repair_revision_unavailable#monitors")
    return _redirect(f"/dashboard/strategies/{strategy.id}/builder?message=repair_revision_ready")


@router.post(
    "/dashboard/capability-extensions/{extension_id}/discard-repair",
    include_in_schema=False,
)
async def discard_capability_repair(
    extension_id: UUID,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    extension = await session.get(CapabilityExtension, extension_id)
    if extension is None or extension.user_id != user.id:
        raise HTTPException(status_code=404, detail="Certified repair not found")
    try:
        await CapabilityExtensionService(settings).discard_pending_repair(
            session,
            extension=extension,
            user_id=user.id,
        )
        await session.commit()
    except ValueError:
        await session.rollback()
        return _redirect("/dashboard/strategies/new?error=repair_revision_unavailable#monitors")
    return _redirect("/dashboard/strategies/new?message=repair_discarded#monitors")


@router.post(
    "/dashboard/capability-extensions/{extension_id}/quarantine",
    include_in_schema=False,
)
async def quarantine_capability_extension(
    extension_id: UUID,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    extension = await session.get(CapabilityExtension, extension_id)
    if extension is None or extension.user_id != user.id:
        raise HTTPException(status_code=404, detail="Custom mechanic not found")
    await CapabilityExtensionService(settings).quarantine(
        session,
        extension=extension,
        user_id=user.id,
        reason="Owner requested immediate quarantine from the Watch Plan dashboard.",
    )
    await session.commit()
    return _redirect("/dashboard/strategies/new?message=mechanic_quarantined#monitors")


@router.post(
    "/dashboard/capability-extensions/{extension_id}/restore",
    include_in_schema=False,
)
async def restore_capability_extension(
    extension_id: UUID,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    extension = await session.get(CapabilityExtension, extension_id)
    if extension is None or extension.user_id != user.id:
        raise HTTPException(status_code=404, detail="Custom mechanic not found")
    await CapabilityExtensionService(settings).restore_from_quarantine(
        session,
        extension=extension,
        user_id=user.id,
    )
    await session.commit()
    return _redirect("/dashboard/strategies/new?message=mechanic_restored#monitors")


@router.get("/dashboard/create-monitor", response_class=HTMLResponse, include_in_schema=False)
async def create_monitor_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return await new_strategy_builder_page(request, user, session, settings)


@router.get("/dashboard/scan-now", response_class=RedirectResponse, include_in_schema=False)
async def scan_now_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    del request, user, session, settings
    return _redirect("/dashboard/strategies/new?mode=scanner")


@router.get(
    "/dashboard/check-market",
    response_class=RedirectResponse,
    include_in_schema=False,
    name="dashboard_check_market",
)
async def dashboard_check_market(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Open the one-time scanner through the shared validated builder flow."""
    del request, user, session, settings
    return _redirect("/dashboard/strategies/new?mode=scanner")


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


@router.get("/dashboard/activity", response_class=HTMLResponse, include_in_schema=False)
@router.get("/dashboard/lifecycles", response_class=HTMLResponse, include_in_schema=False)
async def lifecycles_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    allowed_tabs = {
        "all",
        "forming",
        "alerts",
        "ended",
        "compliance_changes",
        "investigations",
    }
    requested_tab = request.query_params.get("tab", "forming")
    activity_tab = cast(
        Literal[
            "all",
            "forming",
            "alerts",
            "ended",
            "compliance_changes",
            "investigations",
        ],
        requested_tab if requested_tab in allowed_tabs else "forming",
    )
    try:
        activity_page = max(1, int(request.query_params.get("page", "1") or 1))
    except ValueError:
        activity_page = 1
    selected_monitor_id: UUID | None = None
    raw_monitor_id = request.query_params.get("monitor")
    if raw_monitor_id:
        try:
            selected_monitor_id = UUID(raw_monitor_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid monitor filter") from exc
        owned_monitor = await session.scalar(
            select(Strategy.id).where(
                Strategy.id == selected_monitor_id,
                Strategy.user_id == user.id,
                Strategy.archived_at.is_(None),
            )
        )
        if owned_monitor is None:
            raise HTTPException(status_code=404, detail="Monitor not found")
    await StrategyCockpitService(session).sync_inbox(user.id)
    await session.commit()
    preference = await session.scalar(
        select(DashboardPreference).where(DashboardPreference.user_id == user.id)
    )
    muted_setup_ids = (
        set(
            map(
                str,
                ((preference.notification_preferences or {}).get("muted_setup_instance_ids", [])),
            )
        )
        if preference is not None
        else set()
    )
    activity = await ActivityReadService(session, settings).list_items(
        user.id,
        tab=activity_tab,
        monitor_id=selected_monitor_id,
        symbol=request.query_params.get("symbol") or None,
        page=activity_page,
        limit=30,
    )
    return templates.TemplateResponse(
        request,
        "hilal/dashboard/activity.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="activity",
            title="Evidence and Activity",
            lifecycle_monitors=list(
                (
                    await session.execute(
                        select(Strategy.id, Strategy.name)
                        .where(
                            Strategy.user_id == user.id,
                            Strategy.archived_at.is_(None),
                        )
                        .order_by(Strategy.name.asc())
                    )
                ).all()
            ),
            selected_monitor_id=selected_monitor_id,
            observability_poll_seconds=settings.observability_live_poll_seconds,
            activity=activity,
            activity_tab=activity_tab,
            lifecycle_cards=await lifecycle_cards(
                session,
                user.id,
                monitor_id=selected_monitor_id,
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
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def alert_proof_page(
    request: Request,
    alert_id: UUID,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    alert = await session.scalar(
        select(Alert).where(Alert.id == alert_id, Alert.user_id == user.id)
    )
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    proof_hash = seal_alert_proof(alert)
    version = (
        await session.get(StrategyVersion, alert.strategy_version_id)
        if alert.strategy_version_id
        else None
    )
    strategy = await session.get(Strategy, version.strategy_id) if version else None
    current_version = (
        await session.get(StrategyVersion, strategy.active_version_id)
        if strategy and strategy.active_version_id
        else None
    )
    deliveries = list(
        (
            await session.scalars(
                select(AlertDelivery)
                .where(AlertDelivery.alert_id == alert.id)
                .order_by(AlertDelivery.created_at)
            )
        ).all()
    )
    sharia_proof = sharia_evidence_from_proof(alert.proof_receipt or {})
    raw_sharia_asset = sharia_proof.get("asset")
    sharia_asset = raw_sharia_asset if isinstance(raw_sharia_asset, dict) else {}
    proof_symbol = (alert.proof_receipt or {}).get("symbol")
    sharia_passport_asset = sharia_asset.get("canonical_asset") or (
        canonical_asset(str(proof_symbol)) if proof_symbol else None
    )
    passport_publication = (
        await session.get(PublishedAssetAssessment, alert.sharia_passport_version_id)
        if alert.sharia_passport_version_id
        else None
    )
    sharia_passport_url = (
        f"/passports/{passport_publication.canonical_asset_id}/versions/"
        f"{passport_publication.id}?event_time="
        f"{(alert.candle_timestamp or alert.created_at).isoformat()}"
        if passport_publication is not None
        else (
            f"/dashboard/market/{sharia_passport_asset.lower()}" if sharia_passport_asset else None
        )
    )
    await session.commit()
    return _no_store(
        templates.TemplateResponse(
            request,
            "hilal/dashboard/alert_proof.html",
            await _context(
                request=request,
                session=session,
                settings=settings,
                user=user,
                page="alert_proof",
                title="Alert proof",
                alert=alert,
                proof=alert.proof_receipt or {},
                proof_hash=proof_hash,
                version=version,
                strategy=strategy,
                current_version=current_version,
                version_mismatch=bool(
                    version and current_version and version.id != current_version.id
                ),
                deliveries=deliveries,
                sharia_proof=sharia_proof,
                sharia_asset=sharia_asset,
                sharia_passport_asset=sharia_passport_asset,
                sharia_passport_url=sharia_passport_url,
                sharia_passport_version_id=alert.sharia_passport_version_id,
                sharia_passport_canonical_asset_id=(
                    passport_publication.canonical_asset_id
                    if passport_publication is not None
                    else None
                ),
            ),
        )
    )


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
    raise HTTPException(
        status_code=404, detail="Strategy Cockpit was removed. Use Monitors and Lifecycles."
    )


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
) -> RedirectResponse:
    return _redirect("/dashboard/billing")


@router.post("/dashboard/trial/claim", include_in_schema=False)
async def claim_trial(
    csrf_token_value: str = Form(..., alias="csrf_token"),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if not csrf_token_matches(settings, user.id, csrf_token_value):
        raise HTTPException(status_code=403, detail="Invalid form token")
    if settings.billing_enabled:
        return _redirect(
            "/dashboard/billing?"
            + urlencode(
                {
                    "selected_plan": "trader",
                    "billing_interval": "monthly",
                    "checkout": "1",
                    "trial": "1",
                }
            )
        )
    try:
        existing = await session.scalar(select(Trial).where(Trial.user_id == user.id))
        trial_service = TrialLifecycleService(session, settings)
        await trial_service.activate(user.id)
        await trial_service.start_monitoring_cycle(user.id)
        await session.commit()
        if existing is None:
            await AdminNotificationService(settings).send(
                f"Monitor trial claimed: {user.display_name or user.id}"
            )
        return _redirect("/dashboard/billing?message=trial_claimed")
    except TrialError as exc:
        await session.rollback()
        return _redirect(f"/dashboard/billing?error={exc.code}")


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
    completed_provider_trial = await session.scalar(
        select(BillingCheckoutAttempt.id).where(
            BillingCheckoutAttempt.user_id == user.id,
            BillingCheckoutAttempt.billing_cycle == "trial_7_day",
            BillingCheckoutAttempt.status == "completed",
        )
    )
    primary_email = await session.scalar(
        select(UserIdentity.normalized_identifier)
        .where(
            UserIdentity.user_id == user.id,
            UserIdentity.provider == IdentityProvider.EMAIL,
            UserIdentity.is_primary.is_(True),
            UserIdentity.is_verified.is_(True),
        )
        .limit(1)
    )
    billing = BillingService(session, settings)
    card_provider = _billing_method_provider(settings, "card")
    crypto_provider = _billing_method_provider(settings, "crypto")
    active_paid_plan_codes = await _active_paid_plan_codes(session, user_id=user.id)
    display_name_parts = (user.display_name or "").strip().split(maxsplit=1)
    billing_selection_availability = {
        code: {
            "purchasable": _plan_checkout_allowed(
                plan_code=code,
                active_paid_plan_codes=active_paid_plan_codes,
            ),
            "card_monthly": _billing_selection_available(
                settings,
                provider=card_provider,
                plan_code=code,
                billing_cycle="monthly",
            ),
            "card_annual": _billing_selection_available(
                settings,
                provider=card_provider,
                plan_code=code,
                billing_cycle="annual",
            ),
            "crypto_monthly": _billing_selection_available(
                settings,
                provider=crypto_provider,
                plan_code=code,
                billing_cycle="monthly",
            ),
            "trial": (
                code == "trader"
                and trial is None
                and completed_provider_trial is None
                and _billing_selection_available(
                    settings,
                    provider=card_provider,
                    plan_code=code,
                    billing_cycle="trial_7_day",
                )
            ),
        }
        for code in PURCHASABLE_PLAN_CODES
    }
    attempts = list(
        (
            await session.scalars(
                select(BillingCheckoutAttempt)
                .where(BillingCheckoutAttempt.user_id == user.id)
                .order_by(BillingCheckoutAttempt.created_at.desc())
                .limit(25)
            )
        ).all()
    )
    plan_ids = {attempt.plan_id for attempt in attempts}
    history_plans = {
        plan.id: plan
        for plan in (
            list((await session.scalars(select(Plan).where(Plan.id.in_(plan_ids)))).all())
            if plan_ids
            else []
        )
    }
    receipts = list(
        (
            await session.scalars(
                select(PaymentEmailDelivery)
                .where(PaymentEmailDelivery.user_id == user.id)
                .order_by(PaymentEmailDelivery.created_at.desc())
                .limit(25)
            )
        ).all()
    )
    await session.commit()
    return templates.TemplateResponse(
        request,
        "hilal/dashboard/billing.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="billing",
            title="Subscription and Billing",
            entitlement=entitlement,
            trial=trial,
            purchase_plans={
                code: PLAN_DEFINITIONS[code]
                for code in visible_public_plan_codes(
                    billing_enabled=settings.billing_enabled
                )
            },
            plan_presentations=PUBLIC_PLAN_PRESENTATIONS,
            # What each plan costs today and whether it can be bought, from the same
            # `core/plans` definition the landing page reads. One offer, three surfaces.
            plan_offer_values={
                code: plan_offer_payload(code)
                for code in visible_public_plan_codes(
                    billing_enabled=settings.billing_enabled
                )
            },
            promotion_ends_at=PROMOTION_ENDS_AT.isoformat(),
            promotion_active=promotion_is_active(),
            promotion_coming_soon_label=COMING_SOON_LABEL,
            plan_comparison=visible_plan_comparison(
                billing_enabled=settings.billing_enabled
            ),
            plan_comparison_headers=visible_plan_comparison_headers(
                billing_enabled=settings.billing_enabled
            ),
            trial_claimable=(
                trial is None
                and completed_provider_trial is None
                and _plan_checkout_allowed(
                    plan_code="trader",
                    active_paid_plan_codes=active_paid_plan_codes,
                )
            ),
            active_paid_plan_codes=active_paid_plan_codes,
            whatsapp_operational=settings.whatsapp_enabled,
            billing_enabled=settings.billing_enabled,
            billing_provider=billing.provider.provider_name,
            billing_capabilities=billing.provider_capabilities,
            billing_cycle_code=billing.billing_cycle_code,
            checkout_selected_plan=request.query_params.get("selected_plan"),
            checkout_selected_interval=(
                "annual"
                if request.query_params.get("billing_interval") == "annual"
                else "monthly"
            ),
            checkout_auto_open=request.query_params.get("checkout") == "1",
            checkout_trial_selected=request.query_params.get("trial") == "1",
            billing_profile_defaults={
                "first_name": display_name_parts[0] if display_name_parts else "",
                "last_name": display_name_parts[1] if len(display_name_parts) > 1 else "",
                "email": primary_email or "",
            },
            billing_method_providers={
                "card": card_provider,
                "crypto": crypto_provider,
            },
            billing_selection_availability=billing_selection_availability,
            billing_plan_data={
                "plans": {
                    code: {
                        "name": PLAN_DEFINITIONS[code].name,
                        "monthly": str(PLAN_DEFINITIONS[code].monthly_price),
                        "annual": str(PUBLIC_PLAN_PRESENTATIONS[code].annual_price),
                        "availability": billing_selection_availability[code],
                    }
                    for code in PURCHASABLE_PLAN_CODES
                },
                "providers": {
                    "card": card_provider,
                    "crypto": crypto_provider,
                },
            },
            checkout_request_id=uuid4().hex,
            billing_history=_billing_history_rows(
                attempts,
                history_plans,
                now=datetime.now(UTC),
            ),
            payment_receipts=receipts,
        ),
    )


@router.get(
    "/dashboard/billing/portal",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def dashboard_billing_portal_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    await PlanCatalogService(session).sync_defaults()
    entitlement = await EntitlementService(session).current(user.id)
    subscription = await session.scalar(
        select(Subscription)
        .where(Subscription.user_id == user.id)
        .order_by(Subscription.updated_at.desc())
    )
    attempts = list(
        (
            await session.scalars(
                select(BillingCheckoutAttempt)
                .where(BillingCheckoutAttempt.user_id == user.id)
                .order_by(BillingCheckoutAttempt.created_at.desc())
                .limit(10)
            )
        ).all()
    )
    plan_ids = {attempt.plan_id for attempt in attempts}
    history_plans = {
        plan.id: plan
        for plan in (
            list((await session.scalars(select(Plan).where(Plan.id.in_(plan_ids)))).all())
            if plan_ids
            else []
        )
    }
    receipts = list(
        (
            await session.scalars(
                select(PaymentEmailDelivery)
                .where(PaymentEmailDelivery.user_id == user.id)
                .order_by(PaymentEmailDelivery.created_at.desc())
                .limit(10)
            )
        ).all()
    )
    provider_name = (
        subscription.provider
        if subscription is not None
        and subscription.provider not in {None, "free", "admin", "trial"}
        else None
    )
    supports_customer_portal = False
    if provider_name:
        try:
            supports_customer_portal = billing_provider_capabilities(
                provider_name
            ).supports_customer_portal
        except BillingError:
            supports_customer_portal = False
    portal_available = bool(
        settings.billing_enabled
        and subscription is not None
        and subscription.provider_customer_id
        and supports_customer_portal
    )
    await session.commit()
    return templates.TemplateResponse(
        request,
        "hilal/dashboard/billing_portal.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="billing",
            title="Billing Portal",
            entitlement=entitlement,
            subscription=subscription,
            provider_name=provider_name,
            portal_available=portal_available,
            billing_enabled=settings.billing_enabled,
            billing_history=_billing_history_rows(
                attempts,
                history_plans,
                now=datetime.now(UTC),
            ),
            payment_receipts=receipts,
        ),
    )


@router.post("/dashboard/billing/portal", include_in_schema=False)
async def dashboard_billing_portal(
    csrf_token_value: str = Form(..., alias="csrf_token"),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if not csrf_token_matches(settings, user.id, csrf_token_value):
        raise HTTPException(status_code=403, detail="Invalid form token")
    if not settings.billing_enabled:
        return _redirect("/dashboard/billing/portal?error=billing_disabled")
    try:
        base_url = str(settings.app_base_url or settings.public_base_url).rstrip("/")
        result = await BillingService(session, settings).billing_portal(
            user_id=user.id,
            return_url=f"{base_url}/dashboard/billing",
        )
    except BillingError as exc:
        return _redirect(f"/dashboard/billing/portal?error={exc.code}")
    return _redirect(result.portal_url)


@router.get(
    "/dashboard/billing/checkout/{attempt_id}/resume",
    include_in_schema=False,
)
async def resume_billing_checkout(
    attempt_id: UUID,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """Resume only the authenticated user's still-valid provider checkout."""
    attempt = await session.get(BillingCheckoutAttempt, attempt_id)
    if attempt is None or attempt.user_id != user.id:
        raise HTTPException(status_code=404, detail="Checkout not found")

    expires_at = attempt.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    checkout_url = (attempt.checkout_url or "").strip()
    if attempt.status == "pending" and expires_at <= datetime.now(UTC):
        attempt.status = "expired"
        await session.commit()
    elif (
        attempt.status == "pending"
        and attempt.provider_session_id
        and checkout_url.startswith("https://")
    ):
        return RedirectResponse(
            checkout_url,
            status_code=303,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
            },
        )
    return _redirect("/dashboard/billing?error=checkout_not_resumable")


@router.get(
    "/dashboard/billing/checkout",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def billing_checkout_review(
    request: Request,
    plan_code: str = Query(..., min_length=1, max_length=50),
    attempt: str | None = Query(default=None),
    state: str | None = Query(default=None),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    if not settings.billing_enabled:
        return _redirect("/dashboard/billing?error=billing_disabled")
    if plan_code not in PURCHASABLE_PLAN_CODES:
        return _redirect("/dashboard/billing?error=plan_not_available")
    await PlanCatalogService(session).sync_defaults()
    plan = await session.scalar(
        select(Plan).where(Plan.code == plan_code, Plan.is_active.is_(True))
    )
    if plan is None or plan.price_monthly <= 0:
        await session.commit()
        return _redirect("/dashboard/billing?error=plan_not_available")
    checkout_attempt = None
    attempt_id = _optional_uuid(attempt, label="checkout")
    if attempt_id is not None:
        checkout_attempt = await session.get(BillingCheckoutAttempt, attempt_id)
        if checkout_attempt is None or checkout_attempt.user_id != user.id:
            raise HTTPException(status_code=404, detail="Checkout not found")
    active_paid_plan_codes = await _active_paid_plan_codes(session, user_id=user.id)
    billing = BillingService(session, settings)
    primary_email = await session.scalar(
        select(UserIdentity.normalized_identifier)
        .where(
            UserIdentity.user_id == user.id,
            UserIdentity.provider == IdentityProvider.EMAIL,
            UserIdentity.is_primary.is_(True),
            UserIdentity.is_verified.is_(True),
        )
        .limit(1)
    )
    display_name_parts = (user.display_name or "").strip().split(maxsplit=1)
    await session.commit()
    features = dict(plan.features or {})
    response = templates.TemplateResponse(
        request,
        "hilal/dashboard/checkout.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="billing",
            title=f"Review {plan.name} checkout",
            plan=plan,
            plan_limits=dict(features.get("limits") or {}),
            plan_features=dict(features.get("features") or {}),
            billing_cycle=billing.billing_cycle_code,
            billing_provider=billing.provider.provider_name,
            billing_capabilities=billing.provider_capabilities,
            checkout_request_id=uuid4().hex,
            checkout_attempt=checkout_attempt,
            checkout_state=state,
            already_subscribed=plan.code in active_paid_plan_codes,
            billing_profile_defaults={
                "first_name": display_name_parts[0] if display_name_parts else "",
                "last_name": display_name_parts[1] if len(display_name_parts) > 1 else "",
                "email": primary_email or "",
            },
        ),
    )
    return _no_store(response)


@router.post("/dashboard/billing/checkout", include_in_schema=False)
async def billing_checkout(
    request: Request,
    plan_code: str = Form(...),
    billing_cycle: str = Form(default="monthly"),
    payment_method: str = Form(default="card"),
    checkout_request_id: str = Form(default="free-plan"),
    terms_accepted: str | None = Form(default=None),
    first_name: str = Form(default=""),
    last_name: str = Form(default=""),
    address_line1: str = Form(default=""),
    address_line2: str = Form(default=""),
    city: str = Form(default=""),
    region: str = Form(default=""),
    postal_code: str = Form(default=""),
    country: str = Form(default=""),
    csrf_token_value: str = Form(..., alias="csrf_token"),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    wants_json = "application/json" in request.headers.get("accept", "").casefold()
    if not csrf_token_matches(settings, user.id, csrf_token_value):
        raise HTTPException(status_code=403, detail="Invalid form token")
    if not settings.billing_enabled:
        if wants_json:
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "code": "billing_disabled",
                        "message": "Paid checkout is not available right now.",
                    }
                },
            )
        return _redirect("/dashboard/billing?error=billing_disabled")
    base = str(settings.app_base_url or settings.public_base_url).rstrip("/")
    if plan_code not in PUBLIC_PLAN_CODES:
        return _redirect("/dashboard/billing?error=plan_not_available")
    try:
        plan = await PlanCatalogService(session).get_or_sync(plan_code)
        if plan.price_monthly == 0:
            await BillingService(session, settings).activate_free_plan(
                user_id=user.id,
                plan_code=plan_code,
            )
            await session.commit()
            await AdminNotificationService(settings).send(
                f"Free plan: {user.display_name or user.id} {plan_code}"
            )
            redirect_url = "/dashboard/billing?message=free_plan_activated"
            if wants_json:
                return JSONResponse({"checkout_url": redirect_url})
            return _redirect(redirect_url)
        if terms_accepted != "true":
            raise BillingError(
                "billing_terms_required",
                "Accept the billing terms before continuing.",
            )
        provider_name = configured_billing_provider(settings, payment_method)
        service = BillingService(session, settings, provider_name=provider_name)
        profile = _billing_profile(
            first_name=first_name,
            last_name=last_name,
            address_line1=address_line1,
            address_line2=address_line2,
            city=city,
            region=region,
            postal_code=postal_code,
            country=country,
        )
        prepared = await service.prepare_checkout(
            user_id=user.id,
            plan_code=plan_code,
            billing_cycle=billing_cycle,
            request_key=checkout_request_id,
            terms_accepted=True,
            billing_profile=profile,
        )
        await session.commit()
        if prepared.duplicate and prepared.attempt.checkout_url:
            if wants_json:
                return JSONResponse({"checkout_url": prepared.attempt.checkout_url})
            return _redirect(prepared.attempt.checkout_url)
        checkout = await service.open_checkout_attempt(
            attempt_id=prepared.attempt.id,
            user_id=user.id,
            success_url=f"{base}/billing/success?attempt={prepared.attempt.id}",
            cancel_url=f"{base}/billing/cancel?attempt={prepared.attempt.id}",
        )
        await session.commit()
        await AdminNotificationService(settings).send(
            f"Payment link: {user.display_name or user.id} {plan_code}"
        )
        if wants_json:
            return JSONResponse({"checkout_url": checkout.checkout_url})
        return _redirect(checkout.checkout_url)
    except BillingError as exc:
        if session.in_transaction():
            await session.commit()
        if wants_json:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": exc.code, "message": str(exc)}},
            )
        return _redirect(
            "/dashboard/billing?"
            + urlencode(
                {
                    "selected_plan": plan_code,
                    "billing_interval": (
                        "annual" if "annual" in billing_cycle else "monthly"
                    ),
                    "checkout": "1",
                    "trial": "1" if billing_cycle == "trial_7_day" else "0",
                    "error": exc.code,
                }
            )
        )


@router.get("/billing/success", response_class=HTMLResponse, include_in_schema=False)
async def billing_success(
    request: Request,
    attempt: str | None = Query(default=None),
    static_session: str | None = Query(default=None),
    static_token: str | None = Query(default=None),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    attempt_id = _optional_uuid(attempt, label="checkout")
    checkout_attempt = await session.get(BillingCheckoutAttempt, attempt_id) if attempt_id else None
    if checkout_attempt is not None and checkout_attempt.user_id != user.id:
        raise HTTPException(status_code=404, detail="Checkout not found")
    if (
        checkout_attempt is not None
        and checkout_attempt.provider == "static"
        and settings.app_env in {"development", "test"}
        and static_session == checkout_attempt.provider_session_id
        and static_token
    ):
        expected = hmac.new(
            settings.app_secret_key.get_secret_value().encode("utf-8"),
            f"static-checkout:{checkout_attempt.id}:{user.id}".encode(),
            sha256,
        ).hexdigest()
        if hmac.compare_digest(expected, static_token):
            now = datetime.now(UTC)
            try:
                await BillingService(session, settings).process_event(
                    provider="static",
                    payload={
                        "id": f"static_checkout:{static_session}",
                        "type": "checkout.session.completed",
                        "data": {
                            "checkout_attempt_id": str(checkout_attempt.id),
                            "provider_subscription_id": static_session,
                            "provider_payment_reference": static_session,
                            "user_id": str(user.id),
                            "amount": str(checkout_attempt.amount),
                            "currency": checkout_attempt.currency,
                            "status": "active",
                            "current_period_start": now.isoformat(),
                            "current_period_end": (now + timedelta(days=30)).isoformat(),
                            "cancel_at_period_end": True,
                        },
                    },
                )
                await session.commit()
                from ai_market_monitor.services.payment_emails import (
                    PaymentEmailOutboxService,
                )

                await PaymentEmailOutboxService(session, settings).process_due(limit=5)
                checkout_attempt = await session.get(BillingCheckoutAttempt, checkout_attempt.id)
            except BillingError:
                await session.rollback()
                checkout_attempt = await session.get(BillingCheckoutAttempt, attempt_id)
    status = checkout_attempt.status if checkout_attempt else "processing"
    state_content = {
        "completed": ("Plan activated", "payment_successful"),
        "pending": ("Payment confirmation pending", "webhook_confirmation_delayed"),
        "processing": ("Payment is processing", "payment_processing"),
        "failed": ("Payment failed", "payment_failed"),
        "cancelled": ("Payment cancelled", "payment_canceled"),
        "expired": ("Checkout expired", "checkout_expired"),
        "provider_unavailable": ("Payment provider unavailable", "provider_unavailable"),
    }
    title, state_message = state_content.get(
        status,
        ("Payment confirmation pending", "webhook_confirmation_delayed"),
    )
    if (
        checkout_attempt is not None
        and checkout_attempt.status == "completed"
        and checkout_attempt.billing_cycle == "trial_7_day"
    ):
        title, state_message = "Your Monitor trial is ready", "trial_started"
    plan = await session.get(Plan, checkout_attempt.plan_id) if checkout_attempt else None
    entitlement = await EntitlementService(session).current(user.id)
    return templates.TemplateResponse(
        request,
        "billing_result.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="billing_success",
            title=title,
            message=state_message if status in {"completed", "pending", "processing"} else None,
            error=state_message if status not in {"completed", "pending", "processing"} else None,
            checkout_attempt=checkout_attempt,
            plan=plan,
            entitlement=entitlement,
        ),
    )


@router.get("/billing/cancel", response_class=HTMLResponse, include_in_schema=False)
async def billing_cancel(
    request: Request,
    attempt: str | None = Query(default=None),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    attempt_id = _optional_uuid(attempt, label="checkout")
    checkout_attempt = await session.get(BillingCheckoutAttempt, attempt_id) if attempt_id else None
    if checkout_attempt is not None:
        if checkout_attempt.user_id != user.id:
            raise HTTPException(status_code=404, detail="Checkout not found")
        if checkout_attempt.status not in {"completed", "failed", "expired"}:
            checkout_attempt.status = "cancelled"
            await session.commit()
    return templates.TemplateResponse(
        request,
        "billing_result.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="billing_cancel",
            title="Payment Canceled",
            error="payment_canceled",
            checkout_attempt=checkout_attempt,
            plan=(await session.get(Plan, checkout_attempt.plan_id) if checkout_attempt else None),
        ),
    )


@router.get("/billing/error", response_class=HTMLResponse, include_in_schema=False)
async def billing_error(
    request: Request,
    user: User = Depends(_require_user),
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
            user=user,
            page="billing_error",
            title="Payment Error",
            error="payment_failed",
        ),
    )


@router.get(
    "/dashboard/admin/payment-email-preview",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def payment_email_preview(
    plan_code: str = Query(default="pro", min_length=1, max_length=50),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    if settings.app_env == "production":
        raise HTTPException(status_code=404, detail="Not found")
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Administrator role required")
    plan = await PlanCatalogService(session).get_or_sync(plan_code)
    await session.commit()
    features = dict(plan.features or {})
    now = datetime.now(UTC)
    billing = BillingService(session, settings)
    first_name = ((user.display_name or "").strip().split() or ["there"])[0]
    rendered = PaymentEmailRenderer(settings).render(
        first_name=first_name,
        plan_name=plan.name,
        billing_frequency=(
            "monthly auto-renewal"
            if billing.provider_capabilities.supports_recurring_billing
            else "30-day access"
        ),
        amount=plan.price_monthly,
        currency=plan.currency,
        payment_date=now,
        period_end_date=now + timedelta(days=30),
        renews_automatically=billing.provider_capabilities.supports_recurring_billing,
        receipt_url=None,
        plan_limits=dict(features.get("limits") or {}),
    )
    response = HTMLResponse(rendered.html_body)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@router.get("/dashboard/integrations", response_class=HTMLResponse, include_in_schema=False)
@router.get("/dashboard/connections", response_class=HTMLResponse, include_in_schema=False)
async def connections_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    user_id = user.id
    telegram_connect_url = None
    telegram_start_command = None
    try:
        telegram_connect_url = await TelegramAccountLinkService(
            session,
            settings,
        ).create_dashboard_start_link(user_id=user_id)
        if telegram_connect_url and "?start=" in telegram_connect_url:
            telegram_start_command = (
                "/start " + telegram_connect_url.split("?start=", 1)[1].split("&", 1)[0]
            )
        await session.commit()
    except TelegramAccountLinkError:
        await session.rollback()
    await session.refresh(user)
    telegram = await session.scalar(
        select(TelegramConnection).where(TelegramConnection.user_id == user_id)
    )
    return templates.TemplateResponse(
        request,
        "hilal/dashboard/integrations.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="integrations",
            title="Notifications",
            telegram=telegram,
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
    sharia_preferences = dict((preference.notification_preferences or {}) if preference else {})
    return templates.TemplateResponse(
        request,
        "hilal/dashboard/settings.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="settings",
            title="Settings",
            preference=preference,
            supported_timezones=_timezone_options(),
            supported_themes=SUPPORTED_THEMES,
            alert_days=ALERT_DAYS,
            alert_hours=ALERT_HOURS,
            sharia_preferences=sharia_preferences,
        ),
    )


@router.post("/dashboard/settings", include_in_schema=False)
async def settings_submit(
    timezone: str = Form(...),
    near_miss_enabled: str = Form("true"),
    near_miss_threshold: int = Form(70),
    maximum_alerts_per_hour: int = Form(50),
    maximum_alerts_per_day: int = Form(500),
    alert_channels: list[str] = Form(default=[]),
    providers: list[str] = Form(default=["binance"]),
    alert_days: list[str] = Form(default=["Every Day"]),
    alert_hours: list[str] = Form(default=ALERT_HOURS),
    default_sharia_methodology_id: str = Form(default=""),
    allowed_sharia_statuses: list[str] = Form(
        default=[
            ShariaAssetStatus.ELIGIBLE.value,
            ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS.value,
        ]
    ),
    compliance_change_behavior: str = Form(default=ComplianceChangeBehavior.PAUSE_ASSET.value),
    compliance_alert_channels: list[str] = Form(default=["web"]),
    compliance_alert_digest: str = Form(default="immediate"),
    dashboard_notifications_enabled: str = Form(default="true"),
    dashboard_notification_sound: str = Form(default="chime"),
    forming_dashboard_notifications: str = Form(default="false"),
    forming_notification_sound: str = Form(default="pulse"),
    qualification_change_alerts: str = Form(default="true"),
    under_review_alerts: str = Form(default="true"),
    exclusion_alerts: str = Form(default="true"),
    advanced_sharia_override_acknowledged: str = Form(default="false"),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if timezone not in SUPPORTED_TIMEZONES:
        return _redirect("/dashboard/settings?error=unsupported_timezone")
    entitlement = await EntitlementService(session).current(user.id)
    whatsapp_allowed = bool(
        settings.whatsapp_enabled and entitlement.feature_enabled("whatsapp")
    )
    allowed_channels = {"telegram"}
    if whatsapp_allowed:
        allowed_channels.add("whatsapp")
    external_channels = [
        channel for channel in dict.fromkeys(alert_channels) if channel in allowed_channels
    ]
    channels = ["web", *external_channels]
    allowed_providers = {"binance", "bybit"}
    selected_providers = [provider for provider in providers if provider in allowed_providers]
    if not selected_providers:
        selected_providers = ["binance"]
    days = [day for day in alert_days if day in ALERT_DAYS]
    if not days:
        days = ["Every Day"]
    if "Every Day" in days:
        days = ["Every Day"]
    hours = [hour for hour in alert_hours if hour in ALERT_HOURS]
    screening = ShariaScreeningService(session, settings)
    selected_methodology_id: UUID | None = None
    if default_sharia_methodology_id:
        try:
            selected_methodology_id = UUID(default_sharia_methodology_id)
            await screening.methodology(selected_methodology_id, require_active=True)
        except (ValueError, ShariaScreeningError):
            return _redirect("/dashboard/settings?error=invalid_sharia_methodology")
    valid_statuses = {item.value for item in ShariaAssetStatus}
    selected_statuses = list(
        dict.fromkeys(item for item in allowed_sharia_statuses if item in valid_statuses)
    )
    if not selected_statuses:
        selected_statuses = [
            ShariaAssetStatus.ELIGIBLE.value,
            ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS.value,
        ]
    default_statuses = {
        ShariaAssetStatus.ELIGIBLE.value,
        ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS.value,
    }
    advanced_ack = advanced_sharia_override_acknowledged == "true"
    if not set(selected_statuses).issubset(default_statuses) and not advanced_ack:
        return _redirect("/dashboard/settings?error=screening_override_ack_required")
    try:
        change_behavior = ComplianceChangeBehavior(compliance_change_behavior)
    except ValueError:
        change_behavior = ComplianceChangeBehavior.PAUSE_ASSET
    selected_compliance_channels = [
        channel
        for channel in dict.fromkeys(compliance_alert_channels)
        if channel in ({"web", "telegram", "whatsapp"} if whatsapp_allowed else {"web", "telegram"})
    ]
    if "web" not in selected_compliance_channels:
        selected_compliance_channels.insert(0, "web")
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
            "dashboard_notifications_enabled": dashboard_notifications_enabled == "true",
            "dashboard_notification_sound": (
                dashboard_notification_sound
                if dashboard_notification_sound in {"chime", "bell", "soft", "none"}
                else "chime"
            ),
            "forming_dashboard_notifications": forming_dashboard_notifications == "true",
            "forming_notification_sound": (
                forming_notification_sound
                if forming_notification_sound in {"pulse", "chime", "soft", "none"}
                else "pulse"
            ),
            "default_sharia_methodology_id": (
                str(selected_methodology_id) if selected_methodology_id else None
            ),
            "allowed_sharia_statuses": selected_statuses,
            "compliance_change_behavior": change_behavior.value,
            "compliance_alerts_enabled": True,
            "compliance_alert_channels": selected_compliance_channels,
            "compliance_alert_digest": (
                compliance_alert_digest
                if compliance_alert_digest in {"immediate", "daily"}
                else "immediate"
            ),
            "qualification_change_alerts": qualification_change_alerts == "true",
            # Active Watch Plans must retain at least in-app notices for these events.
            "under_review_alerts": True,
            "exclusion_alerts": True,
            "advanced_sharia_override_acknowledged": advanced_ack,
            "sharia": {
                "default_methodology_id": (
                    str(selected_methodology_id) if selected_methodology_id else None
                ),
                "allowed_statuses": selected_statuses,
                "compliance_change_behavior": change_behavior.value,
                "advanced_override_acknowledged": advanced_ack,
            },
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
        "hilal/dashboard/support.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="support",
            title="Support",
            tickets=tickets,
            support_email=(
                email_identity.display_identifier or email_identity.normalized_identifier
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
) -> RedirectResponse:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Administrator role required")
    return _redirect("/dashboard/system-brain")


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
        "hilal/dashboard/referrals.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="referrals",
            title="Referrals",
            referral_url=f"{settings.public_base_url}signup?ref={user.id}",
            reward_balance=reward_balance,
            referral_count=len(relationships),
        ),
    )
