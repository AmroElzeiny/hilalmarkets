import json
from collections import Counter
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.dashboard_paths import MONITOR_PATH
from ai_market_monitor.engine.condition_registry import condition_registry_payload
from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.engine.strategy_compiler_v2 import compile_strategy_draft_v2
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2
from ai_market_monitor.services.monitor_canvas import CanvasPlan, build_condition_ast
from tests.browser.conftest import close_any_open_guide, signup
from tests.browser.test_monitor_canvas_e2e import CONTRACT_URL
from tests.factories import candle_sets


def _install_capabilities_reader(page: Page):
    # Execute the shipped reader and its real requestJson/api helpers. The rest of
    # dashboard.js initializes unrelated screens and is outside this contract test.
    source = Path("src/ai_market_monitor/static/dashboard.js").read_text(encoding="utf-8")
    prefix = source[: source.index("  function csv(value)")]
    page.add_script_tag(
        content=prefix
        + """
      window.publicationTest = {
        load: loadCapabilityRegistry,
        current: () => capabilityRegistry,
      };
    })();"""
    )


def test_capabilities_reader_preserves_only_the_authoritative_items(page: Page):
    page.goto("about:blank")
    payload = condition_registry_payload()
    page.evaluate(
        "payload => { window.fetch = async () => ({ok: true, json: async () => payload}); }",
        payload,
    )
    _install_capabilities_reader(page)
    result = page.evaluate("() => publicationTest.load()")
    assert result == json.loads(json.dumps(payload))
    assert all(item["availability"] == "available" for item in result["items"])
    # A smaller successful response must not retain items from the previous read.
    payload["items"] = [payload["items"][-1]]
    payload["categories"] = []
    page.evaluate(
        "payload => { window.fetch = async () => ({ok: true, json: async () => payload}); }",
        payload,
    )
    result = page.evaluate("() => publicationTest.load()")
    assert result["items"] == json.loads(json.dumps(payload["items"]))


def test_failed_capabilities_read_discards_all_executable_fallbacks(page: Page):
    page.goto("about:blank")
    _install_capabilities_reader(page)
    page.evaluate(
        "payload => { window.fetch = async () => ({ok: true, json: async () => payload}); }",
        condition_registry_payload(),
    )
    assert page.evaluate("async () => (await publicationTest.load()).items.length") > 0
    page.evaluate("""() => {
      window.fetch = async () => ({ok: false, statusText: 'Unavailable', json: async () => ({})});
    }""")
    result = page.evaluate("""async () => {
      try { await publicationTest.load(); return {error: null}; }
      catch (error) { return {error: error.message, current: publicationTest.current()}; }
    }""")
    assert "Conditions are unavailable" in result["error"]
    assert result["current"] is None


def test_current_builder_renders_only_its_published_catalog_and_counts(page: Page, base_url: str):
    signup(page, base_url)
    close_any_open_guide(page)
    response = page.request.get(f"{base_url}/api/v1/dashboard/setup-chat/builder-contract")
    assert response.ok
    payload = response.json()
    available = [item for item in payload["mechanics"] if item["available"]]
    # Use a real available candle capability, not a copied UI catalogue.
    candle = next(item for item in available if item["key"] == "capability:hammer")
    payload["mechanics"] = [candle]
    page.route(CONTRACT_URL, lambda route: route.fulfill(json=payload))
    # Expose only the shipped serializer, so this test can inspect its request
    # without invoking the separate approval/activation action.
    source = Path("src/ai_market_monitor/static/hm-monitor-test.js").read_text(encoding="utf-8")
    source = source.replace(
        "  function planForServer() {",
        "  window.publicationPlan = planForServer;\n  function planForServer() {",
    )
    page.route(
        "**/static/hm-monitor-test.js*",
        lambda route: route.fulfill(content_type="application/javascript", body=source),
    )
    page.goto(f"{base_url}{MONITOR_PATH}", wait_until="domcontentloaded")
    close_any_open_guide(page)
    page.locator("[data-loading]").wait_for(state="hidden")
    page.locator("[data-open-library]").click()
    expect(page.locator("[data-library-list] .m-lib-item")).to_have_count(1)
    expect(page.locator("[data-library-list] .m-lib-item")).to_have_attribute(
        "data-key", candle["key"]
    )
    expect(page.locator("[data-library-count]")).to_have_text("1 condition to choose from")
    counts = Counter(item["category"] for item in payload["mechanics"])
    assert sum(counts.values()) == 1
    assert page.locator(".m-lib-cat-count").all_text_contents() == ["1", "1"]
    page.locator("[data-library-add]").click()
    expect(page.locator("[data-node][data-kind='rule']")).to_have_count(1)
    for name in (
        "min_body_percent",
        "max_body_percent",
        "wick_ratio",
        "trend_context_required",
        "confirmation_required",
    ):
        expect(page.locator(f"[data-inspector-body] [data-set='{name}']")).to_be_visible()
    chosen = {
        "min_body_percent": "60",
        "max_body_percent": "10",
        "wick_ratio": "4",
        "trend_context_required": "true",
        "confirmation_required": "true",
    }
    for name, value in chosen.items():
        page.locator(f"[data-inspector-body] input[data-set='{name}']").fill(value)
        # Wait for the shipped input debounce before editing the next field.
        page.wait_for_function(
            "([name, value]) => String(publicationPlan().root.children[0].values[name]) === value",
            arg=[name, value],
        )
    for name, value in (("comparator", "is_true"), ("timeframe", "15m")):
        control = page.locator(f"[data-inspector-body] [data-set='{name}'][data-value='{value}']")
        if control.get_attribute("aria-pressed") != "true":
            control.click()
    page.locator("[data-node='alert']").click()
    page.locator("[data-inspector-body] [data-channel='web']").click()
    expect(page.locator("[data-saved-pill]")).to_have_attribute("data-state", "saved")
    page.reload(wait_until="domcontentloaded")
    page.locator("[data-loading]").wait_for(state="hidden")
    close_any_open_guide(page)
    page.locator("[data-node][data-kind='rule']").click()
    for name, value in chosen.items():
        expect(page.locator(f"[data-inspector-body] input[data-set='{name}']")).to_have_value(value)

    # Actual UI values -> actual request schema -> actual deterministic compiler.
    plan = CanvasPlan.model_validate(page.evaluate("() => publicationPlan()"))
    ast = build_condition_ast(plan.root, source_turn_id="synthetic-browser", settings=Settings())
    definition = compile_strategy_draft_v2(StrategyDraftV2(condition_ast=ast))
    expected = {
        "min_body_percent": 60,
        "max_body_percent": 10,
        "wick_ratio": 4,
        "trend_context_required": True,
        "confirmation_required": True,
    }
    for name, value in expected.items():
        assert definition.conditions.children[0].left.parameters[name] == value

    # Save a draft via the authenticated strategy API. Approval and activation
    # are separate gates and are not needed to prove parameter persistence.
    saved = page.evaluate(
        """async definition => {
      const response = await fetch('/api/v1/dashboard/strategies', {
        method: 'POST',
        headers: {'Content-Type': 'application/json',
                  'X-CSRF-Token': document.body.dataset.csrfToken || ''},
        body: JSON.stringify({definition}),
      });
      return {status: response.status, body: await response.json()};
    }""",
        definition.model_dump(mode="json"),
    )
    assert saved["status"] == 201, saved
    strategy_id = saved["body"]["strategy"]["id"]
    response = page.request.get(f"{base_url}/api/v1/dashboard/strategies/{strategy_id}/versions")
    assert response.ok
    stored = StrategyDefinition.model_validate(response.json()["items"][0]["schema_json"])
    operand = stored.conditions.children[0].left
    for name, value in expected.items():
        assert operand.parameters[name] == value
    assert isinstance(StrategyRuleEngine._candle_pattern(operand, candle_sets()["15m"]), bool)


@pytest.mark.deliberate_console_errors("Failed to load resource", "503")
def test_current_builder_failure_cannot_create_a_fallback_condition(page: Page, base_url: str):
    signup(page, base_url)
    close_any_open_guide(page)
    page.route(CONTRACT_URL, lambda route: route.fulfill(status=503, json={}))
    page.goto(f"{base_url}{MONITOR_PATH}", wait_until="domcontentloaded")
    close_any_open_guide(page)
    expect(page.locator("[data-contract-error]")).to_be_visible()
    expect(page.locator("[data-node][data-kind='rule']")).to_have_count(0)
    expect(page.locator("[data-library-list] .m-lib-item")).to_have_count(0)
    page.locator("[data-open-library]").click()
    expect(page.locator("[data-library]")).not_to_be_visible()
