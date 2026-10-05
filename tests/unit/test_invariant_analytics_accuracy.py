"""The numbers on the Stats page and in Google Analytics describe people, once each.

Problems measured on the live site on 5 October 2026, each closed here as a class:

* the X ad pixel ran for visitors who had only accepted *analytics* — a custom-code tag in
  the Tag Manager container fired whenever the container loaded;
* a cookie choice made on ``hilalmarkets.com`` was invisible on ``app.hilalmarkets.com``,
  so the product asked again and measured nothing until it was answered twice;
* the site's own counter counted crawlers and headless browsers as visitors, and filed a
  click from one of our pages to another as a visit "from another website".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import SiteVisit
from ai_market_monitor.services.site_analytics import (
    TAGS_BY_KEY,
    SiteAnalyticsService,
    classify_source,
    is_automated,
    own_hosts,
)

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "src/ai_market_monitor/templates"
STATIC = ROOT / "src/ai_market_monitor/static"

#: Every page shell that sets up the consent defaults and can load Tag Manager.
SHELLS = (
    TEMPLATES / "auth.html",
    TEMPLATES / "hilal/base_dashboard.html",
    TEMPLATES / "hilal/base_public.html",
    TEMPLATES / "hilal/public/react_site.html",
    STATIC / "landing/index.html",
    ROOT / "Hilal-Markets-Website/index.html",
)
#: The shells that hand the consent script its configuration.
CONFIGURED_SHELLS = SHELLS[:4]

BLOCKLIST = 'window.dataLayer.push({ "gtm.blocklist": ["html", "customScripts"] });'


# --------------------------------------------------------------------------------
# Tag Manager may not run custom code
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS, ids=lambda path: path.name)
def test_every_shell_blocks_custom_code_tags_before_anything_can_load_tag_manager(shell):
    text = shell.read_text(encoding="utf-8")
    assert BLOCKLIST in text
    assert text.index(BLOCKLIST) < text.index('"consent", "default"')
    for loader in ("hilalmarkets-consent.js", "landing/assets/landing.js", "/src/main.tsx"):
        if loader in text:
            assert text.index(BLOCKLIST) < text.index(loader)


# --------------------------------------------------------------------------------
# One cookie choice for the website and the product
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("public", "app", "expected"),
    [
        ("https://hilalmarkets.com", "https://app.hilalmarkets.com", "hilalmarkets.com"),
        ("https://app.hilalmarkets.com", "https://hilalmarkets.com", "hilalmarkets.com"),
        ("https://hilalmarkets.com", "https://hilalmarkets.com", None),
        ("https://hilalmarkets.com", "https://other.example", None),
        ("http://localhost:8000", "http://app.localhost:8000", None),
        ("https://hilalmarkets.com", None, None),
    ],
)
def test_the_choice_is_shared_only_on_a_real_parent_name(public, app, expected):
    settings = Settings(_env_file=None, public_base_url=public, app_base_url=app)
    assert settings.consent_cookie_domain == expected


@pytest.mark.parametrize("shell", CONFIGURED_SHELLS, ids=lambda path: path.name)
def test_every_configured_shell_hands_the_consent_script_the_shared_name(shell):
    assert '"cookieDomain": consent_cookie_domain(),' in shell.read_text(encoding="utf-8")


def test_the_consent_script_writes_the_shared_cookie_and_reads_it_first():
    script = (STATIC / "hilalmarkets-consent.js").read_text(encoding="utf-8")
    assert "Domain=${domain}" in script
    read = script[script.index("function read()") : script.index("function cookieDomain()")]
    assert read.index("readCookie()") < read.index("localStorage")


# --------------------------------------------------------------------------------
# The site's own counter
# --------------------------------------------------------------------------------

PEOPLE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 10; Cubot Note 7 Build/QP1A) Chrome/120 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Gecko/20100101 Firefox/131.0",
)
PROGRAMS = (
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "HeadlessChrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 11; moto g power) Chrome-Lighthouse",
    "Mozilla/5.0 Playwright/1.60",
    "python-requests/2.32",
    "curl/8.5.0",
    "Mozilla/5.0 (compatible; YandexBot/3.0)",
    "",
)


@pytest.mark.parametrize("agent", PEOPLE)
def test_a_person_s_browser_is_counted(agent):
    assert is_automated(agent) is False


@pytest.mark.parametrize("agent", PROGRAMS)
def test_a_program_is_not_counted(agent):
    assert is_automated(agent) is True


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        public_base_url="https://hilalmarkets.com",
        app_base_url="https://app.hilalmarkets.com",
    )


@pytest.mark.parametrize(
    "referrer",
    [
        "https://hilalmarkets.com/markets",
        "https://www.hilalmarkets.com/",
        "https://app.hilalmarkets.com/dashboard",
        "https://APP.hilalmarkets.com/signin",
    ],
)
def test_a_move_between_our_own_pages_is_internal(referrer):
    assert classify_source(referrer, None, own_hosts(_settings())) == "internal"


def test_another_website_is_still_a_referral():
    assert classify_source("https://blog.example/post", None, own_hosts(_settings())) == (
        "referral"
    )
    assert "source:internal" in TAGS_BY_KEY


@pytest.mark.parametrize("agent", PROGRAMS[:4])
async def test_a_program_never_becomes_a_visit_row(test_context, agent):
    async with test_context["session_factory"]() as session:
        service = SiteAnalyticsService(session, test_context["settings"])
        result = await service.record(
            event="open",
            session_key="a" * 32,
            path="/",
            remote_address="203.0.113.5",
            user_agent=agent,
        )
        assert result is None
        assert await session.scalar(select(func.count(SiteVisit.id))) == 0


def test_the_page_script_reports_the_page_before_and_returns_by_back():
    script = (STATIC / "hm-visit-analytics.js").read_text(encoding="utf-8")
    assert "openedFrom = window.location.origin + lastPath" in script
    assert 'addEventListener("pageshow"' in script
    assert "event.persisted" in script
