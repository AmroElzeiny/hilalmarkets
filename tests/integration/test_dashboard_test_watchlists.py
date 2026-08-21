"""The redesigned Watchlists page, through the real application.

Two kinds of check, and the second is the point of the whole page:

* the page renders for a person with lists and for a person with none;
* **every word a beginner reads is a word a beginner knows.** The page it replaces
  said "Eligible asset scopes", "0s scan latency" and "43/100" to somebody who had
  never run a market check. Those are tested for by name, so they cannot come back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_market_monitor.db.models.enums import ScanJobStatus
from ai_market_monitor.engine.models import ensure_aware
from ai_market_monitor.services.monitor_scan_state import (
    CHECK_FINISHED_STATUSES,
    CHECK_IN_FLIGHT_STATUSES,
    NO_SCANS,
    MonitorScanState,
)
from ai_market_monitor.services.product_language import (
    how_long_ago,
    watchlist_presentation,
)
from tests.integration.test_dashboard_web import _signup_and_verify

PAGE = "/dashboard/monitors"


async def test_the_page_renders_for_somebody_with_no_lists(test_context):
    await _signup_and_verify(test_context, email="wl-empty@example.com")
    page = await test_context["client"].get(PAGE)
    assert page.status_code == 200
    body = page.text
    assert "Nothing is being watched yet" in body
    # One way to make one, and it is the canvas. The assistant route was offered beside
    # it and is gone: authoring never requires the assistant, and two doors to the same
    # room made a beginner choose before they knew what either one was.
    assert "Draw it on the canvas" in body
    assert "Answer a few questions" not in body


async def test_the_page_says_so_when_the_platform_is_not_checking_the_market(test_context):
    """The switch that stops every monitor is said out loud, on the page itself.

    Live scanning is off in this deployment, as it is in the test settings. Nothing said
    so anywhere: the page showed "Not looked yet" and left a person waiting on a first
    check that no scheduled job was ever going to create.
    """

    await _signup_and_verify(test_context, email="wl-not-checking@example.com")
    body = (await test_context["client"].get(PAGE)).text

    assert "We are not checking the market right now." in body
    assert "not something you did" in body
    # And never the name of the switch that did it.
    assert "SCANNING_ENABLED" not in body


async def test_home_says_so_too_rather_than_saying_a_monitor_is_watching(test_context):
    """Home's band and the Monitors banner are one sentence from one owner."""

    await _signup_and_verify(test_context, email="wl-home-checking@example.com")
    body = (await test_context["client"].get("/home")).text

    assert "We are not checking the market right now." in body
    # The band is the element that answers "is anything happening right now", so the
    # claim is checked there rather than anywhere on the page — other text says true
    # things about watching, such as what a screening change means for a coin.
    band = body.split('class="m-now"', 1)[1].split("</section>", 1)[0]
    for claim in ("is watching", "are watching"):
        assert claim not in band, claim


async def test_publishing_does_not_say_the_market_is_being_checked(test_context):
    """The sentence somebody reads at the moment they switch a monitor on.

    It said "It is checking the market now" while nothing was checking, and the card one
    screen later said "Not looked yet". This is where the wrong impression started.
    """

    await _signup_and_verify(test_context, email="wl-published@example.com")
    body = (await test_context["client"].get(f"{PAGE}?message=monitor_published")).text

    assert "It is checking the market now" not in body
    assert "not checking the market right now" in body
    assert "starts on its own as soon as we are" in body


async def test_the_old_address_still_reaches_the_page(test_context):
    """`/dashboard/watchlists` is written into sent email and saved bookmarks."""

    await _signup_and_verify(test_context, email="wl-moved@example.com")
    moved = await test_context["client"].get(
        "/dashboard/watchlists", follow_redirects=False
    )
    assert moved.status_code == 308
    assert moved.headers["location"] == PAGE


async def test_the_page_needs_a_signed_in_person(test_context):
    page = await test_context["client"].get(PAGE, follow_redirects=False)
    assert page.status_code in {302, 303, 307, 401}


#: Every word the old page showed a beginner that a beginner does not know.
#:
#: Kept as one list because they are one failure: a page written from the inside out.
BANNED_WORDS = [
    "Eligible asset scopes",
    "scan latency",
    "Technical health",
    "bottleneck",
    "Bottleneck",
    "blocker",
    "Top blocker",
    "immutable",
    "screened universe",
    "delivery policy",
    "approved version",
    "health evidence",
    "Policy unavailable",
]


@pytest.mark.parametrize("word", BANNED_WORDS, ids=lambda item: item[:24])
async def test_the_page_never_uses_a_word_from_inside_the_machine(test_context, word):
    await _signup_and_verify(test_context, email=f"wl-words-{abs(hash(word)) % 9999}@e.com")
    page = await test_context["client"].get(PAGE)
    assert page.status_code == 200
    assert word not in page.text, f"{word!r} reached a beginner"


async def test_the_page_says_what_a_watchlist_is_before_anything_else(test_context):
    """Rule G6. Somebody who has never seen this page should not have to guess."""
    await _signup_and_verify(test_context, email="wl-explains@example.com")
    page = await test_context["client"].get(PAGE)
    assert "checks the market for you" in page.text


# --------------------------------------------------------------------------------
# The plain words themselves, tested where they are decided.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "approved", "expected"),
    [
        ("active", False, "Watching"),
        ("active", True, "Watching"),
        ("paused", False, "Paused"),
        ("paused", True, "Paused"),
        ("draft", False, "Not finished"),
        # A draft with an approved version behind it is watching the market. "Draft"
        # describes how it was made, not what it is doing, and the live page showed
        # that word to customers.
        ("draft", True, "Watching"),
        ("archived", False, "Put away"),
        ("something_new", False, "Not finished"),
    ],
)
def test_a_watchlist_status_reads_as_something_a_person_would_say(
    status: str, approved: bool, expected: str
):
    presentation = watchlist_presentation(status, has_approved_version=approved)
    assert presentation.label == expected
    # And it never stops at the label: every state says what it means.
    assert presentation.explanation.endswith("."), presentation.explanation
    assert len(presentation.explanation) > 20


NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (None, "Not yet"),
        (NOW, "Just now"),
        (NOW - timedelta(seconds=30), "Just now"),
        (NOW - timedelta(minutes=5), "5 minutes ago"),
        (NOW - timedelta(hours=1), "1 hour ago"),
        (NOW - timedelta(hours=3), "3 hours ago"),
        (NOW - timedelta(days=1), "1 day ago"),
        (NOW - timedelta(days=3), "3 days ago"),
        (NOW - timedelta(days=10), "1 week ago"),
        (NOW - timedelta(days=60), "2 months ago"),
        (NOW - timedelta(days=800), "2 years ago"),
        # A clock that is slightly ahead must not produce "in -1 minutes".
        (NOW + timedelta(minutes=5), "Just now"),
    ],
)
def test_a_time_is_said_the_way_a_person_says_it(moment, expected: str):
    assert how_long_ago(moment, now=NOW) == expected


def test_a_naive_time_is_not_read_as_a_different_hour(test_context):
    """Times arrive from the database without a timezone attached. Reading one as local
    would report a check from an hour ago as hours old, or as being in the future."""

    del test_context
    naive = NOW.replace(tzinfo=None) - timedelta(hours=2)
    assert how_long_ago(naive, now=NOW) == "2 hours ago"


#: The shape `edge_health` really returns: a plain dictionary, not an object.
#:
#: These tests used a stand-in object with attributes, and the card read the payload
#: with `getattr`. `getattr` never finds a dictionary key, so in production the score
#: read 0 for every monitor and the issue sentence read nothing — while these tests, and
#: only these tests, saw the number they had put there. A fake of the wrong shape is how
#: a reader stays broken with its tests green.
_HEALTH_HIGH = {"score": 95, "main_issue": "x", "main_issue_component": "Data coverage"}
_HEALTH_LOW = {"score": 0, "main_issue": "x", "main_issue_component": "Data coverage"}


def _never_finished_card(scan_state: object) -> dict:
    """One monitor whose most recent check, whatever state it is in, never finished."""

    class _Strategy:
        id = "11111111-1111-1111-1111-111111111111"
        name = "A list that has never run"
        description = None
        active_version_id = None

        class status:  # noqa: N801 - a stand-in for the stored enum
            value = "draft"

    return {
        "strategy": _Strategy(),
        # A score high enough that reading it would produce "Working well" — so a test
        # that passes here cannot be passing because the score happened to be zero.
        "health": _HEALTH_HIGH,
        "scan_state": scan_state,
        "methodology": None,
        "main_bottleneck": None,
        "eligible_asset_count": 0,
        "pending_repair": None,
    }


def _unfinished(status: ScanJobStatus | None) -> MonitorScanState:
    """The scanning state of a monitor that has never completed a check."""

    if status is None:
        return NO_SCANS
    row = type("J", (), {"status": status, "completed_at": None, "scheduled_for": None})()
    return MonitorScanState(
        last_completed=None,
        in_flight=row if status in CHECK_IN_FLIGHT_STATUSES else None,
        latest=row,
    )


#: Every shape a most-recent check can have while never having produced a reading.
#:
#: `None` is only the easiest one. A queued, running, failed or canceled job is a row
#: that exists without a finished check behind it, and a reader that asks "is there a
#: row?" instead of "did a check finish?" answers yes for all of them.
_NO_FINISHED_CHECK = [
    pytest.param(_unfinished(None), id="no-job-at-all"),
    *(
        pytest.param(_unfinished(status), id=status.value)
        for status in ScanJobStatus
        if status not in CHECK_FINISHED_STATUSES
    ),
]


@pytest.mark.parametrize("scan_state", _NO_FINISHED_CHECK)
@pytest.mark.parametrize("scanning_enabled", [True, False])
def test_a_list_that_never_finished_a_check_reports_nothing_rather_than_not_yet(
    scan_state, scanning_enabled
):
    """`last_checked` is empty until a check *finished*, never the string "Not yet".

    `how_long_ago` answers a missing moment with a display string, and a string is
    truthy. Any page guarding on this field instead of on the exact moment beside it
    then renders "Looked Not yet". One page guarded correctly and the next one did not,
    so the trap is removed at the source and held shut here.

    The rule is about a *finished* check, not about a job row. Guarding on the row let a
    queued or failed first check render "Looked Not yet" on the front page, and scored a
    monitor that had never produced a single reading — the exact number this page exists
    to stop showing.
    """

    from ai_market_monitor.api.routers.dashboard_test import _watchlist_view

    view = _watchlist_view(
        _never_finished_card(scan_state), scanning_enabled=scanning_enabled
    )

    assert view["last_checked"] is None
    assert view["last_checked_exact"] is None
    # And the state it is really in is still said in words, never as a score.
    assert view["working"]["label"] == "Not looked yet"
    assert "95" not in view["working"]["detail"]
    # Nothing is waiting to be fixed, so the card offers nothing about a fix.
    assert view["repair_id"] is None


@pytest.mark.parametrize("scan_state", _NO_FINISHED_CHECK)
def test_a_first_check_is_never_promised_while_nothing_is_checking(scan_state):
    """"It has not checked for the first time" reads as *soon*, and soon can be false.

    While live scanning is switched off, no first check is coming for any monitor of any
    person. Saying the first-check sentence anyway is how somebody waited on work that
    nothing was ever going to run.
    """

    from ai_market_monitor.api.routers.dashboard_test import _watchlist_view

    off = _watchlist_view(_never_finished_card(scan_state), scanning_enabled=False)
    assert off["working"]["detail"] == (
        "Hilal Markets is not checking the market at the moment. "
        "Nothing is wrong with this list."
    )

    on = _watchlist_view(_never_finished_card(scan_state), scanning_enabled=True)
    assert "not checking the market at the moment" not in on["working"]["detail"]


def test_a_waiting_fix_is_named_so_the_card_can_offer_it():
    """A corrected copy of a monitor is offered on the monitor it belongs to.

    The page knew a fix existed — `needs_repair` — and had no way to name it, so the two
    buttons that act on one lived only in a section of the setup-chat page that is marked
    hidden. The Telegram message telling somebody a fix was ready opened that page, where
    no fix appeared anywhere.
    """

    from uuid import uuid4

    from ai_market_monitor.api.routers.dashboard_test import _watchlist_view

    repair_id = uuid4()

    class _Strategy:
        id = "22222222-2222-2222-2222-222222222222"
        name = "A monitor with a fix waiting"
        description = None
        active_version_id = None

        class status:  # noqa: N801 - a stand-in for the stored enum
            value = "active"

    view = _watchlist_view(
        {
            "strategy": _Strategy(),
            "health": _HEALTH_LOW,
            "scan_state": NO_SCANS,
            "methodology": None,
            "main_bottleneck": None,
            "eligible_asset_count": 0,
            "pending_repair": type("R", (), {"id": repair_id})(),
        },
        scanning_enabled=True,
    )

    assert view["needs_repair"] is True
    assert view["repair_id"] == str(repair_id)


def test_the_monitors_page_offers_both_answers_to_a_waiting_fix():
    """Reading the fix and throwing it away are both on the card, and both ask first."""

    from pathlib import Path

    markup = (
        Path(__file__).resolve().parents[2]
        / "src/ai_market_monitor/templates/hilal/dashboard_test/watchlists.html"
    ).read_text(encoding="utf-8")

    assert "/prepare-repair" in markup
    assert "/discard-repair" in markup
    # Both go through the page's own question, never the browser's unstyled confirm box.
    assert 'data-w-ask="repair"' in markup
    assert 'data-w-ask="repair_discard"' in markup

    questions = (
        Path(__file__).resolve().parents[2]
        / "src/ai_market_monitor/static/hm-watchlists-test.js"
    ).read_text(encoding="utf-8")
    assert "repair:" in questions
    assert "repair_discard:" in questions


def test_change_it_opens_the_canvas_on_that_monitor():
    """"Change it" opened the older assistant page. That page is gone.

    One page authors a monitor now — the canvas — and the same page changes one, so this
    is the address the card carries. The link is found by the marker rather than by the
    address, because the popup that explains what is holding a monitor back offers the
    same way out and used to find it by matching on `/builder`.
    """

    from pathlib import Path

    from ai_market_monitor.api.routers.dashboard_test import _watchlist_view
    from ai_market_monitor.core.dashboard_paths import monitor_edit_path

    class _Strategy:
        id = "33333333-3333-3333-3333-333333333333"
        name = "A monitor to change"
        description = None
        active_version_id = None

        class status:  # noqa: N801 - a stand-in for the stored enum
            value = "active"

    view = _watchlist_view(
        {
            "strategy": _Strategy(),
            "health": _HEALTH_LOW,
            "scan_state": NO_SCANS,
            "methodology": None,
            "main_bottleneck": None,
            "eligible_asset_count": 0,
            "pending_repair": None,
        },
        scanning_enabled=True,
    )

    assert view["edit_url"] == monitor_edit_path(_Strategy.id)
    assert "/builder" not in view["edit_url"]

    root = Path(__file__).resolve().parents[2] / "src/ai_market_monitor"
    markup = (root / "templates/hilal/dashboard_test/watchlists.html").read_text(
        encoding="utf-8"
    )
    assert 'href="{{ item.edit_url }}" data-w-edit' in markup

    questions = (root / "static/hm-watchlists-test.js").read_text(encoding="utf-8")
    assert "[data-w-edit]" in questions
    assert '/builder"' not in questions


# --------------------------------------------------------------------------------
# Through the real database, because the bug was in the query, not in the words.
# --------------------------------------------------------------------------------


async def _card_for(session, strategy):
    """The Monitors card the product would build for this monitor, right now."""

    from ai_market_monitor.api.routers.dashboard import _monitor_cards_context
    from ai_market_monitor.api.routers.dashboard_test import _watchlist_view
    from ai_market_monitor.db.models import User

    user = await session.get(User, strategy.user_id)
    cards = await _monitor_cards_context(session, user)
    card = next(item for item in cards if item["strategy"].id == strategy.id)
    return card, _watchlist_view(card, scanning_enabled=True)


def _scan_job(version_id, *, status, created_at, completed_at, key):
    from ai_market_monitor.db.models import ScanJob

    return ScanJob(
        strategy_version_id=version_id,
        idempotency_key=key,
        job_type="live",
        status=status,
        scheduled_for=created_at,
        created_at=created_at,
        completed_at=completed_at,
        symbols_planned=119,
        symbols_scanned=119,
    )


async def test_a_monitor_reports_the_finished_check_while_the_next_one_runs(test_context):
    """The shape every live monitor is in, almost all of the time.

    The scheduler queues the next check every interval, so the newest row is nearly
    always the one still running. The page read that newest row, found no finishing time
    on it and said "Not looked yet" — for hours, for days, about a monitor that had
    finished a check every few minutes the whole time. This is that exact database.
    """

    from tests.integration.test_scanner_pipeline import _active_strategy

    async with test_context["session_factory"]() as session:
        strategy, version = await _active_strategy(session)
        finished_at = datetime.now(UTC) - timedelta(minutes=6)
        session.add(
            _scan_job(
                version.id,
                status=ScanJobStatus.SUCCEEDED,
                created_at=finished_at - timedelta(minutes=3),
                completed_at=finished_at,
                key="finished-check",
            )
        )
        session.add(
            _scan_job(
                version.id,
                status=ScanJobStatus.RUNNING,
                created_at=datetime.now(UTC) - timedelta(minutes=1),
                completed_at=None,
                key="check-still-running",
            )
        )
        await session.flush()

        card, view = await _card_for(session, strategy)

        # `ensure_aware` because SQLite gives the moment back without its timezone
        # while PostgreSQL keeps it. The card reads whichever the database hands it.
        assert ensure_aware(view["last_checked_exact"]) == finished_at
        assert view["last_checked"] == how_long_ago(finished_at)
        assert view["working"]["label"] != "Not looked yet"
        # And the row still running is reported as what it is, on the older page.
        assert card["last_check_label"] == "Running"
        assert card["scan_state"].is_checking_now is True


async def test_a_check_that_read_nothing_is_not_a_check(test_context):
    """`failed` and `canceled` stamp a finishing time and read no market at all.

    Counting them puts "Looked 3 minutes ago" on a monitor whose every attempt was
    thrown away — the same untrue sentence as "Not looked yet", pointing the other way.
    """

    from tests.integration.test_scanner_pipeline import _active_strategy

    for index, status in enumerate((ScanJobStatus.FAILED, ScanJobStatus.CANCELED)):
        async with test_context["session_factory"]() as session:
            strategy, version = await _active_strategy(session)
            session.add(
                _scan_job(
                    version.id,
                    status=status,
                    created_at=datetime.now(UTC) - timedelta(minutes=5),
                    completed_at=datetime.now(UTC) - timedelta(minutes=3),
                    key=f"threw-it-away-{index}",
                )
            )
            await session.flush()

            _card, view = await _card_for(session, strategy)

            assert view["last_checked_exact"] is None, status
            assert view["last_checked"] is None, status
            assert view["working"]["label"] == "Not looked yet", status


async def test_the_page_never_shows_a_beginner_the_health_payloads_own_words(test_context):
    """The card says what is wrong in the product's words, not the cockpit's.

    The payload writes for an engineer — "Average recorded latency is 653784 ms." — and
    that sentence became reachable the moment the card could read the payload at all.
    """

    from tests.integration.test_scanner_pipeline import _active_strategy

    async with test_context["session_factory"]() as session:
        strategy, version = await _active_strategy(session)
        session.add(
            _scan_job(
                version.id,
                status=ScanJobStatus.SUCCEEDED,
                created_at=datetime.now(UTC) - timedelta(minutes=4),
                completed_at=datetime.now(UTC) - timedelta(minutes=2),
                key="one-finished-check",
            )
        )
        await session.flush()

        card, view = await _card_for(session, strategy)

        detail = view["working"]["detail"]
        assert detail != card["health"]["main_issue"]
        for machine_word in ("ms.", "latency", "evaluation", "/100", "%"):
            assert machine_word not in detail.lower(), detail
        # The sentence that was printed for every monitor, including ones whose every
        # check had arrived.
        assert "not arriving" not in detail

async def test_the_rendered_page_says_when_it_last_looked(test_context):
    """The bug as it was reported: the page itself, in a browser, for hours.

    A monitor that had finished a check every few minutes showed "Not looked yet" on
    this page and "Checked Not yet" on Home. Both are asserted here on the real HTML,
    because that is where a person met the problem.
    """

    from sqlalchemy import select

    from ai_market_monitor.db.models import User
    from tests.integration.test_scanner_pipeline import _active_strategy

    await _signup_and_verify(test_context, email="wl-looked@example.com")

    async with test_context["session_factory"]() as session:
        strategy, version = await _active_strategy(session)
        # The signed-up account is the one whose page is fetched below, so the monitor
        # is put on it. `_active_strategy` makes its own user, which nobody can sign in as.
        owner = await session.scalar(
            select(User).where(User.display_name != "Scanner User")
        )
        strategy.user_id = owner.id
        finished_at = datetime.now(UTC) - timedelta(minutes=8)
        session.add(
            _scan_job(
                version.id,
                status=ScanJobStatus.SUCCEEDED,
                created_at=finished_at - timedelta(minutes=3),
                completed_at=finished_at,
                key="rendered-finished-check",
            )
        )
        session.add(
            _scan_job(
                version.id,
                status=ScanJobStatus.QUEUED,
                created_at=datetime.now(UTC),
                completed_at=None,
                key="rendered-next-check",
            )
        )
        await session.commit()

    expected = how_long_ago(finished_at)

    monitors = await test_context["client"].get(PAGE)
    assert monitors.status_code == 200
    assert expected in monitors.text
    assert "Not looked yet" not in monitors.text

    home = await test_context["client"].get("/home")
    assert home.status_code == 200
    assert expected in home.text
    assert "Not looked yet" not in home.text