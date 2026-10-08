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

import asyncio
import json
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from html import unescape
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
from ai_market_monitor.services import public_passports
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
    test_context,
    count: int = 2,
    *,
    codes: tuple[str, ...] = (),
    symbol: str = "BTC",
    name: str = "Bitcoin",
    asset_type: str = "native_coin",
    network: str = "Bitcoin",
) -> list[ShariaMethodology]:
    """`symbol` reviewed under `count` standards, each published and in force.

    `codes` names the standards' codes in order, where a test needs a particular one.
    `asset_type` is the kind the identity check writes (`core/asset_kinds.py`).
    """

    now = datetime.now(UTC)
    created: list[ShariaMethodology] = []
    async with test_context["session_factory"]() as session:
        coin = CanonicalAsset(
            symbol=symbol,
            name=name,
            asset_type=asset_type,
            native_chain=network,
            contract_addresses={},
            provider_ids={},
            identity_hash=uuid4().hex + uuid4().hex,
            mapping_state="verified",
            mapping_evidence={},
        )
        session.add(coin)
        await session.flush()
        # `standard`, not `name`: reusing `name` here once wrote the standard's name into
        # the coin's assessment as if it were the coin's.
        for index, (standard, status, _label) in enumerate(STANDARDS[:count]):
            methodology = ShariaMethodology(
                code=codes[index] if index < len(codes) else f"PASSPORT_{uuid4().hex[:12].upper()}",
                name=standard,
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
                canonical_asset=symbol,
                asset_name=name,
                methodology_id=methodology.id,
                status=status,
                summary=f"A qualified reviewer recorded this under {standard}.",
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
                        canonical_asset=symbol,
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
                    integrity_hash=f"hash-{symbol}-{index:04d}",
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
    assert "<h1>Is Bitcoin (BTC) Halal?</h1>" in html
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


# -- the sitemap ------------------------------------------------------------------------

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


async def _sitemap(context) -> list[str]:
    """Every address in the sitemap, after checking it is a valid sitemap at all."""

    response = await context["client"].get("/sitemap.xml", follow_redirects=False)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    root = ET.fromstring(response.content)
    assert root.tag == "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset"
    return [loc.text or "" for loc in root.findall("sm:url/sm:loc", _SITEMAP_NS)]


async def test_the_sitemap_lists_a_passport_exactly_when_its_page_opens(test_context):
    """The sitemap and the page share one rule, so they can never disagree.

    Three coins, three outcomes: BTC is published under a Shariah standard, ETH has only
    the machine standard (which never opens on its own), DOGE was never reviewed. Every
    coin is checked both ways — listed means the page opens, unlisted means it does not.
    """

    await _seed(test_context, count=1)
    await _seed(
        test_context, count=1, codes=(METHODOLOGY_SYSTEM_CODE,), symbol="ETH", name="Ethereum"
    )
    await _newer_default_without_btc(test_context)
    base = str(test_context["settings"].public_base_url).rstrip("/")
    listed = set(await _sitemap(test_context))

    outcomes = {}
    for coin in ("btc", "eth", "doge"):
        page = await test_context["client"].get(f"/passports/{coin}", follow_redirects=False)
        outcomes[coin] = page.status_code
        assert (f"{base}/passports/{coin}" in listed) == (page.status_code == 200), (
            coin,
            page.status_code,
        )
    # Both sides of the rule were exercised, not only one.
    assert outcomes == {"btc": 200, "eth": 404, "doge": 404}


async def test_the_sitemap_lists_only_clean_canonical_addresses(test_context):
    await _seed(test_context, count=2)
    base = str(test_context["settings"].public_base_url).rstrip("/")
    locations = await _sitemap(test_context)

    assert len(locations) == len(set(locations))
    assert f"{base}/" in locations
    assert f"{base}/markets" in locations
    assert f"{base}/passports/btc" in locations
    for location in locations:
        assert location.startswith(f"{base}/"), location
        path = location.removeprefix(base)
        # A standard in the address, the printable report, account pages and the
        # dashboard are never pages to index.
        assert "?" not in path, location
        assert not path.endswith("/report"), location
        for private in ("/signin", "/signup", "/reset-password", "/dashboard", "/api/"):
            assert not path.startswith(private), location
        # Every listed address answers itself, never a redirect.
        response = await test_context["client"].get(path, follow_redirects=False)
        assert response.status_code == 200, (location, response.status_code)


async def test_a_newly_published_coin_reaches_the_sitemap_by_itself(test_context, monkeypatch):
    """Nobody edits the sitemap: a coin published is listed once the short cache ends."""

    clock = [1000.0]
    monkeypatch.setattr(public_passports, "monotonic", lambda: clock[0])
    await _seed(test_context, count=1)
    base = str(test_context["settings"].public_base_url).rstrip("/")
    assert f"{base}/passports/eth" not in await _sitemap(test_context)

    await _seed(test_context, count=1, symbol="ETH", name="Ethereum")
    # Within the cache time the list is reused, so a busy crawler costs nothing.
    assert f"{base}/passports/eth" not in await _sitemap(test_context)
    clock[0] += public_passports._PUBLIC_PASSPORTS_SECONDS + 1
    assert f"{base}/passports/eth" in await _sitemap(test_context)


async def test_before_launch_the_sitemap_lists_no_passport(waitlist_context):
    """The Passports are hidden with the Market page, so the sitemap leaves them out."""

    await _seed(waitlist_context, count=1)
    locations = await _sitemap(waitlist_context)
    assert not any("/passports/" in location for location in locations)


# -- one template for every coin: question, answer, title, links ---------------------------

#: (symbol, name, kind as written, network as stored, heading, identity line). A native
#: coin, a token whose network is a platform id, and a coin named by its own symbol.
COINS = [
    ("BTC", "Bitcoin", "native_coin", "Bitcoin", "Is Bitcoin (BTC) Halal?",
     "BTC · Bitcoin · Native coin"),
    ("USDC", "USD Coin", "token", "ethereum", "Is USD Coin (USDC) Halal?",
     "USDC · Ethereum · Token"),
    ("XRP", "XRP", "native_coin", "XRP", "Is XRP Halal?", "XRP · XRP · Native coin"),
]

NOT_A_RULING = (
    "This is a methodology-specific screening result, not a universal religious ruling."
)


def _head(html: str) -> dict[str, object]:
    """What a search engine reads first: title, description, canonical, previews, data."""

    def meta(attribute: str, key: str) -> str:
        found = re.search(rf'<meta {attribute}="{re.escape(key)}" content="([^"]*)"', html)
        assert found, key
        return unescape(found.group(1))

    canonical = re.search(r'<link rel="canonical" href="([^"]+)">', html)
    title = re.search(r"<title>([^<]*)</title>", html)
    assert canonical and title
    return {
        "title": unescape(title.group(1)),
        "description": meta("name", "description"),
        "canonical": canonical.group(1),
        "og_title": meta("property", "og:title"),
        "og_description": meta("property", "og:description"),
        "json_ld": [
            json.loads(block)
            for block in re.findall(
                r'<script type="application/ld\+json">(.*?)</script>', html, flags=re.S
            )
        ],
    }


def _text(html: str, marker: str) -> str:
    """The text of the element carrying ``marker``, without its tags."""

    found = re.search(rf"<[a-z0-9]+ [^>]*{marker}[^>]*>(.*?)</(?:p|h1)>", html, flags=re.S)
    assert found, marker
    return unescape(re.sub(r"<[^>]+>", "", found.group(1))).strip()


@pytest.mark.parametrize(("symbol", "name", "kind", "network", "heading", "line"), COINS)
async def test_every_coin_opens_with_its_own_question_and_answer(
    test_context, symbol, name, kind, network, heading, line
):
    await _seed(
        test_context, count=1, symbol=symbol, name=name, asset_type=kind, network=network
    )
    html = (await test_context["client"].get(f"/passports/{symbol.lower()}")).text

    assert f"<h1>{heading}</h1>" in html
    assert html.count("<h1>") == 1
    assert f"{symbol} Evidence Passport" in _text(html, 'class="t-eyebrow"')
    assert _text(html, "data-passport-identity-line") == line
    answer = _text(html, "data-passport-answer")
    assert answer == (
        f"Under First test standard v1.0, {name} is currently classified as Eligible. "
        f"{NOT_A_RULING}"
    )
    # The answer comes before the standard picker and the result, as the page is read.
    assert html.index("data-passport-answer") < html.index('class="t-standard')
    assert html.index("data-passport-answer") < html.index('class="t-pq-answer"')

    head = _head(html)
    base = str(test_context["settings"].public_base_url).rstrip("/")
    assert head["title"] == (
        f"Is {name} Halal? {symbol} Shariah Screening & Evidence | Hilal Markets"
    )
    assert head["og_title"] == f"Is {name} Halal? {symbol} Shariah Screening & Evidence"
    for fact in (symbol, "Eligible", "First test standard v1.0", "Hilal Markets"):
        assert fact in head["description"], fact
    assert head["og_description"] == head["description"]
    assert head["canonical"] == f"{base}/passports/{symbol.lower()}"


@pytest.mark.parametrize("index", range(len(STANDARDS)))
async def test_the_answer_follows_the_standard_and_the_canonical_does_not(test_context, index):
    """Each standard answers for itself; every standard's page names the one clean URL."""

    methodologies = await _seed(test_context, count=len(STANDARDS))
    chosen = methodologies[index]
    standard, _status, label = STANDARDS[index]
    html = (await test_context["client"].get(f"/passports/btc?methodology_id={chosen.id}")).text

    answer = _text(html, "data-passport-answer")
    assert answer.startswith(f"Under {standard} v{chosen.version}, Bitcoin is currently")
    assert f"classified as {label}" in answer
    for other, _other_status, _other_label in STANDARDS:
        if other != standard:
            assert other not in answer
    head = _head(html)
    base = str(test_context["settings"].public_base_url).rstrip("/")
    assert head["canonical"] == f"{base}/passports/btc"
    assert label in str(head["description"])


async def test_no_two_passports_share_their_words(test_context):
    """One template, but never one generic page copied under several addresses."""

    for symbol, name, kind, network, _heading, _line in COINS:
        await _seed(
            test_context, count=1, symbol=symbol, name=name, asset_type=kind, network=network
        )
    pages = [
        (await test_context["client"].get(f"/passports/{symbol.lower()}")).text
        for symbol, *_rest in COINS
    ]
    for field in ("title", "description", "og_title", "canonical"):
        values = [_head(page)[field] for page in pages]
        assert len(set(values)) == len(values), field
    answers = [_text(page, "data-passport-answer") for page in pages]
    assert len(set(answers)) == len(answers)


async def test_the_breadcrumb_runs_home_market_passport(test_context):
    await _seed(test_context, count=1)
    html = (await test_context["client"].get("/passports/btc")).text
    base = str(test_context["settings"].public_base_url).rstrip("/")

    data = _head(html)["json_ld"]
    assert isinstance(data, list)
    (crumbs,) = [item for item in data if item["@type"] == "BreadcrumbList"]
    trail = [
        (item["position"], item["name"], item["item"]) for item in crumbs["itemListElement"]
    ]
    assert trail == [
        (1, "Home", f"{base}/"),
        (2, "Market", f"{base}/markets"),
        (3, "BTC Evidence Passport", f"{base}/passports/btc"),
    ]
    # Site-wide data stays as it is; nothing invents a "halal" type.
    types = {item["@type"] for item in data}
    assert {"Organization", "WebSite", "WebPage", "BreadcrumbList"} <= types
    assert not any("halal" in str(item["@type"]).casefold() for item in data)


def _links(html: str) -> list[str]:
    return re.findall(r'<a [^>]*href="([^"]+)"', html)


async def test_the_page_links_on_to_screening_the_market_and_similar_coins(test_context):
    """Plain links in the page as sent — a crawler needs no script to follow them."""

    await _seed(test_context, count=1)
    await _seed(test_context, count=1, symbol="ETH", name="Ethereum", network="Ethereum")
    await _seed(
        test_context, count=1, symbol="USDC", name="USD Coin", asset_type="token",
        network="ethereum",
    )
    client = test_context["client"]
    html = (await client.get("/passports/btc")).text
    read_next = html[html.index("data-passport-read-next") :]
    links = _links(read_next)

    assert "/how-we-screen" in links
    assert "/markets" in links
    assert "/how-we-screen" in _links(html[: html.index("data-passport-tabs")])
    related = [link for link in links if link.startswith("/passports/")]
    # ETH first: a native coin like BTC. Never BTC itself.
    assert related == ["/passports/eth", "/passports/usdc"]
    for link in related:
        assert (await client.get(link, follow_redirects=False)).status_code == 200, link
    # A visitor is offered a free account, coming back to the Halal Assets list.
    assert any("/signup?next=" in link for link in links)
    assert "Follow BTC with a free account" in read_next


async def test_a_signed_in_reader_is_sent_to_their_dashboard_instead(test_context):
    await _signup_and_verify(test_context, email="passport-cta@example.com")
    await _seed(test_context, count=1)
    html = (await test_context["client"].get("/passports/btc")).text
    read_next = html[html.index("data-passport-read-next") :]

    assert "Open Halal Assets" in read_next
    assert not any("/signup" in link for link in _links(read_next))


async def test_the_report_names_the_coin_and_carries_the_same_answer(test_context):
    await _seed(test_context, count=1, symbol="USDC", name="USD Coin", asset_type="token",
                network="ethereum")
    html = (await test_context["client"].get("/passports/usdc/report")).text

    assert "<h1>USD Coin (USDC)</h1>" in html
    assert _head(html)["title"] == "USD Coin (USDC) Evidence report | Hilal Markets"
    assert _text(html, "data-passport-answer").startswith(
        "Under First test standard v1.0, USD Coin is currently classified as Eligible."
    )
    assert "<td>Ethereum</td>" in html
    assert "<td>Token</td>" in html


# -- the links never make a reader wait --------------------------------------------------


def _related(html: str) -> list[str]:
    read_next = html[html.index("data-passport-read-next") :]
    return [link for link in _links(read_next) if link.startswith("/passports/")]


async def test_an_old_list_is_used_at_once_and_refreshed_beside_the_page(
    test_context, monkeypatch
):
    clock = [1000.0]
    monkeypatch.setattr(public_passports, "monotonic", lambda: clock[0])
    await _seed(test_context, count=1)
    await _seed(test_context, count=1, symbol="ETH", name="Ethereum", network="Ethereum")
    client = test_context["client"]
    assert _related((await client.get("/passports/btc")).text) == ["/passports/eth"]

    await _seed(test_context, count=1, symbol="SOL", name="Solana", network="Solana")
    clock[0] += public_passports._PUBLIC_PASSPORTS_SECONDS + 1
    # Out of date: the page is sent with the list it has, and a new one is built.
    assert _related((await client.get("/passports/btc")).text) == ["/passports/eth"]
    for task in list(public_passports._building.values()):
        await task
    assert _related((await client.get("/passports/btc")).text) == [
        "/passports/eth",
        "/passports/sol",
    ]


async def test_with_no_list_yet_the_page_waits_only_briefly(test_context, monkeypatch):
    await _seed(test_context, count=1)
    await _seed(test_context, count=1, symbol="ETH", name="Ethereum", network="Ethereum")
    collect = public_passports._collect
    release = asyncio.Event()

    async def slow_collect(session, settings):
        await release.wait()
        return await collect(session, settings)

    monkeypatch.setattr(public_passports, "_collect", slow_collect)
    monkeypatch.setattr(public_passports, "_LINKS_WAIT_SECONDS", 0.05)
    client = test_context["client"]
    page = await client.get("/passports/btc")
    # The page is complete, only without the links to other Passports.
    assert page.status_code == 200
    assert "Is Bitcoin (BTC) Halal?" in page.text
    assert _related(page.text) == []
    assert "/how-we-screen" in _links(page.text)

    release.set()
    for task in list(public_passports._building.values()):
        await task
    assert _related((await client.get("/passports/btc")).text) == ["/passports/eth"]


async def test_a_failed_refresh_keeps_the_old_list(test_context, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(public_passports, "monotonic", lambda: clock[0])
    await _seed(test_context, count=1)
    await _seed(test_context, count=1, symbol="ETH", name="Ethereum", network="Ethereum")
    client = test_context["client"]
    assert _related((await client.get("/passports/btc")).text) == ["/passports/eth"]

    async def broken(session, settings):
        raise RuntimeError("database went away")

    monkeypatch.setattr(public_passports, "_collect", broken)
    clock[0] += public_passports._PUBLIC_PASSPORTS_SECONDS + 1
    for _attempt in range(2):
        page = await client.get("/passports/btc")
        assert page.status_code == 200
        assert _related(page.text) == ["/passports/eth"]
        for task in list(public_passports._building.values()):
            await task
