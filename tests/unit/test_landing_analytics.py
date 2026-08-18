import re
from datetime import timedelta
from pathlib import Path

from ai_market_monitor.core.plans import (
    PLAN_DEFINITIONS,
    PROMOTION_ENDS_AT,
    PUBLIC_PLAN_PRESENTATIONS,
    plan_offer_payload,
)

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "Hilal-Markets-Website" / "src"
ANALYTICS = FRONTEND / "analytics.ts"
TRACKING = FRONTEND / "components" / "Tracking.tsx"
APP = FRONTEND / "App.tsx"
CONTACT = FRONTEND / "pages" / "ContactPage.tsx"
LEGAL = FRONTEND / "pages" / "LegalPage.tsx"
STYLES = FRONTEND / "index.css"
SHELL = ROOT / "src/ai_market_monitor/templates/hilal/public/react_site.html"
FAVICON = ROOT / "favicon-dark.png"
SOCIAL_PREVIEW = ROOT / "src/ai_market_monitor/static/hilalmarkets-social-preview.png"
LEGAL_DOCUMENTS = FRONTEND / "legal" / "documents.tsx"


def _without_comments(path: Path) -> str:
    """One file's text, with the notes to other developers taken out.

    Several checks below ask what a *visitor* can read. A comment explaining why
    something was removed contains the very words it was removed for, so reading the
    raw file makes the explanation itself the failure — and the only way to pass would
    be to delete the explanation.
    """

    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


def test_landing_uses_one_provider_agnostic_consent_aware_analytics_module():
    analytics = ANALYTICS.read_text(encoding="utf-8")
    for function_name in (
        "trackPageView",
        "trackSectionView",
        "trackFaqOpen",
        "trackCtaClick",
        "trackWaitlistFormView",
        "trackWaitlistFormStart",
        "trackWaitlistSubmitAttempt",
        "trackWaitlistSuccess",
        "trackWaitlistError",
        "trackPricingSectionView",
        "trackBillingIntervalChanged",
        "trackPlanSelected",
        "trackCheckoutStarted",
        "trackCheckoutCompleted",
        "trackCheckoutCancelled",
        "trackCheckoutFailed",
    ):
        assert f"function {function_name}" in analytics
    assert "!consent.analytics" in analytics
    assert "!consent.marketing" in analytics
    assert "__hmAnalyticsInitialized" in analytics
    assert "googletagmanager.com/gtm.js?id=" in analytics
    assert "googletagmanager.com/gtag/js" not in analytics
    assert "ga4MeasurementId" not in analytics
    assert "emitGoogle('generate_lead'" not in analytics
    assert "FORBIDDEN_PARAMETER_KEYS" in analytics
    assert "function loadX()" in analytics
    assert "'https://static.ads-twitter.com/uwt.js'" in analytics
    assert "twq('config', pixelId)" in analytics
    assert "__hmXConfiguredPixels" in analytics

    for source_path in FRONTEND.rglob("*.tsx"):
        source = source_path.read_text(encoding="utf-8")
        assert "window.gtag" not in source, source_path
        assert "dataLayer.push" not in source, source_path
        assert "window.fbq" not in source, source_path


def test_every_page_shell_uses_the_root_dark_favicon():
    expected = FAVICON.read_bytes()
    assert (ROOT / "src/ai_market_monitor/static/favicon-dark.png").read_bytes() == expected
    assert (FRONTEND.parent / "public/favicon-dark.png").read_bytes() == expected

    template_paths = (
        "src/ai_market_monitor/templates/auth.html",
        "src/ai_market_monitor/templates/dashboard_public.html",
        "src/ai_market_monitor/templates/hilal/base_dashboard.html",
        "src/ai_market_monitor/templates/hilal/base_public.html",
        "src/ai_market_monitor/templates/hilal/public/react_site.html",
        "src/ai_market_monitor/templates/system_brain.html",
        "src/ai_market_monitor/templates/system_brain_auth.html",
    )
    for relative_path in template_paths:
        shell = (ROOT / relative_path).read_text(encoding="utf-8")
        assert 'rel="icon"' in shell
        assert "favicon-dark.png" in shell
        assert "hilalmarkets-logo-mark.svg" not in next(
            line for line in shell.splitlines() if 'rel="icon"' in line
        )

    site_config = (FRONTEND.parent / ".figma/make/site.json").read_text(
        encoding="utf-8"
    )
    assert '"icon": "/favicon-dark.png"' in site_config


def test_public_shells_have_complete_social_preview_and_seo_metadata():
    expected_title = "Halal Trading With Clarity"
    expected_description = (
        "Screen halal assets, build your rules, and monitor setups without watching "
        "charts all day."
    )
    for relative_path in (
        "src/ai_market_monitor/templates/hilal/base_public.html",
        "src/ai_market_monitor/templates/hilal/public/react_site.html",
    ):
        shell = (ROOT / relative_path).read_text(encoding="utf-8")
        for marker in (
            'name="robots"',
            'rel="canonical"',
            'property="og:site_name"',
            'property="og:title"',
            'property="og:description"',
            'property="og:image"',
            'property="og:image:secure_url"',
            'property="og:image:type"',
            'property="og:image:width"',
            'property="og:image:height"',
            'property="og:image:alt"',
            'name="twitter:card"',
            'name="twitter:title"',
            'name="twitter:description"',
            'name="twitter:image"',
            'name="twitter:image:alt"',
        ):
            assert marker in shell, (relative_path, marker)

    site_config = (ROOT / "Hilal-Markets-Website/.figma/make/site.json").read_text(
        encoding="utf-8"
    )
    assert expected_title in site_config
    assert expected_description in site_config
    assert '"image": "/hilalmarkets-social-preview.png"' in site_config


def test_social_preview_is_a_real_1200_by_630_png_in_both_public_asset_roots():
    browser_asset = ROOT / "Hilal-Markets-Website/public/hilalmarkets-social-preview.png"
    landing_asset = (
        ROOT / "src/ai_market_monitor/static/landing/hilalmarkets-social-preview.png"
    )
    expected = SOCIAL_PREVIEW.read_bytes()
    assert browser_asset.read_bytes() == expected
    assert landing_asset.read_bytes() == expected
    assert expected[:8] == b"\x89PNG\r\n\x1a\n"
    assert int.from_bytes(expected[16:20], "big") == 1200
    assert int.from_bytes(expected[20:24], "big") == 630


def test_non_dashboard_image_elements_have_descriptive_alt_text():
    files = [
        ROOT / "src/ai_market_monitor/templates/auth.html",
        ROOT / "src/ai_market_monitor/templates/dashboard_public.html",
        ROOT / "src/ai_market_monitor/templates/hilal/partials/public_header.html",
        ROOT / "src/ai_market_monitor/templates/hilal/partials/public_footer.html",
        *(ROOT / "src/ai_market_monitor/templates/hilal/public").glob("*.html"),
    ]
    for path in files:
        source = path.read_text(encoding="utf-8")
        for image in re.findall(r"<img\b[^>]*>", source, flags=re.IGNORECASE):
            alt = re.search(r'\balt="([^"]+)"', image, flags=re.IGNORECASE)
            assert alt and alt.group(1).strip(), (path, image)


def test_section_visibility_supports_long_entry_and_configurable_percentage_modes():
    tracking = TRACKING.read_text(encoding="utf-8")
    pricing = (FRONTEND / "components" / "Pricing.tsx").read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    assert "IntersectionObserver" in tracking
    assert "visibilityMode?: VisibilityMode" in tracking
    assert "visibilityMode: options.visibilityMode ?? 'entry'" in tracking
    assert "visibilityMode === 'entry'" in tracking
    # "Seen" is measured against the element or the window, whichever is smaller.
    # `intersectionRatio` alone is the share of the element, which a section taller than
    # the window can never raise above a third - so it was never counted as seen.
    assert "visibleShare(entry) >= threshold" in tracking
    assert "Math.min(elementHeight, windowHeight)" in tracking
    assert "entry.intersectionRect.height / reference" in tracking
    assert "entry.intersectionRatio" not in tracking
    # And the browser is asked for enough steps to notice while a long section scrolls.
    assert "PERCENTAGE_OBSERVER_STEPS = 20" in tracking
    assert "threshold: [0, threshold, 1]" not in tracking
    assert "window.setTimeout(attempt, dwellMs)" in tracking
    assert "rootMargin: '0px 0px -20% 0px'" in tracking
    assert "observer.disconnect()" in tracking
    assert "window.clearTimeout(timer)" in tracking
    assert "visibilityMode: 'entry'" in pricing
    assert "dwellMs: 1000" in pricing
    # Percentage mode is what a form or a long panel needs: seen means half of it was on
    # screen for a second, not that its top edge went past. Asserted on the module that
    # provides it rather than on whichever page happens to use it today — pinning it to
    # `App.tsx` made this test fail when the waitlist form was removed, which says
    # nothing at all about whether the two modes still work.
    assert "'entry' | 'percentage'" in tracking
    assert "DEFAULT_PERCENTAGE_THRESHOLD = 0.5" in tracking
    assert "DEFAULT_DWELL_MS = 1000" in tracking
    assert "threshold?: number" in tracking
    assert app


def test_landing_tracks_each_feature_row_and_stable_faq_id_without_text_payloads():
    app = APP.read_text(encoding="utf-8")
    analytics = ANALYTICS.read_text(encoding="utf-8")
    feature_source = (
        FRONTEND / "imports" / "06CoreFeatures-1" / "index.tsx"
    ).read_text(encoding="utf-8")
    expected_sections = {
        "hero",
        "problem_solution",
        "how_it_works",
        "feature_screen",
        "feature_build",
        "feature_monitor",
        "feature_connect",
        "trust_control",
        "ecosystem",
        "pricing",
        "faq",
    }
    for section in expected_sections - {
        "feature_screen",
        "feature_build",
        "feature_monitor",
        "feature_connect",
    }:
        assert f'analyticsName="{section}"' in app
    for section in {
        "feature_screen",
        "feature_build",
        "feature_monitor",
        "feature_connect",
    }:
        assert f"useSectionTracking<HTMLDivElement>('{section}')" in feature_source
        assert f'data-analytics-section="{section}"' in feature_source
    # The features block is tracked row by row inside the imported section, so there is
    # no single "features" *section* wrapper. Matched as the section tag rather than as
    # the bare name: a link to /features is also called `features`, and it is a call to
    # action, not a section.
    assert '<TrackedSection analyticsName="features"' not in app

    faq_ids = {
        "what_is_hilal",
        "target_audience",
        "is_broker",
        "provides_signals",
        "supported_markets",
        "shariah_screening",
        "halal_guarantee",
        "how_to_build",
        "halal_listings",
        "ai_rule_changes",
        "alert_delivery",
        "strategy_privacy",
    }
    for faq_id in faq_ids:
        assert f"id: '{faq_id}'" in app
    assert "if (!isOpen) trackFaqOpen(f.id)" in app
    assert "emitGoogle('faq_open'" in analytics
    assert "faq_id: normalized" in analytics
    assert (
        "emitOnce(`faq:${googlePageViewSequence}:${pagePath()}:${normalized}`"
        in analytics
    )
    assert "trackFaqOpen," in analytics


def test_the_plans_are_on_the_page_and_track_only_validated_commerce_metadata():
    """The product has launched, so the plans stand where the waitlist form stood.

    `Pricing.tsx` was kept through the pre-launch period precisely so this would be one
    import rather than a rebuild from memory, and a rebuilt price is a price that can
    disagree with the server's. This asserts the section is mounted and that it reports
    nothing it has not validated.
    """

    app = APP.read_text(encoding="utf-8")
    analytics = ANALYTICS.read_text(encoding="utf-8")
    pricing = (FRONTEND / "components" / "Pricing.tsx").read_text(encoding="utf-8")
    assert "<Pricing />" in app
    assert "components/Pricing" in app
    # The form and its panel are gone from the page, not merely unreferenced.
    assert "<Waitlist />" not in app
    assert 'id="waitlist"' not in app
    assert "trackPricingSectionView" in pricing
    assert "trackBillingIntervalChanged" in pricing
    assert "trackPlanSelected" in pricing
    assert "validPlanCode" in analytics
    assert "validBillingInterval" in analytics
    assert "dataLayer" not in pricing
    assert "email_address" not in pricing


def test_the_landing_page_leads_into_the_product_and_never_to_a_waitlist():
    """Every way in goes through the one entry the server owns.

    The mirror of the rule this file held before launch. While the product was
    invite-only nothing was allowed to reach sign-in, sign-up or the dashboard; now
    nothing may offer a waitlist, and every call to action has to use
    `/dashboard-entry` rather than guessing whether the visitor already has an account.
    That guess is the reason this is one address: `/signup` shown to somebody already
    signed in is a dead end, and `/dashboard` shown to a stranger is a redirect to a
    login they did not ask for.

    Checked across every rendered source file rather than the few obvious ones — the
    hero illustration once carried its own button straight to checkout, and a test
    reading only `App.tsx` and `SiteChrome.tsx` passed with it still there.
    """

    app = APP.read_text(encoding="utf-8")
    chrome = (FRONTEND / "components" / "SiteChrome.tsx").read_text(encoding="utf-8")
    rendered = [
        path for path in (*FRONTEND.rglob("*.tsx"), *FRONTEND.rglob("*.ts"))
    ]
    assert len(rendered) > 10
    for path in rendered:
        # Comments describe history and are not what a visitor clicks, so only the
        # rendered strings are searched.
        rendered_source = _without_comments(path)
        for forbidden in (
            'href="#waitlist"',
            "href={waitlistHref}",
            "Join the waitlist",
        ):
            assert forbidden not in rendered_source, (path.name, forbidden)

    # One owner for the entry address: the helper, used everywhere.
    assert "export const DASHBOARD_ENTRY = '/dashboard-entry'" in chrome
    assert "export function dashboardEntryHref()" in chrome
    for source, name in ((app, "App.tsx"), (chrome, "SiteChrome.tsx")):
        assert "dashboardEntryHref()" in source, name
        # Never the raw address beside the helper; that is the second copy that drifts.
        assert 'href="/dashboard"' not in source, name
        assert 'href="/signup"' not in source, name


def test_the_reading_pages_close_on_the_other_documents_rather_than_a_signup():
    """Privacy and Terms end on the paperwork that goes with them.

    They used to end on a waitlist band. Asking somebody to sign up for something else,
    in the middle of reading what they are agreeing to, is the wrong request in the
    wrong place — and it was the last piece of private-beta wording left on either
    page. Where it stood there are now links to the three documents a reader of this
    one actually wants next.
    """

    legal = LEGAL.read_text(encoding="utf-8")

    # The band is gone from the page, and from the project: nothing imports it.
    assert "WaitlistBand" not in legal
    assert not (FRONTEND / "components" / "WaitlistBand.tsx").exists()
    for name in ("privacy_footer", "terms_footer"):
        assert name not in legal, name

    # What replaced it: every sibling document, and a way to ask a question.
    for href in ("/privacy", "/terms", "/cookies", "/risk-disclosure", "/contact"):
        assert f"href: '{href}'" in legal or f'href="{href}"' in legal, href
    # A page never links to itself in that list.
    assert "item.href !== window.location.pathname" in legal


def test_the_contact_page_leads_with_answers_and_never_a_second_request():
    """One page, one job — and the fastest route to an answer comes first.

    The private-beta band and the diagram of our own inbox handling are both still
    gone. What is new is that the page tries to answer the question before asking
    somebody to write it out: most messages are a question that already has an answer,
    and answering it here is faster than any reply we could send.
    """

    # A note in the code explaining what was taken away is not something a visitor sees,
    # so the check reads the page without its comments.
    contact = re.sub(
        r"/\*.*?\*/", "", CONTACT.read_text(encoding="utf-8"), flags=re.DOTALL
    )

    for removed in (
        "WaitlistBand",
        "contact_footer",
        "MessageRouteGraph",
        "A clear route to the team",
        "Secure delivery",
        "Human review",
    ):
        assert removed not in contact, removed

    # What the page is for is still there, whole.
    assert "data-contact-form" in contact
    assert "office@hilalmarkets.com" in contact
    assert "<SiteFooter />" in contact

    # And the parts that make it answer rather than only collect.
    assert "const ANSWERS" in contact
    assert "Answers, before you write" in contact
    assert "const TOPICS" in contact


def test_legal_pages_describe_the_live_service_rather_than_a_private_beta():
    """The documents describe how the service runs, not which door is open today.

    Every sentence that scoped the agreement to a closed beta is gone. That is not a
    loosening: the protections those sentences carried — the price shown before any
    charge, the account obligations — are asserted here in their live form, so removing
    the beta framing cannot quietly remove a promise with it.

    How open the product is stays one server setting, `LAUNCH_STAGE`, which the header,
    the footer and the assistant all read. The legal text says nothing about it, so a
    stage change can never leave these two documents describing the wrong product.
    """

    # A note in the code recording what was removed, and why, is not something a
    # visitor reads. The check is about the page, so it reads the page without its
    # comments — otherwise the only way to keep this test green would be to delete the
    # explanation of the change.
    legal = _without_comments(FRONTEND / "legal" / "documents.tsx")
    page = _without_comments(LEGAL)

    for beta in (
        "private beta",
        "private-beta",
        "invite-only",
        "by invitation",
        "beta testing",
    ):
        assert beta.lower() not in legal.lower(), beta
        assert beta.lower() not in page.lower(), beta

    # The payment protection survives the rewrite, in live form.
    assert "the checkout shows the price" in legal
    assert "no charge may be taken" in legal
    assert "Cancelling stops the next payment" in legal
    # And so do the account obligations.
    assert "must be true and kept up to date" in legal
    assert "Your password is yours alone" in legal
    # The product boundaries are the same boundaries, said plainly.
    assert "never connects to your exchange trading keys" in legal
    assert "It does not place, cancel or manage any order." in legal


def test_every_legal_section_opens_with_a_plain_language_summary():
    """A beginner must be able to read this page and stop.

    The audience for this product is beginners. The old text was ninety-word sentences
    of legal register with no way in, which for that audience is the same as no policy
    at all. Every section now carries `short` — one or two plain sentences — and the
    page shows it first, above the clause.
    """

    from ai_market_monitor.core.copy_rules import scan_text  # noqa: PLC0415

    source = LEGAL_DOCUMENTS.read_text(encoding="utf-8")
    page = LEGAL.read_text(encoding="utf-8")

    # Every section declares one, and there are as many as there are sections. A short
    # summary may sit on the same line as its key or on the next one, depending on its
    # length, so both shapes count.
    sections = re.findall(r"^        id: '[a-z-]+',$", source, re.M)
    summaries = re.findall(r"^        short:(?:$| ')", source, re.M)
    assert len(sections) >= 30, len(sections)
    assert len(summaries) == len(sections), (len(summaries), len(sections))

    # And the page puts it above the clause rather than below it.
    assert page.index("In short") < page.index("Read the full wording")
    # The summary is never presented as the agreement itself.
    assert "The full wording under it is the part that" in page

    # The documents are customer copy, so they obey the same word rules as a template.
    assert scan_text(source, LEGAL_DOCUMENTS) == ()


def test_the_waitlist_form_asks_for_an_email_address_and_nothing_else():
    """No consent box, on the page or on the wire.

    The form briefly offered a pre-ticked "contact me about beta testing" box. A box that
    is already ticked records an answer the person never gave, so the question was
    withdrawn. This checks the whole path, not just the missing input: a field still sent
    by the browser, still accepted by the server, or still written to the sheet would keep
    storing that invented answer with nothing on screen to show for it.
    """

    app = APP.read_text(encoding="utf-8")
    public_forms = (FRONTEND / "publicForms.ts").read_text(encoding="utf-8")
    schema = (
        ROOT / "src/ai_market_monitor/schemas/public_forms.py"
    ).read_text(encoding="utf-8")
    service = (
        ROOT / "src/ai_market_monitor/services/public_forms.py"
    ).read_text(encoding="utf-8")
    model = (
        ROOT / "src/ai_market_monitor/db/models/public_forms.py"
    ).read_text(encoding="utf-8")
    apps_script = (
        ROOT / "scripts/google_apps_script/waitlist_webhook.gs"
    ).read_text(encoding="utf-8")

    # The form is off the landing page now that the product has launched. The consent
    # field must stay gone from every layer whether a form is on screen or not: the
    # column is what recorded an answer nobody gave, and it would start recording again
    # the moment the site is pulled back to the waitlist.
    assert 'id="waitlist-email"' not in app
    for source, name in (
        (app, "App.tsx"),
        (public_forms, "publicForms.ts"),
        (schema, "schemas/public_forms.py"),
        (service, "services/public_forms.py"),
        (model, "db/models/public_forms.py"),
    ):
        for forbidden in ("beta_contact_consent", "betaContactConsent", "betaConsent"):
            assert forbidden not in source, (name, forbidden)
    assert 'type="checkbox"' not in app
    # The sheet keeps a name for the withdrawn column so an existing sheet can be brought
    # back to the current layout, but no new row is ever given a consent value.
    assert "'Beta Testing Consent'" in apps_script
    assert "WAITLIST_HEADERS_WITH_CONSENT" in apps_script
    assert "consentLabel_" not in apps_script

    # The focus indicator survives on any ground the site paints. Near-black would
    # vanish on the dark footer, so that one inverts — same indicator, still readable.
    styles = STYLES.read_text(encoding="utf-8")
    assert "outline: var(--hm-focus-ring)" in styles
    assert ".hm-footer a:focus-visible" in styles
    assert "outline: 3px solid var(--color-surface) !important" in styles
    assert "min-height: 44px" in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles


def test_pricing_uses_approved_plans_accessibility_and_real_handoff():
    """The plans quote what the server charges, and never a number typed out here."""

    app = APP.read_text(encoding="utf-8")
    pricing = (FRONTEND / "components" / "Pricing.tsx").read_text(encoding="utf-8")
    chrome = (FRONTEND / "components" / "SiteChrome.tsx").read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    # The one thing a visitor can act on comes before the questions, wherever that
    # section happens to be.
    assert app.index('analyticsName="pricing"') < app.index('analyticsName="faq"')
    # The phone menu is a named landmark, and the control that opens it says which it
    # does. It used to render the word "Menu" in a pill; it is the standard mark now, so
    # the accessible name is the only thing carrying the message and has to be there.
    assert 'aria-label="Site"' in chrome
    assert "aria-label={menuOpen ? 'Close menu' : 'Open menu'}" in chrome
    assert 'aria-controls="site-mobile-menu"' in chrome
    for content in (
        "Choose how deeply you want to monitor the market.",
        "Basic",
        "Monitor",
        "Pro",
        # Annual is not open on any plan, and Pro is not open at all.
        "annualAvailable: false",
        "monthlyAvailable: false",
        "Choose Monitor monthly",
        "7-day money-back guarantee",
        "Cancel within 7 days of payment for a full refund.",
        "5 active market monitors",
        "2 monitor notifications per week across all monitors",
        "1 quick scan per week",
        "10 active market monitors",
        "Up to 50 monitor alerts per day",
        "Unlimited monitor alerts per day",
        "WhatsApp delivery - coming soon",
    ):
        assert content in pricing

    # The hardcoded list in the component is only a fallback, used when the page is
    # opened with no runtime config. It must still say exactly what the server would
    # have sent. Every number is derived from `core.plans` rather than typed out here,
    # so a price changed in one place fails this test instead of quietly leaving the
    # landing page quoting the old one.
    inside_promotion = PROMOTION_ENDS_AT - timedelta(days=1)
    trader = plan_offer_payload("trader", now=inside_promotion)
    for expected in (
        f"monthlyPrice: {int(trader['monthlyPrice'])}",  # type: ignore[arg-type]
        f"originalMonthlyPrice: {int(trader['originalMonthlyPrice'])}",  # type: ignore[arg-type]
        f"annualPrice: {int(PUBLIC_PLAN_PRESENTATIONS['trader'].annual_price)}",
        # Pro shows no price on the page; the fallback still has to carry the real one
        # so the card can quote it the day Pro opens.
        f"monthlyPrice: {int(PLAN_DEFINITIONS['pro'].monthly_price)}",
        f"annualPrice: {int(PUBLIC_PLAN_PRESENTATIONS['pro'].annual_price)}",
    ):
        assert expected in pricing
    for removed in (
        "first Watchlist",
        "markets per Watchlist",
        "90-day opportunity",
        "1-year opportunity",
        "Advanced Controls",
        "Visual Canvas",
        "Paid subscriptions are not available yet.",
        # No plan is ranked for the buyer. The badge said one was.
        "Most Popular",
    ):
        assert removed not in pricing
    assert "Discord" not in pricing
    assert 'role="status"' in pricing
    assert 'type="radio"' in pricing
    assert "aria-expanded={expanded}" in pricing
    assert 'aria-controls={`plan-${plan.code}-features`}' in pricing
    assert "/subscribe?plan_code=" in pricing
    assert "@media (max-width: 767px)" in styles
    assert ".comparison-mobile" in styles
    assert ".plan-cta:focus-visible" in styles


def test_checkout_outcome_tracking_is_consent_aware_and_contains_no_payment_data():
    script = (
        ROOT / "src/ai_market_monitor/static/hilalmarkets-commerce-analytics.js"
    ).read_text(encoding="utf-8")
    result_template = (
        ROOT / "src/ai_market_monitor/templates/billing_result.html"
    ).read_text(encoding="utf-8")
    checkout_template = (
        ROOT / "src/ai_market_monitor/templates/hilal/dashboard/checkout.html"
    ).read_text(encoding="utf-8")

    assert 'dataset.consentAnalytics !== "granted"' in script
    assert "window.sessionStorage" in script
    assert "checkout_started" in checkout_template
    assert "checkout_completed" in result_template
    assert "checkout_cancelled" in result_template
    assert "checkout_failed" in result_template
    for private_key in ("email", "card", "wallet", "user_id"):
        assert private_key not in script


def test_sheet_configuration_and_form_content_are_not_exposed_in_public_assets():
    shell = SHELL.read_text(encoding="utf-8")
    built_js = (
        ROOT / "src/ai_market_monitor/static/landing/assets/landing.js"
    ).read_text(encoding="utf-8")
    for prohibited in (
        "WAITLIST_GOOGLE_SHEETS_WEBHOOK_URL",
        "WAITLIST_GOOGLE_SHEETS_WEBHOOK_SECRET",
        "script.google.com/macros/s/",
    ):
        assert prohibited not in shell
        assert prohibited not in built_js
    assert "contact_form_recipient_email" not in shell
    # The office address is public contact information. Recipient configuration
    # and private Google Sheets credentials must remain server-only.
    assert "contact_form_recipient_email" not in built_js


def test_contact_page_has_branded_accessible_success_and_safe_failure_states():
    """A result the visitor can see, and a failure that says which failure it was.

    The page used to answer every failure with one sentence — "We could not send your
    message" — whether the connection had dropped, the server had broken, or the
    person had reached their message limit. Those need three different next steps, so
    they now get three different messages, and the one about the limit uses the
    server's own wording because only the server knows when it clears.
    """

    contact = CONTACT.read_text(encoding="utf-8")
    assert 'role="status"' in contact
    assert 'role="alert"' in contact
    assert "Your message was sent." in contact
    assert 'href="mailto:office@hilalmarkets.com"' in contact
    assert "traceback" not in contact.casefold()
    assert "stack trace" not in contact.casefold()

    # A result nobody can see is a result nobody got: focus moves to it, and it is
    # announced.
    assert "resultRef.current" in contact
    assert "node?.focus()" in contact

    # Three failures, three messages, three next steps.
    assert "Your message did not leave this device" in contact
    assert "We could not send your message" in contact
    assert "You have reached the message limit" in contact
    # The limit message defers to the server, which knows when it clears.
    assert "error.detail" in contact


def test_landing_brand_overrides_and_centered_figma_sections_are_stable():
    app = APP.read_text(encoding="utf-8")
    chrome = (FRONTEND / "components" / "SiteChrome.tsx").read_text(encoding="utf-8")
    footer = (FRONTEND / "imports" / "10Footer-1" / "index.tsx").read_text(
        encoding="utf-8"
    )
    legacy_css = (ROOT / "src/ai_market_monitor/static/hilalmarkets.css").read_text(
        encoding="utf-8"
    )
    assert "ResponsiveSection" in app
    assert "transformOrigin: 'top center'" not in app
    assert "mx-[15px]" in app
    # The hero keeps a side margin and a readable lede at every width.
    assert "px-[15px]" in app
    # The header is styled from `index.css` rather than from utility classes written
    # into the component, so the whole bar can be changed in one place. These are the
    # names it now uses; a rewrite that dropped them would take the header with it.
    for name in ("hm-header", "hm-header-bar", "hm-menu-toggle", "hm-menu-panel"):
        assert name in chrome, name
    assert 'className="mx-auto flex w-[90%] max-w-[1248px]' in footer
    assert "body:not(.hm-react-site) h1" in legacy_css
    assert "body h1{font-size:clamp(2.8rem,6vw,5.9rem)" not in legacy_css


def test_responsive_sections_keep_the_original_prototype_internals():
    app = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")
    hero_flow = (FRONTEND / "components" / "HeroFlow.tsx").read_text(
        encoding="utf-8"
    )
    problem = (
        FRONTEND / "imports" / "03ProblemAndSolution-1" / "index.tsx"
    ).read_text(encoding="utf-8")
    steps = (
        FRONTEND / "imports" / "04HowHilalMarketsWorks" / "index.tsx"
    ).read_text(encoding="utf-8")
    features = (
        FRONTEND / "imports" / "06CoreFeatures-1" / "index.tsx"
    ).read_text(encoding="utf-8")
    trust = (
        FRONTEND / "imports" / "07TrustAndControl" / "index.tsx"
    ).read_text(encoding="utf-8")
    footer = (FRONTEND / "imports" / "10Footer-1" / "index.tsx").read_text(
        encoding="utf-8"
    )

    assert "./imports/03ProblemAndSolution-1" in app
    assert "./imports/04HowHilalMarketsWorks" in app
    assert "./imports/06CoreFeatures-1" in app
    assert "prototype-frame" in app
    assert 'data-name="Shariah screening pill"' in problem
    assert 'data-name="One platform pill"' in problem
    assert "View full evidence  →" in steps
    assert 'data-name="Setup card"' in features
    assert "4 of 5 conditions matched" in features
    assert 'data-name="Channel mark"' in features
    assert 'data-name="Control flow"' in trust
    assert 'data-name="Trust card 1"' in trust
    assert 'data-name="Proof"' in trust
    assert trust.count("items-start mt-auto overflow-clip") == 3
    assert 'viewBox="10.6304 0 193.602 51"' in footer
    assert 'h-[51px] w-[194px]' in footer
    assert styles.count("6%;") >= 4
    assert ".problem-corner-vector--top-left" in styles
    assert ".problem-corner-vector--bottom-left" in styles
    assert ".problem-corner-vector--top-right" in styles
    assert ".problem-corner-vector--bottom-right" in styles
    assert "padding-inline: max(20px, calc(50% - 390px))" in styles
    assert "text-align: center" in styles
    assert 'max-w-[820px]' in hero_flow
    assert 'data-name="Hero flow"' in hero_flow


def test_the_hero_illustration_is_shown_at_every_screen_size():
    """A phone gets the picture too, reshaped rather than removed.

    The rule is checked for the whole family, not for the one wrapper that was hiding it:
    nothing in the hero or in the illustration may be dropped at a breakpoint. `hidden`
    paired with a breakpoint is how a piece of a page silently disappears on a phone, so
    no such pair is allowed in either file.
    """

    app = APP.read_text(encoding="utf-8")
    hero_flow = (FRONTEND / "components" / "HeroFlow.tsx").read_text(encoding="utf-8")

    for name, source in (("App.tsx", app), ("HeroFlow.tsx", hero_flow)):
        for hiding in (
            "hidden sm:",
            "hidden md:",
            "hidden lg:",
            "sm:hidden",
            "md:hidden",
            "lg:hidden",
        ):
            assert hiding not in source, (name, hiding)

    # One column on a phone, two on a tablet, the original three on a desktop.
    #
    # Two columns start at 900px, not at Tailwind's 768px `md`. The rest of the page
    # collapses to a single column below 900 — the `@media (max-width: 899px)` block in
    # index.css — and while the hero used `md` it alone stood two-across between 768 and
    # 899 while everything under it was full width.
    assert "min-[900px]:grid-cols-2" in hero_flow
    assert "md:grid-cols-2" not in hero_flow, "the hero must not break at a width nothing else does"
    assert "lg:grid-cols-[1fr_1.15fr_1fr]" in hero_flow
    # The three state cards follow the same grid instead of staying a tall column.
    assert "min-[900px]:col-span-2 min-[900px]:grid min-[900px]:grid-cols-3" in hero_flow
    assert "lg:col-span-1 lg:flex lg:flex-col" in hero_flow
    # The phone gutter is the hero text's own gutter, so the picture lines up with it.
    assert "px-[15px]" in hero_flow
    assert "mx-[15px]" in app
    # And the desktop row keeps a side margin, so a 1024-wide laptop does not put the
    # cards against both edges of the screen.
    assert "lg:px-6" in hero_flow
    assert "lg:px-0" not in hero_flow


def test_legal_pages_use_the_landing_shell_and_cover_required_product_boundaries():
    """Every subject the two documents must cover is still covered.

    The section titles were rewritten into plain words — "Information we collect"
    became "What we collect" — so this checks the *subjects*, not the old headings. A
    rewrite that quietly dropped a whole subject, such as how AI is used or what
    happens to market data, fails here.
    """

    legal = LEGAL.read_text(encoding="utf-8")
    documents = LEGAL_DOCUMENTS.read_text(encoding="utf-8")
    server_legal = (
        ROOT / "src" / "ai_market_monitor" / "templates" / "hilal" / "public" / "_legal_base.html"
    ).read_text(encoding="utf-8")
    for expected in (
        "Privacy Policy",
        "Terms of Use",
    ):
        assert expected in documents, expected
    # One subject per required section id, in the document that must carry it.
    for section_id in (
        "what-we-collect",
        "ai",
        "cookies",
        "security",
        "how-long",
        "your-choices",
        "sharing",
        "never-send",
    ):
        assert f"id: '{section_id}'," in documents, section_id
    for section_id in (
        "screening",
        "boundaries",
        "market-data",
        "alerts",
        "risk",
        "liability",
        "billing",
        "acceptable-use",
    ):
        assert f"id: '{section_id}'," in documents, section_id
    assert "Private-beta legal draft" not in legal
    assert "Counsel review required before public launch" not in legal
    assert "Operating entity details are not yet configured" not in legal
    assert "Governing law is not yet configured" not in legal
    assert "qualified legal review before public launch" not in legal
    assert "qualified counsel review required before launch" not in server_legal
    assert "Not final legal advice or terms" not in server_legal
    assert "SiteNav" in legal
    assert "SiteFooter" in legal
    assert "office@hilalmarkets.com" in legal
    assert "window.HilalMarketsRuntimeConfig?.legal" in legal


def test_analytics_environment_contract_is_documented_for_both_environments():
    required = {
        "VITE_ANALYTICS_ENABLED",
        "VITE_GTM_ID",
        "VITE_GA4_MEASUREMENT_ID",
        "VITE_META_PIXEL_ID",
        "VITE_META_PIXEL_ENABLED",
        "VITE_X_PIXEL_ID",
        "VITE_X_PIXEL_ENABLED",
        "VITE_SITE_URL",
        "VITE_ANALYTICS_DEBUG",
    }
    for name in (".env.example", ".env.production.example"):
        lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
        keys = {
            line.split("=", 1)[0]
            for line in lines
            if "=" in line and not line.lstrip().startswith("#")
        }
        assert required <= keys
        assert "VITE_GA4_MEASUREMENT_ID=" in lines


def test_production_compose_pins_gtm_and_disables_direct_ga4():
    production = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    default = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'VITE_ANALYTICS_ENABLED: "true"' in production
    assert 'VITE_GTM_ID: "GTM-KBBHH2FV"' in production
    assert 'VITE_GA4_MEASUREMENT_ID: ""' in production
    assert 'MARKETING_CONSENT_ENABLED: "true"' in production
    assert 'VITE_X_PIXEL_ENABLED: "true"' in production
    assert 'VITE_X_PIXEL_ID: "re20l"' in production
    assert production.count("environment: *public_analytics_env") == 3
    assert default.count('VITE_ANALYTICS_ENABLED: "true"') == 3
    assert default.count('VITE_GTM_ID: "GTM-KBBHH2FV"') == 3
    assert default.count('VITE_GA4_MEASUREMENT_ID: ""') == 3
    assert default.count('MARKETING_CONSENT_ENABLED: "true"') == 3
    assert default.count('VITE_X_PIXEL_ENABLED: "true"') == 3
    assert default.count('VITE_X_PIXEL_ID: "re20l"') == 3
