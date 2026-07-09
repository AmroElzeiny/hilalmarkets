from __future__ import annotations

import json
import re

from playwright.sync_api import Page, expect

from conftest import (
    assert_no_raw_traceback,
    seed_alert_proof,
    seed_telegram_connection,
    signup,
    unique_email,
)


EXECUTABLE_PROMPT = {
    "goal": "Find coins where RSI crosses back above 30 on 15m.",
    "must": "Volume is at least 1.5x average and price is above EMA 200.",
    "universe": "Binance USDT spot pairs.",
    "timeframe": "15m trigger timeframe.",
}


def test_dashboard_loads_after_signup_and_navigation(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("dashboard-load"))

    expect(page.get_by_test_id("dashboard-root")).to_be_attached()
    expect(page.get_by_test_id("dashboard-nav")).to_be_visible()
    page.locator(".sidebar-create-quick").click()
    page.wait_for_url(re.compile(r".*/dashboard/strategies/new.*"), timeout=10_000)
    expect(page.get_by_test_id("strategy-builder-entry")).to_be_visible()
    assert_no_raw_traceback(page)


def test_strategy_prompt_to_coverage_preview_opens_board(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("prompt-coverage"))
    _open_builder(page, base_url)
    _submit_executable_prompt(page)

    panel = page.get_by_test_id("prompt-coverage-panel")
    expect(panel).to_be_visible()
    expect(panel).to_contain_text(re.compile("RSI", re.I))
    expect(panel).to_contain_text(re.compile("volume", re.I))
    expect(panel).to_contain_text(re.compile("EMA", re.I))
    expect(page.get_by_test_id("prompt-coverage-score")).to_be_visible()
    expect(page.get_by_test_id("interpreted-rule-card").first).to_be_visible()
    expect(panel).not_to_contain_text("No executable rule recognized")
    expect(panel).not_to_contain_text("Needs clarification")

    open_button = page.get_by_test_id("open-strategy-board")
    expect(open_button).to_be_enabled()
    open_button.click()
    expect(page.get_by_test_id("strategy-canvas-panel")).to_be_visible()
    expect(page.get_by_test_id("strategy-board")).to_be_visible()
    expect(_first_condition_board_node(page)).to_be_visible()
    assert_no_raw_traceback(page)


def test_provider_required_prompt_blocks_activation(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("provider-required"))
    _open_builder(page, base_url)
    _choose_describe_path(page)
    page.get_by_test_id("strategy-prompt-goal").fill(
        "Only alert if BTC is above EMA 200 on 1h."
    )
    _add_prompt_section(page, "must")
    page.get_by_test_id("strategy-prompt-must").fill("Open interest is rising.")
    _add_prompt_section(page, "universe")
    page.get_by_test_id("strategy-prompt-universe").fill("BTC/USDT on Binance spot.")
    _add_prompt_section(page, "timeframe")
    page.get_by_test_id("strategy-prompt-timeframe").fill("1h.")
    page.get_by_test_id("strategy-interpret-submit").click()

    panel = page.get_by_test_id("prompt-coverage-panel")
    expect(panel).to_be_visible(timeout=20_000)
    expect(panel).to_contain_text(re.compile("open interest", re.I))
    expect(panel).to_contain_text(re.compile("provider|unsupported|clarification", re.I))

    open_button = page.get_by_test_id("open-strategy-board")
    if open_button.is_enabled():
        open_button.click()
        expect(page.get_by_test_id("strategy-canvas-panel")).to_be_visible()
        expect(page.get_by_text(re.compile("Provider required|provider", re.I)).first).to_be_visible()
        page.get_by_test_id("strategy-publish").click()
        expect(page.get_by_test_id("builder-action-status")).to_contain_text(
            re.compile("blocked|provider|fix|validation", re.I),
            timeout=20_000,
        )
        assert "/dashboard/monitors" not in page.url
    else:
        expect(page.get_by_test_id("critical-activation-blocker")).to_be_visible()

    page.goto(f"{base_url}/dashboard/strategies/new#monitors", wait_until="domcontentloaded")
    statuses = [text.strip().lower() for text in page.get_by_test_id("monitor-status").all_inner_texts()]
    assert "active" not in statuses
    assert_no_raw_traceback(page)


def test_strategy_board_preserves_metadata_after_edit_save_reload(
    page: Page,
    base_url: str,
) -> None:
    signup(page, base_url, unique_email("metadata-preservation"))
    _create_executable_board(page, base_url)

    _open_first_condition_drawer(page)
    original_source = page.get_by_test_id("condition-source-fragment").input_value()
    original_confidence = page.get_by_test_id("condition-confidence").input_value()
    original_provider_required = page.get_by_test_id("condition-provider-required").input_value()
    original_availability = page.get_by_test_id("condition-availability").input_value()
    assert original_source, "Expected condition source_fragment metadata to be present."
    assert original_availability, "Expected condition availability metadata to be present."

    label = page.get_by_test_id("condition-label")
    updated_label = f"{label.input_value()} E2E"
    label.fill(updated_label)
    page.get_by_test_id("condition-save").click()
    page.get_by_test_id("strategy-save-draft").click()
    expect(page.get_by_test_id("builder-action-status")).to_contain_text(
        re.compile("Draft saved", re.I),
        timeout=20_000,
    )

    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_test_id("strategy-canvas-panel")).to_be_visible(timeout=15_000)
    expect(page.locator("#builder-json")).not_to_be_visible()
    _open_first_condition_drawer(page)
    expect(page.get_by_test_id("condition-label")).to_have_value(updated_label)
    expect(page.get_by_test_id("condition-source-fragment")).to_have_value(original_source)
    expect(page.get_by_test_id("condition-provider-required")).to_have_value(
        original_provider_required
    )
    expect(page.get_by_test_id("condition-availability")).to_have_value(original_availability)
    if original_confidence:
        expect(page.get_by_test_id("condition-confidence")).to_have_value(original_confidence)
    page.get_by_test_id("condition-save").click()
    page.get_by_test_id("strategy-validate").click()
    expect(page.locator("#builder-validation-status")).to_contain_text(
        re.compile("ready|passed|conditions", re.I),
        timeout=25_000,
    )
    assert_no_raw_traceback(page)


def test_approve_and_publish_executable_monitor(page: Page, base_url: str, browser_app) -> None:
    email = signup(page, base_url, unique_email("publish-monitor"))
    seed_telegram_connection(browser_app.database_url, email)
    page.reload(wait_until="domcontentloaded")
    _create_executable_board(page, base_url)
    page.get_by_test_id("strategy-validate").click()
    expect(page.locator("#builder-validation-status")).to_contain_text(
        re.compile("ready|passed|conditions", re.I),
        timeout=25_000,
    )
    page.get_by_test_id("strategy-publish").click()
    page.wait_for_url(re.compile(r".*/dashboard/strategies/new.*"), timeout=30_000)
    row = page.get_by_test_id("monitor-row").first
    expect(row).to_be_visible()
    expect(row.get_by_test_id("monitor-status")).to_contain_text("active")
    expect(row.get_by_text(re.compile("Pause|Resume", re.I))).to_be_visible()
    assert_no_raw_traceback(page)


def test_quick_scan_finder_prompt_flow(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("quick-scan"))
    page.goto(f"{base_url}/dashboard/scan-now", wait_until="domcontentloaded")
    page.locator('select[name="scan_mode"]').select_option("prompt")
    page.route(
        "**/api/v1/dashboard/scan-now/interpret",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "activation_blocked": False,
                    "approved_schema_hash": "browser-e2e-scan-hash",
                    "strategy": {"strategy_name": "Daily percentage change finder"},
                    "understanding": {
                        "direction": "long",
                        "exchange": "binance",
                        "market_type": "spot",
                        "pair_universe": "USDT quotes",
                        "timeframes": ["1d"],
                        "trigger_mode": "candle_close",
                        "entry_conditions": ["Daily percentage change is at least 5%"],
                        "risk": {"enabled": False},
                    },
                    "required_rules": [{"name": "Daily percentage change >= 5%"}],
                    "optional_rules": [],
                    "ignored_optional_rules": [],
                    "blocking_unsupported_rules": [],
                    "warnings": [],
                    "ambiguities": [],
                    "scan_safety_level": "strict",
                    "light_mode_compatible": True,
                }
            ),
        ),
    )
    page.get_by_test_id("quick-scan-goal").fill("Find coins up 5% today.")
    page.get_by_test_id("quick-scan-must").fill("Daily percentage change is at least 5%.")
    page.get_by_test_id("quick-scan-interpret").click()
    expect(page.get_by_test_id("quick-scan-interpretation")).to_be_visible(timeout=20_000)
    expect(page.get_by_test_id("quick-scan-interpretation")).to_contain_text(
        re.compile("5|percent|today|change", re.I)
    )

    page.get_by_test_id("quick-scan-submit").click()
    result = page.get_by_test_id("quick-scan-result")
    expect(result).to_be_visible()
    expect(result).to_contain_text("PUMP5/USDT", timeout=20_000)
    expect(result).to_contain_text("100%", timeout=20_000)
    expect(result).not_to_contain_text("Traceback")
    assert_no_raw_traceback(page)


def test_seeded_proof_receipt_visible_without_ai_claims(
    page: Page,
    base_url: str,
    browser_app,
) -> None:
    email = signup(page, base_url, unique_email("proof-receipt"))
    alert_id = seed_alert_proof(browser_app.database_url, email)

    page.goto(
        f"{base_url}/api/v1/dashboard/cockpit/alerts/{alert_id}/proof",
        wait_until="domcontentloaded",
    )
    text = page.locator("body").inner_text(timeout=10_000)
    assert "Browser E2E Proof Strategy" in text
    assert "SOL/USDT" in text
    assert "15m" in text
    assert "rsi_recovery" in text
    assert "passed" in text
    assert "market_data_timestamp" in text
    assert "guaranteed" not in text.lower()
    assert "profit" not in text.lower()
    assert_no_raw_traceback(page)


def test_monitor_and_lifecycle_smoke(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("monitor-lifecycle-smoke"))
    page.goto(f"{base_url}/dashboard/strategies/new#monitors", wait_until="domcontentloaded")
    expect(page.locator("body")).to_contain_text("My Monitors")
    expect(page.locator("body")).not_to_contain_text("Alert Quality Inbox")
    page.goto(f"{base_url}/dashboard/lifecycles", wait_until="domcontentloaded")
    expect(page.locator("body")).to_contain_text("One setup. A complete lifecycle.")
    expect(page.locator("body")).not_to_contain_text("Traceback")
    assert_no_raw_traceback(page)


def test_telegram_discord_handoff_links_smoke(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("integrations-smoke"))
    page.goto(f"{base_url}/dashboard/integrations", wait_until="domcontentloaded")
    expect(page.get_by_test_id("integrations-root")).to_be_visible()
    expect(page.get_by_test_id("telegram-integration-card")).to_contain_text("Telegram")
    expect(page.get_by_test_id("discord-integration-card")).to_contain_text("Discord")
    body = page.locator("body").inner_text(timeout=10_000).lower()
    assert "telegram_bot_token" not in body
    assert "discord_bot_token" not in body
    assert "bot token" not in body
    assert_no_raw_traceback(page)


def _open_builder(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/dashboard/strategies/new", wait_until="domcontentloaded")
    expect(page.get_by_test_id("strategy-builder-entry")).to_be_visible(timeout=10_000)


def _submit_executable_prompt(page: Page) -> None:
    _choose_describe_path(page)
    page.get_by_test_id("strategy-prompt-goal").fill(EXECUTABLE_PROMPT["goal"])
    _add_prompt_section(page, "must")
    page.get_by_test_id("strategy-prompt-must").fill(EXECUTABLE_PROMPT["must"])
    _add_prompt_section(page, "universe")
    page.get_by_test_id("strategy-prompt-universe").fill(EXECUTABLE_PROMPT["universe"])
    _add_prompt_section(page, "timeframe")
    page.get_by_test_id("strategy-prompt-timeframe").fill(EXECUTABLE_PROMPT["timeframe"])
    page.get_by_test_id("strategy-interpret-submit").click()
    expect(page.get_by_test_id("prompt-coverage-panel")).to_be_visible(timeout=20_000)


def _choose_describe_path(page: Page) -> None:
    button = page.get_by_test_id("strategy-builder-describe")
    goal = page.get_by_test_id("strategy-prompt-goal")
    expect(button).to_be_visible(timeout=10_000)
    for _ in range(3):
        button.click()
        try:
            expect(goal).to_be_visible(timeout=3_000)
            return
        except AssertionError:
            page.wait_for_timeout(500)
    expect(goal).to_be_visible(timeout=10_000)


def _add_prompt_section(page: Page, key: str) -> None:
    button = page.locator(f'[data-add-prompt-section="{key}"]')
    expect(button).to_be_visible(timeout=10_000)
    button.click()


def _create_executable_board(page: Page, base_url: str) -> None:
    _open_builder(page, base_url)
    _submit_executable_prompt(page)
    open_button = page.get_by_test_id("open-strategy-board")
    expect(open_button).to_be_enabled()
    open_button.click()
    expect(page.get_by_test_id("strategy-canvas-panel")).to_be_visible(timeout=10_000)
    expect(_first_condition_board_node(page)).to_be_visible(timeout=10_000)


def _open_first_condition_drawer(page: Page) -> None:
    _first_condition_board_node(page).locator("[data-board-edit]").click()
    expect(page.get_by_test_id("condition-drawer")).to_be_visible(timeout=10_000)
    advanced = page.locator("details.drawer-advanced")
    if not advanced.evaluate("node => node.open"):
        advanced.locator("summary").click()
    expect(page.get_by_test_id("condition-source-fragment")).to_be_attached()


def _first_condition_board_node(page: Page):
    return page.locator('[data-testid="strategy-board-node"][data-board-action="condition"]').first
