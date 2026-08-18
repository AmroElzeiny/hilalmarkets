"""How many people looked at the public site, for how long, and what they did next.

One table, one row per visit. A "visit" is one person on one page: it opens when the
page is opened, it grows while the page is actually in front of them, and it closes when
they leave. Everything the Stats page shows is counted from these rows.

Nobody is named here. ``visitor_key`` is a one-way hash of the caller's address and
browser, mixed with the day, so two page views by the same person on the same day count
as one person and the value cannot be turned back into an address. Nothing is written to
the visitor's browser, which is why this measures without asking for cookie permission.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ai_market_monitor.db.base import Base, UUIDPrimaryKeyMixin


class SiteVisit(UUIDPrimaryKeyMixin, Base):
    """One person, one page, one visit.

    ``active_ms`` is time the page was really in front of the person: the browser stops
    the counter when the tab is hidden. Wall-clock time from open to close would count a
    tab left open overnight as an eight-hour read, which is the number every naive
    "time on page" reports.
    """

    __tablename__ = "site_visits"
    __table_args__ = (
        # One row per page instance. A retried beacon finds the row it already wrote.
        Index("ix_site_visit_session", "session_key", unique=True),
        # "How many people, in this window" and "how long did they stay".
        Index("ix_site_visit_started", "started_at"),
        # "Is this the same person as the visit before?" — the journey question.
        Index("ix_site_visit_visitor", "visitor_key", "started_at"),
    )

    #: Pseudonymous, rotates every day. Same person, same day, same value.
    visitor_key: Mapped[str] = mapped_column(String(64), nullable=False)
    #: One page instance in one tab. Supplied by the page, unique across the table.
    session_key: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(String(200), nullable=False)
    #: True only for the front page. The viewer and dwell numbers are about that page.
    is_landing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Where they came from, host only — never a full address with its query string.
    referrer_host: Mapped[str | None] = mapped_column(String(200))
    #: ``direct``, ``search``, ``social``, ``referral`` or ``campaign``.
    source: Mapped[str] = mapped_column(String(24), default="direct", nullable=False)
    campaign: Mapped[str | None] = mapped_column(String(120))
    #: ``phone``, ``tablet`` or ``desktop``.
    device: Mapped[str] = mapped_column(String(16), default="desktop", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Last time the page said it was still open. Used to close visits nobody ended.
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: What they did after this page: ``page``, ``chat``, ``signup`` or ``pricing``.
    #: Empty while the visit is still open, and empty for ever when they simply left —
    #: "left" is the absence of a next action, not a value, so nothing has to invent one.
    #:
    #: The labels the Stats page filters by are derived from these columns by
    #: ``services/site_analytics.py``. There is deliberately no second stored copy of
    #: them: a tag column and a filter over the real columns would be two answers to the
    #: same question, and they would drift.
    next_action: Mapped[str | None] = mapped_column(String(24))
    next_action_detail: Mapped[str | None] = mapped_column(String(200))
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SiteSignupAttribution(UUIDPrimaryKeyMixin, Base):
    """The account that came out of a visit.

    Separate from ``site_visits`` because a signup is a fact about a *person*, not about
    a page: it is written once, when the account is really created, and it must survive
    the visit rows being swept. Counting signups from the visits table would count the
    click that opened the signup form, not the account.
    """

    __tablename__ = "site_signup_attributions"
    __table_args__ = (
        Index("ix_site_signup_user", "user_id", unique=True),
        Index("ix_site_signup_created", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: The visitor whose journey ended here, when one was measured.
    visitor_key: Mapped[str | None] = mapped_column(String(64))
    #: The first page of that journey, so "which page brings accounts" is answerable.
    entry_path: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(24), default="direct", nullable=False)
    campaign: Mapped[str | None] = mapped_column(String(120))
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
