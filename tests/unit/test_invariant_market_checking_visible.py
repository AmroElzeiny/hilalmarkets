"""While the platform is not checking the market, no page may say that it is.

`SCANNING_ENABLED` stops every monitor of every person at once. Nothing in the product
said so. A published monitor sat on the front page under the words "Your monitor is
watching." while the card below it said "Not looked yet. This list has not checked the
market for the first time." — one page, two answers, and the true one written nowhere.
`/health/deep` answered "ok" throughout, so the state was not visible from outside the
machine either.

The rule tested here is not "fix that sentence". It is: **every surface that describes
whether the market is being watched reads the one owner, and none of them may claim
watching while the switch is off.**
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ai_market_monitor.api.routers.main_dashboard import _headline
from ai_market_monitor.services.product_language import (
    checking_message_overrides,
    first_check_words,
    market_checking_notice,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/ai_market_monitor"

#: Every shape the front page's band can be asked about. The switched-off answer has to
#: win in all of them, including the ones with something ready or close — stored evidence
#: from an earlier check does not mean the market is being watched now.
_BAND_SHAPES = [
    pytest.param(0, 0, 0, 0, id="nothing-at-all"),
    pytest.param(0, 0, 0, 3, id="lists-none-running"),
    pytest.param(0, 0, 2, 2, id="lists-running"),
    pytest.param(0, 4, 2, 2, id="something-close"),
    pytest.param(1, 4, 2, 2, id="something-ready"),
]


def test_the_switch_has_exactly_one_owner_for_its_words():
    """One notice, or `None`. Never a second opinion invented at a call site."""

    assert market_checking_notice(scanning_enabled=True) is None

    notice = market_checking_notice(scanning_enabled=False)
    assert notice is not None
    assert notice.title.endswith(".")
    # Never a word from inside the machine, and never blame pointed at the reader.
    for word in ("SCANNING_ENABLED", "scanning", "celery", "worker", "config", "env"):
        assert word.lower() not in (notice.title + " " + notice.detail).lower()
    assert "not something you did" in notice.detail


@pytest.mark.parametrize("ready,close,active,total", _BAND_SHAPES)
def test_the_front_page_never_claims_watching_while_nothing_is_checked(
    ready, close, active, total
):
    """The band says the true thing first, whatever else is stored."""

    notice = market_checking_notice(scanning_enabled=False)
    band = _headline(
        ready_count=ready,
        close_count=close,
        active_lists=active,
        total_lists=total,
        checking=notice,
    )

    assert notice is not None
    assert band["headline"] == notice.title
    assert band["detail"] == notice.detail
    # The words the band used to reach for, none of which is true right now.
    said = (band["headline"] + " " + band["detail"]).lower()
    for claim in ("is watching", "are watching", "will be told when"):
        assert claim not in said
    # It still offers somewhere to go. A band with no way out is a dead end.
    assert band["action"]["href"]


@pytest.mark.parametrize("ready,close,active,total", _BAND_SHAPES)
def test_the_band_is_unchanged_when_the_market_really_is_checked(
    ready, close, active, total
):
    """The switched-on page keeps every answer it had. A guard that changes the normal
    path is a second behaviour hiding inside a fix."""

    band = _headline(
        ready_count=ready,
        close_count=close,
        active_lists=active,
        total_lists=total,
        checking=market_checking_notice(scanning_enabled=True),
    )

    assert "not checking the market" not in band["headline"].lower()


def test_a_first_check_is_promised_only_when_one_can_happen():
    """Three reasons a list has not looked yet, and only one of them ends on its own."""

    off = first_check_words(scanning_enabled=False, check_started=False)
    off_started = first_check_words(scanning_enabled=False, check_started=True)
    assert off == off_started, "the switch decides this, not a job row"
    assert "not checking the market at the moment" in off

    running = first_check_words(scanning_enabled=True, check_started=True)
    assert "running now" in running

    waiting = first_check_words(scanning_enabled=True, check_started=False)
    assert waiting == "This list has not checked the market for the first time."


def test_no_done_message_promises_a_check_that_is_not_going_to_happen():
    """"It is checking the market now" is the sentence somebody reads at the moment they
    publish. It was shown while nothing was checking, and the card on the very next
    screen said "Not looked yet"."""

    assert checking_message_overrides(scanning_enabled=True) == {}

    replaced = checking_message_overrides(scanning_enabled=False)
    # Every stored message that makes the promise is covered, not only the one reported.
    assert set(replaced) == {"monitor_published", "monitor_resumed"}
    for code, words in replaced.items():
        assert "checking the market now" not in words, code
        assert "not checking the market right now" in words, code
        # It still says the useful part: the monitor itself is fine and starts by itself.
        assert "starts on its own" in words, code


def test_the_shared_page_context_carries_both_answers():
    """Every dashboard page gets the notice and the replacements, built in one place.

    A page that asked for itself is the second reader this codebase keeps growing. The
    Monitors page did exactly that for one release and had to be taken back out.
    """

    source = (SOURCE / "api/routers/dashboard.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_context"
    )
    body = ast.unparse(function)

    assert "'market_checking': market_checking_notice" in body
    assert "'dashboard_message_overrides': checking_message_overrides" in body

    pages = (SOURCE / "api/routers/dashboard_test.py").read_text(encoding="utf-8")
    assert "market_checking=market_checking_notice" not in pages


def test_every_surface_reads_the_owner_rather_than_the_setting():
    """No page decides for itself what a switched-off scanner means.

    The recurring failure in this codebase is two readers of one fact, each with its own
    idea of what it means. Pages ask `market_checking_notice`; only the health endpoint,
    which reports the raw state rather than describing it, may read the setting.
    """

    allowed = {
        "core/config.py",  # where the setting is declared
        "services/product_language.py",  # the owner of what it means
        "api/routers/public.py",  # /health/deep reports the state, it does not word it
        "worker.py",  # the tasks the switch actually stops
        "services/scanner.py",  # and the scheduler behind them
        "core/startup.py",  # deployment validation
    }

    offenders: list[str] = []
    for path in SOURCE.rglob("*.py"):
        relative = path.relative_to(SOURCE).as_posix()
        source = path.read_text(encoding="utf-8")
        if relative in allowed or "scanning_enabled" not in source:
            continue
        tree = ast.parse(source)
        # Handing the switch to a function that owns the meaning is the one sanctioned
        # use. Anything else — an `if`, a ternary, a value written into a template — is a
        # second reader free to decide something different from the first.
        handed_over = {
            id(keyword.value)
            for node in ast.walk(tree)
            for keyword in getattr(node, "keywords", [])
        }
        offenders.extend(
            f"{relative}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == "scanning_enabled"
            and id(node) not in handed_over
        )

    assert offenders == [], (
        "these decide for themselves what the scanning switch means, instead of "
        f"handing it to market_checking_notice(): {offenders}"
    )


def test_the_card_asks_whether_a_check_finished_not_whether_a_row_exists():
    """`_watchlist_view` must never decide "has it looked" from the job row alone.

    A queued, running or failed job is a row with an empty `completed_at`. Reading the
    row answered "yes" for a monitor that had never produced a reading, which rendered
    "Looked Not yet" and scored it out of 100.
    """

    source = (SOURCE / "api/routers/dashboard_test.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_watchlist_view"
    )
    body = ast.unparse(function)

    assert "completed_at" not in body, (
        "read the resolved moment, never the row's field at the point of use"
    )
    assert "checked_at" in body
    # And which row that moment comes from is not this page's decision either. See
    # `tests/unit/test_invariant_monitor_scan_state.py`: the newest row and the last
    # finished check are two different rows on any monitor that is running.
    assert "scan_state.last_checked_at" in body
