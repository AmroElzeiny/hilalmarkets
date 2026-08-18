"""The redesigned Opportunities page, driven the way a person drives it.

Everything here is something only a running browser can answer: that a filter really
filters, that a popup really shows the evidence for the card that opened it, that a bar
tells a screen reader something a screen reader can use, and that nothing on the page is
too small to press.

The shared `page` fixture fails the test on any console error, any page error and any
failed request, so "no bugs" is enforced by the harness rather than by reading.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import (
    assert_contrast,
    assert_hilal_brand_palette,
    assert_no_horizontal_overflow,
    assert_no_raw_traceback,
    close_any_open_guide,
    seed_setup_observability,
    signup,
    unique_email,
)

PAGE = "/dashboard/opportunities"


def _with_findings(page: Page, base_url: str, browser_app) -> None:
    email = unique_email("opp-e2e")
    signup(page, base_url, email)
    close_any_open_guide(page)
    seed_setup_observability(browser_app.database_url, email)
    page.goto(f"{base_url}{PAGE}", wait_until="domcontentloaded")
    close_any_open_guide(page)
    expect(page.locator("[data-o-card]").first).to_be_visible(timeout=15_000)
    assert_no_raw_traceback(page)


def _empty(page: Page, base_url: str) -> None:
    signup(page, base_url)
    close_any_open_guide(page)
    page.goto(f"{base_url}{PAGE}", wait_until="domcontentloaded")
    close_any_open_guide(page)


# ── What it says ─────────────────────────────────────────────────────────────


def test_somebody_with_nothing_found_is_given_two_ways_on(page: Page, base_url: str):
    _empty(page, base_url)
    expect(page.locator(".o-first")).to_be_visible()
    ways = page.locator(".w-way")
    expect(ways).to_have_count(2)
    for index in range(2):
        assert ways.nth(index).get_attribute("href")


def test_the_two_ways_on_are_actually_drawn_as_cards(page: Page, base_url: str):
    """Present is not the same as designed.

    The check above passed for months while this panel had no layout at all on this
    page: every rule for it was scoped to the Watchlists page, and the Opportunities
    markup uses the same class names. The links existed, so the test was happy; what a
    person saw was a run of bare text with the arrows stacked underneath it.

    So this measures the drawing, not the markup — a real border, a real box, and the
    two of them side by side on a wide screen.
    """

    _empty(page, base_url)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.wait_for_timeout(300)

    drawn = page.evaluate(
        """() => [...document.querySelectorAll('.o-first .w-way')].map((node) => {
             const style = getComputedStyle(node);
             const box = node.getBoundingClientRect();
             return {
               border: parseFloat(style.borderTopWidth),
               radius: parseFloat(style.borderTopLeftRadius),
               top: Math.round(box.top),
               width: Math.round(box.width),
             };
           })"""
    )
    assert len(drawn) == 2
    for card in drawn:
        assert card["border"] >= 1, f"this way out has no border: {card}"
        assert card["radius"] >= 8, f"this way out is not a rounded card: {card}"
        assert card["width"] >= 200, f"this way out collapsed: {card}"
    assert drawn[0]["top"] == drawn[1]["top"], (
        f"the two ways out are stacked rather than side by side: {drawn}"
    )

    # The picture inside each one is drawn too, not an empty box.
    marks = page.evaluate(
        """() => [...document.querySelectorAll('.o-first .w-way-mark')]
             .map((node) => Math.round(node.getBoundingClientRect().width))"""
    )
    assert marks and all(width >= 30 for width in marks), f"the icons collapsed: {marks}"


def test_the_same_coin_is_never_shown_twice(page: Page, base_url: str, browser_app):
    """Finding 3. The live page drew SOL/USDT once as a radar row reading
    "Confirmation pending, 4/5 required rules passed" and again as a journey card
    reading "Getting closer, 80% ready", with nothing saying they were one thing."""

    _with_findings(page, base_url, browser_app)
    cards = page.locator("[data-o-card]")
    symbols = [
        cards.nth(index).locator("h2").inner_text().strip()
        for index in range(cards.count())
    ]
    assert len(symbols) == len(set(symbols)), f"a coin appeared more than once: {symbols}"


def test_every_card_says_what_it_is_doing_in_words(page: Page, base_url: str, browser_app):
    """Rule F2. Never colour alone: the rail has a word and an icon beside it."""

    _with_findings(page, base_url, browser_app)
    cards = page.locator("[data-o-card]")
    for index in range(cards.count()):
        state = cards.nth(index).locator(".o-state").inner_text().strip()
        assert len(state.split()) > 4, f"the status said nothing useful: {state!r}"


def test_progress_is_counted_and_a_screen_reader_can_read_it(
    page: Page, base_url: str, browser_app
):
    """Finding 11. The live page's bar carried no accessible value at all, so a screen
    reader read a bar that said nothing."""

    _with_findings(page, base_url, browser_app)
    bars = page.locator("[role='progressbar']")
    assert bars.count() > 0
    for index in range(bars.count()):
        bar = bars.nth(index)
        spoken = bar.get_attribute("aria-valuetext") or ""
        assert "things you asked for" in spoken or "thing you asked for" in spoken, spoken
        assert bar.get_attribute("aria-valuenow") is not None
        assert bar.get_attribute("aria-valuemax") is not None


def test_a_coin_we_could_not_check_is_not_drawn_as_a_failure(
    page: Page, base_url: str, browser_app
):
    """Finding 6. "0/5 required rules passed" with an empty bar reads as "your rules
    failed" when the truth is that nobody ever looked."""

    _with_findings(page, base_url, browser_app)
    unchecked = page.locator("[data-o-card][data-kind='unchecked']")
    if unchecked.count() == 0:
        pytest.skip("no coin on this account failed to be read")
    card = unchecked.first
    # No bar at all, and a sentence saying what it means.
    expect(card.locator("[role='progressbar']")).to_have_count(0)
    assert "not a pass and not a fail" in card.locator(".o-unchecked").inner_text()


def test_the_page_invents_no_colour_of_its_own(page: Page, base_url: str, browser_app):
    """Rule D1. Nothing on this path may introduce a colour the brand does not have."""

    _with_findings(page, base_url, browser_app)
    assert_hilal_brand_palette(page)


def test_the_small_print_is_actually_readable(page: Page, base_url: str, browser_app):
    """Rule F1, measured rather than assumed."""

    _with_findings(page, base_url, browser_app)
    assert_contrast(
        page,
        [
            [".o-found-by", "color"],
            [".o-waiting-why", "color"],
            [".w-fact dt", "color"],
            [".w-fact dd", "color"],
            [".o-progress-words", "color"],
            [".o-state .t-info-box", "color"],
            [".w-filter-count", "color"],
        ],
        at_least=4.5,
    )


def test_nothing_on_the_page_is_too_small_to_press(page: Page, base_url: str, browser_app):
    """WCAG 2.2 SC 2.5.8, and a thumb."""

    _with_findings(page, base_url, browser_app)
    small = page.evaluate(
        """() => [...document.querySelectorAll(
                '.hm-o button, .hm-o a[href], .hm-o input, .hm-o select')]
            .filter(el => el.offsetParent !== null)
            .map(el => ({h: Math.round(el.getBoundingClientRect().height),
                         t: (el.innerText || el.getAttribute('aria-label') || '').slice(0, 30)}))
            .filter(item => item.h > 0 && item.h < 44)"""
    )
    assert small == [], f"targets under 44px: {small}"


# ── Finding one among several ────────────────────────────────────────────────


def test_a_filter_really_filters(page: Page, base_url: str, browser_app):
    _with_findings(page, base_url, browser_app)
    total = page.locator("[data-o-card]").count()
    picked = None
    for key in ("close", "forming", "unchecked", "ready", "ended"):
        button = page.locator(f"[data-o-filter='{key}']")
        if button.is_enabled():
            picked = (key, button)
            break
    assert picked, "no group on this account had anything in it"
    key, button = picked
    button.click()
    expect(button).to_have_attribute("aria-pressed", "true")
    shown = page.locator("[data-o-card]:visible")
    assert shown.count() > 0
    for index in range(shown.count()):
        assert shown.nth(index).get_attribute("data-kind") == key

    page.locator("[data-o-filter='all']").click()
    expect(page.locator("[data-o-card]:visible")).to_have_count(total)


def test_a_group_with_nothing_in_it_cannot_be_pressed(page: Page, base_url: str, browser_app):
    _with_findings(page, base_url, browser_app)
    buttons = page.locator("[data-o-filter]")
    for index in range(buttons.count()):
        button = buttons.nth(index)
        if button.get_attribute("data-o-filter") == "all":
            continue
        if int(button.locator(".w-filter-count").inner_text()) == 0:
            expect(button).to_be_disabled()


def test_searching_for_nothing_offers_a_way_back(page: Page, base_url: str, browser_app):
    _with_findings(page, base_url, browser_app)
    page.locator("[data-o-search]").fill("zzzzzzz")
    expect(page.locator("[data-o-nothing]")).to_be_visible(timeout=5_000)
    page.locator("[data-o-reset]").click()
    expect(page.locator("[data-o-nothing]")).to_be_hidden()
    expect(page.locator("[data-o-card]").first).to_be_visible()


def test_searching_by_coin_finds_that_coin(page: Page, base_url: str, browser_app):
    _with_findings(page, base_url, browser_app)
    page.locator("[data-o-search]").fill("sol")
    page.wait_for_timeout(500)
    shown = page.locator("[data-o-card]:visible")
    assert shown.count() > 0
    for index in range(shown.count()):
        assert "sol" in shown.nth(index).get_attribute("data-name")


# ── The popups ───────────────────────────────────────────────────────────────


def test_what_did_we_see_shows_the_readings_for_that_card(
    page: Page, base_url: str, browser_app
):
    """Finding 5. The live page printed "Current: 1.27 - Required: 1.5 - Distance: 0.23"
    and left the reader to work out which number was which."""

    _with_findings(page, base_url, browser_app)
    card = page.locator("[data-o-card]").first
    symbol = card.locator("h2").inner_text().strip()
    card.locator("[data-o-saw]").click()
    dialog = page.locator("[data-o-saw-dialog]")
    expect(dialog).to_be_visible()
    assert symbol in page.locator("[data-o-saw-title]").inner_text()
    words = dialog.inner_text()
    assert "nothing here is advice" in words.lower()
    # Numbers arrive with their meaning attached, or the popup says there are none.
    assert "You asked for" in words or "Nothing was recorded" in words


def test_a_popup_gives_the_keyboard_back(page: Page, base_url: str, browser_app):
    _with_findings(page, base_url, browser_app)
    opener = page.locator("[data-o-saw]").first
    opener.click()
    expect(page.locator("[data-o-saw-dialog]")).to_be_visible()
    page.locator("[data-o-saw-cancel]").click()
    expect(page.locator("[data-o-saw-dialog]")).to_be_hidden()
    assert page.evaluate("() => document.activeElement.hasAttribute('data-o-saw')")


def test_escape_closes_a_popup(page: Page, base_url: str, browser_app):
    _with_findings(page, base_url, browser_app)
    page.locator("[data-o-saw]").first.click()
    expect(page.locator("[data-o-saw-dialog]")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("[data-o-saw-dialog]")).to_be_hidden()


def test_each_card_shows_its_own_evidence_not_the_first_ones(
    page: Page, base_url: str, browser_app
):
    """One popup serving many cards is one popup that can show the wrong card's
    numbers. It is filled from the card that opened it, every time."""

    _with_findings(page, base_url, browser_app)
    cards = page.locator("[data-o-card]")
    if cards.count() < 2:
        pytest.skip("only one coin on this account")
    seen = []
    for index in range(2):
        card = cards.nth(index)
        card.locator("[data-o-saw]").click()
        expect(page.locator("[data-o-saw-dialog]")).to_be_visible()
        seen.append(page.locator("[data-o-saw-title]").inner_text())
        page.locator("[data-o-saw-cancel]").click()
        expect(page.locator("[data-o-saw-dialog]")).to_be_hidden()
    assert seen[0] != seen[1]


# A coin with no screening record on file is answered with 409 by the evidence service,
# and the browser logs that. It is the case this test exists to drive.
@pytest.mark.deliberate_console_errors("409", "404")
def test_asking_whether_a_coin_is_halal_never_lands_on_a_missing_page(
    page: Page, base_url: str, browser_app
):
    """A link straight to the full Passport is a visible button that leads to "not
    found" whenever the coin has no published record. The popup answers in words."""

    _with_findings(page, base_url, browser_app)
    page.locator("[data-o-passport]").first.click()
    dialog = page.locator("[data-passport-dialog]")
    expect(dialog).to_be_visible()
    # Either the record, or a plain sentence saying there is none. Never a blank box.
    page.wait_for_function(
        """() => {
            const shown = document.querySelector('[data-pq-content]');
            const missing = document.querySelector('[data-pq-error]');
            return (shown && !shown.hidden) || (missing && !missing.hidden);
        }""",
        timeout=15_000,
    )
    assert len(dialog.inner_text().strip()) > 40
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()


@pytest.mark.deliberate_console_errors("404", "502", "500")
def test_the_price_picture_always_says_something(page: Page, base_url: str, browser_app):
    """Finding 12. The picture is drawn by an outside company's tool, and the popup says
    so. When there is no picture to draw it says that in words rather than leaving an
    empty grey box."""

    _with_findings(page, base_url, browser_app)
    chart = page.locator("[data-o-chart]")
    if chart.count() == 0:
        pytest.skip("no coin on this account has a recorded opportunity")
    chart.first.click()
    dialog = page.locator("[data-o-chart-dialog]")
    expect(dialog).to_be_visible()
    assert "outside company" in dialog.inner_text()
    # Whatever happens, the note settles on a sentence rather than on "getting ready".
    page.wait_for_function(
        "() => !document.querySelector('[data-o-chart-note]').textContent.includes('ready…')",
        timeout=15_000,
    )
    note = page.locator("[data-o-chart-note]").inner_text()
    assert len(note) > 20, note


# ── Everything else the harness cannot infer ─────────────────────────────────


@pytest.mark.parametrize("width", [1440, 1024, 760, 390])
def test_the_page_never_scrolls_sideways(page: Page, base_url: str, browser_app, width: int):
    page.set_viewport_size({"width": width, "height": 900})
    _with_findings(page, base_url, browser_app)
    assert_no_horizontal_overflow(page)


def test_the_whole_page_works_without_a_mouse(page: Page, base_url: str, browser_app):
    _with_findings(page, base_url, browser_app)
    page.locator("[data-o-filter='all']").focus()
    for _ in range(10):
        page.keyboard.press("Tab")
        if not page.evaluate("() => Boolean(document.activeElement.closest('.hm-o'))"):
            break
    page.locator("[data-o-saw]").first.focus()
    page.keyboard.press("Enter")
    expect(page.locator("[data-o-saw-dialog]")).to_be_visible()


def test_it_still_works_for_somebody_who_asked_for_less_motion(
    page: Page, base_url: str, browser_app
):
    page.emulate_media(reduced_motion="reduce")
    _with_findings(page, base_url, browser_app)
    expect(page.locator("[data-o-card]").first).to_be_visible()
    # The bar reaches its real width without ever moving.
    width = page.evaluate(
        "() => document.querySelector('[data-o-fill]')?.style.width || ''"
    )
    assert width.endswith("%"), width
    page.locator("[data-o-search]").fill("zzzzzzz")
    expect(page.locator("[data-o-nothing]")).to_be_visible(timeout=5_000)
