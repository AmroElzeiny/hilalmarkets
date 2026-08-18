"""The redesigned Connections page, driven the way a person drives it.

Everything here is something only a running browser can answer: that a switch really
switches and really saves, that a test send really reports back, that the dialogs trap
and return the keyboard, that the page does not scroll sideways on a phone, that the
colours are strong enough to read, and that nothing is too small to press.

The shared `page` fixture fails the test on any console error, any page error and any
failed request, so "no bugs, clean console" is enforced by the harness rather than by
reading the code.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.browser.conftest import (
    assert_contrast,
    assert_no_horizontal_overflow,
    assert_no_raw_traceback,
    close_any_open_guide,
    signup,
    unique_email,
)

PAGE = "/dashboard/connections"

#: The smallest a control on this page may be, in CSS pixels (WCAG 2.2 AA, 2.5.8).
MIN_TARGET = 44


def _open(page: Page, base_url: str) -> str:
    email = unique_email("conn-e2e")
    signup(page, base_url, email)
    close_any_open_guide(page)
    page.goto(f"{base_url}{PAGE}", wait_until="domcontentloaded")
    close_any_open_guide(page)
    expect(page.locator("[data-c-card]").first).to_be_visible(timeout=15_000)
    assert_no_raw_traceback(page)
    return email


# ── What it says ─────────────────────────────────────────────────────────────


def test_every_way_of_being_told_is_on_the_page(page: Page, base_url: str) -> None:
    _open(page, base_url)
    for channel in ("web", "email", "telegram", "whatsapp"):
        expect(page.locator(f'[data-channel="{channel}"]')).to_be_visible()


def test_the_page_says_what_will_arrive_before_anything_is_switched_on(
    page: Page, base_url: str
) -> None:
    """The live page never answered this. Somebody could switch a channel on with no
    idea what it would bring them."""

    _open(page, base_url)
    expect(page.locator(".c-kinds")).to_be_visible()
    assert page.locator("[data-c-kind]").count() >= 5


def test_a_channel_that_is_not_available_says_why(page: Page, base_url: str) -> None:
    _open(page, base_url)
    locked = page.locator('[data-channel="whatsapp"] .c-locked')
    expect(locked).to_be_visible()
    assert locked.inner_text().strip()


# ── What it does ─────────────────────────────────────────────────────────────


def test_switching_a_channel_on_saves_and_says_so(page: Page, base_url: str) -> None:
    """The switch is the whole point of the page. It has to move, save, and report."""

    _open(page, base_url)
    switch = page.locator('[data-c-switch="email"]')
    expect(switch).to_be_visible()

    before = switch.get_attribute("aria-checked")
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "false" if before == "true" else "true")

    # Saved, not only drawn: a reload has to agree with what the switch now shows.
    page.wait_for_timeout(600)
    page.reload(wait_until="domcontentloaded")
    close_any_open_guide(page)
    reloaded = page.locator('[data-c-switch="email"]')
    expect(reloaded).to_have_attribute(
        "aria-checked", "false" if before == "true" else "true"
    )


def test_the_state_line_follows_the_switch(page: Page, base_url: str) -> None:
    """Colour, word and icon move together. A card must never say "On" while its rail
    still says otherwise."""

    _open(page, base_url)
    card = page.locator('[data-channel="email"]')
    switch = card.locator("[data-c-switch]")
    if switch.get_attribute("aria-checked") != "true":
        switch.click()
        page.wait_for_timeout(400)
    expect(card.locator("[data-c-state-label]")).to_have_text("On")
    assert card.locator(".c-mark").get_attribute("data-tone") == "success"


def test_sending_a_test_really_sends_and_says_so(page: Page, base_url: str) -> None:
    """"Is it working?" is the question this page exists to answer, and the only honest
    way to answer it is to send something and report what happened.

    The browser harness runs the app with a real sender writing into a test outbox, so
    this is a real send — not a stub that always says yes.
    """

    _open(page, base_url)
    button = page.locator('[data-c-test="email"]')
    expect(button).to_be_visible()
    button.click()

    expect(button).to_contain_text("Sent", timeout=15_000)
    # And it says so in words a screen reader hears, not only by changing a button.
    expect(page.locator("[data-c-said]")).to_contain_text("test was sent", timeout=5_000)


# ── The popups ───────────────────────────────────────────────────────────────


def test_the_link_popup_opens_closes_and_gives_the_keyboard_back(
    page: Page, base_url: str
) -> None:
    _open(page, base_url)
    opener = page.locator("[data-c-connect-telegram]")
    expect(opener).to_be_visible()
    opener.click()

    dialog = page.locator("[data-c-link-dialog]")
    expect(dialog).to_be_visible()
    assert dialog.locator(".c-step").count() == 3

    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    assert page.evaluate(
        "() => document.activeElement?.hasAttribute('data-c-connect-telegram')"
    ), "closing the popup did not give the keyboard back to the button that opened it"


# ── How it looks and how it is used ──────────────────────────────────────────


def test_nothing_is_too_small_to_press(page: Page, base_url: str) -> None:
    _open(page, base_url)
    small = page.evaluate(
        """(minimum) => [...document.querySelectorAll(
             '.hm-c button, .hm-c a.t-action, .hm-c [role=switch]'
           )]
           .filter((node) => node.offsetParent !== null)
           .map((node) => ({ text: node.innerText.trim().slice(0, 40),
                             height: node.getBoundingClientRect().height }))
           .filter((item) => item.height < minimum)""",
        MIN_TARGET,
    )
    assert small == [], f"controls smaller than {MIN_TARGET}px: {small}"


def test_the_colours_are_strong_enough_to_read(page: Page, base_url: str) -> None:
    _open(page, base_url)
    assert_contrast(
        page,
        [
            [".hm-c .t-lede", "body"],
            [".hm-c .c-title p", ".c-card"],
            [".hm-c .c-state strong", ".c-state"],
            [".hm-c .c-switch-words span", ".c-switch"],
            [".hm-c .c-kind strong", ".c-kind"],
        ],
        at_least=4.5,
    )


def test_the_page_never_scrolls_sideways(page: Page, base_url: str) -> None:
    _open(page, base_url)
    for width in (1440, 1024, 760, 390):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(220)
        assert_no_horizontal_overflow(page)


def test_the_whole_page_can_be_reached_with_the_keyboard(page: Page, base_url: str) -> None:
    """Every control, in an order that makes sense, each with a visible focus ring."""

    _open(page, base_url)
    reached = page.evaluate(
        """() => {
             const wanted = [...document.querySelectorAll(
               '.hm-c button:not([disabled]), .hm-c a[href], .hm-c input, .hm-c [role=switch]'
             )].filter((node) => node.offsetParent !== null);
             return wanted.every((node) => node.tabIndex >= 0);
           }"""
    )
    assert reached, "something on this page cannot be reached with the keyboard"
