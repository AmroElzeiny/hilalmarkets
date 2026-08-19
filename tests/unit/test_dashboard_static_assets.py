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
    "#63696f",
    "#6c271f",
    "#7ba428",
    # The edge of a *control*, as opposed to the edge of a card. Both hairlines below
    # measure under 1.5:1 on white, so a bordered input had a boundary in the code and
    # none on the screen; this one measures 3.90:1 and clears WCAG 1.4.11.
    "#79828d",
    "#8a6316",
    "#8d3029",
    # Text and edges **on** the near-black panel, where the light neutrals are far too
    # bright. 6.51:1 and 3.04:1 on `--hm-ink` respectively.
    "#aeb4bd",
    "#767b83",
    # The edge of an information panel — the partner of `#e4b8b2`, which is the edge of
    # a danger panel. It was missing, so the one component that needed it had to write a
    # value of its own.
    "#bcdcec",
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
        "src/ai_market_monitor/templates/hilal/dashboard_test/settings.html"
    ).read_text(encoding="utf-8")
    support = Path(
        "src/ai_market_monitor/templates/hilal/dashboard_test/support.html"
    ).read_text(encoding="utf-8")
    script = Path("src/ai_market_monitor/static/dashboard.js").read_text()
    styles = Path("src/ai_market_monitor/static/hilalmarkets.css").read_text()
    builder_styles = Path(
        "src/ai_market_monitor/static/hilalmarkets-builder.css"
    ).read_text()

    assert "Market assistant" in template
    assert "Advanced Controls" not in template
    assert "data-ai-setup-chat" in template
    assert "creation-card-top" in template
    assert "builder-header-status" in template
    assert "builder-bottom-bar" not in template
    assert 'name="theme"' not in template
    # Settings saves as you go, so the thing to check is that it says so, not that it has
    # a Save button. Help still takes pictures with the message.
    assert "data-g-saved" in settings
    assert "data-h-files" in support
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
    # The ring is a token now, not a colour written here. It used to be a see-through
    # teal that measured about 1.4:1 against the pages that load this file. Whether the
    # value itself is visible is checked by `test_invariant_focus_visibility.py`.
    assert "outline:var(--hm-focus-ring)" in stylesheet
    assert "box-shadow:var(--hm-focus-halo)" in stylesheet


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


def test_notification_channels_are_decided_once_and_a_locked_one_says_why():
    """Which channels exist is a server decision, and it is made in one place.

    The older Notifications page listed its channels in its own markup and the older
    Settings page listed them again in its own, so switching one on in the environment
    meant editing two templates and nothing said when they disagreed. Both pages are gone.
    Their replacements loop over what the router hands them, and the router asks
    `offered_channels`, which is the single owner of "can we really deliver this".
    """

    connections = Path(
        "src/ai_market_monitor/templates/hilal/dashboard_test/connections.html"
    ).read_text(encoding="utf-8")
    settings = Path(
        "src/ai_market_monitor/templates/hilal/dashboard_test/settings.html"
    ).read_text(encoding="utf-8")
    router = Path(
        "src/ai_market_monitor/api/routers/dashboard_test.py"
    ).read_text(encoding="utf-8")
    script = Path("src/ai_market_monitor/static/dashboard.js").read_text()

    # Both pages draw a list they were given.
    assert "for channel in channels" in connections or "channel.label" in connections
    assert "for channel in alert_channels" in settings

    # And neither one names a channel of its own.
    for typed in ('value="telegram"', 'value="whatsapp"', 'value="discord"'):
        assert typed not in settings, typed
    assert "Discord" not in connections
    assert "Discord" not in settings

    # A channel that is off says why, and what would turn it on.
    assert "channel.unavailable_reason" in connections
    assert "channel.unavailable_fix" in connections

    # One owner for "can we deliver this at all".
    assert "offered_channels" in router
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
        # `/main`. A new page is exactly where a new colour gets invented, so the sheet
        # that designs it is held to the same palette as the ones it sits on top of.
        Path("src/ai_market_monitor/static/hm-main.css"),
        # The side menu and the topbar. They are on every signed-in page, so a colour
        # invented here would be invented on all of them at once.
        Path("src/ai_market_monitor/static/hm-shell.css"),
        # Every page the side menu opens. These were not checked at all, and the menu
        # used to open the older copy of five of them — so nothing walked the redesigned
        # pages and two off-palette colours sat on Connections unnoticed. Now that the
        # menu really leads here, the palette is checked here too.
        Path("src/ai_market_monitor/static/hm-dashboard-test.css"),
        Path("src/ai_market_monitor/static/hm-connections-test.css"),
        Path("src/ai_market_monitor/static/hm-watch-test.css"),
        Path("src/ai_market_monitor/static/hm-monitor-test.css"),
        Path("src/ai_market_monitor/static/hm-account-test.css"),
        Path("src/ai_market_monitor/static/hm-hilal-chat.css"),
        # The way in. It was not checked at all, which is how a page nobody scans gets a
        # colour nobody approved — and it is the first thing every customer sees.
        Path("src/ai_market_monitor/static/hilalmarkets-auth.css"),
        # The cookie banner, now that one stylesheet draws it for every surface.
        Path("src/ai_market_monitor/static/hilalmarkets-cookie.css"),
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


def test_the_small_mark_is_a_piece_of_the_real_logo_and_not_a_drawing_of_it():
    """Wherever the product needs the symbol alone, it uses the real one.

    There used to be a hand-drawn `hilalmarkets-logo-mark.svg`: a dark rounded square
    with a cut corner and two green circles. It was not the brand's symbol, nothing kept
    it in step with the real logo, and it was on six surfaces — the minimized side menu,
    the public header and footer, the admin sign-in, a public dashboard page, and the
    `logo` a search engine reads out of the site's structured data.

    `hilal-markets-symbol.svg` is not a redrawing. Every path in it appears **byte for
    byte** inside `hilal-markets-logo.svg`, which is what makes drift impossible: change
    the logo and this fails until the symbol is taken from it again.
    """

    static = Path("src/ai_market_monitor/static")
    symbol = (static / "hilal-markets-symbol.svg").read_text(encoding="utf-8")
    wordmark = (static / "hilal-markets-logo.svg").read_text(encoding="utf-8")

    paths = re.findall(r'<path d="([^"]+)"', symbol)
    assert len(paths) >= 4, "the symbol lost its shape"
    for one in paths:
        assert one in wordmark, "this path is not in the logo; the symbol was redrawn"

    # The brand's ink, and no invented accent. The mark it replaced painted itself apple
    # green, which section 9 keeps for a single focal element and not for a logo.
    assert "#2B2E35" in symbol
    assert "#CBFA4D" not in symbol
    assert "#0F5C4D" not in symbol

    # The hand-drawn one is gone from disk, not merely unreferenced.
    assert not (static / "hilalmarkets-logo-mark.svg").exists()

    icons = (static / "hilalmarkets-icons.js").read_text(encoding="utf-8")
    templates = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/ai_market_monitor/templates").rglob("*.html")
    )
    requested_icons = set(re.findall(r'data-icon="([a-z0-9_-]+)"', templates))
    available_icons = set(re.findall(r"^\s*([a-z0-9_]+):", icons, re.MULTILINE))
    assert requested_icons <= available_icons
    assert "api.iconify.design" not in icons
    assert 'stroke-width="1.75"' in icons
