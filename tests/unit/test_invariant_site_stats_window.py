"""The Stats window has one boundary rule, and it reaches the present moment.

Three separate queries built this comparison by hand — the tiles, the tag chips, and the
sign-up count, across two tables. All three used a half-open range ending at the report's
own ``now()``, and a row is stamped with ``now()`` at the instant it is written. On a clock
that advances in steps, those two readings can be the identical value, and the row then
sits exactly on an excluded edge: a visit that just happened counts as nobody.

It surfaced as a test that passed alone and failed in a long run, which is the worst way
for it to surface, because the honest reading of that is "the machine was busy" rather
than "the report drops the newest row".

These tests assert the rule for every column the report measures, so the next table added
to the Stats page cannot quietly grow a fourth copy with a different edge.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from ai_market_monitor.db.models import SiteSignupAttribution, SiteVisit
from ai_market_monitor.services.site_analytics import SiteAnalyticsService, recorded_within

#: Every timestamp column the report windows. Adding one to the page without adding it
#: here leaves its boundary untested, which is how the third copy went unnoticed.
WINDOWED_COLUMNS = {
    "site_visits.started_at": SiteVisit.started_at,
    "site_signup_attributions.created_at": SiteSignupAttribution.created_at,
}

SINCE = datetime(2026, 8, 1, tzinfo=UTC)
UNTIL = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("name", sorted(WINDOWED_COLUMNS))
def test_the_live_edge_includes_a_row_stamped_at_that_exact_instant(name: str) -> None:
    """The whole defect, stated as a rule."""

    column = WINDOWED_COLUMNS[name]
    live = recorded_within(column, SINCE, UNTIL, True)
    joined = recorded_within(column, SINCE, UNTIL, False)

    assert "<=" in str(live.compile()), f"{name}: the live window must reach its end"
    assert "<=" not in str(joined.compile()), f"{name}: a window join must stay exclusive"


@pytest.mark.parametrize("name", sorted(WINDOWED_COLUMNS))
def test_the_lower_edge_is_inclusive_either_way(name: str) -> None:
    """A row landing exactly on the start belongs to the window that starts there."""

    for include_until in (True, False):
        rendered = str(recorded_within(WINDOWED_COLUMNS[name], SINCE, UNTIL, include_until))
        assert ">=" in rendered


async def test_a_visit_recorded_this_instant_is_counted(test_context) -> None:
    """End to end, through the real service, with no sleep and no clock control.

    Recording and reporting in the same breath is the exact shape that failed: the two
    ``now()`` calls can land on the same tick. Before the fix this asserted 0.
    """

    async with test_context["session_factory"]() as session:
        service = SiteAnalyticsService(session, test_context["settings"])
        await service.record(
            event="open",
            session_key="a" * 32,
            path="/",
            remote_address="203.0.113.77",
            user_agent="Mozilla/5.0",
        )
        await session.commit()
        report = await service.report(days=30)

    tiles = {item["key"]: item for item in report["tiles"]}
    assert tiles["views"]["value"] == 1
    assert report["measured"] is True


async def test_a_signup_recorded_this_instant_is_counted(test_context) -> None:
    """The same instant, on the other table. This is the one the shared rule caught."""

    from ai_market_monitor.db.models import User

    async with test_context["session_factory"]() as session:
        person = User(display_name="Just now")
        session.add(person)
        await session.flush()
        service = SiteAnalyticsService(session, test_context["settings"])
        await service.record_signup(
            user_id=person.id,
            remote_address="203.0.113.78",
            user_agent="Mozilla/5.0",
        )
        await session.commit()
        report = await service.report(days=30, landing_only=False)

    signups = next(item for item in report["tiles"] if item["key"] == "signups")
    assert signups["value"] == 1


def test_every_count_in_the_window_uses_the_one_boundary_rule() -> None:
    """No count inside ``_window`` may write its own ``>=`` / ``<`` on a timestamp.

    ``recorded_within`` exists because that comparison "was written thrice" — its own
    docstring says so. The extraction then missed a fourth copy: the ``accounts`` count
    hand-wrote ``User.created_at >= since, User.created_at < until``, which ignores
    ``include_until``. So the live window stopped just short of the present moment, and a
    person who signed up in the same instant the Stats page was opened was not counted.

    It read as "nobody signed up", never as a boundary being off by one tick, and it is
    why ``test_a_signup_recorded_this_instant_is_counted`` failed only sometimes — the two
    timestamps have to land on the same tick for it to show.

    A source rule, not a behaviour rule, on purpose: the next count added to this window
    is the one at risk, and no amount of testing today's counts catches tomorrow's copy.
    """
    import inspect

    from ai_market_monitor.services.site_analytics import SiteAnalyticsService

    source = inspect.getsource(SiteAnalyticsService._window)
    offenders = re.findall(
        r"\b\w+\.(?:created_at|started_at|recorded_at)\s*(?:>=|<=|<|>)\s*\w+",
        source,
    )
    assert not offenders, (
        "these comparisons in _window bypass recorded_within: "
        f"{offenders}. Every window boundary goes through the one rule, or the counts on "
        "one page disagree with each other."
    )


async def test_an_account_created_this_instant_is_counted(test_context) -> None:
    """The counter the Sign-ups tile actually shows, at the boundary.

    The tile keyed ``signups`` displays ``accounts`` — every account the product has,
    whether or not a visit was measured first — and uses the attributed sign-up count only
    in its hint. So this is the number a reader sees, and it is the one that was wrong.
    """
    from ai_market_monitor.db.models import User

    async with test_context["session_factory"]() as session:
        session.add(User(display_name="This very instant"))
        await session.commit()
        service = SiteAnalyticsService(session, test_context["settings"])
        report = await service.report(days=30, landing_only=False)

    tile = next(item for item in report["tiles"] if item["key"] == "signups")
    assert tile["value"] == 1, (
        "an account created a moment before the report was asked for was not counted. "
        "The live window must include the present moment."
    )


async def test_the_two_windows_never_both_claim_the_same_row(test_context) -> None:
    """Why the join stays exclusive.

    A row sitting exactly on the line between "this period" and "the one before" must be
    counted once. Making every edge inclusive to fix the live edge would have traded a
    vanishing row for a doubled one, which is harder to notice and worse to explain.
    """

    boundary = datetime(2026, 8, 10, tzinfo=UTC)
    async with test_context["session_factory"]() as session:
        session.add(
            SiteVisit(
                visitor_key="on-the-line",
                session_key="b" * 32,
                path="/",
                is_landing=True,
                started_at=boundary,
                last_seen_at=boundary,
                active_ms=1_000,
            )
        )
        await session.commit()

        earlier = boundary - timedelta(days=5)
        later = boundary + timedelta(days=5)
        counted = 0
        for since, until, include in ((earlier, boundary, False), (boundary, later, True)):
            found = await session.scalar(
                select(func.count(SiteVisit.id)).where(
                    recorded_within(SiteVisit.started_at, since, until, include)
                )
            )
            counted += int(found or 0)

    assert counted == 1
