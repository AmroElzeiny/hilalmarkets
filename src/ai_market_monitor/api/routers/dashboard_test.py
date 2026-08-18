"""The redesigned dashboard pages: the market, monitors, opportunities and the account.

These were built beside the older pages, at `/dashboard-test/...`, so they could be
finished without taking anything away. They are the older pages now and they answer at
`/dashboard/...`; the the redesigned dashboard addresses are gone, and the older copy of each
screen has been deleted rather than left behind a second address.

That second address was doing real harm while it lasted. The side menu opened these
pages; every Telegram button, every WhatsApp reply, every alert email and the front page
opened the older copies, because those were written with the `/dashboard/...` address
long before the redesign. Somebody following a message from us and somebody using the
menu were looking at two different screens with the same name and different answers.

Addresses come from `dashboard_paths.py` — one place, read by both routers, because a
path written twice is a path that drifts. Every route here still calls the context
builder the older page called, so what a page *says* has one owner too.

The file keeps its name for now. Renaming the module, the templates under
`templates/hilal/dashboard_test/` and the twenty `hm-*-test` stylesheets is a rename of
about sixty files that changes no behaviour, so it is deliberately not mixed into a
change that does.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any, get_args
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import get_market_data_provider
from ai_market_monitor.api.routers.dashboard import (
    _active_paid_plan_codes,
    _billing_history_rows,
    _billing_method_provider,
    _billing_selection_available,
    _builder_screening_context,
    _context,
    _monitor_cards_context,
    _permanent_redirect,
    _plan_checkout_allowed,
    _require_user,
    _short_datetime,
    _timezone_options,
    asset_passport_context,
    screened_market_context,
    templates,
)
from ai_market_monitor.core.asset_logos import asset_logo
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.dashboard_paths import (
    CONNECTIONS_PATH,
    LIFECYCLES_PATH,
    MARKET_PATH,
    MONITOR_PATH,
    OPPORTUNITIES_PATH,
    SETTINGS_PATH,
    SUBSCRIPTION_PATH,
    SUPPORT_PATH,
    WATCHLISTS_PATH,
)
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.core.plans import (
    PLAN_DEFINITIONS,
    PUBLIC_PLAN_PRESENTATIONS,
    PURCHASABLE_PLAN_CODES,
    UNLIMITED_SYMBOL_CAP,
    plan_offer_payload,
    visible_plan_comparison,
    visible_plan_comparison_headers,
    visible_public_plan_codes,
)
from ai_market_monitor.core.site_content import TopbarAction
from ai_market_monitor.db.models import (
    BillingCheckoutAttempt,
    CanonicalAsset,
    DashboardPreference,
    Plan,
    Strategy,
    SupportRequest,
    TelegramConnection,
    Trial,
    User,
    UserIdentity,
    WhatsAppConnection,
)
from ai_market_monitor.db.models.enums import (
    ConnectionStatus,
    DeliveryChannel,
    IdentityProvider,
    ShariaAssetStatus,
    StrategyStatus,
)
from ai_market_monitor.engine.builder_boolean import boolean_limits
from ai_market_monitor.schemas.strategy import AlertPolicy
from ai_market_monitor.services.account_settings import (
    ALERT_DAYS,
    MARKET_PROVIDERS,
    MUTED_SYMBOL_LIMIT,
    AccountSettingsService,
    clean_muted_symbols,
)
from ai_market_monitor.services.alert_emails import alert_email_address
from ai_market_monitor.services.billing import BillingService
from ai_market_monitor.services.entitlements import EntitlementService, PlanCatalogService
from ai_market_monitor.services.interfaces import MarketDataProvider
from ai_market_monitor.services.lifecycle_dashboard import lifecycle_cards
from ai_market_monitor.services.notification_preferences import (
    NotificationPreferenceService,
    offered_channels,
)
from ai_market_monitor.services.product_language import (
    check_presentation,
    checks_in_words,
    every_message_kind,
    gap_in_words,
    how_long_ago,
    how_often,
    number_in_words,
    opportunity_presentation,
    watchlist_presentation,
    why_no_message,
)
from ai_market_monitor.services.setup_observability import SetupObservabilityService
from ai_market_monitor.services.support_intake import support_intake_limits
from ai_market_monitor.services.telegram_account_links import (
    TelegramAccountLinkError,
    TelegramAccountLinkService,
)

router = APIRouter(tags=["dashboard"])

#: Kept under its old name here because every route below reads it, and renamed to what
#: it is in `dashboard_paths.py`, which is where the address itself lives.
MARKET_BASE_PATH = MARKET_PATH

#: The words a person reads for each delivery channel.
#:
#: Only the words. Which channels exist is not decided here.
_CHANNEL_WORDS = {
    "web": ("In the dashboard", "A notice waiting for you the next time you sign in."),
    "telegram": ("Telegram", "A message from the Hilal Markets bot."),
    "whatsapp": ("WhatsApp", "A message on WhatsApp."),
    "email": ("Email", "A message to the address you signed up with."),
}


def _alert_channels(settings: Settings) -> list[dict[str, str]]:
    """Every way a person can really be told, in the words they read.

    Two questions, both answered by their owner and then met in the middle:

    * *Would the compiler accept it?* — ``AlertPolicy``'s own accepted values, so the
      canvas can never offer something the alert schema would refuse.
    * *Can we actually deliver it?* — ``offered_channels``, so a retired channel is
      never offered however long its value lingers in the schema for old rows.

    Asking only the first question is what put a retired channel on this page. The
    schema still accepts it so that historical alerts stay readable, so the canvas was
    offering a way to be told that nothing would ever deliver. The retirement note in
    ``docs/`` explains why the value has to stay in the enum; ``offered_channels`` is
    where the list of what we really deliver lives.
    """

    annotation = AlertPolicy.model_fields["channels"].annotation
    literal = get_args(annotation)[0] if get_args(annotation) else None
    accepted = [str(value) for value in get_args(literal)] if literal is not None else []
    deliverable = {channel.value for channel in offered_channels(settings)}
    channels: list[dict[str, str]] = []
    for value in accepted:
        if value not in deliverable:
            continue
        label, explanation = _CHANNEL_WORDS.get(
            value, (value.replace("_", " ").capitalize(), "")
        )
        channels.append({"value": value, "label": label, "explanation": explanation})
    return channels

#: What every page here tells the shared shell about itself.
#:
#: `passport_quick_view_variant` picks the redesigned popup instead of the older one, so
#: only one Passport dialog is ever in the document.
#:
#: The topbar no longer carries a create button of its own. Each page declares what
#: belongs up there through `topbar_actions`, so there is nothing to hide from the pages
#: that do not want one — which is what `hide_new_watchlist_cta` used to be for.
#: `hilal_chat` puts the dashboard assistant on every one of these pages and on no
#: other. Decided here rather than by the browser reading the URL: which assistant a
#: page carries is a server decision, exactly like the Passport popup above it, and a
#: page that guessed from its own address would put Hilal on `/dashboard` the first
#: time a route moved.
_PATH_CHROME = {
    "passport_quick_view_variant": "test",
    "hilal_chat": True,
}


@router.get(MARKET_BASE_PATH, response_class=HTMLResponse, include_in_schema=False)
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
    context = await screened_market_context(
        request=request,
        methodology_id_input=methodology_id_input,
        status_filter=status_filter,
        exchange=exchange,
        quote_asset=quote_asset,
        liquidity=liquidity,
        search=search,
        view=view,
        page_number=page_number,
        user=user,
        session=session,
        settings=settings,
        provider=provider,
        base_path=MARKET_BASE_PATH,
    )
    context.update(_PATH_CHROME)
    return templates.TemplateResponse(request, "hilal/dashboard_test/market.html", context)


@router.get(MONITOR_PATH, response_class=HTMLResponse, include_in_schema=False)
async def monitor_canvas_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """The visual canvas, as its own page.

    Nothing about a rule is decided here. The page draws what the Builder contract at
    ``/api/v1/dashboard/setup-chat/builder-contract`` offers — the same contract the
    guided Builder reads — so the canvas can never present a mechanic, an option or a
    limit the compiler would refuse.

    Only two things are settled server-side: the shape limits, so the canvas can say
    "this is as deep as a monitor may go" before the catalogue has finished loading,
    and which screening standard the universe would be drawn from.
    """

    context = await _context(
        request=request,
        session=session,
        settings=settings,
        user=user,
        page="monitor_canvas",
        title="Monitor",
        monitor_contract_url="/api/v1/dashboard/setup-chat/builder-contract",
        monitor_limits=boolean_limits().to_dict(),
        builder_screening=await _builder_screening_context(session, user, settings),
        watchlists_path="/dashboard/watchlists",
        market_path=MARKET_BASE_PATH,
    )
    context["monitor_channels"] = _alert_channels(settings)
    context.update(_PATH_CHROME)
    # There is no coin on this page, so there is nothing for a Passport popup to open.
    context["passport_quick_view_variant"] = "none"
    return templates.TemplateResponse(request, "hilal/dashboard_test/monitor.html", context)


@router.get(
    MARKET_BASE_PATH + "/{asset_slug}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def asset_passport_page(
    request: Request,
    asset_slug: str,
    methodology_id: UUID | None = Query(default=None),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    context = await asset_passport_context(
        request=request,
        asset_slug=asset_slug,
        methodology_id=methodology_id,
        user=user,
        session=session,
        settings=settings,
        market_base_path=MARKET_BASE_PATH,
    )
    context.update(_PATH_CHROME)
    return templates.TemplateResponse(request, "hilal/dashboard_test/passport.html", context)


@router.get(
    MARKET_BASE_PATH + "/{asset_slug}/report",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def asset_passport_report(
    request: Request,
    asset_slug: str,
    methodology_id: UUID | None = Query(default=None),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """The Passport as one printable evidence record.

    Same read model as the Passport page. It is laid out for printing and for handing
    to someone else, so nothing is behind a disclosure and nothing is summarised away.
    """

    context = await asset_passport_context(
        request=request,
        asset_slug=asset_slug,
        methodology_id=methodology_id,
        user=user,
        session=session,
        settings=settings,
        market_base_path=MARKET_BASE_PATH,
    )
    passport = context.get("passport")
    if passport is None:  # pragma: no cover - the context builder raises first
        raise HTTPException(status_code=404, detail="Passport not found")
    context["title"] = f"{passport.assessment.canonical_asset} Evidence report"
    # A printable record needs no popup at all.
    context["passport_quick_view_variant"] = "none"
    context["hide_new_watchlist_cta"] = True
    return templates.TemplateResponse(request, "hilal/dashboard_test/report.html", context)


# ── Watchlists ───────────────────────────────────────────────────────────────


def _watchlist_view(item: dict) -> dict:
    """One Watchlist, turned from stored rows into the words a person reads.

    Every judgement the older page made inside its own template is made here instead:
    what the status really is, whether the list has ever run, what is holding it back,
    and how long ago it looked. A template that decides things cannot be tested, and
    the next page needing the same decision will make a different one.
    """

    strategy = item["strategy"]
    health = item["health"]
    scan = item["latest_scan"]
    methodology = item["methodology"]
    blocker = item["main_bottleneck"]

    presentation = watchlist_presentation(
        strategy.status.value,
        has_approved_version=bool(strategy.active_version_id),
    )
    score = int(round(float(getattr(health, "score", 0) or 0)))

    # "Is it working?" is one question with three honest answers, and the third one —
    # "it has not looked yet" — is the one the older page could not say. It showed a
    # score of 43 out of 100 for a list that had never run, which is a number about
    # nothing.
    if scan is None:
        working = {
            "tone": "neutral",
            "label": "Not looked yet",
            "detail": "This list has not checked the market for the first time.",
        }
    elif score >= 80:
        working = {
            "tone": "success",
            "label": "Working well",
            "detail": "Every check it needs is arriving.",
        }
    elif score >= 50:
        working = {
            "tone": "warning",
            "label": "Working, with a gap",
            "detail": getattr(health, "main_issue", None) or "Some checks are not arriving.",
        }
    else:
        working = {
            "tone": "danger",
            "label": "Needs a look",
            "detail": getattr(health, "main_issue", None) or "Most checks are not arriving.",
        }

    return {
        "id": str(strategy.id),
        "name": strategy.name,
        "description": (strategy.description or "").strip() or None,
        "status": presentation.label,
        "status_detail": presentation.explanation,
        "status_tone": presentation.semantic_status,
        "is_paused": strategy.status.value == "paused",
        "watching_count": int(item["eligible_asset_count"] or 0),
        "standard": methodology.name if methodology else None,
        "standard_version": methodology.version if methodology else None,
        "working": working,
        # `None`, not the words "Not yet". `how_long_ago` answers a missing moment with
        # a display string, which is truthy — so any template guarding on this field
        # rather than on the exact moment beside it renders "Looked Not yet". One page
        # guarded correctly and the next one did not; the trap is removed here instead
        # of being remembered at each call site.
        "last_checked": how_long_ago(scan.completed_at) if scan is not None else None,
        "last_checked_exact": scan.completed_at if scan is not None else None,
        # The one thing most often stopping this list, in its own words. Named "holding
        # it back" rather than "bottleneck", which is a word about pipes.
        "holding_back": (
            {
                "name": blocker["condition_label"],
                "share": int(round(float(blocker["blocking_rate"] or 0))),
            }
            if blocker
            else None
        ),
        "needs_repair": bool(item["pending_repair"]),
        "edit_url": f"/dashboard/strategies/{strategy.id}/builder",
        "opportunities_url": f"{OPPORTUNITIES_PATH}?monitor={strategy.id}",
    }


@router.get(WATCHLISTS_PATH, response_class=HTMLResponse, include_in_schema=False)
async def watchlists_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """The monitors a person is watching with, redesigned.

    The rows come from `_monitor_cards_context`, which is the same function the rest of
    the product asks about monitors — called, never copied — so no two screens can
    disagree about how many are running.
    """

    cards = await _monitor_cards_context(session, user)
    views = [_watchlist_view(item) for item in cards]
    context = await _context(
        request=request,
        session=session,
        settings=settings,
        user=user,
        page="watchlists",
        title="Monitors",
        # The page's own "create" button used to sit inside its heading, and only when
        # there was already at least one monitor — so the emptiest page in the product
        # was the one with no way to make anything. It is a topbar action now: same
        # place, same shape and always there, whatever the page below it shows.
        topbar_actions=(
            TopbarAction("Create a monitor", MONITOR_PATH, "circle_plus", "primary"),
        ),
        watchlists=views,
        watchlists_counts={
            "all": len(views),
            "watching": sum(1 for item in views if item["status"] == "Watching"),
            "paused": sum(1 for item in views if item["is_paused"]),
            "unfinished": sum(1 for item in views if item["status"] == "Not finished"),
            "attention": sum(
                1 for item in views if item["working"]["tone"] in {"warning", "danger"}
            ),
        },
        opportunities_path=OPPORTUNITIES_PATH,
        market_path=MARKET_BASE_PATH,
        monitor_path=MONITOR_PATH,
        new_watchlist_path="/dashboard/strategies/new",
    )
    context.update(_PATH_CHROME)
    # No coin on this page, so no Passport popup belongs on it.
    context["passport_quick_view_variant"] = "none"
    return templates.TemplateResponse(
        request, "hilal/dashboard_test/watchlists.html", context
    )


# ── Connections ──────────────────────────────────────────────────────────────


def _channel_card(
    *,
    value: str,
    connected: bool,
    chosen: bool,
    available: bool,
    icon: str,
    what_it_is: str,
    facts: list[dict[str, str]],
    unavailable_reason: str | None = None,
    unavailable_fix: str | None = None,
    always_on: bool = False,
    linked: bool | None = None,
) -> dict[str, Any]:
    """One way of being told, in the words a person reads.

    Every judgement is made here rather than in the template. The older page decided in
    Jinja whether a channel counted as connected — `telegram and telegram.status.value
    == 'active' and telegram.alerts_enabled` was written out three times in one file —
    and a template that decides things cannot be tested.

    **Three facts, not one, and they are kept apart on purpose.**

    * ``linked`` — there is an account on record. Only this decides whether the page
      offers "Link" or "Unlink".
    * ``connected`` — that account is in a state where a message would actually arrive.
    * ``chosen`` — the person has switched this way of being told on.

    ``linked`` and ``connected`` used to be the same value, and the gap between them was
    a dead end a person could not get out of. A Telegram account that is on record but
    paused, or waiting, is not "connected" — so the page showed **Link Telegram** for an
    account that was already linked, and never showed Unlink at all. Pressing Link then
    failed, because that Telegram account was already attached to them. There was no way
    forward and no way back.

    ``linked`` defaults to ``connected`` so every channel that really does have one fact
    keeps behaving exactly as it did.
    """

    label, explanation = _CHANNEL_WORDS.get(value, (value.capitalize(), ""))
    if not available:
        # The state line says *what* the state is; the panel below it says *why* and
        # what would change it. Putting the reason in both is the same sentence twice on
        # one card, which reads as a mistake and pushes everything else further down.
        state = {
            "label": "Not available yet",
            "tone": "neutral",
            "icon": "lock",
            "meaning": "Nothing arrives here, and there is nothing to switch on.",
        }
    elif always_on:
        state = {
            "label": "Always on",
            "tone": "success",
            "icon": "check",
            "meaning": "You can always come back and read what happened.",
        }
    elif connected and chosen:
        state = {
            "label": "On",
            "tone": "success",
            "icon": "check",
            "meaning": "Messages are being sent here.",
        }
    elif connected and not chosen:
        state = {
            "label": "Connected, but switched off",
            "tone": "warning",
            "icon": "pause",
            "meaning": "It is ready. Nothing is being sent here until you switch it on.",
        }
    elif linked:
        # There is an account on record and nothing can arrive on it: a link that was
        # started and never finished, or one that has since been stopped.
        #
        # "Not set up" was what this said, and it was the wrong half of the truth. Some
        # of it *is* set up — enough that starting again from scratch fails, because the
        # half-made row is already attached to this person. So the page says what is
        # actually true and offers the one action that clears it.
        state = {
            "label": "Not finished",
            "tone": "warning",
            "icon": "alert",
            "meaning": (
                "This was started and never finished, so nothing can arrive here. "
                "Unlink it below and link it again to start over."
            ),
        }
    else:
        state = {
            "label": "Not set up",
            "tone": "neutral",
            "icon": "plus",
            "meaning": "Nothing is being sent here yet.",
        }
    return {
        "value": value,
        "label": label,
        "what_it_is": what_it_is or explanation,
        "icon": icon,
        "state": state,
        "connected": connected,
        # There is an account on record, whether or not it can receive anything today.
        # This, and only this, decides whether the page offers Link or Unlink.
        "linked": bool(connected if linked is None else linked),
        "chosen": chosen,
        "available": available,
        "always_on": always_on,
        "facts": facts,
        "unavailable_reason": unavailable_reason,
        "unavailable_fix": unavailable_fix,
    }


@router.get(
    CONNECTIONS_PATH,
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def connections_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Where a person is told, redesigned.

    Three questions, in the order somebody asks them: **where will you be told, is it
    working, and what will you be told about.** The older page answered the first one
    twice — three summary tiles at the top repeating the two cards underneath — and
    never answered the third at all.

    Nothing about which channels exist is decided here. `offered_channels` is the one
    owner of that, so a channel that cannot deliver can never appear on this page.
    """

    # Taken before anything commits. Minting the Telegram link below writes a row, and a
    # commit expires every object loaded in this session — so `user.id` after it is a
    # lazy reload, which raises rather than reloading inside an async request.
    user_id = user.id
    telegram_connect_url = None
    telegram_start_command = None
    try:
        telegram_connect_url = await TelegramAccountLinkService(
            session, settings
        ).create_dashboard_start_link(user_id=user_id)
        if telegram_connect_url and "?start=" in telegram_connect_url:
            telegram_start_command = (
                "/start " + telegram_connect_url.split("?start=", 1)[1].split("&", 1)[0]
            )
        await session.commit()
    except TelegramAccountLinkError:
        # The page still renders. A link we could not mint is one button that says so,
        # not a page that fails to load — everything else here is still worth reading.
        await session.rollback()

    # Loaded again, because the shared page shell reads the person's own name and time
    # zone off it and the commit above expired them.
    await session.refresh(user)

    telegram = await session.scalar(
        select(TelegramConnection).where(TelegramConnection.user_id == user_id)
    )
    whatsapp = await session.scalar(
        select(WhatsAppConnection).where(WhatsAppConnection.user_id == user_id)
    )
    entitlement = await EntitlementService(session).current(user_id)
    preference = await NotificationPreferenceService(session, settings).current(user_id)
    offered = offered_channels(settings)
    chosen = {channel.value for channel in preference.channels}

    telegram_live = bool(
        telegram
        and telegram.status == ConnectionStatus.ACTIVE
        and telegram.alerts_enabled
        and telegram.chat_id
    )
    whatsapp_available = bool(
        DeliveryChannel.WHATSAPP in offered and entitlement.feature_enabled("whatsapp")
    )
    email_available = DeliveryChannel.EMAIL in offered
    # Asked of the one owner, not read off the account. "The email address on this
    # account" is a real question — there can be several, only a verified one may
    # receive alerts — and the page must show the address the sender will actually use.
    account_email = await alert_email_address(session, user_id)

    channels = [
        _channel_card(
            value="web",
            connected=True,
            chosen=True,
            available=True,
            always_on=True,
            # The dashboard's own mark. Every other card on this page shows the real logo
            # of the place it means — Telegram's, WhatsApp's, an envelope — and this one
            # showed a bell, which is what a notice *is* rather than where it arrives.
            icon="dashboard",
            what_it_is="A notice waiting for you here, every time. This one cannot be "
            "switched off.",
            facts=[],
        ),
        _channel_card(
            value="email",
            connected=bool(email_available and account_email),
            chosen=DeliveryChannel.EMAIL.value in chosen,
            available=bool(email_available and account_email),
            icon="mail",
            what_it_is="A message to the address you signed up with. Nothing to install.",
            facts=[{"name": "Goes to", "value": account_email or "No address on this account"}],
            unavailable_reason=(
                "There is no email address on this account."
                if email_available and not account_email
                else "Email notices are not switched on for this platform yet."
            ),
            unavailable_fix=(
                "Add an email address to your account and this switches on by itself."
                if email_available and not account_email
                else "Nothing for you to do. This appears here as soon as it opens."
            ),
        ),
        _channel_card(
            value="telegram",
            connected=telegram_live,
            # A row exists, whatever state it is in. Unlink has to be offered for every
            # one of those states, or a half-finished link becomes permanent.
            linked=telegram is not None,
            chosen=DeliveryChannel.TELEGRAM.value in chosen,
            available=DeliveryChannel.TELEGRAM in offered,
            icon="telegram",
            what_it_is="A private message from the Hilal Markets bot. Fastest of the three.",
            facts=[
                {
                    "name": "Linked account",
                    "value": ("@" + telegram.username)
                    if telegram and telegram.username
                    else "Not linked yet",
                },
                {
                    "name": "Last message",
                    "value": how_long_ago(telegram.last_delivery_at)
                    if telegram and telegram.last_delivery_at
                    else "None sent yet",
                },
            ],
            unavailable_reason="Telegram is not switched on for this platform yet.",
        ),
        _channel_card(
            value="whatsapp",
            connected=bool(whatsapp and whatsapp.status == ConnectionStatus.ACTIVE),
            chosen=DeliveryChannel.WHATSAPP.value in chosen,
            available=whatsapp_available,
            icon="whatsapp",
            what_it_is="A message on WhatsApp, to a number you confirm yourself.",
            facts=[],
            unavailable_reason=(
                "WhatsApp is not part of your plan."
                if DeliveryChannel.WHATSAPP in offered
                else "WhatsApp notices are not open yet. We are still finishing them."
            ),
            unavailable_fix=(
                "Change your plan and WhatsApp appears here."
                if DeliveryChannel.WHATSAPP in offered
                else "Nothing for you to do. This page will show it the day it opens."
            ),
        ),
    ]

    working = [item for item in channels if item["state"]["label"] in {"On", "Always on"}]
    # "Only the dashboard" is the state worth saying something about: a person who set
    # nothing up will not be told anywhere they would notice.
    only_in_app = len(working) == 1

    context = await _context(
        request=request,
        session=session,
        settings=settings,
        user=user,
        page="integrations",
        title="Connections",
        channels=channels,
        only_in_app=only_in_app,
        message_kinds=every_message_kind(),
        telegram_connect_url=telegram_connect_url,
        telegram_start_command=telegram_start_command,
        telegram_connected=telegram_live,
        telegram_linked=telegram is not None,
        settings_path="/dashboard/settings",
        opportunities_path=OPPORTUNITIES_PATH,
        watchlists_path=WATCHLISTS_PATH,
    )
    context.update(_PATH_CHROME)
    # No coin on this page, so no Passport popup belongs on it.
    context["passport_quick_view_variant"] = "none"
    return templates.TemplateResponse(
        request, "hilal/dashboard_test/connections.html", context
    )


# ── Opportunities ────────────────────────────────────────────────────────────

#: The five things one card can be, in the order a person scans for them.
#:
#: Each one is a `kind` from `opportunity_presentation`, so the filter and the card can
#: never sort a coin into one group while calling it another.
_OPPORTUNITY_BUCKETS: tuple[tuple[str, str, str], ...] = (
    ("ready", "Ready for you", "check"),
    ("close", "Nearly there", "spark"),
    ("forming", "Still forming", "clock"),
    ("unchecked", "Could not check", "alert"),
    ("ended", "Finished", "history"),
)

#: At most this many "why was I not told" answers are worked out for one page load.
#:
#: Each one reads a whole opportunity's history. A person cannot read fifty of them,
#: and a page that quietly did fifty database reads to render buttons nobody presses is
#: a page that gets slower every month without anybody noticing.
_WHY_ANSWER_LIMIT = 12


async def _assets_by_ticker(
    session: AsyncSession, rows: list[dict[str, Any]]
) -> dict[str, CanonicalAsset]:
    """The stored record for every coin on the page, keyed by its ticker.

    Only for the logo. The record holds the picture the platform kept when the coin's
    identity was verified, which is the only picture that exists for a coin the shared
    icon catalogue has never heard of. Nothing about a screening status is read here —
    that comes from the assessment, as it does everywhere else.
    """

    tickers = {
        str(row.get("symbol") or "").partition("/")[0].upper()
        for row in rows
        if row.get("symbol")
    }
    tickers.discard("")
    if not tickers:
        return {}
    found = (
        await session.scalars(
            select(CanonicalAsset)
            .where(CanonicalAsset.symbol.in_(tickers))
            .order_by(CanonicalAsset.created_at.desc())
        )
    ).all()
    by_ticker: dict[str, CanonicalAsset] = {}
    for asset in found:
        by_ticker.setdefault(asset.symbol.upper(), asset)
    return by_ticker


def _check_view(
    *,
    label: str,
    timeframe: str | None,
    outcome: str | None,
    saw: object,
    wanted: object,
    must_pass: bool | None,
) -> dict[str, Any]:
    """One thing on a person's own list, and what the platform saw for it."""

    presentation = check_presentation(outcome)
    return {
        "name": label,
        "how_often": how_often(timeframe),
        "label": presentation.label,
        "tone": presentation.semantic_status,
        "saw": number_in_words(saw),
        "wanted": number_in_words(wanted),
        "must_pass": must_pass,
    }


def _checks(passed: int, total: int) -> dict[str, Any] | None:
    """How much of somebody's own list is true, as a count and as a bar width.

    The count is clamped to its own total. A stored count that outran it would draw a
    bar past the end of its track and tell a screen reader "6 of 5", which is nothing a
    person can act on — and it must never be silently rewritten as a smaller total.
    """

    if total <= 0:
        return None
    shown = max(0, min(passed, total))
    return {
        "passed": shown,
        "total": total,
        "sentence": checks_in_words(passed, total),
        "percent": int(round(shown / total * 100)),
    }


def _changed_times(count: int) -> str | None:
    """How many times an opportunity moved, with its plural right.

    The older page printed "1 lifecycle events" on screen. Two faults in three words: a
    plural that does not agree, and a word from inside the machine.
    """

    if count <= 0:
        return None
    if count == 1:
        return "Changed once since we found it"
    return f"Changed {count} times since we found it"


def _opportunity_from_readiness(
    row: dict[str, Any], assets: dict[str, CanonicalAsset]
) -> dict[str, Any]:
    """One card, from the row the readiness check writes.

    ``assets`` is the coin's own record, looked up by ticker. It is passed in rather
    than fetched here because every card on the page needs it and one query for all of
    them is one query; what matters is that it arrives at all. This function used to
    write ``logo_url: None`` unconditionally, so any coin the shared icon catalogue has
    never heard of — every small or newly listed token — drew three letters even when
    the platform was holding a picture of it.
    """

    required = row.get("required") or {}
    passed = int(required.get("passed") or 0)
    total = int(required.get("total") or 0)
    state = opportunity_presentation(row.get("state"))
    blocker = row.get("blocker") or {}
    symbol = str(row.get("symbol") or "")
    coin = symbol.partition("/")[0].upper()
    asset = assets.get(coin)
    logo = asset_logo(coin, asset.provider_ids if asset is not None else None)

    waiting_on = None
    if blocker.get("label"):
        waiting_on = {
            "name": str(blocker["label"]),
            "sentence": gap_in_words(
                outcome=blocker.get("outcome"),
                saw=blocker.get("actual"),
                wanted=blocker.get("required"),
                distance=blocker.get("distance"),
            ),
        }

    return {
        "key": str(row.get("id")),
        "setup_id": row.get("setup_id"),
        "symbol": symbol,
        "coin": coin,
        # A coin the recorded history has never seen still gets its own logo, and it
        # gets *every* source for it — the picture stored on its own record first, then
        # the shared catalogue. Both are built in one place for the whole product, so
        # this cannot know a different set of sources from the card beside it.
        "logo_module_url": logo.module_url,
        "logo_url": logo.image_url,
        "watchlist_name": str(row.get("monitor_name") or ""),
        "watchlist_id": str(row.get("monitor_id") or ""),
        "how_often": how_often(row.get("timeframe")),
        # The size of candle itself, for the price picture. It is never shown to a
        # person — `how_often` above is what they read.
        "candle_size": str(row.get("timeframe") or ""),
        "state": {
            "label": state.label,
            "meaning": state.meaning,
            "tone": state.semantic_status,
            "kind": state.kind,
        },
        # A count, never a bare percentage. The percentage is kept only so the bar has a
        # width; the words beside it are what a person reads.
        "checks": _checks(passed, total),
        "waiting_on": waiting_on,
        # Old numbers are a fact worth saying, and they are not a status. Showing them
        # as one is how the older page ended up with two status words on one card.
        "old_numbers": row.get("data_health") == "stale",
        "changed": how_long_ago(row.get("last_changed_at")),
        "changed_exact": row.get("last_changed_at"),
        "what_we_saw": [
            _check_view(
                label=str(item.get("label") or item.get("key") or "A check"),
                timeframe=item.get("timeframe"),
                outcome=item.get("outcome"),
                saw=item.get("actual"),
                wanted=item.get("required_value"),
                must_pass=bool(item.get("required")) if "required" in item else None,
            )
            for item in (row.get("latest_values") or [])
        ],
        "changed_times": None,
        "alert_id": None,
        "can_ask_why": False,
        "why": None,
        "standard_id": "",
    }


def _opportunity_from_journey(card: dict[str, Any]) -> dict[str, Any]:
    """One card, from the opportunity's own recorded history.

    The same shape as the readiness row above, on purpose. The older page kept these two
    apart and drew both, so one coin appeared twice under two different words.
    """

    state = opportunity_presentation(card.get("state"))
    passed_conditions = list(card.get("passed_conditions") or [])
    monitoring = list(card.get("monitoring_conditions") or [])
    total = len(passed_conditions) + len(monitoring)
    symbol = str(card.get("symbol") or "")
    coin = symbol.partition("/")[0].upper()

    waiting_on = None
    if monitoring:
        first = monitoring[0]
        waiting_on = {
            "name": str(first.get("name") or "A check"),
            "sentence": gap_in_words(
                outcome=first.get("state"),
                saw=first.get("actual"),
                wanted=first.get("required"),
            ),
        }

    return {
        "key": str(card.get("id")),
        "setup_id": str(card.get("id")),
        "symbol": symbol,
        "coin": coin,
        "logo_module_url": card.get("logo_module_url"),
        "logo_url": card.get("logo_url"),
        "watchlist_name": str(card.get("strategy_name") or ""),
        "watchlist_id": "",
        "how_often": how_often(card.get("timeframe")),
        "candle_size": str(card.get("timeframe") or ""),
        "state": {
            "label": state.label,
            "meaning": state.meaning,
            "tone": state.semantic_status,
            "kind": state.kind,
        },
        "checks": _checks(len(passed_conditions), total),
        "waiting_on": waiting_on,
        "old_numbers": False,
        "changed": how_long_ago(card.get("last_evaluated_at")),
        "changed_exact": card.get("last_evaluated_at"),
        # Whether a recorded check was one of the required ones is not kept on this
        # record, so nothing here says it was. An invented "must be true" marker is a
        # claim about somebody's own rule that we cannot support.
        "what_we_saw": [
            _check_view(
                label=str(item.get("name") or "A check"),
                timeframe=item.get("timeframe"),
                outcome=item.get("state"),
                saw=item.get("actual"),
                wanted=item.get("required"),
                must_pass=None,
            )
            for group in (passed_conditions, monitoring)
            for item in group
        ],
        "changed_times": _changed_times(len(card.get("completed_events") or [])),
        "alert_id": str(card["latest_alert_id"]) if card.get("latest_alert_id") else None,
        "can_ask_why": bool(card.get("show_why_no_alert")),
        "why": None,
        # Which screening standard this opportunity was found under, so the Passport
        # popup answers about the same standard the Watchlist actually used.
        "standard_id": str(card["sharia_methodology_id"])
        if card.get("sharia_methodology_id")
        else "",
    }


def _merge_opportunities(
    readiness: list[dict[str, Any]], journeys: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """One card per coin, from the two records the platform keeps about it.

    The readiness row knows how much of a person's list is true right now. The recorded
    history knows what happened and whether a message was sent. They describe the same
    coin, and the older page showed them as two separate cards with different words on
    each — a person could not tell whether they were looking at one thing or two.

    Neither record is dropped: a history with no readiness row still gets a card, so an
    opportunity cannot vanish from this page because a short-lived row was tidied away.
    """

    by_setup = {
        str(card["setup_id"]): card for card in journeys if card.get("setup_id")
    }
    merged: list[dict[str, Any]] = []
    used: set[str] = set()
    for card in readiness:
        setup_id = str(card.get("setup_id") or "")
        history = by_setup.get(setup_id)
        if history is not None:
            used.add(setup_id)
            card = {
                **card,
                # Whichever record actually has a picture wins, rather than the history
                # record always winning. Both are resolved by the same owner now, but
                # the history record is keyed by the *verified* canonical symbol and the
                # readiness row by the traded symbol's base, and those two can differ —
                # so "history wins" would sometimes replace a real logo with nothing.
                "logo_module_url": history["logo_module_url"] or card["logo_module_url"],
                "logo_url": history["logo_url"] or card["logo_url"],
                "changed_times": history["changed_times"],
                "alert_id": history["alert_id"],
                "can_ask_why": history["can_ask_why"],
                "standard_id": history["standard_id"],
                # The recorded checks carry the exact values that were stored with the
                # evidence; the readiness row carries only the latest reading.
                "what_we_saw": history["what_we_saw"] or card["what_we_saw"],
            }
        merged.append(card)
    merged.extend(card for card in journeys if str(card.get("setup_id")) not in used)
    return merged


# `response_model=None` because this route can answer with a page *or* a redirect, and
# FastAPI otherwise tries to build a Pydantic response model out of the union and
# refuses at import time. That refusal is not a failing test — it stops the application
# module importing at all, so every suite that builds the app errors during collection
# and none of them names this route.
@router.get(
    OPPORTUNITIES_PATH,
    response_class=HTMLResponse,
    response_model=None,
    include_in_schema=False,
)
async def opportunities_page(
    request: Request,
    monitor: str | None = Query(default=None, max_length=64),
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse | RedirectResponse:
    """What the lists found, redesigned.

    Three questions, in the order somebody asks them: what is closest, why is it not
    there yet, and what did we actually see? Every number on the page can be opened and
    read; nothing is a score without its evidence behind it.
    """

    # A link that asks something this page cannot answer goes to the page that can.
    #
    # This address used to serve Evidence and Activity, and messages we sent years ago
    # still say so: an alert email links to `?tab=compliance_changes`, Compliance Watch
    # writes the same address into every recorded change, WhatsApp replies with it and
    # the front page's "screening changes" tile points at it. None of those questions —
    # what changed, what happened to one setup, why no alert arrived — is one this page
    # answers, and showing today's opportunities to somebody who asked what changed
    # yesterday is worse than sending them one step further.
    #
    # `?monitor=` is deliberately not in the list: this page filters by monitor itself.
    if {"tab", "setup", "investigation", "symbol"} & set(request.query_params):
        return _permanent_redirect(request, LIFECYCLES_PATH)

    selected: UUID | None = None
    if monitor:
        try:
            selected = UUID(monitor)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid Watchlist") from exc
        owned = await session.scalar(
            select(Strategy.id).where(
                Strategy.id == selected,
                Strategy.user_id == user.id,
                Strategy.archived_at.is_(None),
            )
        )
        if owned is None:
            raise HTTPException(status_code=404, detail="Watchlist not found")

    observability = SetupObservabilityService(session, settings)
    radar = await observability.radar(user.id, monitor_id=selected, page_size=100)
    journeys = await lifecycle_cards(session, user.id, monitor_id=selected)
    # One query for every coin on the page, not one per card.
    assets = await _assets_by_ticker(session, radar["items"])
    views = _merge_opportunities(
        [_opportunity_from_readiness(row, assets) for row in radar["items"]],
        [_opportunity_from_journey(card) for card in journeys],
    )

    entitlement = await EntitlementService(session).current(user.id)
    can_investigate = bool(
        entitlement and entitlement.feature_enabled("missed_alert_investigations")
    )
    answered = 0
    for view in views:
        view["can_ask_why"] = view["can_ask_why"] and can_investigate
        if not view["can_ask_why"] or answered >= _WHY_ANSWER_LIMIT:
            view["can_ask_why"] = False
            continue
        try:
            record = await observability.investigation(
                user.id, UUID(str(view["setup_id"]))
            )
        except (LookupError, ValueError):
            # Nothing to explain rather than a broken button. A record that cannot be
            # read is not an answer, and inventing one here would be worse than silence.
            view["can_ask_why"] = False
            continue
        answer = why_no_message(record.get("primary_category"))
        view["why"] = {
            "headline": answer.headline,
            "meaning": answer.meaning,
            "what_to_do": answer.what_to_do,
            "tone": answer.semantic_status,
        }
        answered += 1

    watchlists = list(
        (
            await session.execute(
                select(Strategy.id, Strategy.name)
                .where(Strategy.user_id == user.id, Strategy.archived_at.is_(None))
                .order_by(Strategy.name.asc())
            )
        ).all()
    )

    counts = {"all": len(views)}
    for key, _label, _glyph in _OPPORTUNITY_BUCKETS:
        counts[key] = sum(1 for view in views if view["state"]["kind"] == key)

    context = await _context(
        request=request,
        session=session,
        settings=settings,
        user=user,
        page="activity",
        title="Opportunities",
        # The way back to the monitors that found all this. It was a button inside the
        # page heading; it is the same action in the same place on every page now.
        topbar_actions=(TopbarAction("Monitors", WATCHLISTS_PATH, "radar"),),
        opportunities=views,
        opportunity_counts=counts,
        opportunity_buckets=_OPPORTUNITY_BUCKETS,
        opportunity_watchlists=watchlists,
        selected_watchlist_id=str(selected) if selected else "",
        selected_watchlist_name=next(
            (name for identifier, name in watchlists if identifier == selected), ""
        ),
        opportunities_path=OPPORTUNITIES_PATH,
        watchlists_path=WATCHLISTS_PATH,
        market_path=MARKET_BASE_PATH,
        monitor_path=MONITOR_PATH,
        new_watchlist_path="/dashboard/strategies/new",
    )
    # Every card names a coin, so this page carries the path's Passport popup. It is
    # opened rather than linked to on purpose: a link straight to the full Passport is a
    # visible button that leads to a "not found" page whenever the coin has no published
    # record, and the popup already answers that case in words — with the link to the
    # full Passport inside it once there is one.
    context.update(_PATH_CHROME)
    return templates.TemplateResponse(
        request, "hilal/dashboard_test/opportunities.html", context
    )


# ── Subscription ─────────────────────────────────────────────────────────────

#: What each plan limit means, in the words a person reads, and whether we can count
#: what they have already used against it.
#:
#: Keyed by the limit's stored name, so a plan whose limits change cannot leave this
#: page describing an allowance that no longer exists. A limit with no entry here is not
#: shown at all — an unexplained number is worse than a missing one.
_ALLOWANCE_WORDS: tuple[tuple[str, str, str, str], ...] = (
    ("active_strategies", "Watchlists running at once", "radar", "watchlists"),
    ("symbols_per_strategy", "Coins in one Watchlist", "coins", ""),
    ("on_demand_scans_per_month", "Market checks a month", "scan", ""),
    ("user_initiated_scans_per_week", "Market checks a week", "scan", ""),
    ("detailed_history_days", "Days of history kept", "history", ""),
)

#: Two caps on the same thing, and only the one that really binds is shown.
#:
#: The free plan allows two messages a day *and* two a week. Printing "2 a day" beside
#: the weekly cap of 2 tells somebody they can have fourteen, which is the expensive
#: direction to be wrong in. Whichever cap bites first is the honest one to show.
_MESSAGE_CAPS: tuple[tuple[str, str, int], ...] = (
    ("alerts_per_day", "Messages a day", 7),
    ("alerts_per_week", "Messages a week", 1),
)

#: How a payment attempt actually ended, in words, with the tone that agrees.
#:
#: The older page printed the stored value with its underscores turned into spaces and
#: title case applied — "Provider Unavailable" — which is a machine's word for a thing
#: that happened to a person's money.
_PAYMENT_WORDS: dict[str, tuple[str, str, str]] = {
    "completed": ("Paid", "success", "This payment went through."),
    "pending": ("Not finished", "warning", "You started this and did not finish paying."),
    "creating": ("Being set up", "neutral", "We were still opening the payment page."),
    "processing": ("Being checked", "neutral", "The payment company is still checking it."),
    "failed": ("Did not go through", "danger", "No money was taken."),
    "expired": ("Ran out of time", "neutral", "The payment page closed before it was used."),
    "cancelled": ("You stopped it", "neutral", "Nothing was charged."),
    "provider_unavailable": (
        "We could not reach the payment company",
        "danger",
        "Nothing was charged. Trying again usually works.",
    ),
    "refunded": ("Refunded", "neutral", "The money went back to you."),
}

#: What we say about a payment state we do not recognise.
#:
#: Never the nearest one. Telling somebody "Paid" about a state we could not name is
#: telling them something about their money that we do not know to be true.
_PAYMENT_UNKNOWN = (
    "We cannot read this one",
    "neutral",
    "Ask us about it and we will look it up for you.",
)

#: How the payment companies are named to a person.
_PAYMENT_METHOD_WORDS = {
    "creem": "Card",
    "nowpayments": "Crypto",
    "free": "No payment needed",
    "admin": "Given by Hilal Markets",
    "trial": "Trial access",
}


def _tightest_message_cap(entitlement: Any) -> tuple[str, str, str, str]:
    """Of the caps on how many messages arrive, the one that really bites.

    Compared over the same length of time — a daily cap is multiplied out to a week —
    so "two a day" and "two a week" can be told apart. Returns an empty key when the
    plan sets neither.
    """

    tightest: tuple[str, str, str, str] = ("", "", "", "")
    smallest: int | None = None
    for key, label, per_week in _MESSAGE_CAPS:
        raw = entitlement.limit(key)
        if raw is None:
            continue
        try:
            weekly = int(raw) * per_week
        except (TypeError, ValueError):
            continue
        if smallest is None or weekly < smallest:
            smallest = weekly
            tightest = (key, label, "bell", "")
    return tightest


def _allowance_rows(
    entitlement: Any, *, watchlists_running: int
) -> list[dict[str, Any]]:
    """What this plan lets somebody do, and how much of it they have used.

    Only limits we can explain in ordinary words, and only a "used" figure where we can
    really count it. A bar drawn against a number nobody measured is a picture of
    nothing, which is what the older page's plan panel amounted to.
    """

    counted = {"watchlists": watchlists_running}
    rows: list[dict[str, Any]] = []
    for key, label, icon, counter in (*_ALLOWANCE_WORDS, _tightest_message_cap(entitlement)):
        if not key:
            continue
        raw = entitlement.limit(key)
        if raw is None:
            continue
        try:
            allowed = int(raw)
        except (TypeError, ValueError):
            continue
        # A limit of zero is "this is not part of your plan", and a tile showing a large
        # "0" reads as something running out. What the plan does and does not include is
        # already said in words on its own card, so nothing is hidden by leaving it out.
        if allowed <= 0:
            continue
        unlimited = allowed >= UNLIMITED_SYMBOL_CAP
        used = counted.get(counter) if counter else None
        rows.append(
            {
                "label": label,
                "icon": icon,
                "allowed": "No limit" if unlimited else str(allowed),
                "used": used,
                # A bar only where both ends are real. `used is None` means nobody
                # counted it, and that is said in words rather than drawn as zero.
                "percent": (
                    min(100, int(round(used / allowed * 100)))
                    if used is not None and not unlimited and allowed > 0
                    else None
                ),
                "at_limit": bool(
                    used is not None and not unlimited and allowed > 0 and used >= allowed
                ),
            }
        )
    return rows


def _payment_row(row: dict[str, Any], timezone_name: str) -> dict[str, Any]:
    """One payment, in the words a person reads."""

    attempt = row["attempt"]
    status = str(attempt.status or "").lower()
    label, tone, meaning = _PAYMENT_WORDS.get(status, _PAYMENT_UNKNOWN)
    provider = str(attempt.provider or "")
    return {
        "plan_name": row["plan_name"],
        "label": label,
        "tone": tone,
        "meaning": meaning,
        "when": _short_datetime(attempt.created_at, timezone_name),
        "when_ago": how_long_ago(attempt.created_at),
        "amount": f"{attempt.amount} {attempt.currency}",
        "method": _PAYMENT_METHOD_WORDS.get(provider, provider.replace("_", " ") or "Unknown"),
        "can_resume": bool(row["can_resume"]),
        "resume_url": row["resume_url"],
    }


def _plan_card(
    code: str,
    *,
    entitlement: Any,
    active_paid_plan_codes: Collection[str],
    availability: dict[str, Any],
) -> dict[str, Any]:
    """One plan, as a card. Every number comes from `core/plans.py`.

    Nothing is typed in here: the price, the crossed-out price, whether it can be
    bought, and the words used when it cannot are all read from the one offer
    definition the landing page and the public pricing page read.
    """

    plan = PLAN_DEFINITIONS[code]
    presentation = PUBLIC_PLAN_PRESENTATIONS[code]
    offer = plan_offer_payload(code)
    current = code == entitlement.plan.code or code in active_paid_plan_codes
    buyable = bool(
        offer["monthlyAvailable"]
        and availability.get("purchasable")
        and (availability.get("card_monthly") or availability.get("crypto_monthly"))
    )
    return {
        "code": code,
        "name": plan.name,
        "who_it_is_for": presentation.description,
        "monthly_price": offer["monthlyPrice"],
        "was_price": offer["originalMonthlyPrice"],
        "is_free": offer["monthlyPrice"] == 0,
        "for_sale": bool(offer["monthlyAvailable"]),
        "coming_soon_label": offer["comingSoonLabel"],
        "current": current,
        "buyable": buyable,
        # Why a person cannot press the button, when they cannot. "Disabled" on its own
        # is a state, not an answer: it tells nobody whether to wait, to change
        # something, or to give up.
        #
        # The free plan is the case worth naming separately. It is not "switched off"
        # and it is not "coming soon" — there is simply nothing to buy, and saying
        # anything else about it would be wrong in a way a beginner cannot check.
        "blocked_reason": (
            None
            if buyable or current
            else (
                "This is the free plan. Every account already includes it."
                if offer["monthlyPrice"] == 0
                else f"{plan.name} is not open for new subscriptions yet."
                if not offer["monthlyAvailable"]
                else "Paid subscriptions are switched off just now. Nothing was charged."
            )
        ),
        "highlight": presentation.highlighted_feature,
        "features": list(presentation.visible_features),
        "more_features": list(presentation.additional_features),
        "money_back": presentation.trial_note,
    }


@router.get(SUBSCRIPTION_PATH, response_class=HTMLResponse, include_in_schema=False)
async def subscription_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """The plan somebody is on, and what changing it would do — redesigned.

    Three questions, in the order a person asks them: **what do I have, am I running
    out of it, and what would I get instead.** The older page opened with a price grid,
    which answers the third question to somebody who has not been told the first.

    Every price, saving and availability flag comes from `core/plans.py` — the same
    definition the landing page reads — so this page cannot quote a price the public
    site does not.
    """

    await PlanCatalogService(session).sync_defaults()
    entitlement = await EntitlementService(session).current(user.id)
    trial = await session.scalar(select(Trial).where(Trial.user_id == user.id))
    active_paid_plan_codes = await _active_paid_plan_codes(session, user_id=user.id)
    watchlists_running = int(
        await session.scalar(
            select(func.count(Strategy.id)).where(
                Strategy.user_id == user.id,
                Strategy.status == StrategyStatus.ACTIVE,
                Strategy.archived_at.is_(None),
            )
        )
        or 0
    )

    card_provider = _billing_method_provider(settings, "card")
    crypto_provider = _billing_method_provider(settings, "crypto")
    availability = {
        code: {
            "purchasable": _plan_checkout_allowed(
                plan_code=code,
                active_paid_plan_codes=active_paid_plan_codes,
            ),
            "card_monthly": _billing_selection_available(
                settings, provider=card_provider, plan_code=code, billing_cycle="monthly"
            ),
            "crypto_monthly": _billing_selection_available(
                settings, provider=crypto_provider, plan_code=code, billing_cycle="monthly"
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
    payments = [
        _payment_row(row, user.timezone or "UTC")
        for row in _billing_history_rows(attempts, history_plans, now=datetime.now(UTC))
    ]

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
    name_parts = (user.display_name or "").strip().split(maxsplit=1)
    billing = BillingService(session, settings)
    await session.commit()

    # How this access ends, in one sentence. Three different facts used to be spread
    # across three tiles reading "Access source: Trial", "Trial access until", and
    # "Renewal: No automatic renewal" — which is the same sentence said three times in
    # a vocabulary nobody uses out loud.
    if entitlement.plan.code == "demo":
        renewal = "Free, with no end date and nothing to cancel."
    elif entitlement.source == "trial" and trial is not None:
        renewal = (
            "Your trial access ends on "
            f"{_short_datetime(trial.ends_at, user.timezone or 'UTC')}. "
            "Nothing renews by itself."
        )
    elif billing.provider_capabilities.supports_recurring_billing:
        renewal = "This renews by itself each month until you stop it."
    else:
        renewal = "This lasts 30 days. It does not renew by itself."

    cards = [
        _plan_card(
            code,
            entitlement=entitlement,
            active_paid_plan_codes=active_paid_plan_codes,
            availability=availability.get(code, {}),
        )
        for code in visible_public_plan_codes(billing_enabled=settings.billing_enabled)
    ]
    # Arriving with a plan already chosen. Only for a plan whose own card really carries
    # a button, so the popup can never open over a page that offers no way to press it.
    #
    # `purchasable` is not that question: it only says this account does not already
    # hold the plan, and it stays true while paid checkout is switched off entirely.
    wanted = str(request.query_params.get("plan") or "")
    open_for_plan = wanted if any(
        card["code"] == wanted and card["buyable"] for card in cards
    ) else ""

    context = await _context(
        request=request,
        session=session,
        settings=settings,
        user=user,
        page="billing",
        title="Your plan",
        entitlement=entitlement,
        renewal_sentence=renewal,
        allowances=_allowance_rows(entitlement, watchlists_running=watchlists_running),
        plan_cards=cards,
        comparison_rows=visible_plan_comparison(billing_enabled=settings.billing_enabled),
        comparison_headers=visible_plan_comparison_headers(
            billing_enabled=settings.billing_enabled
        ),
        payments=payments,
        unfinished_payment=next((row for row in payments if row["can_resume"]), None),
        billing_enabled=settings.billing_enabled,
        checkout_request_id=uuid4().hex,
        checkout_profile={
            "first_name": name_parts[0] if name_parts else "",
            "last_name": name_parts[1] if len(name_parts) > 1 else "",
            "email": primary_email or "",
        },
        pay_methods={"card": card_provider, "crypto": crypto_provider},
        open_for_plan=open_for_plan,
        settings_path=SETTINGS_PATH,
        support_path=SUPPORT_PATH,
        watchlists_path=WATCHLISTS_PATH,
    )
    context.update(_PATH_CHROME)
    # No coin on this page, so no Passport popup belongs on it.
    context["passport_quick_view_variant"] = "none"
    return templates.TemplateResponse(
        request, "hilal/dashboard_test/subscription.html", context
    )


# ── Settings ─────────────────────────────────────────────────────────────────

#: The four sounds a confirmed match can make, in words rather than file names.
_CONFIRMED_SOUND_WORDS: tuple[tuple[str, str], ...] = (
    ("chime", "A short chime"),
    ("bell", "A soft bell"),
    ("soft", "A very quiet tone"),
    ("none", "No sound"),
)

#: The four sounds a forming setup can make.
_FORMING_SOUND_WORDS: tuple[tuple[str, str], ...] = (
    ("pulse", "A gentle pulse"),
    ("chime", "A short chime"),
    ("soft", "A very quiet tone"),
    ("none", "No sound"),
)

#: Which exchange is which, in the words a person reads, with its own picture.
_PROVIDER_WORDS: dict[str, tuple[str, str]] = {
    "binance": ("Binance", "/static/brand-binance.svg"),
    "bybit": ("Bybit", "/static/brand-bybit.svg"),
}


def _hour_rows() -> list[dict[str, Any]]:
    """The 24 hours, grouped the way a person thinks about a day.

    A flat grid of 24 identical boxes is a puzzle. Night, morning, afternoon and
    evening are four choices somebody can make in one press each, with the individual
    hours still there underneath for anyone who wants them.
    """

    groups = (
        ("Night", "00:00", 0, 6, "moon"),
        ("Morning", "06:00", 6, 12, "spark"),
        ("Afternoon", "12:00", 12, 18, "chart"),
        ("Evening", "18:00", 18, 24, "clock"),
    )
    return [
        {
            "name": name,
            "icon": icon,
            "from_label": start_label,
            "to_label": f"{end:02d}:00" if end < 24 else "24:00",
            "hours": [f"{hour:02d}:00" for hour in range(start, end)],
        }
        for name, start_label, start, end, icon in groups
    ]


@router.get(
    SETTINGS_PATH,
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def settings_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """When and how a person is told — redesigned, and every control really saves.

    The older page was one long form with a Save button at the top, and it decided
    things inside its own template: which channels existed, which defaults applied,
    what a sound was called. Two of its settings had no reader at the other end and two
    settings the product *does* read had no control at all.

    Everything offered here is written by `AccountSettingsService`, the one owner, and
    read back by `NotificationPreferenceService`, the one reader.
    """

    preference = await session.scalar(
        select(DashboardPreference).where(DashboardPreference.user_id == user.id)
    )
    stored = dict((preference.notification_preferences or {}) if preference else {})
    account = AccountSettingsService(session, settings)
    choosable = await account.choosable_channels(user.id)
    entitlement = await EntitlementService(session).current(user.id)

    # Asked of the one reader, never worked out here. The older page decided its own
    # defaults in its template and decided differently from the service, so a fresh
    # account saw WhatsApp already ticked while the product had not switched it on.
    chosen = await NotificationPreferenceService(session, settings).current(user.id)
    chosen_channels = {channel.value for channel in chosen.channels}
    evidence_channels = {
        channel.value for channel in (chosen.compliance_alert_channels or set())
    }
    whatsapp_in_plan = bool(entitlement.feature_enabled("whatsapp"))

    def channel_row(value: str, icon: str, what_it_is: str, chosen: set[str]) -> dict[str, Any]:
        available = value in choosable
        return {
            "value": value,
            "label": _CHANNEL_WORDS.get(value, (value.capitalize(), ""))[0],
            "what_it_is": what_it_is,
            "icon": icon,
            "available": available,
            "chosen": bool(available and value in chosen),
            # Why not, when not. A greyed-out box with no reason is a dead end.
            "why_not": (
                None
                if available
                else (
                    "WhatsApp is not part of your plan yet."
                    if value == "whatsapp" and not whatsapp_in_plan
                    else f"{value.capitalize()} is not switched on for this platform yet."
                )
            ),
        }

    by_email = "A message to the address you signed up with."
    by_telegram = "A private message from our bot. The fastest of the three."
    by_whatsapp = "A message on WhatsApp."
    alert_channels = [
        channel_row("email", "mail", by_email, chosen_channels),
        channel_row("telegram", "telegram", by_telegram, chosen_channels),
        channel_row("whatsapp", "whatsapp", by_whatsapp, chosen_channels),
    ]
    evidence_extra = [
        channel_row("email", "mail", by_email, evidence_channels),
        channel_row("telegram", "telegram", by_telegram, evidence_channels),
        channel_row("whatsapp", "whatsapp", by_whatsapp, evidence_channels),
    ]

    chosen_providers = set(stored.get("providers", list(MARKET_PROVIDERS)))
    providers = [
        {
            "value": value,
            "label": _PROVIDER_WORDS[value][0],
            "logo": _PROVIDER_WORDS[value][1],
            "chosen": value in chosen_providers,
        }
        for value in MARKET_PROVIDERS
    ]

    chosen_days = set(stored.get("alert_days", ["Every Day"]))
    chosen_hours = set(stored.get("alert_hours", []))

    context = await _context(
        request=request,
        session=session,
        settings=settings,
        user=user,
        page="settings",
        title="Settings",
        saved={
            "timezone": user.timezone or "UTC",
            "near_miss_enabled": bool(stored.get("near_miss_enabled", True)),
            "near_miss_threshold": int(stored.get("near_miss_threshold", 70)),
            "maximum_alerts_per_hour": int(stored.get("maximum_alerts_per_hour", 50)),
            "maximum_alerts_per_day": int(stored.get("maximum_alerts_per_day", 500)),
            "finished_opportunity_alerts": bool(stored.get("lifecycle_enabled", True)),
            "muted_symbols": clean_muted_symbols(list(stored.get("muted_symbols", []))),
            "compliance_alert_digest": str(stored.get("compliance_alert_digest", "immediate")),
            "qualification_change_alerts": bool(
                stored.get("qualification_change_alerts", True)
            ),
            "dashboard_notifications_enabled": bool(
                stored.get("dashboard_notifications_enabled", True)
            ),
            "dashboard_notification_sound": str(
                stored.get("dashboard_notification_sound", "chime")
            ),
            "forming_dashboard_notifications": bool(
                stored.get("forming_dashboard_notifications", False)
            ),
            "forming_notification_sound": str(stored.get("forming_notification_sound", "pulse")),
        },
        alert_channels=alert_channels,
        evidence_channels=evidence_extra,
        providers=providers,
        timezones=_timezone_options(),
        day_options=[
            {"value": day, "chosen": day in chosen_days, "short": day[:3]}
            for day in ALERT_DAYS
            if day != "Every Day"
        ],
        every_day=("Every Day" in chosen_days) or not chosen_days,
        hour_groups=_hour_rows(),
        chosen_hours=sorted(chosen_hours),
        confirmed_sounds=_CONFIRMED_SOUND_WORDS,
        forming_sounds=_FORMING_SOUND_WORDS,
        muted_symbol_limit=MUTED_SYMBOL_LIMIT,
        connections_path=CONNECTIONS_PATH,
        subscription_path=SUBSCRIPTION_PATH,
        support_path=SUPPORT_PATH,
    )
    context.update(_PATH_CHROME)
    context["passport_quick_view_variant"] = "none"
    return templates.TemplateResponse(request, "hilal/dashboard_test/settings.html", context)


# ── Support ──────────────────────────────────────────────────────────────────

#: What somebody can be writing in about, in their words, with the stored category it
#: becomes. The stored value is what `SupportEscalationService` prioritises on, so the
#: two must be chosen together rather than the page inventing a label of its own.
SUPPORT_TOPICS: tuple[tuple[str, str, str, str], ...] = (
    (
        "missing_alert",
        "I was not told about something",
        "bell",
        "A setup matched and no message reached you.",
    ),
    (
        "billing",
        "Something about paying",
        "billing",
        "A charge, a refund, a receipt, or changing your plan.",
    ),
    (
        "bug_report",
        "Something is broken",
        "alert",
        "A page, a button or a number that is not behaving.",
    ),
    (
        "screening",
        "A coin's Shariah status",
        "compliance",
        "A question about why a coin is eligible, under review or excluded.",
    ),
    (
        "general",
        "Something else",
        "chat",
        "Anything that does not fit the others.",
    ),
)

#: Where a request has got to, in words, and the tone that agrees with it.
_TICKET_WORDS: dict[str, tuple[str, str, str]] = {
    "open": ("Waiting for us", "warning", "We have it. Nobody has replied yet."),
    "pending_user": ("Waiting for you", "warning", "We asked you something and are waiting."),
    "in_progress": ("We are on it", "neutral", "Somebody is looking at this now."),
    "resolved": ("Answered", "success", "We answered this one."),
    "closed": ("Closed", "neutral", "This one is finished."),
}

#: What we say about a state we do not recognise. Never the nearest one.
_TICKET_UNKNOWN = ("We cannot read this one", "neutral", "Ask us and we will look it up.")


@router.get(
    SUPPORT_PATH,
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def support_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Getting help — redesigned, and it tries to answer before it asks.

    The older page was one long form and a list of subjects. It asked for a subject line
    before it knew what the problem was, never said what would happen next, and offered
    no way at all to read back what you had written.

    The order here is the order somebody actually needs: *can I fix this myself*, then
    *what is it about*, then *tell us*, then *what you already asked us*.
    """

    tickets = list(
        (
            await session.scalars(
                select(SupportRequest)
                .where(SupportRequest.user_id == user.id)
                .order_by(SupportRequest.created_at.desc())
                .limit(20)
            )
        ).all()
    )
    email_identity = await session.scalar(
        select(UserIdentity)
        .where(
            UserIdentity.user_id == user.id,
            UserIdentity.provider == IdentityProvider.EMAIL,
        )
        .order_by(UserIdentity.is_primary.desc(), UserIdentity.created_at.asc())
        .limit(1)
    )

    views = []
    for ticket in tickets:
        label, tone, meaning = _TICKET_WORDS.get(ticket.status.value, _TICKET_UNKNOWN)
        views.append(
            {
                "id": str(ticket.id),
                "subject": ticket.subject,
                "topic": next(
                    (words for code, words, _icon, _hint in SUPPORT_TOPICS
                     if code == ticket.category),
                    "Something else",
                ),
                "label": label,
                "tone": tone,
                "meaning": meaning,
                "sent": how_long_ago(ticket.created_at),
                "sent_exact": _short_datetime(ticket.created_at, user.timezone or "UTC"),
                # A machine-readable instant for `<time datetime>`. The exact wording
                # above is for a person to read and is not a valid value for it.
                "sent_iso": ticket.created_at.isoformat(),
                # Their own words, read back. Cut here rather than in the template so
                # the length is one decision and can be tested.
                "what_you_said": (ticket.description or "").strip()[:600],
                "attachment_count": len((ticket.context or {}).get("attachments") or []),
            }
        )

    context = await _context(
        request=request,
        session=session,
        settings=settings,
        user=user,
        page="support",
        title="Get help",
        topics=SUPPORT_TOPICS,
        tickets=views,
        support_email=(
            email_identity.display_identifier or email_identity.normalized_identifier
            if email_identity
            else ""
        ),
        # Where somebody can very likely answer their own question, without waiting.
        self_help=[
            {
                "icon": "bell",
                "title": "I am not getting messages",
                "detail": "Check where we send them and switch a channel on.",
                "action": "Open Connections",
                "url": CONNECTIONS_PATH,
            },
            {
                "icon": "clock",
                "title": "I get too many, or at the wrong time",
                "detail": "Set the days, the hours and how many a day.",
                "action": "Open Settings",
                "url": SETTINGS_PATH,
            },
            {
                "icon": "compliance",
                "title": "Why is this coin eligible or excluded?",
                "detail": "Every coin has an evidence record with its sources and dates.",
                "action": "Open Halal Assets",
                "url": MARKET_BASE_PATH,
            },
            {
                "icon": "billing",
                "title": "A question about paying",
                "detail": "Your plan, your payments and your receipts are all here.",
                "action": "Open your plan",
                "url": SUBSCRIPTION_PATH,
            },
        ],
        settings_path=SETTINGS_PATH,
        subscription_path=SUBSCRIPTION_PATH,
        # Said before somebody writes, not after they press Send. Read from the one
        # module that enforces it, so the page can never advertise a different number
        # from the one being applied.
        support_limits=support_intake_limits(settings),
    )
    context.update(_PATH_CHROME)
    context["passport_quick_view_variant"] = "none"
    return templates.TemplateResponse(request, "hilal/dashboard_test/support.html", context)
