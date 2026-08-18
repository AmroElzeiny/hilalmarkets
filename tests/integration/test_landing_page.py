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
    # The product has launched: the plans stand where the waitlist form stood.
    assert 'analyticsName="pricing"' in source
    assert "<Pricing />" in source
    assert "<Waitlist />" not in source
    assert "Join the waitlist" not in source
    for forbidden in ("guaranteed profits", "guaranteed returns", "fake win rate"):
        assert forbidden not in source.casefold()


async def test_landing_page_does_not_lead_with_notification_channels(test_context):
    test_context["settings"].telegram_bot_username = "@simiautobybit_bot"
    response = await test_context["client"].get("/")
    assert response.status_code == 200
    assert "https://t.me/" not in response.text
    assert "Start on Telegram" not in response.text


async def test_landing_page_links_to_primary_start_paths(test_context):
    """The landing page has one destination, and the server owns its address.

    Before launch that destination was the waitlist form on the page itself. It is the
    product now, reached through `/dashboard-entry` — one address that sends a signed-in
    person to their dashboard and everybody else to sign-up, so nothing on the page has
    to guess which of the two the visitor is.
    """

    response = await test_context["client"].get("/")
    assert response.status_code == 200
    assert "Open dashboard preview" not in response.text
    source = LANDING_SOURCE.read_text(encoding="utf-8")
    chrome = (
        ROOT / "Hilal-Markets-Website" / "src" / "components" / "SiteChrome.tsx"
    ).read_text(encoding="utf-8")
    assert 'href="#waitlist"' not in source
    assert "Join the waitlist" not in chrome
    # One owner for the entry address, and the page uses it rather than writing its own.
    assert "export const DASHBOARD_ENTRY = '/dashboard-entry'" in chrome
    assert "dashboardEntryHref()" in source
    assert 'href="/dashboard"' not in source
    assert 'href="/signup"' not in source
    # The footer menu now comes from the server, so the React file holds no copy of it.
    assert "FALLBACK_FOOTER_GROUPS" in chrome
    for group in ("Product", "Legal", "Contact"):
        assert f"label: '{group}'" in chrome, group
    # "How We Screen" was withdrawn from the footer menu. Both the address and the
    # analytics name go, so a click cannot be recorded for a link nobody can see.
    for withdrawn in ('href="/how-we-screen"', "how_we_screen", "How We Screen"):
        assert withdrawn not in chrome, withdrawn
    assert "TODO_" not in response.text


async def test_the_shipped_landing_bundle_matches_the_launched_source(test_context):
    """The built file is what visitors actually get; the source is only what we meant.

    A change to `App.tsx` that was never rebuilt leaves the old page live. The copy step
    into `static/landing/assets/` is done by hand, so this is the only thing standing
    between an edited source and a stale page: the shipped bundle must carry the plans
    and must not carry the waitlist.
    """

    bundle = (
        ROOT / "src/ai_market_monitor/static/landing/assets/landing.js"
    ).read_text(encoding="utf-8")
    assert "Join the waitlist" not in bundle
    assert "/dashboard-entry" in bundle
    # The plans really shipped, not just the component that can draw them.
    assert "Choose how deeply you want to monitor the market." in bundle
    assert "public-forms/bootstrap" in bundle
    # The withdrawn consent box is gone from what visitors are actually served, not only
    # from the source. A stale bundle would keep showing it and keep sending its answer.
    for withdrawn in ("beta_contact_consent", "waitlist-beta-consent", "beta testing"):
        assert withdrawn not in bundle, withdrawn
    # No page closes on a waitlist band any more. Privacy and Terms end on the sibling
    # documents instead, and Contact ends on its own form, which is the one action
    # that page is for.
    for withdrawn in ("contact_footer", "privacy_footer", "terms_footer"):
        assert withdrawn not in bundle, withdrawn
    # The two legal documents describe the live service. Checked in the built file
    # rather than the source, because a rewrite that was never rebuilt leaves the
    # private-beta wording live on the site whatever the source now says.
    for beta in (
        "private beta",
        "private-beta",
        "invite-only",
        "Accounts are issued by invitation",
        "Paid access is not offered to the public",
    ):
        assert beta not in bundle, beta
    # And the promises those sentences used to carry are in the bundle in live form.
    assert "the checkout shows the price" in bundle
    assert "no charge may be taken" in bundle
    # The three rebuilt pages actually shipped, not only their source.
    assert "Answers, before you write" in bundle
    assert "Read the full wording" in bundle
    assert "In short" in bundle
    # The way into the product, and the plans, really shipped. Each of these was on the
    # forbidden list while the site was pre-launch; the list is inverted rather than
    # deleted, so a bundle that quietly loses the pricing section fails here.
    for required in (
        "/signin",
        "/dashboard-entry",
        "/subscribe?",
        "Choose Monitor monthly",
        "monthlyPrice",
        "7-day money-back guarantee",
    ):
        assert required in bundle, required
    for forbidden in (
        "#waitlist",
        "Join the waitlist",
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


async def test_an_unknown_plan_link_lands_somewhere_that_exists(test_context):
    """An unusable plan link goes to whatever the current stage actually shows.

    Launched, that is the pricing section. Pre-launch there is no pricing section to
    scroll to, so it is the waitlist. Both are asserted because the point of the rule is
    that the address always exists — sending somebody to `#pricing` on a page with no
    plans on it is the missing anchor this test is named after.
    """

    settings = test_context["settings"]
    for waitlist_mode, expected in ((False, "/#pricing"), (True, "/#waitlist")):
        settings.public_waitlist_mode = waitlist_mode
        response = await test_context["client"].get(
            "/subscribe?plan_code=internal&billing_interval=annual",
            follow_redirects=False,
        )
        assert response.status_code == 303, waitlist_mode
        assert response.headers["location"] == expected, waitlist_mode


async def test_dashboard_preview_pages_are_available(test_context):
    response = await test_context["client"].get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/signin")
