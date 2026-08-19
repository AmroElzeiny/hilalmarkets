"""The redesigned Opportunities page, through the real application.

The page it replaces made three promises it could not keep at once. It showed the same
coin twice under two different words; it printed the words from inside the machine at a
beginner; and it drew "we could not read the market" exactly like "your rule failed".

Each of those is a family, not a single line, so each is tested as one:

* every word that reached a beginner is named, and none of them may come back;
* the two vocabularies for "what is this doing" resolve to **one** answer, for every
  pair, so a page can never show a coin as two different things again;
* "could not read it" is never the same answer as "not true yet", for any input.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from ai_market_monitor.api.routers.dashboard_test import (
    _changed_times,
    _merge_opportunities,
    _opportunity_from_journey,
    _opportunity_from_readiness,
)
from ai_market_monitor.db.models import CandidateReadinessSnapshot, User
from ai_market_monitor.db.models.enums import SetupLifecycleState
from ai_market_monitor.services.product_language import (
    UNKNOWN_OPPORTUNITY,
    check_presentation,
    checks_in_words,
    gap_in_words,
    how_often,
    number_in_words,
    opportunity_presentation,
    opportunity_state,
    why_no_message,
)
from tests.integration.test_dashboard_web import _signup_and_verify
from tests.unit.test_setup_observability import _seed_lifecycle, _seed_monitor

PAGE = "/dashboard/opportunities"


async def _with_one_opportunity(test_context, *, email: str) -> dict[str, str]:
    """One signed-in person with one coin their list is watching."""

    await _signup_and_verify(test_context, email=email)
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User))
        _user, strategy, version = await _seed_monitor(
            session, user=user, name="SOL Volume Watch"
        )
        _, _, scan, setup, _ = await _seed_lifecycle(session, user, strategy, version)
        now = datetime.now(UTC)
        session.add(
            CandidateReadinessSnapshot(
                user_id=user.id,
                strategy_id=strategy.id,
                strategy_version_id=version.id,
                setup_instance_id=setup.id,
                scan_result_id=scan.id,
                exchange="binance",
                symbol="SOL/USDT",
                timeframe="15m",
                direction="long",
                lifecycle_state="confirmation_pending",
                stage_rank=3,
                required_total=5,
                required_passed=4,
                optional_total=0,
                optional_passed=0,
                blocker_key="volume_ratio",
                blocker_label="Volume confirmation",
                blocker_outcome="failed",
                blocker_actual={"value": 1.27},
                blocker_required={"value": 1.5},
                blocker_distance=Decimal("0.23"),
                blocker_unit="absolute",
                most_recent_change="Volume became the final blocker.",
                last_changed_at=now,
                last_evaluated_at=now,
                data_freshness_ms=250,
                data_health="healthy",
                notification_status="not_attempted",
                condition_tree={},
                latest_values=[
                    {
                        "key": "volume_ratio",
                        "label": "Volume confirmation",
                        "role": "required_confirmation",
                        "required": True,
                        "outcome": "failed",
                        "actual": 1.27,
                        "required_value": 1.5,
                        "timeframe": "15m",
                    }
                ],
            )
        )
        await session.commit()
        return {"strategy_id": str(strategy.id), "setup_id": str(setup.id)}


# ── The page itself ──────────────────────────────────────────────────────────


async def test_the_page_needs_a_signed_in_person(test_context):
    page = await test_context["client"].get(PAGE, follow_redirects=False)
    assert page.status_code in {302, 303, 307, 401}


async def test_somebody_with_nothing_found_is_told_why_and_given_a_way_on(test_context):
    """Rule G5. The live page said "Activate a validated Watchlist and allow its first
    market evaluation to complete", which is an instruction in a language nobody speaks."""

    await _signup_and_verify(test_context, email="opp-empty@example.com")
    page = await test_context["client"].get(PAGE)
    assert page.status_code == 200
    body = page.text
    assert "Nothing has been found yet" in body
    assert "A monitor has to be watching first" in body
    assert "/dashboard/monitors" in body


async def test_a_coin_appears_once_with_its_state_and_its_count(test_context):
    await _with_one_opportunity(test_context, email="opp-one@example.com")
    page = await test_context["client"].get(PAGE)
    assert page.status_code == 200
    body = page.text
    # Once. The live page drew the same coin as a radar row *and* as a journey card.
    assert body.count('data-o-card') == 1
    assert "SOL/USDT" in body
    assert "Nearly there" in body
    assert "4 of 5 things you asked for are true" in body
    # The numbers carry their meaning, not three labels in a row.
    assert "You asked for 1.5. Right now it is 1.27." in body


async def test_the_page_says_where_each_coin_came_from(test_context):
    await _with_one_opportunity(test_context, email="opp-source@example.com")
    body = (await test_context["client"].get(PAGE)).text
    assert "SOL Volume Watch" in body
    assert "Every 15 minutes" in body


async def test_asking_for_a_watchlist_that_is_not_yours_is_refused(test_context):
    await _with_one_opportunity(test_context, email="opp-other@example.com")
    other = await test_context["client"].get(f"{PAGE}?monitor={uuid4()}")
    assert other.status_code == 404


async def test_a_watchlist_that_is_not_a_watchlist_is_refused(test_context):
    await _signup_and_verify(test_context, email="opp-bad-id@example.com")
    page = await test_context["client"].get(f"{PAGE}?monitor=not-a-real-id")
    assert page.status_code == 422


async def test_only_the_chosen_watchlist_is_shown(test_context):
    seeded = await _with_one_opportunity(test_context, email="opp-filter@example.com")
    page = await test_context["client"].get(f"{PAGE}?monitor={seeded['strategy_id']}")
    assert page.status_code == 200
    assert page.text.count("data-o-card") == 1


async def test_choosing_a_list_that_found_nothing_still_offers_the_way_back(test_context):
    """Somebody who picks one monitor and finds it empty is not somebody with none.

    Hiding the chooser inside "we have something to show" left them on a page with no
    way back to everything, and offered them "make your first monitor" when they already
    had one."""

    await _signup_and_verify(test_context, email="opp-empty-pick@example.com")
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User))
        _user, strategy, _version = await _seed_monitor(
            session, user=user, name="Quiet Watch"
        )
        await session.commit()
        strategy_id = str(strategy.id)

    page = await test_context["client"].get(f"{PAGE}?monitor={strategy_id}")
    assert page.status_code == 200
    body = page.text
    assert "Quiet Watch has not found anything yet" in body
    assert "Show everything again" in body
    # And it does not tell somebody with a monitor to go and make their first one.
    assert "Make your first monitor" not in body


# ── Every word the live page showed a beginner ───────────────────────────────
#
# One list because they are one failure: a page written from the inside out. The check
# runs against a page **with data on it**, because most of these words only appeared
# once there was something to describe.

BANNED_WORDS = [
    "near miss",
    "Near miss",
    "provider_data_error",
    "Provider/data error",
    "lifecycle",
    "Lifecycle",
    "blocker",
    "Blocker",
    "Opportunity journeys",
    "Setup evidence",
    "Confirmation pending",
    "confirmation_pending",
    "completion score",
    "Readiness",
    "readiness",
    "Current:",
    "Required:",
    "Distance:",
    "required rules passed",
    "Condition bottlenecks",
    "Technical health",
    "immutable",
    "deterministic",
    "Evidence and Activity",
]


@pytest.mark.parametrize("word", BANNED_WORDS, ids=lambda item: item[:24])
async def test_the_page_never_uses_a_word_from_inside_the_machine(test_context, word):
    await _with_one_opportunity(
        test_context, email=f"opp-words-{abs(hash(word)) % 9999}@e.com"
    )
    page = await test_context["client"].get(PAGE)
    assert page.status_code == 200
    assert word not in page.text, f"{word!r} reached a beginner"


async def test_the_page_has_one_name_everywhere(test_context):
    """Finding 2. The live page called itself three things at once: "Opportunities &
    Evidence" in the menu, "What is closest right now?" as its heading, and "Evidence
    and Activity" in the browser tab."""

    await _with_one_opportunity(test_context, email="opp-name@example.com")
    body = (await test_context["client"].get(PAGE)).text
    assert "<title>Opportunities | Hilal Markets</title>" in body
    assert "<h1>Opportunities</h1>" in body
    assert "What is closest right now?" not in body


async def test_the_page_never_reads_as_advice(test_context):
    await _with_one_opportunity(test_context, email="opp-advice@example.com")
    body = (await test_context["client"].get(PAGE)).text
    assert "never buys, sells, or tells you what to buy" in body
    assert "Nothing here is advice" in body


# ── One answer, from either vocabulary ───────────────────────────────────────


@pytest.mark.parametrize(
    ("readiness_word", "recorded_state"),
    [
        ("not_started", SetupLifecycleState.DETECTED),
        ("forming", SetupLifecycleState.FORMING),
        ("near_miss", SetupLifecycleState.NEAR_CONFIRMATION),
        ("confirmation_pending", SetupLifecycleState.NEAR_CONFIRMATION),
        ("confirmed", SetupLifecycleState.CONFIRMED),
        ("invalidated", SetupLifecycleState.INVALIDATED),
        ("expired", SetupLifecycleState.EXPIRED),
        ("provider_data_error", SetupLifecycleState.DATA_UNAVAILABLE),
    ],
)
def test_the_two_vocabularies_give_one_answer(readiness_word, recorded_state):
    """The whole point of the redesign, as a rule rather than an example.

    One record writes "confirmation_pending" and the other writes "near_confirmation"
    for the same coin at the same moment. The live page showed both and a person could
    not tell whether they were looking at one thing or two."""

    assert opportunity_state(readiness_word) is opportunity_state(recorded_state)
    assert opportunity_presentation(readiness_word) == opportunity_presentation(
        recorded_state
    )


@pytest.mark.parametrize("state", list(SetupLifecycleState))
def test_every_recorded_state_reads_as_something_a_person_would_say(state):
    presentation = opportunity_presentation(state)
    assert presentation is not UNKNOWN_OPPORTUNITY, state
    assert presentation.label and presentation.label[0].isupper()
    assert presentation.meaning.endswith(".")
    assert len(presentation.meaning) > 20
    assert presentation.kind in {"ready", "close", "forming", "unchecked", "ended"}
    assert presentation.semantic_status in {
        "success",
        "warning",
        "danger",
        "information",
        "neutral",
    }


@pytest.mark.parametrize("value", ["", None, "something_new", "42", "  "])
def test_a_state_we_do_not_know_is_never_guessed(value):
    """Fail closed. Telling somebody "still forming" about a word we did not recognise
    is telling them something we do not know to be true."""

    assert opportunity_state(value) is None
    assert opportunity_presentation(value) is UNKNOWN_OPPORTUNITY


# ── A gap and a failure are different facts ──────────────────────────────────


@pytest.mark.parametrize("outcome", ["unavailable", "error", "UNAVAILABLE", " Error "])
def test_a_reading_we_could_not_take_is_never_called_a_failure(outcome):
    assert check_presentation(outcome).label == "We could not read it"
    assert check_presentation(outcome).label != check_presentation("failed").label


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("passed", "This is true"),
        ("failed", "Not true yet"),
        ("pending", "Still waiting"),
        ("unavailable", "We could not read it"),
        ("error", "We could not read it"),
        (None, "We could not read it"),
        ("something_else", "We could not read it"),
    ],
)
def test_every_reading_has_words_a_beginner_knows(outcome, expected):
    assert check_presentation(outcome).label == expected


@pytest.mark.parametrize("outcome", ["unavailable", "error"])
def test_a_number_we_could_not_read_is_said_so_rather_than_compared(outcome):
    sentence = gap_in_words(outcome=outcome, saw=None, wanted=1.5, distance=None)
    assert sentence == "We could not read this number, so there is nothing to compare yet."


@pytest.mark.parametrize(
    ("saw", "wanted", "distance", "expected"),
    [
        (1.27, 1.5, 0.23, "You asked for 1.5. Right now it is 1.27. That is 0.23 away."),
        (1.5, 1.5, 0, "You asked for 1.5. Right now it is 1.5."),
        (1.27, 1.5, None, "You asked for 1.5. Right now it is 1.27."),
        # Nothing is invented from half a pair.
        (None, 1.5, 0.23, ""),
        (1.27, None, 0.23, ""),
        (None, None, None, ""),
    ],
)
def test_a_gap_explains_its_numbers_without_inventing_a_direction(
    saw, wanted, distance, expected
):
    """The distance is stored without a direction. "It needs to go higher" would be a
    guess about somebody's own rule, so no word here ever says which way."""

    sentence = gap_in_words(outcome="failed", saw=saw, wanted=wanted, distance=distance)
    assert sentence == expected
    for direction in ("higher", "lower", "above", "below", "more", "less", "up", "down"):
        assert direction not in sentence.lower()


# ── The plain words themselves ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("timeframe", "expected"),
    [
        ("1m", "Every minute"),
        ("5m", "Every 5 minutes"),
        ("15m", "Every 15 minutes"),
        ("30m", "Every 30 minutes"),
        ("1h", "Every hour"),
        ("4h", "Every 4 hours"),
        ("12h", "Every 12 hours"),
        ("1d", "Every day"),
        ("3d", "Every 3 days"),
        # Nothing invented for something we cannot read.
        ("", ""),
        (None, ""),
        ("banana", ""),
        ("15x", ""),
    ],
)
def test_how_often_a_list_looks_is_said_in_words(timeframe, expected):
    assert how_often(timeframe) == expected


@pytest.mark.parametrize(
    ("passed", "total", "expected"),
    [
        (0, 5, "0 of 5 things you asked for are true"),
        (1, 5, "1 of 5 things you asked for are true"),
        (5, 5, "5 of 5 things you asked for are true"),
        (1, 1, "1 of 1 thing you asked for is true"),
        (0, 1, "0 of 1 thing you asked for is true"),
        (0, 0, "Nothing to check yet"),
        # A count that outran its total is clamped rather than printed.
        (9, 5, "5 of 5 things you asked for are true"),
        (-1, 5, "0 of 5 things you asked for are true"),
    ],
)
def test_progress_is_counted_rather_than_scored(passed, total, expected):
    assert checks_in_words(passed, total) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.5, "1.5"),
        (1.27, "1.27"),
        (0.23, "0.23"),
        (100, "100"),
        (0, "0"),
        (Decimal("1.5000"), "1.5"),
        (1.234567, "1.2346"),
        (True, None),
        (False, None),
        (None, None),
        ("1.5", None),
    ],
)
def test_a_market_number_is_written_one_way_everywhere(value, expected):
    assert number_in_words(value) == expected


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, None),
        (-1, None),
        (1, "Changed once since we found it"),
        (2, "Changed 2 times since we found it"),
        (11, "Changed 11 times since we found it"),
    ],
)
def test_how_many_times_it_moved_gets_its_plural_right(count, expected):
    """Finding 8. The live page printed "1 lifecycle events" on screen."""

    assert _changed_times(count) == expected


@pytest.mark.parametrize(
    "category",
    [
        "strategy_condition_failure",
        "data_provider_issue",
        "notification_delivery_failure",
        "cooldown_or_exclusion",
        "notification_not_attempted",
        "completed_without_alert",
        "alert_delivered",
    ],
)
def test_every_reason_nobody_was_told_is_explained_in_words(category):
    answer = why_no_message(category)
    assert answer.headline and not answer.headline.endswith(".")
    assert answer.meaning.endswith(".")
    assert answer.what_to_do.endswith(".")
    for word in ("lifecycle", "suppressed", "delivery diagnostics", "provider", "setup"):
        assert word not in answer.meaning.lower(), category


@pytest.mark.parametrize("category", [None, "", "something_new"])
def test_a_reason_we_do_not_recognise_is_never_invented(category):
    assert why_no_message(category).headline == "We cannot say why"


# ── One card per coin ────────────────────────────────────────────────────────


def _readiness_row(setup_id: str | None, symbol: str = "SOL/USDT") -> dict:
    return {
        "id": f"snapshot-{symbol}",
        "setup_id": setup_id,
        "monitor_id": "m1",
        "monitor_name": "SOL Volume Watch",
        "symbol": symbol,
        "exchange": "binance",
        "timeframe": "15m",
        "state": "confirmation_pending",
        "required": {"passed": 4, "total": 5},
        "blocker": {
            "label": "Volume confirmation",
            "outcome": "failed",
            "actual": 1.27,
            "required": 1.5,
            "distance": 0.23,
        },
        "last_changed_at": datetime.now(UTC),
        "data_health": "healthy",
        "latest_values": [],
    }


def _journey_card(setup_id: str, symbol: str = "SOL/USDT") -> dict:
    return {
        "id": setup_id,
        "symbol": symbol,
        "asset_symbol": symbol.partition("/")[0],
        "logo_module_url": "https://example.invalid/logo.js",
        "logo_url": None,
        "exchange": "binance",
        "timeframe": "15m",
        "strategy_name": "SOL Volume Watch",
        "state": "near_confirmation",
        "completion_score": 80.0,
        "last_evaluated_at": datetime.now(UTC),
        "completed_events": [{"label": "Detected"}],
        "passed_conditions": [],
        "monitoring_conditions": [],
        "latest_alert_id": None,
        "show_why_no_alert": True,
    }


#: No stored coin records for these cards.
#:
#: Passed explicitly rather than defaulted, and the argument is required in the code for
#: the same reason: a caller that could leave it out is a caller that can silently ship a
#: page where no coin has a logo. That is exactly what the Opportunities page did.
NO_STORED_COINS: dict = {}


def test_the_same_coin_becomes_one_card_not_two():
    setup_id = str(uuid4())
    merged = _merge_opportunities(
        [_opportunity_from_readiness(_readiness_row(setup_id), NO_STORED_COINS)],
        [_opportunity_from_journey(_journey_card(setup_id))],
    )
    assert len(merged) == 1
    card = merged[0]
    # The count comes from the readiness record and the history from the recorded one.
    assert card["checks"]["sentence"] == "4 of 5 things you asked for are true"
    assert card["changed_times"] == "Changed once since we found it"
    assert card["logo_module_url"] == "https://example.invalid/logo.js"


def test_a_recorded_opportunity_with_no_readiness_row_still_gets_a_card():
    """A short-lived row being tidied away must never make an opportunity disappear."""

    merged = _merge_opportunities(
        [_opportunity_from_readiness(_readiness_row(None, symbol="ETH/USDT"), NO_STORED_COINS)],
        [_opportunity_from_journey(_journey_card(str(uuid4())))],
    )
    assert len(merged) == 2
    assert {card["symbol"] for card in merged} == {"ETH/USDT", "SOL/USDT"}


@pytest.mark.parametrize(
    ("passed", "total", "shown", "percent"),
    [
        (4, 5, 4, 80),
        (0, 5, 0, 0),
        (5, 5, 5, 100),
        # A stored count that outran its own total must not draw a bar past the end of
        # its track, nor tell a screen reader "6 of 5".
        (6, 5, 5, 100),
        (-2, 5, 0, 0),
    ],
)
def test_the_bar_can_never_draw_past_the_end_of_its_track(passed, total, shown, percent):
    row = _readiness_row(None)
    row["required"] = {"passed": passed, "total": total}
    checks = _opportunity_from_readiness(row, NO_STORED_COINS)["checks"]
    assert checks["passed"] == shown
    assert checks["percent"] == percent
    assert checks["passed"] <= checks["total"]


def test_a_coin_we_could_not_check_is_never_drawn_as_a_failure():
    """Finding 6. "0 of 5" with an empty bar reads as "your rules failed" when the
    truth is that nobody ever looked."""

    row = _readiness_row(None)
    row["state"] = "provider_data_error"
    row["required"] = {"passed": 0, "total": 5}
    row["data_health"] = "error"
    card = _opportunity_from_readiness(row, NO_STORED_COINS)
    assert card["state"]["kind"] == "unchecked"
    assert card["state"]["label"] == "We could not check it"
