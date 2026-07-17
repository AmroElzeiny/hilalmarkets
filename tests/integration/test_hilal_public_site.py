import html
import re

from ai_market_monitor.core.plans import PLAN_DEFINITIONS
from ai_market_monitor.core.site_content import (
    DASHBOARD_NAVIGATION,
    FOOTER_NAVIGATION,
    PUBLIC_NAVIGATION,
    PUBLIC_PAGES,
)


async def _signup(test_context, email: str) -> None:
    response = await test_context["client"].post(
        "/signup",
        data={
            "email": email,
            "display_name": "Hilal Public Test",
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    code = test_context["settings"].email_test_outbox[-1]["code"]
    verified = await test_context["client"].post(
        "/signup/verify",
        data={"email": email, "code": code},
        follow_redirects=False,
    )
    assert verified.status_code == 303


async def test_every_public_page_renders_unique_metadata_without_prototype_content(
    test_context,
):
    titles: set[str] = set()
    canonicals: set[str] = set()

    for page in PUBLIC_PAGES:
        response = await test_context["client"].get(page.path)
        assert response.status_code == 200, page.path
        content = response.text
        title = re.search(r"<title>([^<]+)</title>", content)
        canonical = re.search(r'<link rel="canonical" href="([^"]+)">', content)
        assert title and page.title in html.unescape(title.group(1)), page.path
        assert canonical and canonical.group(1).endswith(page.path), page.path
        assert title.group(1) not in titles, page.path
        assert canonical.group(1) not in canonicals, page.path
        titles.add(title.group(1))
        canonicals.add(canonical.group(1))
        assert "TODO_" not in content
        assert "#TODO" not in content
        ids = re.findall(r'\sid="([^"]+)"', content)
        assert len(ids) == len(set(ids)), f"duplicate id on {page.path}"
        assert 'type="application/ld+json"' in content


async def test_public_header_and_footer_follow_the_central_navigation(test_context):
    response = await test_context["client"].get("/features")
    assert response.status_code == 200
    content = html.unescape(response.text)

    header_labels = re.findall(
        r'class="public-nav-link[^\"]*"[^>]*>([^<]+)</a>',
        content,
    )
    assert header_labels == [item.label for item in PUBLIC_NAVIGATION]
    assert content.index(">Sign in</a>") < content.index(">Start free</a>")

    for group in FOOTER_NAVIGATION:
        assert f"<h2>{group.label}</h2>" in content
        for item in group.items:
            assert f">{item.label}</a>" in content
    assert "data-cookie-settings" in content
    assert ">Cookie Settings</button>" in content


async def test_public_screening_uses_safe_empty_state_without_sample_claims(test_context):
    response = await test_context["client"].get("/")
    assert response.status_code == 200
    content = response.text
    assert "Screening preview is not available yet" in content
    assert "No active screening methodology is published" in content
    assert "85% ready" not in content
    assert "Sample assessment" not in content
    assert "definitely halal" not in content.lower()
    assert "universally halal" not in content.lower()


async def test_public_sitemap_and_robots_exclude_private_surfaces(test_context):
    sitemap = await test_context["client"].get("/sitemap.xml")
    assert sitemap.status_code == 200
    for path in ("/", *(page.path for page in PUBLIC_PAGES)):
        assert f"{path}</loc>" in sitemap.text
    assert "/dashboard" not in sitemap.text
    assert "/system-brain" not in sitemap.text

    robots = await test_context["client"].get("/robots.txt")
    assert robots.status_code == 200
    assert "Disallow: /dashboard" in robots.text
    assert "Disallow: /system-brain" in robots.text
    assert "Disallow: /api/" in robots.text


async def test_pricing_and_billing_share_the_public_plan_catalog(test_context):
    pricing = await test_context["client"].get("/pricing")
    assert pricing.status_code == 200
    assert PLAN_DEFINITIONS["demo"].name in pricing.text
    assert "$0" in pricing.text
    assert "free and invite-only" in pricing.text
    assert "$12" not in pricing.text
    assert "$29" not in pricing.text
    assert "Choose Core" not in pricing.text
    assert "Choose Pro" not in pricing.text
    for internal_code in ("creator", "community", "lifetime", "pro_trial"):
        assert PLAN_DEFINITIONS[internal_code].name not in pricing.text

    await _signup(test_context, "catalog-parity@example.com")
    billing = await test_context["client"].get("/dashboard/billing")
    assert billing.status_code == 200
    assert PLAN_DEFINITIONS["demo"].name in billing.text
    assert "Current plan" in billing.text
    assert "Paid billing is disabled" in billing.text
    for code in ("trader", "pro"):
        assert f"/dashboard/billing/checkout?plan_code={code}" not in billing.text
    for internal_code in ("creator", "community", "lifetime", "pro_trial"):
        assert f"plan_code={internal_code}" not in billing.text

    blocked = await test_context["client"].get(
        "/dashboard/billing/checkout?plan_code=lifetime",
        follow_redirects=False,
    )
    assert blocked.status_code == 303
    assert blocked.headers["location"] == "/dashboard/billing?error=billing_disabled"


async def test_dashboard_navigation_matches_the_customer_information_architecture(
    test_context,
):
    await _signup(test_context, "dashboard-navigation@example.com")
    dashboard = await test_context["client"].get("/dashboard")
    assert dashboard.status_code == 200
    expected = [
        item.label
        for group in DASHBOARD_NAVIGATION
        for item in group.items
    ]
    nav_match = re.search(
        r'<nav data-testid="dashboard-nav".*?</nav>',
        dashboard.text,
        flags=re.DOTALL,
    )
    assert nav_match
    nav = html.unescape(nav_match.group(0))
    positions = [nav.index(f"<span>{label}</span>") for label in expected]
    assert positions == sorted(positions)
    assert "Portfolio" not in nav
    assert "Referrals" not in nav
    assert "System Brain" not in nav

    market_check = await test_context["client"].get(
        "/dashboard/check-market",
        follow_redirects=False,
    )
    assert market_check.status_code == 303
    assert market_check.headers["location"] == "/dashboard/strategies/new?mode=scanner"


def test_screened_opportunities_use_one_shared_component():
    with open(
        "src/ai_market_monitor/templates/hilal/public/index.html",
        encoding="utf-8",
    ) as handle:
        public_template = handle.read()
    with open(
        "src/ai_market_monitor/templates/hilal/dashboard/market.html",
        encoding="utf-8",
    ) as handle:
        dashboard_template = handle.read()

    shared_import = 'from "hilal/macros/opportunity_card.html" import opportunity_card'
    assert shared_import in public_template
    assert shared_import in dashboard_template
    assert "{{ opportunity_card(" in public_template
    assert "{{ opportunity_card(" in dashboard_template
