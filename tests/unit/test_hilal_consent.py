from pathlib import Path

from ai_market_monitor.core.site_content import PROHIBITED_ANALYTICS_PROPERTIES

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "src/ai_market_monitor/templates/hilal/base_public.html"
CONSENT = ROOT / "src/ai_market_monitor/static/hilalmarkets-consent.js"
BANNER = ROOT / "src/ai_market_monitor/templates/hilal/partials/cookie_banner.html"


def test_consent_mode_denied_defaults_precede_optional_analytics_loader():
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


def test_shared_public_shell_loads_direct_ga4_only_after_analytics_consent():
    base = BASE.read_text(encoding="utf-8")
    script = CONSENT.read_text(encoding="utf-8")

    assert '"analyticsEnabled": public_analytics_enabled' in base
    assert '"ga4MeasurementId": analytics_runtime_config.ga4MeasurementId' in base
    assert 'if (value.analytics) loadGoogle()' in script
    assert '/^G-[A-Z0-9]+$/.test(measurementId)' in script
    assert "googletagmanager.com/gtag/js?id=" in script
    assert 'window.gtag("config", measurementId)' in script
    assert 'script.dataset.hmProvider = "google-analytics"' in script


def test_first_visit_banner_has_equal_explicit_choices_and_preference_center():
    banner = BANNER.read_text(encoding="utf-8")
    assert "Essential only" in banner
    assert "Customize" in banner
    assert "Accept analytics" in banner
    assert banner.count("btn btn-secondary btn-sm") >= 3
    assert 'role="dialog"' in banner
    assert 'aria-modal="true"' in banner
    assert "data-cookie-save" in banner


def test_optional_analytics_code_contains_no_prohibited_product_properties():
    script = CONSENT.read_text(encoding="utf-8").casefold()
    for property_name in PROHIBITED_ANALYTICS_PROPERTIES:
        assert property_name.casefold() not in script
