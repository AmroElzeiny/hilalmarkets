"""Home — the `/home` dashboard page.

A redesign of what `/dashboard` tries to say, on exactly the same data. Every number
here comes from a read model that another page already owns — the readiness check, the
recorded history, the Watchlist health rows, the screening service — so Home can
never disagree with the page it links to. Nothing on this page is computed twice.

The page answers four questions, in the order somebody actually asks them:

  1. Is anything happening right now?      -> the band at the top
  2. Where do things stand?                -> four tiles, each one question
  3. What is closest, and what is missing? -> one card, with its evidence
  4. Will I be told, and by what?          -> the delivery strip

Every judgement is made here, in Python, in the words a person reads. A template that
decides things cannot be tested, and the next page needing the same decision will make
a different one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.routers.dashboard import (
    _context,
    _monitor_cards_context,
    _require_user,
    templates,
)
from ai_market_monitor.api.routers.dashboard_test import (
    MARKET_BASE_PATH,
    MONITOR_PATH,
    MONITORS_PATH,
    OPPORTUNITIES_PATH,
    _assets_by_ticker,
    _merge_opportunities,
    _opportunity_from_journey,
    _opportunity_from_readiness,
    _watchlist_view,
)
from ai_market_monitor.core.asset_logos import asset_logo
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.dashboard_paths import HOME_PATH, LEGACY_HOME_PATH
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.db.models import (
    ComplianceDriftNotification,
    TelegramConnection,
    User,
    WhatsAppConnection,
)
from ai_market_monitor.db.models.enums import (
    ConnectionStatus,
    ShariaAssetStatus,
)
from ai_market_monitor.services.lifecycle_dashboard import lifecycle_cards
from ai_market_monitor.services.notification_preferences import offered_channels
from ai_market_monitor.services.product_language import (
    MarketCheckingNotice,
    how_long_ago,
    market_checking_notice,
)
from ai_market_monitor.services.setup_observability import SetupObservabilityService
from ai_market_monitor.services.sharia_screening import (
    DEFAULT_ALLOWED_STATUSES,
    ShariaScreeningService,
)

router = APIRouter(tags=["main-dashboard"])

#: Kept as a name because it reads well at the call sites below, but it is the shared
#: address rather than a second copy of the string. See `core/dashboard_paths.py`.
MAIN_PATH = HOME_PATH

#: Where a person goes to make a monitor. The canvas — the only place one is authored.
#:
#: It was `/dashboard/strategies/new`, the older assistant page, written as its own
#: string in four files. That page is gone, and this is the shared address rather than a
#: fifth copy of a string. See `core/dashboard_paths.py`.
NEW_WATCHLIST_PATH = MONITOR_PATH
COMPLIANCE_PATH = "/dashboard/compliance"
CONNECTIONS_PATH = "/dashboard/connections"

#: How many coins the screened strip shows before it stops and links to the full list.
#:
#: Twelve fits two rows at every width the dashboard supports. A strip that grew with
#: the market would push everything below it off the first screen, which is the one
#: thing a summary page must not do.
_COIN_STRIP_LIMIT = 12

#: How many Watchlists the summary shows before it links to the full page.
_LIST_LIMIT = 4

#: An opportunity in one of these two states is worth putting on the front page.
#:
#: Taken from the buckets the Opportunities page already uses, so "close" cannot come
#: to mean one thing here and another thing one click away.
_LIVE_KINDS = ("ready", "close")

#: The order the front page cares about. "Ready" outranks "close", and inside a kind the
#: one with more of a person's own list already true comes first.
_KIND_RANK = {"ready": 0, "close": 1, "forming": 2, "unchecked": 3, "ended": 4}


def _rank(view: dict[str, Any]) -> tuple[int, int]:
    checks = view.get("checks") or {}
    return (
        _KIND_RANK.get(view["state"]["kind"], 9),
        -int(checks.get("percent") or 0),
    )


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _tile(
    *,
    key: str,
    icon: str,
    question: str,
    value: int,
    unit: str,
    meaning: str,
    empty: str,
    href: str,
    tone: str,
    explain: tuple[str, ...],
) -> dict[str, Any]:
    """One tile: a question, its number, and what the number means.

    ``empty`` is not decoration. A bare "0" on a dashboard is the least useful thing a
    screen can show — the live page prints four of them and explains none. Where the
    count is nothing, the tile says what nothing means instead of leaving a person to
    guess whether the platform is broken or the market is quiet.
    """

    return {
        "key": key,
        "icon": icon,
        "question": question,
        "value": value,
        "unit": unit,
        "meaning": meaning if value else empty,
        "is_empty": value == 0,
        "href": href,
        "tone": tone if value else "neutral",
        "explain": list(explain),
    }


def _tiles(
    *,
    live_count: int,
    ready_count: int,
    eligible_count: int,
    screening_standard: str | None,
    active_lists: int,
    total_lists: int,
    changed_count: int,
) -> list[dict[str, Any]]:
    """The four questions this page answers with a number.

    Each tile owns exactly one question and exactly one place to go next. The live page
    put four unrelated counts in a row with no destination on any of them, so a person
    who read "3" had nowhere to go and nothing to do about it.
    """

    return [
        _tile(
            key="close",
            icon="spark",
            question="Close to what you asked for",
            value=live_count,
            unit=_plural(live_count, "coin", "coins"),
            meaning=(
                f"{ready_count} {_plural(ready_count, 'has', 'have')} everything you asked for."
                if ready_count
                else "Not everything yet, but not far off."
            ),
            empty="Nothing is close right now. Your monitors keep looking.",
            href=OPPORTUNITIES_PATH,
            tone="success" if ready_count else "accent",
            explain=(
                "A coin is counted here when your own monitor is mostly or fully true for it.",
                "Hilal Markets never buys or sells. This is only a count of what your rules found.",
            ),
        ),
        _tile(
            key="eligible",
            icon="shield_check",
            question="Coins you can watch",
            value=eligible_count,
            unit=_plural(eligible_count, "coin", "coins"),
            meaning=(
                f"Screened under {screening_standard}."
                if screening_standard
                else "Screened and published."
            ),
            empty=(
                "No coin has a published review yet. "
                "One appears here after its review is finished."
            ),
            href=MARKET_BASE_PATH,
            tone="accent",
            explain=(
                "A coin is listed only after a qualified reviewer finished its evidence review.",
                "The result is one standard, on one date. It is not a universal religious ruling.",
            ),
        ),
        _tile(
            key="lists",
            icon="radar",
            question="Monitors looking for you",
            value=active_lists,
            unit=_plural(active_lists, "monitor", "monitors"),
            meaning=(
                f"Out of {total_lists} you have made."
                if total_lists > active_lists
                else "All of yours are running."
            ),
            empty=(
                "You have monitors, but none is running."
                if total_lists
                else "You have not made a monitor yet."
            ),
            href=MONITORS_PATH,
            tone="success",
            explain=(
                "A monitor is your own set of rules about a coin. It checks the market for you.",
                "You approve every rule before anything is watched.",
            ),
        ),
        _tile(
            key="changed",
            icon="history",
            question="Screening changes",
            value=changed_count,
            unit=_plural(changed_count, "coin", "coins"),
            meaning="Changed in the last 30 days. Worth a look.",
            empty="No screening result changed in the last 30 days.",
            href=COMPLIANCE_PATH,
            tone="warning",
            explain=(
                "A coin can move to under review or excluded when its evidence changes.",
                "You are told when it happens to a coin one of your monitors is watching.",
            ),
        ),
    ]


def _headline(
    *,
    ready_count: int,
    close_count: int,
    active_lists: int,
    total_lists: int,
    checking: MarketCheckingNotice | None,
) -> dict[str, Any]:
    """What is happening right now, in one sentence a beginner can read.

    Six states, and each one is a true thing to say. The order matters: a person with
    something ready is told that first, and somebody with no list at all is never shown
    a market summary they cannot act on.

    The "we are not checking" state comes before all of them, because it changes what
    every other sentence on this page means. This band told people "Your monitor is
    watching." while the platform was not looking at the market at all, and the card
    below it said "Not looked yet" — one page, two answers, and the true one nowhere.
    """

    if checking is not None:
        return {
            "tone": checking.tone,
            "icon": "pause",
            "headline": checking.title,
            "detail": checking.detail,
            "action": {
                "label": "See your monitors" if total_lists else "Make your first monitor",
                "href": MONITORS_PATH if total_lists else NEW_WATCHLIST_PATH,
                "icon": "arrow" if total_lists else "plus",
            },
        }
    if ready_count:
        return {
            "tone": "success",
            "icon": "check",
            "headline": (
                "One coin has everything you asked for."
                if ready_count == 1
                else f"{ready_count} coins have everything you asked for."
            ),
            "detail": "Open it to see every reading behind that.",
            "action": {"label": "Open what is ready", "href": OPPORTUNITIES_PATH, "icon": "arrow"},
        }
    if close_count:
        return {
            "tone": "accent",
            "icon": "spark",
            "headline": (
                "One coin is close." if close_count == 1 else f"{close_count} coins are close."
            ),
            "detail": "Nothing is ready yet. Here is what each one is still waiting on.",
            "action": {"label": "See what is closest", "href": OPPORTUNITIES_PATH, "icon": "arrow"},
        }
    if active_lists:
        return {
            "tone": "neutral",
            "icon": "radar",
            "headline": (
                "Your monitor is watching."
                if active_lists == 1
                else f"Your {active_lists} monitors are watching."
            ),
            "detail": "Nothing has come close yet. You will be told when something does.",
            "action": {"label": "See your monitors", "href": MONITORS_PATH, "icon": "arrow"},
        }
    if total_lists:
        return {
            "tone": "warning",
            "icon": "pause",
            "headline": "Nothing is running.",
            "detail": "You have made monitors, but none of them is looking at the market.",
            # Straight to the canvas, which is where a monitor is drawn. It used to send
            # somebody back to the same list of stopped monitors they were already
            # looking at, which answers nothing.
            "action": {
                "label": "Create a monitor",
                "href": MONITOR_PATH,
                "icon": "circle_plus",
            },
        }
    return {
        "tone": "neutral",
        "icon": "workflow",
        "headline": "Let us set up your first monitor.",
        "detail": (
            "Tell Hilal Markets what you already watch for. "
            "You approve every rule before it runs."
        ),
        "action": {
            "label": "Make your first monitor",
            "href": NEW_WATCHLIST_PATH,
            "icon": "plus",
        },
    }


async def _delivery(
    session: AsyncSession, settings: Settings, user: User
) -> list[dict[str, Any]]:
    """Every way a person can really be told, and whether it is ready for them.

    Which channels exist is not decided here. ``offered_channels`` is the one owner of
    that, so a retired channel can never appear on this page however long its value
    lingers in the schema for old rows.
    """

    offered = {channel.value for channel in offered_channels(settings)}
    telegram = await session.scalar(
        select(TelegramConnection).where(TelegramConnection.user_id == user.id)
    )
    whatsapp = await session.scalar(
        select(WhatsAppConnection).where(WhatsAppConnection.user_id == user.id)
    )
    rows: list[dict[str, Any]] = [
        {
            "key": "web",
            # The dashboard's own mark, not a bell. A bell is what a notification is; this
            # row is about *where* it arrives, and the other three rows all show the mark
            # of the place they mean.
            "icon": "dashboard",
            "label": "In the dashboard",
            "detail": "A notice waiting for you here.",
            "ready": True,
            "state": "Always on",
        }
    ]
    if "telegram" in offered:
        ready = bool(
            telegram
            and telegram.status == ConnectionStatus.ACTIVE
            and telegram.alerts_enabled
        )
        rows.append(
            {
                "key": "telegram",
                "icon": "telegram",
                "label": "Telegram",
                "detail": "A message from the Hilal Markets bot."
                if ready
                else "Not connected yet.",
                "ready": ready,
                "state": "Ready" if ready else "Set this up",
            }
        )
    if "whatsapp" in offered:
        ready = bool(whatsapp and whatsapp.status == ConnectionStatus.ACTIVE)
        rows.append(
            {
                "key": "whatsapp",
                "icon": "whatsapp",
                "label": "WhatsApp",
                "detail": "A message on WhatsApp." if ready else "Not connected yet.",
                "ready": ready,
                "state": "Ready" if ready else "Set this up",
            }
        )
    if "email" in offered:
        rows.append(
            {
                "key": "email",
                "icon": "mail",
                "label": "Email",
                "detail": "A message to the address you signed up with.",
                "ready": True,
                "state": "Ready",
            }
        )
    return rows


@router.get(
    LEGACY_HOME_PATH,
    response_class=RedirectResponse,
    include_in_schema=False,
    name="legacy_main_dashboard_page",
)
async def legacy_main_dashboard_page() -> RedirectResponse:
    """The address Home used to have. Everything arriving here is taken to Home.

    A 308 rather than a 404, and rather than a second page. `/main` is written into
    email we have already sent, into Telegram buttons, into the `target_path` column of
    two tables and into people's bookmarks — none of which can be corrected now. It is
    also *not* signed-in-only here on purpose: Home's own guard sends a visitor who is
    not signed in to sign-in, and putting a second copy of that rule in front of a
    redirect is how the two come to disagree.
    """

    return RedirectResponse(HOME_PATH, status_code=308)


@router.get(MAIN_PATH, response_class=HTMLResponse, include_in_schema=False)
async def main_dashboard_page(
    request: Request,
    user: User = Depends(_require_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    observability = SetupObservabilityService(session, settings)
    radar = await observability.radar(user.id, monitor_id=None, page_size=100)
    journeys = await lifecycle_cards(session, user.id, monitor_id=None)
    assets = await _assets_by_ticker(session, radar["items"])
    opportunities = _merge_opportunities(
        [_opportunity_from_readiness(row, assets) for row in radar["items"]],
        [_opportunity_from_journey(card) for card in journeys],
    )
    opportunities.sort(key=_rank)

    live = [item for item in opportunities if item["state"]["kind"] in _LIVE_KINDS]
    ready_count = sum(1 for item in opportunities if item["state"]["kind"] == "ready")
    close_count = sum(1 for item in opportunities if item["state"]["kind"] == "close")
    closest = opportunities[0] if opportunities else None

    checking = market_checking_notice(scanning_enabled=settings.scanning_enabled)
    lists = [
        _watchlist_view(item, scanning_enabled=settings.scanning_enabled)
        for item in await _monitor_cards_context(session, user)
    ]
    active_lists = sum(1 for item in lists if item["status"] == "Watching")
    attention_lists = [
        item for item in lists if item["working"]["tone"] in {"warning", "danger"}
    ]

    screened = await ShariaScreeningService(session, settings).list_screened_assets(
        methodology_id=None,
        statuses=DEFAULT_ALLOWED_STATUSES,
        page=1,
        limit=_COIN_STRIP_LIMIT,
    )
    coins = [
        {
            "coin": row.canonical_asset,
            "name": row.asset_name or row.canonical_asset,
            "status_label": row.status_label,
            "tone": "success"
            if row.status == ShariaAssetStatus.ELIGIBLE
            else "warning",
            "standard_id": str(row.methodology_id),
            "logo_module_url": asset_logo(row.canonical_asset).module_url,
            # The picture stored on the assessment wins over the shared catalogue when
            # it exists, because a newly listed coin has no catalogue entry at all.
            "logo_url": row.logo_url or asset_logo(row.canonical_asset).image_url,
        }
        for row in screened.items
    ]

    changed_count = int(
        await session.scalar(
            select(func.count(ComplianceDriftNotification.id)).where(
                ComplianceDriftNotification.user_id == user.id,
                ComplianceDriftNotification.created_at
                >= datetime.now(UTC) - timedelta(days=30),
                ComplianceDriftNotification.new_status.in_(
                    [ShariaAssetStatus.UNDER_REVIEW, ShariaAssetStatus.EXCLUDED]
                ),
            )
        )
        or 0
    )

    # When the market was last really looked at. The most recent completed check across
    # every list the person owns — not "now", which is what a page says when it is
    # describing its own render rather than the market.
    looked_at = max(
        (item["last_checked_exact"] for item in lists if item["last_checked_exact"]),
        default=None,
    )

    now = _headline(
        ready_count=ready_count,
        close_count=close_count,
        active_lists=active_lists,
        total_lists=len(lists),
        checking=checking,
    )
    now["checked"] = how_long_ago(looked_at)
    now["checked_exact"] = looked_at

    context = await _context(
        request=request,
        session=session,
        settings=settings,
        user=user,
        page="main",
        title="Main",
        now=now,
        tiles=_tiles(
            live_count=len(live),
            ready_count=ready_count,
            eligible_count=screened.total,
            screening_standard=screened.methodology.name if screened.methodology else None,
            active_lists=active_lists,
            total_lists=len(lists),
            changed_count=changed_count,
        ),
        closest=closest,
        live_count=len(live),
        lists=lists[:_LIST_LIMIT],
        lists_total=len(lists),
        lists_attention=len(attention_lists),
        coins=coins,
        coins_total=screened.total,
        screening_standard=screened.methodology.name if screened.methodology else None,
        screening_warning=screened.warning,
        delivery=await _delivery(session, settings, user),
        opportunities_path=OPPORTUNITIES_PATH,
        watchlists_path=MONITORS_PATH,
        market_path=MARKET_BASE_PATH,
        monitor_path=MONITOR_PATH,
        connections_path=CONNECTIONS_PATH,
        compliance_path=COMPLIANCE_PATH,
        new_watchlist_path=NEW_WATCHLIST_PATH,
        # Coins are named on this page, so it carries the Passport popup. Opened rather
        # than linked to: a link straight to the full Passport is a visible button that
        # leads to a "not found" page whenever the coin has no published record.
        passport_quick_view_variant="test",
    )
    return templates.TemplateResponse(request, "hilal/main/home.html", context)
