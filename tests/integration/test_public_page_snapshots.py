"""The words of the React pages reach the HTML a crawler reads — and never a stale copy.

Checked through the real app:

* every public page whose content is drawn by the landing bundle has a kept copy to
  use, and carries the version key that copy is matched against;
* the key is the key of exactly what the bundle is handed: any change to that data is
  a new key, and analytics settings (which draw nothing) are not part of it;
* a copy taken under the page's key is put in the page's empty slots; a copy taken
  under any other key is not, so an old price or an old coin list can never be shown;
* the Full Report is kept out of search results; the Passport itself stays indexable.
"""

from __future__ import annotations

import json
import re
from html import unescape

import pytest

from ai_market_monitor.api.routers import public as public_router
from ai_market_monitor.core.site_content import PUBLIC_PAGES
from ai_market_monitor.services import public_page_snapshots as snapshots
from tests.integration.test_public_passports import _seed

_RUNTIME = re.compile(r"window\.HilalMarketsRuntimeConfig = (\{.*?\});", re.DOTALL)
_ROOT = re.compile(r'<div id="root">(.*?)</div>', re.DOTALL)


def _runtime_config(html: str) -> dict:
    match = _RUNTIME.search(html)
    assert match, "the page published no runtime config"
    return json.loads(match.group(1))


def _drawn_by_the_bundle(html: str) -> bool:
    return "window.HilalMarketsRuntimeConfig" in html and 'id="root"' in html


async def test_every_page_the_bundle_draws_has_a_copy_and_a_matching_key(test_context):
    client = test_context["client"]
    paths = ["/", *(page.path for page in PUBLIC_PAGES)]
    drawn = []
    for path in paths:
        html = (await client.get(path)).text
        if not _drawn_by_the_bundle(html):
            continue
        drawn.append(path)
        root = _ROOT.search(html)
        if root and root.group(1).strip() == "" or 'id="hm-site-footer"' in html:
            # An empty slot is drawn in the browser, so it needs a kept copy.
            assert snapshots.snapshot_source(path) is not None, path
        key = snapshots.page_key(html)
        assert key == snapshots.render_key(_runtime_config(html)), path
    # The pages Google reported, and the screener, are among them.
    assert {"/", "/features", "/how-it-works", "/markets"} <= set(drawn)


def test_every_listed_source_is_a_public_page():
    known = {"/", *(page.path for page in PUBLIC_PAGES)}
    assert set(snapshots.SNAPSHOT_SOURCE_BY_PATH.values()) <= known
    assert snapshots.snapshot_source("/passports/btc") == "/markets"
    assert snapshots.snapshot_source("/passports/btc/report") == "/markets"
    assert snapshots.snapshot_source("/signin") is None


BASE_CONFIG = {
    "analytics": {"gtmId": "GTM-A", "debug": False},
    "legal": {"legalName": "A", "privacyEmail": "p@example.test"},
    "chrome": {"marketHref": "/markets", "footerGroups": []},
    "methodology": None,
    "waitlist": {"mode": False},
    "commerce": {"plans": [{"code": "trader", "monthlyPrice": "10.50"}]},
}


@pytest.mark.parametrize(
    "change",
    [
        ("legal", "privacyEmail", "other@example.test"),
        ("chrome", "marketHref", None),
        ("waitlist", "mode", True),
        ("commerce", "plans", [{"code": "trader", "monthlyPrice": "15.00"}]),
        ("methodology", None, {"approved": 69}),
    ],
    ids=lambda change: change[0] + ("." + change[1] if change[1] else ""),
)
def test_any_change_to_what_the_bundle_draws_is_a_new_key(change):
    section, field, value = change
    changed = json.loads(json.dumps(BASE_CONFIG))
    if field is None:
        changed[section] = value
    else:
        changed[section][field] = value
    assert snapshots.render_key(changed) != snapshots.render_key(BASE_CONFIG)


def test_analytics_settings_are_not_part_of_the_key():
    changed = json.loads(json.dumps(BASE_CONFIG))
    changed["analytics"] = {"gtmId": "GTM-B", "debug": True}
    assert snapshots.render_key(changed) == snapshots.render_key(BASE_CONFIG)


def test_a_copy_is_used_only_under_its_own_key():
    stored = json.dumps(
        {"key": "abc", "slots": {"root": "<h1>Hi</h1>", "elsewhere": "<p>x</p>"}}
    )
    assert snapshots.snapshot_slots(stored, "abc") == {"root": "<h1>Hi</h1>"}
    assert snapshots.snapshot_slots(stored, "abd") == {}
    assert snapshots.snapshot_slots(None, "abc") == {}
    assert snapshots.snapshot_slots("not json", "abc") == {}
    assert snapshots.snapshot_slots(json.dumps(["abc"]), "abc") == {}


def test_a_kept_copy_carries_no_script_and_no_already_seen_mark():
    html = (
        '<section class="reveal is-visible hm-hero"><h1>Features</h1>'
        "<script>alert(1)</script><SCRIPT src=x></SCRIPT>"
        '<div class="is-visible">a</div><p class="not-is-visible-x">b</p></section>'
    )
    cleaned = snapshots.clean_slot(html)
    assert "script" not in cleaned.lower()
    assert 'class="reveal  hm-hero"' in cleaned
    assert 'class=""' in cleaned
    assert "not-is-visible-x" in cleaned


@pytest.mark.parametrize("matches", [True, False], ids=["same key", "other key"])
async def test_the_copy_is_put_in_the_page_only_when_its_key_matches(
    test_context, monkeypatch, matches
):
    async def fake_load(settings, path, key):
        stored = json.dumps(
            {
                "key": key if matches else "0" * 32,
                "slots": {"root": "<h1>Everything you need</h1>"},
            }
        )
        return snapshots.snapshot_slots(stored, key)

    monkeypatch.setattr(public_router, "load_snapshot", fake_load)
    html = (await test_context["client"].get("/features")).text
    root = _ROOT.search(html)
    assert root
    if matches:
        assert root.group(1) == "<h1>Everything you need</h1>"
    else:
        assert root.group(1) == ""


async def test_a_passport_wears_the_market_pages_header_and_footer(test_context, monkeypatch):
    await _seed(test_context, count=1)
    asked: list[str] = []

    async def fake_load(settings, path, key):
        asked.append(path)
        return {"root": "<nav>Header</nav>", "hm-site-footer": "<footer>Footer</footer>"}

    monkeypatch.setattr(public_router, "load_snapshot", fake_load)
    html = (await test_context["client"].get("/passports/btc")).text
    assert '<div id="root"><nav>Header</nav></div>' in html
    assert '<div id="hm-site-footer"><footer>Footer</footer></div>' in html
    assert asked == ["/passports/btc"]


def _robots(html: str) -> str:
    match = re.search(r'<meta name="robots" content="([^"]+)">', html)
    assert match
    return unescape(match.group(1))


async def test_the_full_report_is_kept_out_of_search_results(test_context):
    await _seed(test_context, count=2)
    client = test_context["client"]
    passport = await client.get("/passports/btc")
    assert _robots(passport.text).startswith("index,follow")
    assert "x-robots-tag" not in passport.headers

    report = await client.get("/passports/btc/report")
    assert report.status_code == 200
    assert _robots(report.text) == "noindex,follow"
    assert report.headers["x-robots-tag"] == "noindex,follow"
    # Still one coin page in search: the report names the Passport as the canonical one.
    assert re.search(r'<link rel="canonical" href="[^"]*/passports/btc">', report.text)
