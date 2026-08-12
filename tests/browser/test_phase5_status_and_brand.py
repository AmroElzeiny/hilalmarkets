"""Status banners, reduced motion, heading case and status-not-by-colour-alone.

Everything here is asserted in a real browser because every one of these properties
is invisible to a unit test. A banner can be present in the template and still be
unreadable at 390 pixels; a heading can be sentence case in the source and rendered in
capitals by a `text-transform`; an accessible name can exist in the markup and be
hidden from the accessibility tree by an `aria-hidden` on an ancestor.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page

from tests.browser.conftest import (
    assert_no_raw_traceback,
    signup,
    unique_email,
)

MOBILE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 1000}

#: Words a customer must never see attached to an AI or provider outage. Borrowing a
#: Shariah or compiler refusal to explain an unrelated failure teaches something false
#: about the part that was borrowed.
FORBIDDEN_IN_A_DEGRADATION_MESSAGE = ("shariah", "sharia", "compiler", "haram")


def _visible_headings(page: Page) -> list[str]:
    return page.evaluate(
        """() => [...document.querySelectorAll('h1, h2, h3')]
              .filter(node => node.offsetParent !== null)
              .map(node => ({
                  text: node.textContent.trim(),
                  transform: getComputedStyle(node).textTransform
              }))
              .filter(item => item.text.length > 0)
              .map(item => item.transform === 'uppercase'
                    ? item.text.toUpperCase()
                    : item.text)"""
    )


def _assert_headings_are_sentence_case(headings: list[str]) -> None:
    """Brand guide sections 11 and 19: no ALL CAPS, no Title Case.

    A heading is judged only on words long enough to have a case convention. Short
    words, acronyms and ticker symbols (BTC-USDT, API) are allowed capitals by the
    same brand rule, so counting them would fail every honest heading.
    """

    for heading in headings:
        letters = [character for character in heading if character.isalpha()]
        if len(letters) >= 8 and all(
            character.isupper() for character in letters
        ):
            raise AssertionError(f"ALL CAPS heading: {heading!r}")
        words = [
            word
            for word in re.findall(r"[A-Za-z][A-Za-z'’-]*", heading)
            if len(word) > 3 and word.upper() != word
        ]
        if len(words) < 4:
            continue
        capitalised = [word for word in words[1:] if word[0].isupper()]
        if len(capitalised) == len(words) - 1:
            raise AssertionError(f"Title Case heading: {heading!r}")


def _assert_status_is_not_colour_alone(page: Page) -> None:
    """Brand guide section 10: colour may never be the only carrier of meaning.

    Every status-bearing element must also carry words. Checked on the rendered page
    rather than the template because a `text-indent: -9999px` or an `aria-hidden`
    ancestor removes the text after the template has done everything right.
    """

    bare = page.evaluate(
        """() => [...document.querySelectorAll(
                '[data-status-banner], .badge, .notice, [data-status]')]
              .filter(node => node.offsetParent !== null)
              .filter(node => {
                  const label = (node.innerText || '').trim()
                      || (node.getAttribute('aria-label') || '').trim();
                  return label.length === 0;
              })
              .map(node => node.className || node.tagName)"""
    )
    assert bare == [], f"Status shown by colour alone: {bare}"


# --------------------------------------------------------------------------
# Status banners
# --------------------------------------------------------------------------


@pytest.mark.parametrize("viewport", [DESKTOP, MOBILE], ids=["1440", "390"])
def test_a_degradation_banner_renders_and_is_announced(
    page: Page, base_url: str, viewport: dict[str, int]
) -> None:
    """The banner must be visible, readable and in the accessibility tree.

    Forced through the test-only degradation hook rather than by breaking a real
    provider, so the assertion is about the banner, not about how the outage began.
    """

    signup(page, base_url, unique_email("phase5-banner"))
    page.set_viewport_size(viewport)
    page.goto(
        f"{base_url}/dashboard?force_status_banner=ai_unavailable",
        wait_until="domcontentloaded",
    )

    banner = page.locator("[data-status-banner]").first
    banner.wait_for(state="visible", timeout=10_000)

    # Announced politely: reduced service is not an emergency that should interrupt
    # whatever a screen-reader user is in the middle of reading.
    assert banner.get_attribute("role") == "status"
    assert banner.get_attribute("aria-live") == "polite"

    text = banner.inner_text().casefold()
    assert "still working" in text
    assert "paused" in text
    for forbidden in FORBIDDEN_IN_A_DEGRADATION_MESSAGE:
        assert forbidden not in text, f"{forbidden!r} blamed for an AI outage"

    box = banner.bounding_box()
    assert box is not None
    assert box["width"] <= viewport["width"] + 1
    assert box["x"] >= -1
    assert_no_raw_traceback(page)


def test_no_banner_is_shown_when_nothing_is_wrong(page: Page, base_url: str) -> None:
    """A banner on a healthy morning teaches customers to ignore banners."""

    signup(page, base_url, unique_email("phase5-nobanner"))
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/dashboard", wait_until="domcontentloaded")
    assert page.locator("[data-status-banner]").count() == 0
    assert_no_raw_traceback(page)


# --------------------------------------------------------------------------
# Reduced motion
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/features"])
def test_reduced_motion_is_honoured_on_public_pages(
    page: Page, base_url: str, path: str
) -> None:
    """Brand guide section 15: always provide a reduced-motion experience.

    Asserted as *imperceptible*, not as exactly zero. The site's reset uses
    `transition-duration: .01ms`, which is the deliberate and widely used value: a
    hard `0s` can stop `transitionend` firing, and the built landing bundle contains
    library code that waits for those events. One hundred-thousandth of a second is
    motion no one can see, so the rule is "nothing perceptible", and a real 300ms
    transition still fails this.
    """

    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")

    slowest = page.evaluate(
        """() => {
              const seconds = value => value.split(',')
                  .map(part => {
                      const text = part.trim();
                      if (text.endsWith('ms')) return parseFloat(text) / 1000;
                      return parseFloat(text) || 0;
                  });
              let worst = 0;
              for (const node of document.querySelectorAll('*')) {
                  const style = getComputedStyle(node);
                  for (const value of seconds(style.transitionDuration)) {
                      if (value > worst) worst = value;
                  }
                  for (const value of seconds(style.animationDuration)) {
                      if (value > worst) worst = value;
                  }
              }
              return worst;
          }"""
    )
    assert slowest <= 0.001, f"Perceptible motion under reduced-motion: {slowest}s"
    assert_no_raw_traceback(page)


def test_reduced_motion_keeps_the_content(page: Page, base_url: str) -> None:
    """Removing motion must not remove the page.

    The failure this guards against is a reveal animation whose starting state is
    `opacity: 0`: switch the animation off and the content never appears.
    """

    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/features", wait_until="domcontentloaded")
    invisible = page.evaluate(
        """() => [...document.querySelectorAll('h1, h2, p')]
              .filter(node => node.textContent.trim().length > 0)
              .filter(node => {
                  const style = getComputedStyle(node);
                  return style.opacity === '0' || style.visibility === 'hidden';
              }).length"""
    )
    assert invisible == 0
    assert_no_raw_traceback(page)


# --------------------------------------------------------------------------
# Heading case and status colour, on one public and one dashboard page
# --------------------------------------------------------------------------


def test_public_page_headings_and_status_follow_the_brand_rules(
    page: Page, base_url: str
) -> None:
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/features", wait_until="domcontentloaded")
    headings = _visible_headings(page)
    assert headings, "No visible headings found on the public page"
    _assert_headings_are_sentence_case(headings)
    _assert_status_is_not_colour_alone(page)
    assert_no_raw_traceback(page)


def test_dashboard_headings_and_status_follow_the_brand_rules(
    page: Page, base_url: str
) -> None:
    signup(page, base_url, unique_email("phase5-brand"))
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/dashboard", wait_until="domcontentloaded")
    headings = _visible_headings(page)
    assert headings, "No visible headings found on the dashboard"
    _assert_headings_are_sentence_case(headings)
    _assert_status_is_not_colour_alone(page)
    assert_no_raw_traceback(page)


def test_focus_is_visible_on_the_public_page(page: Page, base_url: str) -> None:
    """Brand guide section 18: provide visible focus states."""

    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/features", wait_until="domcontentloaded")
    page.keyboard.press("Tab")
    outline = page.evaluate(
        """() => {
              const node = document.activeElement;
              if (!node || node === document.body) return null;
              const style = getComputedStyle(node);
              return {
                  outlineWidth: style.outlineWidth,
                  outlineStyle: style.outlineStyle,
                  boxShadow: style.boxShadow
              };
          }"""
    )
    assert outline is not None, "Tab moved focus nowhere"
    has_outline = (
        outline["outlineStyle"] not in {"none", ""}
        and outline["outlineWidth"] not in {"0px", ""}
    )
    has_ring = outline["boxShadow"] not in {"none", ""}
    assert has_outline or has_ring, f"No visible focus state: {outline}"
    assert_no_raw_traceback(page)
