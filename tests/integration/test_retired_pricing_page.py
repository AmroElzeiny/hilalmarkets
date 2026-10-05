"""The old Pricing page is gone, and nothing leads to it any more.

``/pricing`` was a separate page that went stale. It was taken down on 4 October 2026.
Prices are now shown in exactly one place: the Pricing section of the home page
(``/#pricing``). The old address only forwards there, permanently, so a bookmark or a
search result still lands somewhere useful and search engines drop the address.

Taking a page down is the same failure class as hiding one: it has to disappear from
every surface at once — the address itself, the sitemap, the menus, every rendered page
on both hostnames, the chat assistant, the source cards, the Telegram bot and the source
files. Each is checked here, across every variant of the address and every stage.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import AnyHttpUrl

from ai_market_monitor.core import dashboard_paths
from ai_market_monitor.core.config import get_settings
from ai_market_monitor.core.launch_stage import LaunchStage
from ai_market_monitor.core.site_content import PUBLIC_PAGES, WAITLIST_ANCHOR
from ai_market_monitor.services.public_chat import PUBLIC_ROUTE_PATHS
from ai_market_monitor.services.source_previews import SOURCE_PAGES

pytestmark = pytest.mark.anyio

SITE = "http://testserver"
APP = "http://app.testserver"
ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "src" / "ai_market_monitor" / "templates"

#: A link to the retired address: `/pricing` followed by nothing, `/`, `?` or `#`, and
#: optionally written on a hostname. `/#pricing` (the home page section) does not match.
RETIRED_HREF = re.compile(
    r"""href\s*=\s*["'](?:https?://[^/"']+)?/pricing(?:[/?#][^"']*)?["']""", re.IGNORECASE
)

#: Every public address a visitor can open, the home page included.
PUBLIC_PATHS = ("/", *(item.path for item in PUBLIC_PAGES))


def _with(test_context, **update):
    changed = test_context["settings"].model_copy(update=update)
    test_context["app"].dependency_overrides[get_settings] = lambda: changed
    return changed


def _with_hosts(test_context, **update):
    return _with(
        test_context,
        public_base_url=AnyHttpUrl(SITE),
        app_base_url=AnyHttpUrl(APP),
        **update,
    )


def test_the_page_is_no_longer_a_public_page() -> None:
    assert all(item.page != "pricing" for item in PUBLIC_PAGES)
    assert all(item.path.rstrip("/") != "/pricing" for item in PUBLIC_PAGES)
    assert not (TEMPLATES / "hilal" / "public" / "pricing.html").exists()
    assert dashboard_paths.PRICING_PATH == "/#pricing"


@pytest.mark.parametrize(
    "address", ["/pricing", "/pricing?utm_source=google", "/pricing?plan=trader#cards"]
)
async def test_every_form_of_the_old_address_forwards_permanently_to_the_home_section(
    test_context, address
):
    response = await test_context["client"].get(address, follow_redirects=False)
    assert response.status_code == 301, address
    assert response.headers["location"] == "/#pricing", address
    # Never the old page's content.
    assert "Choose how deeply you want to monitor" not in response.text


async def test_a_trailing_slash_ends_on_the_home_page_too(test_context):
    response = await test_context["client"].get("/pricing/", follow_redirects=True)
    assert response.status_code == 200
    assert response.url.path == "/"
    assert any(step.status_code == 301 for step in response.history)


async def test_on_the_product_hostname_it_forwards_to_the_website_not_the_dashboard(
    test_context,
):
    _with_hosts(test_context)
    for host in (SITE, APP):
        response = await test_context["client"].get(
            f"{host}/pricing", follow_redirects=False
        )
        assert response.status_code == 301, host
        assert response.headers["location"] == f"{SITE}/#pricing", host


async def test_before_launch_it_forwards_to_the_waitlist(test_context):
    _with(test_context, public_waitlist_mode=True)
    response = await test_context["client"].get("/pricing", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == WAITLIST_ANCHOR


@pytest.mark.parametrize("stage", list(LaunchStage), ids=lambda stage: stage.value)
async def test_no_stage_lists_the_old_address_in_the_sitemap(test_context, stage):
    _with(test_context, launch_stage=stage, public_waitlist_mode=False)
    sitemap = (await test_context["client"].get("/sitemap.xml")).text
    assert "/pricing<" not in sitemap
    assert "/pricing/" not in sitemap


@pytest.mark.parametrize("host", [SITE, APP])
@pytest.mark.parametrize("path", PUBLIC_PATHS)
async def test_no_public_page_links_to_the_old_address(test_context, host, path):
    _with_hosts(test_context)
    response = await test_context["client"].get(f"{host}{path}", follow_redirects=True)
    assert response.status_code == 200, (host, path)
    assert RETIRED_HREF.findall(response.text) == [], (host, path)


@pytest.mark.parametrize("host", [SITE, APP])
async def test_the_header_pricing_link_opens_the_home_section_on_the_website(
    test_context, host
):
    """`/help` is drawn by the Jinja header, which reads the menu from site_content."""

    _with_hosts(test_context)
    page = await test_context["client"].get(f"{host}/help")
    assert page.status_code == 200
    assert f'href="{SITE}/#pricing"' in page.text


def test_no_assistant_route_or_source_card_names_the_old_address() -> None:
    for route_id, (_label, path) in PUBLIC_ROUTE_PATHS.items():
        assert path.rstrip("/") != "/pricing", route_id
    for key, page in SOURCE_PAGES.items():
        assert page.path.rstrip("/") != "/pricing", key
    assert PUBLIC_ROUTE_PATHS["pricing"][1] == "/#pricing"
    assert SOURCE_PAGES["pricing"].path == "/#pricing"


def test_no_template_or_shipped_script_links_to_the_old_address() -> None:
    written = re.compile(r"""["'`](?:https?://[^/"'`]+)?/pricing(?:[/?#][^"'`]*)?["'`]""")
    sources = [
        *TEMPLATES.rglob("*.html"),
        *(ROOT / "src" / "ai_market_monitor" / "static").rglob("*.js"),
        *(ROOT / "Hilal-Markets-Website" / "src").rglob("*.tsx"),
        *(ROOT / "Hilal-Markets-Website" / "src").rglob("*.ts"),
    ]
    assert sources
    offenders = [
        str(path.relative_to(ROOT))
        for path in sources
        if written.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert offenders == []
