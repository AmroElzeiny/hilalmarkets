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
    # The waitlist form is the section the landing page is currently built around, and
    # it counts as seen only after half of it has been on screen for a second.
    assert "visibilityMode: 'percentage'" in app
    assert "threshold: 0.5" in app
    assert "dwellMs: 1000" in app


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
        "waitlist",
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
    assert 'analyticsName="features"' not in app

    faq_ids = {
        "what_is_hilal",
        "target_audience",
        "is_broker",
        "provides_signals",
        "supported_markets",
        "shariah_screening",
        "halal_guarantee",
        "ai_strategy_builder",
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


def test_waitlist_replaces_pricing_and_tracks_only_validated_commerce_metadata():
    """The landing page is pre-launch: the waitlist form stands where the plans stood.

    `Pricing.tsx` is kept, and still has to satisfy every rule it satisfied before, so
    putting the section back is one import rather than a rebuild from memory. What it
    must not do is appear on the page while nobody can buy anything.
    """

    app = APP.read_text(encoding="utf-8")
    analytics = ANALYTICS.read_text(encoding="utf-8")
    pricing = (FRONTEND / "components" / "Pricing.tsx").read_text(encoding="utf-8")
    assert "<Waitlist />" in app
    assert 'id="waitlist"' in app
    assert "<Pricing />" not in app
    assert "components/Pricing" not in app
    assert "trackWaitlist" in analytics
    assert "trackPricingSectionView" in pricing
    assert "trackBillingIntervalChanged" in pricing
    assert "trackPlanSelected" in pricing
    assert "validPlanCode" in analytics
    assert "validBillingInterval" in analytics
    assert "dataLayer" not in pricing
    assert "email_address" not in pricing


def test_the_landing_page_offers_the_waitlist_and_never_an_account():
    """Nothing rendered by the site may lead to sign-in, sign-up or the dashboard.

    Checked across every rendered source file rather than the few obvious ones. The
    header was not the only place with a way into the product: the hero illustration
    carried its own "Build plan" button straight to checkout, and a test that only
    looked at `App.tsx` and `SiteChrome.tsx` would have passed with it still there.
    """

    app = APP.read_text(encoding="utf-8")
    chrome = (FRONTEND / "components" / "SiteChrome.tsx").read_text(encoding="utf-8")
    rendered = [
        path
        for path in (*FRONTEND.rglob("*.tsx"), *FRONTEND.rglob("*.ts"))
        # Kept on purpose and not imported by anything; it is the section that returns
        # when the product opens, so it still holds the real plan links.
        if path.name != "Pricing.tsx"
    ]
    assert len(rendered) > 10
    for path in rendered:
        source = path.read_text(encoding="utf-8")
        for forbidden in (
            "/signin",
            "/signup",
            "/dashboard",
            "/subscribe?",
            "Sign in",
            "Get started",
            "Start free",
            "Create a free account",
        ):
            assert forbidden not in source, (path.name, forbidden)

    # Every call to action points at the one form, and it is named the same way in
    # every place a visitor can meet it: the hero button, the desktop header and the
    # phone menu.
    assert app.count('href="#waitlist"') == 1
    assert "waitlistHref" in chrome
    assert chrome.count("href={waitlistHref}") == 2
    assert chrome.count(">\n            Join the waitlist\n          </TrackedCta>") == 2
    assert "{ label: 'Pricing', target: '#pricing' }" not in chrome


def test_every_react_page_closes_on_the_waitlist_using_the_servers_own_wording():
    """Contact, Privacy and Terms end on the waitlist, like every server-rendered page.

    They are the half of the site the bundle draws, so without this they stayed silent
    about the private beta while the other half spoke about nothing else. The words come
    from the server's runtime config rather than being typed again here: two renderers,
    one message, so neither can be updated without the other.
    """

    band = (FRONTEND / "components" / "WaitlistBand.tsx").read_text(encoding="utf-8")
    contact = (FRONTEND / "pages" / "ContactPage.tsx").read_text(encoding="utf-8")
    legal = (FRONTEND / "pages" / "LegalPage.tsx").read_text(encoding="utf-8")

    # The band takes every word from the server, so no copy is duplicated in the bundle.
    assert "window.HilalMarketsRuntimeConfig?.waitlist" in band
    assert "config.headline" in band
    assert "config.body" in band
    assert "if (!config?.mode) return null" in band
    # It links back to the one form; it does not grow a second copy of it.
    assert "/#waitlist" in band
    assert "<input" not in band
    assert "submitWaitlist" not in band

    for name, source in (("ContactPage.tsx", contact), ("LegalPage.tsx", legal)):
        assert "import { WaitlistBand }" in source, name
        assert "<WaitlistBand location=" in source, name
        # Inside <main>, above the footer, so it is part of the page rather than chrome.
        assert source.index("<WaitlistBand") < source.index("<SiteFooter />"), name

    # Privacy and Terms each get their own name, so the analytics can tell them apart.
    assert "privacy_footer" in legal
    assert "terms_footer" in legal


def test_legal_pages_scope_accounts_and_billing_to_the_private_beta():
    """The legal text may not read as though anyone can buy or open an account today."""

    legal = (FRONTEND / "pages" / "LegalPage.tsx").read_text(encoding="utf-8")

    # Terms: paid access is not offered, with no escape hatch that reopens it silently.
    assert "unless a separately presented offer expressly states otherwise" not in legal
    assert "Paid access is not offered to the public during the private beta." in legal
    assert "nothing is charged for use of the service in this period" in legal
    # The protection for a future paid launch is kept, not deleted with the rest.
    assert "must disclose the price, currency, access period" in legal

    # Accounts exist, but only by invitation, in both documents.
    assert "Accounts are not open to the public during the private beta" in legal
    assert "Accounts are issued by invitation during the private beta." in legal
    assert "Accounts are not open to the public: they are issued only to people" in legal
    assert "invited private-beta users only" in legal
    # And the account obligations for invited users survive.
    assert "Account information must be accurate and kept current." in legal
    assert "You are responsible for protecting credentials" in legal


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

    assert 'id="waitlist-email"' in app
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

    # The panel is apple green, where the site's green focus ring would vanish.
    styles = STYLES.read_text(encoding="utf-8")
    assert "#waitlist input:focus-visible" in styles
    assert "#waitlist button:focus-visible" in styles
    assert "outline: 3px solid var(--color-ink)" in styles
    assert "min-height: 44px" in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles


def test_pricing_uses_approved_plans_accessibility_and_real_handoff():
    """`Pricing.tsx` is not on the page today, but it still has to be correct.

    It is kept so the section can return with one import when the product opens. Every
    rule it had to satisfy while it was live is asserted here, so it cannot rot into a
    version that quotes a price the server no longer charges.
    """

    app = APP.read_text(encoding="utf-8")
    pricing = (FRONTEND / "components" / "Pricing.tsx").read_text(encoding="utf-8")
    chrome = (FRONTEND / "components" / "SiteChrome.tsx").read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    # The one thing a visitor can act on comes before the questions, wherever that
    # section happens to be.
    assert app.index('analyticsName="waitlist"') < app.index('analyticsName="faq"')
    assert 'aria-label="Mobile navigation"' in chrome
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
    contact = CONTACT.read_text(encoding="utf-8")
    assert "CheckIcon" in contact
    assert 'role="status"' in contact
    assert 'role="alert"' in contact
    assert "Your message was sent successfully." in contact
    assert "We could not send your message." in contact
    assert 'href="mailto:office@hilalmarkets.com"' in contact
    assert "decoration-dotted" in contact
    assert 'min-h-[142px]' in contact
    assert "font-semibold text-ink" in contact
    assert "traceback" not in contact.casefold()
    assert "stack trace" not in contact.casefold()


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
    assert 'mx-[15px]' in app
    assert "pt-3 text-[15px]" in app
    assert "shadow-[0_14px_34px_-22px_rgba(43,46,53,0.6)]" in chrome
    assert "border-[#e1e5ea]" in chrome
    assert "font-sans text-[15px] font-bold text-[#2b2e35]" in chrome
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


def test_legal_pages_use_the_landing_shell_and_cover_required_product_boundaries():
    legal = LEGAL.read_text(encoding="utf-8")
    server_legal = (
        ROOT / "src" / "ai_market_monitor" / "templates" / "hilal" / "public" / "_legal_base.html"
    ).read_text(encoding="utf-8")
    for expected in (
        "Privacy Policy",
        "Terms of Use",
        "Information we collect",
        "AI-assisted features",
        "Cookies, analytics, and marketing",
        "Shariah screening and evidence",
        "Watchlists, Scanner, and AI",
        "Risk and disclaimers",
    ):
        assert expected in legal
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
