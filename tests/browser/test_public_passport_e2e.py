"""The public Passport and the dashboard's sticky bars, in a real browser.

Measured, not looked at: a sticky bar is "under the topbar" when its top edge is at or
below the topbar's bottom edge once the page has scrolled. Before the fix the Passport's
section links and the Settings page's bars stuck at `top: 0`, where the topbar — held
at the same place and drawn above them — covered them.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy.ext.asyncio import AsyncSession

from tests.browser.conftest import (
    _run_async_in_thread,
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


#: Set to a folder to keep a screenshot of each width, for a person to look at.
SCREENSHOT_DIR = os.environ.get("HM_PASSPORT_SCREENSHOT_DIR")


@pytest.mark.parametrize("size", SIZES, ids=["desktop", "phone"])
def test_the_passport_opens_with_the_question_then_the_answer_then_the_standard(
    page: Page, base_url: str, browser_app, size
) -> None:
    """Read top to bottom: which coin, the question, the answer, then the standard."""

    seeded = seed_sharia_screened_market(browser_app.database_url, signup(page, base_url))
    page.context.clear_cookies()
    page.set_viewport_size(size)
    page.goto(
        f"{base_url}/passports/sol?methodology_id={seeded['methodology_id']}",
        wait_until="networkidle",
    )
    heading = page.locator("main h1")
    expect(heading).to_have_count(1)
    expect(heading).to_have_text(re.compile(r"^Is .+ Halal\?$"))
    eyebrow = page.locator(".t-passport-hero .t-eyebrow")
    expect(eyebrow).to_contain_text("SOL Evidence Passport")
    answer = page.locator("[data-passport-answer]")
    expect(answer).to_be_visible()
    expect(answer).to_contain_text("not a universal religious ruling")
    tops = page.evaluate(
        """() => ['.t-passport-hero .t-eyebrow', 'main h1', '[data-passport-identity-line]',
                  '[data-passport-answer]', '.t-standard', '.t-pq-answer']
            .map(selector => document.querySelector(selector).getBoundingClientRect().top)"""
    )
    assert tops == sorted(tops), tops
    expect(page.locator("[data-passport-read-next] a[href='/how-we-screen']")).to_be_visible()
    expect(page.locator("[data-passport-read-next] a[href='/markets']")).to_be_visible()
    assert_no_horizontal_overflow(page)
    if SCREENSHOT_DIR:
        Path(SCREENSHOT_DIR).mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(Path(SCREENSHOT_DIR) / f"passport-{size['width']}.png"), full_page=True
        )


def test_an_old_dashboard_passport_link_lands_on_the_website(
    page: Page, base_url: str, browser_app
) -> None:
    seed_sharia_screened_market(browser_app.database_url, signup(page, base_url))
    page.goto(f"{base_url}/dashboard/market/sol", wait_until="domcontentloaded")
    assert re.search(r"/passports/sol$", page.url), page.url


def test_a_visitor_reports_a_problem_with_their_email(
    page: Page, base_url: str, browser_app
) -> None:
    """The form a member uses, sent by a visitor without an account, in a real browser."""

    seeded = seed_sharia_screened_market(browser_app.database_url, signup(page, base_url))
    _record_the_coins_identity(
        browser_app.database_url, "SOL", "Solana", seeded["methodology_id"]
    )
    page.context.clear_cookies()
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(
        f"{base_url}/passports/sol?methodology_id={seeded['methodology_id']}",
        wait_until="networkidle",
    )
    form = page.locator("[data-problem-form]")
    expect(form).to_have_attribute("data-visitor", "true")
    expect(page.get_by_text("Sign in to report a problem")).to_have_count(0)
    form.locator("[name='report_type']").select_option("broken_source")
    form.locator("[name='details']").fill("The second source link answers with a not-found page.")
    form.locator("[name='email']").fill("browser-visitor@example.com")
    form.locator("button[type='submit']").click()
    expect(form.locator("[data-problem-status]")).to_contain_text(
        "A reviewer will look at this", timeout=15_000
    )


def _record_the_coins_identity(
    database_url: str, symbol: str, name: str, methodology_id: str
) -> None:
    """A Passport offers its report form only for a published coin whose identity is
    recorded: the coin's record, and a publication of its assessment."""

    from datetime import UTC, datetime
    from uuid import UUID, uuid4

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine

    from ai_market_monitor.db.models import (
        AssetShariaAssessment,
        CanonicalAsset,
        PublishedAssetAssessment,
    )

    async def _add() -> None:
        engine = create_async_engine(database_url)
        async with AsyncSession(engine) as session:
            coin = CanonicalAsset(
                symbol=symbol,
                name=name,
                asset_type="native_coin",
                native_chain=name,
                contract_addresses={},
                provider_ids={},
                identity_hash=uuid4().hex + uuid4().hex,
                mapping_state="verified",
                mapping_evidence={},
            )
            session.add(coin)
            await session.flush()
            assessment = await session.scalar(
                select(AssetShariaAssessment).where(
                    AssetShariaAssessment.canonical_asset == symbol,
                    AssetShariaAssessment.methodology_id == UUID(methodology_id),
                )
            )
            session.add(
                PublishedAssetAssessment(
                    canonical_asset_id=coin.id,
                    external_assessment_id=uuid4(),
                    dossier_id=uuid4(),
                    review_decision_id=uuid4(),
                    asset_assessment_id=assessment.id,
                    version=1,
                    publication_state="published",
                    passport_snapshot={},
                    integrity_hash=f"hash-{symbol}-browser",
                    is_active=True,
                    published_by_user_id=uuid4(),
                    published_at=datetime.now(UTC),
                )
            )
            await session.commit()
        await engine.dispose()

    _run_async_in_thread(_add)
