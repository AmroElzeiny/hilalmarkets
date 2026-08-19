"""The one place public-site visits are counted, labelled and reported.

Four questions the Stats page answers, and where each number comes from:

===========================  ======================================================
Viewers                      distinct ``visitor_key`` — one person counted once a day
Average time on the page     mean of ``active_ms``, which stops while the tab is hidden
What they did next           ``next_action``, written when the same person opens a
                             second page, opens the chat, or reaches the sign-up door
Sign-ups                     rows in ``site_signup_attributions`` — real accounts
===========================  ======================================================

**One vocabulary for tags.** A tag is a label *and* the filter that selects it, defined
once in :data:`TAG_DEFINITIONS`. The chips on the page and the ``WHERE`` clause behind
them are the same objects, so a chip can never select something different from what it
says. This is the rule the compiler side of this product already follows: one owner per
concept, every caller importing it.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import SiteSignupAttribution, SiteVisit, User

#: The front page. Everything the request called "the landing page" means this path.
LANDING_PATH = "/"

#: Doors that mean the person went to make an account.
SIGNUP_PATHS = frozenset({"/signup", "/signin", "/dashboard-entry", "/dashboard"})

#: The longest a single visit may claim to have lasted. A page left open in a background
#: tab reports nothing (the counter stops when it is hidden), but a browser that keeps a
#: page in front of somebody for two hours is still not a two-hour read of one page, and
#: one such row would move the average on its own.
MAX_ACTIVE_MS = 30 * 60 * 1000

#: How long a page may go quiet before a later report treats the visit as finished.
STALE_VISIT_MINUTES = 30

_SEARCH_HOSTS = ("google.", "bing.", "duckduckgo.", "yahoo.", "yandex.", "ecosia.", "brave.")
_SOCIAL_HOSTS = (
    "x.com",
    "twitter.",
    "t.co",
    "facebook.",
    "instagram.",
    "linkedin.",
    "youtube.",
    "tiktok.",
    "reddit.",
    "telegram.",
    "whatsapp.",
)


@dataclass(frozen=True, slots=True)
class TagDefinition:
    """One chip on the Stats page: what it says, and exactly what it selects."""

    key: str
    label: str
    group: str
    predicate: Callable[[], ColumnElement[bool]]


def _tag_definitions() -> tuple[TagDefinition, ...]:
    return (
        TagDefinition("device:phone", "Phone", "Device", lambda: SiteVisit.device == "phone"),
        TagDefinition("device:tablet", "Tablet", "Device", lambda: SiteVisit.device == "tablet"),
        TagDefinition(
            "device:desktop", "Computer", "Device", lambda: SiteVisit.device == "desktop"
        ),
        TagDefinition("source:direct", "Typed the address", "Came from",
                      lambda: SiteVisit.source == "direct"),
        TagDefinition("source:search", "Search engine", "Came from",
                      lambda: SiteVisit.source == "search"),
        TagDefinition("source:social", "Social media", "Came from",
                      lambda: SiteVisit.source == "social"),
        TagDefinition("source:referral", "Another website", "Came from",
                      lambda: SiteVisit.source == "referral"),
        TagDefinition("source:campaign", "Campaign link", "Came from",
                      lambda: SiteVisit.source == "campaign"),
        TagDefinition("did:signup", "Went to sign up", "Did next",
                      lambda: SiteVisit.next_action == "signup"),
        TagDefinition("did:chat", "Opened the chat", "Did next",
                      lambda: SiteVisit.next_action == "chat"),
        TagDefinition("did:pricing", "Opened pricing", "Did next",
                      lambda: SiteVisit.next_action == "pricing"),
        TagDefinition("did:page", "Read another page", "Did next",
                      lambda: SiteVisit.next_action == "page"),
        TagDefinition("did:left", "Left the site", "Did next",
                      lambda: SiteVisit.next_action.is_(None)),
    )


TAG_DEFINITIONS: tuple[TagDefinition, ...] = _tag_definitions()
TAGS_BY_KEY: dict[str, TagDefinition] = {item.key: item for item in TAG_DEFINITIONS}


def visitor_key(
    settings: Settings,
    *,
    remote_address: str,
    user_agent: str,
    at: datetime | None = None,
) -> str:
    """One person, one day, one opaque value.

    Deliberately not reversible and deliberately not stable across days: it can count a
    returning visitor within a day, and it cannot be used to follow anybody. Nothing is
    stored in the visitor's browser, which is why this measures without a cookie banner.
    """

    day: date = (at or datetime.now(UTC)).astimezone(UTC).date()
    material = f"site-visit:{day.isoformat()}:{remote_address}:{user_agent[:200]}".encode()
    secret = settings.app_secret_key.get_secret_value().encode("utf-8")
    return hmac.new(secret, material, hashlib.sha256).hexdigest()


def classify_source(referrer: str | None, campaign: str | None) -> str:
    if campaign:
        return "campaign"
    host = referrer_host(referrer)
    if not host:
        return "direct"
    if any(marker in host for marker in _SEARCH_HOSTS):
        return "search"
    if any(marker in host for marker in _SOCIAL_HOSTS):
        return "social"
    return "referral"


def referrer_host(referrer: str | None) -> str | None:
    if not referrer:
        return None
    parsed = urlsplit(referrer)
    host = (parsed.netloc or "").split("@")[-1].split(":")[0].lower()
    return host[:200] or None


def classify_next_action(path: str) -> str:
    """What opening this page says about the page before it."""

    normalized = normalize_path(path)
    if normalized in SIGNUP_PATHS or normalized.startswith("/dashboard"):
        return "signup"
    if normalized.startswith("/pricing") or normalized.startswith("/subscribe"):
        return "pricing"
    return "page"


def recorded_within(
    column: Any,
    since: datetime,
    until: datetime,
    include_until: bool,
) -> ColumnElement[bool]:
    """Whether a timestamped row falls in a window. One rule, because it was written thrice.

    The tiles, the tag chips and the sign-up count each carried their own copy of this
    comparison, across two tables. Three copies of a boundary is how a chip ends up
    reporting a different number from the tile above it, and the reader blames the filter
    rather than the edge.

    ``include_until`` exists for the live window only. Windows are half-open so that
    "this month" and "the month before" cannot both claim a row that landed exactly on
    the join. That is right for a boundary between two windows, and wrong for the one at
    the present moment: a row is stamped with ``now()`` as it is written, the report takes
    its own ``now()`` moments later, and on a clock that advances in steps — about every
    15ms on Windows — those two readings can be the same value. The row then sits exactly
    on an excluded edge and the page reports nobody at all. The join between the two
    windows stays exclusive; only the live edge reaches the present.
    """

    return and_(column >= since, column <= until if include_until else column < until)


def normalize_path(value: str) -> str:
    """The path alone: no host, no query, no fragment, never longer than the column."""

    raw = (value or "/").strip()
    if "://" in raw:
        raw = urlsplit(raw).path or "/"
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    if not raw.startswith("/"):
        raw = f"/{raw}"
    if len(raw) > 1 and raw.endswith("/"):
        raw = raw.rstrip("/") or "/"
    return raw[:200]


def classify_device(user_agent: str) -> str:
    lowered = (user_agent or "").lower()
    if "ipad" in lowered or ("tablet" in lowered and "mobile" not in lowered):
        return "tablet"
    if "mobi" in lowered or "iphone" in lowered or "android" in lowered:
        return "phone"
    return "desktop"


class SiteAnalyticsService:
    """Writes what the public site measured, and reads it back as plain numbers."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def record(
        self,
        *,
        event: str,
        session_key: str,
        path: str,
        remote_address: str,
        user_agent: str,
        referrer: str | None = None,
        campaign: str | None = None,
        active_ms: int = 0,
        action: str | None = None,
        action_detail: str | None = None,
    ) -> SiteVisit | None:
        """Record one beacon from a public page.

        Every event is safe to repeat: a retried ``open`` finds the row it already wrote,
        and ``active_ms`` only ever moves forward. Browsers retry these beacons on a flaky
        connection, so an implementation that added instead of replacing would count the
        same seconds twice.
        """

        now = datetime.now(UTC)
        key = visitor_key(
            self.settings,
            remote_address=remote_address,
            user_agent=user_agent,
            at=now,
        )
        normalized_path = normalize_path(path)
        visit = await self.session.scalar(
            select(SiteVisit).where(SiteVisit.session_key == session_key)
        )
        if visit is None:
            if event != "open":
                # A ping or a close for a visit that was never opened describes nothing
                # measurable. Inventing a start time for it would invent a duration.
                return None
            visit = SiteVisit(
                visitor_key=key,
                session_key=session_key,
                path=normalized_path,
                is_landing=normalized_path == LANDING_PATH,
                referrer_host=referrer_host(referrer),
                source=classify_source(referrer, campaign),
                campaign=str(campaign)[:120] if campaign else None,
                device=classify_device(user_agent),
                started_at=now,
                last_seen_at=now,
                active_ms=0,
            )
            self.session.add(visit)
            await self.session.flush()
            await self._close_previous_journey(visit)
            return visit

        visit.last_seen_at = now
        # Never added to: the page reports its running total, so a beacon that arrives
        # twice reports the same total twice rather than doubling it.
        visit.active_ms = max(visit.active_ms, min(max(int(active_ms), 0), MAX_ACTIVE_MS))
        if event == "close":
            visit.ended_at = now
        if event == "action" and action:
            self._apply_next_action(visit, action=action, detail=action_detail, at=now)
        return visit

    async def record_signup(
        self,
        *,
        user_id: UUID,
        remote_address: str,
        user_agent: str,
        context: dict[str, Any] | None = None,
    ) -> SiteSignupAttribution | None:
        """One account created. Written once per account, whatever the door was."""

        existing = await self.session.scalar(
            select(SiteSignupAttribution).where(SiteSignupAttribution.user_id == user_id)
        )
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        key = visitor_key(
            self.settings,
            remote_address=remote_address,
            user_agent=user_agent,
            at=now,
        )
        first_visit = await self.session.scalar(
            select(SiteVisit)
            .where(SiteVisit.visitor_key == key)
            .order_by(SiteVisit.started_at.asc())
            .limit(1)
        )
        record = SiteSignupAttribution(
            user_id=user_id,
            visitor_key=key if first_visit is not None else None,
            entry_path=first_visit.path if first_visit else None,
            source=first_visit.source if first_visit else "direct",
            campaign=first_visit.campaign if first_visit else None,
            context=dict(context or {}),
            created_at=now,
        )
        self.session.add(record)
        if first_visit is not None:
            self._apply_next_action(first_visit, action="signup", detail="/signup", at=now)
        await self.session.flush()
        return record

    async def _close_previous_journey(self, visit: SiteVisit) -> None:
        """The page this person opened *before* now has an answer to "what next?"."""

        previous = await self.session.scalar(
            select(SiteVisit)
            .where(
                SiteVisit.visitor_key == visit.visitor_key,
                SiteVisit.id != visit.id,
                SiteVisit.next_action.is_(None),
                SiteVisit.started_at <= visit.started_at,
            )
            .order_by(SiteVisit.started_at.desc())
            .limit(1)
        )
        if previous is None:
            return
        self._apply_next_action(
            previous,
            action=classify_next_action(visit.path),
            detail=visit.path,
            at=visit.started_at,
        )

    @staticmethod
    def _apply_next_action(
        visit: SiteVisit,
        *,
        action: str,
        detail: str | None,
        at: datetime,
    ) -> None:
        """The first thing that happened after a page is what "next" means.

        Only recorded once. A person who reads three more pages did one thing next and
        then kept going; overwriting it would make the journey report describe the last
        page of every visit instead of the step after the one being measured.
        """

        if visit.next_action is not None:
            return
        if action not in {"page", "chat", "signup", "pricing"}:
            return
        visit.next_action = action
        visit.next_action_detail = (detail or "")[:200] or None
        visit.next_action_at = at

    async def report(
        self,
        *,
        days: int = 30,
        tag: str | None = None,
        landing_only: bool = True,
    ) -> dict[str, Any]:
        """Every number the Stats page shows, measured over one window."""

        days = max(1, min(int(days), 365))
        now = datetime.now(UTC)
        since = now - timedelta(days=days)
        previous_since = since - timedelta(days=days)
        selected = TAGS_BY_KEY.get(tag or "")

        # The live window reaches the present moment; the one before it stops where the
        # live one starts. See `visits_within` for why only one of them includes its end.
        current = await self._window(since, now, selected, landing_only, include_until=True)
        earlier = await self._window(previous_since, since, selected, landing_only)
        return {
            "days": days,
            "tag": selected.key if selected else "",
            "tag_label": selected.label if selected else "Everything",
            "landing_only": landing_only,
            "since": since,
            "until": now,
            "tiles": self._tiles(current, earlier, selected=selected),
            "journey": current["journey"],
            "daily": current["daily"],
            "top_pages": current["top_pages"],
            "tag_groups": await self._tag_groups(since, now, landing_only),
            "measured": current["visits"] > 0,
        }

    async def _window(
        self,
        since: datetime,
        until: datetime,
        selected: TagDefinition | None,
        landing_only: bool,
        *,
        include_until: bool = False,
    ) -> dict[str, Any]:
        def scoped(statement: Select[Any]) -> Select[Any]:
            statement = statement.where(
                recorded_within(SiteVisit.started_at, since, until, include_until)
            )
            if landing_only:
                statement = statement.where(SiteVisit.is_landing.is_(True))
            if selected is not None:
                statement = statement.where(selected.predicate())
            return statement

        visits = int(await self.session.scalar(scoped(select(func.count(SiteVisit.id)))) or 0)
        viewers = int(
            await self.session.scalar(
                scoped(select(func.count(func.distinct(SiteVisit.visitor_key))))
            )
            or 0
        )
        # Only visits that were really measured feed the average. A page closed before
        # the first second is a real visit and a meaningless duration, and counting it as
        # zero would drag the average towards zero for everybody else.
        average_ms = float(
            await self.session.scalar(
                scoped(select(func.avg(SiteVisit.active_ms))).where(SiteVisit.active_ms > 0)
            )
            or 0.0
        )
        measured_visits = int(
            await self.session.scalar(
                scoped(select(func.count(SiteVisit.id))).where(SiteVisit.active_ms > 0)
            )
            or 0
        )
        journey_rows = (
            await self.session.execute(
                scoped(select(SiteVisit.next_action, func.count(SiteVisit.id))).group_by(
                    SiteVisit.next_action
                )
            )
        ).all()
        page_rows = (
            await self.session.execute(
                scoped(
                    select(
                        SiteVisit.next_action_detail,
                        func.count(SiteVisit.id).label("total"),
                    )
                )
                .where(SiteVisit.next_action == "page")
                .group_by(SiteVisit.next_action_detail)
                .order_by(func.count(SiteVisit.id).desc())
                .limit(6)
            )
        ).all()
        daily_rows = (
            await self.session.execute(
                scoped(
                    select(
                        func.date(SiteVisit.started_at).label("day"),
                        func.count(func.distinct(SiteVisit.visitor_key)),
                    )
                )
                .group_by(func.date(SiteVisit.started_at))
                .order_by(func.date(SiteVisit.started_at))
            )
        ).all()
        signups = int(
            await self.session.scalar(
                select(func.count(SiteSignupAttribution.id)).where(
                    recorded_within(
                        SiteSignupAttribution.created_at, since, until, include_until
                    ),
                    *(
                        [SiteSignupAttribution.source == selected.key.split(":", 1)[1]]
                        if selected is not None and selected.key.startswith("source:")
                        else []
                    ),
                )
            )
            or 0
        )
        # Accounts the product really has, whether or not a visit was measured first.
        # The two numbers differ when somebody signs up from a link that never touched
        # the public site, and the page says which is which rather than hiding the gap.
        accounts = int(
            await self.session.scalar(
                select(func.count(User.id)).where(
                    User.created_at >= since, User.created_at < until
                )
            )
            or 0
        )
        return {
            "visits": visits,
            "viewers": viewers,
            "average_ms": average_ms,
            "measured_visits": measured_visits,
            "signups": signups,
            "accounts": accounts,
            "journey": self._journey(journey_rows, visits),
            "top_pages": [
                {"path": row[0] or "Another page", "count": int(row[1])} for row in page_rows
            ],
            "daily": [
                {"day": str(row[0]), "viewers": int(row[1])} for row in daily_rows
            ],
        }

    @staticmethod
    def _journey(rows: Sequence[Any], total: int) -> list[dict[str, Any]]:
        counts = {row[0] or "left": int(row[1]) for row in rows}
        order = (
            ("signup", "Went to sign up"),
            ("chat", "Opened the chat"),
            ("pricing", "Opened pricing"),
            ("page", "Read another page"),
            ("left", "Left the site"),
        )
        return [
            {
                "key": key,
                "label": label,
                "count": counts.get(key, 0),
                "share": round(counts.get(key, 0) * 100 / total, 1) if total else 0.0,
            }
            for key, label in order
        ]

    def _tiles(
        self,
        current: dict[str, Any],
        earlier: dict[str, Any],
        *,
        selected: TagDefinition | None = None,
    ) -> list[dict[str, Any]]:
        average_seconds = round(current["average_ms"] / 1000)
        earlier_seconds = round(earlier["average_ms"] / 1000)
        # An account is not a page visit, so most tags cannot narrow it: "Phone" selects
        # visits, and nothing records which device an account was made on. The tile says
        # so rather than looking as if it had been filtered along with the others.
        signup_hint = f"{current['signups']:,} came from a measured visit."
        if selected is not None and not selected.key.startswith("source:"):
            signup_hint += " Not narrowed by this tag."
        return [
            {
                "key": "viewers",
                "label": "People",
                "value": current["viewers"],
                "display": f"{current['viewers']:,}",
                "hint": "Counted once each, per day.",
                "change": _change(current["viewers"], earlier["viewers"]),
            },
            {
                "key": "views",
                "label": "Page opens",
                "value": current["visits"],
                "display": f"{current['visits']:,}",
                "hint": "Every time the page was opened.",
                "change": _change(current["visits"], earlier["visits"]),
            },
            {
                "key": "dwell",
                "label": "Time on page",
                "value": average_seconds,
                "display": _duration(average_seconds),
                "hint": f"Average of {current['measured_visits']:,} measured visits.",
                "change": _change(average_seconds, earlier_seconds),
            },
            {
                "key": "signups",
                "label": "Sign-ups",
                "value": current["accounts"],
                "display": f"{current['accounts']:,}",
                "hint": signup_hint,
                "change": _change(current["accounts"], earlier["accounts"]),
            },
        ]

    async def _tag_groups(
        self,
        since: datetime,
        until: datetime,
        landing_only: bool,
    ) -> list[dict[str, Any]]:
        """Every chip, with how many visits it selects. A chip that selects nothing is
        still shown: an empty answer is information, and hiding it makes the page look
        like the tag does not exist."""

        # Called only for the live window, so it reaches the present moment like the
        # tiles above it. A chip that counted fewer visits than the tile beside it would
        # be read as a filter bug rather than as a boundary.
        base = recorded_within(SiteVisit.started_at, since, until, True)
        if landing_only:
            base = and_(base, SiteVisit.is_landing.is_(True))
        columns = [
            func.sum(case((item.predicate(), 1), else_=0)).label(f"tag_{index}")
            for index, item in enumerate(TAG_DEFINITIONS)
        ]
        row = (await self.session.execute(select(*columns).where(base))).first()
        values = list(row) if row is not None else []
        groups: dict[str, list[dict[str, Any]]] = {}
        for index, item in enumerate(TAG_DEFINITIONS):
            counted = values[index] if index < len(values) else 0
            groups.setdefault(item.group, []).append(
                {
                    "key": item.key,
                    "label": item.label,
                    "count": int(counted or 0),
                }
            )
        return [{"group": name, "tags": items} for name, items in groups.items()]

    async def close_stale_visits(self, *, limit: int = 500) -> int:
        """Finish visits whose page stopped reporting.

        A browser that is force-quit never sends its closing beacon. Without this the
        visit stays open for ever and its measured time is whatever the last ping said —
        which is correct, so this only stamps the end time and changes no duration.
        """

        cutoff = datetime.now(UTC) - timedelta(minutes=STALE_VISIT_MINUTES)
        rows = list(
            (
                await self.session.scalars(
                    select(SiteVisit)
                    .where(SiteVisit.ended_at.is_(None), SiteVisit.last_seen_at < cutoff)
                    .limit(max(1, limit))
                )
            ).all()
        )
        for row in rows:
            row.ended_at = row.last_seen_at
        return len(rows)

    async def recent_visits(self, *, limit: int = 12) -> list[dict[str, Any]]:
        """The last few visits, for the live strip. No identity, only shape.

        ``reading`` is the reason ``close_stale_visits`` exists: a page that has not
        ended and is still reporting is somebody who is on the site right now, and a
        browser that was force-quit would otherwise show as reading for ever.
        """

        rows = list(
            (
                await self.session.scalars(
                    select(SiteVisit).order_by(SiteVisit.started_at.desc()).limit(limit)
                )
            ).all()
        )
        return [
            {
                "path": row.path,
                "device": row.device,
                "source": row.source,
                "seconds": round(row.active_ms / 1000),
                "next_action": row.next_action or "left",
                "reading": row.ended_at is None,
                "started_at": row.started_at.isoformat(),
            }
            for row in rows
        ]


def _change(current: float, earlier: float) -> dict[str, Any]:
    """How this window compares with the one before it.

    ``None`` when there is nothing to compare against. A jump from zero is not a
    percentage — reporting it as "+100%" would put a number on a comparison that was
    never made.
    """

    if not earlier:
        return {"percent": None, "direction": "flat" if not current else "up"}
    percent = round((current - earlier) * 100 / earlier)
    return {
        "percent": percent,
        "direction": "up" if percent > 0 else "down" if percent < 0 else "flat",
    }


def _duration(seconds: float) -> str:
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes, remainder = divmod(total, 60)
    return f"{minutes}m {remainder:02d}s"


def valid_session_key(value: str) -> bool:
    """A session key is 24–64 hexadecimal characters and nothing else.

    Checked rather than trusted: it is the primary key of a public write, so anything a
    caller can put there ends up in an index. Rejecting the shape closes that.
    """

    text = (value or "").strip()
    return 24 <= len(text) <= 64 and all(
        character in "0123456789abcdefABCDEF" for character in text
    )
