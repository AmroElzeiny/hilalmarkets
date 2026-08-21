"""Every visit of the last day, one row each, on the reader's own clock.

The Stats tiles answer "how many people" and "how long on average". An average cannot
answer "what happened at half past eight" — it hides the one visitor who read for six
minutes behind the twenty who bounced. This list is the same rows the tiles are counted
from, shown rather than summarised.

Two things here are easy to get wrong and are pinned down below.

**The clock.** Every moment is stored in UTC, which is the only sane way to store one.
The person reading the page does not live on UTC, and "was that this morning?" is a
question about *their* day. The conversion happens once, on the way out; nothing about
what is stored changes.

**"Left" is not a value.** ``next_action`` is empty when somebody simply left, and empty
while they are still reading. Those are two different things and neither is a stored
label, so the list must say which without inventing a third state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ai_market_monitor.db.models import SiteVisit
from ai_market_monitor.services.site_analytics import (
    REPORT_TIMEZONE,
    REPORT_TIMEZONE_LABEL,
    SiteAnalyticsService,
)


def _visit(
    *,
    started_at: datetime,
    path: str = "/",
    active_ms: int = 30_000,
    next_action: str | None = None,
    ended: bool = True,
    key: str = "a",
) -> SiteVisit:
    return SiteVisit(
        visitor_key=key * 16,
        session_key=key * 32,
        path=path,
        is_landing=path == "/",
        source="direct",
        device="desktop",
        started_at=started_at,
        last_seen_at=started_at,
        ended_at=started_at + timedelta(seconds=30) if ended else None,
        active_ms=active_ms,
        next_action=next_action,
        next_action_at=started_at + timedelta(seconds=31) if next_action else None,
    )


async def test_a_visit_from_the_last_day_is_listed_with_its_own_row(test_context) -> None:
    """The whole point: one row per visit, not one number for all of them."""

    started = datetime.now(UTC) - timedelta(hours=2)
    async with test_context["session_factory"]() as session:
        session.add(_visit(started_at=started, path="/pricing", active_ms=95_000))
        await session.commit()
        report = await SiteAnalyticsService(session, test_context["settings"]).visits_last_day()

    assert report["total"] == 1
    assert len(report["visits"]) == 1
    row = report["visits"][0]
    assert row["path"] == "/pricing"
    assert row["duration"] == "1m 35s"
    assert row["seconds"] == 95


async def test_the_time_is_shown_on_the_readers_clock_not_utc(test_context) -> None:
    """Stored in UTC, read on UTC+3. The stored value is never touched."""

    started = datetime.now(UTC) - timedelta(hours=3)
    async with test_context["session_factory"]() as session:
        session.add(_visit(started_at=started))
        await session.commit()
        report = await SiteAnalyticsService(session, test_context["settings"]).visits_last_day()

    row = report["visits"][0]
    assert report["timezone_label"] == REPORT_TIMEZONE_LABEL
    assert row["local_time"] == started.astimezone(REPORT_TIMEZONE).strftime("%H:%M")
    # The stored moment is still the UTC one it was written as.
    assert row["started_at"].startswith(started.strftime("%Y-%m-%dT%H:%M"))


async def test_leaving_and_still_reading_are_told_apart(test_context) -> None:
    """Both have no next action stored, and they are not the same thing."""

    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        session.add(_visit(started_at=now - timedelta(minutes=30), key="b", ended=True))
        session.add(_visit(started_at=now - timedelta(minutes=10), key="c", ended=False))
        await session.commit()
        report = await SiteAnalyticsService(session, test_context["settings"]).visits_last_day()

    actions = {row["next_action"] for row in report["visits"]}
    assert actions == {"left", "still reading"}
    open_rows = [row for row in report["visits"] if row["still_open"]]
    assert len(open_rows) == 1
    assert open_rows[0]["next_action"] == "still reading"


async def test_a_real_next_action_is_shown_with_when_it_happened(test_context) -> None:
    """What they did after the page, and at what time on the reader's clock."""

    started = datetime.now(UTC) - timedelta(hours=1)
    async with test_context["session_factory"]() as session:
        session.add(_visit(started_at=started, next_action="signup"))
        await session.commit()
        report = await SiteAnalyticsService(session, test_context["settings"]).visits_last_day()

    row = report["visits"][0]
    assert row["next_action"] == "signup"
    assert row["next_action_time"], "the moment of the next action was not shown"


async def test_a_visit_older_than_a_day_is_not_listed(test_context) -> None:
    """The window is the last 24 hours, not everything ever measured."""

    async with test_context["session_factory"]() as session:
        session.add(
            _visit(started_at=datetime.now(UTC) - timedelta(hours=26), key="d")
        )
        session.add(_visit(started_at=datetime.now(UTC) - timedelta(hours=1), key="e"))
        await session.commit()
        report = await SiteAnalyticsService(session, test_context["settings"]).visits_last_day()

    assert report["total"] == 1
    assert len(report["visits"]) == 1


async def test_the_newest_visit_is_first(test_context) -> None:
    """A day is read from now backwards."""

    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        session.add(_visit(started_at=now - timedelta(hours=5), path="/older", key="f"))
        session.add(_visit(started_at=now - timedelta(hours=1), path="/newer", key="g"))
        await session.commit()
        report = await SiteAnalyticsService(session, test_context["settings"]).visits_last_day()

    assert [row["path"] for row in report["visits"]] == ["/newer", "/older"]


async def test_a_busy_day_says_how_many_it_did_not_show(test_context) -> None:
    """A page ceiling must never make a busy day look like a quiet one."""

    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        for index in range(5):
            session.add(
                _visit(
                    started_at=now - timedelta(minutes=index + 1),
                    key=chr(ord("h") + index),
                )
            )
        await session.commit()
        report = await SiteAnalyticsService(session, test_context["settings"]).visits_last_day(
            limit=2
        )

    assert report["total"] == 5, "the count must be the real number, not the shown number"
    assert report["shown"] == 2
    assert report["truncated"] is True
