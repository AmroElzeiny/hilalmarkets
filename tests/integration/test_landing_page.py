from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LANDING_SOURCE = ROOT / "Hilal-Markets-Website" / "src" / "App.tsx"


async def test_landing_page_serves_supplied_react_design_without_legacy_shell(
    test_context,
):
    response = await test_context["client"].get("/")
    assert response.status_code == 200
    content = response.text
    assert "<title>Halal Trading With Clarity | Hilal Markets</title>" in content
    assert 'id="root"' in content
    assert "/static/landing/assets/landing.css" in content
    assert "/static/landing/assets/landing.js" in content
    assert "hilal/partials/public_header" not in content
    assert "landing-hero" not in content
    assert "WAITLIST_GOOGLE_SHEETS" not in content
    assert "script.google.com" not in content

    source = LANDING_SOURCE.read_text(encoding="utf-8")
    assert "A better way for Muslim crypto traders" in source
    assert 'analyticsName="waitlist"' in source
    assert "<Waitlist />" in source
    assert "<Pricing />" not in source
    assert "Join the waitlist" in source
    for forbidden in ("guaranteed profits", "guaranteed returns", "fake win rate"):
        assert forbidden not in source.casefold()


async def test_landing_page_does_not_lead_with_notification_channels(test_context):
    test_context["settings"].telegram_bot_username = "@simiautobybit_bot"
    response = await test_context["client"].get("/")
    assert response.status_code == 200
    assert "https://t.me/" not in response.text
    assert "Start on Telegram" not in response.text


async def test_landing_page_links_to_primary_start_paths(test_context):
    """The landing page has exactly one destination: the waitlist form on itself."""

    response = await test_context["client"].get("/")
    assert response.status_code == 200
    assert "Open dashboard preview" not in response.text
    source = LANDING_SOURCE.read_text(encoding="utf-8")
    chrome = (
        ROOT / "Hilal-Markets-Website" / "src" / "components" / "SiteChrome.tsx"
    ).read_text(encoding="utf-8")
    footer = (
        ROOT
        / "Hilal-Markets-Website"
        / "src"
        / "imports"
        / "10Footer-1"
        / "index.tsx"
    ).read_text(encoding="utf-8")
    assert 'href="#waitlist"' in source
    assert "/subscribe?" not in source
    assert "{ label: 'Pricing', target: '#pricing' }" not in chrome
    assert "Join the waitlist" in chrome
    assert "Get started" not in chrome
    assert "Sign in" not in chrome
    for href in ('href="/privacy"', 'href="/terms"', 'href="/contact"'):
        assert href in footer
    # "How We Screen" was withdrawn from the footer menu. Both the address and the
    # analytics name go, so a click cannot be recorded for a link nobody can see.
    for withdrawn in ('href="/how-we-screen"', "how_we_screen", "How We Screen"):
        assert withdrawn not in footer, withdrawn
    assert "TODO_" not in response.text


async def test_the_shipped_landing_bundle_matches_the_waitlist_source(test_context):
    """The built file is what visitors actually get; the source is only what we meant.

    A change to `App.tsx` that was never rebuilt leaves the old page live, so the shipped
    bundle is checked directly: it must offer the waitlist and must contain no route into
    the product and no plan price.
    """

    bundle = (
        ROOT / "src/ai_market_monitor/static/landing/assets/landing.js"
    ).read_text(encoding="utf-8")
    assert "Join the waitlist" in bundle
    assert "public-forms/bootstrap" in bundle
    # The withdrawn consent box is gone from what visitors are actually served, not only
    # from the source. A stale bundle would keep showing it and keep sending its answer.
    for withdrawn in ("beta_contact_consent", "waitlist-beta-consent", "beta testing"):
        assert withdrawn not in bundle, withdrawn
    # The Privacy and Terms pages carry the waitlist band, and their legal text scopes
    # accounts and payment to the private beta. Both are drawn by this file. Contact does
    # not carry the band: its own form is the one action on that page.
    assert "contact_footer" not in bundle
    assert "privacy_footer" in bundle
    assert "terms_footer" in bundle
    assert "Paid access is not offered to the public during the private beta." in bundle
    assert "Accounts are issued by invitation during the private beta." in bundle
    for forbidden in (
        "/signin",
        "/signup",
        "/subscribe?",
        "Choose Monitor monthly",
        "monthlyPrice",
        "7-day money-back guarantee",
        "unless a separately presented offer expressly states otherwise",
    ):
        assert forbidden not in bundle, forbidden


async def test_privacy_and_terms_use_the_react_landing_shell(test_context):
    for path, title in (("/privacy", "Privacy Policy"), ("/terms", "Terms of Use")):
        response = await test_context["client"].get(path)
        assert response.status_code == 200
        assert f"<title>{title} | Hilal Markets</title>" in response.text
        assert 'id="root"' in response.text
        assert "/static/landing/assets/landing.css" in response.text
        assert "/static/landing/assets/landing.js" in response.text
        assert '"legal"' in response.text
        assert "content-nav-layout" not in response.text


async def test_dashboard_entry_uses_saved_session_or_signup(test_context):
    anonymous = await test_context["client"].get("/dashboard-entry", follow_redirects=False)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/signup"

    requested = await test_context["client"].post(
        "/signup",
        data={
            "email": "entry@example.com",
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert requested.headers["location"].startswith("/signup/verify")
    code = test_context["settings"].email_test_outbox[-1]["code"]
    verified = await test_context["client"].post(
        "/signup/verify",
        data={"email": "entry@example.com", "code": code},
        follow_redirects=False,
    )
    assert verified.headers["location"].startswith("/dashboard")
    authenticated = await test_context["client"].get(
        "/dashboard-entry",
        follow_redirects=False,
    )
    assert authenticated.status_code == 303
    assert authenticated.headers["location"] == "/dashboard"


async def test_subscription_selection_is_validated_and_preserved_through_signup(
    test_context,
):
    # Plan selection belongs to the open product, so it is asserted with the pre-launch
    # switch off. The one thing that must still hold in waitlist mode is checked below.
    test_context["settings"].public_waitlist_mode = False
    selected = await test_context["client"].get(
        "/subscribe?plan_code=trader&billing_interval=monthly",
        follow_redirects=False,
    )
    assert selected.status_code == 303
    assert selected.headers["location"] == (
        "/signup?plan_code=trader&billing_interval=monthly"
    )

    # Annual billing is not open on any plan, so an annual link is not a selection the
    # server can honour. It goes back to the plans rather than carrying an interval
    # nobody can be charged for through sign-up.
    annual = await test_context["client"].get(
        "/subscribe?plan_code=trader&billing_interval=annual",
        follow_redirects=False,
    )
    assert annual.status_code == 303
    assert annual.headers["location"] == "/#pricing"

    invalid = await test_context["client"].get(
        "/subscribe?plan_code=internal&billing_interval=annual",
        follow_redirects=False,
    )
    assert invalid.status_code == 303
    assert invalid.headers["location"] == "/#pricing"

    requested = await test_context["client"].post(
        "/signup",
        data={
            "first_name": "Plan",
            "last_name": "Tester",
            "email": "pricing-selection@example.com",
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
            "plan_code": "trader",
            "billing_interval": "monthly",
        },
        follow_redirects=False,
    )
    assert "plan_code=trader" in requested.headers["location"]
    assert "billing_interval=monthly" in requested.headers["location"]
    code = test_context["settings"].email_test_outbox[-1]["code"]
    verified = await test_context["client"].post(
        "/signup/verify",
        data={
            "email": "pricing-selection@example.com",
            "code": code,
            "plan_code": "trader",
            "billing_interval": "monthly",
        },
        follow_redirects=False,
    )
    assert verified.headers["location"] == (
        "/dashboard/billing?selected_plan=trader&billing_interval=monthly"
        "&checkout=1&error=billing_disabled"
    )


async def test_an_unknown_plan_link_lands_on_the_waitlist_not_a_missing_anchor(
    test_context,
):
    """There is no pricing section to scroll to while the site is pre-launch."""

    response = await test_context["client"].get(
        "/subscribe?plan_code=internal&billing_interval=annual",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/#waitlist"


async def test_dashboard_preview_pages_are_available(test_context):
    response = await test_context["client"].get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/signin")
