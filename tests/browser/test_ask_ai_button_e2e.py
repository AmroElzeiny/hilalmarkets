"""The Ask AI button in a real browser.

Only a browser can measure the animation, the focus move, and the rendered box height.
The shared `page` fixture fails the test on any console error, page error, or failed
request, so runtime problems are caught by the harness.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import close_any_open_guide, signup

MARKET = "/dashboard/market"


def _open_market(page: Page, base_url: str) -> None:
    signup(page, base_url)
    close_any_open_guide(page)
    page.goto(f"{base_url}{MARKET}", wait_until="domcontentloaded")
    close_any_open_guide(page)


@pytest.mark.parametrize(
    "viewport",
    [
        pytest.param({"width": 1440, "height": 900}, id="v1_desktop"),
        pytest.param({"width": 1024, "height": 768}, id="v2_small_laptop"),
        pytest.param({"width": 390, "height": 844}, id="v3_phone"),
    ],
)
def test_ask_ai_button_opens_hilal_and_meets_target_size(
    page: Page, base_url: str, viewport: dict[str, int]
) -> None:
    page.set_viewport_size(viewport)
    _open_market(page, base_url)

    button = page.locator("[data-hilal-ask]")
    expect(button).to_be_visible()
    expect(button).to_have_attribute("aria-haspopup", "dialog")
    expect(button).to_have_attribute("aria-controls", "hilal-window")
    expect(button).not_to_have_attribute("aria-expanded", "")
    label = button.get_attribute("aria-label") or ""
    assert label.startswith("Ask AI"), f"accessible name {label!r} does not start with 'Ask AI'"

    box = button.bounding_box()
    assert box is not None
    assert box["height"] >= 44, f"button height is {box['height']}, below 44px"

    computed = page.evaluate(
        """selector => {
          const el = document.querySelector(selector);
          const style = getComputedStyle(el);
          return {
            animationName: style.animationName,
            animationPlayState: style.animationPlayState,
          };
        }""",
        "[data-hilal-ask]",
    )
    assert computed["animationName"] == "hm-ask-breathe", computed

    button.click()
    window = page.locator("[data-hilal-window]")
    expect(window).to_be_visible()
    focused_inside = page.evaluate(
        """() => {
          const win = document.querySelector('[data-hilal-window]');
          return win && win.contains(document.activeElement);
        }"""
    )
    assert focused_inside, "focus did not move inside the Hilal window"


def test_ask_ai_button_animation_pauses_on_interaction(
    page: Page, base_url: str
) -> None:
    _open_market(page, base_url)
    button = page.locator("[data-hilal-ask]")
    expect(button).to_be_visible()

    button.hover()
    page.wait_for_timeout(100)
    paused_hover = page.evaluate(
        """selector => getComputedStyle(document.querySelector(selector)).animationPlayState""",
        "[data-hilal-ask]",
    )
    assert paused_hover == "paused", paused_hover

    button.focus()
    paused_focus = page.evaluate(
        """selector => getComputedStyle(document.querySelector(selector)).animationPlayState""",
        "[data-hilal-ask]",
    )
    assert paused_focus == "paused", paused_focus


def test_ask_ai_button_is_static_under_reduced_motion(
    page: Page, base_url: str
) -> None:
    page.emulate_media(reduced_motion="reduce")
    _open_market(page, base_url)
    button = page.locator("[data-hilal-ask]")
    expect(button).to_be_visible()

    computed = page.evaluate(
        """selector => {
          const style = getComputedStyle(document.querySelector(selector));
          return { animationName: style.animationName, animation: style.animation };
        }""",
        "[data-hilal-ask]",
    )
    assert computed["animationName"] == "none", computed
