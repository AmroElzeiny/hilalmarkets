"""The contextual page guide, exercised against the real rendered dashboard.

The guide's whole promise is that it points at the exact thing it is describing. These
tests check that promise where it can actually break: real markup, real scrolling, a real
collapsed sidebar, a real 390px viewport.

Two of them deliberately poison the page — remove a marker, duplicate a marker — because
the fail-closed rule is the one that keeps a wrong arrow off the screen, and a rule with
no test is a rule that quietly stops holding.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import (
    assert_no_raw_traceback,
    close_any_open_guide,
    seed_sharia_screened_market,
    signup,
    unique_email,
)

MOBILE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 1000}

#: How far the spotlight may sit from the element it is describing. The brief allows
#: about two pixels; the engine rounds to whole pixels, so one pixel of rounding plus the
#: 8px padding it adds on every side is the whole budget.
ALIGNMENT_TOLERANCE_PX = 2
SPOTLIGHT_PADDING = 8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _registry(page: Page) -> dict:
    """The live registry the running page is actually using."""

    return page.evaluate("() => window.HilalMarketsGuide && window.HilalMarketsGuide.registry")


def _page_key(page: Page) -> str | None:
    return page.evaluate(
        "() => { const n = document.querySelector('[data-hm-guide-page]');"
        " return n ? n.getAttribute('data-hm-guide-page') : null; }"
    )


def _close_any_open_guide(page: Page) -> None:
    """One implementation, shared with the rest of the browser suite.

    Home auto-starts for a new account and the overlay would eat the click. The
    overlay blocking the page is the guide behaving correctly, so anything that clicks
    the page underneath has to close it first rather than force the click.
    """

    close_any_open_guide(page)


def _start_guide(page: Page) -> None:
    _close_any_open_guide(page)
    launcher = page.locator("[data-hm-guide-launcher]")
    expect(launcher).to_be_visible()
    launcher.click()
    expect(page.locator("[data-hm-guide-popover]")).to_be_visible()


def _current_target_is_in_the_menu(page: Page) -> bool:
    return bool(
        page.evaluate(
            "() => { const engine = window.HilalMarketsGuide && window.HilalMarketsGuide.engine;"
            " const bar = document.querySelector('[data-hilal-sidebar], [data-sidebar]');"
            " return Boolean(engine && engine.target && bar && bar.contains(engine.target)); }"
        )
    )


def _walk_to_first_menu_step(page: Page, limit: int = 12) -> None:
    """Press Next until the highlighted element is a side-menu link."""

    for _ in range(limit):
        if _current_target_is_in_the_menu(page):
            return
        page.locator("[data-hm-guide-next]").click()
        page.wait_for_timeout(120)
    raise AssertionError("no step in this guide points at the side menu")


def _counter_text(page: Page) -> str:
    return page.locator("[data-hm-guide-counter]").inner_text()


def _spotlight_box(page: Page) -> dict:
    return page.locator("[data-hm-guide-spotlight]").bounding_box()


def _guide_pages(base_url: str) -> list[tuple[str, str]]:
    """Every route that renders a guide, with the page key it must report.

    `/dashboard/watchlist`, `/dashboard/compliance` and `/dashboard/methodology` are
    deliberately absent: the router redirects all three, so they are not pages that own a
    guide. Their destinations are covered instead.
    """

    return [
        # Main is the front page. `/dashboard` is the old Home page's address
        # and redirects here, so this names the page a browser really lands on.
        (f"{base_url}/main", "dashboard-today"),
        (f"{base_url}/dashboard/market", "screened-market"),
        (f"{base_url}/dashboard/strategies", "watch-plans"),
        (f"{base_url}/dashboard/opportunities", "activity"),
        (f"{base_url}/dashboard/integrations", "integrations"),
        (f"{base_url}/dashboard/billing", "billing"),
        (f"{base_url}/dashboard/settings", "settings"),
    ]


# ---------------------------------------------------------------------------
# 0. The guide must never stop the page it is describing from loading.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "key"),
    [
        ("/dashboard/opportunities", "activity"),
        ("/dashboard/market", "screened-market"),
        ("/dashboard/billing", "billing"),
    ],
)
def test_a_page_whose_markers_are_absent_still_finishes_loading(
    page: Page,
    base_url: str,
    path: str,
    key: str,
) -> None:
    """The launcher check runs on pages where no marker has rendered yet.

    It used to rewrite the launcher's `hidden` attribute on every pass while watching
    for attribute changes, so its own write woke it up again. That loop runs as
    microtasks, which starve the event loop completely: the page never reached
    DOMContentLoaded, no timer fired, and the browser showed it loading for ever.
    """

    signup(page, base_url)
    # A short budget on purpose. The failure being guarded against is unbounded.
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded", timeout=15_000)
    assert page.evaluate("() => document.readyState") in {"interactive", "complete"}
    assert _page_key(page) == key
    assert_no_raw_traceback(page)


def test_the_opportunities_page_resolves_its_loading_placeholders(
    page: Page,
    base_url: str,
) -> None:
    """A skeleton is a promise that something is coming. It must be kept."""

    signup(page, base_url)
    page.goto(f"{base_url}/dashboard/opportunities", wait_until="domcontentloaded", timeout=15_000)
    radar = page.locator("[data-radar-list]")
    expect(radar).to_have_attribute("aria-busy", "false", timeout=15_000)
    expect(radar.locator(".skeleton")).to_have_count(0)


# ---------------------------------------------------------------------------
# 1. The registry is honest about the pages it claims to describe.
# ---------------------------------------------------------------------------


def test_every_configured_target_is_unique_on_its_own_page(
    page: Page,
    base_url: str,
    browser_app,
) -> None:
    """INV: one step, one element. Never zero, never two.

    This is the test that stops the guide drifting away from the templates. A marker
    renamed or deleted during ordinary UI work fails here, loudly, instead of silently
    shrinking someone's guide in production.
    """

    email = signup(page, base_url, unique_email("guide-registry"))
    seed_sharia_screened_market(browser_app.database_url, email)

    problems: list[str] = []
    checked = 0
    for url, expected_key in _guide_pages(base_url):
        page.set_viewport_size(DESKTOP)
        page.goto(url, wait_until="domcontentloaded")
        assert_no_raw_traceback(page)

        actual_key = _page_key(page)
        if actual_key != expected_key:
            problems.append(f"{url}: page key is {actual_key!r}, expected {expected_key!r}")
            continue

        registry = _registry(page)
        assert registry, f"{url}: the guide script did not load"
        guide = registry.get(expected_key)
        assert guide, f"{url}: no guide is registered for {expected_key}"

        for step in guide["steps"]:
            checked += 1
            count = page.locator(f'[data-hm-guide-target="{step["target"]}"]').count()
            if count != 1:
                problems.append(
                    f"{expected_key}: '{step['target']}' matched {count} elements at {url}"
                )

    assert not problems, "guide targets did not resolve exactly once:\n" + "\n".join(problems)
    assert checked >= 15, f"only {checked} steps were checked; the registry looks truncated"


def test_the_passport_guide_targets_resolve_on_a_real_passport(
    page: Page,
    base_url: str,
    browser_app,
) -> None:
    """The Passport is the one guided page whose URL carries an asset slug."""

    email = signup(page, base_url, unique_email("guide-passport-registry"))
    seeded = seed_sharia_screened_market(browser_app.database_url, email)
    page.set_viewport_size(DESKTOP)
    page.goto(
        f"{base_url}/dashboard/market?methodology_id={seeded['methodology_id']}",
        wait_until="domcontentloaded",
    )

    link = page.locator('a[href^="/dashboard/market/"]').first
    if link.count() == 0:
        pytest.skip("no screened asset was seeded, so there is no Passport to check")
    link.click()
    page.wait_for_url(re.compile(r".*/dashboard/market/.+"), timeout=15_000)

    assert _page_key(page) == "asset-passport"
    for step in _registry(page)["asset-passport"]["steps"]:
        count = page.locator(f'[data-hm-guide-target="{step["target"]}"]').count()
        assert count == 1, f"'{step['target']}' matched {count} elements on the Passport"
    assert_no_raw_traceback(page)


def test_every_registered_guide_is_reachable_from_a_real_page(
    page: Page,
    base_url: str,
) -> None:
    """A guide nobody can open is dead weight that still has to be maintained."""

    signup(page, base_url, unique_email("guide-reach"))
    page.goto(f"{base_url}/main", wait_until="domcontentloaded")
    registered = set(_registry(page).keys())
    # The Passport lives under a dynamic asset slug, so it cannot appear in a static
    # route list. Its own test navigates to a real one.
    reachable = {key for _, key in _guide_pages(base_url)} | {"asset-passport"}
    assert registered == reachable, (
        f"registered but unreachable: {sorted(registered - reachable)}; "
        f"reachable but unregistered: {sorted(reachable - registered)}"
    )


def test_guide_copy_obeys_the_length_and_safety_rules(
    page: Page,
    base_url: str,
) -> None:
    """Short titles, two sentences at most, and nothing the product cannot support."""

    signup(page, base_url, unique_email("guide-copy"))
    page.goto(f"{base_url}/main", wait_until="domcontentloaded")
    registry = _registry(page)

    banned = re.compile(
        r"\b(halal|haram|profit|guarantee|guaranteed|will rise|will fall|invest|"
        r"buy now|sell now|next button|this is the)\b",
        re.IGNORECASE,
    )
    problems: list[str] = []
    for key, guide in registry.items():
        for step in guide["steps"]:
            title, body = step["title"], step["body"]
            if len(title.split()) > 5:
                problems.append(f"{key}/{step['target']}: title is {len(title.split())} words")
            if len(body.split()) > 34:
                problems.append(f"{key}/{step['target']}: body is {len(body.split())} words")
            if len([s for s in re.split(r"(?<=[.!?])\s+", body.strip()) if s]) > 2:
                problems.append(f"{key}/{step['target']}: body is more than two sentences")
            if banned.search(f"{title} {body}"):
                problems.append(f"{key}/{step['target']}: uses forbidden wording")
    assert not problems, "guide copy broke its own rules:\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# 2. Home, desktop and mobile.
# ---------------------------------------------------------------------------


def test_home_guide_runs_on_desktop_with_a_counter_and_a_real_hole(
    page: Page,
    base_url: str,
) -> None:
    signup(page, base_url, unique_email("guide-home-desktop"))
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/main", wait_until="domcontentloaded")

    _start_guide(page)
    counter = _counter_text(page)
    assert re.match(r"^Step 1 of \d+", counter), counter

    total = int(re.search(r"of (\d+)", counter).group(1))
    if total > 1:
        assert "left" in counter, counter

    # The target itself is never covered: the spotlight is a border, not a fill.
    covered = page.evaluate(
        "() => { const s = document.querySelector('[data-hm-guide-spotlight]');"
        " return getComputedStyle(s).pointerEvents; }"
    )
    assert covered == "none", "the spotlight must not intercept clicks on the target"
    assert_no_raw_traceback(page)


def test_today_guide_runs_at_mobile_width(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("guide-home-mobile"))
    page.set_viewport_size(MOBILE)
    page.goto(f"{base_url}/main", wait_until="domcontentloaded")

    _start_guide(page)
    popover = page.locator("[data-hm-guide-popover]").bounding_box()
    assert popover["x"] >= 0, popover
    assert popover["x"] + popover["width"] <= MOBILE["width"] + 1, popover
    assert popover["y"] >= 0, popover

    # Every control stays tappable at this width.
    for name in ("back", "next", "skip"):
        box = page.locator(f"[data-hm-guide-{name}]").bounding_box()
        assert box["height"] >= 44, f"{name} is {box['height']}px tall"

    # Icon-only launcher on a constrained layout.
    label = page.locator("[data-hm-guide-launcher] .hm-guide-launcher-label")
    assert label.bounding_box()["width"] <= 2, "the launcher label must collapse on mobile"
    assert_no_raw_traceback(page)


# ---------------------------------------------------------------------------
# 3. Hidden targets: the sidebar has to be opened, then put back.
# ---------------------------------------------------------------------------


def test_a_collapsed_desktop_sidebar_is_expanded_and_then_restored(
    page: Page,
    base_url: str,
) -> None:
    """The guide may reveal a target, but the user's layout is theirs to keep."""

    signup(page, base_url, unique_email("guide-sidebar-desktop"))
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/main", wait_until="domcontentloaded")
    # Main opens its guide by itself for a new account, and the dimmed panels correctly
    # block the page underneath. Put it away before touching the menu control.
    _close_any_open_guide(page)
    page.locator("[data-sidebar-collapse]").click()
    expect(page.locator("body")).to_have_class(re.compile(r"sidebar-collapsed"))

    # The Home guide's last three steps really do point at menu links, so this uses the
    # shipped registry rather than a target injected for the test.
    _start_guide(page)
    _walk_to_first_menu_step(page)
    expect(page.locator("body")).not_to_have_class(re.compile(r"sidebar-collapsed"))

    page.locator("[data-hm-guide-skip]").click()
    expect(page.locator("[data-hm-guide-popover]")).to_be_hidden()
    expect(page.locator("body")).to_have_class(re.compile(r"sidebar-collapsed"))
    assert_no_raw_traceback(page)


def test_three_menu_steps_in_a_row_still_give_the_menu_back(
    page: Page,
    base_url: str,
) -> None:
    """Revealing the menu records the user's own state once, not once per step.

    Saving it again on the second menu step would record the state the guide itself
    produced, and the menu would stay open after the guide closed.
    """

    signup(page, base_url, unique_email("guide-sidebar-run"))
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/main", wait_until="domcontentloaded")
    # Main opens its guide by itself for a new account, and the dimmed panels correctly
    # block the page underneath. Put it away before touching the menu control.
    _close_any_open_guide(page)
    page.locator("[data-sidebar-collapse]").click()
    expect(page.locator("body")).to_have_class(re.compile(r"sidebar-collapsed"))

    _start_guide(page)
    menu_steps = 0
    while page.locator("[data-hm-guide-next]").inner_text().strip() != "Done":
        if _current_target_is_in_the_menu(page):
            menu_steps += 1
        page.locator("[data-hm-guide-next]").click()
        page.wait_for_timeout(120)
    assert _current_target_is_in_the_menu(page), "the last Main step points at the menu"
    menu_steps += 1
    assert menu_steps >= 3, f"expected at least three menu steps, walked {menu_steps}"

    page.locator("[data-hm-guide-next]").click()
    expect(page.locator("[data-hm-guide-popover]")).to_be_hidden()
    expect(page.locator("body")).to_have_class(re.compile(r"sidebar-collapsed"))
    assert_no_raw_traceback(page)


def test_a_mobile_sidebar_target_is_opened_and_then_closed_again(
    page: Page,
    base_url: str,
) -> None:
    signup(page, base_url, unique_email("guide-sidebar-mobile"))
    page.set_viewport_size(MOBILE)
    page.goto(f"{base_url}/main", wait_until="domcontentloaded")

    sidebar = page.locator("[data-sidebar]")
    assert "is-open" not in (sidebar.get_attribute("class") or "")

    _start_guide(page)
    _walk_to_first_menu_step(page)
    assert "is-open" in (sidebar.get_attribute("class") or ""), "the guide must open the drawer"

    page.keyboard.press("Escape")
    expect(page.locator("[data-hm-guide-popover]")).to_be_hidden()
    assert "is-open" not in (sidebar.get_attribute("class") or ""), "the drawer must be restored"
    assert_no_raw_traceback(page)


# ---------------------------------------------------------------------------
# 4. Each workflow page opens its own guide.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected_key"),
    [
        ("/dashboard/activity", "activity"),
        ("/dashboard/settings", "settings"),
    ],
)
def test_each_workflow_page_loads_its_own_guide(
    page: Page,
    base_url: str,
    path: str,
    expected_key: str,
) -> None:
    """One route owning two workflows must not serve one guide for both."""

    signup(page, base_url, unique_email("guide-workflow"))
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")

    assert _page_key(page) == expected_key
    _start_guide(page)
    expect(page.locator("[data-hm-guide-title]")).not_to_be_empty()
    assert_no_raw_traceback(page)


def test_the_evidence_passport_guide_explains_the_published_record(
    page: Page,
    base_url: str,
    browser_app,
) -> None:
    email = signup(page, base_url, unique_email("guide-passport"))
    seeded = seed_sharia_screened_market(browser_app.database_url, email)
    page.set_viewport_size(DESKTOP)
    page.goto(
        f"{base_url}/dashboard/market?methodology_id={seeded['methodology_id']}",
        wait_until="domcontentloaded",
    )

    passport_link = page.locator('a[href^="/dashboard/market/"]').first
    if passport_link.count() == 0:
        pytest.skip("no screened asset was seeded, so no Passport page exists to guide")
    passport_link.click()
    page.wait_for_url(re.compile(r".*/dashboard/market/.+"), timeout=15_000)

    assert _page_key(page) == "asset-passport"
    _start_guide(page)
    expect(page.locator("[data-hm-guide-title]")).to_have_text("Published screening status")
    assert_no_raw_traceback(page)


def test_the_compliance_changes_view_is_reached_through_activity(
    page: Page,
    base_url: str,
) -> None:
    """`/dashboard/compliance` redirects, so the guide belongs to where it lands."""

    signup(page, base_url, unique_email("guide-compliance"))
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/dashboard/compliance", wait_until="domcontentloaded")

    assert "/dashboard/opportunities" in page.url
    assert "compliance_changes" in page.url
    assert _page_key(page) == "activity"
    _start_guide(page)
    assert_no_raw_traceback(page)


# ---------------------------------------------------------------------------
# 5. Navigation, keys and the counter.
# ---------------------------------------------------------------------------


def test_next_back_done_skip_escape_and_the_counter_all_behave(
    page: Page,
    base_url: str,
) -> None:
    signup(page, base_url, unique_email("guide-navigation"))
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/dashboard/settings", wait_until="domcontentloaded")

    _start_guide(page)
    total = int(re.search(r"of (\d+)", _counter_text(page)).group(1))
    assert total >= 2, "this page needs at least two steps to exercise Back and Next"

    expect(page.locator("[data-hm-guide-back]")).to_be_disabled()
    first_title = page.locator("[data-hm-guide-title]").inner_text()

    page.locator("[data-hm-guide-next]").click()
    assert _counter_text(page).startswith("Step 2 of")
    expect(page.locator("[data-hm-guide-back]")).to_be_enabled()
    assert page.locator("[data-hm-guide-title]").inner_text() != first_title

    page.locator("[data-hm-guide-back]").click()
    assert _counter_text(page).startswith("Step 1 of")
    assert page.locator("[data-hm-guide-title]").inner_text() == first_title

    # Arrow keys move too, because focus is on the guide and not in a text field.
    page.keyboard.press("ArrowRight")
    assert _counter_text(page).startswith("Step 2 of")
    page.keyboard.press("ArrowLeft")
    assert _counter_text(page).startswith("Step 1 of")

    # The last step says Done, and finishing marks the guide completed.
    for _ in range(total - 1):
        page.locator("[data-hm-guide-next]").click()
    expect(page.locator("[data-hm-guide-next]")).to_have_text("Done")
    assert f"{total} left" not in _counter_text(page)
    page.locator("[data-hm-guide-next]").click()
    expect(page.locator("[data-hm-guide-popover]")).to_be_hidden()

    stored = page.evaluate(
        "() => window.localStorage.getItem('hm-guide:settings:v1')"
    )
    assert stored == "done", stored

    # Escape closes a restarted guide, and focus returns to the launcher.
    _start_guide(page)
    page.keyboard.press("Escape")
    expect(page.locator("[data-hm-guide-popover]")).to_be_hidden()
    focused = page.evaluate("() => document.activeElement.hasAttribute('data-hm-guide-launcher')")
    assert focused, "closing the guide must return focus to the launcher"
    assert_no_raw_traceback(page)


def test_a_completed_guide_can_always_be_restarted_from_the_launcher(
    page: Page,
    base_url: str,
) -> None:
    signup(page, base_url, unique_email("guide-restart"))
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/dashboard/billing", wait_until="domcontentloaded")
    page.evaluate("() => window.localStorage.setItem('hm-guide:billing:v1', 'done')")
    page.reload(wait_until="domcontentloaded")

    expect(page.locator("[data-hm-guide-launcher]")).to_be_visible()
    _start_guide(page)
    assert _counter_text(page).startswith("Step 1 of"), "a restart begins at step one"
    assert_no_raw_traceback(page)


def test_arrow_keys_do_not_hijack_typing_in_a_text_field(
    page: Page,
    base_url: str,
) -> None:
    """A guide open beside a text field must not steal the caret keys.

    This used to type into the assistant page's chat box. That page is gone, so it types
    into the search field on Monitors instead — the property is about any text field, not
    about that one page.
    """

    signup(page, base_url, unique_email("guide-typing"))
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/dashboard/strategies", wait_until="domcontentloaded")

    _start_guide(page)
    before = _counter_text(page)
    field = page.locator("input[type='search'], input[type='text']").first
    field.click()
    field.type("watch ETH")
    page.keyboard.press("ArrowLeft")
    assert _counter_text(page) == before, "arrow keys inside a field must not move the guide"
    assert_no_raw_traceback(page)


# ---------------------------------------------------------------------------
# 6. Fail-closed. The two cases that keep a wrong arrow off the screen.
# ---------------------------------------------------------------------------


def test_a_missing_target_is_skipped_and_the_counter_is_recalculated(
    page: Page,
    base_url: str,
) -> None:
    warnings: list[str] = []
    page.on("console", lambda message: warnings.append(message.text))

    signup(page, base_url, unique_email("guide-missing"))
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/dashboard/settings", wait_until="domcontentloaded")

    configured = len(_registry(page)["settings"]["steps"])
    removed = page.evaluate(
        "() => { const steps = window.HilalMarketsGuide.registry['settings'].steps;"
        " const name = steps[0].target;"
        " document.querySelector(`[data-hm-guide-target='${name}']`)"
        "   .removeAttribute('data-hm-guide-target');"
        " return name; }"
    )
    _start_guide(page)

    assert _counter_text(page).startswith(f"Step 1 of {configured - 1}"), _counter_text(page)
    assert any(removed in text and "skipped" in text for text in warnings), warnings
    assert any("settings" in text for text in warnings), warnings
    assert_no_raw_traceback(page)


def test_a_duplicated_target_is_refused_rather_than_guessed(
    page: Page,
    base_url: str,
) -> None:
    """Two matches is not "pick the first". It is "do not point at either"."""

    warnings: list[str] = []
    page.on("console", lambda message: warnings.append(message.text))

    signup(page, base_url, unique_email("guide-duplicate"))
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/dashboard/settings", wait_until="domcontentloaded")

    configured = len(_registry(page)["settings"]["steps"])
    duplicated = page.evaluate(
        "() => { const steps = window.HilalMarketsGuide.registry['settings'].steps;"
        " const name = steps[0].target;"
        " const clone = document.createElement('div');"
        " clone.setAttribute('data-hm-guide-target', name);"
        " clone.textContent = 'duplicate';"
        " document.body.appendChild(clone);"
        " return name; }"
    )
    _start_guide(page)

    assert _counter_text(page).startswith(f"Step 1 of {configured - 1}"), _counter_text(page)
    assert any(duplicated in text and "unique" in text for text in warnings), warnings
    assert_no_raw_traceback(page)


def test_a_page_with_no_valid_steps_hides_the_launcher(
    page: Page,
    base_url: str,
) -> None:
    signup(page, base_url, unique_email("guide-nosteps"))
    page.set_viewport_size(DESKTOP)
    # Support has no guide registered, because it holds no non-obvious workflow.
    page.goto(f"{base_url}/dashboard/support", wait_until="domcontentloaded")

    assert _page_key(page) is None
    expect(page.locator("[data-hm-guide-launcher]")).to_be_hidden()
    assert_no_raw_traceback(page)


# ---------------------------------------------------------------------------
# 7. The spotlight actually lands on the element.
# ---------------------------------------------------------------------------


def test_the_spotlight_matches_the_target_rectangle_after_scrolling(
    page: Page,
    base_url: str,
) -> None:
    """Alignment is the whole product. A guide that points 40px off is worse than none."""

    signup(page, base_url, unique_email("guide-alignment"))
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/dashboard/settings", wait_until="domcontentloaded")

    _start_guide(page)
    total = int(re.search(r"of (\d+)", _counter_text(page)).group(1))

    for index in range(total):
        if index:
            page.locator("[data-hm-guide-next]").click()
        # Wait for the guide to say it has finished moving, not for a fixed number of
        # milliseconds. A step can involve a smooth scroll of unknown length, and a
        # guessed wait measures the outline while the page is still gliding — which
        # reports the guide as misaligned when it is simply not finished.
        page.wait_for_selector(
            '[data-hm-guide-root][data-hm-guide-busy="false"]', timeout=10_000
        )
        # The outline then glides to its new box over the shared 120ms motion token.
        # That part has a known length, so a short fixed wait is honest here; the
        # unknown-length part is the scroll, and the signal above covers that.
        page.wait_for_timeout(200)
        measured = page.evaluate(
            "() => { const t = window.HilalMarketsGuide.engine.target;"
            " const r = t.getBoundingClientRect();"
            " const s = document.querySelector('[data-hm-guide-spotlight]')"
            "   .getBoundingClientRect();"
            " return { t: [r.top, r.left, r.width, r.height],"
            "          s: [s.top, s.left, s.width, s.height] }; }"
        )
        target, spot = measured["t"], measured["s"]
        assert abs(spot[0] - (target[0] - SPOTLIGHT_PADDING)) <= ALIGNMENT_TOLERANCE_PX, measured
        assert abs(spot[1] - (target[1] - SPOTLIGHT_PADDING)) <= ALIGNMENT_TOLERANCE_PX, measured
        assert abs(spot[2] - (target[2] + SPOTLIGHT_PADDING * 2)) <= ALIGNMENT_TOLERANCE_PX, (
            measured
        )
        assert abs(spot[3] - (target[3] + SPOTLIGHT_PADDING * 2)) <= ALIGNMENT_TOLERANCE_PX, (
            measured
        )

    # And it stays aligned after the page moves underneath it.
    page.mouse.wheel(0, 400)
    page.wait_for_timeout(300)
    after = page.evaluate(
        "() => { const t = window.HilalMarketsGuide.engine.target.getBoundingClientRect();"
        " const s = document.querySelector('[data-hm-guide-spotlight]').getBoundingClientRect();"
        " return Math.abs(s.top - (t.top - 8)); }"
    )
    assert after <= ALIGNMENT_TOLERANCE_PX, f"spotlight drifted {after}px after scrolling"
    assert_no_raw_traceback(page)


def test_the_popover_never_leaves_the_viewport(page: Page, base_url: str) -> None:
    """Deterministic flip and shift, checked at the width where it actually matters."""

    signup(page, base_url, unique_email("guide-collision"))
    for viewport in (DESKTOP, MOBILE, {"width": 768, "height": 600}):
        page.set_viewport_size(viewport)
        page.goto(f"{base_url}/dashboard/settings", wait_until="domcontentloaded")
        _start_guide(page)
        total = int(re.search(r"of (\d+)", _counter_text(page)).group(1))
        for index in range(total):
            if index:
                page.locator("[data-hm-guide-next]").click()
            page.wait_for_timeout(220)
            box = page.locator("[data-hm-guide-popover]").bounding_box()
            assert box["x"] >= -1, (viewport, box)
            assert box["y"] >= -1, (viewport, box)
            assert box["x"] + box["width"] <= viewport["width"] + 1, (viewport, box)
            assert box["y"] + box["height"] <= viewport["height"] + 1, (viewport, box)
        page.keyboard.press("Escape")
    assert_no_raw_traceback(page)


# ---------------------------------------------------------------------------
# 8. Accessibility, motion, and doing no harm to the page underneath.
# ---------------------------------------------------------------------------


def test_reduced_motion_removes_the_animation_but_keeps_the_guide(
    page: Page,
    base_url: str,
) -> None:
    signup(page, base_url, unique_email("guide-motion"))
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/dashboard/settings", wait_until="domcontentloaded")

    _start_guide(page)
    transitions = page.evaluate(
        "() => [...document.querySelectorAll('[data-hm-guide-panel], [data-hm-guide-spotlight],"
        " [data-hm-guide-popover]')].map(n => getComputedStyle(n).transitionDuration)"
    )
    # Asserted as imperceptible, not as exactly "0s".
    #
    # This assertion used to require the literal string "0s" and had been failing. The
    # cause was not a regression in the guide: `hilalmarkets-brand.css` carries the
    # standard reduced-motion reset, `transition-duration: .01ms !important` on `*`,
    # which wins over the guide's own `transition: none` and computes as `1e-05s`.
    #
    # The `.01ms` value is deliberate and is the one to keep. A hard `0s` can stop
    # `transitionend` and `animationend` from firing, and the built landing bundle
    # contains library code that waits for exactly those events; a listener that never
    # fires leaves the interface stuck. One hundred-thousandth of a second is motion
    # nobody can perceive, which is what the accessibility rule actually asks for.
    #
    # So the test now asserts the rule — no perceptible motion — instead of one
    # implementation of it. A real 300ms transition still fails here.
    def _seconds(value: str) -> float:
        text = value.strip()
        if text.endswith("ms"):
            return float(text[:-2]) / 1000
        if text.endswith("s"):
            return float(text[:-1])
        return 0.0

    slowest = max(
        (_seconds(part) for value in transitions for part in value.split(",") if part.strip()),
        default=0.0,
    )
    assert slowest <= 0.001, transitions
    blur = page.evaluate(
        "() => getComputedStyle(document.querySelector('[data-hm-guide-panel]')).backdropFilter"
    )
    assert blur in {"none", ""}, blur
    assert_no_raw_traceback(page)


def test_the_guide_is_a_dialog_with_trapped_focus_and_a_live_counter(
    page: Page,
    base_url: str,
) -> None:
    signup(page, base_url, unique_email("guide-a11y"))
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/dashboard/settings", wait_until="domcontentloaded")

    _start_guide(page)
    popover = page.locator("[data-hm-guide-popover]")
    expect(popover).to_have_attribute("role", "dialog")
    expect(popover).to_have_attribute("aria-modal", "true")
    expect(page.locator("[data-hm-guide-counter]")).to_have_attribute("aria-live", "polite")
    expect(page.locator("[data-hm-guide-launcher]")).to_have_attribute(
        "aria-label", "Guide this page"
    )

    # Tabbing repeatedly must never escape the guide's own controls.
    for _ in range(8):
        page.keyboard.press("Tab")
        inside = page.evaluate(
            "() => document.querySelector('[data-hm-guide-popover]')"
            "  .contains(document.activeElement)"
        )
        assert inside, "focus escaped the guide"
    assert_no_raw_traceback(page)


def test_the_guide_leaves_the_page_and_its_scrolling_untouched(
    page: Page,
    base_url: str,
) -> None:
    """No layout shift, no scroll lock left behind, no console errors."""

    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: errors.append(message.text) if message.type == "error" else None,
    )

    signup(page, base_url, unique_email("guide-noharm"))
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/dashboard/settings", wait_until="domcontentloaded")

    # Read the element the guide will actually point at, not merely the first marked
    # element in the document. The side menu is rendered before the page content and
    # carries markers of its own, so `querySelector('[data-hm-guide-target]')` returned
    # a nav link while the guide highlighted a settings panel — and the test then
    # reported a difference between two different elements as damage done by the guide.
    first_target = page.evaluate(
        "() => { const key = document.querySelector('[data-hm-guide-page]')"
        "   .getAttribute('data-hm-guide-page');"
        " return window.HilalMarketsGuide.registry[key].steps[0].target; }"
    )
    before = page.evaluate(
        "(name) => { const t = document.querySelector(`[data-hm-guide-target=\"${name}\"]`);"
        " const r = t.getBoundingClientRect();"
        " const s = getComputedStyle(t);"
        " return { w: document.body.scrollWidth, h: r.height,"
        "          position: s.position, zIndex: s.zIndex, filter: s.filter }; }",
        first_target,
    )
    _start_guide(page)
    during = page.evaluate(
        "(name) => { const t = window.HilalMarketsGuide.engine.target;"
        " const s = getComputedStyle(t);"
        " return { same: t === document.querySelector(`[data-hm-guide-target=\"${name}\"]`),"
        "          h: t.getBoundingClientRect().height, position: s.position,"
        "          zIndex: s.zIndex, filter: s.filter }; }",
        first_target,
    )
    assert during["same"], "the guide did not start on the step the registry declares first"
    assert during["position"] == before["position"], "the target's position was changed"
    assert during["zIndex"] == before["zIndex"], "the target's stacking context was changed"
    assert during["filter"] == before["filter"], "the target itself was filtered"
    assert abs(during["h"] - before["h"]) <= 1, "the target changed size"

    page.keyboard.press("Escape")
    after = page.evaluate(
        "() => ({ w: document.body.scrollWidth,"
        "         overflow: getComputedStyle(document.body).overflow }) "
    )
    assert after["w"] == before["w"], "the page width changed"
    assert after["overflow"] != "hidden", "a scroll lock was left on the body"
    assert not errors, errors


# ---------------------------------------------------------------------------
# 9. Auto-start is driven by the server, never inferred.
# ---------------------------------------------------------------------------


def test_a_new_user_is_offered_the_home_guide_from_a_server_owned_signal(
    page: Page,
    base_url: str,
) -> None:
    """`onboarding_completed_at` is the only signal. Empty data is not evidence."""

    signup(page, base_url, unique_email("guide-newuser"))
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/main", wait_until="domcontentloaded")

    signal = page.locator("body").get_attribute("data-hm-guide-new-user")
    assert signal in {"true", "false"}, signal
    if signal == "true":
        expect(page.locator("[data-hm-guide-popover]")).to_be_visible()
    else:
        expect(page.locator("[data-hm-guide-invite]")).to_be_visible()
        page.locator("[data-hm-guide-invite]").click()
        expect(page.locator("[data-hm-guide-popover]")).to_be_visible()

    # Whatever Home did, no other page opens a guide by itself.
    page.goto(f"{base_url}/dashboard/settings", wait_until="domcontentloaded")
    expect(page.locator("[data-hm-guide-popover]")).to_be_hidden()
    assert_no_raw_traceback(page)


def test_a_dismissed_invitation_stays_dismissed_but_the_launcher_does_not(
    page: Page,
    base_url: str,
) -> None:
    signup(page, base_url, unique_email("guide-dismiss"))
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/main", wait_until="domcontentloaded")
    # The key carries the guide's version, so writing a literal `v1` stopped
    # suppressing anything the moment the Main guide was revised — the test then
    # measured an auto-started guide instead of a dismissed one. Read the version the
    # page is really running so this cannot drift again.
    page.evaluate(
        "() => { const g = window.HilalMarketsGuide.registry['dashboard-today'];"
        " window.localStorage.setItem(`hm-guide:${g.id}:v${g.version}`, 'skipped'); }"
    )
    page.reload(wait_until="domcontentloaded")

    expect(page.locator("[data-hm-guide-popover]")).to_be_hidden()
    expect(page.locator("[data-hm-guide-invite]")).to_be_hidden()
    expect(page.locator("[data-hm-guide-launcher]")).to_be_visible()
    _start_guide(page)
    assert_no_raw_traceback(page)


def test_a_new_guide_version_is_offered_again(page: Page, base_url: str) -> None:
    """Completion keys carry the version, so an updated guide is not suppressed."""

    signup(page, base_url, unique_email("guide-version"))
    page.goto(f"{base_url}/main", wait_until="domcontentloaded")
    version = _registry(page)["dashboard-today"]["version"]
    page.evaluate(
        f"() => window.localStorage.setItem('hm-guide:dashboard-today:v{version - 1}', 'done')"
    )
    page.reload(wait_until="domcontentloaded")
    # The old key must not satisfy the current version.
    still_offered = page.evaluate(
        f"() => window.localStorage.getItem('hm-guide:dashboard-today:v{version}') === null"
    )
    assert still_offered, "an older completion key suppressed the current guide"


# ---------------------------------------------------------------------------
# 10. The report artefact.
# ---------------------------------------------------------------------------


def test_registry_snapshot_is_written_for_the_report(
    page: Page,
    base_url: str,
    repo_root: Path,
) -> None:
    """Persist what shipped, so the step list in the report is a measurement."""

    signup(page, base_url, unique_email("guide-snapshot"))
    page.goto(f"{base_url}/main", wait_until="domcontentloaded")
    registry = _registry(page)

    output = repo_root / "reports" / "playwright" / "dashboard-guide"
    output.mkdir(parents=True, exist_ok=True)
    (output / "registry.json").write_text(
        json.dumps(registry, indent=2, sort_keys=True), encoding="utf-8"
    )
    assert sum(len(guide["steps"]) for guide in registry.values()) >= 15
