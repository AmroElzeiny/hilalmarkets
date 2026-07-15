from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_JS = ROOT / "src" / "ai_market_monitor" / "static" / "dashboard.js"
BUILDER_HTML = (
    ROOT
    / "src"
    / "ai_market_monitor"
    / "templates"
    / "hilal"
    / "dashboard"
    / "partials"
    / "builder_workspace.html"
)


def test_strategy_builder_uses_dedicated_interpretation_endpoint():
    source = DASHBOARD_JS.read_text(encoding="utf-8")

    assert 'api("/strategies/interpret"' in source
    assert "data-open-interpreted-map" in source


def test_builder_preserves_advanced_condition_metadata_fields():
    source = DASHBOARD_JS.read_text(encoding="utf-8")

    for field in (
        "source_fragment",
        "confidence",
        "provider_required",
        "availability",
        "approximation_note",
        "near_miss",
        "alerts",
        "universe",
    ):
        assert field in source


def test_builder_hides_raw_json_by_default_and_blocks_critical_activation():
    html = BUILDER_HTML.read_text(encoding="utf-8")
    source = DASHBOARD_JS.read_text(encoding="utf-8")

    assert "Advanced schema and debug details" in html
    assert 'id="builder-json"' in html
    assert "activation_blocked" in source
    assert "Critical issue" in source
    assert "provider-badge" in source
