"""Photograph the methodology page, so the design can be looked at rather than assumed.

Not an assertion about taste. It writes full-page images at a phone width and a desktop
width into the scratch directory, and asserts only the things a picture cannot show: that
the sections are all present, that the rail tracks the reading, and that the warning is
above the fold on both.

Run it when the page changes:

    .venv/Scripts/python -m pytest tests/browser/test_methodology_page_look.py -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

SHOTS = Path(os.environ.get("HM_SHOT_DIR", "")) if os.environ.get("HM_SHOT_DIR") else None

SECTION_IDS = (
    "what",
    "how",
    "rules",
    "skipped",
    "answers",
    "coins",
    "others",
    "limits",
)


def _open(page: Page, base_url: str, *, still: bool = False) -> None:
    """Open the page. ``still`` asks the browser for reduced motion.

    A full-page screenshot never scrolls, so with the scroll reveal running every section
    below the fold is captured at `opacity: 0` and the picture is a hero above eight
    screens of nothing. Asking for reduced motion is not a trick to make the picture
    work — it is the state a real person with that preference gets, and the picture
    should be what they see.
    """

    if still:
        page.emulate_media(reduced_motion="reduce")
    page.goto(f"{base_url}/hilal-methodology", wait_until="domcontentloaded")
    page.wait_for_selector("#root h1", timeout=20_000)
    page.wait_for_function(
        "() => document.querySelectorAll('main svg').length > 5", timeout=15_000
    )


@pytest.mark.parametrize(("width", "height"), [(390, 844), (1440, 900)])
def test_the_page_is_whole_at_every_width(
    page: Page, base_url: str, width: int, height: int
) -> None:
    page.set_viewport_size({"width": width, "height": height})
    _open(page, base_url, still=True)

    for section in SECTION_IDS:
        expect(page.locator(f"#{section}")).to_have_count(1)

    # With reduced motion asked for, everything is present without scrolling. This is
    # the half of that promise the component's docstring made and the stylesheet did
    # not keep: `.reveal` sat at `opacity: 0` whatever the setting was, so a person who
    # asked for less motion still got a page that faded in section by section.
    hidden = page.evaluate(
        """() => [...document.querySelectorAll('main .reveal')]
              .filter((el) => Number(getComputedStyle(el).opacity) < 0.99).length"""
    )
    assert hidden == 0, f"{hidden} revealed blocks are still transparent at rest"

    # The warning is the point of the page, so it is above the fold at both widths —
    # not "on the page somewhere", which is what a disclosure at the bottom would be.
    warning = page.locator(".hm-m-warning")
    expect(warning).to_be_visible()
    box = warning.bounding_box()
    assert box is not None
    assert box["y"] < height * 1.6, (
        f"the under-development warning starts {box['y']:.0f}px down a {height}px "
        "viewport; it has to be one short scroll away at most"
    )

    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        # The whole document, and separately the first screen — the two things a
        # designer looks at, and they answer different questions.
        page.screenshot(path=str(SHOTS / f"methodology-{width}.png"), full_page=True)
        page.screenshot(path=str(SHOTS / f"methodology-{width}-first-screen.png"))


def test_the_rail_follows_the_reader(page: Page, base_url: str) -> None:
    """A long document needs to say where you are, or it is a wall."""

    page.set_viewport_size({"width": 1440, "height": 900})
    _open(page, base_url)

    rail = page.locator(".hm-m-rail a")
    expect(rail).to_have_count(len(SECTION_IDS))

    page.locator("#coins").scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    current = page.locator('.hm-m-rail a[aria-current="true"]')
    expect(current).to_have_count(1)


def test_every_figure_shows_a_real_number(page: Page, base_url: str) -> None:
    """The counters animate. A reader with motion off, and any crawler, sees the value."""

    _open(page, base_url)
    values = page.locator(".hm-m-figure-value").all_inner_texts()
    assert len(values) == 4
    assert all(value.strip().isdigit() and int(value) > 0 for value in values), values


def test_the_conditions_can_be_narrowed(page: Page, base_url: str) -> None:
    """Fifty-six rules is more than anybody reads at once."""

    page.set_viewport_size({"width": 1440, "height": 900})
    _open(page, base_url)

    before = page.locator(".hm-m-family").count()
    assert before > 1

    page.locator("#rules .hm-m-chip").nth(1).click()
    page.wait_for_timeout(200)
    assert page.locator(".hm-m-family").count() == 1
