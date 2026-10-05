"""One Passport per coin, on the public website, at `/passports/<coin>`.

What the move promised, each checked through the real app:

* the Passport opens for everybody, with no account, inside the website's own header
  and footer rather than the dashboard's side menu and topbar;
* a coin reviewed under several Shariah standards has **one** page, and the standard is
  a choice on it — a highlighted picker whose every option is that same page with the
  standard written into the address;
* the standard in the address is the standard on the page, for every standard, not one
  sample — and the report link carries it on;
* an old, wrong or stale link still lands on the coin's Passport rather than an error;
* `/dashboard/market/<coin>` and its report — the addresses in every alert email,
  Telegram button and notice already sent — forward there permanently, without asking
  anybody to sign in first.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    AssetShariaStatusHistory,
    CanonicalAsset,
    PublishedAssetAssessment,
    ShariaEvidenceSource,
    ShariaMethodology,
)
from ai_market_monitor.db.models.enums import ShariaAssetStatus, ShariaMethodologyStatus
from ai_market_monitor.services.sharia_automated_screen import METHODOLOGY_SYSTEM_CODE
from tests.factories import methodology_evidence_requirements, methodology_rules
from tests.integration.test_dashboard_web import _signup_and_verify

#: Two standards, each with its own result for the same coin. Different results on
#: purpose: a page that showed the wrong standard's answer would show the wrong words.
STANDARDS = (
    ("First test standard", ShariaAssetStatus.ELIGIBLE, "Eligible"),
    (
        "Second test standard",
        ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS,
        "Eligible with qualifications",
    ),
)

#: Who published the test records. Their account number must never reach the page.
PUBLISHER_ID = uuid4()


async def _seed(
    test_context, count: int = 2, *, codes: tuple[str, ...] = ()
) -> list[ShariaMethodology]:
    """BTC reviewed under `count` standards, each published and in force.

    `codes` names the standards' codes in order, where a test needs a particular one.
    """

    now = datetime.now(UTC)
    created: list[ShariaMethodology] = []
    async with test_context["session_factory"]() as session:
        coin = CanonicalAsset(
            symbol="BTC",
            name="Bitcoin",
            asset_type="native",
            native_chain="Bitcoin",
            contract_addresses={},
            provider_ids={},
            identity_hash=uuid4().hex + uuid4().hex,
            mapping_state="verified",
            mapping_evidence={},
        )
        session.add(coin)
        await session.flush()
        for index, (name, status, _label) in enumerate(STANDARDS[:count]):
            methodology = ShariaMethodology(
                code=codes[index] if index < len(codes) else f"PASSPORT_{uuid4().hex[:12].upper()}",
                name=name,
                version=f"{index + 1}.0",
                description="Evidence-backed test standard for the public Passport.",
                status=ShariaMethodologyStatus.ACTIVE,
                governing_body="Qualified test governance",
                reviewer_group="Qualified test reviewers",
                published_at=now - timedelta(days=3 - index),
                effective_from=now - timedelta(days=3 - index),
                rules_json=methodology_rules(source_family=f"passport_test_{index}"),
                evidence_requirements_json=methodology_evidence_requirements(),
            )
            session.add(methodology)
            await session.flush()
            assessment = AssetShariaAssessment(
                canonical_asset="BTC",
                asset_name="Bitcoin",
                methodology_id=methodology.id,
                status=status,
                summary=f"A qualified reviewer recorded this under {name}.",
                qualifications=(
                    ["Spot holding only."]
                    if status == ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS
                    else []
                ),
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
                        title="Official BTC disclosure",
                        publisher="Project documentation",
                        source_url="https://example.com/btc-evidence",
                        retrieved_at=now - timedelta(days=1),
                        evidence_category="primary_activity",
                        evidence_summary="Retained evidence used only for deterministic tests.",
                        source_hash=uuid4().hex + uuid4().hex,
                    ),
                    AssetShariaStatusHistory(
                        canonical_asset="BTC",
                        methodology_id=methodology.id,
                        previous_status=None,
                        new_status=status,
                        reason_code="test_review",
                        reason_summary="Qualified test evidence review completed.",
                        assessment_id=assessment.id,
                        changed_at=assessment.valid_from,
                        approved_by="Qualified test approver",
                    ),
                ]
            )
            # A published record, written straight in: what is under test is the page,
            # not how a record comes to be published.
            session.add(
                PublishedAssetAssessment(
                    canonical_asset_id=coin.id,
                    external_assessment_id=uuid4(),
                    dossier_id=uuid4(),
                    review_decision_id=uuid4(),
                    asset_assessment_id=assessment.id,
                    version=index + 1,
                    publication_state="published",
                    passport_snapshot={},
                    integrity_hash=f"hash-{index:04d}",
                    is_active=True,
                    published_by_user_id=PUBLISHER_ID,
                    published_at=now - timedelta(hours=12),
                )
            )
            created.append(methodology)
        await session.commit()
    return created


def _options(html: str) -> list[tuple[str, str, bool]]:
    """Every option in the standard picker: (value, address it opens, selected)."""

    picker = html[html.index("data-passport-standard-select") :]
    picker = picker[: picker.index("</select>")]
    return [
        (value, href, bool(selected))
        for value, href, selected in re.findall(
            r'<option value="([^"]+)" data-href="([^"]+)"( selected)?>', picker
        )
    ]


async def test_a_visitor_opens_a_passport_without_an_account(test_context):
    await _seed(test_context, count=1)
    page = await test_context["client"].get("/passports/btc", follow_redirects=False)

    assert page.status_code == 200, page.text[:600]
    html = page.text
    assert "<h1>Bitcoin</h1>" in html
    # The website's own header and footer, not the dashboard's.
    assert '<div id="root"></div>' in html
    assert '<div id="hm-site-footer"></div>' in html
    assert "data-hm-shell-top" not in html
    assert "data-hm-shell-nav" not in html
    # A report needs an account; a visitor is asked to sign in and brought back.
    assert "data-problem-form" not in html
    assert "Sign in to report a problem" in html
    assert "next=%2Fpassports%2Fbtc%23report-problem" in html
    # One canonical address, on the website, whatever standard is being read.
    assert re.search(r'<link rel="canonical" href="[^"]*/passports/btc">', html)


async def test_a_coin_under_several_standards_has_one_passport_with_a_picker(test_context):
    methodologies = await _seed(test_context, count=2)
    html = (await test_context["client"].get("/passports/btc")).text

    options = _options(html)
    assert {value for value, _href, _selected in options} == {
        str(item.id) for item in methodologies
    }
    # Every option is this same page, with only the standard changed.
    for value, href, _selected in options:
        assert href == f"/passports/btc?methodology_id={value}"
    assert sum(selected for *_rest, selected in options) == 1
    # The picker is the highlighted control, and it works without scripting too.
    assert 'class="t-standard t-no-print"' in html
    assert 'action="/passports/btc"' in html
    assert "data-passport-standard-submit" in html
    # The old second list of standards further down the page is gone.
    assert "Other standards reviewed this coin" not in html


@pytest.mark.parametrize("index", range(len(STANDARDS)))
async def test_the_standard_in_the_address_is_the_standard_on_the_page(test_context, index):
    methodologies = await _seed(test_context, count=len(STANDARDS))
    chosen = methodologies[index]
    name, _status, label = STANDARDS[index]
    html = (await test_context["client"].get(f"/passports/btc?methodology_id={chosen.id}")).text

    selected = [value for value, _href, is_selected in _options(html) if is_selected]
    assert selected == [str(chosen.id)]
    assert f"<strong>{name} v{chosen.version}</strong>" in html
    assert label in html
    # The report opens on the same standard.
    assert f"/passports/btc/report?methodology_id={chosen.id}" in html

    report = await test_context["client"].get(f"/passports/btc/report?methodology_id={chosen.id}")
    assert report.status_code == 200
    assert f"{name} v{chosen.version}" in report.text
    assert f'href="/passports/btc?methodology_id={chosen.id}"' in report.text


async def test_a_coin_under_one_standard_names_it_without_a_picker(test_context):
    await _seed(test_context, count=1)
    html = (await test_context["client"].get("/passports/btc")).text

    assert "data-passport-standard-select" not in html
    assert "The only standard that has reviewed this coin so far." in html
    assert "First test standard v1.0" in html


@pytest.mark.parametrize("bad", ["not-a-standard", str(uuid4())])
async def test_a_wrong_or_stale_standard_still_opens_the_coin(test_context, bad):
    await _seed(test_context, count=1)
    for path in (
        f"/passports/btc?methodology_id={bad}",
        f"/passports/btc/report?methodology_id={bad}",
    ):
        response = await test_context["client"].get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == path.split("?", 1)[0]


async def test_one_address_per_coin_whatever_the_case(test_context):
    await _seed(test_context, count=1)
    response = await test_context["client"].get("/passports/BTC", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/passports/btc"


async def test_a_coin_nobody_reviewed_has_no_passport(test_context):
    await _seed(test_context, count=1)
    response = await test_context["client"].get("/passports/doge", follow_redirects=False)
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("/dashboard/market/btc", "/passports/btc"),
        ("/dashboard/market/BTC", "/passports/btc"),
        ("/dashboard/market/btc/report", "/passports/btc/report"),
    ],
)
@pytest.mark.parametrize("with_standard", [False, True])
async def test_the_old_dashboard_addresses_forward_without_sign_in(
    test_context, old, new, with_standard
):
    """Every alert email, Telegram button and notice already sent names these."""

    methodology_id = uuid4()
    query = f"?methodology_id={methodology_id}" if with_standard else ""
    response = await test_context["client"].get(f"{old}{query}", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == f"{new}{query}"


async def test_a_signed_in_reader_gets_the_problem_form_with_their_own_token(test_context):
    await _signup_and_verify(test_context, email="passport-reader@example.com")
    await _seed(test_context, count=1)
    page = await test_context["client"].get("/passports/btc")

    assert page.status_code == 200
    assert "data-problem-form" in page.text
    assert re.search(r'data-csrf-token="[0-9a-f]{64}"', page.text)
    assert "Sign in to report a problem" not in page.text
    # The token is this reader's own, so no shared cache may keep the page.
    assert "no-store" in page.headers["cache-control"]


async def test_the_passport_carries_no_forbidden_claim(test_context):
    await _seed(test_context, count=2)
    for path in ("/passports/btc", "/passports/btc/report"):
        html = (await test_context["client"].get(path)).text.casefold()
        for claim in ("100% halal", "guaranteed halal", "guaranteed profit", "buy now"):
            assert claim not in html, (claim, path)


async def test_the_history_never_shows_an_internal_account_number(test_context):
    """The record was published by the platform, and the page says so in words."""

    await _seed(test_context, count=1)
    for path in ("/passports/btc", "/passports/btc/report"):
        html = (await test_context["client"].get(path)).text
        assert str(PUBLISHER_ID) not in html, path
        assert "Hilal Markets" in html


async def test_before_launch_the_passport_is_hidden_with_the_market(waitlist_context):
    await _seed(waitlist_context, count=1)
    response = await waitlist_context["client"].get("/passports/btc", follow_redirects=False)
    assert response.status_code == 303


async def _newer_default_without_btc(test_context) -> ShariaMethodology:
    """The product's default standard, newer than the rest, with no result for BTC."""

    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        methodology = ShariaMethodology(
            code=f"DEFAULT_{uuid4().hex[:12].upper()}",
            name="Default test standard",
            version="9.0",
            description="The default standard, which never reviewed BTC.",
            status=ShariaMethodologyStatus.ACTIVE,
            governing_body="Qualified test governance",
            reviewer_group="Qualified test reviewers",
            published_at=now - timedelta(hours=6),
            effective_from=now - timedelta(hours=6),
            rules_json=methodology_rules(source_family="passport_default"),
            evidence_requirements_json=methodology_evidence_requirements(),
        )
        session.add(methodology)
        await session.commit()
        return methodology


async def test_a_coin_the_default_standard_never_reviewed_still_has_its_passport(test_context):
    """One Passport per coin: a coin some standard reviewed always has a page."""

    methodologies = await _seed(test_context, count=2)
    await _newer_default_without_btc(test_context)
    page = await test_context["client"].get("/passports/btc", follow_redirects=False)

    assert page.status_code == 200, page.text[:400]
    selected = [value for value, _href, is_selected in _options(page.text) if is_selected]
    assert selected and selected[0] in {str(item.id) for item in methodologies}
    # The default standard has nothing to say about BTC, so it is not offered.
    assert "Default test standard" not in page.text


async def test_the_machine_standard_is_never_chosen_for_somebody(test_context):
    """It may be picked on purpose, never put in front of a reader who did not pick it."""

    (automated,) = await _seed(test_context, count=1, codes=(METHODOLOGY_SYSTEM_CODE,))
    await _newer_default_without_btc(test_context)
    client = test_context["client"]

    assert (await client.get("/passports/btc", follow_redirects=False)).status_code == 404
    chosen = await client.get(f"/passports/btc?methodology_id={automated.id}")
    assert chosen.status_code == 200
    assert "(automated, no Shariah advisor)" in chosen.text
    assert "data-automated-methodology-notice" in chosen.text
