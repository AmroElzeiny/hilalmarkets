from pathlib import Path

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


def test_section_and_form_visibility_use_the_required_threshold_and_dwell_time():
    tracking = TRACKING.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    assert "IntersectionObserver" in tracking
    assert "visibilityMode?: VisibilityMode" in tracking
    assert "visibilityMode: options.visibilityMode ?? 'entry'" in tracking
    assert "visibilityMode === 'entry'" in tracking
    assert "entry.intersectionRatio >= threshold" in tracking
    assert "window.setTimeout(attempt, dwellMs)" in tracking
    assert "rootMargin: '0px 0px -20% 0px'" in tracking
    assert "observer.disconnect()" in tracking
    assert "window.clearTimeout(timer)" in tracking
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


def test_waitlist_conversion_requires_a_new_server_confirmed_record():
    app = APP.read_text(encoding="utf-8")
    analytics = ANALYTICS.read_text(encoding="utf-8")
    created_branch = app.index("if (result.created)")
    conversion = app.index("trackWaitlistSuccess", created_branch)
    duplicate = app.index("trackWaitlistError('duplicate_email'", created_branch)
    assert created_branch < conversion < duplicate
    assert "trackWaitlistSuccess('landing_final', idempotencyKey.current)" in app
    assert "emitGoogle('waitlist_signup_success', {})" in analytics
    assert "emitGoogle('generate_lead'" not in analytics
    assert "trackWaitlistSubmitAttempt" in app
    assert "getFirstTouchAttribution()" in app
    assert "This email is already on the waitlist. Please use a different email." in app
    assert "status === 'duplicate' || status === 'error'" in app
    assert "aria-invalid" in app


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
    for expected in (
        "Privacy Policy",
        "Terms of Use",
        "Information we collect",
        "AI-assisted features",
        "Cookies, analytics, and marketing",
        "Shariah screening and evidence",
        "Watch Plans, Scanner, and AI",
        "Risk and disclaimers",
    ):
        assert expected in legal
    assert "Private-beta legal draft" not in legal
    assert "Counsel review required before public launch" not in legal
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
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert 'VITE_ANALYTICS_ENABLED: "true"' in compose
    assert 'VITE_GTM_ID: "GTM-KBBHH2FV"' in compose
    assert 'VITE_GA4_MEASUREMENT_ID: ""' in compose
    assert compose.count("environment: *public_analytics_env") == 3
