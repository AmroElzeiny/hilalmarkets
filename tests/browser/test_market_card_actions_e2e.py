"""Each action on a screened-coin card gets a row of its own, at every width.

Side by side, "See the evidence" and "Full Passport" shared one card's width between
them, so on the narrower cards both wrapped or were cut short. Checked in a browser
because it is a question about laid-out boxes, and at three widths because the request
was "across all viewports" — a rule that only holds on a wide screen is not the rule.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import (
    assert_no_horizontal_overflow,
    close_any_open_guide,
    seed_sharia_screened_market,
    signup,
)


def _open_market(page: Page, base_url: str, browser_app) -> None:
    email = signup(page, base_url)
    close_any_open_guide(page)
    seed_sharia_screened_market(browser_app.database_url, email)
    page.goto(f"{base_url}/dashboard/market", wait_until="domcontentloaded")
    close_any_open_guide(page)
    page.wait_for_timeout(1200)

    options = page.locator("[data-standard] option").evaluate_all(
        "nodes => nodes.map(node => [node.value, node.textContent.trim()])"
    )
    seeded = [value for value, label in options if "browser qa" in label.lower()]
    assert seeded, f"the seeded screening standard is missing: {options}"
    page.select_option("[data-standard]", seeded[0])
    close_any_open_guide(page)
    expect(page.locator(".t-asset").first).to_be_visible(timeout=20_000)


@pytest.mark.parametrize("width", [1440, 1024, 760])
def test_each_card_action_gets_its_own_row(page: Page, base_url: str, browser_app, width) -> None:
    page.set_viewport_size({"width": width, "height": 950})
    _open_market(page, base_url, browser_app)

    boxes = page.locator(".t-asset").first.locator(".t-asset-actions > .t-action").evaluate_all(
        """nodes => nodes.map(node => {
            const box = node.getBoundingClientRect();
            return {
                x: Math.round(box.x),
                y: Math.round(box.y),
                width: Math.round(box.width),
                height: Math.round(box.height),
            };
        })"""
    )
    assert len(boxes) >= 2, f"a card should offer two actions, found {boxes}"

    # Stacked: same left edge, same width, and each one strictly below the last.
    first = boxes[0]
    for previous, current in zip(boxes, boxes[1:], strict=False):
        assert current["x"] == first["x"], f"actions are not aligned at {width}px: {boxes}"
        assert current["width"] == first["width"], f"actions differ in width at {width}px: {boxes}"
        assert current["y"] >= previous["y"] + previous["height"], (
            f"actions share a row at {width}px: {boxes}"
        )

    # And each one is a comfortable target rather than half a card.
    for box in boxes:
        assert box["height"] >= 40, f"action shorter than 40px at {width}px: {box}"

    assert_no_horizontal_overflow(page)


def test_the_action_wording_is_never_cut_short(page: Page, base_url: str, browser_app) -> None:
    """A full row each exists so the words fit. This proves they do."""
    page.set_viewport_size({"width": 1024, "height": 950})
    _open_market(page, base_url, browser_app)

    overflowing = page.locator(".t-asset .t-asset-actions > .t-action").evaluate_all(
        """nodes => nodes
            .filter(node => node.scrollWidth > node.clientWidth + 1)
            .map(node => node.textContent.trim())"""
    )
    assert overflowing == [], f"action wording does not fit its row: {overflowing}"
