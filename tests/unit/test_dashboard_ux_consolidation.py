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
    assert '"Check the Market Now"' in dashboard_navigation


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


def test_activity_is_a_minimal_evidence_notification_center():
    activity = _read("templates/hilal/dashboard/activity.html")

    assert "Notification center" in activity
    assert "Show evidence difference" in activity
    assert "data-evidence-dialog" in activity
    assert "Condition bottlenecks" in activity
    assert "Monitor and strategy health" in activity
    assert ">Ended<" not in activity
    assert 'class="stat-grid"' not in activity


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


def test_dashboard_shell_has_notification_center_and_cache_busted_brand_assets():
    base = _read("templates/hilal/base_dashboard.html")
    topbar = _read("templates/hilal/partials/dashboard_topbar.html")
    sidebar = _read("templates/hilal/partials/dashboard_sidebar.html")

    assert "20260722-dashboard-refresh" in base
    assert "data-notification-center" in topbar
    assert 'data-icon="bell"' in topbar
    assert "data-sidebar-collapse" in sidebar
    assert 'data-icon="panel"' in sidebar
