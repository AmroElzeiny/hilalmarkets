"""The public Passport and the dashboard's sticky bars, in a real browser.

Measured, not looked at: a sticky bar is "under the topbar" when its top edge is at or
below the topbar's bottom edge once the page has scrolled. Before the fix the Passport's
section links and the Settings page's bars stuck at `top: 0`, where the topbar — held
at the same place and drawn above them — covered them.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import (
    assert_no_horizontal_overflow,
    close_any_open_guide,
    seed_sharia_screened_market,
    signup,
)

SIZES = [{"width": 1440, "height": 900}, {"width": 390, "height": 844}]

EDGES = """([held, bar]) => {
  const a = document.querySelector(held).getBoundingClientRect();
  const b = document.querySelector(bar).getBoundingClientRect();
  return {heldBottom: a.bottom, barTop: b.top};
}"""


def _assert_below(page: Page, held: str, bar: str) -> None:
    page.mouse.wheel(0, 1600)
    page.wait_for_timeout(700)
    edges = page.evaluate(EDGES, [held, bar])
    assert edges["barTop"] >= edges["heldBottom"] - 0.5, edges
    # And close under it, not floating far down the page.
    assert edges["barTop"] - edges["heldBottom"] <= 24, edges


@pytest.mark.parametrize("size", SIZES, ids=["desktop", "phone"])
def test_the_passport_opens_for_a_visitor_and_its_links_stop_under_the_header(
    page: Page, base_url: str, browser_app, size
) -> None:
    seeded = seed_sharia_screened_market(browser_app.database_url, signup(page, base_url))
    page.context.clear_cookies()  # read it as a visitor, with no account
    page.set_viewport_size(size)
    page.goto(
        f"{base_url}/passports/sol?methodology_id={seeded['methodology_id']}",
        wait_until="networkidle",
    )
    assert re.search(r"/passports/sol\?methodology_id=", page.url), page.url
    expect(page.locator(".hm-header")).to_be_visible()
    expect(page.locator("[data-hm-shell-top]")).to_have_count(0)
    expect(page.locator(".t-standard")).to_be_visible()
    assert_no_horizontal_overflow(page)
    _assert_below(page, ".hm-header", "[data-passport-tabs]")


@pytest.mark.parametrize("size", SIZES, ids=["desktop", "phone"])
def test_the_settings_bars_stop_under_the_topbar(page: Page, base_url: str, size) -> None:
    signup(page, base_url)
    page.set_viewport_size(size)
    page.goto(f"{base_url}/dashboard/settings", wait_until="networkidle")
    close_any_open_guide(page)
    _assert_below(page, "[data-hm-shell-top]", "[data-g-sticky]")


def test_an_old_dashboard_passport_link_lands_on_the_website(
    page: Page, base_url: str, browser_app
) -> None:
    seed_sharia_screened_market(browser_app.database_url, signup(page, base_url))
    page.goto(f"{base_url}/dashboard/market/sol", wait_until="domcontentloaded")
    assert re.search(r"/passports/sol$", page.url), page.url
