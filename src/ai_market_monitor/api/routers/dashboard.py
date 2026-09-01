import asyncio
import hmac
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from html import escape as html_escape
from pathlib import Path
from typing import Any, Final, Literal, cast
from urllib.parse import urlencode
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import get_market_previewer
from ai_market_monitor.api.template_env import register as register_template_helpers
from ai_market_monitor.cockpit_service import StrategyCockpitService
from ai_market_monitor.core.auth_pages import (
    CODE_RESEND_SECONDS,
    PRODUCT_PROMISES,
    alert_for,
    browser_password_rules,
    page_copy,
)
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.csrf import csrf_token, csrf_token_matches
from ai_market_monitor.core.dashboard_paths import (
    AFFILIATE_PATH,
    CONNECTIONS_PATH,
    HOME_PATH,
    INTEGRATIONS_PATH,
    LEGACY_ASSISTANT_PATH,
    LEGACY_REFERRALS_PATH,
    LIFECYCLES_PATH,
    MONITOR_PATH,
    MONITORS_PATH,
    monitor_edit_path,
)
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.core.plans import (
    COMING_SOON_LABEL,
    PLAN_DEFINITIONS,
    PROMOTION_ENDS_AT,
    PUBLIC_PLAN_CODES,
    PUBLIC_PLAN_PRESENTATIONS,
    PURCHASABLE_PLAN_CODES,
    maximum_annual_saving,
    plan_offer,
    plan_offer_payload,
    promotion_is_active,
    visible_plan_comparison,
    visible_plan_comparison_headers,
    visible_public_plan_codes,
)
from ai_market_monitor.core.site_content import (
    DASHBOARD_NAVIGATION,
    WAITLIST_ANCHOR,
    dashboard_page_identity,
)
from ai_market_monitor.db.models import (
    AffiliatePayoutRequest,
    Alert,
    AlertDelivery,
    ApprovedWatchlist,
    ApprovedWatchlistAsset,
    AssetShariaStatusHistory,
    BillingCheckoutAttempt,
    CapabilityExtension,
    DashboardNotification,
    DashboardPreference,
    MonitorShariaAssetState,
    NearMissSnapshot,
    PaymentEmailDelivery,
    Plan,
    PublishedAssetAssessment,
    SetupInstance,
    ShariaMethodology,
    Strategy,
    StrategyUniverse,
    StrategyVersion,
    Subscription,
    TelegramConnection,
    Trial,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import (
    ComplianceChangeBehavior,
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
from ai_market_monitor.observability.banners import (
    banner_for_ai_disabled,
    customer_status_banners,
)
from ai_market_monitor.observability.metrics import get_metrics_recorder
from ai_market_monitor.services.account_settings import (
    SUPPORTED_TIMEZONES,
)
from ai_market_monitor.services.activity import ActivityReadService
from ai_market_monitor.services.admin_notifications import AdminNotificationService
from ai_market_monitor.services.affiliate import (
    DECISION_TARGET_HOURS,
    DEFAULT_COMMISSION_PERCENT,
    MAXIMUM_SOCIAL_LINKS,
    AffiliateError,
    AffiliateService,
    enqueue_affiliate_email,
    first_name_of,
    try_sending_now,
)
from ai_market_monitor.services.affiliate_payout_options import (
    ALTERNATIVE_METHOD_EMAIL,
    MINIMUM_PAYOUT_USD,
    payout_options_payload,
)
from ai_market_monitor.services.billing import (
    PAYMENT_METHODS,
    BillingError,
    BillingService,
    billing_method_provider,
    billing_provider_capabilities,
    configured_billing_provider,
    payment_method_available,
    payment_method_offers,
    payment_method_offers_by_method,
    payment_method_payload,
    payment_method_refusal,
)
from ai_market_monitor.services.capability_extensions import CapabilityExtensionService
from ai_market_monitor.services.coverage import market_coverage_for_user
from ai_market_monitor.services.dashboard_links import DashboardLinkError, DashboardLinkService
from ai_market_monitor.services.email_delivery import EmailDeliveryError
from ai_market_monitor.services.entitlements import EntitlementService, PlanCatalogService
from ai_market_monitor.services.google_oauth import GoogleOAuthError, GoogleOAuthService
from ai_market_monitor.services.interfaces import MarketDataProvider, RecentMarketPreviewer
from ai_market_monitor.services.lifecycle_dashboard import lifecycle_cards
from ai_market_monitor.services.market_sentiment import MarketSentimentService
from ai_market_monitor.services.monitor_operations import (
    MonitorOperationError,
    MonitorOperationService,
)
from ai_market_monitor.services.monitor_scan_state import scan_state_for_version
from ai_market_monitor.services.payment_emails import PaymentEmailRenderer
from ai_market_monitor.services.product_language import (
    checking_message_overrides,
    freshness_words,
    market_checking_notice,
)
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
from ai_market_monitor.services.site_analytics import SiteAnalyticsService
from ai_market_monitor.services.telegram_account_links import (
    TelegramAccountLinkError,
    TelegramAccountLinkService,
)
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


# Every filter and global this environment offers is installed in one place, so a
# template that uses one still loads through any other router's environment. See
# ``api/template_env.py``.
register_template_helpers(templates)


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
    selected_interval: Literal["monthly", "annual"] = (
        "annual" if billing_interval == "annual" else "monthly"
    )
    selected_plan = plan_code if plan_code in PUBLIC_PLAN_CODES else None
    if selected_plan is not None:
        offer = plan_offer(selected_plan)
        available = (
            offer.annual_available
            if selected_interval == "annual"
            else offer.monthly_available
        )
        if not available:
            selected_plan = None
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
        and payment_method_available(
            settings,
            method="card",
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
    offer = plan_offer(plan_code)
    return (
        plan_code not in active_paid_plan_codes
        and (offer.monthly_available or offer.annual_available)
    )


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
    # Read once for every monitor, instead of worked out again for each one.
    #
    # Edge Health is a score over thirty days of evidence, and the scheduled worker already
    # calculates it after every scan and writes it down. Recalculating it here cost about
    # thirteen queries per monitor, plus a second calculation of the same bottleneck inside
    # it — roughly nine hundred queries to draw a page of fifty cards, all to arrive at
    # numbers already sitting in two tables. These two calls answer for every monitor a
    # person owns, whatever the number.
    strategy_ids = [strategy.id for strategy in strategies]
    stored_health = await cockpit_service.stored_health(strategy_ids)
    stored_bottlenecks = await cockpit_service.stored_main_bottlenecks(strategy_ids)
    monitor_cards = []
    for strategy in strategies:
        health = stored_health.get(strategy.id)
        if health is None:
            # A monitor the worker has not reached yet — usually one made moments ago.
            # Worked out live so a new card says the same thing it always said, rather
            # than an empty placeholder. It costs the old price, and only for the few
            # monitors in that state, and only until the next worker run.
            health = await cockpit_service.edge_health(strategy, persist=False)
        main_bottleneck = stored_bottlenecks.get(strategy.id)
        # One reader for "what has this monitor's scanning done", because the question
        # has two halves and the newest row cannot answer both. See
        # `services/monitor_scan_state.py`: the newest row of a live monitor is almost
        # always the check that has not finished yet, so reading it said "Not looked
        # yet" about a monitor that had completed dozens of checks.
        scan_state = await scan_state_for_version(session, strategy.active_version_id)
        finished = scan_state.last_completed
        latency_label = "No completed scan yet"
        if finished is not None and finished.completed_at and finished.scheduled_for:
            seconds = max(
                0,
                int((finished.completed_at - finished.scheduled_for).total_seconds()),
            )
            latency_label = f"{seconds}s scan latency"
        elif scan_state.latest is not None:
            latency_label = scan_state.latest.status.value.replace("_", " ").title()
        # What the most recent check is doing, in one string, decided here rather than
        # in a template. A template that decides things cannot be tested, and this one
        # was reading `.status.value` off a row whose meaning it had to guess.
        last_check_label = (
            scan_state.latest.status.value.replace("_", " ").title()
            if scan_state.latest is not None
            else "Not run yet"
        )
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
                "main_bottleneck": main_bottleneck,
                "scan_state": scan_state,
                "latency_label": latency_label,
                "last_check_label": last_check_label,
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


def _status_banners(request: Request, settings: Settings) -> list[dict[str, str]]:
    """Degradation messages for the current customer, from the live measurements.

    Read from the same objectives the on-call alerts read, so a banner cannot say the
    product is healthy while an alert says it is not.

    ``force_status_banner`` is a development and test control only. Browser tests
    cannot break a real provider from outside the process, and the alternative — a
    stub that renders a banner the product would never produce — proves nothing about
    the real path. It follows the precedent already set by header admin principals in
    ``api/dependencies.py``: available in development and test, inert everywhere else,
    so a query string can never change what a deployed customer is told.
    """

    # The two switches that actually close the assistant to a customer: the emergency
    # stop, and the free-text composer.
    #
    # Deliberately not AI_INTERPRETER_PROVIDER. That selects the legacy deterministic
    # interpreter and says nothing about whether Setup Chat is usable; reading it here
    # would raise a permanent "assistant unavailable" banner on every deployment that
    # sets it to `rules`, including the browser test app. A banner that is always on is
    # a banner nobody reads.
    ai_enabled = (
        not settings.setup_chat_emergency_disabled and settings.setup_free_text_enabled
    )
    banners = customer_status_banners(get_metrics_recorder(), ai_enabled=ai_enabled)
    if settings.app_env in {"development", "test"}:
        forced = request.query_params.get("force_status_banner")
        if forced == "ai_unavailable" and not banners:
            banners = (banner_for_ai_disabled(),)
    return [banner.as_dict() for banner in banners]


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
        # Which menu entry this page belongs to, so the topbar can say where a person is.
        # Read from the navigation data rather than written per page: a second list would
        # start disagreeing with the menu the first time an entry was renamed.
        "page_identity": dashboard_page_identity(page),
        # What the shared topbar draws on this page's behalf. Empty unless the page says
        # otherwise through `**extra`, which is spread last and therefore wins.
        "topbar_actions": (),
        "dashboard_preference": dashboard_preference,
        # The one server-owned answer to "is this person new?". It is written by
        # OnboardingService.complete(), so it records a step the user actually finished.
        # The page guide auto-starts on this and nothing else: an empty dashboard, a
        # recent signup date or a missing Watchlist are all states an experienced user
        # can be in, and starting a tour over their work would be wrong.
        "onboarding_complete": bool(user and user.onboarding_completed_at),
        "entitlement": entitlement,
        "whatsapp_plan_included": bool(
            entitlement and entitlement.feature_enabled("whatsapp")
        ),
        "whatsapp_available": bool(
            entitlement
            and entitlement.feature_enabled("whatsapp")
            and settings.whatsapp_enabled
        ),
        # Built once, here, for every dashboard page. A per-page banner decision is how
        # one screen ends up telling a customer the assistant is down while the next
        # screen says nothing.
        "status_banners": _status_banners(request, settings),
        # Whether the platform is checking the market at all — `None` when it is, so a
        # page only draws something when there is something true to draw. Built here for
        # every dashboard page, for the same reason as the banners above.
        "market_checking": market_checking_notice(
            scanning_enabled=settings.scanning_enabled
        ),
        # The "done" messages that claim the market is being checked right now, replaced
        # while that would not be true. Publishing a monitor answered "It is checking the
        # market now" and the card underneath said "Not looked yet" — the same screen
        # disagreeing with itself, with the false half shown first.
        "dashboard_message_overrides": checking_message_overrides(
            scanning_enabled=settings.scanning_enabled
        ),
        "unread_notification_count": unread_notification_count,
        "dashboard_theme": dashboard_theme,
        "dashboard_csrf_token": csrf_token(settings, user.id) if user else None,
        "selected_plan_code": selected_plan_code,
        "selected_billing_interval": selected_billing_interval,
        "auth_link_suffix": f"?{urlencode(auth_query)}" if auth_query else "",
        **extra,
    }


async def _auth_context(
    *,
    request: Request,
    session: AsyncSession,
    settings: Settings,
    page: str,
) -> dict:
    """Everything one of the five sign-in pages needs, decided in Python.

    The template used to work all of this out itself: which of a page's two forms to
    draw, what an error code means in English, what the password rule is, and how long a
    code lasts. Four of those are facts the server owns, and a template restating a fact
    is a second copy of it — which is why the page could say "wait one minute" while the
    server waited sixty seconds, and could print an SMTP configuration instruction to
    somebody trying to sign up. ``core/auth_pages.py`` owns them now; this function only
    joins them to the addresses this router knows about.
    """

    user = await _current_user(request, session, settings)
    message = request.query_params.get("message")
    error = request.query_params.get("error")
    email = (request.query_params.get("email") or "").strip()
    # The name travels with the address between the three sign-up screens, so a refusal
    # on any of them hands both back rather than making somebody type their name again.
    # It is capped at the length the server accepts, because a query string is the one
    # input anybody can edit by hand.
    name = (request.query_params.get("name") or "").strip()[:60]
    copy = page_copy(page, has_email=bool(email), code_sent=message == "code_sent")

    selected_plan_code, selected_billing_interval = _subscription_selection(
        request.query_params.get("plan_code"),
        request.query_params.get("billing_interval"),
    )
    auth_query = _subscription_query(selected_plan_code, selected_billing_interval)
    if request.query_params.get("telegram_link"):
        auth_query["telegram_link"] = request.query_params["telegram_link"]
    suffix = f"?{urlencode(auth_query)}" if auth_query else ""

    # An error's "do this instead" button has to keep whatever the person arrived with.
    # A bare `/signin` here would quietly drop the plan they had just chosen.
    links = {
        "signin": f"/signin{suffix}",
        "signup": f"/signup{suffix}",
        "signin_code": f"/signin/code{suffix}",
        "reset": "/reset-password",
        "support": f"mailto:{settings.support_email}",
    }

    # Where "send me another code" posts to, on the pages that have one. A page with no
    # entry here simply does not offer the button, rather than offering one that 404s.
    resend_actions = {
        ("signup_verify", ""): "/signup/verify/resend",
        ("signin_code", "enter"): "/signin/code/request",
        ("reset_password", "enter"): "/reset-password/request",
    }

    # The Google door. It is offered on the two pages where it is a way *in* — never on
    # the confirm step or the reset step, where the person is mid-way through something
    # and a second door would only lose their place. `google_signin_enabled` is the one
    # owner of "is this configured": a button that opens a window and then fails is
    # worse than no button, which is why the page never decides this for itself.
    google_query = dict(auth_query)
    google_query["mode"] = "signup" if page == "signup" else "signin"
    google_available = settings.google_signin_enabled and page in {"signup", "signin"}

    return await _context(
        request=request,
        session=session,
        settings=settings,
        user=user,
        page=page,
        title=copy.title,
        auth=copy,
        auth_resend_action=resend_actions.get((page, copy.state), ""),
        auth_email=email,
        auth_name=name,
        auth_links=links,
        auth_alert=alert_for(
            page=page,
            message=message,
            error=error,
            ttl_minutes=settings.auth_code_ttl_minutes,
            links=links,
        ),
        auth_password_rules=browser_password_rules(),
        auth_code_ttl_minutes=settings.auth_code_ttl_minutes,
        auth_code_resend_seconds=CODE_RESEND_SECONDS,
        auth_code_max_attempts=settings.auth_code_max_attempts,
        # The three ticks under the button. Owned by `core/auth_pages.py` so the page
        # cannot invent a fourth promise, and so the copy rules can read them.
        auth_promises=PRODUCT_PROMISES,
        auth_google_enabled=google_available,
        auth_google_href=f"/auth/google/start?{urlencode(google_query)}",
        auth_google_label=(
            "Sign up with Google" if page == "signup" else "Sign in with Google"
        ),
        support_email=settings.support_email,
    )


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
        # The builder banner used to carry only the name. A name cannot tell a person
        # whether the standard behind their monitor is an authority's decision or a
        # machine reading websites, and the template needs the code to ask.
        "methodology_code": methodology.code if methodology else None,
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
        # The landing page has no pricing section while the site is pre-launch, so a
        # bad plan link would otherwise send the visitor to an anchor that is not there.
        return _redirect(
            WAITLIST_ANCHOR if settings.waitlist_mode else "/#pricing"
        )
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
            await _auth_context(
                request=request,
                session=session,
                settings=settings,
                page="signup",
            ),
        )
    )


@router.post("/signup", include_in_schema=False)
async def signup_submit(
    display_name: str = Form(default=""),
    email: str = Form(...),
    telegram_link: str | None = Form(None),
    plan_code: str | None = Form(None),
    billing_interval: str | None = Form(None),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Step one of three: who they are and where to reach them.

    It only checks. Nothing is written and no code is sent, so a person who changes
    their mind here has left nothing behind. The point of asking on its own is that
    "you already have an account" arrives now, rather than after somebody has invented
    and re-typed a password they will never use.

    The name is checked here too, on the screen it was typed on. A name refused later
    would send somebody back past a password they had already chosen, to a box they
    could no longer see.
    """

    clean_name = display_name.strip()[:60]
    query: dict[str, str] = {"email": email}
    # Only when there is one. An empty `name=` hanging off the address is noise in a bar
    # the person can read, and it is the difference these routes' tests assert on.
    if clean_name:
        query["name"] = clean_name
    query.update(_subscription_query(plan_code, billing_interval))
    if telegram_link:
        query["telegram_link"] = telegram_link
    try:
        await WebAuthService(session, settings).check_signup_details(
            email=email,
            display_name=display_name,
        )
    except WebAuthError as exc:
        # The name and the address come back with the refusal. Without them a person
        # whose email was already taken landed on an empty form and typed it all again.
        query["error"] = getattr(exc, "code", "invalid_email")
        return _redirect(f"/signup?{urlencode(query)}")
    return _redirect(f"/signup/password?{urlencode(query)}")


@router.get("/signup/password", response_class=HTMLResponse, include_in_schema=False)
async def signup_password_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    if not (request.query_params.get("email") or "").strip():
        # Nobody can choose a password for an address they have not given us. Arriving
        # here without one is a link somebody kept, not a step in the journey.
        return _redirect("/signup")
    return _no_store(
        templates.TemplateResponse(
            request,
            "auth.html",
            await _auth_context(
                request=request,
                session=session,
                settings=settings,
                page="signup_password",
            ),
        )
    )


@router.post("/signup/password", include_in_schema=False)
async def signup_password_submit(
    request: Request,
    email: str = Form(...),
    display_name: str = Form(default=""),
    password: str = Form(...),
    repeat_password: str = Form(...),
    telegram_link: str | None = Form(None),
    plan_code: str | None = Form(None),
    billing_interval: str | None = Form(None),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Step two of three: the password, and then the code goes out.

    Every check from step one runs again inside ``request_signup_email_code``. Step one
    is a courtesy, not a gate — an address that became taken in between is caught here,
    and so is a name that was edited out of the address bar on the way.

    The name rides through as a hidden field rather than being asked for twice. It is the
    only thing on this screen the person cannot see, so a refusal about it goes back to
    step one where the box is.
    """

    clean_name = display_name.strip()[:60]
    signup_lock = await _signup_lock_for(email)
    async with signup_lock:
        service = WebAuthService(session, settings)
        try:
            if password != repeat_password:
                raise WebAuthError("password_mismatch", "Password fields must match.")
            await service.request_signup_email_code(
                email=email,
                password=password,
                display_name=clean_name,
                telegram_link=telegram_link,
                requested_ip=request.client.host if request.client else None,
            )
            await session.commit()
        except (WebAuthError, EmailDeliveryError) as exc:
            await session.rollback()
            code = getattr(exc, "code", "signup_failed")
            query = {"error": code, "email": email}
            if clean_name:
                query["name"] = clean_name
            query.update(_subscription_query(plan_code, billing_interval))
            if telegram_link:
                query["telegram_link"] = telegram_link
            if code == "code_recently_sent":
                query["message"] = "code_sent"
                del query["error"]
                return _redirect(f"/signup/verify?{urlencode(query)}")
            # A taken address or a refused name is a step-one problem, so it goes back to
            # step one. Being sent back to the password box to fix an email address or a
            # name you cannot see is the kind of dead end a person simply gives up at.
            back = (
                "/signup"
                if code in {"account_exists", "invalid_email", "invalid_name"}
                else "/signup/password"
            )
            return _redirect(f"{back}?{urlencode(query)}")

    # The name travels on to step three as well, so "Wrong email? Start again" goes back
    # to a filled-in form rather than an empty one.
    query = {"message": "code_sent", "email": email}
    if clean_name:
        query["name"] = clean_name
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
            await _auth_context(
                request=request,
                session=session,
                settings=settings,
                page="signup_verify",
            ),
        )
    )


@router.post("/signup/verify/resend", include_in_schema=False)
async def signup_verify_resend(
    request: Request,
    email: str = Form(...),
    telegram_link: str | None = Form(None),
    plan_code: str | None = Form(None),
    billing_interval: str | None = Form(None),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Send the waiting sign-up another code.

    The confirm step used to offer only "Start again", which threw away the name, the
    email and the password somebody had just typed because their code had not arrived
    yet. Everything needed is already stored, so nothing is asked for twice.
    """

    query: dict[str, str] = {"email": email}
    query.update(_subscription_query(plan_code, billing_interval))
    if telegram_link:
        query["telegram_link"] = telegram_link
    try:
        await WebAuthService(session, settings).resend_signup_email_code(email=email)
        await session.commit()
    except (WebAuthError, EmailDeliveryError) as exc:
        await session.rollback()
        query["error"] = getattr(exc, "code", "email_unavailable")
        return _redirect(f"/signup/verify?{urlencode(query)}")
    query["message"] = "code_sent"
    return _redirect(f"/signup/verify?{urlencode(query)}")


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
        # The sign-up counter on the System Brain Stats page counts accounts, not clicks
        # on a button. This is the only place an account is really created, so it is the
        # only place the count is written.
        await SiteAnalyticsService(session, settings).record_signup(
            user_id=user.id,
            remote_address=(
                (request.headers.get("cf-connecting-ip") or "").strip()
                or (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
                or (request.client.host if request.client else "unknown")
            ),
            user_agent=request.headers.get("user-agent", ""),
            context={"door": "dashboard", "plan_code": plan_code or ""},
        )
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
        # Same reason as the sign-up path: a wrong password should not also cost a
        # person their email address.
        query = {"error": code, "email": email}
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
            await _auth_context(
                request=request,
                session=session,
                settings=settings,
                page="signin",
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
            await _auth_context(
                request=request,
                session=session,
                settings=settings,
                page="signin_code",
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
            await _auth_context(
                request=request,
                session=session,
                settings=settings,
                page="reset_password",
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


# ---------------------------------------------------------------------------
# The Google door.
# ---------------------------------------------------------------------------
#
# Two addresses. `/auth/google/start` sends somebody to Google; `/auth/google/callback`
# is where Google sends them back. Everything between the two is carried in a signed
# state value rather than in a cookie, because the round trip happens inside a popup
# window and a cookie written there is not reliably readable by the page underneath.


def _google_failure(mode: str, code: str, suffix: str) -> str:
    """Where a failed Google trip lands, with the plan the person chose still attached."""

    page = "/signup" if mode == "signup" else "/signin"
    joiner = "&" if suffix else "?"
    return f"{page}{suffix}{joiner}error={code}"


def _google_popup_close(target: str) -> HTMLResponse:
    """The last thing the popup window does before it disappears.

    It tells the page that opened it where to go and then closes itself. Two things
    matter here and both are failure modes somebody hits on a real machine: the message
    is sent to this origin only, and if there is no opener at all — the popup was
    blocked, so this is an ordinary tab — the page navigates itself instead of closing
    and leaving a person staring at a blank window.
    """

    safe_target = html_escape(target, quote=True)
    return HTMLResponse(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>Signing you in</title>"
        f'<meta http-equiv="refresh" content="0;url={safe_target}">'
        "</head><body>"
        "<p>Signing you in…</p>"
        "<script>(function(){"
        f'var target={json.dumps(target)};'
        "try{"
        "if(window.opener&&!window.opener.closed){"
        'window.opener.postMessage({source:"hilal-markets-google",target:target},'
        "window.location.origin);"
        "window.close();return;}"
        "}catch(e){}"
        "window.location.replace(target);"
        "})();</script>"
        "</body></html>"
    )


@router.get("/auth/google/start", include_in_schema=False)
async def google_start(
    mode: str = Query(default="signin", max_length=20),
    popup: str = Query(default="", max_length=4),
    plan_code: str | None = Query(default=None, max_length=20),
    billing_interval: str | None = Query(default=None, max_length=20),
    telegram_link: str | None = Query(default=None, max_length=200),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    selected_plan, selected_interval = _subscription_selection(plan_code, billing_interval)
    auth_query = _subscription_query(selected_plan, selected_interval)
    if telegram_link:
        auth_query["telegram_link"] = telegram_link
    suffix = f"?{urlencode(auth_query)}" if auth_query else ""
    chosen_mode = "signup" if mode == "signup" else "signin"

    service = GoogleOAuthService(settings)
    try:
        state = service.issue_state(
            {
                "mode": chosen_mode,
                "popup": "1" if popup else "",
                "plan_code": selected_plan or "",
                "billing_interval": selected_interval or "",
                "telegram_link": telegram_link or "",
            }
        )
        return _redirect(
            service.authorization_url(
                redirect_uri=settings.google_oauth_redirect_uri,
                state=state,
            )
        )
    except GoogleOAuthError as exc:
        return _redirect(_google_failure(chosen_mode, exc.code, suffix))


@router.get("/auth/google/callback", include_in_schema=False)
async def google_callback(
    request: Request,
    code: str | None = Query(default=None, max_length=2048),
    state: str | None = Query(default=None, max_length=2048),
    error: str | None = Query(default=None, max_length=100),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    service = GoogleOAuthService(settings)
    # The state is read before anything else, because it is the only thing that says
    # whether this window is a popup and which page the person started on. Without it
    # even the error has nowhere sensible to go.
    carried: dict[str, str] = {}
    try:
        carried = service.read_state(state or "")
    except GoogleOAuthError as exc:
        return _redirect(_google_failure("signin", exc.code, ""))

    mode = carried.get("mode") or "signin"
    plan_code = carried.get("plan_code") or None
    billing_interval = carried.get("billing_interval") or None
    telegram_link = carried.get("telegram_link") or None
    is_popup = bool(carried.get("popup"))
    auth_query = _subscription_query(plan_code, billing_interval)
    if telegram_link:
        auth_query["telegram_link"] = telegram_link
    suffix = f"?{urlencode(auth_query)}" if auth_query else ""

    def _finish(target: str) -> Response:
        return _google_popup_close(target) if is_popup else _redirect(target)

    # Google says "access_denied" when somebody closes its window or presses cancel.
    # That is not a failure, it is a person changing their mind, and it is worded that
    # way in `core/auth_pages.py`.
    if error or not code:
        failed = "google_cancelled" if error == "access_denied" else "google_unavailable"
        return _finish(_google_failure(mode, failed, suffix))

    linked_telegram_user_id: str | None = None
    try:
        profile = await service.exchange(
            code=code,
            redirect_uri=settings.google_oauth_redirect_uri,
        )
        auth = WebAuthService(session, settings)
        user, created = await auth.signin_or_signup_with_google(profile=profile)
        telegram_connected = False
        if telegram_link:
            linked_telegram_user_id = await TelegramAccountLinkService(session, settings).complete(
                telegram_link, user=user
            )
            telegram_connected = True
        cookie = await auth.create_session(user, user_agent=request.headers.get("user-agent"))
        if created:
            # The same counter the six-digit door writes. An account is an account
            # however the person got here, and a door that did not count would quietly
            # under-report every Google sign-up on the Stats page.
            await SiteAnalyticsService(session, settings).record_signup(
                user_id=user.id,
                remote_address=(
                    (request.headers.get("cf-connecting-ip") or "").strip()
                    or (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
                    or (request.client.host if request.client else "unknown")
                ),
                user_agent=request.headers.get("user-agent", ""),
                context={"door": "google", "plan_code": plan_code or ""},
            )
        await session.commit()
    except (GoogleOAuthError, WebAuthError, TelegramAccountLinkError) as exc:
        await session.rollback()
        return _finish(
            _google_failure(mode, getattr(exc, "code", "google_unavailable"), suffix)
        )

    if created:
        await AdminNotificationService(settings).send_signup_created(
            user_id=user.id,
            email=profile.email,
            source="google",
        )
    await _send_telegram_connected_notification(session, settings, linked_telegram_user_id)
    default_message = "account_created" if created else "login_successful"
    if telegram_connected:
        default_message = "telegram_connected"
    response = _finish(
        _subscription_destination(
            settings,
            plan_code=plan_code,
            billing_interval=billing_interval,
            default_message=default_message,
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


#: Where "/dashboard" goes now.
#:
#: The old counting front page is gone. It answered "how many of everything do you have"
#: with four counters, three of which normally read zero and none of which said what
#: nothing meant, and Home answers the question a person actually arrives with. Two front
#: pages meant two places to fix the same wording and two different answers to "what is
#: happening".
#:
#: The address stays and moves, rather than refusing. It is written into old email, into
#: Telegram buttons, into the `target_path` column of two tables and into the default
#: landing path after sign-in; a 404 there would strand every one of them for the sake of
#: a page that no longer exists anyway.
#:
#: The name is kept for the call sites; the address itself is `core/dashboard_paths.py`,
#: which is now the only place it is written. Three files used to hold their own copy of
#: the string, and renaming the page meant finding all three.
MAIN_DASHBOARD_PATH: Final[str] = HOME_PATH


@router.get("/dashboard", response_class=RedirectResponse, include_in_schema=False)
async def dashboard_home(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """The old front page. Everything that arrives here is taken to Home."""

    del request, user, session, settings
    return _redirect(MAIN_DASHBOARD_PATH)


async def screened_market_context(
    *,
    request: Request,
    methodology_id_input: str | None,
    status_filter: list[ShariaAssetStatus] | None,
    exchange: str | None,
    quote_asset: str,
    liquidity: float | None,
    search: str | None,
    view: str,
    page_number: int,
    user: User,
    session: AsyncSession,
    settings: Settings,
    provider: MarketDataProvider,
    base_path: str = "/dashboard/market",
) -> dict[str, Any]:
    """Assemble everything the screened-market page needs, for any template.

    `/dashboard/market` and `/dashboard/market` show the same screened assets
    through different designs. Reading the market twice, in two functions, is how the
    two would quietly start disagreeing about which assets are eligible. One owner
    here; `base_path` is the only thing the two callers differ on, because their
    pagination links must stay on their own path.
    """

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
    # The Halal Assets starts with the explicit All methodology. A saved
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
        return f"{base_path}?" + urlencode([*market_query, ("page", str(target_page))])

    # How the whole market is behaving today. Everything else on this page answers a
    # question about one coin; a reader watching one coin fall cannot otherwise tell
    # whether that coin is in trouble or whether everything fell together. Cached
    # process-wide and never allowed to fail the page — an unavailable reading renders
    # as unavailable rather than as a stale number presented as current.
    market_sentiment = await MarketSentimentService(settings).read()

    return await _context(
        request=request,
        session=session,
        settings=settings,
        user=user,
        page="screened_market",
        title="Halal Assets",
        market_sentiment=market_sentiment,
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
        market_base_path=base_path,
        market_previous_url=(market_page_url(screened.page - 1) if screened.page > 1 else None),
        market_next_url=(
            market_page_url(screened.page + 1) if screened.page < maximum_page else None
        ),
        market_maximum_page=maximum_page,
    )


#: `/dashboard/market` is served by the redesigned page in `dashboard_test.py`.
#:
#: The older page that answered here is gone, template and all. It called the same
#: `screened_market_context` below, which is why deleting it took nothing away: the
#: redesigned page asks that function the same question and gets the same assets.


async def asset_passport_context(
    *,
    request: Request,
    asset_slug: str,
    methodology_id: UUID | None,
    user: User,
    session: AsyncSession,
    settings: Settings,
    market_base_path: str = "/dashboard/market",
) -> dict[str, Any]:
    """Assemble the current Passport read model for any template.

    Shared by `/dashboard/market/{asset}` and `/dashboard/market/{asset}` so the
    two designs can never show different evidence for the same asset.
    """

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
    return await _context(
        request=request,
        session=session,
        settings=settings,
        user=user,
        page="asset_passport",
        title=f"{passport.assessment.canonical_asset} Evidence Passport",
        passport=passport,
        methodology_comparison=comparison,
        watchlists=watchlists,
        market_base_path=market_base_path,
    )


#: `/dashboard/market/{asset}` — one coin's Evidence Passport — is served by the
#: redesigned page in `dashboard_test.py`, over `asset_passport_context` above.
#:
#: The historical Passport below is *not* the same page. It reads a stored version of a
#: record rather than the current one, and it is reached only from the version list.


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
    return _redirect(f"{LIFECYCLES_PATH}?{urlencode(query_values)}")


@router.get("/dashboard/methodology", response_class=HTMLResponse, include_in_schema=False)
async def methodology_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    del request, user, session, settings
    return _redirect("/how-we-screen")


#: `/dashboard/monitors` is served by the redesigned Monitors page in
#: `dashboard_test.py`, which calls `_monitor_cards_context` below — the same rows.
#: This older address still answers here because links written years ago use it.
#:
#: It used to answer `/dashboard/monitors` as well, under the name
#: `legacy_monitors_page`. That address now belongs to the redesigned page — the same
#: move `/home` and `/dashboard/market` already made — and `dashboard.py` is included
#: before `dashboard_test.py`, so leaving the route here would have kept the older copy
#: in front of the page the side menu opens.
@router.get(
    "/dashboard/strategies",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="monitors_page",
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


#: The assistant page is gone. Both of its addresses forward to the canvas.
#:
#: It was one page with two jobs — a chat box that asked somebody to describe a monitor
#: in words, and the same page opened on a monitor they already had — and it was the
#: second place a monitor could be authored. Two authoring surfaces is how the canvas and
#: the assistant came to offer different rules for the same product: the canvas is drawn
#: from the platform's own contract, so every condition it offers is one the compiler can
#: run, and nothing there depends on a model being available or on it having understood a
#: sentence.
#:
#: The addresses stay as permanent redirects rather than becoming 404s. Both are written
#: into payment email that has already been sent, into Telegram buttons, into WhatsApp
#: replies and into saved bookmarks, and none of those can be corrected after the fact —
#: the same rule `LEGACY_HOME_PATH` follows in `core/dashboard_paths.py`.
#:
#: The one-time Scanner went with the page. It was a mode of it (`?mode=scanner`), it had
#: no other front door, and it is not rebuilt elsewhere.


@router.get(LEGACY_ASSISTANT_PATH, include_in_schema=False, name="legacy_assistant_page")
async def legacy_assistant_page() -> RedirectResponse:
    """Where a monitor used to be described in words. The canvas draws one now."""

    return RedirectResponse(MONITOR_PATH, status_code=308)


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
    include_in_schema=False,
    name="legacy_assistant_edit_page",
)
async def legacy_assistant_edit_page(strategy_id: UUID) -> RedirectResponse:
    """Where one monitor used to be edited. The canvas opens it now.

    The monitor is named in the address, so the redirect carries it: somebody following
    an old link lands on their own monitor rather than on an empty board. Whether that
    monitor can be drawn is settled by the canvas, which says so plainly when it cannot.

    Nothing about the monitor is read here. Checking ownership before forwarding would
    tell a stranger which ids are real, and the canvas refuses an id nobody owns anyway.
    """

    return RedirectResponse(monitor_edit_path(strategy_id), status_code=308)


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


#: Where a person is taken after they act on a monitor.
#:
#: Every one of these actions used to end on the AI Setup Chat page, which reopens the
#: last conversation somebody had. So pressing "Pause" or "Put away" on the Monitors page
#: threw them into a stale chat about a different monitor, and the anchor they arrived on
#: named a section of that page that is marked hidden. Nothing they had just done was
#: visible anywhere on the page they landed on.
#:
#: There are only two honest destinations, and both are pages the action is about:
#: the list of monitors, and the canvas where a new one is drawn. They are imported from
#: ``core/dashboard_paths.py`` rather than written here, so a page that moves address
#: cannot leave these redirects pointing at nothing.
_AFTER_MONITOR_ACTION = MONITORS_PATH
_AFTER_MONITOR_DELETED = MONITOR_PATH


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
        return _redirect(f"{_AFTER_MONITOR_ACTION}?message=monitor_paused")
    except MonitorOperationError as exc:
        await session.rollback()
        return _redirect(f"{_AFTER_MONITOR_ACTION}?error={exc.code}")


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
        return _redirect(f"{_AFTER_MONITOR_ACTION}?message=monitor_resumed")
    except MonitorOperationError as exc:
        await session.rollback()
        return _redirect(f"{_AFTER_MONITOR_ACTION}?error={exc.code}")


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
        # The monitor is gone, so the list is not what somebody wants next — a fresh
        # canvas is. The message still travels, so the canvas opens with "Your monitor
        # was put away" above it and nothing else to read.
        return _redirect(f"{_AFTER_MONITOR_DELETED}?message=monitor_deleted")
    except MonitorOperationError as exc:
        await session.rollback()
        # Nothing was put away, so the list is still the right place to stand.
        return _redirect(f"{_AFTER_MONITOR_ACTION}?error={exc.code}")


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
        return _redirect(f"{_AFTER_MONITOR_ACTION}?error=repair_revision_unavailable")
    # The corrected version is already prepared and waiting on the monitor's own card.
    # This used to send people to the assistant page to look at it; that page is gone,
    # and the canvas cannot draw a version it did not draw, so the honest destination is
    # the monitor itself.
    del strategy
    return _redirect(f"{_AFTER_MONITOR_ACTION}?message=repair_revision_ready")


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
        return _redirect(f"{_AFTER_MONITOR_ACTION}?error=repair_revision_unavailable")
    return _redirect(f"{_AFTER_MONITOR_ACTION}?message=repair_discarded")


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
        reason="Owner requested immediate quarantine from the Watchlist dashboard.",
    )
    await session.commit()
    return _redirect(f"{_AFTER_MONITOR_ACTION}?message=mechanic_quarantined")


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
    return _redirect(f"{_AFTER_MONITOR_ACTION}?message=mechanic_restored")


#: `/dashboard/create-monitor` is the visual canvas, and only the canvas.
#:
#: It used to be registered here as a second front door onto the older assistant page —
#: the same page `/dashboard/strategies/new` served. That page is gone entirely now and
#: its address forwards here, so there is one page where a monitor is authored
#: (`MONITOR_PATH`, served by `routers/dashboard_test.py`). Two routers cannot both own
#: one address: which page answered would depend on the order they were registered in,
#: which is not a decision anybody made.


#: Trading Assistant is gone from the product, and so are both of its addresses.
#:
#: It was one page with two names — `/dashboard/scan-now` and `/dashboard/check-market` —
#: which is the shape this repository keeps producing: one thing, several front doors,
#: and no way to remove it without missing one. Both refuse now.
#:
#: The one-time scan is gone with them. It was a mode of the assistant page
#: (`/dashboard/strategies/new?mode=scanner`) and had no front door of its own, so
#: deleting that page removed it. Nothing rebuilds it elsewhere.
_SCANNER_PAGE_GONE = "Trading Assistant was removed. Build a monitor instead."


@router.get("/dashboard/scan-now", response_class=HTMLResponse, include_in_schema=False)
async def scan_now_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    del request, user, session, settings
    raise HTTPException(status_code=404, detail=_SCANNER_PAGE_GONE)


@router.get(
    "/dashboard/check-market",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="dashboard_check_market",
)
async def dashboard_check_market(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """The Trading Assistant page. Removed; this address no longer serves anything."""
    del request, user, session, settings
    raise HTTPException(status_code=404, detail=_SCANNER_PAGE_GONE)


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


#: The one address for this page. Everything else that used to serve it now sends the
#: browser here. It lives in `dashboard_paths.py` because both routers write it.
#:
#: It used to be `/dashboard/opportunities`, which was never this page's name: the title
#: says "Evidence and Activity" and the constant says lifecycles. The redesigned
#: Opportunities page answers at that address now, and this page keeps its own name.
#: Nothing that used to reach it stopped reaching it — `/dashboard/activity` and
#: `/dashboard/opportunities?tab=…` both arrive here, and so does every link that asked
#: about a screening change or a missed alert, which Opportunities cannot answer.

#: The address that used to serve the same page from a second route.
#:
#: It stayed registered so old bookmarks would not 404, but serving one page from several
#: URLs meant the address bar disagreed with itself: the in-product guide keys its steps
#: on the path, so whichever URL the customer arrived on decided whether they got a guide
#: at all. It is a permanent redirect now, so there is one address the guide, the links
#: and the tests can all agree on.
#:
#: `/dashboard/lifecycles` used to be in this list. It is the page's own address now, so
#: leaving it here would have been a route that redirects to itself for ever.
LIFECYCLES_LEGACY_PATHS: Final[tuple[str, ...]] = ("/dashboard/activity",)


def _permanent_redirect(request: Request, path: str) -> RedirectResponse:
    """308 to ``path``, carrying the query string through unchanged.

    308 rather than 301: it is the one permanent redirect that browsers and proxies
    may not rewrite to a GET, so a link that is later reused for a form keeps working.
    Dropping the query string would silently change the request — `?tab=alerts` would
    land on the default tab and look like the page ignored the customer.
    """

    query = request.url.query
    return RedirectResponse(f"{path}?{query}" if query else path, status_code=308)


@router.get("/dashboard/activity", response_class=HTMLResponse, include_in_schema=False)
async def activity_page_redirect(request: Request) -> RedirectResponse:
    return _permanent_redirect(request, LIFECYCLES_PATH)


@router.get(LIFECYCLES_PATH, response_class=HTMLResponse, include_in_schema=False)
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
    return _redirect(f"{LIFECYCLES_PATH}?message=alerts_moved_to_lifecycles")


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
    return _redirect(f"{LIFECYCLES_PATH}?message=alert_context_moved_to_lifecycles")


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
                # In words, not milliseconds. This tile printed "341593 ms", which tells
                # a reader nothing unless they already know what it should have been —
                # and since lateness is measured past a candle's close it can be
                # negative, which as a raw number would have read as "-180000 ms".
                data_freshness_words=freshness_words(alert.proof_receipt or {}),
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
    if not settings.billing_enabled:
        return _redirect("/dashboard/billing?error=billing_disabled")
    return _redirect(
        "/dashboard/billing?"
        + urlencode(
            {
                "selected_plan": "trader",
                "billing_interval": "monthly",
                "checkout": "1",
            }
        )
    )


#: `/dashboard/subscription` — the plan somebody is on — is the redesigned page in
#: `dashboard_test.py`. This is the checkout and billing-history page behind it, and it
#: keeps its own address because it is a different screen, not a second copy of that one.
@router.get("/dashboard/billing", response_class=HTMLResponse, include_in_schema=False)
async def billing_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    await PlanCatalogService(session).sync_defaults()
    entitlement = await EntitlementService(session).current(user.id)
    trial = await session.scalar(select(Trial).where(Trial.user_id == user.id))
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
    card_provider = billing_method_provider(settings, "card")
    crypto_provider = billing_method_provider(settings, "crypto")
    active_paid_plan_codes = await _active_paid_plan_codes(session, user_id=user.id)
    display_name_parts = (user.display_name or "").strip().split(maxsplit=1)
    billing_selection_availability = {
        code: {
            "purchasable": _plan_checkout_allowed(
                plan_code=code,
                active_paid_plan_codes=active_paid_plan_codes,
            ),
            "card_monthly": payment_method_available(
                settings, method="card", plan_code=code, billing_cycle="monthly"
            ),
            "card_annual": payment_method_available(
                settings, method="card", plan_code=code, billing_cycle="annual"
            ),
            "crypto_monthly": payment_method_available(
                settings, method="crypto", plan_code=code, billing_cycle="monthly"
            ),
            "trial": False,
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
            # Computed from the prices beside it, so the toggle cannot promise a saving
            # no plan gives.
            maximum_annual_saving=int(maximum_annual_saving()),
            plan_comparison=visible_plan_comparison(
                billing_enabled=settings.billing_enabled
            ),
            plan_comparison_headers=visible_plan_comparison_headers(
                billing_enabled=settings.billing_enabled
            ),
            trial_claimable=False,
            active_paid_plan_codes=active_paid_plan_codes,
            whatsapp_operational=settings.whatsapp_enabled,
            billing_enabled=settings.billing_enabled,
            billing_provider=billing.provider.provider_name,
            billing_capabilities=billing.provider_capabilities,
            billing_cycle_code=billing.billing_cycle_code,
            checkout_selected_plan=(
                "trader" if request.query_params.get("selected_plan") == "trader" else None
            ),
            checkout_selected_interval="monthly",
            checkout_auto_open=(
                request.query_params.get("checkout") == "1"
                and request.query_params.get("selected_plan") == "trader"
            ),
            checkout_trial_selected=False,
            billing_profile_defaults={
                "first_name": display_name_parts[0] if display_name_parts else "",
                "last_name": display_name_parts[1] if len(display_name_parts) > 1 else "",
                "email": primary_email or "",
            },
            billing_method_providers={
                "card": card_provider,
                "crypto": crypto_provider,
            },
            billing_method_offers=payment_method_offers_by_method(
                settings,
                plan_codes=PURCHASABLE_PLAN_CODES,
                billing_cycle="monthly",
            ),
            # Whether a year at a time can be bought for *any* plan. The page used to work
            # this out from `annualAvailable` alone, which says a plan offers a yearly
            # price — not that the payment company holds a yearly product to charge for
            # it. Annual stayed pressable and then failed at the payment step.
            annual_purchasable=any(
                offer.available
                for offer in payment_method_offers(
                    settings,
                    plan_codes=PURCHASABLE_PLAN_CODES,
                    billing_cycle="annual",
                )
            ),
            billing_selection_availability=billing_selection_availability,
            billing_plan_data={
                "plans": {
                    code: {
                        "name": PLAN_DEFINITIONS[code].name,
                        "monthly": str(PLAN_DEFINITIONS[code].monthly_price),
                        "annual": str(PUBLIC_PLAN_PRESENTATIONS[code].annual_price),
                        "availability": billing_selection_availability[code],
                        # Decided here, per period, so the script draws an answer rather
                        # than working one out from flags a second time.
                        "methods": payment_method_payload(settings, plan_code=code),
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
    # A plan the site says is "Soon" is not for sale on this page either. The checkout
    # service already refuses it, so the form here could only ever fail — and rendering
    # it put that plan's price into the page source, which is the one thing the "Soon"
    # card exists to avoid. `plan_offer` is the single owner of "is this for sale".
    if not plan_offer(plan_code).monthly_available:
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
    # What the payment will ask for, from the same function that sets the amount on the
    # checkout attempt. The page used to print the plan catalogue's normal price, so a
    # running launch offer made the last screen before payment disagree with the charge.
    checkout_price = billing.checkout_amount(
        plan.code, plan.price_monthly, billing.billing_cycle_code
    )
    # A price to cross out only when this checkout really costs less than the normal
    # monthly price — never for a free trial or a yearly total, which are different
    # things being bought, not a discount on this one.
    checkout_price_was = (
        plan.price_monthly
        if billing.billing_cycle_code not in {"trial_7_day", "annual_auto_renewal"}
        and checkout_price < plan.price_monthly
        else None
    )
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
            checkout_price=checkout_price,
            checkout_price_was=checkout_price_was,
            promotion_ends_at=PROMOTION_ENDS_AT.isoformat(),
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
            # This page used to post "card" whatever was switched on. It asks now, and it
            # can only ask about ways of paying that really work for this plan.
            billing_method_offers=payment_method_offers_by_method(
                settings,
                plan_codes=(plan.code,),
                billing_cycle=(
                    "annual" if "annual" in billing.billing_cycle_code else "monthly"
                ),
            ),
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
        # A refusal that only says "not available" leaves a beginner with nothing to try.
        # The page should never have offered this way of paying; if it did, say which one
        # does work, in a sentence, before the provider is even looked up.
        if payment_method in PAYMENT_METHODS and not payment_method_available(
            settings,
            method=payment_method,
            plan_code=plan_code,
            billing_cycle="annual" if "annual" in billing_cycle else "monthly",
        ):
            raise BillingError(
                "payment_method_unavailable",
                payment_method_refusal(
                    settings,
                    method=payment_method,
                    plan_code=plan_code,
                    billing_cycle="annual" if "annual" in billing_cycle else "monthly",
                ),
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


#: `/dashboard/connections` is served by the redesigned page in `dashboard_test.py`.
#:
#: This is the older name for the same thing, and it is written into outgoing WhatsApp
#: replies, the Telegram account-link flow, several dashboard notices and a handful of
#: pages, so it answers rather than 404s — with a redirect, not a second copy of the
#: page. Two pages under two names is how they came to disagree in the first place.
@router.get(INTEGRATIONS_PATH, response_class=HTMLResponse, include_in_schema=False)
async def integrations_page_redirect(request: Request) -> RedirectResponse:
    return _permanent_redirect(request, CONNECTIONS_PATH)


@router.get("/dashboard/exports", response_class=HTMLResponse, include_in_schema=False)
async def exports_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    return _redirect("/dashboard?message=exports_hidden")


#: `/dashboard/settings`, `/dashboard/support` and the form that used to save the
#: settings are all served by the redesigned pages in `dashboard_test.py`.
#:
#: The older Settings page was one long form that posted the whole set of preferences
#: back to a `POST` here. The redesigned page saves one control at a time through
#: `PUT /api/v1/dashboard/preferences/settings`, and both call `AccountSettingsService`,
#: so nothing about what a setting may be was lost with the form.


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


@router.get(LEGACY_REFERRALS_PATH, include_in_schema=False)
async def referrals_page() -> RedirectResponse:
    """The old address. Referrals became the affiliate programme.

    Kept as a redirect rather than deleted: the link is in sent email and in people's
    bookmarks, and neither can be corrected after the fact.
    """

    return _redirect(AFFILIATE_PATH)


@router.get(AFFILIATE_PATH, response_class=HTMLResponse, include_in_schema=False)
async def affiliate_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """One address, three states: apply, waiting, or an approved affiliate's numbers.

    Three pages would mean three places that decide which state somebody is in, and a
    person refreshing after approval would have to find the new address themselves.
    """

    service = AffiliateService(session)
    application = await service.application_for(user.id)
    stats = None
    payouts: list[AffiliatePayoutRequest] = []
    referral_url = None
    if application is not None and application.status == "approved":
        stats = await service.stats(application)
        payouts = await service.payout_requests(user.id)
        base = str(settings.public_base_url).rstrip("/")
        referral_url = f"{base}/signup?ref={application.discount_code}"
    return templates.TemplateResponse(
        request,
        "hilal/dashboard/affiliate.html",
        await _context(
            request=request,
            session=session,
            settings=settings,
            user=user,
            page="affiliate",
            title="Affiliate",
            application=application,
            stats=stats,
            payouts=payouts,
            referral_url=referral_url,
            default_commission=DEFAULT_COMMISSION_PERCENT,
            decision_hours=DECISION_TARGET_HOURS,
            minimum_payout=MINIMUM_PAYOUT_USD,
            maximum_links=MAXIMUM_SOCIAL_LINKS,
            payout_currencies=payout_options_payload(),
            alternative_method_email=ALTERNATIVE_METHOD_EMAIL,
        ),
    )


@router.post(f"{AFFILIATE_PATH}/apply", include_in_schema=False)
async def affiliate_apply(
    display_name: str = Form(...),
    social_links: str = Form(...),
    requested_discount_code: str = Form(...),
    applicant_note: str = Form(default=""),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    # There is deliberately no `requested_commission_percent` parameter. The share is the
    # same for everybody and only an administrator changes it, so the form has no box for
    # it and this route reads none: a hand-made POST carrying one is ignored because
    # nothing here looks. Extra form fields are dropped by FastAPI, so this is silent
    # rather than an error, which is the right answer for a field that is not ours.
    service = AffiliateService(session)
    try:
        application = await service.apply(
            user_id=user.id,
            display_name=display_name,
            # One box, one link per line. Asking for "your social media link" singular is
            # how a form loses the account that actually has the audience.
            social_links=[line for line in social_links.splitlines()],
            requested_discount_code=requested_discount_code,
            applicant_note=applicant_note,
        )
    except AffiliateError as exc:
        return _redirect(f"{AFFILIATE_PATH}?error={exc.code}")
    delivery = await enqueue_affiliate_email(
        session,
        user_id=user.id,
        template_kind="affiliate_application_received",
        # The submission time is in the key, so applying again after a refusal raises a
        # second receipt rather than being swallowed as a duplicate of the first.
        event_key=f"affiliate-received:{application.id}:{int(application.submitted_at.timestamp())}",
        payload={
            # The name on the application, not the one on the account: an account made
            # through the three-screen email sign-up has no name, and this form has just
            # asked for one.
            "first_name": first_name_of(user, prefer=application.display_name),
            "requested_code": application.requested_discount_code,
            "decision_hours": DECISION_TARGET_HOURS,
        },
    )
    await session.commit()
    await try_sending_now(session, settings, delivery)
    return _redirect(f"{AFFILIATE_PATH}?message=affiliate_application_sent")


@router.post(f"{AFFILIATE_PATH}/payout", include_in_schema=False)
async def affiliate_request_payout(
    currency: str = Form(...),
    network: str = Form(...),
    destination_address: str = Form(...),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    service = AffiliateService(session)
    application = await service.application_for(user.id)
    if application is None:
        return _redirect(f"{AFFILIATE_PATH}?error=not_an_affiliate")
    try:
        await service.request_payout(
            application=application,
            currency=currency,
            network=network,
            destination_address=destination_address,
        )
    except AffiliateError as exc:
        return _redirect(f"{AFFILIATE_PATH}?error={exc.code}")
    await session.commit()
    return _redirect(f"{AFFILIATE_PATH}?message=affiliate_payout_requested")