import re
from pathlib import Path

from ai_market_monitor.services.template_catalog import builtin_template_payloads

BUILDER_TEMPLATE = Path("src/ai_market_monitor/templates/hilal/dashboard/builder.html")
BUILDER_WORKSPACE = Path(
    "src/ai_market_monitor/templates/hilal/dashboard/partials/builder_workspace.html"
)

APPROVED_BRAND_HEX = {
    "#1f6e97",
    "#202329",
    "#2a8fc3",
    "#2b2e35",
    "#46551b",
    "#50555e",
    "#55712a",
    "#5b626b",
    "#63716c",
    "#6c271f",
    "#7a8089",
    "#7ba428",
    "#8a6316",
    "#8d3029",
    "#cbfa4d",
    "#d0d6de",
    "#e1e5ea",
    "#e2f1f9",
    "#e4b8b2",
    "#e8fbbf",
    "#eef1f4",
    "#f1fadf",
    "#f5f8fb",
    "#fafbfc",
    "#fdf2df",
    "#fff5f3",
    "#ffffff",
}
APPROVED_BRAND_RGB = {
    tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))
    for value in APPROVED_BRAND_HEX
}


def _builder_markup() -> str:
    return BUILDER_TEMPLATE.read_text() + BUILDER_WORKSPACE.read_text()


def test_dashboard_js_includes_safe_render_helpers_and_no_invalid_math_syntax():
    source = Path("src/ai_market_monitor/static/dashboard.js").read_text()

    for helper in (
        "function safeNumber",
        "function safeArray",
        "function safeJson",
        "function renderEmptyState",
        "function renderErrorState",
        "function renderLoadingState",
    ):
        assert helper in source

    assert "Math.max(.values)" not in source
    assert "Math.min(.values)" not in source


def test_strategy_canvas_uses_progressive_disclosure_components():
    source = Path("src/ai_market_monitor/static/dashboard.js").read_text()
    template = _builder_markup()

    for helper in (
        "function renderStrategyCanvas",
        "function renderMonitorCard",
        "function renderUniverseCard",
        "function renderLogicGroupCard",
        "function renderConditionCard",
        "function renderRightPanel",
        "function openConditionDrawer",
        "function openConditionLibrary",
        "function updateBuilderStatus",
        "function renderValidationChecklist",
        "function renderPromptUnderstandingPreview",
    ):
        assert helper in source

    for marker in (
        'class="builder-app-header"',
        'class="builder-left-rail"',
        'class="builder-canvas"',
        'class="builder-right-panel"',
        'id="condition-library-modal"',
        'id="condition-editor-drawer"',
        'class="builder-mobile-stepper"',
        'class="advanced-schema-panel"',
    ):
        assert marker in template

    assert "Edit condition fields" not in template
    assert 'data-publish-schema disabled' in template


def test_strategy_canvas_keeps_schema_and_api_compatibility_hooks():
    source = Path("src/ai_market_monitor/static/dashboard.js").read_text()
    template = _builder_markup()

    for hook in (
        "loadInitialSchema()",
        "schemaFromForm(schema)",
        "hydrateBuilderForm(schema)",
        'api("/scan-now/interpret"',
        'api("/cockpit/strategies/validate"',
        'api("/scan-now"',
        "publishStrategyVersion(strategyId",
    ):
        assert hook in source

    for field_name in (
        "name",
        "direction",
        "exchange",
        "quote",
        "base_timeframe",
        "trigger_mode",
        "include_symbols",
        "alert_channels",
    ):
        assert f'name="{field_name}"' in template


def test_hilalmarkets_dashboard_interaction_system_is_present():
    template = _builder_markup()
    base = Path(
        "src/ai_market_monitor/templates/hilal/base_dashboard.html"
    ).read_text()
    settings = Path(
        "src/ai_market_monitor/templates/hilal/dashboard/settings.html"
    ).read_text()
    support = Path(
        "src/ai_market_monitor/templates/hilal/dashboard/support.html"
    ).read_text()
    script = Path("src/ai_market_monitor/static/dashboard.js").read_text()
    styles = Path("src/ai_market_monitor/static/hilalmarkets.css").read_text()
    builder_styles = Path(
        "src/ai_market_monitor/static/hilalmarkets-builder.css"
    ).read_text()

    assert "Market Assistant" in template
    assert "Advanced Controls" not in template
    assert "data-ai-setup-chat" in template
    assert "creation-card-top" in template
    assert "builder-header-status" in template
    assert "builder-bottom-bar" not in template
    assert 'name="theme"' not in template
    assert "data-settings-save" in settings
    assert 'name="screenshots"' in support
    assert "api.iconify.design" not in template
    assert "strategy-board-dialog" in template
    assert "data-open-strategy-board" in template
    assert "data-template-categories" in template
    assert 'data-builder-right-tab="coverage"' in template
    assert 'data-board-tab="coverage"' in template
    assert 'data-builder-prompt-part="goal"' in template
    assert 'data-add-prompt-section="optional"' in template
    assert 'data-add-prompt-section="avoid"' in template
    assert 'data-add-prompt-section="extra"' in template
    assert 'data-add-prompt-section="notes"' not in template
    assert "data-prompt-example-chip" in template
    assert "data-improve-builder-prompt" in template
    assert "data-check-builder-meaning" not in template
    assert "Show canvas" in template
    assert "Open workflow board" not in template
    assert "selectedTemplateCategories" in script
    assert "templatePromptParts" in script
    assert "applyTemplateToPrompt" in script
    assert "updateTemplateFilter" in script
    assert "renderStrategyBoard" in script
    assert "renderCoveragePanel" in script
    assert "submitInterpretationFeedback" in script
    assert 'api("/strategies/interpret/feedback"' in script
    assert "strategy-board-arrows" in script
    assert "findConditionByKey" in script
    assert "builderUiController?.isAiInterpreted?.()" not in script
    assert "hilalmarkets.css" in base
    assert "traceedge-polish.css" not in base + template
    assert "hilalmarkets-bridge.css" not in base + template
    assert "--emerald-800" in styles
    assert ".guided-builder-heading" in builder_styles
    assert "prefers-reduced-motion" in styles


def test_builtin_templates_have_explicit_dashboard_categories():
    payloads = builtin_template_payloads()

    assert payloads
    assert all(template["ui_categories"] for template in payloads)
    assert any(
        template["key"] == "liquidity_sweep"
        and {"Liquidity sweep", "Trend continuation"}.issubset(template["ui_categories"])
        for template in payloads
    )
    assert any(
        template["key"] == "six_month_high_breakout"
        and "Breakout confirmation" in template["ui_categories"]
        for template in payloads
    )


def test_hilalmarkets_core_styles_include_focus_and_reduced_motion_guards():
    stylesheet = Path("src/ai_market_monitor/static/hilalmarkets.css").read_text()

    assert ":focus-visible" in stylesheet
    assert "prefers-reduced-motion:reduce" in stylesheet
    assert "outline:3px solid" in stylesheet


def test_authenticated_dashboard_has_no_legacy_blue_theme():
    dashboard_css = Path("src/ai_market_monitor/static/dashboard.css").read_text()
    hilal_css = Path("src/ai_market_monitor/static/hilalmarkets.css").read_text()

    assert "Unified modern blue dashboard theme" not in dashboard_css
    for legacy_color in ("#60a5fa", "#3b82f6", "#2563eb", "#7dd3fc"):
        assert legacy_color not in dashboard_css.lower()
    assert ".home-start-card" in hilal_css
    assert "--dash-mint:var(--emerald-800)" in hilal_css


def test_hilalmarkets_runtime_icons_do_not_require_remote_iconify():
    sources = [
        Path("src/ai_market_monitor/static/ai-setup-chat.js").read_text(),
        Path("src/ai_market_monitor/static/dashboard.js").read_text(),
        Path("src/ai_market_monitor/static/hilalmarkets-icons.js").read_text(),
    ]

    assert all("api.iconify.design" not in source for source in sources)
    assert "window.icon" in sources[0]
    assert "window.icon" in sources[1]


def test_notification_integrations_show_plan_gated_whatsapp_without_discord():
    template = Path(
        "src/ai_market_monitor/templates/hilal/dashboard/integrations.html"
    ).read_text()
    settings = Path(
        "src/ai_market_monitor/templates/hilal/dashboard/settings.html"
    ).read_text()
    script = Path("src/ai_market_monitor/static/dashboard.js").read_text()
    assert 'data-testid="telegram-integration-card"' in template
    assert 'data-testid="whatsapp-integration-card"' in template
    assert "In-app" in template
    assert "WhatsApp" in template
    assert "is-plan-locked" in template
    assert "Discord" not in template
    assert 'value="telegram"' in settings
    assert 'value="whatsapp"' in settings
    assert 'value="bybit"' in settings
    assert 'value="discord"' not in settings
    assert "return channelActive(payload?.telegram);" in script


def test_dashboard_notification_polling_is_scoped_to_authenticated_shell():
    source = Path("src/ai_market_monitor/static/hilalmarkets.js").read_text()

    assert "if (notificationCenter && !notificationStack)" in source
    assert "if (!notificationCenter || document.hidden" in source


def test_authenticated_surfaces_load_the_final_brand_layer_last():
    dashboard = Path(
        "src/ai_market_monitor/templates/hilal/base_dashboard.html"
    ).read_text(encoding="utf-8")
    brain = Path("src/ai_market_monitor/templates/system_brain.html").read_text(
        encoding="utf-8"
    )
    brain_auth = Path(
        "src/ai_market_monitor/templates/system_brain_auth.html"
    ).read_text(encoding="utf-8")

    assert "hilalmarkets-brand.css" in dashboard
    assert "hilalmarkets-dashboard-v2.css" in dashboard
    page_styles_index = dashboard.index("{% block page_styles %}")
    brand_index = dashboard.index("hilalmarkets-brand.css")
    final_index = dashboard.index("hilalmarkets-dashboard-v2.css")
    assert page_styles_index < brand_index < final_index
    assert "fonts.googleapis.com" not in dashboard
    assert 'data-brand-system="hilal-markets-v2"' in dashboard
    assert "hilalmarkets-brand.css" in brain
    assert "hilalmarkets-brand.css" in brain_auth
    assert "brain-orbit" not in brain_auth


def test_every_page_shares_the_current_cache_busting_release_key():
    """One key across every asset on every page, whatever this release's key is.

    The rule is that they all match, not that they equal one particular string. Spelling
    the string out here meant a released CSS change failed this test until somebody
    edited it, which taught the habit of editing the test instead of the templates.

    Public and authenticated pages are checked together on purpose. They shared nine
    different keys, and a stylesheet both of them load could be edited, bumped on one
    page and left stale on the other, so half the site kept serving the old file.
    """

    release_keys = {
        value
        for path in Path("src/ai_market_monitor/templates").rglob("*.html")
        for value in re.findall(
            r"\?v=([a-zA-Z0-9-]+)", path.read_text(encoding="utf-8")
        )
    }

    assert len(release_keys) == 1, sorted(release_keys)
    # And a stale asset cannot be served: the key has to be present at all.
    assert release_keys != {""}


def test_final_authenticated_styles_use_only_approved_brand_hex_colors():
    files = (
        Path("src/ai_market_monitor/static/hilalmarkets-brand.css"),
        Path("src/ai_market_monitor/static/hilalmarkets-dashboard-v2.css"),
        Path("src/ai_market_monitor/static/system-brain.css"),
    )
    unexpected: dict[str, list[str]] = {}
    for path in files:
        hex_values = {
            value.lower()
            for value in re.findall(r"#[0-9a-fA-F]{6}", path.read_text(encoding="utf-8"))
        }
        rgb_values = {
            tuple(map(int, value))
            for value in re.findall(
                r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)",
                path.read_text(encoding="utf-8"),
            )
        }
        rejected_hex = sorted(hex_values - APPROVED_BRAND_HEX)
        rejected_rgb = sorted(rgb_values - APPROVED_BRAND_RGB)
        if rejected_hex or rejected_rgb:
            unexpected[str(path)] = [
                *rejected_hex,
                *(f"rgb{value}" for value in rejected_rgb),
            ]

    assert unexpected == {}


def test_dashboard_runtime_generated_colors_use_only_approved_brand_palette():
    path = Path("src/ai_market_monitor/static/dashboard.js")
    source = path.read_text(encoding="utf-8")
    hex_values = {
        value.lower()
        for value in re.findall(r"#[0-9a-fA-F]{6}", source)
    }
    rgb_values = {
        tuple(map(int, value))
        for value in re.findall(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", source)
    }

    assert hex_values <= APPROVED_BRAND_HEX
    assert rgb_values <= APPROVED_BRAND_RGB


def test_every_dashboard_template_inherits_the_single_brand_shell():
    dashboard_templates = Path("src/ai_market_monitor/templates/hilal/dashboard")
    top_level = sorted(dashboard_templates.glob("*.html"))

    assert top_level
    assert all(
        '{% extends "hilal/base_dashboard.html" %}' in path.read_text(encoding="utf-8")
        for path in top_level
    )
    assert all(
        "path='/hilalmarkets.css'" not in path.read_text(encoding="utf-8")
        for path in top_level
    )
    assert not {
        path.name: re.findall(
            r"#[0-9a-fA-F]{3,8}(?![\w-])", path.read_text(encoding="utf-8")
        )
        for path in dashboard_templates.rglob("*.html")
        if re.findall(
            r"#[0-9a-fA-F]{3,8}(?![\w-])", path.read_text(encoding="utf-8")
        )
    }


def test_official_mark_and_complete_outline_icon_catalog_are_used():
    logo = Path("src/ai_market_monitor/static/hilalmarkets-logo-mark.svg").read_text(
        encoding="utf-8"
    )
    icons = Path("src/ai_market_monitor/static/hilalmarkets-icons.js").read_text(
        encoding="utf-8"
    )
    templates = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/ai_market_monitor/templates").rglob("*.html")
    )
    requested_icons = set(re.findall(r'data-icon="([a-z0-9_-]+)"', templates))
    available_icons = set(re.findall(r"^\s*([a-z0-9_]+):", icons, re.MULTILINE))

    assert "#CBFA4D" in logo
    assert "#2B2E35" in logo
    assert "#0F5C4D" not in logo
    assert requested_icons <= available_icons
    assert "api.iconify.design" not in icons
    assert 'stroke-width="1.75"' in icons
