"""The rules behind the Stats page, asserted for every member of each family.

Four numbers were asked for — how many people, how long they stayed, what they did next,
and how many signed up — and every one of them is a rule rather than a case:

* a **person** is one visitor key, whatever the page, whatever the device;
* **time on page** is time the page was in front of them, never wall-clock time, and
  never the same seconds counted twice because a beacon was retried;
* **what they did next** is the *first* thing that happened after a page, recorded once;
* a **tag** says what it selects, and selects exactly what it says.

Each test below walks the whole vocabulary — every tag, every sign-up door, every
device string — so a fix that only helps the one input somebody reported fails here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from ai_market_monitor.api.request_guards import rate_limit_rules
from ai_market_monitor.core.config import RATE_LIMIT_SCOPES
from ai_market_monitor.db.models import SiteVisit, User
from ai_market_monitor.services.site_analytics import (
    LANDING_PATH,
    MAX_ACTIVE_MS,
    SIGNUP_PATHS,
    TAG_DEFINITIONS,
    TAGS_BY_KEY,
    SiteAnalyticsService,
    classify_device,
    classify_next_action,
    classify_source,
    normalize_path,
    valid_session_key,
    visitor_key,
)

# --------------------------------------------------------------------------------
# One list of rate-limited scopes, not two.
# --------------------------------------------------------------------------------


def test_every_rate_limited_scope_is_named_in_exactly_one_place():
    """The guard rules and the settings validator must agree, by construction.

    They were two hand-written lists. Adding a ceiling to one and not the other stopped
    the application booting with a message that named neither scope — which is what
    happened the first time a new public endpoint was given one. Both read
    ``RATE_LIMIT_SCOPES`` now, so this walks whatever that tuple holds rather than a
    third copy typed into a test.
    """

    from ai_market_monitor.core.config import Settings

    settings = Settings(app_env="test")
    assert {rule.scope for rule in rate_limit_rules(settings)} == set(RATE_LIMIT_SCOPES)
    assert set(settings.api_rate_limits) == set(RATE_LIMIT_SCOPES)
    assert len(RATE_LIMIT_SCOPES) == len(set(RATE_LIMIT_SCOPES))


@pytest.mark.parametrize("scope", RATE_LIMIT_SCOPES)
def test_every_scope_has_a_positive_ceiling_and_window(scope):
    from ai_market_monitor.core.config import Settings

    values = Settings(app_env="test").api_rate_limits[scope]
    assert values["limit"] > 0
    assert values["window_seconds"] > 0


# --------------------------------------------------------------------------------
# Reading an address: the path, and nothing but the path.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("/", "/"),
        ("", "/"),
        ("pricing", "/pricing"),
        ("/pricing/", "/pricing"),
        ("/pricing?utm_source=x", "/pricing"),
        ("/pricing#plans", "/pricing"),
        ("/pricing?a=1#b", "/pricing"),
        ("https://hilalmarkets.com/features", "/features"),
        ("https://hilalmarkets.com/features?ref=y", "/features"),
        ("https://hilalmarkets.com", "/"),
    ],
)
def test_a_reported_address_is_stored_as_its_path_alone(supplied, expected):
    """Query strings carry personal values often enough that none is ever stored."""

    assert normalize_path(supplied) == expected


def test_a_very_long_address_can_never_overflow_its_column():
    assert len(normalize_path("/" + "a" * 5000)) <= 200


# --------------------------------------------------------------------------------
# What they did next: every door means what it says.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(SIGNUP_PATHS))
def test_every_signup_door_counts_as_going_to_sign_up(path):
    """One list of doors. A door added to the product and not to this list would be
    counted as "read another page", which is the opposite conclusion."""

    assert classify_next_action(path) == "signup"


@pytest.mark.parametrize("path", ["/pricing", "/pricing/", "/subscribe?plan_code=trader"])
def test_the_price_page_is_its_own_step(path):
    assert classify_next_action(path) == "pricing"


@pytest.mark.parametrize("path", ["/features", "/help", "/about", "/contact", "/"])
def test_anything_else_is_reading_another_page(path):
    assert classify_next_action(path) == "page"


# --------------------------------------------------------------------------------
# Which device, and where they came from.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Mobile/15E148", "phone"),
        ("Mozilla/5.0 (Linux; Android 14) Mobile Safari", "phone"),
        ("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)", "tablet"),
        ("Mozilla/5.0 (Linux; Android 14; Tablet)", "tablet"),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "desktop"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "desktop"),
        ("", "desktop"),
    ],
)
def test_the_device_is_read_from_the_whole_family_of_browser_names(agent, expected):
    assert classify_device(agent) == expected


@pytest.mark.parametrize(
    ("referrer", "campaign", "expected"),
    [
        (None, None, "direct"),
        ("", None, "direct"),
        ("https://www.google.com/search?q=halal", None, "search"),
        ("https://duckduckgo.com/", None, "search"),
        ("https://x.com/hilalmarkets", None, "social"),
        ("https://www.instagram.com/", None, "social"),
        ("https://someblog.example/post", None, "referral"),
        # A campaign link is a campaign whatever the referrer says.
        ("https://www.google.com/", "spring", "campaign"),
        (None, "spring", "campaign"),
    ],
)
def test_where_a_visit_came_from_is_read_the_same_way_every_time(
    referrer, campaign, expected
):
    assert classify_source(referrer, campaign) == expected


# --------------------------------------------------------------------------------
# Tags: a chip selects exactly what it says.
# --------------------------------------------------------------------------------


def test_the_tag_vocabulary_has_one_owner_and_no_duplicates():
    assert {item.key: item for item in TAG_DEFINITIONS} == TAGS_BY_KEY
    assert len({item.key for item in TAG_DEFINITIONS}) == len(TAG_DEFINITIONS)
    assert all(item.label and item.group for item in TAG_DEFINITIONS)


@pytest.mark.parametrize("tag", TAG_DEFINITIONS, ids=lambda item: item.key)
async def test_every_tag_selects_only_the_visits_it_describes(test_context, tag):
    """Applied for real, one tag at a time, against rows that cover every value.

    The point is that the chip and the filter are the same object: a chip that said
    "Phone" while selecting computers would be worse than no chip at all.
    """

    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        index = 0
        for device in ("phone", "tablet", "desktop"):
            for source in ("direct", "search", "social", "referral", "campaign"):
                for action in ("signup", "chat", "pricing", "page", None):
                    index += 1
                    session.add(
                        SiteVisit(
                            visitor_key=f"visitor-{index}",
                            session_key=f"{index:032x}",
                            path=LANDING_PATH,
                            is_landing=True,
                            source=source,
                            device=device,
                            started_at=now,
                            last_seen_at=now,
                            active_ms=1000,
                            next_action=action,
                        )
                    )
        await session.commit()
        selected = list(
            (await session.scalars(select(SiteVisit).where(tag.predicate()))).all()
        )

    assert selected, f"{tag.key} selected nothing at all"
    field, value = tag.key.split(":", 1)
    for row in selected:
        if field == "device":
            assert row.device == value
        elif field == "source":
            assert row.source == value
        elif value == "left":
            assert row.next_action is None
        else:
            assert row.next_action == value


# --------------------------------------------------------------------------------
# Measuring one visit.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("event", ["ping", "close", "action"])
async def test_a_beacon_for_a_visit_that_was_never_opened_writes_nothing(
    test_context, event
):
    """Inventing a start time would invent a duration, so nothing is invented."""

    async with test_context["session_factory"]() as session:
        service = SiteAnalyticsService(session, test_context["settings"])
        result = await service.record(
            event=event,
            session_key="f" * 32,
            path="/",
            remote_address="203.0.113.9",
            user_agent="Mozilla/5.0",
            active_ms=5000,
            action="chat",
        )
        await session.commit()
        stored = list((await session.scalars(select(SiteVisit))).all())

    assert result is None
    assert stored == []


async def test_a_retried_beacon_never_counts_the_same_seconds_twice(test_context):
    """Browsers retry these on a flaky connection.

    The page reports its running total rather than an increment, and the server takes
    the larger of the two. An implementation that added would have turned one thirty-
    second read into a minute the first time a beacon arrived twice.
    """

    async with test_context["session_factory"]() as session:
        service = SiteAnalyticsService(session, test_context["settings"])
        key = "a" * 32
        common = {
            "session_key": key,
            "path": "/",
            "remote_address": "203.0.113.10",
            "user_agent": "Mozilla/5.0 (Windows NT 10.0)",
        }
        await service.record(event="open", **common)
        await service.record(event="ping", active_ms=15_000, **common)
        await service.record(event="ping", active_ms=15_000, **common)
        # An out-of-order beacon reporting less than what is already known is ignored.
        await service.record(event="ping", active_ms=9_000, **common)
        await service.record(event="close", active_ms=30_000, **common)
        await session.commit()
        visit = await session.scalar(select(SiteVisit).where(SiteVisit.session_key == key))

    assert visit is not None
    assert visit.active_ms == 30_000
    assert visit.ended_at is not None
    assert visit.is_landing is True


async def test_no_single_visit_may_claim_more_than_the_ceiling(test_context):
    """One page left in front of somebody for a day is not a day of reading, and one
    such row would move the average on its own."""

    async with test_context["session_factory"]() as session:
        service = SiteAnalyticsService(session, test_context["settings"])
        common = {
            "session_key": "b" * 32,
            "path": "/",
            "remote_address": "203.0.113.11",
            "user_agent": "Mozilla/5.0",
        }
        await service.record(event="open", **common)
        await service.record(event="close", active_ms=99 * 60 * 60 * 1000, **common)
        await session.commit()
        visit = await session.scalar(
            select(SiteVisit).where(SiteVisit.session_key == "b" * 32)
        )

    assert visit is not None
    assert visit.active_ms == MAX_ACTIVE_MS


@pytest.mark.parametrize(
    ("second_path", "expected"),
    [("/pricing", "pricing"), ("/features", "page"), ("/signup", "signup")],
)
async def test_opening_a_second_page_answers_what_the_first_one_led_to(
    test_context, second_path, expected
):
    async with test_context["session_factory"]() as session:
        service = SiteAnalyticsService(session, test_context["settings"])
        address, agent = "203.0.113.12", "Mozilla/5.0 (Windows NT 10.0)"
        await service.record(
            event="open",
            session_key="c" * 32,
            path="/",
            remote_address=address,
            user_agent=agent,
        )
        await service.record(
            event="open",
            session_key="d" * 32,
            path=second_path,
            remote_address=address,
            user_agent=agent,
        )
        await session.commit()
        first = await session.scalar(
            select(SiteVisit).where(SiteVisit.session_key == "c" * 32)
        )

    assert first is not None
    assert first.next_action == expected
    assert first.next_action_detail == second_path


async def test_the_next_step_is_recorded_once_and_never_rewritten(test_context):
    """A person who reads three more pages did one thing next and then kept going.

    Overwriting would make the journey report describe the last page of every visit
    instead of the step after the one being measured.
    """

    async with test_context["session_factory"]() as session:
        service = SiteAnalyticsService(session, test_context["settings"])
        address, agent = "203.0.113.13", "Mozilla/5.0 (Windows NT 10.0)"
        await service.record(
            event="open",
            session_key="1" * 32,
            path="/",
            remote_address=address,
            user_agent=agent,
        )
        for index, path in enumerate(("/features", "/pricing", "/signup"), start=2):
            await service.record(
                event="open",
                session_key=str(index) * 32,
                path=path,
                remote_address=address,
                user_agent=agent,
            )
        await session.commit()
        first = await session.scalar(
            select(SiteVisit).where(SiteVisit.session_key == "1" * 32)
        )

    assert first is not None
    assert first.next_action == "page"
    assert first.next_action_detail == "/features"


async def test_opening_the_chat_is_the_one_step_the_page_reports_itself(test_context):
    async with test_context["session_factory"]() as session:
        service = SiteAnalyticsService(session, test_context["settings"])
        common = {
            "session_key": "e" * 32,
            "path": "/",
            "remote_address": "203.0.113.14",
            "user_agent": "Mozilla/5.0",
        }
        await service.record(event="open", **common)
        await service.record(
            event="action", action="chat", action_detail="/", **common
        )
        await session.commit()
        visit = await session.scalar(
            select(SiteVisit).where(SiteVisit.session_key == "e" * 32)
        )

    assert visit is not None
    assert visit.next_action == "chat"


# --------------------------------------------------------------------------------
# Who a visitor is, and who they are not.
# --------------------------------------------------------------------------------


def test_a_visitor_is_the_same_person_within_a_day_and_a_stranger_after_it(test_context):
    """Recognised for one day, never followed across days, never reversible."""

    settings = test_context["settings"]
    monday = datetime(2026, 5, 4, 9, 0, tzinfo=UTC)
    tuesday = monday + timedelta(days=1)
    same = {"remote_address": "203.0.113.20", "user_agent": "Mozilla/5.0 (Windows NT 10.0)"}

    morning = visitor_key(settings, at=monday, **same)
    evening = visitor_key(settings, at=monday + timedelta(hours=9), **same)
    next_day = visitor_key(settings, at=tuesday, **same)
    other_person = visitor_key(
        settings,
        at=monday,
        remote_address="203.0.113.21",
        user_agent=same["user_agent"],
    )

    assert morning == evening
    assert morning != next_day
    assert morning != other_person
    # Nothing about the person survives in the value itself.
    assert same["remote_address"] not in morning
    assert len(morning) == 64


@pytest.mark.parametrize(
    ("supplied", "accepted"),
    [
        ("a" * 32, True),
        ("A" * 24, True),
        ("f" * 64, True),
        ("a" * 23, False),
        ("a" * 65, False),
        ("", False),
        ("../../etc/passwd", False),
        ("zzzz" * 8, False),
        ("<script>alert(1)</script>xxxxxxxx", False),
    ],
)
def test_a_public_write_only_accepts_the_shape_it_expects(supplied, accepted):
    """It is the key of a public write, so its shape is checked rather than trusted."""

    assert valid_session_key(supplied) is accepted


# --------------------------------------------------------------------------------
# Sign-ups: accounts, not clicks.
# --------------------------------------------------------------------------------


async def test_a_signup_is_counted_once_per_account_however_many_times_it_is_recorded(
    test_context,
):
    async with test_context["session_factory"]() as session:
        person = User(display_name="New trader")
        session.add(person)
        await session.flush()
        service = SiteAnalyticsService(session, test_context["settings"])
        first = await service.record_signup(
            user_id=person.id,
            remote_address="203.0.113.30",
            user_agent="Mozilla/5.0",
        )
        second = await service.record_signup(
            user_id=person.id,
            remote_address="203.0.113.30",
            user_agent="Mozilla/5.0",
        )
        await session.commit()
        report = await SiteAnalyticsService(
            session, test_context["settings"]
        ).report(days=30, landing_only=False)

    assert first is not None
    assert second is not None
    assert first.id == second.id
    signups = next(item for item in report["tiles"] if item["key"] == "signups")
    assert signups["value"] == 1


async def test_a_signup_closes_the_journey_of_the_visit_that_led_to_it(test_context):
    async with test_context["session_factory"]() as session:
        person = User(display_name="Converted")
        session.add(person)
        await session.flush()
        service = SiteAnalyticsService(session, test_context["settings"])
        address, agent = "203.0.113.31", "Mozilla/5.0 (Windows NT 10.0)"
        await service.record(
            event="open",
            session_key="9" * 32,
            path="/",
            remote_address=address,
            user_agent=agent,
        )
        await service.record_signup(
            user_id=person.id, remote_address=address, user_agent=agent
        )
        await session.commit()
        visit = await session.scalar(
            select(SiteVisit).where(SiteVisit.session_key == "9" * 32)
        )

    assert visit is not None
    assert visit.next_action == "signup"


# --------------------------------------------------------------------------------
# The report itself.
# --------------------------------------------------------------------------------


async def test_the_report_counts_a_person_once_and_averages_only_measured_visits(
    test_context,
):
    """Two page opens by one person are one person, and a visit closed before the first
    second is a real visit with a meaningless duration — counting it as zero would drag
    everybody else's average towards zero."""

    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        session.add_all(
            [
                SiteVisit(
                    visitor_key="one-person",
                    session_key="1" * 32,
                    path="/",
                    is_landing=True,
                    started_at=now,
                    last_seen_at=now,
                    active_ms=20_000,
                ),
                SiteVisit(
                    visitor_key="one-person",
                    session_key="2" * 32,
                    path="/",
                    is_landing=True,
                    started_at=now,
                    last_seen_at=now,
                    active_ms=40_000,
                ),
                SiteVisit(
                    visitor_key="bounced",
                    session_key="3" * 32,
                    path="/",
                    is_landing=True,
                    started_at=now,
                    last_seen_at=now,
                    active_ms=0,
                ),
            ]
        )
        await session.commit()
        report = await SiteAnalyticsService(session, test_context["settings"]).report(
            days=30
        )

    tiles = {item["key"]: item for item in report["tiles"]}
    assert tiles["viewers"]["value"] == 2
    assert tiles["views"]["value"] == 3
    assert tiles["dwell"]["value"] == 30
    assert tiles["dwell"]["display"] == "30s"
    assert report["measured"] is True


async def test_a_jump_from_nothing_is_never_reported_as_a_percentage(test_context):
    """"+100%" would put a number on a comparison that was never made."""

    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        session.add(
            SiteVisit(
                visitor_key="first-ever",
                session_key="4" * 32,
                path="/",
                is_landing=True,
                started_at=now,
                last_seen_at=now,
                active_ms=5_000,
            )
        )
        await session.commit()
        report = await SiteAnalyticsService(session, test_context["settings"]).report(
            days=7
        )

    for tile in report["tiles"]:
        assert tile["change"]["percent"] is None


async def test_an_empty_window_reports_zero_rather_than_failing(test_context):
    async with test_context["session_factory"]() as session:
        report = await SiteAnalyticsService(session, test_context["settings"]).report(
            days=30
        )

    assert report["measured"] is False
    assert all(tile["value"] == 0 for tile in report["tiles"])
    # Every chip is still offered, with an honest count of nothing.
    offered = {item["key"] for group in report["tag_groups"] for item in group["tags"]}
    assert offered == set(TAGS_BY_KEY)


@pytest.mark.parametrize("tag", sorted(TAGS_BY_KEY), ids=lambda value: value)
async def test_applying_any_tag_returns_a_complete_report(test_context, tag):
    """Every chip has to be applicable, not only the ones with rows behind them."""

    async with test_context["session_factory"]() as session:
        report = await SiteAnalyticsService(session, test_context["settings"]).report(
            days=30, tag=tag
        )

    assert report["tag"] == tag
    assert report["tag_label"] == TAGS_BY_KEY[tag].label
    # An account is not a page visit, so a tag that describes a visit cannot narrow the
    # sign-up count. The tile says which it is rather than looking filtered.
    signups = next(item for item in report["tiles"] if item["key"] == "signups")
    narrowed = tag.startswith("source:")
    assert ("Not narrowed by this tag." in signups["hint"]) is not narrowed
    assert {item["key"] for item in report["tiles"]} == {
        "viewers",
        "views",
        "dwell",
        "signups",
    }
    assert [step["key"] for step in report["journey"]] == [
        "signup",
        "chat",
        "pricing",
        "page",
        "left",
    ]


async def test_a_visit_whose_page_stopped_reporting_is_closed_without_changing_its_time(
    test_context,
):
    """A force-quit browser never sends its closing beacon. Stamping the end time keeps
    the live list honest; changing the measured seconds would be inventing them."""

    long_ago = datetime.now(UTC) - timedelta(hours=3)
    async with test_context["session_factory"]() as session:
        session.add(
            SiteVisit(
                visitor_key="gone",
                session_key="5" * 32,
                path="/",
                is_landing=True,
                started_at=long_ago,
                last_seen_at=long_ago,
                active_ms=12_000,
            )
        )
        await session.commit()
        closed = await SiteAnalyticsService(
            session, test_context["settings"]
        ).close_stale_visits()
        await session.commit()
        visit = await session.scalar(
            select(SiteVisit).where(SiteVisit.session_key == "5" * 32)
        )

    assert closed == 1
    assert visit is not None
    assert visit.ended_at is not None
    assert visit.active_ms == 12_000


# --------------------------------------------------------------------------------
# The page and the server describe the same beacon.
# --------------------------------------------------------------------------------


def test_the_measuring_script_is_loaded_by_every_public_shell():
    """Both renderers of the public site, or the numbers only cover half of it.

    The site is drawn twice — React for the landing pages, Jinja for the rest — and a
    script added to one shell measures one of them. It is deliberately outside the React
    bundle: that bundle has to be rebuilt and copied by hand, so measurement written
    inside it stops the day somebody edits a component and forgets.
    """

    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for shell in (
        "src/ai_market_monitor/templates/hilal/public/react_site.html",
        "src/ai_market_monitor/templates/hilal/base_public.html",
    ):
        text = (root / shell).read_text(encoding="utf-8")
        assert "hm-visit-analytics.js" in text, shell
        assert "site_visit_measurement_enabled" in text, shell
    built_bundle = root / "src/ai_market_monitor/static/landing/assets/landing.js"
    assert "site-analytics/collect" not in built_bundle.read_text(
        encoding="utf-8", errors="ignore"
    )


def test_the_script_and_the_endpoint_use_the_same_words_for_the_same_things():
    """One vocabulary across the wire.

    A field renamed on the server and not in the page is the quietest failure this
    measurement can have: every beacon is refused, nothing is logged where anybody
    looks, and the Stats page simply reports zero for ever.
    """

    from pathlib import Path

    from ai_market_monitor.api.routers.site_analytics import SiteVisitBeacon

    root = Path(__file__).resolve().parents[2]
    script = (root / "src/ai_market_monitor/static/hm-visit-analytics.js").read_text(
        encoding="utf-8"
    )

    assert "/api/v1/site-analytics/collect" in script
    for field in SiteVisitBeacon.model_fields:
        assert field in script, field
    for event in ("open", "ping", "close", "action"):
        assert f'"{event}"' in script, event
    # The page must be able to say the visitor opened the chat, and nothing else about
    # what somebody did — the rest is worked out by the server from the next page open.
    assert 'action: "chat"' in script
    # It never writes an identifier to the visitor's device. That is why this measures
    # without a cookie banner, so it is checked rather than trusted.
    for storage in ("localStorage", "sessionStorage", "document.cookie"):
        assert storage not in script, storage


async def test_the_live_strip_says_who_is_reading_now_and_who_has_finished(test_context):
    """The one reader of ``ended_at``, and the reason the stale sweep exists.

    Without the sweep a browser that was force-quit would show as "reading now" for
    ever, which is the kind of number that makes a whole page untrustworthy.
    """

    now = datetime.now(UTC)
    long_ago = now - timedelta(hours=3)
    async with test_context["session_factory"]() as session:
        session.add_all(
            [
                SiteVisit(
                    visitor_key="here-now",
                    session_key="6" * 32,
                    path="/",
                    is_landing=True,
                    started_at=now,
                    last_seen_at=now,
                    active_ms=8_000,
                ),
                SiteVisit(
                    visitor_key="force-quit",
                    session_key="7" * 32,
                    path="/pricing",
                    started_at=long_ago,
                    last_seen_at=long_ago,
                    active_ms=3_000,
                ),
            ]
        )
        await session.commit()
        service = SiteAnalyticsService(session, test_context["settings"])
        await service.close_stale_visits()
        await session.commit()
        strip = {item["path"]: item for item in await service.recent_visits()}

    assert strip["/"]["reading"] is True
    assert strip["/pricing"]["reading"] is False
