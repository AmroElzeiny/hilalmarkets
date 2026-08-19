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


def test_a_list_that_never_ran_reports_nothing_rather_than_the_words_not_yet():
    """`last_checked` is empty when there was no check, not the string "Not yet".

    `how_long_ago` answers a missing moment with a display string, and a string is
    truthy. Any page guarding on this field instead of on the exact moment beside it
    then renders "Looked Not yet". One page guarded correctly and the next one did not,
    so the trap is removed at the source and held shut here.
    """

    from ai_market_monitor.api.routers.dashboard_test import _watchlist_view

    class _Strategy:
        id = "11111111-1111-1111-1111-111111111111"
        name = "A list that has never run"
        description = None
        active_version_id = None

        class status:  # noqa: N801 - a stand-in for the stored enum
            value = "draft"

    view = _watchlist_view(
        {
            "strategy": _Strategy(),
            "health": type("H", (), {"score": 0, "main_issue": None})(),
            "latest_scan": None,
            "methodology": None,
            "main_bottleneck": None,
            "eligible_asset_count": 0,
            "pending_repair": None,
        }
    )

    assert view["last_checked"] is None
    assert view["last_checked_exact"] is None
    # And the state it is really in is still said in words.
    assert view["working"]["label"] == "Not looked yet"
    # Nothing is waiting to be fixed, so the card offers nothing about a fix.
    assert view["repair_id"] is None


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
            "health": type("H", (), {"score": 0, "main_issue": None})(),
            "latest_scan": None,
            "methodology": None,
            "main_bottleneck": None,
            "eligible_asset_count": 0,
            "pending_repair": type("R", (), {"id": repair_id})(),
        }
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
