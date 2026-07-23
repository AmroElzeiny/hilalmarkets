import html
import re

from ai_market_monitor.core.plans import PLAN_DEFINITIONS
from ai_market_monitor.core.site_content import (
    DASHBOARD_NAVIGATION,
    FOOTER_NAVIGATION,
    PUBLIC_NAVIGATION,
    PUBLIC_PAGES,
    SOCIAL_PREVIEW_DESCRIPTION,
    SOCIAL_PREVIEW_TITLE,
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
        assert f'<meta name="description" content="{page.description}">' in content
        assert (
            '<meta name="robots" '
            'content="index,follow,max-image-preview:large,max-snippet:-1,'
            'max-video-preview:-1">'
        ) in content
        assert f'<meta property="og:title" content="{SOCIAL_PREVIEW_TITLE}">' in content
        assert (
            f'<meta property="og:description" content="{SOCIAL_PREVIEW_DESCRIPTION}">'
            in content
        )
        assert '<meta property="og:site_name" content="Hilal Markets">' in content
        assert '<meta property="og:image:type" content="image/png">' in content
        assert '<meta property="og:image:width" content="1200">' in content
        assert '<meta property="og:image:height" content="630">' in content
        assert 'property="og:image:alt"' in content
        assert '<meta name="twitter:card" content="summary_large_image">' in content
        assert f'<meta name="twitter:title" content="{SOCIAL_PREVIEW_TITLE}">' in content
        twitter_description = (
            f'<meta name="twitter:description" '
            f'content="{SOCIAL_PREVIEW_DESCRIPTION}">'
        )
        assert twitter_description in content
        assert 'name="twitter:image"' in content
        assert 'name="twitter:image:alt"' in content
        assert '/static/hilalmarkets-social-preview.png' in content
        assert '"@type": "WebPage"' in content
        assert title.group(1) not in titles, page.path
        assert canonical.group(1) not in canonicals, page.path
        titles.add(title.group(1))
        canonicals.add(canonical.group(1))
        assert "TODO_" not in content
        assert "#TODO" not in content
        ids = re.findall(r'\sid="([^"]+)"', content)
        assert len(ids) == len(set(ids)), f"duplicate id on {page.path}"
        assert 'type="application/ld+json"' in content


async def test_homepage_uses_requested_social_preview_copy_and_bundled_image(
    test_context,
):
    response = await test_context["client"].get("/")
    assert response.status_code == 200
    content = response.text

    assert f"<title>{SOCIAL_PREVIEW_TITLE} | Hilal Markets</title>" in content
    assert f'<meta name="description" content="{SOCIAL_PREVIEW_DESCRIPTION}">' in content
    assert f'<meta property="og:title" content="{SOCIAL_PREVIEW_TITLE}">' in content
    assert f'<meta property="og:description" content="{SOCIAL_PREVIEW_DESCRIPTION}">' in content

    image = await test_context["client"].get(
        "/static/hilalmarkets-social-preview.png"
    )
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/png")
    assert image.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert int.from_bytes(image.content[16:20], "big") == 1200
    assert int.from_bytes(image.content[20:24], "big") == 630


async def test_authentication_pages_are_not_indexable(test_context):
    for path in ("/signin", "/signup"):
        response = await test_context["client"].get(path)
        assert response.status_code == 200
        assert '<meta name="robots" content="noindex,nofollow,noarchive">' in response.text


async def test_public_legal_pages_do_not_expose_internal_launch_placeholders(
    test_context,
):
    forbidden = (
        "Draft structure",
        "qualified counsel review required before launch",
        "Not final legal advice or terms",
        "Operating entity details are not yet configured",
        "Governing law is not yet configured",
    )

    for path in ("/cookies", "/risk-disclosure"):
        response = await test_context["client"].get(path)
        assert response.status_code == 200, path
        for phrase in forbidden:
            assert phrase not in response.text, (path, phrase)


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


async def test_public_landing_is_available_without_screening_seed_data(test_context):
    response = await test_context["client"].get("/")
    assert response.status_code == 200
    content = response.text
    assert "/static/landing/assets/landing.js" in content
    assert "Screening preview is not available yet" not in content
    assert "85% ready" not in content
    assert "Sample assessment" not in content
    assert "definitely halal" not in content.lower()
    assert "universally halal" not in content.lower()


async def test_every_public_shell_exposes_gtm_config_without_preloading(test_context):
    settings = test_context["settings"]
    settings.vite_analytics_enabled = True
    settings.vite_gtm_id = "GTM-HILALTEST1"
    settings.vite_ga4_measurement_id = None
    settings.marketing_consent_enabled = True
    settings.vite_x_pixel_enabled = True
    settings.vite_x_pixel_id = "re20l"

    for path in ("/", *(page.path for page in PUBLIC_PAGES)):
        response = await test_context["client"].get(path)
        assert response.status_code == 200, path
        content = response.text
        assert "GTM-HILALTEST1" in content, path
        assert "re20l" in content, path
        assert '"xPixelEnabled": true' in content, path
        assert 'analytics_storage: "denied"' in content, path
        assert "googletagmanager.com/gtm.js" not in content, path
        assert "googletagmanager.com/ns.html" not in content, path
        assert "static.ads-twitter.com/uwt.js" not in content, path
        assert "G-EJN34D4BEM" not in content, path

    homepage = await test_context["client"].get("/")
    assert '"gtmContainerId"' in homepage.text
    assert '"gtmId"' in homepage.text
    assert homepage.text.count("GTM-HILALTEST1") == 2


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


def test_landing_and_contact_templates_delegate_to_the_supplied_react_site():
    with open(
        "src/ai_market_monitor/templates/hilal/public/index.html",
        encoding="utf-8",
    ) as handle:
        public_template = handle.read()
    with open(
        "src/ai_market_monitor/templates/hilal/public/contact.html",
        encoding="utf-8",
    ) as handle:
        contact_template = handle.read()

    include = '{% include "hilal/public/react_site.html" %}'
    assert public_template.strip() == include
    assert contact_template.strip() == include
