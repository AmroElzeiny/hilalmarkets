"""The "Ask AI" label above the round assistant button, and the Ask AI surfaces, in a browser.

Only a browser can say where a label really sits, what colour it really paints, whether
it breathes, and whether it collides with the back-to-top button in the same corner. The
stylesheet says what was meant; these tests measure what was drawn.

The contract is `.hm-orchestrator/current/VISUAL_CONTRACT.md`, section "The corner label".
The shared `page` fixture fails a test on any console error, page error or failed request.

The last two tests photograph the four Ask AI surfaces and the subscription page at the
three contract viewports, plus reduced motion, for the vision review. Each state is
asserted before it is photographed: a screenshot of a broken page is not evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import (
    assert_no_horizontal_overflow,
    close_any_open_guide,
    seed_setup_observability,
    signup,
)

RUN = Path(".hm-orchestrator/runs/20260911-wp8-finish")
SCREENS = RUN / "screens"

V1 = {"width": 1440, "height": 900}
V2 = {"width": 1024, "height": 768}
V3 = {"width": 390, "height": 844}
VIEWPORTS = [
    pytest.param(V1, id="v1_desktop"),
    pytest.param(V2, id="v2_small_laptop"),
    pytest.param(V3, id="v3_phone"),
]

#: `--hm-sky`, `--hm-sky-strong` and `--white`, as a browser reports them.
SKY = "rgb(14, 120, 175)"
SKY_STRONG = "rgb(10, 96, 134)"
WHITE = "rgb(255, 255, 255)"

LANDING_BUTTON = "[data-public-chat-launcher]"
DASHBOARD_BUTTON = ".hilal-orb"

ASK_AI_SURFACES = {
    "market": "/dashboard/market",
    "create-monitor": "/dashboard/create-monitor",
    "opportunities": "/dashboard/opportunities",
    "settings": "/dashboard/settings",
}

_MEASURE = """selector => {
  const button = document.querySelector(selector);
  const tag = button.querySelector(':scope > .hm-ask-tag');
  const b = button.getBoundingClientRect();
  const t = tag.getBoundingClientRect();
  const s = getComputedStyle(tag);
  return {
    button: {left: b.left, right: b.right, top: b.top, bottom: b.bottom},
    tag: {left: t.left, right: t.right, top: t.top, bottom: t.bottom, height: t.height},
    viewport: {width: window.innerWidth, height: window.innerHeight},
    text: tag.textContent.trim(),
    ariaHidden: tag.getAttribute('aria-hidden'),
    radius: parseFloat(s.borderTopLeftRadius),
    background: s.backgroundColor,
    color: s.color,
    display: s.display,
    animationName: s.animationName,
    playState: s.animationPlayState,
  };
}"""

#: Where everything in the corner is once a scroll to the bottom has come to rest, and
#: what could move it. The first pictures taken there showed the label and the circle
#: below the bottom edge of the screen, and the header pushed down, while every check
#: passed: none of them asked whether the button was still on the screen.
_AFTER_SCROLL = """selector => {
  const rect = el => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {top: r.top, bottom: r.bottom, left: r.left, right: r.right};
  };
  const button = document.querySelector(selector);
  const vv = window.visualViewport;
  return {
    button: rect(button),
    tag: rect(button && button.querySelector(':scope > .hm-ask-tag')),
    to_top: rect(document.querySelector('.hm-to-top')),
    header: rect(document.querySelector('header')),
    viewport: {width: window.innerWidth, height: window.innerHeight},
    visual_viewport: vv ? {top: vv.offsetTop, height: vv.height, scale: vv.scale} : null,
    scroll_y: window.scrollY,
    scroll_height: document.documentElement.scrollHeight,
    transforms: ['html', 'body', '#root']
      .map(s => [s, document.querySelector(s)])
      .filter(([, el]) => el)
      .map(([s, el]) => [s, getComputedStyle(el).transform]),
  };
}"""


def _wait_for_scroll_to_rest(page: Page) -> None:
    """The landing page scrolls smoothly, so `scrollTo` returns while the page still
    moves. At rest when two readings 100ms apart agree."""

    last = None
    for _ in range(50):
        now = page.evaluate("window.scrollY")
        if now == last:
            return
        last = now
        page.wait_for_timeout(100)
    raise AssertionError("the landing page never stopped scrolling")


def _record_after_scroll(size: str, found: dict[str, Any]) -> None:
    """Written before anything is asserted, so a failure still leaves its numbers."""

    path = RUN / "landing_corner_after_scroll.json"
    try:
        seen = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        seen = {}
    seen[size] = found
    RUN.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seen, indent=2), encoding="utf-8")


def _settle_cookie_choice(page: Page) -> None:
    banner = page.locator("[data-cookie-banner]")
    if banner.count() and banner.is_visible():
        page.locator("[data-cookie-essential]").first.click()
        expect(banner).to_be_hidden()


def _open_landing(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    _settle_cookie_choice(page)
    expect(page.locator(LANDING_BUTTON)).to_be_visible(timeout=15_000)
    # Nothing may be hovering the button: a pointer on it pauses the label and deepens it.
    page.mouse.move(1, 1)


def _open_dashboard(page: Page, base_url: str, path: str) -> None:
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    close_any_open_guide(page)
    _settle_cookie_choice(page)
    page.mouse.move(1, 1)


def _measure(page: Page, selector: str) -> dict[str, Any]:
    return page.evaluate(_MEASURE, selector)


def _assert_the_label(m: dict[str, Any]) -> None:
    """Every rule the owner asked for, measured on the drawn box."""

    assert m["text"] == "Ask AI", m
    assert m["ariaHidden"] == "true", "the label repeats the button's name to screen readers"
    assert m["display"] != "none", m
    # A rounded rectangle, not a pill: a pill's corners are half its height.
    assert 0 < m["radius"] <= 12, f"corner radius {m['radius']}px is not a rounded rectangle"
    assert m["radius"] < m["tag"]["height"] / 2, m
    assert m["background"] == SKY, m
    assert m["color"] == WHITE, m
    assert m["animationName"] == "hm-ask-tag-breathe", m
    assert m["playState"] == "running", m

    # Above the circle, with its 10px of air (the scale grows upward from the bottom edge,
    # so the gap does not move while it breathes).
    gap = m["button"]["top"] - m["tag"]["bottom"]
    assert 8 <= gap <= 12, f"the label is {gap:.1f}px above the circle, not 10px"
    # Centred on the circle.
    button_centre = (m["button"]["left"] + m["button"]["right"]) / 2
    tag_centre = (m["tag"]["left"] + m["tag"]["right"]) / 2
    assert abs(button_centre - tag_centre) <= 1.5, (button_centre, tag_centre)
    # Wholly on the screen.
    assert m["tag"]["top"] >= 0 and m["tag"]["left"] >= 0, m
    assert m["tag"]["right"] <= m["viewport"]["width"], m


def _shoot(page: Page, name: str, *, full_page: bool = False) -> Path:
    SCREENS.mkdir(parents=True, exist_ok=True)
    path = SCREENS / f"{name}.png"
    page.screenshot(path=str(path), full_page=full_page)
    return path


# ── The label, on the landing page ───────────────────────────────────────────


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_the_landing_page_corner_button_wears_the_ask_ai_label(
    page: Page, base_url: str, viewport: dict[str, int]
) -> None:
    """The landing page, named in the request, at every contract viewport.

    Back to top shares the corner. It must sit above the label once it appears, never
    on it: the two were drawn by different sheets that knew nothing of each other.
    """

    page.set_viewport_size(viewport)
    _open_landing(page, base_url)
    _assert_the_label(_measure(page, LANDING_BUTTON))
    _shoot(page, f"landing-corner-{viewport['width']}x{viewport['height']}")

    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_function(
        """() => {
          const b = document.querySelector('.hm-to-top');
          return b && getComputedStyle(b).opacity === '1';
        }""",
        timeout=10_000,
    )
    _wait_for_scroll_to_rest(page)
    page.wait_for_timeout(300)
    after = page.evaluate(_AFTER_SCROLL, LANDING_BUTTON)
    _record_after_scroll(f"{viewport['width']}x{viewport['height']}", after)
    # At the bottom of the page the circle and its label are still wholly on the screen.
    assert after["tag"]["top"] >= 0, after
    assert after["button"]["bottom"] <= after["viewport"]["height"], after
    to_top = page.locator(".hm-to-top").first.bounding_box()
    label = page.locator(f"{LANDING_BUTTON} > .hm-ask-tag").bounding_box()
    assert to_top is not None and label is not None
    assert to_top["y"] + to_top["height"] <= label["y"], (
        f"back to top ({to_top}) overlaps the Ask AI label ({label})"
    )
    _shoot(page, f"landing-corner-with-back-to-top-{viewport['width']}x{viewport['height']}")


def test_the_label_holds_still_and_deepens_under_a_pointer(page: Page, base_url: str) -> None:
    _open_landing(page, base_url)
    page.locator(LANDING_BUTTON).hover()
    page.wait_for_timeout(250)
    m = _measure(page, LANDING_BUTTON)
    assert m["playState"] == "paused", m
    assert m["background"] == SKY_STRONG, m


def test_a_click_on_the_label_opens_the_assistant(page: Page, base_url: str) -> None:
    """The label sits outside the circle. A click on it must still reach the button."""

    _open_landing(page, base_url)
    label = page.locator(f"{LANDING_BUTTON} > .hm-ask-tag")
    label.click()
    expect(page.locator("[data-public-chat-panel]")).to_be_visible()
    # Once the window is open the button is the way back, and the label goes.
    expect(page.locator(LANDING_BUTTON)).to_have_attribute("aria-expanded", "true")
    expect(label).to_be_hidden()


def test_the_label_is_still_for_somebody_who_asked_for_less_motion(
    page: Page, base_url: str
) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size(V1)
    _open_landing(page, base_url)
    m = _measure(page, LANDING_BUTTON)
    assert m["animationName"] == "none", m
    assert m["text"] == "Ask AI" and m["background"] == SKY, m
    _shoot(page, "landing-corner-reduced-motion-1440x900")


# ── The label, on the dashboard ──────────────────────────────────────────────


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_the_dashboard_corner_orb_wears_the_same_label(
    page: Page, base_url: str, viewport: dict[str, int]
) -> None:
    signup(page, base_url)
    close_any_open_guide(page)
    page.set_viewport_size(viewport)
    for surface in ("market", "settings"):
        _open_dashboard(page, base_url, ASK_AI_SURFACES[surface])
        expect(page.locator(DASHBOARD_BUTTON)).to_be_visible(timeout=15_000)
        _assert_the_label(_measure(page, DASHBOARD_BUTTON))
    _shoot(page, f"dashboard-corner-{viewport['width']}x{viewport['height']}")


# ── Screenshots for the vision review ────────────────────────────────────────

_ROW = """() => {
  const bar = document.querySelector('div.t-controls');
  const ask = bar && bar.querySelector('[data-hilal-ask]');
  if (!ask) return null;
  const top = el => Math.round(el.getBoundingClientRect().top);
  const others = [...bar.children].filter(el => el !== ask && el.offsetParent !== null);
  const askTop = top(ask);
  return {
    ask_top: askTop,
    others_tops: others.map(top),
    own_row: others.every(el => top(el) < askTop - 4),
    bar_width: Math.round(bar.getBoundingClientRect().width),
  };
}"""


def test_screenshots_of_the_ask_ai_surfaces_and_the_subscription_page(
    page: Page, base_url: str, browser_app: Any
) -> None:
    """Four Ask AI surfaces and the subscription page, at V1, V2 and V3.

    Also records, for each viewport, whether the Ask AI button in the Halal Assets bar
    lands on a row of its own, measured from the drawn boxes.

    The account is given findings first. Opportunities with nothing found draws its
    empty state, which has no filter bar and so no Ask AI button to photograph.
    """

    # Asserted rather than left to the seed helper, which skips when the run points at
    # an outside server: a missing screenshot must fail, never pass as skipped.
    assert browser_app.database_url, "these screenshots need the auto-started server"
    email = signup(page, base_url)
    close_any_open_guide(page)
    seed_setup_observability(browser_app.database_url, email)
    rows: dict[str, Any] = {}
    for viewport in (V1, V2, V3):
        size = f"{viewport['width']}x{viewport['height']}"
        page.set_viewport_size(viewport)
        for surface, path in ASK_AI_SURFACES.items():
            _open_dashboard(page, base_url, path)
            buttons = page.locator("[data-hilal-ask]")
            expect(buttons.first).to_be_visible(timeout=15_000)
            if surface == "settings":
                expect(buttons).to_have_count(8)
            for index in range(buttons.count()):
                box = buttons.nth(index).bounding_box()
                assert box is not None and box["height"] >= 44, (surface, size, box)
            assert_no_horizontal_overflow(page)
            if surface == "market":
                rows[size] = page.evaluate(_ROW)
            page.wait_for_timeout(300)
            _shoot(page, f"{surface}-{size}", full_page=True)

        _open_dashboard(page, base_url, "/dashboard/subscription")
        expect(page.locator("#s-plans")).to_be_visible(timeout=15_000)
        expect(page.locator("[data-hilal-ask]")).to_have_count(0)
        expect(page.locator("nav.a-jump")).to_have_count(0)
        assert_no_horizontal_overflow(page)
        page.wait_for_timeout(300)
        _shoot(page, f"subscription-{size}", full_page=True)

    (RUN / "halal_assets_ask_row.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8"
    )


def test_screenshot_of_the_ask_ai_button_under_reduced_motion(
    page: Page, base_url: str
) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size(V1)
    signup(page, base_url)
    close_any_open_guide(page)
    _open_dashboard(page, base_url, ASK_AI_SURFACES["market"])
    button = page.locator("[data-hilal-ask]")
    expect(button).to_be_visible(timeout=15_000)
    assert button.evaluate("el => getComputedStyle(el).animationName") == "none"
    assert _measure(page, DASHBOARD_BUTTON)["animationName"] == "none"
    _shoot(page, "market-reduced-motion-1440x900", full_page=True)
