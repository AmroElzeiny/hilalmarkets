"""The "Markets" link in the site's header opens the public Market page, for everyone.

It used to send anybody who was signed in to the dashboard's Halal Assets instead, so
the people most likely to press it never saw the page it names. Checked in a browser,
through the header link a person actually clicks, for a visitor and for a member.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from playwright.sync_api import Page, expect

from tests.browser.conftest import (
    assert_no_horizontal_overflow,
    close_any_open_guide,
    seed_sharia_screened_market,
    signup,
)

#: Set to a folder to keep a screenshot of each width, for a person to look at.
SCREENSHOT_DIR = os.environ.get("HM_MARKETS_SCREENSHOT_DIR")


def _click_markets_in_the_header(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    link = page.locator("header a", has_text=re.compile(r"^\s*Markets\s*$")).first
    expect(link).to_be_visible(timeout=20_000)
    link.click()
    page.wait_for_load_state("domcontentloaded")


def _show_the_seeded_standard(page: Page) -> None:
    options = page.locator("[data-standard] option").evaluate_all(
        "nodes => nodes.map(node => [node.value, node.textContent.trim()])"
    )
    seeded = [value for value, label in options if "browser qa" in label.lower()]
    assert seeded, f"the seeded screening standard is missing: {options}"
    page.select_option("[data-standard]", seeded[0])
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator(".t-asset").first).to_be_visible(timeout=20_000)


def _keep(page: Page, name: str) -> None:
    if SCREENSHOT_DIR:
        Path(SCREENSHOT_DIR).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SCREENSHOT_DIR) / f"{name}.png"), full_page=True)


def test_a_signed_in_member_stays_on_the_public_market_page(
    page: Page, base_url: str, browser_app
) -> None:
    page.set_viewport_size({"width": 1440, "height": 950})
    email = signup(page, base_url)
    close_any_open_guide(page)
    seed_sharia_screened_market(browser_app.database_url, email)

    _click_markets_in_the_header(page, base_url)
    assert re.search(r"/markets(\?|$)", page.url), page.url
    root = page.locator("[data-market-root]")
    expect(root).to_have_attribute("data-audience", "public")
    expect(root).to_have_attribute("data-unlocked", "true")
    _show_the_seeded_standard(page)
    assert "/dashboard" not in page.url, page.url

    # Nothing is locked and nothing asks a member to sign up.
    assert page.locator("[data-locked]").count() == 0
    assert page.locator("[data-account-dialog]").count() == 0
    # The member's followed coin is marked, and the counter says so.
    expect(page.locator('[data-count="following"]')).to_have_text("1", timeout=10_000)
    # Favorites opens in the dashboard, where following a coin happens.
    favorites = page.locator("[data-favorites-in-dashboard]")
    expect(favorites).to_have_attribute("href", re.compile(r"/dashboard/market\?saved_assets=1$"))
    # "See the evidence" opens the coin's Passport in the dashboard.
    page.locator(".t-asset [data-quick-view]").first.click()
    page.wait_for_url(re.compile(r"/dashboard/market/[a-z0-9]+"), timeout=20_000)


def test_a_visitor_lands_on_the_public_market_page(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 950})
    _click_markets_in_the_header(page, base_url)
    assert re.search(r"/markets(\?|$)", page.url), page.url
    root = page.locator("[data-market-root]")
    expect(root).to_have_attribute("data-audience", "public")
    assert root.get_attribute("data-unlocked") is None


def test_the_member_view_holds_at_every_width(page: Page, base_url: str, browser_app) -> None:
    email = signup(page, base_url)
    close_any_open_guide(page)
    seed_sharia_screened_market(browser_app.database_url, email)
    for width in (1440, 1024, 390):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(f"{base_url}/markets", wait_until="domcontentloaded")
        _show_the_seeded_standard(page)
        page.wait_for_timeout(600)
        assert_no_horizontal_overflow(page)
        _keep(page, f"markets-member-{width}")
