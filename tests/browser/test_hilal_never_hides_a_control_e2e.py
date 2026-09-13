"""The corner assistant must never sit on top of a control somebody is using.

Hilal is fixed to the bottom-right corner of every signed-in page, above the page. Measured
before this file existed: on Settings at 390 wide its round button covered the Timing
group's own "Ask AI" pill; at 1024 wide its label covered the Methodology pill on the
market page and the "Open the list" button on Create monitor; on a phone its button
covered the "A coin jumps / drops" chips. A person tabbing through the page landed on
controls they could not see.

The rule is WCAG 2.2 SC 2.4.11, Focus Not Obscured: a control that has keyboard focus is
not hidden by author content. It is asserted for **every** focusable control on each page,
at the three widths the product is designed for, not for the four that were reported —
and for the last control on the page, which must be reachable by scrolling alone.

"Covered" is decided the way the eye decides it: where the control and a visible part of
the corner widget overlap, the topmost element at that point belongs to the widget.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

from ai_market_monitor.core.dashboard_paths import (
    MARKET_PATH,
    MONITOR_PATH,
    OPPORTUNITIES_PATH,
    SETTINGS_PATH,
    SUBSCRIPTION_PATH,
)
from tests.browser.conftest import assert_no_raw_traceback, close_any_open_guide, signup

SURFACES = (MARKET_PATH, MONITOR_PATH, OPPORTUNITIES_PATH, SETTINGS_PATH, SUBSCRIPTION_PATH)
_SCREENS = Path(__file__).resolve().parents[2] / "test-results" / "browser" / "corner-clearance"
_SCREENS.mkdir(parents=True, exist_ok=True)
VIEWPORTS = {
    "1440x900": {"width": 1440, "height": 900},
    "1024x768": {"width": 1024, "height": 768},
    "390x844": {"width": 390, "height": 844},
}

#: Shared by both checks: which parts of the widget are visible, and whether the topmost
#: element over a rectangle's overlap with them belongs to the widget.
_COVERED_BY_WIDGET = """
const root = document.querySelector('.hm-hilal');
const parts = [...root.querySelectorAll('[data-hilal-tag], [data-hilal-open], .hm-ask-tag')]
  .map(part => part.getBoundingClientRect())
  .filter(box => box.width > 0 && box.height > 0);
const coveredBy = (box) => {
  for (const part of parts) {
    const x1 = Math.max(box.left, part.left), x2 = Math.min(box.right, part.right);
    const y1 = Math.max(box.top, part.top), y2 = Math.min(box.bottom, part.bottom);
    if (x2 - x1 < 1 || y2 - y1 < 1) continue;
    const hit = document.elementFromPoint((x1 + x2) / 2, (y1 + y2) / 2);
    if (hit && root.contains(hit)) return true;
  }
  return false;
};
const FOCUSABLE = 'a[href], button, input:not([type="hidden"]), select, textarea, summary, '
  + '[tabindex]:not([tabindex="-1"])';
const OUT_OF_PLAY = '[hidden], [inert], [aria-hidden="true"], dialog:not([open]), '
  + '[data-cookie-banner], [data-cookie-modal], [aria-modal="true"]';
const name = (el) => `${el.tagName.toLowerCase()} "${(el.innerText || el.value
  || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').slice(0, 50)}"`;
const controls = () => [...document.querySelectorAll(FOCUSABLE)].filter(el => {
  if (root.contains(el) || el.disabled || el.closest(OUT_OF_PLAY)) return false;
  const style = getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  const box = el.getBoundingClientRect();
  return box.width > 0 && box.height > 0;
});
"""

#: The dashboard scrolls smoothly, so focusing a control that is off screen starts a
#: scroll that finishes frames later. A control is judged where it comes to rest; reading
#: it at once saw it still off screen and skipped it, which is how a first draft of this
#: test passed controls it never looked at.
_FOCUSED_CONTROLS_HIDDEN = (
    "async () => {"
    + _COVERED_BY_WIDGET
    + """
  const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
  const settle = async () => {
    let last = null, still = 0;
    for (let frames = 0; frames < 120 && still < 4; frames += 1) {
      await frame();
      still = window.scrollY === last ? still + 1 : 0;
      last = window.scrollY;
    }
  };
  const hidden = [];
  for (const el of controls()) {
    el.focus();
    if (document.activeElement !== el) continue;
    await settle();
    const box = el.getBoundingClientRect();
    if (box.bottom <= 0 || box.top >= window.innerHeight) continue;
    if (coveredBy(box)) hidden.push(name(el));
  }
  if (document.activeElement) document.activeElement.blur();
  return { checked: controls().length, hidden };
}"""
)

_LAST_CONTROL_HIDDEN_AT_THE_BOTTOM = (
    "() => {"
    + _COVERED_BY_WIDGET
    + """
  window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'instant' });
  const all = controls();
  const last = all[all.length - 1];
  if (!last) return null;
  return coveredBy(last.getBoundingClientRect()) ? name(last) : null;
}"""
)


def _answer_the_cookie_question(page: Page) -> None:
    """The banner lifts the widget while it shows; the normal state is after the answer."""

    essential = page.locator("[data-cookie-banner] [data-cookie-essential]")
    if essential.count() and essential.first.is_visible():
        essential.first.click()


def _open(page: Page, base_url: str, path: str) -> None:
    page.goto(f"{base_url}{path}", wait_until="load")
    close_any_open_guide(page)
    _answer_the_cookie_question(page)
    page.locator("[data-hilal-open]").wait_for(state="visible")
    assert_no_raw_traceback(page)
    # Let late layout settle: charts, lazy lists and the widget's own measurement.
    page.wait_for_timeout(600)


@pytest.mark.parametrize("viewport", list(VIEWPORTS), ids=list(VIEWPORTS))
def test_no_focused_control_is_hidden_behind_the_corner_assistant(
    page: Page, base_url: str, viewport: str
) -> None:
    page.set_viewport_size(VIEWPORTS[viewport])
    signup(page, base_url)
    close_any_open_guide(page)

    hidden_by_page: dict[str, list[str]] = {}
    checked_by_page: dict[str, int] = {}
    for path in SURFACES:
        _open(page, base_url, path)
        result = page.evaluate(_FOCUSED_CONTROLS_HIDDEN)
        checked_by_page[path] = result["checked"]
        if result["hidden"]:
            hidden_by_page[path] = result["hidden"]
        last = page.evaluate(_LAST_CONTROL_HIDDEN_AT_THE_BOTTOM)
        if last:
            hidden_by_page.setdefault(path, []).append(f"last control at the bottom: {last}")
        # Evidence for the vision review: the end of the page, where the last controls
        # have to sit clear of the corner widget.
        page.screenshot(path=str(_SCREENS / f"{viewport}{path.replace('/', '-')}-bottom.png"))

    # Self-check: a page that yielded no controls would pass without testing anything.
    assert all(count > 0 for count in checked_by_page.values()), checked_by_page
    assert hidden_by_page == {}, (
        f"At {viewport}, the corner assistant covers controls that have focus: {hidden_by_page}"
    )
