import html
import re
from urllib.parse import urlsplit

import pytest

from ai_market_monitor.core.plans import PLAN_DEFINITIONS, plan_offer_payload
from ai_market_monitor.core.site_content import (
    ACCOUNT_ONLY_PATH_PREFIXES,
    COOKIE_SETTINGS_PATH,
    DASHBOARD_NAVIGATION,
    FOOTER_NAVIGATION,
    PUBLIC_NAVIGATION,
    PUBLIC_PAGES,
    SOCIAL_LINKS,
    SOCIAL_PREVIEW_DESCRIPTION,
    SOCIAL_PREVIEW_TITLE,
    WAITLIST_HIDDEN_PAGES,
    is_account_only_path,
)

#: Every public address a visitor can reach, including the landing page, which is not a
#: PUBLIC_PAGES entry. Tests loop over this so a page added later is covered the day it
#: is added rather than the day somebody remembers to extend a list.
ALL_PUBLIC_PATHS = ("/", *(item.path for item in PUBLIC_PAGES))

#: Pre-launch, these two are redirects, not pages: /pricing goes to the waitlist and
#: Halal Assets lives in the dashboard. They are checked by their own assertions.
WAITLIST_REDIRECTED_PATHS = frozenset(
    item.path for item in PUBLIC_PAGES if item.page in WAITLIST_HIDDEN_PAGES
)

_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)


def _hold_pre_launch(test_context) -> None:
    """Put the site back into the public-waitlist stage for one test.

    Every test below about pre-launch behaviour used to read this from the *default*,
    with `assert settings.public_waitlist_mode is True` standing in for setting it. The
    day the product opened, the default changed and sixteen of them failed at once —
    while testing nothing that had broken. A test about a stage names its stage.

    The pre-launch tests are kept rather than deleted because `PUBLIC_WAITLIST_MODE` is
    the live emergency brake: turning it on has to pull the whole site back, and these
    are what prove it still does.
    """

    test_context["settings"].public_waitlist_mode = True


def _react_rendered(template: str) -> bool:
    """True when the page hands its body to the landing bundle.

    Read from the template file rather than from a list kept here, because a list kept
    here is a second place that has to be remembered when a page changes renderer.
    """

    with open(f"src/ai_market_monitor/templates/{template}", encoding="utf-8") as handle:
        return "react_site.html" in handle.read()


def _internal_links(markup: str) -> list[str]:
    """Every same-site address the page invites the reader to open.

    Jinja's ``url_for`` writes a full address, so a template link comes out as
    ``http://testserver/dashboard/market`` rather than ``/dashboard/market``. A scanner
    that only accepted paths beginning with "/" would therefore have seen none of the
    links this test exists to catch, and passed while every one of them was still there.
    """

    links: list[str] = []
    for href in _HREF_RE.findall(html.unescape(markup)):
        parts = urlsplit(href)
        if parts.scheme not in {"", "http", "https"}:
            continue
        if parts.netloc and parts.netloc not in {"testserver", "hilalmarkets.com"}:
            continue
        if parts.path.startswith("/"):
            links.append(parts.path)
    return links


async def _signup(test_context, email: str) -> None:
    response = await test_context["client"].post(
        "/signup/password",
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
    # Checked with waitlist mode off so the pricing page is rendered rather than
    # redirected; its own metadata still has to be unique and complete.
    test_context["settings"].public_waitlist_mode = False
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
    """The server-rendered chrome draws the menus from `core/site_content.py`.

    Read on `/about`, which is still a Jinja page. It used to be read on `/features` —
    which is now rendered by the React bundle, so the header and footer are not in the
    response at all and every assertion here found nothing. The page is chosen from the
    templates rather than named by hand for exactly that reason.
    """

    test_context["settings"].public_waitlist_mode = False
    jinja_path = next(
        item.path
        for item in PUBLIC_PAGES
        if not _react_rendered(item.template) and item.page not in WAITLIST_HIDDEN_PAGES
    )
    response = await test_context["client"].get(jinja_path)
    assert response.status_code == 200, jinja_path
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
    # Cookie settings is a link with a real address, not a button that goes nowhere.
    # As a `<button>` it was the one entry in this menu that could not be opened in a new
    # tab, could not be copied, and did nothing at all with scripting off.
    assert "data-cookie-settings" in content
    assert f'<a href="{COOKIE_SETTINGS_PATH}" data-cookie-settings>Cookie settings</a>' in content
    assert ">Cookie settings</button>" not in content

    # The three channels, from the same module, each announced by name.
    for channel in SOCIAL_LINKS:
        assert f'aria-label="Hilal Markets on {channel.label}"' in content
        assert channel.href in content


@pytest.mark.parametrize(
    "path",
    [path for path in ALL_PUBLIC_PATHS if path not in WAITLIST_REDIRECTED_PATHS],
)
async def test_no_public_page_links_into_the_product_in_waitlist_mode(test_context, path):
    """Every public page, one rule: pre-launch, no link may need an account to follow.

    The earlier version of this test read four pages and looked for three button labels.
    That is a test of the reported examples, not of the rule, and it passed while How We
    Screen carried a primary button to /dashboard/market and the Help Center carried one
    to authenticated Support. Reading every rendered anchor on every page catches the
    whole family, including a page added next month.
    """

    _hold_pre_launch(test_context)

    response = await test_context["client"].get(path)
    assert response.status_code == 200, path

    leaking = sorted(
        {link for link in _internal_links(response.text) if is_account_only_path(link)}
    )
    assert leaking == [], (
        f"{path} invites a visitor with no account to open {leaking}. "
        f"Account-only prefixes: {list(ACCOUNT_ONLY_PATH_PREFIXES)}"
    )


@pytest.mark.parametrize(
    "path",
    [
        item.path
        for item in PUBLIC_PAGES
        if item.page not in WAITLIST_HIDDEN_PAGES
        # The React shell renders its own body from the bundle; its waitlist section is
        # asserted separately against the runtime config it is given.
        and not _react_rendered(item.template)
    ],
)
async def test_every_server_rendered_page_closes_on_the_waitlist(test_context, path):
    """One closing section, on every page, with one wording.

    It is included by the base template rather than by each page, so this asserts the
    property that makes that worth doing: a page cannot ship without it.
    """

    _hold_pre_launch(test_context)
    response = await test_context["client"].get(path)
    assert response.status_code == 200, path
    content = html.unescape(response.text)

    assert 'id="waitlist-band"' in content, path
    assert "Hilal Markets is in a private beta." in content, path
    assert content.count('href="/#waitlist"') >= 1, path
    for forbidden in (">Sign in</a>", ">Start free</a>", ">Create a free account</a>"):
        assert forbidden not in content, (path, forbidden)
    # Menu entries for pages nobody can use are gone from the header and the footer.
    assert ">Pricing</a>" not in content, path
    assert ">Halal Assets</a>" not in content, path


async def test_how_we_screen_offers_the_waitlist_instead_of_the_dashboard(test_context):
    """The regression: a primary button on a public page pointed at /dashboard/market.

    The page had already been dropped from every menu. The link in the page body was
    never looked at, because nothing looked at page bodies.
    """

    _hold_pre_launch(test_context)
    response = await test_context["client"].get("/how-we-screen")
    content = html.unescape(response.text)

    assert "/dashboard/market" not in content
    assert ">Explore Halal Assets</a>" not in content
    assert '<a class="btn btn-primary" href="/#waitlist">Join the waitlist</a>' in content
    # The rest of the page is untouched: this is a link fix, not a content cut.
    assert "Trust &amp; Safety" in response.text
    assert "A reviewable chain, not an unexplained score." in content


async def test_help_center_routes_support_through_the_public_contact_form(test_context):
    """The second regression: the Help Center sent people to authenticated Support."""

    _hold_pre_launch(test_context)
    response = await test_context["client"].get("/help")
    content = html.unescape(response.text)

    assert ">Dashboard support</a>" not in content
    assert "authenticated Support" not in content
    assert ">Contact support</a>" in content
    assert "/contact" in [link for link in _internal_links(response.text)]
    assert "support runs through the public contact form" in content
    # The plan article answers the state the product is actually in.
    assert "Open Plan &amp; Billing in the dashboard" not in response.text
    assert "What does Hilal Markets cost?" in content
    assert "nothing is charged during the beta" in content


async def test_react_pages_are_given_the_waitlist_state_and_wording(test_context):
    """Contact, Privacy and Terms are rendered by the bundle, so the server hands it the
    same words the server-rendered pages use. One source of the message, two renderers."""

    _hold_pre_launch(test_context)
    for path in ("/contact", "/privacy", "/terms"):
        response = await test_context["client"].get(path)
        assert response.status_code == 200, path
        assert '"mode": true' in response.text, path
        assert "Hilal Markets is in a private beta." in response.text, path
        assert '"ctaLabel": "Join the waitlist"' in response.text, path
        assert '"href": "/#waitlist"' in response.text, path


async def test_waitlist_mode_removes_every_public_route_into_the_product(test_context):
    """The site-wide effects of the one setting: menus, sitemap, pricing address, prices."""

    _hold_pre_launch(test_context)

    # The plans and the comparison table are not reachable, and an old link lands on
    # the waitlist rather than on a page of prices.
    redirected = await test_context["client"].get("/pricing", follow_redirects=False)
    assert redirected.status_code == 303
    assert redirected.headers["location"] == "/#waitlist"

    # A redirect is not a page to index.
    sitemap = await test_context["client"].get("/sitemap.xml")
    assert "/pricing</loc>" not in sitemap.text

    # And the landing page carries no plan prices in its source at all.
    landing = await test_context["client"].get("/")
    assert '"plans": []' in landing.text
    assert '"comparisonRows": []' in landing.text
    assert "monthlyPrice" not in landing.text


async def test_launched_mode_restores_the_product_routes_the_waitlist_hides(test_context):
    """Turning the switch off has to give everything back, or it is not a switch.

    Every change made for the waitlist is conditional. This is the assertion that keeps
    it that way: a fix written as a deletion would pass every test above and fail here.
    """

    test_context["settings"].public_waitlist_mode = False

    pricing = await test_context["client"].get("/pricing")
    assert pricing.status_code == 200
    assert "Pricing" in pricing.text

    screening = html.unescape((await test_context["client"].get("/how-we-screen")).text)
    assert ">Explore Halal Assets</a>" in screening
    assert "/dashboard/market" in screening

    help_page = html.unescape((await test_context["client"].get("/help")).text)
    assert ">Dashboard support</a>" in help_page
    assert "authenticated Support" in help_page
    assert "Where do I manage my plan?" in help_page

    # The account entry comes back on the server-rendered pages. Read on a Jinja page,
    # not on `/features`: that page is drawn by the React bundle now, so its header is
    # not in the response and this assertion could only ever fail there.
    jinja_path = next(
        item.path
        for item in PUBLIC_PAGES
        if not _react_rendered(item.template) and item.page not in WAITLIST_HIDDEN_PAGES
    )
    server_rendered = html.unescape((await test_context["client"].get(jinja_path)).text)
    assert ">Sign in</a>" in server_rendered, jinja_path
    assert ">Start free</a>" in server_rendered, jinja_path
    # And the pre-launch closing section is not shown once there is nothing to wait for.
    assert 'id="waitlist-band"' not in server_rendered, jinja_path

    # The React pages are told the same thing through the runtime config they read.
    features = (await test_context["client"].get("/features")).text
    assert '"mode": false' in features

    landing = await test_context["client"].get("/")
    assert '"plans": []' not in landing.text
    assert '"mode": false' in landing.text


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
    # Every public page has to carry the same consent-aware tag configuration, and that
    # includes the pricing page, so it is checked with the pre-launch redirect off.
    settings.public_waitlist_mode = False
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
    test_context["settings"].public_waitlist_mode = False
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
    # The pricing page only exists once the product is open, so this is asserted with
    # waitlist mode off: it is the launch-day page that must match the dashboard.
    test_context["settings"].public_waitlist_mode = False
    catalog = await test_context["client"].get("/api/v1/billing/plans")
    assert catalog.status_code == 200
    catalog_plans = {plan["code"]: plan for plan in catalog.json()["plans"]}
    assert set(catalog_plans) == {"demo", "trader", "pro"}
    assert catalog_plans["trader"]["annual_price"] == "120.00"
    assert catalog_plans["pro"]["monthly_price"] == "22.00"
    assert catalog_plans["pro"]["annual_price"] == "220.00"

    pricing = await test_context["client"].get("/pricing")
    assert pricing.status_code == 200
    assert PLAN_DEFINITIONS["demo"].name in pricing.text
    assert "$0" in pricing.text
    # The launch offer. Both numbers come from `core.plans`, not from this file: a price
    # changed there must show up here, and the assertion still holds on the day the
    # offer ends, when there is no crossed-out price left to show.
    trader_offer = plan_offer_payload("trader")
    assert f"${int(trader_offer['monthlyPrice'])}" in pricing.text  # type: ignore[arg-type]
    original = trader_offer["originalMonthlyPrice"]
    if original:
        assert f"${int(original)}" in pricing.text  # type: ignore[arg-type]
        assert 'class="price-original"' in pricing.text
        assert "data-offer-countdown" in pricing.text
    # The Pro plan is not on sale yet, so it says "Soon" and carries no price at all.
    assert "$22" not in pricing.text
    assert "Pro is coming soon" in pricing.text
    assert "$29" not in pricing.text
    assert "Choose Monitor monthly" in pricing.text
    assert "7-day money-back guarantee" in pricing.text
    assert "Cancel within 7 days of payment for a full refund." in pricing.text
    assert "Choose Core" not in pricing.text
    assert "Choose Pro" not in pricing.text
    for internal_code in ("creator", "community", "lifetime", "pro_trial"):
        assert PLAN_DEFINITIONS[internal_code].name not in pricing.text

    await _signup(test_context, "catalog-parity@example.com")
    billing = await test_context["client"].get("/dashboard/billing")
    assert billing.status_code == 200
    assert PLAN_DEFINITIONS["demo"].name in billing.text
    assert "Your current plan" in billing.text
    assert "Paid billing is disabled" not in billing.text
    assert "What billing changes" not in billing.text
    assert "Screening evidence stays the same on every plan" not in billing.text
    assert 'data-billing-page-interval' in billing.text
    assert "Choose Monitor monthly" in billing.text
    assert "7-day money-back guarantee" in billing.text
    assert "5 active market monitors" in billing.text
    assert "No payment method needed" not in billing.text
    assert "10 active market monitors" in billing.text
    assert "Unlimited monitor alerts per day" in billing.text
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
    # `/dashboard` is the old front page's address and now sends a browser to Home, which
    # is the page the menu is read on.
    dashboard = await test_context["client"].get("/home")
    assert dashboard.status_code == 200
    expected = [
        item.label
        for group in DASHBOARD_NAVIGATION
        for item in group.items
    ]
    nav_match = re.search(
        r'<nav[^>]*data-testid="dashboard-nav".*?</nav>',
        dashboard.text,
        flags=re.DOTALL,
    )
    assert nav_match
    nav = html.unescape(nav_match.group(0))
    positions = [nav.index(f'class="hm-nav-text">{label}</span>') for label in expected]
    assert positions == sorted(positions)
    assert "Portfolio" not in nav
    assert "Referrals" not in nav
    assert "System Brain" not in nav

    # Trading Assistant is gone from the menu and its page is gone with it.
    market_check = await test_context["client"].get(
        "/dashboard/check-market",
        follow_redirects=False,
    )
    assert market_check.status_code == 404


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
