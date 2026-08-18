"""The `/dashboard-test` design path must actually render, and must obey its own rules.

These are not screenshot tests. They prove the things the redesign brief made
non-negotiable and that a person reading the diff cannot check by eye:

* every page renders through the real app, with real data;
* the market page, its popups, the Passport and the report carry no "New watchlist";
* the new path and the live path read the *same* screened assets, so the two designs
  can never disagree about which coins are eligible;
* no forbidden claim from `brand guide.md` section 17 reaches the page.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    AssetShariaStatusHistory,
    ShariaEvidenceSource,
    ShariaMethodology,
    User,
)
from ai_market_monitor.db.models.enums import ShariaAssetStatus, ShariaMethodologyStatus
from tests.factories import methodology_evidence_requirements, methodology_rules
from tests.integration.test_dashboard_web import _signup_and_verify

#: `FixtureMarketDataProvider` serves these, so the market page has a real universe.
SEEDED_ASSETS = (("BTC", "Bitcoin"), ("ETH", "Ethereum"), ("SOL", "Solana"))


async def _seed_screening(session) -> ShariaMethodology:
    now = datetime.now(UTC)
    methodology = ShariaMethodology(
        code=f"DASHTEST_{uuid4().hex[:12].upper()}",
        name="Dashboard test standard",
        version="1.0-test",
        description="Evidence-backed test standard for the /dashboard-test pages.",
        status=ShariaMethodologyStatus.ACTIVE,
        governing_body="Qualified test governance",
        reviewer_group="Qualified test reviewers",
        published_at=now - timedelta(days=2),
        effective_from=now - timedelta(days=2),
        rules_json=methodology_rules(source_family="dashboard_test"),
        evidence_requirements_json=methodology_evidence_requirements(),
    )
    session.add(methodology)
    await session.flush()

    for asset, name in SEEDED_ASSETS:
        assessment = AssetShariaAssessment(
            canonical_asset=asset,
            asset_name=name,
            methodology_id=methodology.id,
            status=ShariaAssetStatus.ELIGIBLE,
            summary="A qualified test reviewer recorded this conclusion from evidence.",
            qualifications=[],
            exclusion_reasons=[],
            evidence_snapshot={
                "reviewed_dimensions": [{"name": "Primary activity", "result": "reviewed"}],
                "methodology_result": {"passed": ["test rule"]},
            },
            reviewed_by="Qualified test reviewer",
            reviewed_at=now - timedelta(days=1),
            valid_from=now - timedelta(days=1),
        )
        session.add(assessment)
        await session.flush()
        session.add_all(
            [
                ShariaEvidenceSource(
                    assessment_id=assessment.id,
                    source_type="official_disclosure",
                    title=f"Official {asset} disclosure",
                    publisher="Project documentation",
                    source_url=f"https://example.com/{asset.casefold()}-evidence",
                    retrieved_at=now - timedelta(days=1),
                    evidence_category="primary_activity",
                    evidence_summary="Retained evidence used only for deterministic tests.",
                    source_hash=uuid4().hex + uuid4().hex,
                ),
                AssetShariaStatusHistory(
                    canonical_asset=asset,
                    methodology_id=methodology.id,
                    previous_status=None,
                    new_status=ShariaAssetStatus.ELIGIBLE,
                    reason_code="test_review",
                    reason_summary="Qualified test evidence review completed.",
                    assessment_id=assessment.id,
                    changed_at=assessment.valid_from,
                    approved_by="Qualified test approver",
                ),
            ]
        )
    await session.commit()
    return methodology


async def _signed_in_with_screening(test_context, email: str) -> ShariaMethodology:
    await _signup_and_verify(test_context, email=email)
    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(User)) is not None
        return await _seed_screening(session)


async def _pages(test_context) -> dict[str, str]:
    """Every /dashboard-test surface, rendered."""
    client = test_context["client"]
    market = await client.get("/dashboard/market")
    assert market.status_code == 200, market.text[:800]
    passport = await client.get("/dashboard/market/btc")
    assert passport.status_code == 200, passport.text[:800]
    report = await client.get("/dashboard/market/btc/report")
    assert report.status_code == 200, report.text[:800]
    return {"market": market.text, "passport": passport.text, "report": report.text}


async def test_every_dashboard_test_page_renders(test_context):
    await _signed_in_with_screening(test_context, email="dash-test-render@example.com")
    pages = await _pages(test_context)

    assert "hm-market-test.js" in pages["market"]
    assert "Bitcoin" in pages["passport"]
    assert "Shariah Evidence report" in pages["report"]


async def test_the_market_heading_is_the_product_name_for_this_feature(test_context):
    """The page title and the menu that led to it must say the same thing.

    `product_language.py` owns what this feature is called. Writing the name by hand in
    the template is how a page ends up titled "Halal assets" under a menu item reading
    "Halal Assets".
    """
    from ai_market_monitor.services.product_language import product_term

    await _signed_in_with_screening(test_context, email="dash-test-name@example.com")
    market = (await test_context["client"].get("/dashboard/market")).text

    assert f"<h1>{product_term('universe')}</h1>" in market


async def test_the_second_set_of_addresses_is_gone(test_context):
    """These pages were built at `/dashboard-test/...` beside the older ones.

    They are the live pages now, at `/dashboard/...`, and the second address answers
    nothing. That matters more than it sounds: while both existed, the side menu opened
    one set and every Telegram button, WhatsApp reply and alert email opened the other,
    so two people looking at "Halal Assets" could be looking at different screens.
    """

    await _signed_in_with_screening(test_context, email="dash-test-home@example.com")
    client = test_context["client"]

    for gone in (
        "/dashboard-test",
        "/dashboard-test/market",
        "/dashboard-test/watchlists",
        "/dashboard-test/opportunities",
        "/dashboard-test/connections",
        "/dashboard-test/subscription",
        "/dashboard-test/settings",
        "/dashboard-test/support",
    ):
        response = await client.get(gone, follow_redirects=False)
        assert response.status_code == 404, gone


@pytest.mark.parametrize("page", ["market", "passport", "report"])
async def test_no_page_on_this_path_offers_a_new_watchlist(test_context, page):
    """The brief removes "New watchlist" from this path, including every page it leads to."""
    await _signed_in_with_screening(test_context, email=f"dash-test-nw-{page}@example.com")
    pages = await _pages(test_context)

    assert not re.search(r"new\s+watchlist", pages[page], re.IGNORECASE)
    assert "/dashboard/strategies/new" not in pages[page]


@pytest.mark.parametrize("page", ["market", "passport", "report"])
def test_the_popups_carry_no_new_watchlist_either(page):
    """The popups ship as templates, so they are checked as source, not as rendered HTML."""
    from pathlib import Path

    import ai_market_monitor

    partials = (
        Path(ai_market_monitor.__file__).parent
        / "templates"
        / "hilal"
        / "dashboard_test"
        / "partials"
    )
    for template in partials.glob("*.html"):
        source = template.read_text(encoding="utf-8")
        assert not re.search(r"new\s+watchlist", source, re.IGNORECASE), template.name


@pytest.mark.parametrize(
    "claim",
    [
        "100% halal",
        "guaranteed halal",
        "guaranteed profit",
        "winning signal",
        "risk-free",
        "buy now",
        "sell now",
        "AI trades for you",
    ],
)
async def test_no_forbidden_claim_reaches_any_page(test_context, claim):
    """`brand guide.md` section 17. Checked on every page of the path, not one sample."""
    await _signed_in_with_screening(
        test_context, email=f"dash-test-claim-{uuid4().hex[:8]}@example.com"
    )
    pages = await _pages(test_context)

    for name, html in pages.items():
        assert claim.casefold() not in html.casefold(), f"{claim!r} appeared on the {name} page"


async def test_the_two_paths_read_the_same_screened_assets(test_context):
    """One context builder feeds both designs, so neither can drift from the other."""
    await _signed_in_with_screening(test_context, email="dash-test-parity@example.com")
    client = test_context["client"]

    live = await client.get("/dashboard/market")
    new = await client.get("/dashboard/market")
    assert live.status_code == 200
    assert new.status_code == 200

    for asset, name in SEEDED_ASSETS:
        # The new page's no-JavaScript table lists the same screened assets the live
        # page renders, which is the only place either page states them server-side.
        assert asset in new.text, f"{asset} is missing from the new market page"
        assert name in new.text, f"{name} is missing from the new market page"


async def test_the_new_market_page_works_without_javascript(test_context):
    """A live grid that needs JavaScript must still leave the screening results readable."""
    await _signed_in_with_screening(test_context, email="dash-test-nojs@example.com")
    market = (await test_context["client"].get("/dashboard/market")).text

    start = market.index("data-noscript-results")
    fallback = market[start : market.index("</noscript>", start)]
    for asset, _name in SEEDED_ASSETS:
        assert asset in fallback
    assert "/dashboard/market/btc" in fallback


async def test_the_passport_links_back_to_the_market_it_was_opened_from(test_context):
    """One market page, so there is one place "back" can mean."""
    await _signed_in_with_screening(test_context, email="dash-test-links@example.com")
    passport = (await test_context["client"].get("/dashboard/market/btc")).text

    assert 'href="/dashboard/market"' in passport
    assert "/dashboard/market/btc/report" in passport
    # And nothing on it reaches for the address the redesigned pages used to answer at.
    assert "/dashboard-test" not in passport


async def test_the_report_states_the_limits_of_the_result(test_context):
    """A printed page is read away from the product, so it must carry its own scope."""
    await _signed_in_with_screening(test_context, email="dash-test-report@example.com")
    report = (await test_context["client"].get("/dashboard/market/btc/report")).text

    assert "not a universal religious ruling" in report
    assert "does not execute trades" in report
    assert "Evidence last checked" in report


async def test_only_one_passport_popup_is_ever_on_the_page(test_context):
    """Two dialogs answering the same hooks would both open.

    There used to be two market pages with a popup each, and the check here was that
    neither carried the other's. There is one market page now, so the check is that it
    carries one popup and that the older one is not loaded beside it.
    """

    await _signed_in_with_screening(test_context, email="dash-test-popup@example.com")
    market = (await test_context["client"].get("/dashboard/market")).text

    assert market.count("data-passport-dialog") == 1
    assert "data-passport-quick-dialog" not in market
    assert "passport-quick-view.js" not in market


async def test_the_report_page_carries_no_popup_at_all(test_context):
    await _signed_in_with_screening(test_context, email="dash-test-report-popup@example.com")
    report = (await test_context["client"].get("/dashboard/market/btc/report")).text

    assert "data-passport-dialog" not in report
    assert "data-passport-quick-dialog" not in report
