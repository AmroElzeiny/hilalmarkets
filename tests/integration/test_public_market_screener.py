"""`/markets` is the site's one Halal Crypto Screener page, and every page answers HEAD.

What is asserted, as rules across the whole family rather than one sample:

* the page's title, heading, description and share preview are the screener's, and its
  canonical address is the clean ``/markets`` whatever the query says;
* there is no second page for the same search: ``/halal-crypto-screener`` does not
  exist and is not in the sitemap, and ``/markets`` is listed exactly once;
* every status is named and explained from one table, the same on ``/markets`` and on
  How We Screen, and the screening code's own names are that table's;
* every question in the page's FAQ is on the page and in its FAQ data, with the same
  answer;
* the page links, in its own HTML, to How We Screen and to exactly the Passports the
  sitemap lists — each of which opens;
* every address the sitemap lists answers HEAD with the status and headers of GET and
  no body; an address that refuses GET still refuses HEAD.
"""

from __future__ import annotations

import json
import re
from html import unescape

import pytest

from ai_market_monitor.api.dependencies import get_market_data_provider
from ai_market_monitor.core.site_content import (
    PUBLIC_PAGE_BY_PAGE,
    SHARIA_STATUS_PRESENTATION,
    market_screener_faqs,
)
from ai_market_monitor.db.models.enums import ShariaAssetStatus
from ai_market_monitor.services.sharia_screening import STATUS_LABELS
from tests.integration.test_public_market_page import VolumeProvider
from tests.integration.test_public_passports import _head, _seed, _sitemap

SEARCH_TITLE = "Halal Crypto Screener — Shariah-Screened Assets With Evidence"


async def _three_passports(context) -> None:
    await _seed(context, count=2)
    await _seed(context, count=1, symbol="ETH", name="Ethereum", network="Ethereum")
    await _seed(
        context, count=1, symbol="USDC", name="USD Coin", asset_type="token",
        network="ethereum",
    )
    context["app"].dependency_overrides[get_market_data_provider] = VolumeProvider


async def _page(context, query: str = "") -> str:
    response = await context["client"].get(f"/markets{query}")
    assert response.status_code == 200
    return response.text


def _section(html: str, marker: str) -> str:
    start = html.index(marker)
    end = html.find("</section>", start)
    return html[start:end]


def _links(html: str) -> list[str]:
    return re.findall(r'<a [^>]*href="([^"]+)"', html)


# -- the head ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", ["", "?exchange=bybit", "?methodology_id=not-a-uuid"])
async def test_the_page_is_the_screener_under_one_canonical_address(test_context, query):
    await _three_passports(test_context)
    html = await _page(test_context, query)
    head = _head(html)
    base = str(test_context["settings"].public_base_url).rstrip("/")
    metadata = PUBLIC_PAGE_BY_PAGE["market"]

    assert head["title"] == f"{SEARCH_TITLE} | Hilal Markets"
    assert head["og_title"] == SEARCH_TITLE
    assert head["description"] == metadata.description
    assert head["og_description"] == metadata.description
    assert head["canonical"] == f"{base}/markets"
    robots = re.search(r'<meta name="robots" content="([^"]+)">', html)
    assert robots and robots.group(1).startswith("index,follow")
    assert re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.S) == ["Halal Crypto Screener"]
    for words in ("halal crypto screener", "shariah standard"):
        assert words in head["description"].casefold(), words


async def test_the_breadcrumb_keeps_the_pages_name(test_context):
    """The search title is for search results; the trail still says Market, as on a Passport."""

    await _three_passports(test_context)
    (crumbs,) = [
        item for item in _head(await _page(test_context))["json_ld"]
        if item["@type"] == "BreadcrumbList"
    ]
    assert [item["name"] for item in crumbs["itemListElement"]] == ["Home", "Market"]


async def test_there_is_no_second_page_for_the_same_search(test_context):
    await _three_passports(test_context)
    base = str(test_context["settings"].public_base_url).rstrip("/")
    response = await test_context["client"].get(
        "/halal-crypto-screener", follow_redirects=False
    )
    assert response.status_code == 404
    locations = await _sitemap(test_context)
    assert locations.count(f"{base}/markets") == 1
    assert not any("screener" in location for location in locations)


# -- statuses ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", list(ShariaAssetStatus), ids=lambda status: status.value)
def test_every_status_has_one_name_and_one_meaning(status):
    words = SHARIA_STATUS_PRESENTATION[status.value]
    assert STATUS_LABELS[status] == words["label"]
    assert words["plain_language"].strip()


def _definitions(html: str) -> dict[str, tuple[str, str]]:
    found = re.findall(
        r'data-status-definition="([a-z_]+)">\s*<span[^>]*>([^<]+)</span>\s*<p[^>]*>([^<]+)</p>',
        html,
    )
    return {key: (unescape(label), unescape(text)) for key, label, text in found}


@pytest.mark.parametrize("path", ["/markets", "/how-we-screen"])
async def test_both_pages_explain_every_status_from_the_one_table(test_context, path):
    await _three_passports(test_context)
    response = await test_context["client"].get(path)
    assert response.status_code == 200
    expected = {
        status.value: (
            SHARIA_STATUS_PRESENTATION[status.value]["label"],
            SHARIA_STATUS_PRESENTATION[status.value]["plain_language"],
        )
        for status in ShariaAssetStatus
    }
    assert _definitions(response.text) == expected


async def test_how_we_screen_sends_a_visitor_to_the_public_screener(test_context):
    await _three_passports(test_context)
    html = (await test_context["client"].get("/how-we-screen")).text
    assert re.search(r'href="/markets">Open the Halal Crypto Screener</a>', html)


# -- the FAQ ----------------------------------------------------------------------------


async def test_every_question_is_on_the_page_and_in_its_faq_data(test_context):
    await _three_passports(test_context)
    html = await _page(test_context)
    faqs = market_screener_faqs()
    (data,) = [item for item in _head(html)["json_ld"] if item["@type"] == "FAQPage"]

    assert [(item["name"], item["acceptedAnswer"]["text"]) for item in data["mainEntity"]] == [
        (item["question"], item["answer"]) for item in faqs
    ]
    shown = re.findall(
        r"<details[^>]*data-market-faq>\s*<summary>(.*?)<span.*?<p>(.*?)</p>", html, re.S
    )
    assert [(unescape(q), unescape(a)) for q, a in shown] == [
        (item["question"], item["answer"]) for item in faqs
    ]
    # Nothing is described as held back from a visitor any more.
    assert "The whole list, every Evidence Passport and the Ask AI chat" in faqs[-1]["answer"]
    for item in faqs:
        assert "first 20" not in item["answer"] and "full list" not in item["answer"]


# -- links ------------------------------------------------------------------------------


async def test_the_page_links_to_how_we_screen_and_every_passport_the_sitemap_lists(
    test_context,
):
    await _three_passports(test_context)
    html = await _page(test_context)
    base = str(test_context["settings"].public_base_url).rstrip("/")

    assert "/how-we-screen" in _links(_section(html, "data-market-read-next"))
    linked = [
        link for link in _links(_section(html, "data-market-passports"))
        if link.startswith("/passports/")
    ]
    in_sitemap = [
        location.removeprefix(base)
        for location in await _sitemap(test_context)
        if "/passports/" in location
    ]
    assert linked == sorted(in_sitemap) == ["/passports/btc", "/passports/eth", "/passports/usdc"]
    assert "3 coins, from A to Z." in _section(html, "data-market-passports")
    for link in linked:
        assert (
            await test_context["client"].get(link, follow_redirects=False)
        ).status_code == 200, link


async def test_with_no_passport_the_section_is_left_out(test_context):
    test_context["app"].dependency_overrides[get_market_data_provider] = VolumeProvider
    html = await _page(test_context)
    assert "data-market-passports" not in html
    assert "data-market-guide" in html


async def test_one_passport_is_counted_as_one_coin(test_context):
    await _seed(test_context, count=1)
    test_context["app"].dependency_overrides[get_market_data_provider] = VolumeProvider
    html = await _page(test_context)
    assert "1 coin, from A to Z." in _section(html, "data-market-passports")


async def test_the_guide_is_only_on_the_public_page(test_context):
    from tests.integration.test_dashboard_web import _signup_and_verify

    await _three_passports(test_context)
    await _signup_and_verify(test_context, email="screener-guide@example.com")
    dashboard = await test_context["client"].get("/dashboard/market")
    assert dashboard.status_code == 200
    assert "data-market-guide" not in dashboard.text
    assert "<h1>Halal Assets</h1>" in dashboard.text


# -- HEAD -------------------------------------------------------------------------------


async def test_every_listed_address_answers_head_as_it_answers_get(test_context):
    await _three_passports(test_context)
    base = str(test_context["settings"].public_base_url).rstrip("/")
    paths = [location.removeprefix(base) for location in await _sitemap(test_context)]
    paths += ["/sitemap.xml", "/robots.txt"]
    assert "/markets" in paths and "/passports/btc" in paths
    client = test_context["client"]
    for path in paths:
        got = await client.get(path, follow_redirects=False)
        head = await client.head(path, follow_redirects=False)
        assert head.status_code == got.status_code == 200, (path, head.status_code)
        assert head.content == b"", path
        assert head.headers["content-type"] == got.headers["content-type"], path
        assert "content-length" in head.headers, path


async def test_head_follows_get_for_a_redirect_and_a_missing_page(test_context):
    client = test_context["client"]
    for path in ("/market", "/halal-crypto-screener"):
        got = await client.get(path, follow_redirects=False)
        head = await client.head(path, follow_redirects=False)
        assert head.status_code == got.status_code, path
        assert head.headers.get("location") == got.headers.get("location"), path
        assert head.content == b"", path


async def test_an_address_that_refuses_get_still_refuses_head(test_context):
    """HEAD is answered as GET, never as some other method the address accepts."""

    client = test_context["client"]
    got = await client.get("/api/v1/public-forms/contact")
    head = await client.head("/api/v1/public-forms/contact")
    assert got.status_code == 405
    assert head.status_code == 405
    assert json.loads(got.content)  # a real refusal body for GET, and none for HEAD
    assert head.content == b""
