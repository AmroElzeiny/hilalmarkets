from pathlib import Path

from ai_market_monitor.services.template_catalog import builtin_template_payloads


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
    template = Path("src/ai_market_monitor/templates/dashboard.html").read_text()

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
    template = Path("src/ai_market_monitor/templates/dashboard.html").read_text()

    for hook in (
        "loadInitialSchema()",
        "schemaFromForm(schema)",
        "hydrateBuilderForm(schema)",
        'api("/scan-now/interpret"',
        'api("/cockpit/strategies/validate"',
        'api("/scan-now"',
        'api(`/strategies/${id}/publish`',
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


def test_traceedge_dashboard_interaction_polish_is_present():
    template = Path("src/ai_market_monitor/templates/dashboard.html").read_text()
    script = Path("src/ai_market_monitor/static/dashboard.js").read_text()
    styles = Path("src/ai_market_monitor/static/traceedge-polish.css").read_text()

    assert "creation-card-top" in template
    assert "builder-header-status" in template
    assert "builder-bottom-bar" not in template
    assert 'name="theme"' not in template
    assert "data-settings-save" in template
    assert "data-copy-referral" in template
    assert "lucide:copy.svg" in template
    assert "support-file-button" in template
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
    assert "Referral link copied." in script
    assert "mask:url(\"data:image/svg+xml" in styles
    assert "strategy-board-surface" in styles
    assert ".understanding-feedback" in styles
    assert ".builder-coverage-panel" in styles
    assert ".builder-shell [hidden]" in styles
    assert "body.dashboard-body .monitor-card" in styles
    assert "position:relative!important" in styles


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
