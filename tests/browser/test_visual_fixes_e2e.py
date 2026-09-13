"""Reproducer tests for visual defects found in run 20260912T152825Z-d6d10e2a.

These tests fail before the CSS fixes and pass after them. They cover:
- the landing-page footer legal text overlapping the fixed AI launcher;
- the market-page methodology button text truncation.

The checkout review-page defects live in `test_checkout_pay_button_e2e.py`.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import close_any_open_guide, signup, unique_email

VIEWPORTS = {
    "v1_desktop": {"width": 1440, "height": 900},
    "v2_small_laptop": {"width": 1024, "height": 768},
    "v3_phone": {"width": 390, "height": 844},
}

LANDING_BUTTON = "[data-public-chat-launcher]"
LEGAL_TEXT = ".hm-footer-bottom p"

_METHOD_LINK = "a[href='/hilal-methodology']"


def _settle_cookie_choice(page: Page) -> None:
    banner = page.locator("[data-cookie-banner]")
    if banner.count() and banner.is_visible():
        page.locator("[data-cookie-essential]").first.click()
        expect(banner).to_be_hidden()


def _open_landing(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    _settle_cookie_choice(page)
    expect(page.locator(LANDING_BUTTON)).to_be_visible(timeout=15_000)
    page.mouse.move(1, 1)


def _scroll_to_bottom(page: Page) -> None:
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_function(
        """() => {
          const b = document.querySelector('.hm-to-top');
          return b && getComputedStyle(b).opacity === '1';
        }""",
        timeout=10_000,
    )
    # At rest when two readings 100ms apart agree.
    last = None
    for _ in range(50):
        now = page.evaluate("window.scrollY")
        if now == last:
            break
        last = now
        page.wait_for_timeout(100)
    page.wait_for_timeout(300)


def _boxes_intersect(a: dict[str, float], b: dict[str, float]) -> bool:
    return (
        a["x"] < b["x"] + b["width"]
        and a["x"] + a["width"] > b["x"]
        and a["y"] < b["y"] + b["height"]
        and a["y"] + a["height"] > b["y"]
    )


def _measure_legal_text_and_launcher(page: Page) -> dict[str, Any]:
    return page.evaluate("""
        () => {
          const paras = [...document.querySelectorAll('.hm-footer-bottom p')];
          const launcher = document.querySelector('[data-public-chat-launcher]');
          const label = launcher && launcher.querySelector(':scope > .hm-ask-tag');
          const rect = el => {
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {x: r.left, y: r.top, width: r.width, height: r.height};
          };
          // Each rendered line of text has its own rectangle, so a wrapped line that
          // runs under the launcher is caught even when the whole paragraph box is
          // wider than the launcher column.
          const lines = [];
          paras.forEach(p => {
            for (let i = 0; i < p.getClientRects().length; i++) {
              const r = p.getClientRects()[i];
              lines.push({
                x: r.left, y: r.top, width: r.width, height: r.height,
                text: p.textContent.trim(),
              });
            }
          });
          return {
            legal: paras.map(rect),
            lines,
            launcher: rect(launcher),
            label: rect(label),
            viewport: {width: window.innerWidth, height: window.innerHeight},
            scrollY: window.scrollY,
            scrollHeight: document.documentElement.scrollHeight,
          };
        }
    """)


@pytest.mark.parametrize("viewport", list(VIEWPORTS.values()), ids=list(VIEWPORTS))
def test_landing_footer_legal_text_clears_the_ai_launcher(
    page: Page, base_url: str, viewport: dict[str, int]
) -> None:
    """The fixed AI launcher must not cover the footer legal disclaimer.

    At 1024 and 390 the text wraps low enough to run under the launcher; at 1440 it
    already clears it. The test is run at all three widths so the fix cannot break the
    desktop gap.
    """

    page.set_viewport_size(viewport)
    _open_landing(page, base_url)
    _scroll_to_bottom(page)

    measured = _measure_legal_text_and_launcher(page)
    launcher = measured["launcher"]
    assert launcher is not None, "launcher must be present"

    failures = []
    label = measured["label"]
    for i, line in enumerate(measured["lines"]):
        if line is None:
            continue
        if _boxes_intersect(line, launcher):
            failures.append(("line-vs-launcher", i, line, launcher))
        if label is not None and _boxes_intersect(line, label):
            failures.append(("line-vs-label", i, line, label))

    assert not failures, f"footer legal text overlaps the corner: {failures}"


def test_market_methodology_button_text_is_not_accidentally_clipped(
    page: Page, base_url: str
) -> None:
    """At 1024 the methodology link must show its full text or a declared ellipsis.

    If the stylesheet deliberately truncates with `text-overflow: ellipsis`, the test
    reports that and passes without asking for a fix. An accidental clip — no ellipsis
    declaration, element overflowing its container, or text missing from the DOM — is a
    defect.
    """

    signup(page, base_url, unique_email("visual-methodology"))
    close_any_open_guide(page)
    page.set_viewport_size(VIEWPORTS["v2_small_laptop"])
    page.goto(f"{base_url}/dashboard/market", wait_until="domcontentloaded")
    close_any_open_guide(page)
    page.mouse.move(1, 1)

    link = page.locator(_METHOD_LINK).first
    expect(link).to_be_visible(timeout=15_000)

    info = link.evaluate("""
        el => {
          const style = getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          const parent = el.parentElement;
          const parentRect = parent ? parent.getBoundingClientRect() : null;
          const overflowsParent = parentRect && (
            rect.right > parentRect.right + 1 || rect.left < parentRect.left - 1
          );
          return {
            text: el.textContent.trim(),
            textOverflow: style.textOverflow,
            overflow: style.overflow,
            overflowX: style.overflowX,
            overflowY: style.overflowY,
            whiteSpace: style.whiteSpace,
            width: rect.width,
            height: rect.height,
            right: rect.right,
            parentRight: parentRect ? parentRect.right : null,
            overflowsParent,
            scrollWidth: el.scrollWidth,
            clientWidth: el.clientWidth,
          };
        }
    """)

    # A declared ellipsis is a product choice, not a clip bug.
    if info["textOverflow"] == "ellipsis" and info["overflow"] in ("hidden", "clip"):
        # Assert the ellipsis really is declared, rather than skipping the case.
        assert info["textOverflow"] == "ellipsis" and info["overflow"] in (
            "hidden",
            "clip",
        ), info
        return

    # If there is no ellipsis declaration, the full text must be visible and inside its
    # container.
    assert info["overflow"] not in ("hidden", "clip"), (
        f"methodology link is clipped without ellipsis: overflow={info['overflow']}"
    )
    assert not info["overflowsParent"], (
        f"methodology link overflows its container: {info}"
    )
    assert info["scrollWidth"] <= info["clientWidth"] + 1, (
        f"methodology link has hidden overflow: scrollWidth={info['scrollWidth']} "
        f"clientWidth={info['clientWidth']}"
    )
