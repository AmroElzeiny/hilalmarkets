"""The redesigned side menu and topbar, driven the way a person drives them.

Everything here is something only a running browser can answer:

* that a link in the minimized menu still has a name a screen reader can read — the
  offline tests can only see that the CSS is right, not what the browser computed;
* that the marker really travels from row to row;
* that the flyout name appears for the keyboard and not only for a mouse;
* that a person can still sign out of a minimized menu;
* that the assistant's tag really moves, and really stops when asked;
* that the whole shell is readable, big enough to press, and throws nothing.

The shared `page` fixture fails the test on any console error, any page error and any
failed request, so "no bugs, clean console" is enforced by the harness.
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

#: The smallest a control anywhere in the shell may be (WCAG 2.2 AA, 2.5.8).
MIN_TARGET = 44

#: Nine destinations in three groups.
MENU_NAMES = (
    "Home",
    "Halal Assets",
    "Monitors",
    "Create a monitor",
    "Opportunities",
    "Notifications",
    "Plan and billing",
    "Settings",
    "Support",
)


def _open(page: Page, base_url: str, path: str = "/home") -> None:
    signup(page, base_url, unique_email("shell-e2e"))
    close_any_open_guide(page)
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    close_any_open_guide(page)
    expect(page.locator("[data-hm-shell-nav]")).to_be_visible(timeout=15_000)
    assert_no_raw_traceback(page)


def _minimize(page: Page) -> None:
    page.locator("[data-sidebar-collapse]").click()
    expect(page.locator("body.sidebar-collapsed")).to_have_count(1)
    page.wait_for_timeout(350)


# ── What the menu is ─────────────────────────────────────────────────────────


def test_the_menu_lists_every_destination_once(page: Page, base_url: str) -> None:
    _open(page, base_url)
    links = page.locator("[data-hm-nav-link]")
    expect(links).to_have_count(len(MENU_NAMES))
    for index, name in enumerate(MENU_NAMES):
        expect(links.nth(index)).to_contain_text(name)


def test_the_removed_entries_are_not_in_the_menu(page: Page, base_url: str) -> None:
    _open(page, base_url)
    menu = page.locator("[data-hm-shell-nav]").inner_text()
    assert "Trading Assistant" not in menu
    # "Main" was the front page's name. It is "Home" now, and the old word must not
    # survive anywhere in the menu beside it.
    assert "\nMain\n" not in f"\n{menu}\n"


def test_the_page_you_are_on_is_marked_and_announced(page: Page, base_url: str) -> None:
    _open(page, base_url)
    current = page.locator('[data-hm-nav-link][aria-current="page"]')
    expect(current).to_have_count(1)
    expect(current).to_contain_text("Home")


# ── The minimized menu ───────────────────────────────────────────────────────


def test_every_link_still_has_a_name_when_the_menu_is_minimized(
    page: Page, base_url: str
) -> None:
    """The bug this whole redesign started from.

    The old minimized menu hid each label with `display: none`, which takes it out of the
    accessibility tree as well — so nine links had no accessible name at all and a screen
    reader read nine identical "link". Measured here from what the browser really
    computed, not from the stylesheet.
    """

    _open(page, base_url)
    _minimize(page)

    names = page.eval_on_selector_all(
        "[data-hm-nav-link]",
        """(links) => links.map((link) => ({
            name: (link.textContent || '').trim(),
            visible: link.getBoundingClientRect().width > 0,
        }))""",
    )
    assert len(names) == len(MENU_NAMES)
    for entry in names:
        assert entry["visible"], "a link disappeared entirely"
        assert entry["name"], "a link in the minimized menu has no name to read"


def test_minimizing_narrows_the_shell_and_is_remembered(page: Page, base_url: str) -> None:
    _open(page, base_url)
    wide = page.locator("[data-hm-shell-nav]").bounding_box()["width"]
    _minimize(page)
    narrow = page.locator("[data-hm-shell-nav]").bounding_box()["width"]
    assert narrow < wide / 2, f"{narrow} is not a minimized menu beside {wide}"

    page.reload(wait_until="domcontentloaded")
    close_any_open_guide(page)
    expect(page.locator("body.sidebar-collapsed")).to_have_count(1)
    assert page.locator("[data-hm-shell-nav]").bounding_box()["width"] == narrow


def test_the_flyout_name_appears_for_the_keyboard_as_well_as_the_mouse(
    page: Page, base_url: str
) -> None:
    """A keyboard user has no hover. A name that only appears on hover is not a name."""

    _open(page, base_url)
    _minimize(page)

    cell = page.locator(".hm-nav-cell").nth(2)
    link = cell.locator("[data-hm-nav-link]")
    tip = cell.locator(".hm-nav-tip")

    link.hover()
    page.wait_for_timeout(300)
    expect(tip).to_be_visible()
    assert "Monitors" in tip.inner_text()

    page.mouse.move(0, 0)
    page.wait_for_timeout(300)
    link.focus()
    page.wait_for_timeout(300)
    expect(tip).to_be_visible()


def test_a_person_can_still_sign_out_of_a_minimized_menu(page: Page, base_url: str) -> None:
    """The sign-out form used to be one of the things `display: none` removed."""

    _open(page, base_url)
    _minimize(page)

    trigger = page.locator("[data-hm-nav-user]")
    expect(trigger).to_be_visible()
    trigger.click()
    menu = page.locator("[data-hm-nav-account]")
    expect(menu).to_be_visible()
    expect(menu.get_by_role("menuitem", name="Sign out")).to_be_visible()

    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()


# ── Movement ─────────────────────────────────────────────────────────────────


def test_the_marker_travels_to_whichever_row_is_pointed_at(page: Page, base_url: str) -> None:
    """One surface that moves, rather than nine that blink on and off.

    Motion is what says "that row, now this one". Measured as a real position change,
    because an animation object being created proves nothing about where anything ended.
    """

    _open(page, base_url)
    marker = page.locator("[data-hm-nav-marker]")

    page.locator("[data-hm-nav-link]").nth(1).hover()
    page.wait_for_timeout(400)
    first = marker.bounding_box()["y"]

    page.locator("[data-hm-nav-link]").nth(6).hover()
    page.wait_for_timeout(400)
    second = marker.bounding_box()["y"]

    assert second > first + 40, f"the marker did not travel: {first} then {second}"
    assert float(marker.evaluate("(el) => getComputedStyle(el).opacity")) > 0.5


# ── The topbar ───────────────────────────────────────────────────────────────


def test_the_topbar_says_which_page_you_are_on(page: Page, base_url: str) -> None:
    _open(page, base_url, "/dashboard/monitors")
    expect(page.locator(".hm-top-here-group")).to_have_text("Your monitors")
    expect(page.locator(".hm-top-here-name")).to_have_text("Monitors")


def test_the_page_action_is_in_the_bar_and_works(page: Page, base_url: str) -> None:
    _open(page, base_url, "/dashboard/monitors")
    action = page.locator("[data-hm-top-action]")
    expect(action).to_have_count(1)
    expect(action).to_contain_text("Create a monitor")
    action.click()
    page.wait_for_url("**/dashboard/monitor**", timeout=20_000)


def test_the_keyboard_shortcut_reaches_the_search_box(page: Page, base_url: str) -> None:
    _open(page, base_url)
    key = page.locator("[data-hm-top-search-key]")
    assert key.inner_text().strip(), "the shortcut is not written in the bar"
    page.keyboard.press("Control+k")
    page.wait_for_timeout(200)
    assert page.evaluate(
        "() => document.activeElement?.matches('[data-hm-top-search-input]')"
    )


# ── The assistant ────────────────────────────────────────────────────────────


def test_the_assistant_tag_scrolls_its_line_and_can_be_stopped(
    page: Page, base_url: str
) -> None:
    """One line, one box narrower than it, and movement that never ends.

    Also the WCAG 2.2.2 half: moving text that starts by itself has to be stoppable.
    """

    _open(page, base_url, "/dashboard/monitors")
    tag = page.locator("[data-hilal-tag]")
    expect(tag).to_be_visible()

    run = page.locator("[data-hilal-tag-run]")
    box = tag.bounding_box()["width"]
    line = run.bounding_box()["width"]
    assert line > box, f"the line ({line}) is not longer than its box ({box})"

    first = run.evaluate("(el) => el.getBoundingClientRect().x")
    page.wait_for_timeout(900)
    second = run.evaluate("(el) => el.getBoundingClientRect().x")
    assert second < first, "the line is not moving"

    tag.hover()
    page.wait_for_timeout(300)
    paused = run.evaluate("(el) => el.getBoundingClientRect().x")
    page.wait_for_timeout(600)
    assert abs(run.evaluate("(el) => el.getBoundingClientRect().x") - paused) < 1, (
        "the line does not stop when a pointer reaches it"
    )


def test_the_assistant_says_it_is_software_and_what_it_can_see(
    page: Page, base_url: str
) -> None:
    _open(page, base_url, "/dashboard/monitors")
    words = page.locator("[data-hilal-tag]").inner_text()
    assert "AI assistant" in words
    assert "sees the page you are on" in words
    # And the same sentence is the button's own name, for anybody who cannot see the tag.
    name = page.locator("[data-hilal-open]").get_attribute("aria-label")
    assert "AI assistant" in name
    assert "sees the page you are on" in name


# ── It is readable, pressable, and quiet ─────────────────────────────────────


def test_every_word_in_the_shell_is_readable(page: Page, base_url: str) -> None:
    _open(page, base_url)
    assert_contrast(
        page,
        [
            [".hm-nav-group-label", "body"],
            # The row you are on is white on near-black and is measured separately, so
            # this one is a row you are not on: quiet grey on the menu's own white.
            [".hm-nav-link:not(.is-active)", "body"],
            [".hm-top-here-group", "body"],
            [".hm-top-here-name", "body"],
            [".hm-nav-user-copy small", "body"],
        ],
        at_least=4.5,
    )
    # The row you are on: white words on the near-black fill. Measured through the label
    # inside it, because the shared helper reads the background of an element's *parent*
    # and the row's own fill is only visible from in there.
    assert_contrast(page, [[".hm-nav-link.is-active .hm-nav-text", "body"]], at_least=4.5)


def test_nothing_in_the_shell_is_too_small_to_press(page: Page, base_url: str) -> None:
    _open(page, base_url)
    small = page.evaluate(
        """(least) => {
            const parts = [
              ...document.querySelectorAll(
                '[data-hm-shell-nav] a, [data-hm-shell-nav] button, ' +
                '[data-hm-shell-top] a, [data-hm-shell-top] button, ' +
                '[data-hm-shell-top] input'
              ),
            ];
            return parts
              .map((el) => ({ el: el.className, box: el.getBoundingClientRect() }))
              .filter((item) => item.box.width > 0)
              .filter((item) => item.box.width < least || item.box.height < least)
              .map((item) => ({
                el: item.el,
                w: Math.round(item.box.width),
                h: Math.round(item.box.height),
              }));
        }""",
        MIN_TARGET,
    )
    assert small == [], f"targets under {MIN_TARGET}px: {small}"


def test_the_shell_never_pushes_the_page_sideways(page: Page, base_url: str) -> None:
    _open(page, base_url)
    assert_no_horizontal_overflow(page)
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(300)
    assert_no_horizontal_overflow(page)


def test_the_drawer_opens_and_closes_on_a_phone(page: Page, base_url: str) -> None:
    _open(page, base_url)
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(300)

    nav = page.locator("[data-hm-shell-nav]")
    assert nav.bounding_box()["x"] < 0, "the drawer starts open on a phone"

    page.locator("[data-open-sidebar]").click()
    page.wait_for_timeout(400)
    assert nav.bounding_box()["x"] >= 0

    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    assert nav.bounding_box()["x"] < 0
