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
    expect(root).to_have_attribute("data-signed-in", "true")
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
    _see_the_evidence_in_the_popup(page)


def _search_and_sort_like_a_member(page: Page) -> None:
    """A visitor searches and sorts the whole list, as a member on the free plan does."""

    cards = page.locator(".t-asset:visible")
    total = cards.count()
    assert total >= 1
    assert page.locator("[data-locked]").count() == 0
    symbol = cards.first.locator(".t-asset-symbol").inner_text().strip()
    page.fill("[data-search]", symbol)
    expect(page.locator(".t-asset:visible").first).to_contain_text(symbol)
    assert page.locator(".t-asset:visible").count() <= total
    page.fill("[data-search]", "")
    expect(page.locator(".t-asset:visible")).to_have_count(total)
    page.locator('[data-view="table"]').click()
    heading = page.locator('button[data-sort="symbol"]')
    expect(heading).to_be_enabled()
    heading.click()
    expect(heading.locator("xpath=..")).to_have_attribute("aria-sort", "ascending")
    page.locator('[data-view="cards"]').click()


def _see_the_evidence_in_the_popup(page: Page) -> None:
    """"See the evidence" opens the dashboard's Passport popup on this same page, and
    its "Open the full Passport" goes to the coin's public Passport."""

    address = page.url
    page.locator(".t-asset [data-quick-view]").first.click()
    dialog = page.locator("[data-passport-dialog]")
    expect(dialog).to_be_visible(timeout=10_000)
    expect(dialog.locator("[data-pq-content]")).to_be_visible(timeout=20_000)
    expect(dialog.locator("[data-pq-error]")).to_be_hidden()
    expect(dialog.locator("[data-pq-name]")).not_to_have_text("Loading")
    assert page.url == address, "the popup must not leave the page"
    _keep(page, "markets-evidence-popup")
    expect(dialog.locator("[data-pq-full]")).to_have_attribute(
        "href", re.compile(r"/passports/[a-z0-9]+")
    )
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden(timeout=5_000)
    page.locator(".t-asset [data-quick-view]").first.click()
    expect(dialog.locator("[data-pq-content]")).to_be_visible(timeout=20_000)
    dialog.locator("[data-pq-full]").click()
    page.wait_for_url(re.compile(r"/passports/[a-z0-9]+"), timeout=20_000)


def test_a_visitor_lands_on_the_public_market_page(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 950})
    _click_markets_in_the_header(page, base_url)
    assert re.search(r"/markets(\?|$)", page.url), page.url
    root = page.locator("[data-market-root]")
    expect(root).to_have_attribute("data-audience", "public")
    assert root.get_attribute("data-signed-in") is None


def test_a_visitor_sees_the_evidence_in_the_popup(
    page: Page, base_url: str, browser_app
) -> None:
    """A visitor without an account gets the same popup a member does."""

    page.set_viewport_size({"width": 1440, "height": 950})
    # The seed needs an account to own the followed coin; the visitor is signed out.
    email = signup(page, base_url)
    seed_sharia_screened_market(browser_app.database_url, email)
    page.context.clear_cookies()
    page.goto(f"{base_url}/markets", wait_until="domcontentloaded")
    assert page.locator("[data-market-root]").get_attribute("data-signed-in") is None
    _show_the_seeded_standard(page)
    _search_and_sort_like_a_member(page)
    _see_the_evidence_in_the_popup(page)

    # On a phone the popup fits the screen and its main button can be reached.
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/markets", wait_until="domcontentloaded")
    _show_the_seeded_standard(page)
    page.locator(".t-asset [data-quick-view]").first.click()
    dialog = page.locator("[data-passport-dialog]")
    expect(dialog.locator("[data-pq-content]")).to_be_visible(timeout=20_000)
    box = dialog.bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390, box
    expect(dialog.locator("[data-pq-full]")).to_be_in_viewport()
    if SCREENSHOT_DIR:
        page.screenshot(path=str(Path(SCREENSHOT_DIR) / "markets-evidence-popup-390.png"))


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
