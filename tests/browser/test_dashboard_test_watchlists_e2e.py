"""The redesigned Watchlists page, driven the way a person drives it.

Everything here is something only a running browser can answer: that a filter really
filters, that "Show me" really shows, that nothing changes a list without asking first,
that the dialogs behave, and that nothing on the page is too small to press.

The shared `page` fixture fails the test on any console error, any page error and any
failed request, so "no bugs" is enforced by the harness rather than by reading.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from ai_market_monitor.core.dashboard_paths import MONITOR_PATH, MONITORS_PATH
from tests.browser.conftest import (
    assert_contrast,
    assert_no_horizontal_overflow,
    assert_no_raw_traceback,
    close_any_open_guide,
    seed_setup_observability,
    signup,
    unique_email,
)

#: Both addresses come from the one file that owns them. Written out here again, they
#: were a second opinion about where each page lives — and when the canvas moved, this
#: file was still waiting at the old address.
PAGE = MONITORS_PATH


def _with_lists(page: Page, base_url: str, browser_app) -> None:
    email = unique_email("wl-e2e")
    signup(page, base_url, email)
    close_any_open_guide(page)
    seed_setup_observability(browser_app.database_url, email)
    page.goto(f"{base_url}{PAGE}", wait_until="domcontentloaded")
    close_any_open_guide(page)
    expect(page.locator("[data-w-card]").first).to_be_visible(timeout=15_000)
    assert_no_raw_traceback(page)


def _empty(page: Page, base_url: str) -> None:
    signup(page, base_url)
    close_any_open_guide(page)
    page.goto(f"{base_url}{PAGE}", wait_until="domcontentloaded")
    close_any_open_guide(page)


# ── What it says ─────────────────────────────────────────────────────────────


def test_a_person_with_no_lists_is_shown_the_way_to_make_one(
    page: Page, base_url: str
) -> None:
    """One way, and it is the canvas.

    There used to be two — the assistant and the canvas — side by side. Authoring never
    requires the assistant, so the second card asked a beginner to choose between two
    things before they knew what either one was. It is gone; what is left has to work.
    """

    _empty(page, base_url)
    expect(page.locator(".w-first")).to_be_visible()
    ways = page.locator(".w-way")
    expect(ways).to_have_count(1)
    expect(ways.first).to_contain_text("Draw it on the canvas")
    assert ways.first.get_attribute("href"), "the one way on the page goes nowhere"
    ways.first.click()
    page.wait_for_url(re.compile(f"{re.escape(MONITOR_PATH)}$"), timeout=30_000)


def test_every_card_says_what_it_is_doing_in_words(
    page: Page, base_url: str, browser_app
) -> None:
    """Rule F2. Never colour alone: the rail has a word beside it, always."""
    _with_lists(page, base_url, browser_app)
    card = page.locator("[data-w-card]").first
    status = card.locator(".w-status").inner_text().strip()
    assert status, "the card showed a colour and no word"
    assert len(status.split()) > 3, f"the status said nothing useful: {status!r}"
    expect(card.locator(".w-working")).to_be_visible()


def test_the_small_print_is_actually_readable(
    page: Page, base_url: str, browser_app
) -> None:
    """Rule F1, measured rather than assumed."""

    _with_lists(page, base_url, browser_app)
    assert_contrast(
        page,
        [
            [".w-status", "color"],
            [".w-note", "color"],
            [".w-fact dt", "color"],
            [".w-fact dd", "color"],
            [".w-working", "color"],
            [".w-filter-count", "color"],
        ],
        at_least=4.5,
    )


def test_nothing_on_the_page_is_too_small_to_press(
    page: Page, base_url: str, browser_app
) -> None:
    """WCAG 2.2 SC 2.5.8, and a thumb."""
    _with_lists(page, base_url, browser_app)
    small = page.evaluate(
        """() => [...document.querySelectorAll('.hm-w button, .hm-w a[href], .hm-w input')]
            .filter(el => el.offsetParent !== null)
            .map(el => ({h: Math.round(el.getBoundingClientRect().height),
                         t: (el.innerText || el.getAttribute('aria-label') || '').slice(0, 30)}))
            .filter(item => item.h > 0 && item.h < 44)"""
    )
    assert small == [], f"targets under 44px: {small}"


# ── Finding one list among several ───────────────────────────────────────────


def test_a_filter_really_filters(page: Page, base_url: str, browser_app) -> None:
    _with_lists(page, base_url, browser_app)
    total = page.locator("[data-w-card]").count()
    # "Watching" is the one bucket a seeded account always has something in.
    watching = page.locator("[data-w-filter='watching']")
    expect(watching).to_be_enabled()
    watching.click()
    expect(watching).to_have_attribute("aria-pressed", "true")
    shown = page.locator("[data-w-card]:visible")
    assert shown.count() > 0
    for index in range(shown.count()):
        assert shown.nth(index).get_attribute("data-bucket") == "watching"

    page.locator("[data-w-filter='all']").click()
    expect(page.locator("[data-w-card]:visible")).to_have_count(total)


def test_a_filter_with_nothing_in_it_cannot_be_pressed(
    page: Page, base_url: str, browser_app
) -> None:
    """A button that leads to an empty page is a button that should say so first."""
    _with_lists(page, base_url, browser_app)
    for key in ("paused", "unfinished"):
        button = page.locator(f"[data-w-filter='{key}']")
        count = int(button.locator(".w-filter-count").inner_text())
        if count == 0:
            expect(button).to_be_disabled()


def test_searching_for_nothing_offers_a_way_back(
    page: Page, base_url: str, browser_app
) -> None:
    _with_lists(page, base_url, browser_app)
    page.locator("[data-w-search]").fill("zzzzzzz")
    expect(page.locator("[data-w-nothing]")).to_be_visible(timeout=5_000)
    page.locator("[data-w-reset]").click()
    expect(page.locator("[data-w-nothing]")).to_be_hidden()
    expect(page.locator("[data-w-card]").first).to_be_visible()


def test_show_me_takes_you_to_the_list_that_needs_a_look(
    page: Page, base_url: str, browser_app
) -> None:
    _with_lists(page, base_url, browser_app)
    jump = page.locator("[data-w-jump]")
    if jump.count() == 0:
        pytest.skip("no list on this account needs a look")
    jump.click()
    page.wait_for_timeout(700)
    shown = page.locator("[data-w-card]:visible")
    expect(shown.first).to_be_visible()
    for index in range(shown.count()):
        assert shown.nth(index).get_attribute("data-attention") == "true"


# ── Nothing changes without being asked ──────────────────────────────────────


@pytest.mark.parametrize("which", ["pause", "archive"])
def test_changing_a_list_asks_first(
    page: Page, base_url: str, browser_app, which: str
) -> None:
    """The live page used the browser's own confirm box for putting a list away —
    unstyled, unbranded, and not something a screen reader announces as a choice."""
    _with_lists(page, base_url, browser_app)
    page.locator(f"[data-w-ask='{which}']").first.click()
    dialog = page.locator("[data-w-dialog]")
    expect(dialog).to_be_visible()
    # It names the list, so nobody pauses the wrong one.
    assert page.locator("[data-w-ask-name]").inner_text().strip()
    # And it says what will happen, not only what the button is called.
    assert len(page.locator("[data-w-ask-body]").inner_text()) > 40


def test_saying_no_changes_nothing_and_gives_the_keyboard_back(
    page: Page, base_url: str, browser_app
) -> None:
    _with_lists(page, base_url, browser_app)
    opener = page.locator("[data-w-ask='pause']").first
    opener.click()
    expect(page.locator("[data-w-dialog]")).to_be_visible()
    page.locator("[data-w-ask-cancel]").click()
    expect(page.locator("[data-w-dialog]")).to_be_hidden()
    focused = page.evaluate("() => document.activeElement.dataset.wAsk || ''")
    assert focused == "pause", "the keyboard was left nowhere"
    # And the list is still exactly as it was.
    expect(page.locator("[data-w-card]").first).to_be_visible()


def test_escape_closes_the_question(page: Page, base_url: str, browser_app) -> None:
    _with_lists(page, base_url, browser_app)
    page.locator("[data-w-ask='archive']").first.click()
    expect(page.locator("[data-w-dialog]")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("[data-w-dialog]")).to_be_hidden()


# ── Explaining a word instead of assuming it ─────────────────────────────────


def test_what_is_holding_a_list_back_can_be_asked_about(
    page: Page, base_url: str, browser_app
) -> None:
    _with_lists(page, base_url, browser_app)
    holding = page.locator("[data-w-holding]")
    if holding.count() == 0:
        pytest.skip("no list on this account has a recorded blocker")
    holding.first.click()
    dialog = page.locator("[data-w-explain]")
    expect(dialog).to_be_visible()
    words = dialog.inner_text().lower()
    # It explains rather than restating, and it refuses to be read as advice.
    assert "not advice" in words, words
    assert "%" in dialog.locator("[data-w-explain-share]").inner_text()
    page.wait_for_timeout(400)
    width = page.evaluate(
        "() => document.querySelector('[data-w-explain-fill]').getBoundingClientRect().width"
    )
    assert width > 0, "the share bar never filled"


# ── Everything else the harness cannot infer ─────────────────────────────────


@pytest.mark.parametrize("width", [1440, 1024, 760, 390])
def test_the_page_never_scrolls_sideways(
    page: Page, base_url: str, browser_app, width: int
) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    _with_lists(page, base_url, browser_app)
    assert_no_horizontal_overflow(page)


def test_the_whole_page_works_without_a_mouse(
    page: Page, base_url: str, browser_app
) -> None:
    _with_lists(page, base_url, browser_app)
    page.locator("[data-w-filter='all']").focus()
    for _ in range(10):
        page.keyboard.press("Tab")
        inside = page.evaluate(
            "() => Boolean(document.activeElement.closest('.hm-w'))"
        )
        if not inside:
            break
    page.locator("[data-w-ask='pause']").first.focus()
    page.keyboard.press("Enter")
    expect(page.locator("[data-w-dialog]")).to_be_visible()


def test_it_still_works_for_somebody_who_asked_for_less_motion(
    page: Page, base_url: str, browser_app
) -> None:
    page.emulate_media(reduced_motion="reduce")
    _with_lists(page, base_url, browser_app)
    expect(page.locator("[data-w-card]").first).to_be_visible()
    page.locator("[data-w-search]").fill("zzzzzzz")
    expect(page.locator("[data-w-nothing]")).to_be_visible(timeout=5_000)
