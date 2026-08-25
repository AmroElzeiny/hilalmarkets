from pathlib import Path

from ai_market_monitor.core.site_content import PROHIBITED_ANALYTICS_PROPERTIES

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "src/ai_market_monitor/templates/hilal/base_public.html"
REACT_SHELL = ROOT / "src/ai_market_monitor/templates/hilal/public/react_site.html"
DASHBOARD_SHELL = ROOT / "src/ai_market_monitor/templates/hilal/base_dashboard.html"
AUTH_SHELL = ROOT / "src/ai_market_monitor/templates/auth.html"
LANDING_INDEX = ROOT / "Hilal-Markets-Website/index.html"
CONSENT = ROOT / "src/ai_market_monitor/static/hilalmarkets-consent.js"
BANNER = ROOT / "src/ai_market_monitor/templates/hilal/partials/cookie_banner.html"


def test_consent_mode_denied_defaults_precede_consent_gated_gtm_loader():
    base = BASE.read_text(encoding="utf-8")
    consent_script = "hilalmarkets-consent.js"
    assert 'window.gtag("consent", "default"' in base
    assert base.index('window.gtag("consent", "default"') < base.index(consent_script)
    for setting in (
        "ad_storage",
        "analytics_storage",
        "ad_user_data",
        "ad_personalization",
        "functionality_storage",
        "personalization_storage",
    ):
        assert f'{setting}: "denied"' in base
    assert 'security_storage: "granted"' in base
    assert "googletagmanager.com/gtm.js" not in base
    assert "googletagmanager.com/ns.html" not in base


def test_consent_choice_is_versioned_persistent_and_withdrawable():
    script = CONSENT.read_text(encoding="utf-8")
    assert "hm-cookie-consent-v${version}" in script
    assert 'cookieName = "hm_cookie_consent"' in script
    assert "updatedAt: new Date().toISOString()" in script
    assert "window.localStorage.setItem" in script
    assert "document.cookie" in script
    assert "if (value.analytics) loadGoogle()" in script
    assert "saveAndClose(defaults)" in script
    assert 'analytics_storage: value.analytics ? "granted" : "denied"' in script
    assert 'document.querySelector("input[data-consent-analytics]")' in script
    assert 'document.querySelector("input[data-consent-marketing]")' in script


def test_shared_public_shell_loads_only_gtm_after_analytics_consent():
    base = BASE.read_text(encoding="utf-8")
    react_shell = REACT_SHELL.read_text(encoding="utf-8")
    script = CONSENT.read_text(encoding="utf-8")

    for shell in (base, react_shell):
        assert '"analyticsEnabled": public_analytics_enabled' in shell
        assert '"gtmContainerId": analytics_runtime_config.gtmId' in shell
        assert "ga4MeasurementId" not in shell
    assert 'if (value.analytics) loadGoogle()' in script
    assert '!/^GTM-[A-Z0-9]+$/.test(containerId)' in script
    assert "googletagmanager.com/gtm.js?id=" in script
    assert 'script.dataset.hmProvider = "google-tag-manager"' in script
    assert "googletagmanager.com/gtag/js" not in script
    assert "google-analytics" not in script
    assert "ga4MeasurementId" not in script


def test_x_pixel_loads_once_only_after_marketing_consent():
    script = CONSENT.read_text(encoding="utf-8")
    assert 'if (marketing) loadX()' in script
    assert 'script.src = "https://static.ads-twitter.com/uwt.js"' in script
    assert 'window.twq.integration = "gtm-ad-manager"' in script
    assert 'window.twq("config", pixelId)' in script
    assert 'script.dataset.hmProvider = "x-pixel"' in script
    assert "configuredPixels.has(pixelId)" in script

    for shell in (BASE, REACT_SHELL, DASHBOARD_SHELL, AUTH_SHELL):
        content = shell.read_text(encoding="utf-8")
        assert '"xPixelEnabled"' in content
        assert '"xPixelId"' in content
        assert "static.ads-twitter.com" not in content
        assert "twq(" not in content
        assert "hilalmarkets-consent.js" in content


def test_system_brain_templates_never_include_x_pixel_or_consent_loader():
    templates = ROOT / "src/ai_market_monitor/templates"
    system_brain_templates = tuple(templates.glob("system_brain*.html"))
    assert system_brain_templates
    for path in system_brain_templates:
        content = path.read_text(encoding="utf-8")
        assert "hilalmarkets-consent.js" not in content, path
        assert "static.ads-twitter.com" not in content, path
        assert "xPixelId" not in content, path
        assert "twq(" not in content, path


def test_public_document_shells_and_standalone_source_do_not_preload_gtm():
    for path in (BASE, REACT_SHELL):
        shell = path.read_text(encoding="utf-8")
        assert "<!-- Google Tag Manager -->" not in shell
        assert "Google Tag Manager (noscript)" not in shell

    standalone = LANDING_INDEX.read_text(encoding="utf-8")
    assert "googletagmanager.com/gtm.js" not in standalone
    assert "googletagmanager.com/ns.html" not in standalone


def test_first_visit_banner_has_equal_explicit_choices_and_preference_center():
    """Refusing must cost exactly what accepting costs.

    "Equal" here is about **effort**, not about colour. All three answers are buttons in
    the same row of the same banner, at the same size, one click each — refusing analytics
    is never a second step behind "Customize", and never a smaller target.

    "Accept analytics" is the emphasised one, which is a design choice about where the
    eye lands, not about what is possible: the refusal is not hidden, not shrunk, and not
    moved. If that emphasis ever has to go, this test is the place that says what may not
    change with it.
    """

    banner = BANNER.read_text(encoding="utf-8")
    assert "Essential only" in banner
    assert "Customize" in banner
    assert "Accept analytics" in banner
    # Three buttons, same size class, all inside the banner's own actions row.
    actions = banner.split('<div class="cookie-actions">', 1)[1].split("</div>", 1)[0]
    assert actions.count("btn-sm") == 3
    assert actions.count("<button") == 3
    for marker in (
        "data-cookie-essential",
        "data-cookie-customize",
        "data-cookie-accept-analytics",
    ):
        assert marker in actions
    # Refusing is a first-class button, never only a choice inside the settings window.
    assert 'role="dialog"' in banner
    assert 'aria-modal="true"' in banner
    assert "data-cookie-save" in banner


def test_optional_analytics_code_contains_no_prohibited_product_properties():
    script = CONSENT.read_text(encoding="utf-8").casefold()
    for property_name in PROHIBITED_ANALYTICS_PROPERTIES:
        assert property_name.casefold() not in script
