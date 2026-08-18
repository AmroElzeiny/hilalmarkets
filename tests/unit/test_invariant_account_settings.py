"""The rules `AccountSettingsService` holds, asserted as rules rather than as cases.

Every closed set of choices on the Settings page has exactly one owner, and every value
somebody can silence is compared in exactly one place. These tests parametrise across
the whole family, so a fix that only helps one channel, one sound or one coin fails.

The rules these back are in `docs/dashboard-test-account-rules.md`, sections H and J5.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.services import account_settings
from ai_market_monitor.services.account_settings import (
    ALERT_DAYS,
    ALERT_HOURS,
    CONFIRMED_SOUNDS,
    EVIDENCE_TIMING,
    FORMING_SOUNDS,
    MARKET_PROVIDERS,
    MUTED_SYMBOL_LIMIT,
    SUPPORTED_TIMEZONES,
    clean_muted_symbols,
)
from ai_market_monitor.services.notification_preferences import symbol_is_muted

# ── One owner for every vocabulary ──────────────────────────────────────────


def test_the_page_router_does_not_keep_its_own_copy_of_the_vocabulary():
    """The router that draws the page must import these, never redeclare them.

    The recurring fault in this codebase is two modules each holding their own idea of
    what a word means. The moment the page keeps its own day list, it can offer a day
    that the endpoint saving it would refuse.

    It used to be two routers, because it used to be two Settings pages: an older one at
    `/dashboard/settings` with a whole-form `POST`, and the redesigned one that saves as
    you go. The older page is gone, so there is one page and one router to check.
    """

    from ai_market_monitor.api.routers import dashboard_test

    assert dashboard_test.ALERT_DAYS is ALERT_DAYS
    assert dashboard_test.MARKET_PROVIDERS is MARKET_PROVIDERS
    assert dashboard_test.MUTED_SYMBOL_LIMIT is MUTED_SYMBOL_LIMIT


def test_every_way_of_saving_calls_the_same_owner():
    """Whatever writes a setting, `AccountSettingsService` decides what it may be."""

    from ai_market_monitor.api.routers import dashboard_api, dashboard_test

    assert dashboard_api.AccountSettingsService is account_settings.AccountSettingsService
    assert dashboard_test.AccountSettingsService is account_settings.AccountSettingsService

    # And there is no second way in. The older page posted a whole form to a handler of
    # its own; that handler and its page were removed together, so a control cannot be
    # saved by a route that does not know today's rules.
    from ai_market_monitor.api.routers import dashboard

    assert not hasattr(dashboard, "settings_submit")
    assert not hasattr(dashboard, "settings_page")


@pytest.mark.parametrize(
    "vocabulary",
    [
        ALERT_DAYS,
        ALERT_HOURS,
        MARKET_PROVIDERS,
        CONFIRMED_SOUNDS,
        FORMING_SOUNDS,
        EVIDENCE_TIMING,
        SUPPORTED_TIMEZONES,
    ],
)
def test_no_vocabulary_repeats_itself(vocabulary):
    """A duplicate in a closed set means two controls for one choice."""

    assert len(vocabulary) == len(set(vocabulary))


def test_every_hour_of_the_day_is_offered():
    """Twenty-four hours, every one of them, in the one shape the schedule compares."""

    assert len(ALERT_HOURS) == 24
    assert ALERT_HOURS[0] == "00:00"
    assert ALERT_HOURS[-1] == "23:00"


def test_every_day_of_the_week_is_offered_plus_the_shortcut():
    assert set(ALERT_DAYS) == {
        "Every Day",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    }


# ── Silencing a coin ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("typed", "kept"),
    [
        ("btc", "BTC"),
        ("  eth  ", "ETH"),
        ("sol/usdt", "SOL/USDT"),
        ("ADA-USDT", "ADA/USDT"),
        ("XrP", "XRP"),
    ],
)
def test_a_coin_is_stored_in_one_shape_however_it_was_typed(typed, kept):
    """Upper case, slashes not dashes. One shape, so one comparison can match it."""

    assert clean_muted_symbols([typed]) == [kept]


@pytest.mark.parametrize("junk", ["", "   ", "A" * 25])
def test_nothing_unusable_is_stored(junk):
    assert clean_muted_symbols([junk]) == []


def test_the_same_coin_twice_is_stored_once():
    assert clean_muted_symbols(["btc", "BTC", " btc "]) == ["BTC"]


def test_the_list_of_silenced_coins_is_capped():
    typed = [f"C{index}" for index in range(MUTED_SYMBOL_LIMIT + 20)]
    assert len(clean_muted_symbols(typed)) == MUTED_SYMBOL_LIMIT


@pytest.mark.parametrize(
    "pair",
    ["BTC/USDT", "BTC/USDC", "BTC/EUR", "BTC/TRY"],
)
def test_silencing_a_coin_silences_every_pair_it_trades_in(pair):
    """Somebody silencing "BTC" means Bitcoin, not one pair of it.

    Comparing the whole recorded symbol against the whole typed word is what made this
    control useless: the recorded symbol is a pair, so a person would have had to guess
    the quote currency to silence anything at all.
    """

    assert symbol_is_muted(pair, {"BTC"}) is True


def test_silencing_one_pair_silences_only_that_pair():
    """The exact-pair case still works. A stored pair is a narrower choice, not a
    broken one."""

    assert symbol_is_muted("BTC/USDT", {"BTC/USDT"}) is True
    assert symbol_is_muted("BTC/USDC", {"BTC/USDT"}) is False


@pytest.mark.parametrize("written", ["btc/usdt", "BTC-USDT", " BTC/USDT "])
def test_a_recorded_symbol_matches_however_it_was_written(written):
    assert symbol_is_muted(written, {"BTC/USDT"}) is True


@pytest.mark.parametrize("nothing", [None, set()])
def test_nothing_is_silenced_when_nothing_was_chosen(nothing):
    assert symbol_is_muted("BTC/USDT", nothing) is False


def test_an_unnamed_market_is_never_treated_as_silenced():
    """A blank symbol must not match a blank entry and swallow a real message."""

    assert symbol_is_muted("", {"BTC"}) is False


def test_a_coin_that_was_not_silenced_still_gets_through():
    assert symbol_is_muted("ETH/USDT", {"BTC", "SOL/USDT"}) is False


# ── A value nobody offered is never stored ──────────────────────────────────


@pytest.mark.parametrize(
    ("offered", "fallback"),
    [(CONFIRMED_SOUNDS, "chime"), (FORMING_SOUNDS, "pulse"), (EVIDENCE_TIMING, "immediate")],
)
def test_an_unknown_choice_falls_back_to_the_one_the_page_shows(offered, fallback):
    """Never the nearest value, never the raw one. The default the page itself
    displays, so what is stored and what is drawn cannot disagree."""

    assert account_settings._one_of("something-nobody-offered", offered, fallback) == fallback
    for value in offered:
        assert account_settings._one_of(value, offered, fallback) == value
