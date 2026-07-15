from __future__ import annotations

import json
import re
import time
from pathlib import Path

from conftest import (
    assert_no_raw_traceback,
    seed_alert_proof,
    seed_setup_observability,
    seed_sharia_screened_market,
    seed_telegram_connection,
    signup,
    unique_email,
)
from playwright.sync_api import Page, expect

from tests.factories import load_strategy

EXECUTABLE_PROMPT = {
    "goal": "Find coins where RSI crosses back above 30 on 15m.",
    "must": "Volume is at least 1.5x average and price is above EMA 200.",
    "universe": "Binance USDT spot pairs.",
    "timeframe": "15m trigger timeframe.",
}


def _visual_chat_payload(status: str, *, can_approve: bool) -> dict:
    draft_strategy = load_strategy().model_dump(mode="json")
    draft_strategy["name"] = "15m breakout monitor"
    lint = [] if can_approve else [
        {
            "code": "missing_threshold",
            "severity": "critical",
            "message": "Define the breakout lookback before approval.",
        }
    ]
    summary = (
        "HilalMarkets will watch Binance USDT spot pairs on 15m. A close above the prior "
        "20-candle high is the primary trigger; volume is a required confirmation."
    )
    return {
        "id": "11111111-1111-4111-8111-111111111111",
        "status": status,
        "title": "15m breakout monitor",
        "original_idea": "Find a breakout with strong volume on 15m Binance spot.",
        "messages": [
            {
                "id": "22222222-2222-4222-8222-222222222222",
                "role": "assistant",
                "message_type": "welcome",
                "content": "Tell me what you want to monitor.",
                "payload": {},
                "client_message_id": None,
                "created_at": "2026-07-10T10:00:00Z",
            },
            {
                "id": "33333333-3333-4333-8333-333333333333",
                "role": "user",
                "message_type": "text",
                "content": "Find a breakout with strong volume on 15m Binance spot.",
                "payload": {},
                "client_message_id": "visual-user-message",
                "created_at": "2026-07-10T10:00:01Z",
            },
            {
                "id": "44444444-4444-4444-8444-444444444444",
                "role": "assistant",
                "message_type": "translation",
                "content": (
                    "Ready to review." if can_approve
                    else "I can’t approve this yet. Define the breakout lookback."
                ),
                "payload": {
                    "understanding_summary": summary,
                    "suggestions": ["Add candle-close confirmation"],
                },
                "client_message_id": None,
                "created_at": "2026-07-10T10:00:02Z",
            },
        ],
        "draft_strategy": draft_strategy,
        "schema_hash": "a" * 64,
        "translation_sheet": {
            "summary_paragraph": summary,
            "original_idea": "Find a breakout with strong volume on 15m Binance spot.",
            "monitor_name": "15m breakout monitor",
            "exchange": "binance",
            "market_type": "spot",
            "direction": "both",
            "symbols_watchlist": [],
            "quote_currencies": ["USDT"],
            "timeframes": ["15m"],
            "alert_timing": {"trigger_mode": "candle_close"},
            "delivery_channels": ["telegram"],
            "conditions": [
                {
                    "key": "breakout_trigger",
                    "name": "Close above previous 20-candle high",
                    "required": True,
                    "role": "primary_trigger",
                    "timeframe": "15m",
                    "operator": "is_true",
                },
                {
                    "key": "volume_suggestion",
                    "name": "Volume at least 1.5x average",
                    "required": True,
                    "role": "required_confirmation",
                    "timeframe": "15m",
                    "operator": "gte",
                },
            ],
            "assumptions": ["All eligible Binance USDT spot pairs."],
            "unsupported_conditions": [],
            "approval_required": True,
            "execution": "No automatic trade execution. Deterministic monitoring only.",
        },
        "lint_warnings": lint,
        "rule_confidence": [],
        "assumptions": ["All eligible Binance USDT spot pairs."],
        "ambiguities": [],
        "unsupported_conditions": [],
        "can_approve": can_approve,
        "approved_strategy_id": None,
        "approved_strategy_version_id": None,
        "next_url": None,
        "updated_at": "2026-07-10T10:00:02Z",
    }


def test_hilalmarkets_landing_and_auth_visual_qa(
    page: Page,
    base_url: str,
    repo_root: Path,
) -> None:
    output = repo_root / "reports" / "playwright" / "visual-qa"
    output.mkdir(parents=True, exist_ok=True)

    page.goto(base_url, wait_until="domcontentloaded")
    expect(page.locator(".public-nav .logo")).to_contain_text("HilalMarkets")
    expect(page.locator(".landing-hero h1")).to_contain_text(
        "Halal-conscious crypto monitoring"
    )
    expect(page.locator(".feature-bento")).to_contain_text("Evidence Passports")
    assert "TODO_" not in page.content()
    page.evaluate(
        """async () => {
            document.documentElement.style.scrollBehavior = 'auto';
            for (const element of document.querySelectorAll('.reveal')) {
                element.scrollIntoView({block: 'center', behavior: 'instant'});
                await new Promise((resolve) => setTimeout(resolve, 70));
            }
            window.scrollTo(0, 0);
            document.documentElement.style.removeProperty('scroll-behavior');
        }"""
    )
    expect(page.locator(".reveal:not(.is-visible)")).to_have_count(0)
    for width, height in ((1440, 1000), (1024, 900), (768, 900)):
        page.set_viewport_size({"width": width, "height": height})
        page.screenshot(
            path=str(output / f"hilalmarkets-landing-{width}.png"),
            full_page=True,
        )

    page.set_viewport_size({"width": 360, "height": 800})
    page.locator("[data-public-menu]").click()
    expect(page.locator(".public-links")).to_be_visible()
    page.screenshot(path=str(output / "hilalmarkets-landing-360.png"), full_page=True)

    page.keyboard.press("Tab")
    focused = page.evaluate("document.activeElement && document.activeElement.tagName")
    assert focused in {"A", "BUTTON", "INPUT"}

    page.emulate_media(reduced_motion="reduce")
    reduced_motion = page.evaluate(
        "window.matchMedia('(prefers-reduced-motion: reduce)').matches"
    )
    assert reduced_motion is True

    page.goto(f"{base_url}/signup", wait_until="domcontentloaded")
    expect(page.locator(".auth-shell")).to_be_visible()
    expect(page.locator(".auth-form-wrap")).to_be_visible()
    expect(page.get_by_test_id("signup-form")).to_be_visible()
    assert "placeholder-note" not in page.content()
    page.screenshot(path=str(output / "hilalmarkets-auth-mobile-360.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.screenshot(path=str(output / "hilalmarkets-auth-desktop.png"), full_page=True)
    assert_no_raw_traceback(page)


def test_dashboard_loads_after_signup_and_navigation(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("dashboard-load"))

    expect(page.get_by_test_id("dashboard-root")).to_be_attached()
    expect(page.get_by_test_id("dashboard-nav")).to_be_visible()
    page.locator(".sidebar-create-quick").click()
    page.wait_for_url(re.compile(r".*/dashboard/strategies/new.*"), timeout=10_000)
    expect(page.get_by_test_id("ai-setup-chat")).to_be_visible()
    expect(page.locator("[data-ai-chat-input]")).to_be_visible()
    expect(page.locator(".ai-chat-start-card.scanner")).to_be_visible()
    expect(page.locator(".ai-chat-start-card.monitor")).to_be_visible()
    assert page.locator('a[href="/dashboard/scan-now"]').count() == 0
    assert_no_raw_traceback(page)


def test_screened_market_passport_and_mobile_visual_qa(
    page: Page,
    base_url: str,
    browser_app,
) -> None:
    email = signup(page, base_url, unique_email("screened-market"))
    seed_sharia_screened_market(browser_app.database_url, email)
    visual_dir = Path("reports/visual-qa/sharia-first")
    visual_dir.mkdir(parents=True, exist_ok=True)

    page.goto(f"{base_url}/dashboard/market?view=opportunities")
    expect(page.get_by_role("heading", name="Find opportunities inside a screened market."))\
        .to_be_visible()
    card = page.locator(".opportunity-card").first
    expect(card).to_be_visible()
    expect(card).to_contain_text("SOL/USDT")
    expect(card).to_contain_text("Eligible")
    expect(card).to_contain_text("80%")
    expect(card).to_contain_text("SOL Browser Watchlist")
    page.screenshot(
        path=str(visual_dir / "screened-market-desktop.png"),
        full_page=True,
    )

    card.get_by_role("link", name="View evidence").click()
    expect(page.get_by_text("SHARIA EVIDENCE PASSPORT")).to_be_visible()
    expect(page.get_by_text("Official browser-test disclosure")).to_be_visible()
    expect(page.get_by_text("HilalMarkets AI does not issue religious rulings.")).to_be_visible()
    page.screenshot(
        path=str(visual_dir / "sharia-evidence-passport-desktop.png"),
        full_page=True,
    )

    page.goto(f"{base_url}/dashboard/market?view=opportunities")
    page.set_viewport_size({"width": 390, "height": 844})
    expect(card).to_be_visible()
    assert card.bounding_box()["width"] <= 390
    page.screenshot(
        path=str(visual_dir / "screened-market-mobile-390.png"),
        full_page=True,
    )
    assert_no_raw_traceback(page)


def test_strategy_prompt_to_coverage_preview_opens_board(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("prompt-coverage"))
    _open_builder(page, base_url)
    input_box = page.locator("[data-ai-chat-input]")
    input_box.fill("Find bullish breakouts with strong volume on 15m Binance spot.")
    page.locator("[data-ai-chat-send]").click()
    expect(page.locator("[data-ai-chat-suggestions] .ai-chat-chip").first).to_be_visible(
        timeout=20_000
    )
    expect(page.locator("[data-ai-chat-messages]")).to_contain_text(
        re.compile("measurable definition|breakout", re.I)
    )
    expect(page.locator("[data-ai-preview-empty]")).to_be_visible()
    assert_no_raw_traceback(page)


def test_ai_setup_chat_mobile_layout(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("ai-chat-mobile"))
    page.set_viewport_size({"width": 390, "height": 844})
    _open_builder(page, base_url)
    chat_panel = page.locator(".ai-chat-panel")
    preview_panel = page.locator(".ai-preview-panel")
    expect(chat_panel).to_be_visible()
    expect(preview_panel).to_be_hidden()
    expect(page.locator("[data-ai-chat-input]")).to_be_visible()
    page.locator("[data-ai-open-canvas]").click()
    expect(page.locator("[data-ai-setup-chat]")).to_have_class(re.compile("canvas-open"))
    expect(page.get_by_test_id("strategy-builder-root")).to_be_visible()
    page.locator("[data-ai-return-chat]").click()
    expect(page.locator("[data-ai-setup-chat]")).not_to_have_class(re.compile("canvas-open"))
    assert_no_raw_traceback(page)


def test_visual_canvas_is_secondary_to_ai_chat(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("chat-canvas"))
    _open_builder(page, base_url)
    page.locator("[data-ai-open-canvas]").click()
    expect(page.locator("[data-ai-setup-chat]")).to_have_class(re.compile("canvas-open"))
    expect(page.get_by_test_id("strategy-builder-root")).to_be_visible()
    expect(page.get_by_test_id("strategy-canvas-panel")).to_be_visible(timeout=10_000)
    page.locator("[data-ai-return-chat]").click()
    expect(page.locator("[data-ai-setup-chat]")).not_to_have_class(re.compile("canvas-open"))
    assert_no_raw_traceback(page)


def test_ai_setup_chat_optimistic_retry_and_option_selection(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("ai-chat-optimistic"))
    _open_builder(page, base_url)
    request_ids: list[str] = []
    held_routes = []
    attempts = 0

    def intercept(route) -> None:
        nonlocal attempts
        attempts += 1
        request_ids.append(json.loads(route.request.post_data)["client_message_id"])
        if attempts == 1:
            route.fulfill(
                status=200,
                content_type="text/plain",
                body="temporary malformed response",
            )
            return
        if attempts == 3:
            held_routes.append(route)
            return
        time.sleep(0.45)
        route.continue_()

    page.route("**/api/v1/dashboard/setup-chat/sessions/*/messages", intercept)
    text = "Find bullish breakouts with strong volume on 15m Binance spot."
    page.locator("[data-ai-chat-input]").fill(text)
    page.locator("[data-ai-chat-send]").click()
    expect(page.locator(".ai-chat-message.user.failed")).to_contain_text(
        text, timeout=10_000
    )
    expect(page.locator(".ai-chat-message.user.failed .ai-chat-bubble")).to_have_css(
        "color", "rgb(255, 255, 255)"
    )
    expect(page.locator("[data-ai-chat-input]")).to_have_value("")
    page.locator("[data-ai-chat-retry]").click()
    expect(page.locator("[data-ai-chat-suggestions] .ai-chat-chip").first).to_be_visible(
        timeout=20_000
    )
    assert request_ids[0] == request_ids[1]
    assert page.locator(".ai-chat-message.user", has_text=text).count() == 1

    chip = page.locator("[data-ai-chat-suggestions] .ai-chat-chip").first
    label = chip.locator("strong").inner_text()
    chip.click()
    expect(page.locator(".ai-chat-message.user", has_text=label)).to_be_visible()
    expect(chip).to_be_disabled()
    expect(chip).to_have_class(re.compile("selected"))
    expect(page.locator("[data-ai-chat-input]")).to_have_value("")
    held_routes[0].continue_()
    assert_no_raw_traceback(page)


def test_ai_setup_chat_visual_qa_states(
    page: Page,
    base_url: str,
    repo_root: Path,
) -> None:
    signup(page, base_url, unique_email("ai-chat-visual-qa"))
    _open_builder(page, base_url)
    output = repo_root / "reports" / "playwright" / "visual-qa"
    output.mkdir(parents=True, exist_ok=True)
    sidebar_box = page.locator("[data-hilal-sidebar]").bounding_box()
    notification_box = page.locator(".topbar-right > .btn").first.bounding_box()
    create_plan_box = page.locator(".topbar-right > .sidebar-create-quick").bounding_box()
    assert sidebar_box is not None and sidebar_box["x"] <= 1
    assert notification_box is not None and notification_box["width"] <= 60
    assert create_plan_box is not None and create_plan_box["width"] <= 220
    expect(page.locator(".topbar-right > .sidebar-create-quick")).to_contain_text(
        "New Watchlist"
    )
    page.screenshot(path=str(output / "ai-setup-chat-desktop.png"), full_page=True)

    prompt = "Find a breakout with strong volume on 15m Binance spot."
    page.locator("[data-ai-chat-input]").fill(prompt)
    page.locator("[data-ai-chat-send]").click()
    expect(page.locator(".ai-chat-chip").first).to_be_visible(timeout=20_000)
    page.screenshot(path=str(output / "ai-setup-chat-option-chips.png"), full_page=True)

    blocked = _visual_chat_payload("needs_clarification", can_approve=False)
    ready = _visual_chat_payload("ready_for_approval", can_approve=True)
    replies = iter((blocked, ready))

    def visual_response(route) -> None:
        try:
            payload = next(replies)
        except StopIteration:
            payload = ready
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.unroute("**/api/v1/dashboard/setup-chat/sessions/*/messages")
    page.route("**/api/v1/dashboard/setup-chat/sessions/*/messages", visual_response)
    page.locator("[data-ai-chat-input]").fill("Use measurable rules.")
    page.locator("[data-ai-chat-send]").click()
    expect(page.locator(".ai-warning.critical")).to_be_visible(timeout=10_000)
    expect(page.locator(".ai-refusal-reason")).to_have_count(1)
    expect(page.locator(".ai-refusal-reason")).to_contain_text(
        "Define the breakout lookback before approval."
    )
    expect(page.locator("[data-ai-chat-approve]")).to_be_disabled()
    page.screenshot(
        path=str(output / "ai-setup-chat-lint-approval-disabled.png"), full_page=True
    )

    page.locator("[data-ai-chat-input]").fill("Apply: Add candle-close confirmation")
    page.locator("[data-ai-chat-send]").click()
    expect(page.locator(".ai-improvement-list button").first).to_be_visible(timeout=10_000)
    expect(page.locator("[data-ai-chat-approve]")).to_be_enabled()
    page.screenshot(
        path=str(output / "ai-setup-chat-translation-suggestions-ready.png"),
        full_page=True,
    )

    page.locator("[data-ai-open-canvas]").click()
    expect(page.locator("[data-ai-setup-chat]")).to_have_class(re.compile("canvas-open"))
    expect(page.get_by_test_id("strategy-canvas-panel")).to_be_visible(timeout=10_000)
    expect(page.locator("#builder-monitor-node-name")).to_contain_text(
        "15m breakout monitor"
    )
    page.screenshot(
        path=str(output / "ai-setup-chat-expanded-canvas-minimized-chat.png"),
        full_page=True,
    )
    page.locator("[data-ai-return-chat]").click()

    page.set_viewport_size({"width": 390, "height": 844})
    if not page.locator("body").evaluate("node => node.classList.contains('sidebar-collapsed')"):
        page.locator("[data-sidebar-toggle]").click()
    page.screenshot(path=str(output / "ai-setup-chat-mobile-390.png"), full_page=True)
    assert_no_raw_traceback(page)


def test_provider_required_prompt_blocks_activation(page: Page, base_url: str, browser_app) -> None:
    email = signup(page, base_url, unique_email("provider-required"))
    seed_telegram_connection(browser_app.database_url, email)
    _create_strategy_from_prompt(
        page,
        base_url,
        {
            "goal": "Only alert if BTC is above EMA 200 on 1h.",
            "must": "Open interest is rising.",
            "universe": "BTC/USDT on Binance spot.",
            "timeframe": "1h.",
        },
    )
    expect(page.get_by_test_id("strategy-canvas-panel")).to_be_visible(timeout=20_000)
    page.get_by_test_id("strategy-validate").click()
    expect(page.locator("#builder-validation-status")).to_contain_text(
        re.compile("blocked|provider|fix|critical|condition", re.I),
        timeout=20_000,
    )
    publish = page.get_by_test_id("strategy-publish")
    if publish.is_enabled():
        publish.click()
        expect(page.get_by_test_id("builder-action-status")).to_contain_text(
            re.compile("blocked|provider|unsupported|fix|validation", re.I),
            timeout=20_000,
        )
    assert "/dashboard/monitors" not in page.url

    page.goto(f"{base_url}/dashboard/strategies", wait_until="domcontentloaded")
    statuses = [
        text.strip().lower() for text in page.get_by_test_id("monitor-status").all_inner_texts()
    ]
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


def test_approve_and_publish_executable_monitor(
    page: Page,
    base_url: str,
    browser_app,
    repo_root,
) -> None:
    email = signup(page, base_url, unique_email("publish-monitor"))
    seed_telegram_connection(browser_app.database_url, email)
    page.reload(wait_until="domcontentloaded")
    strategy_id = _create_executable_board(page, base_url)
    page.get_by_test_id("strategy-validate").click()
    expect(page.locator("#builder-validation-status")).to_contain_text(
        re.compile("ready|passed|conditions", re.I),
        timeout=25_000,
    )
    page.goto(
        f"{base_url}/dashboard/strategies/{strategy_id}/verify",
        wait_until="domcontentloaded",
    )
    expect(page.locator("[data-verified-content]")).to_be_visible(timeout=20_000)
    output = repo_root / "reports" / "playwright" / "visual-qa"
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / "verified-strategy-workflow-desktop.png"), full_page=True)

    while page.locator("[data-accept-statement]").count():
        count = page.locator("[data-accept-statement]").count()
        page.locator("[data-accept-statement]").first.click()
        expect(page.locator("[data-accept-statement]")).to_have_count(count - 1, timeout=15_000)
    page.locator("[data-approve-interpretation]").click()
    expect(page.locator("[data-interpretation-state]")).to_contain_text(
        "approved", timeout=15_000
    )
    expect(page.locator("[data-approve-version]")).to_be_enabled()
    page.locator("[data-approve-version]").click()
    expect(page.locator("[data-verified-notice]")).to_contain_text(
        re.compile("no verification blocker", re.I), timeout=15_000
    )

    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(output / "verified-strategy-workflow-mobile-390.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("[data-activate-version]").click()
    page.wait_for_url(re.compile(r".*/dashboard/lifecycles.*"), timeout=30_000)

    page.goto(f"{base_url}/dashboard/strategies", wait_until="domcontentloaded")
    row = page.get_by_test_id("monitor-row").first
    expect(row).to_be_visible()
    expect(row.get_by_test_id("monitor-status")).to_contain_text(re.compile("active", re.I))
    expect(
        row.get_by_role("button", name=re.compile(r"^(Pause|Resume)$", re.I))
    ).to_be_visible()
    page.screenshot(
        path=str(output / "hilalmarkets-watch-plans-desktop.png"),
        full_page=True,
    )
    assert_no_raw_traceback(page)


def test_legacy_scan_route_redirects_into_chat_scanner(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("scanner-route"))
    page.goto(f"{base_url}/dashboard/scan-now", wait_until="domcontentloaded")
    page.wait_for_url(re.compile(r".*/dashboard/strategies/new\?mode=scanner"), timeout=10_000)
    expect(page.get_by_test_id("ai-setup-chat")).to_be_visible()
    expect(page.locator("[data-ai-chat-messages]")).to_contain_text(
        "Scanner is ready", timeout=10_000
    )
    assert_no_raw_traceback(page)


def test_seeded_proof_receipt_visible_without_ai_claims(
    page: Page,
    base_url: str,
    browser_app,
    repo_root,
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
    page.goto(f"{base_url}/dashboard/alerts/{alert_id}/proof", wait_until="domcontentloaded")
    expect(page.get_by_text("Immutable monitoring receipt")).to_be_visible(timeout=10_000)
    expect(page.get_by_text("Integrity verified")).to_be_visible()
    output = repo_root / "reports" / "playwright" / "visual-qa"
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / "immutable-alert-proof-desktop.png"), full_page=True)
    assert_no_raw_traceback(page)
    assert "15m" in text
    assert "rsi_recovery" in text
    assert "passed" in text
    assert "market_data_timestamp" in text
    assert "guaranteed" not in text.lower()
    assert "profit" not in text.lower()
    assert_no_raw_traceback(page)


def test_monitor_and_lifecycle_smoke(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("monitor-lifecycle-smoke"))
    page.goto(f"{base_url}/dashboard/strategies", wait_until="domcontentloaded")
    expect(page.locator("body")).to_contain_text("Watchlists")
    expect(page.locator("body")).not_to_contain_text("Alert Quality Inbox")
    page.goto(f"{base_url}/dashboard/lifecycles", wait_until="domcontentloaded")
    expect(page.locator("body")).to_contain_text("Follow every market journey.")
    expect(page.locator("body")).not_to_contain_text("Traceback")
    assert_no_raw_traceback(page)


def test_setup_observability_desktop_mobile_and_visual_qa(
    page: Page,
    base_url: str,
    browser_app,
    repo_root: Path,
) -> None:
    email = signup(page, base_url, unique_email("setup-observability"))
    seeded = seed_setup_observability(browser_app.database_url, email)
    visual_dir = repo_root / "reports" / "playwright" / "visual-qa"
    visual_dir.mkdir(parents=True, exist_ok=True)

    page.goto(f"{base_url}/dashboard/lifecycles", wait_until="domcontentloaded")
    expect(page.get_by_text("Live Setup Readiness Radar")).to_be_visible()
    expect(page.locator("[data-radar-list] .readiness-candidate")).to_have_count(4, timeout=15_000)
    expect(page.locator("[data-health-list]")).to_contain_text("Degraded")
    expect(page.locator("[data-health-list]")).to_contain_text("Too Strict")
    expect(page.locator("[data-bottleneck-list]")).to_contain_text("RVOL above 1.50x")
    page.screenshot(path=str(visual_dir / "setup-observability-desktop.png"), full_page=True)
    page.locator(".state-forming").screenshot(path=str(visual_dir / "forming-candidate.png"))
    page.locator(".state-near_miss").screenshot(path=str(visual_dir / "near-miss-candidate.png"))
    page.locator(".monitor-health-card").screenshot(
        path=str(visual_dir / "degraded-too-strict-monitor.png")
    )
    page.locator(".bottleneck-intelligence").screenshot(
        path=str(visual_dir / "bottleneck-intelligence.png")
    )

    page.locator("[data-monitor-filter-trigger]").click()
    expect(page.locator("[data-monitor-filter-menu]")).to_be_visible()
    page.locator(f'[data-monitor-option="{seeded["strategy_id"]}"]').click()
    page.wait_for_url(
        re.compile(
            rf".*/dashboard/activity\?tab=forming&monitor={seeded['strategy_id']}"
        )
    )
    expect(page.locator("[data-monitor-filter-label]")).to_contain_text(
        "SOL Readiness Monitor"
    )
    expect(page.locator("[data-radar-list] .readiness-candidate")).to_have_count(4)

    page.locator(f'[data-candidate-investigate="{seeded["setup_id"]}"]').click()
    expect(page.locator("[data-observability-drawer]")).to_have_class(
        re.compile("open"), timeout=10_000
    )
    expect(page.locator("[data-observability-drawer-content]")).to_contain_text(
        "RVOL above 1.50x"
    )
    page.locator("[data-observability-drawer]").screenshot(
        path=str(visual_dir / "candidate-detail-timeline.png")
    )
    page.locator("[data-observability-drawer-close]").first.click()

    page.locator("[data-radar-state]").select_option("provider_data_error")
    expect(page.locator("[data-radar-list] .readiness-candidate")).to_have_count(1)
    page.locator("[data-radar-list]").screenshot(path=str(visual_dir / "provider-error-state.png"))
    page.locator("[data-radar-state]").select_option("invalidated")
    expect(page.locator("[data-radar-list]")).to_contain_text("No readiness evidence yet")
    page.locator("[data-radar-list]").screenshot(path=str(visual_dir / "empty-radar-state.png"))

    page.locator("[data-radar-state]").select_option("")
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator("[data-radar-list] .readiness-candidate")).to_have_count(4)
    page.screenshot(path=str(visual_dir / "setup-observability-mobile-390.png"), full_page=True)
    page.emulate_media(reduced_motion="reduce")
    animation_name = page.locator(".observability-live i").evaluate(
        "node => getComputedStyle(node).animationName"
    )
    assert animation_name == "none"
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
    expect(page.get_by_test_id("ai-setup-chat")).to_be_visible(timeout=10_000)


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


def _create_executable_board(page: Page, base_url: str) -> str:
    strategy_id = _create_strategy_from_prompt(page, base_url, EXECUTABLE_PROMPT)
    expect(page.get_by_test_id("strategy-canvas-panel")).to_be_visible(timeout=10_000)
    expect(_first_condition_board_node(page)).to_be_visible(timeout=10_000)
    return strategy_id


def _create_strategy_from_prompt(
    page: Page,
    base_url: str,
    prompt_parts: dict[str, str],
) -> str:
    interpreted = page.request.post(
        f"{base_url}/api/v1/dashboard/strategies/interpret",
        data={
            "prompt_parts": prompt_parts,
            "exchange": "binance",
            "quote_currency": "USDT",
            "timeframe": "15m",
            "trigger_mode": "candle_close",
        },
    )
    assert interpreted.ok, interpreted.text()
    preview = interpreted.json()
    created = page.request.post(
        f"{base_url}/api/v1/dashboard/strategies",
        data={
            "definition": preview["strategy"],
            "source_text": "\n".join(prompt_parts.values()),
            "interpreter": preview["interpreter"],
            "assumptions": preview["assumptions"],
            "ambiguities": preview["ambiguities"],
            "unsupported_conditions": preview["unsupported_conditions"],
        },
    )
    assert created.ok, created.text()
    strategy_id = created.json()["strategy"]["id"]
    page.goto(
        f"{base_url}/dashboard/strategies/{strategy_id}/builder",
        wait_until="domcontentloaded",
    )
    return strategy_id


def _open_first_condition_drawer(page: Page) -> None:
    _first_condition_board_node(page).locator("[data-board-edit]").click()
    expect(page.get_by_test_id("condition-drawer")).to_be_visible(timeout=10_000)
    advanced = page.locator("details.drawer-advanced")
    if not advanced.evaluate("node => node.open"):
        advanced.locator("summary").click()
    expect(page.get_by_test_id("condition-source-fragment")).to_be_attached()


def _first_condition_board_node(page: Page):
    return page.locator('[data-testid="strategy-board-node"][data-board-action="condition"]').first
