from pathlib import Path

from ai_market_monitor.services.template_catalog import builtin_template_payloads

BUILDER_TEMPLATE = Path("src/ai_market_monitor/templates/hilal/dashboard/builder.html")
BUILDER_WORKSPACE = Path(
    "src/ai_market_monitor/templates/hilal/dashboard/partials/builder_workspace.html"
)


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

    assert "Guided Watch Plan" in template
    assert "Advanced Controls" in template
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


def test_hilalmarkets_runtime_icons_do_not_require_remote_iconify():
    sources = [
        Path("src/ai_market_monitor/static/ai-setup-chat.js").read_text(),
        Path("src/ai_market_monitor/static/dashboard.js").read_text(),
        Path("src/ai_market_monitor/static/hilalmarkets-icons.js").read_text(),
    ]

    assert all("api.iconify.design" not in source for source in sources)
    assert "window.icon" in sources[0]
    assert "window.icon" in sources[1]


def test_private_beta_integrations_expose_only_in_app_and_telegram():
    template = Path(
        "src/ai_market_monitor/templates/hilal/dashboard/integrations.html"
    ).read_text()
    settings = Path(
        "src/ai_market_monitor/templates/hilal/dashboard/settings.html"
    ).read_text()
    script = Path("src/ai_market_monitor/static/dashboard.js").read_text()
    assert 'data-testid="telegram-integration-card"' in template
    assert "In-app" in template
    assert "WhatsApp" not in template
    assert "Discord" not in template
    assert 'value="telegram"' in settings
    assert 'value="whatsapp"' not in settings
    assert 'value="discord"' not in settings
    assert "return channelActive(payload?.telegram);" in script
