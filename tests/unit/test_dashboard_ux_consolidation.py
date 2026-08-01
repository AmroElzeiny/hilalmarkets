import re
from pathlib import Path

ROOT = Path("src/ai_market_monitor")


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_customer_navigation_uses_consolidated_information_architecture():
    navigation = _read("core/site_content.py")

    dashboard_navigation = navigation.split("DASHBOARD_NAVIGATION =", 1)[1].split(
        "PUBLIC_PAGES =", 1
    )[0]
    assert '"Saved Assets"' not in dashboard_navigation
    assert '"Compliance Changes"' not in dashboard_navigation
    assert '"How We Screen"' not in dashboard_navigation
    assert '"Notifications"' in dashboard_navigation
    assert '"Market Scanner"' in dashboard_navigation
    assert '"Watchlists"' in dashboard_navigation


def test_screened_market_owns_staged_saved_asset_management():
    market = _read("templates/hilal/dashboard/market.html")
    runtime = _read("static/hilalmarkets.js")

    assert "data-saved-assets-dialog" in market
    assert "Mark assets you want to remove" in market
    assert "data-saved-assets-save hidden" in market
    assert "data-saved-assets-cancel" in market
    assert "Opportunities" not in market
    assert "All screened assets" not in market
    assert "removal-impact" in runtime
    assert "?confirmed=true" in runtime


def test_activity_is_a_minimal_evidence_workspace():
    activity = _read("templates/hilal/dashboard/activity.html")
    dashboard_runtime = _read("static/dashboard.js")

    assert "Notification center" not in activity
    assert "Show evidence difference" in activity
    assert "data-evidence-dialog" in activity
    assert "Condition bottlenecks" in activity
    assert "Monitor and strategy health" in activity
    assert ">Ended<" not in activity
    assert 'class="stat-grid"' not in activity
    assert 'class="activity-toolbar"' not in activity
    assert "readiness-filters" in activity
    assert "data-radar-state" in activity
    assert "data-radar-sort" in activity
    assert "activity-page-tabs" not in activity
    assert activity.count("data-hm-select") == 2
    assert "opportunity-journey-actions" in activity
    assert "dashboard.css" in activity
    assert 'xmlns="http://www.w3.org/2000/svg"' in dashboard_runtime


def test_settings_use_highlighted_channels_provider_and_schedule_controls():
    settings = _read("templates/hilal/dashboard/settings.html")

    assert 'value="binance"' in settings
    assert 'value="bybit"' in settings
    assert 'value="whatsapp"' in settings
    assert "data-near-miss-threshold" in settings
    assert "data-schedule-options=\"days\"" in settings
    assert "data-schedule-options=\"hours\"" in settings
    assert "dashboard_notifications_enabled" in settings
    assert "dashboard_notification_sound" in settings
    assert "data-settings-save disabled" in settings
    assert "ui-settings.svg" in settings
    assert "brand-binance.svg" in settings
    assert "brand-bybit.svg" in settings
    assert "brand-telegram.svg" in settings
    assert "brand-whatsapp.svg" in settings
    assert settings.count("data-hm-select") == 7
    for removed_heading in (
        "Screening policy",
        "Status-change preferences",
        "Product-specific memory",
        "Qualification changes",
        "Active alert times",
    ):
        assert removed_heading not in settings


def test_builder_uses_ai_sheet_and_minimizable_canvas_assistant():
    builder = _read("templates/hilal/dashboard/builder.html")
    script = _read("static/ai-setup-chat.js")

    assert "AI Sheet" in builder
    assert "Your Watch Plan sheet" not in builder
    assert "Live translation" not in builder
    assert "Advanced Controls" not in builder
    assert "data-ai-minimize-chat" in builder
    assert 'classList.toggle("assistant-minimized")' in script
    heading = builder.split(
        '<section class="guided-builder-heading"',
        1,
    )[1].split("</section>", 1)[0]
    assert "guided-builder-heading-line" in heading
    assert "guided-builder-heading-actions" in heading
    assert "Market Assistant" in heading
    assert "guided-starts" not in heading
    assert "ai-chat-guided-starts" in builder
    assert 'class="guided-starts ai-chat-guided-starts"' in builder


def test_dashboard_shell_has_notification_center_and_cache_busted_brand_assets():
    base = _read("templates/hilal/base_dashboard.html")
    topbar = _read("templates/hilal/partials/dashboard_topbar.html")
    sidebar = _read("templates/hilal/partials/dashboard_sidebar.html")

    # Every asset carries a cache-busting key. Which key it is belongs to the release,
    # and `test_dashboard_static_assets` proves they all share one; naming the string
    # here as well taught the habit of editing the test instead of the templates.
    assert re.search(r"\?v=[a-zA-Z0-9-]+", base)
    assert "data-notification-center" in topbar
    assert 'data-icon="bell"' in topbar
    assert "data-sidebar-collapse" in sidebar
    assert 'data-icon="panel"' in sidebar
    assert 'data-icon="panel_expand"' in sidebar
    assert "hilal-markets-logo.svg" in sidebar
    assert 'class="nav-icon nav-icon-{{ item.icon }}"' in sidebar
    assert 'data-icon="{{ item.icon }}"' not in sidebar


def test_navigation_uses_imported_outline_assets_with_green_selected_state():
    styles = _read("static/hilalmarkets-dashboard-v2.css")
    sidebar_icons = (
        "nav-home.svg",
        "nav-market.svg",
        "nav-radar.svg",
        "nav-scan.svg",
        "nav-activity.svg",
        "nav-bell.svg",
        "nav-billing.svg",
        "nav-support.svg",
    )

    for asset in sidebar_icons:
        icon = _read(f"static/{asset}")
        assert "Lucide" in icon
        assert asset in styles
    assert 'nav-icon-settings { --nav-icon-url: url("/static/ui-settings.svg"); }' in styles
    assert ".nav-item.is-active .nav-icon" in styles
    assert "color: var(--hm-apple);" in styles


def test_requested_selects_share_one_branded_accessible_component():
    activity = _read("templates/hilal/dashboard/activity.html")
    settings = _read("templates/hilal/dashboard/settings.html")
    market = _read("templates/hilal/dashboard/partials/live_market.html")
    runtime = _read("static/hilalmarkets.js")
    styles = _read("static/hilalmarkets-dashboard-v2.css")

    assert activity.count("data-hm-select") == 2
    assert settings.count("data-hm-select") == 7
    assert market.count("data-hm-select>") == 2
    assert 'document.createElement("button")' in runtime
    assert 'setAttribute("role", "listbox")' in runtime
    assert 'setAttribute("aria-haspopup", "listbox")' in runtime
    assert 'new Event("change", { bubbles: true })' in runtime
    assert '"ArrowDown"' in runtime
    assert '"Escape"' in runtime
    assert ".hm-select-trigger" in styles
    assert ".hm-select-option.is-selected" in styles
    assert ".hm-select-menu[hidden]" in styles


def test_notifications_billing_and_support_use_the_compact_layouts():
    integrations = _read("templates/hilal/dashboard/integrations.html")
    billing = _read("templates/hilal/dashboard/billing.html")
    support = _read("templates/hilal/dashboard/support.html")
    styles = _read("static/hilalmarkets-dashboard-v2.css")

    header = integrations.split(
        '<div class="page-header integrations-page-header">',
        1,
    )[1].split("</div>\n\n<section", 1)[0]
    assert "integration-summary-grid" in header
    assert "<h1>Notifications</h1>" not in integrations
    assert "billing-current-plan" in billing
    assert "billing-current-meta" in billing
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in styles
    assert "body.hilal-dashboard .billing-plan-panel .dashboard-price-card {" in styles
    assert "width: 100%;" in styles
    assert 'class="grid grid-3"' not in support


def test_collapsed_sidebar_reduces_the_shell_to_icon_width():
    styles = _read("static/hilalmarkets-dashboard-v2.css")
    runtime = _read("static/hilalmarkets.js")

    assert "body.dashboard-body.hilal-dashboard.sidebar-collapsed" in styles
    assert "--sidebar: 76px" in styles
    assert "grid-template-columns: 76px minmax(0, 1fr)" in styles
    assert 'classList.toggle("sidebar-collapsed"' in runtime
    assert "hilalmarkets-sidebar-collapsed" in runtime
    assert 'data-sidebar-collapse-icon="minimize"' in runtime
    assert 'data-sidebar-collapse-icon="expand"' in runtime


def test_auth_pages_use_the_official_logo_and_brand_styles():
    auth = _read("templates/auth.html")
    styles = _read("static/hilalmarkets-auth.css")

    assert auth.count("hilal-markets-logo.svg") == 1
    assert "auth-form-logo" not in auth
    assert "Create your account, review screened assets" not in auth
    assert "Return to your screened market" not in auth
    assert "hilalmarkets-auth.css" in auth
    assert "auth-trust-list" in auth
    assert "SOL/USDT" not in auth
    assert "var(--hm-apple)" in styles
    assert "prefers-reduced-motion" in styles


def test_requested_home_market_passport_and_scanner_refinements_are_bound():
    home = _read("templates/hilal/dashboard/home.html")
    market = _read("templates/hilal/dashboard/partials/live_market.html")
    passport = _read("templates/hilal/dashboard/passport.html")
    quick_passport = _read(
        "templates/hilal/dashboard/partials/passport_quick_view.html"
    )
    brain = _read("templates/system_brain.html")
    market_runtime = _read("static/sharia-market.js")
    dashboard_styles = _read("static/hilalmarkets-dashboard-v2.css")

    assert "Evidence and lifecycle records" not in home
    assert "opportunity-journey-progress" in home
    assert 'class="market-filter-submit"' not in market
    assert "data-live-market-methodology" in market
    assert "Favorites" in market
    assert "ui-heart-filled.svg" in market
    assert "Spread" not in market
    assert "| v{{ method.version }}" not in market
    assert "methodology.form?.requestSubmit()" in market_runtime
    assert "marketStatusLabel" in market_runtime
    assert "data-favorite-toggle" in market_runtime
    assert "status_change_following" in _read("api/routers/dashboard.py")
    assert "home-coin-logo" in home
    assert "min-height: 80px" in dashboard_styles
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in dashboard_styles

    assert "passport-page-title" not in passport
    assert "Back to market" in passport.split("</section>", 1)[0]
    assert "Reviewed by" not in passport
    assert "Next source scan" in passport
    assert "passport-select-control" in passport
    assert "Reviewed by" not in quick_passport
    assert "data-passport-quick-next-scan" in quick_passport
    assert "data-passport-quick-methodologies" in quick_passport
    assert "Add to My Screened Watchlist" not in quick_passport
    assert "Create Watchlist" not in quick_passport
    assert "Reviewer record" not in brain

    assert ".opportunity-journey-progress" in dashboard_styles
    assert 'data-dashboard-page="check_market"' in dashboard_styles
    assert ".passport-select-control" in dashboard_styles
    assert "position: relative;" in dashboard_styles
    assert "position: static !important;" in dashboard_styles
    scanner_styles = dashboard_styles.split(
        'data-dashboard-page="check_market"',
        1,
    )[1]
    assert "background: var(--hm-surface);" in scanner_styles
    assert "background: #cbfa4d !important;" in scanner_styles
    assert "background-image: none !important;" in scanner_styles
    assert "color: var(--hm-ink) !important;" in scanner_styles


def test_opportunity_cards_use_the_shared_real_asset_logo_loader():
    opportunity_card = _read("templates/hilal/macros/opportunity_card.html")
    assert "data-asset-logo-module" in opportunity_card
    assert "@web3icons/core" in opportunity_card


def test_system_brain_is_reviewer_first_and_uses_five_sections():
    template = _read("templates/system_brain.html")
    review = _read("templates/system_brain_review_workspace.html")
    styles = _read("static/system-brain.css")
    runtime = _read("static/system-brain.js")

    for label in ("Inbox", "Cases", "Operations", "Governance", "Audit &amp; Settings"):
        assert f"<span>{label}</span>" in template
    assert template.count("<nav>") == 1
    assert 'data-testid="system-brain-assistant"' in template
    assert "/dashboard/system-brain/assistant" in runtime
    assert "X-CSRF-Token" in runtime
    assert "textContent = text" in runtime
    assert "brain-overview-grid" in styles
    assert "brain-field-list" in styles
    assert "brain-terminal-action" in styles
    assert "Terminal governance actions are available" in review
    assert "AI research assistance. This is not a Sharia ruling" in review
    assert "dismiss_false_positive" in review
