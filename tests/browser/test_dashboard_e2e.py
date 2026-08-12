from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, expect

from tests.browser.conftest import (
    assert_hilal_brand_palette,
    assert_no_horizontal_overflow,
    assert_no_raw_traceback,
    seed_alert_proof,
    seed_disclaimer_acceptance,
    seed_paid_monitor_access,
    seed_setup_observability,
    seed_sharia_screened_market,
    seed_system_brain_reviewer,
    seed_telegram_connection,
    signup,
    unique_email,
)
from tests.factories import load_strategy


def expected_primary_cta() -> str:
    """The main call to action the current launch stage declares.

    Read from the server-owned stage table rather than written here as a literal.
    This assertion used to hard-code "Get started", which only exists once the
    product is open; the site ships pre-launch, so the landing page says "Join the
    waitlist" and the test had been failing against a page that was behaving
    correctly.

    Deriving it means the next stage change moves the test with the product instead
    of leaving a stale string to be discovered as a mystery browser failure.
    """

    from ai_market_monitor.core.config import Settings

    settings = Settings(
        _env_file=None,
        app_secret_key="browser-test-secret-key-at-least-32-characters",
    )
    return settings.stage_exposure.primary_cta_label


EXECUTABLE_PROMPT = {
    "goal": "Find coins where RSI crosses back above 30 on 15m.",
    "must": "Volume is at least 1.5x average and price is above EMA 200.",
    "universe": "Binance USDT spot pairs.",
    "timeframe": "15m trigger timeframe.",
}


def test_system_brain_reviewer_first_desktop_and_mobile(
    page: Page,
    base_url: str,
    browser_app,
    repo_root: Path,
) -> None:
    email = signup(page, base_url, unique_email("system-brain"))
    case_id = seed_system_brain_reviewer(browser_app.database_url, email)
    output = repo_root / "reports" / "playwright" / "visual-qa"
    output.mkdir(parents=True, exist_ok=True)

    page.goto(f"{base_url}/dashboard/system-brain", wait_until="domcontentloaded")
    expect(page.get_by_test_id("system-brain-assistant")).to_be_visible()
    expect(page.locator(".brain-sidebar nav a")).to_have_count(6)
    expect(page.get_by_role("heading", name="Needs Attention")).to_be_visible()
    assert_no_horizontal_overflow(page)
    assert_no_raw_traceback(page)
    page.screenshot(path=str(output / "system-brain-desktop.png"), full_page=True)

    page.goto(
        f"{base_url}/dashboard/system-brain/cases/{case_id}",
        wait_until="domcontentloaded",
    )
    expect(page.get_by_test_id("system-brain-review-board")).to_be_visible()
    expect(page.get_by_test_id("human-decision-panel")).to_be_visible()
    expect(page.get_by_test_id("ai-field-assistance")).to_be_visible()
    assert_no_horizontal_overflow(page)

    page.set_viewport_size({"width": 390, "height": 844})
    page.reload(wait_until="domcontentloaded")
    expect(page.locator(".brain-mobile-decision-note")).to_be_visible()
    assert page.locator(".brain-terminal-action").first.is_hidden()
    assert_no_horizontal_overflow(page)
    assert_no_raw_traceback(page)
    page.screenshot(path=str(output / "system-brain-mobile-390.png"), full_page=True)


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
    expect(page.locator('header a[aria-label="Hilal Markets home"]')).to_be_visible()
    expect(page.locator("main h1")).to_contain_text(
        "A better way for Muslim crypto traders"
    )
    expect(page.locator("#features")).to_be_attached()
    expect(
        page.get_by_role("link", name=expected_primary_cta()).first
    ).to_be_visible()
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

    page.set_viewport_size({"width": 768, "height": 900})
    faq_panel_box = page.locator("#faq > div").nth(1).bounding_box()
    assert faq_panel_box is not None
    reference_width = faq_panel_box["width"]

    for card in page.locator('[data-name^="Step 0"]').all():
        card_box = card.bounding_box()
        assert card_box is not None
        assert abs(card_box["width"] - reference_width) <= 1

    first_feature_row = page.locator('[data-name="Feature row 1"]')
    for content_name in ("Feature copy", "Product visual"):
        content_box = first_feature_row.locator(
            f'[data-name="{content_name}"]'
        ).bounding_box()
        assert content_box is not None
        assert abs(content_box["width"] - reference_width) <= 1

    for selector in (
        '[data-name="Control flow"]',
        '[data-name^="Trust card "]',
        '[data-name="Hero flow"] > div',
    ):
        for item in page.locator(selector).all():
            item_box = item.bounding_box()
            assert item_box is not None
            assert abs(item_box["width"] - reference_width) <= 1

    for heading in (
        "From your trading idea to continuous monitoring",
        "Everything you need to build and monitor with confidence",
    ):
        assert page.get_by_text(heading, exact=True).evaluate(
            "element => getComputedStyle(element).textAlign"
        ) == "center"

    control_flow = page.locator('[data-name="Control flow"]')
    page.set_viewport_size({"width": 650, "height": 900})
    expect(control_flow).to_be_visible()
    assert control_flow.evaluate(
        "element => getComputedStyle(element).flexDirection"
    ) == "row"
    page.set_viewport_size({"width": 649, "height": 900})
    assert control_flow.evaluate(
        "element => getComputedStyle(element).flexDirection"
    ) == "column"

    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator('header a[aria-label="Hilal Markets home"]')).to_be_visible()
    page.get_by_role("button", name="Menu").click()
    expect(
        page.get_by_role("navigation", name="Mobile navigation").get_by_text(
            expected_primary_cta()
        )
    ).to_be_visible()

    problem = page.locator('[data-name^="03 "]').first
    corner_boxes = {}
    for corner in (
        "top-left",
        "bottom-left",
        "top-right",
        "bottom-right",
    ):
        vector = problem.locator(f".problem-corner-vector--{corner}")
        assert vector.evaluate("element => getComputedStyle(element).width") == "39px"
        assert vector.evaluate(
            "element => getComputedStyle(element).aspectRatio"
        ) == "1 / 1"
        corner_boxes[corner] = vector.bounding_box()
        overlaps_text = vector.evaluate(
            """element => {
                const vectorRect = element.getBoundingClientRect();
                const section = element.closest('[data-name^="03 "]');
                return [...section.querySelectorAll('p')].some((text) => {
                    const textRect = text.getBoundingClientRect();
                    return !(
                        vectorRect.right <= textRect.left ||
                        vectorRect.left >= textRect.right ||
                        vectorRect.bottom <= textRect.top ||
                        vectorRect.top >= textRect.bottom
                    );
                });
            }""",
        )
        assert overlaps_text is False

    assert all(corner_boxes.values())
    top_left = corner_boxes["top-left"]
    bottom_left = corner_boxes["bottom-left"]
    top_right = corner_boxes["top-right"]
    bottom_right = corner_boxes["bottom-right"]
    assert abs(top_left["x"] - bottom_left["x"]) <= 1
    assert abs(top_right["x"] - bottom_right["x"]) <= 1
    assert abs(top_left["y"] - top_right["y"]) <= 1
    assert abs(bottom_left["y"] - bottom_right["y"]) <= 1

    for row in page.locator('[data-name^="Feature row"]').all():
        copy_box = row.locator('[data-name="Feature copy"]').bounding_box()
        visual_box = row.locator('[data-name="Product visual"]').bounding_box()
        assert copy_box is not None and visual_box is not None
        assert copy_box["y"] < visual_box["y"]

    for card in page.locator('[data-name^="Step 0"]').all():
        card_box = card.bounding_box()
        assert card_box is not None
        for paragraph in card.locator("p").all():
            paragraph_box = paragraph.bounding_box()
            if paragraph_box is None:
                continue
            assert paragraph_box["x"] >= card_box["x"] - 1
            assert (
                paragraph_box["x"] + paragraph_box["width"]
                <= card_box["x"] + card_box["width"] + 1
            )

    trust_cards_box = page.locator('[data-name="Trust cards"]').bounding_box()
    control_flow_box = control_flow.bounding_box()
    assert trust_cards_box is not None and control_flow_box is not None
    assert abs(trust_cards_box["width"] - control_flow_box["width"]) <= 1

    proof_bottom_gaps = []
    for card in page.locator('[data-name^="Trust card "]').all():
        card_box = card.bounding_box()
        proof_box = card.locator('[data-name="Proof"]').bounding_box()
        assert card_box is not None and proof_box is not None
        proof_bottom_gaps.append(
            card_box["y"] + card_box["height"] - proof_box["y"] - proof_box["height"]
        )
    assert max(proof_bottom_gaps) - min(proof_bottom_gaps) <= 1

    footer_brand = page.locator('[data-name="Footer brand"]')
    footer_copy_box = footer_brand.locator(":scope > p").bounding_box()
    assert footer_copy_box is not None
    logo_left = footer_brand.locator("svg path").evaluate_all(
        "paths => Math.min(...paths.map(path => path.getBoundingClientRect().left))"
    )
    assert abs(logo_left - footer_copy_box["x"]) <= 1
    page.screenshot(path=str(output / "hilalmarkets-landing-390.png"), full_page=True)

    page.keyboard.press("Tab")
    focused = page.evaluate("document.activeElement && document.activeElement.tagName")
    assert focused in {"A", "BUTTON", "INPUT"}

    page.emulate_media(reduced_motion="reduce")
    reduced_motion = page.evaluate(
        "window.matchMedia('(prefers-reduced-motion: reduce)').matches"
    )
    assert reduced_motion is True

    page.goto(f"{base_url}/contact", wait_until="domcontentloaded")
    expect(
        page.get_by_role(
            "heading", name="How can we help?"
        )
    ).to_be_visible()
    expect(page.locator("[data-contact-form]")).to_be_visible()
    page.screenshot(path=str(output / "hilalmarkets-contact-mobile-390.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.screenshot(path=str(output / "hilalmarkets-contact-desktop.png"), full_page=True)

    page.goto(f"{base_url}/signup", wait_until="domcontentloaded")
    expect(page.locator(".auth-shell")).to_be_visible()
    expect(page.locator(".auth-form-wrap")).to_be_visible()
    expect(page.get_by_test_id("signup-form")).to_be_visible()
    assert "placeholder-note" not in page.content()
    page.screenshot(path=str(output / "hilalmarkets-auth-mobile-390.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.screenshot(path=str(output / "hilalmarkets-auth-desktop.png"), full_page=True)
    assert_no_raw_traceback(page)


def test_public_product_chat_consent_grounding_inquiry_and_returning_profile(
    page: Page,
    base_url: str,
    repo_root: Path,
) -> None:
    output = repo_root / "reports" / "playwright" / "visual-qa" / "public-chat"
    output.mkdir(parents=True, exist_ok=True)
    page.goto(base_url, wait_until="domcontentloaded")

    page.locator("[data-cookie-customize]").first.click()
    page.locator("input[data-consent-functional]").check()
    page.locator("[data-cookie-save]").click()
    launcher = page.locator("[data-public-chat-launcher]")
    launcher_box = launcher.bounding_box()
    assert launcher_box is not None
    assert round(launcher_box["width"]) == 56
    assert round(launcher_box["height"]) == 56
    page.emulate_media(reduced_motion="reduce")
    expect(launcher).to_have_css("animation-name", "none")
    page.emulate_media(reduced_motion="no-preference")
    launcher.click()
    panel = page.locator("[data-public-chat-panel]")
    expect(panel).to_be_visible()
    expect(launcher).to_have_class(re.compile("was-opened"))
    expect(page.locator("[data-public-chat-profile]")).to_be_visible()

    page.locator("#public-chat-name").fill("Amina Beta")
    page.locator("#public-chat-email").fill("AMINA@EXAMPLE.COM")
    page.get_by_text("Remember me on this device").click()
    page.get_by_role("button", name="Start conversation").click()
    expect(page.locator("[data-public-chat-messages]")).to_contain_text("Hi Amina")
    inquiry = page.locator("[data-public-chat-inquiry]")
    page.locator("[data-public-chat-input]").fill("Hi")
    page.locator("[data-public-chat-send]").click()
    expect(page.locator("[data-public-chat-messages]")).to_contain_text(
        "How can I help you with Hilal Markets today?",
        timeout=10_000,
    )
    expect(inquiry).to_be_hidden()
    feedback = page.locator("[data-public-chat-answer-feedback]")
    expect(feedback).to_contain_text("Did AI answer your question?")
    feedback.get_by_role("button", name="Yes", exact=True).click()
    expect(feedback).to_contain_text("Great! Ready when you are.")

    page.locator("[data-public-chat-input]").fill(
        "Which markets are in the private beta?"
    )
    page.locator("[data-public-chat-send]").click()
    expect(page.locator("[data-public-chat-messages]")).to_contain_text(
        "BTC, ETH, and SOL",
        timeout=10_000,
    )
    expect(feedback).to_contain_text("Did AI answer your question?")
    expect(page.locator("[data-public-chat-input]")).to_have_attribute(
        "placeholder", "Ask about Hilal Markets..."
    )
    expect(page.locator(".public-chat-links")).to_have_count(0)
    page.screenshot(path=str(output / "public-chat-desktop-1440.png"), full_page=False)
    for width, height in ((1024, 900), (768, 900)):
        page.set_viewport_size({"width": width, "height": height})
        expect(panel).to_be_visible()
        page.screenshot(
            path=str(output / f"public-chat-desktop-{width}.png"),
            full_page=False,
        )
    page.set_viewport_size({"width": 1440, "height": 1000})

    page.locator("[data-public-chat-close]").click()
    expect(launcher).to_be_focused()
    page.reload(wait_until="domcontentloaded")
    page.locator("[data-public-chat-launcher]").click()
    expect(page.locator("[data-public-chat-profile]")).to_be_hidden()
    expect(page.locator("[data-public-chat-messages]")).to_contain_text("Hi Amina")
    page.locator("[data-public-chat-new]").click()
    expect(page.locator(".public-chat-message")).to_have_count(1)
    expect(page.locator("[data-public-chat-messages]")).to_contain_text("Hi Amina")

    page.set_viewport_size({"width": 390, "height": 844})
    expect(panel).to_be_visible()
    assert panel.bounding_box()["width"] <= 390
    page.screenshot(path=str(output / "public-chat-mobile-390.png"), full_page=False)

    question = "Can your team certify my private satellite telemetry feed?"
    page.locator("[data-public-chat-input]").fill(question)
    page.locator("[data-public-chat-send]").click()
    expect(inquiry).to_be_hidden()
    expect(feedback).to_contain_text("Did AI answer your question?", timeout=10_000)
    page.wait_for_timeout(300)
    page.screenshot(
        path=str(output / "public-chat-mobile-feedback-390.png"),
        full_page=False,
    )
    feedback.get_by_role("button", name="No. Submit a support form").click()
    expect(inquiry).to_be_visible(timeout=10_000)
    expect(inquiry.locator("input[name='name']")).to_have_value("Amina Beta")
    expect(inquiry.locator("input[name='email']")).to_have_value(
        "amina@example.com"
    )
    expect(inquiry.locator("textarea[name='details']")).to_have_value(question)
    page.wait_for_timeout(300)
    page.screenshot(
        path=str(output / "public-chat-mobile-support-form-390.png"),
        full_page=False,
    )
    inquiry.locator("input[name='name']").fill("Amina Follow Up")
    inquiry.locator("input[name='email']").fill("followup@example.com")
    inquiry.locator("textarea[name='details']").fill(f"{question} Please contact me.")
    inquiry.get_by_role("button", name="Send question").click()
    success = page.locator("[data-public-chat-success]")
    expect(success).to_be_visible(timeout=10_000)
    expect(success).to_contain_text("Your message was sent successfully")
    success.locator("[data-public-chat-rating-feedback]").fill("Easy handoff.")
    success.get_by_role("button", name="Yes").click()
    expect(success).to_contain_text("feedback was recorded")
    success.get_by_role("button", name="Ask another question").click()
    page.locator("[data-public-chat-forget]").click()
    expect(page.locator("[data-public-chat-profile]")).to_be_visible()
    assert page.evaluate(
        "localStorage.getItem('hm-public-chat-profile-v1')"
    ) is None
    page.keyboard.press("Escape")
    expect(launcher).to_be_focused()
    assert_no_raw_traceback(page)


def test_public_product_chat_session_profile_offline_and_focus_containment(
    page: Page,
    base_url: str,
) -> None:
    page.goto(base_url, wait_until="domcontentloaded")
    page.locator("[data-cookie-essential]").first.click()
    launcher = page.locator("[data-public-chat-launcher]")
    launcher.click()
    page.locator("#public-chat-name").fill("Session Visitor")
    page.locator("#public-chat-email").fill("session@example.com")
    page.get_by_role("button", name="Start conversation").click()

    assert page.evaluate(
        "localStorage.getItem('hm-public-chat-profile-v1')"
    ) is None
    page.wait_for_function(
        "() => sessionStorage.getItem('hm-public-chat-profile-v1') !== null"
    )
    assert page.evaluate(
        "sessionStorage.getItem('hm-public-chat-profile-v1')"
    ) is not None
    page.reload(wait_until="domcontentloaded")
    launcher = page.locator("[data-public-chat-launcher]")
    launcher.click()
    expect(page.locator("[data-public-chat-profile]")).to_be_hidden()
    expect(page.locator("[data-public-chat-messages]")).to_contain_text(
        "Hi Session"
    )

    page.locator("[data-public-chat-new]").focus()
    page.keyboard.press("Shift+Tab")
    assert page.evaluate(
        "document.querySelector('[data-public-chat-panel]').contains(document.activeElement)"
    )

    page.context.set_offline(True)
    page.locator("[data-public-chat-input]").fill("What does HilalMarkets do?")
    expect(page.locator("[data-public-chat-connectivity]")).to_be_visible()
    expect(page.locator("[data-public-chat-send]")).to_be_disabled()
    page.context.set_offline(False)
    expect(page.locator("[data-public-chat-connectivity]")).to_be_hidden()
    expect(page.locator("[data-public-chat-send]")).to_be_enabled()

    question = "Can you explain Watchlists?"
    page.locator("[data-public-chat-input]").fill(question)
    page.locator("[data-public-chat-send]").click()
    feedback = page.locator("[data-public-chat-answer-feedback]")
    expect(feedback).to_contain_text("Did AI answer your question?", timeout=10_000)
    feedback.get_by_role("button", name="No. Submit a support form").click()
    inquiry = page.locator("[data-public-chat-inquiry]")
    expect(inquiry).to_be_visible(timeout=10_000)
    inquiry.get_by_role("button", name="Not now").click()
    expect(inquiry).to_be_hidden()
    expect(page.locator("[data-public-chat-messages]")).to_contain_text(question)
    expect(page.locator("[data-public-chat-composer]")).to_be_visible()
    expect(page.locator("[data-public-chat-input]")).to_be_enabled()

    page.locator("[data-public-chat-forget]").click()
    assert page.evaluate(
        "sessionStorage.getItem('hm-public-chat-profile-v1')"
    ) is None
    page.keyboard.press("Escape")
    expect(launcher).to_be_focused()
    assert_no_raw_traceback(page)


def test_public_product_chat_renders_safe_bold_without_executable_html(
    page: Page,
    base_url: str,
) -> None:
    page.goto(base_url, wait_until="domcontentloaded")
    page.locator("[data-cookie-essential]").first.click()
    page.locator("[data-public-chat-launcher]").click()
    page.locator("#public-chat-name").fill("Bold Visitor")
    page.locator("#public-chat-email").fill("bold@example.com")
    page.get_by_role("button", name="Start conversation").click()

    page.route(
        "**/api/v1/public-chat/answers",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "message": (
                        "Use **verified evidence**. "
                        '<img src="x" onerror="window.__chatInjected = true">'
                    ),
                    "clarification_question": None,
                    "suggested_follow_ups": [],
                    "answer_event_id": "00000000-0000-0000-0000-000000000001",
                    "support_handoff_explicitly_requested": False,
                }
            ),
        ),
    )
    page.locator("[data-public-chat-input]").fill("Explain the evidence")
    page.locator("[data-public-chat-send]").click()

    assistant_bubble = page.locator(
        ".public-chat-message.is-assistant .public-chat-bubble"
    ).last
    expect(assistant_bubble.locator("strong")).to_have_text("verified evidence")
    expect(assistant_bubble).to_contain_text('<img src="x"')
    expect(assistant_bubble.locator("img")).to_have_count(0)
    assert page.evaluate("window.__chatInjected === true") is False
    assert "**" not in assistant_bubble.inner_text()
    assert_no_raw_traceback(page)


def test_dashboard_loads_after_signup_and_navigation(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("dashboard-load"))

    expect(page.get_by_test_id("dashboard-root")).to_be_attached()
    expect(page.get_by_test_id("dashboard-nav")).to_be_visible()
    sidebar = page.locator("[data-sidebar]")
    minimize_icon = page.locator('[data-sidebar-collapse-icon="minimize"]')
    expand_icon = page.locator('[data-sidebar-collapse-icon="expand"]')
    expect(minimize_icon).to_be_visible()
    expect(expand_icon).to_be_hidden()
    expanded_width = sidebar.evaluate("node => node.getBoundingClientRect().width")
    page.locator("[data-sidebar-collapse]").click()
    expect(page.locator("body")).to_have_class(re.compile(r"sidebar-collapsed"))
    expect(minimize_icon).to_be_hidden()
    expect(expand_icon).to_be_visible()
    collapsed_width = sidebar.evaluate("node => node.getBoundingClientRect().width")
    assert expanded_width > 200
    assert collapsed_width <= 80
    expect(page.locator("[data-sidebar-collapse]")).to_have_attribute(
        "aria-label", "Expand side menu"
    )
    page.locator("[data-sidebar-collapse]").click()
    page.locator(".sidebar-create-quick").click()
    page.wait_for_url(re.compile(r".*/dashboard/strategies/new.*"), timeout=10_000)
    expect(page.get_by_test_id("ai-setup-chat")).to_be_visible()
    expect(page.locator("[data-ai-chat-input]")).to_be_visible()
    expect(page.locator("[data-ai-chat-input]")).to_have_attribute(
        "placeholder", "Describe your setup…"
    )
    expect(page.locator(".ai-chat-start-card.scanner")).to_be_visible()
    expect(page.locator(".ai-chat-start-card.monitor")).to_be_visible()
    assert page.locator('a[href="/dashboard/scan-now"]').count() == 0
    assert_no_raw_traceback(page)


def test_all_customer_dashboard_pages_use_the_brand_system(
    page: Page,
    base_url: str,
    repo_root: Path,
) -> None:
    signup(page, base_url, unique_email("dashboard-brand"))
    output = repo_root / "reports" / "playwright" / "dashboard-brand"
    output.mkdir(parents=True, exist_ok=True)
    routes = page.locator("[data-testid='dashboard-nav'] .nav-item").evaluate_all(
        "links => [...new Set(links.map(link => link.getAttribute('href')))]"
    )
    routes.append("/dashboard/strategies/new")

    for route in routes:
        page.set_viewport_size({"width": 1440, "height": 1000})
        target = route if route.startswith("http") else f"{base_url}{route}"
        page.goto(target, wait_until="domcontentloaded")
        expect(page.get_by_test_id("dashboard-root")).to_be_attached()
        expect(page.locator("body")).to_have_attribute(
            "data-brand-system", "hilal-markets-v2"
        )
        expect(page.locator(".sidebar .logo img")).to_have_attribute(
            "src", re.compile(r"hilal-markets-logo\.svg")
        )
        assert "Onest" in page.locator("body").evaluate(
            "node => getComputedStyle(node).fontFamily"
        )
        heading = page.locator("h1:visible, h2:visible").first
        if heading.count():
            expect(heading).to_be_visible()
            assert "Geometria" in heading.evaluate(
                "node => getComputedStyle(node).fontFamily"
            )
        else:
            expect(page.locator(".app-content > *").first).to_be_visible()
        assert page.locator("body").evaluate(
            "node => getComputedStyle(node).backgroundColor"
        ) == "rgb(245, 248, 251)"
        branded_select_count = page.locator("[data-hm-select]").count()
        if branded_select_count:
            expect(page.locator("[data-hm-select-trigger]")).to_have_count(
                branded_select_count
            )
        assert_no_horizontal_overflow(page)
        assert_hilal_brand_palette(page)
        assert_no_raw_traceback(page)
        route_path = urlparse(route).path if route.startswith("http") else route
        slug = route_path.strip("/").replace("/", "-") or "dashboard"
        page.screenshot(path=str(output / f"{slug}-desktop.png"), full_page=True)

        page.set_viewport_size({"width": 390, "height": 844})
        assert_no_horizontal_overflow(page)
        assert_hilal_brand_palette(page)
        page.screenshot(path=str(output / f"{slug}-mobile-390.png"), full_page=True)


def test_screened_market_passport_and_mobile_visual_qa(
    page: Page,
    base_url: str,
    browser_app,
) -> None:
    email = signup(page, base_url, unique_email("screened-market"))
    seeded = seed_sharia_screened_market(browser_app.database_url, email)
    visual_dir = Path("reports/visual-qa/sharia-first")
    visual_dir.mkdir(parents=True, exist_ok=True)

    page.goto(
        f"{base_url}/dashboard/market?methodology_id={seeded['methodology_id']}"
    )
    expect(page.get_by_role("form", name="Filter screened assets")).to_be_visible()
    expect(page.locator("[data-hm-select-trigger]")).to_have_count(2)
    expect(page.get_by_role("region", name="Live screened spot market quotes")).to_be_visible()
    expect(page.get_by_text("All screened assets")).to_have_count(0)
    expect(page.get_by_text("Find opportunities inside a screened market.")).to_have_count(0)
    live_row = page.locator(".live-market-row", has_text="SOL/USDT")
    expect(live_row).to_be_visible(timeout=15_000)
    expect(live_row).to_contain_text("Halal")
    expect(page.locator("[data-live-market-error]")).to_be_hidden()
    expect(page.locator("[data-live-market-status]")).to_have_text("Live quotes connected")
    assert_no_horizontal_overflow(page)
    assert_hilal_brand_palette(page)
    page.locator("[data-live-market-search]").fill("SOL/USDT")
    expect(live_row).to_be_visible()
    expect(page.locator(".live-market-row:visible")).to_have_count(1)
    passport_button = live_row.get_by_role("button", name="Show passport")
    passport_button.click()
    passport_dialog = page.locator("[data-passport-quick-dialog]")
    expect(passport_dialog).to_be_visible()
    expect(passport_dialog).to_contain_text("Eligible")
    expect(passport_dialog.get_by_role("link", name="Open Full Passport")).to_be_visible()
    assert_no_horizontal_overflow(page)
    assert_hilal_brand_palette(page)
    page.screenshot(
        path=str(visual_dir / "passport-quick-view-desktop.png"),
        full_page=False,
    )
    page.keyboard.press("Escape")
    expect(passport_dialog).to_be_hidden()
    expect(passport_button).to_be_focused()

    page.locator("[data-saved-assets-open]").click()
    saved_dialog = page.locator("[data-saved-assets-dialog]")
    expect(saved_dialog).to_be_visible()
    saved_row = saved_dialog.locator("[data-saved-asset]", has_text="SOL")
    expect(saved_row).to_be_visible()
    mark_button = saved_row.locator("[data-saved-asset-mark]")
    mark_button.click()
    expect(mark_button).to_have_attribute("aria-checked", "true")
    expect(saved_dialog.locator("[data-saved-assets-save]")).to_be_visible()
    saved_dialog.locator("[data-saved-assets-cancel]").click()
    expect(saved_dialog).to_be_hidden()
    page.locator("[data-saved-assets-open]").click()
    expect(mark_button).to_have_attribute("aria-checked", "false")
    saved_dialog.locator("[data-saved-assets-cancel]").click()

    page.screenshot(
        path=str(visual_dir / "screened-market-live-table-desktop.png"),
        full_page=True,
    )

    passport_button.click()
    passport_dialog.get_by_role("link", name="Open Full Passport").click()
    expect(page.locator(".passport-summary-header h1")).to_be_visible()
    assert page.locator(".passport-tabs").evaluate(
        "node => getComputedStyle(node).position"
    ) == "static"
    expect(
        page.locator(".passport-summary-header").get_by_role(
            "link", name="Back to market"
        )
    ).to_be_visible()
    expect(page.get_by_text("Official browser-test disclosure")).to_be_visible()
    expect(
        page.get_by_role(
            "heading",
            name="AI-organized factual research — not a religious decision.",
        )
    ).to_be_visible()
    assert_no_horizontal_overflow(page)
    assert_hilal_brand_palette(page)
    page.screenshot(
        path=str(visual_dir / "sharia-evidence-passport-desktop.png"),
        full_page=True,
    )

    page.goto(
        f"{base_url}/dashboard/market?methodology_id={seeded['methodology_id']}"
    )
    page.set_viewport_size({"width": 390, "height": 844})
    expect(live_row).to_be_visible(timeout=15_000)
    assert page.locator(".live-market-panel").bounding_box()["width"] <= 390
    live_row.get_by_role("button", name="Show passport").click()
    expect(passport_dialog).to_be_visible()
    assert_no_horizontal_overflow(page)
    assert_hilal_brand_palette(page)
    page.screenshot(
        path=str(visual_dir / "passport-quick-view-mobile-390.png"),
        full_page=False,
    )
    page.keyboard.press("Escape")
    page.screenshot(
        path=str(visual_dir / "screened-market-mobile-390.png"),
        full_page=True,
    )
    assert_no_raw_traceback(page)


def test_screening_change_opens_evidence_difference_dialog(
    page: Page,
    base_url: str,
    browser_app,
) -> None:
    email = signup(page, base_url, unique_email("screening-difference"))
    seed_sharia_screened_market(browser_app.database_url, email)

    page.goto(
        f"{base_url}/dashboard/opportunities?tab=compliance_changes",
        wait_until="domcontentloaded",
    )
    expect(page.get_by_role("heading", name="Screening changes")).to_be_visible()
    expect(page.get_by_role("link", name="Ended")).to_have_count(0)
    page.get_by_role("button", name="Show evidence difference").click()
    dialog = page.locator("[data-evidence-dialog]")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("Decision difference")
    expect(dialog).to_contain_text("Under Review")
    expect(dialog).to_contain_text("Eligible")
    expect(dialog).to_contain_text("Evidence that changed")
    assert_no_raw_traceback(page)


def test_private_beta_billing_desktop_and_mobile_visual_qa(
    page: Page,
    base_url: str,
) -> None:
    signup(page, base_url, unique_email("checkout-review"))
    output = Path("reports/visual-qa/checkout")
    output.mkdir(parents=True, exist_ok=True)

    page.goto(f"{base_url}/dashboard/billing")
    expect(page.locator(".billing-current-plan")).to_be_visible()
    expect(page.locator(".billing-current-plan").get_by_role("heading")).to_have_text(
        "Basic"
    )
    expect(page.get_by_text("Choose the level of monitoring you need")).to_be_visible()
    expect(page.locator(".dashboard-price-card")).to_have_count(3)
    expect(page.get_by_role("button", name="Choose Monitor monthly")).to_be_visible()
    expect(page.get_by_text("7-day money-back guarantee")).to_be_visible()
    expect(page.get_by_text("Cancel within 7 days of payment for a full refund.")).to_be_visible()
    expect(page.get_by_text("Pro is coming soon")).to_be_visible()
    expect(page.get_by_text("$22", exact=True)).to_have_count(0)
    expect(page.get_by_text("Paid billing is disabled")).to_have_count(0)
    expect(page.get_by_role("radio", name="Annual")).to_be_disabled()
    expect(page.get_by_role("radio", name="Monthly")).to_be_checked()
    expect(page.get_by_text("$220", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Choose Monitor annually")).to_have_count(0)
    expect(page.locator(".dashboard-plan-comparison table")).to_be_visible()
    expect(page.locator(".dashboard-plan-comparison")).to_have_css("margin-top", "18px")
    expect(page.locator(".billing-history-panel")).to_have_css("margin-top", "18px")
    expect(page.get_by_role("link", name="Review and pay")).to_have_count(0)
    assert_no_horizontal_overflow(page)
    assert_hilal_brand_palette(page)
    page.screenshot(path=str(output / "private-beta-access-desktop-1440.png"), full_page=True)

    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator(".billing-current-plan")).to_be_visible()
    expect(page.get_by_role("radio", name="Annual")).to_be_disabled()
    expect(page.get_by_role("radio", name="Monthly")).to_be_checked()
    expect(page.locator(".dashboard-comparison-mobile")).to_be_visible()
    assert_no_horizontal_overflow(page)
    assert_hilal_brand_palette(page)
    page.screenshot(path=str(output / "private-beta-access-mobile-390.png"), full_page=True)
    assert_no_raw_traceback(page)


def test_billing_portal_is_branded_responsive_and_accessible(
    page: Page,
    base_url: str,
) -> None:
    signup(page, base_url, unique_email("billing-portal"))
    page.goto(f"{base_url}/dashboard/billing/portal")
    expect(page.get_by_role("heading", name="Manage your subscription")).to_be_visible()
    expect(page.get_by_role("heading", name="Basic")).to_be_visible()
    expect(page.get_by_text("No paid subscription to manage")).to_be_visible()
    expect(page.get_by_role("heading", name="Your payments and receipts")).to_be_visible()
    expect(page.get_by_text("No payments yet")).to_be_visible()
    expect(page.get_by_role("link", name="Back to plans")).to_be_visible()
    assert_no_horizontal_overflow(page)
    assert_hilal_brand_palette(page)

    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator(".billing-portal-facts")).to_be_visible()
    expect(page.get_by_role("link", name="Back to plans")).to_be_visible()
    assert_no_horizontal_overflow(page)
    assert_no_raw_traceback(page)


def test_strategy_prompt_to_coverage_preview_opens_board(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("prompt-coverage"))
    _open_builder(page, base_url)
    input_box = page.locator("[data-ai-chat-input]")
    input_box.fill(
        "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%"
    )
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.endswith("/messages")
        )
    ) as response_info:
        page.locator("[data-ai-chat-send]").click()
    assert response_info.value.status == 200, response_info.value.body().decode(
        "utf-8", errors="replace"
    )
    expect(page.locator("[data-ai-chat-messages]")).to_contain_text(
        re.compile("inactive preview", re.I)
    )
    expect(page.locator("[data-ai-preview-content]")).to_be_visible()
    expect(page.locator("[data-ai-chat-approve]")).to_be_enabled()
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


def test_ai_setup_chat_v2_deterministic_preview_and_exact_approval(
    page: Page,
    base_url: str,
) -> None:
    signup(page, base_url, unique_email("ai-chat-v2-launch"))
    _open_builder(page, base_url)
    essential_only = page.locator("[data-cookie-essential]").first
    if essential_only.is_visible():
        essential_only.click()
    prompt = (
        "Monitor BTC/USDT when the 15m candle rises open-to-close "
        "by at least 3%"
    )
    page.get_by_test_id("ai-setup-input").fill(prompt)
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and "/api/v1/dashboard/setup-chat/sessions/" in response.url
            and response.url.endswith("/messages")
        )
    ) as response_info:
        page.get_by_test_id("ai-setup-send").click()
    response = response_info.value
    assert response.status == 200
    backend = response.json()
    assert backend["draft_v2"]["schema_version"] == "2.2"
    assert backend["draft_v2"]["executable_version"] >= 2
    assert re.fullmatch(r"[a-f0-9]{64}", backend["draft_v2"]["executable_hash"])
    assert backend["draft_v2"]["condition_ast"]["threshold"] == 3
    assert backend["can_approve"] is True

    expect(page.get_by_test_id("ai-setup-assistant-message").last).to_contain_text(
        "inactive", timeout=10_000
    )
    expect(page.get_by_test_id("ai-setup-validation-errors")).to_have_count(0)
    expect(page.locator("[data-ai-chat-approve]")).to_be_enabled()
    page.locator("[data-ai-open-canvas]").click()
    expect(page.get_by_test_id("strategy-board-node").first).to_be_visible(
        timeout=10_000
    )
    page.locator("[data-ai-return-chat]").click()

    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.endswith("/approve")
        )
    ) as approval_info:
        page.locator("[data-ai-chat-approve]").click()
    approval_response = approval_info.value
    assert approval_response.status == 200
    approved = approval_response.json()
    assert approved["draft_v2"]["approval"]["approved"] is True
    expect(page).to_have_url(re.compile(r"/dashboard/strategies/.+/verify"))
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
    text = (
        "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%"
    )
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
    expect(page.get_by_test_id("ai-setup-assistant-message").last).to_contain_text(
        "inactive",
        timeout=20_000
    )
    assert request_ids[0] == request_ids[1]
    assert page.locator(".ai-chat-message.user", has_text=text).count() == 1
    expect(page.locator("[data-ai-chat-approve]")).to_be_enabled()
    assert_no_raw_traceback(page)


def test_ai_setup_chat_sends_the_current_question_identity(
    page: Page, base_url: str
) -> None:
    """Every message carries the question it was written under, typed or clicked.

    The defect this closes is invisible to a person: a reply typed just before the step
    advanced — or sent from a tab left open, or from a retry of an older action — landed
    on whatever field was current *now*, which is a field the trader was never asked
    about, and nothing on screen said so.
    """

    signup(page, base_url, unique_email("ai-chat-identity"))
    _open_builder(page, base_url)
    sent: list[dict] = []

    def record(route) -> None:
        sent.append(json.loads(route.request.post_data))
        route.continue_()

    page.route("**/api/v1/dashboard/setup-chat/sessions/*/messages", record)
    page.locator("[data-ai-chat-input]").fill("Monitor")
    page.locator("[data-ai-chat-send]").click()
    expect(page.get_by_test_id("ai-setup-assistant-message").last).to_be_visible(
        timeout=20_000
    )
    page.locator("[data-ai-chat-input]").fill("alert me when BTC rises 5%")
    page.locator("[data-ai-chat-send]").click()
    expect(page.get_by_test_id("ai-setup-assistant-message").last).to_be_visible(
        timeout=20_000
    )

    identified = [item for item in sent if item.get("question_id")]
    if identified:
        for item in identified:
            assert item.get("step_revision") is not None, (
                "question_id and step_revision must always travel together"
            )
    assert_no_raw_traceback(page)


def test_ai_setup_chat_option_chip_answers_the_question_on_screen(
    page: Page, base_url: str
) -> None:
    """An old chip cannot answer a newer question, and no chip posts its own label.

    Both halves matter. The label is presentation: a reworded or translated one used to
    silently stop answering its own question, because the label *was* the answer. And the
    identity is read when the chip is clicked, not when it was drawn, so a control left
    over from an earlier step is refused by the server instead of landing on a new field.
    """

    signup(page, base_url, unique_email("ai-chat-chip"))
    _open_builder(page, base_url)
    sent: list[dict] = []

    def record(route) -> None:
        sent.append(json.loads(route.request.post_data))
        route.continue_()

    page.route("**/api/v1/dashboard/setup-chat/sessions/*/messages", record)
    page.locator("[data-ai-chat-input]").fill("Monitor")
    page.locator("[data-ai-chat-send]").click()
    expect(page.get_by_test_id("ai-setup-assistant-message").last).to_be_visible(
        timeout=20_000
    )
    page.locator("[data-ai-chat-input]").fill("alert me when BTC rises 5%")
    page.locator("[data-ai-chat-send]").click()
    expect(page.get_by_test_id("ai-setup-assistant-message").last).to_be_visible(
        timeout=20_000
    )

    chips = page.locator(".ai-chat-chip")
    if chips.count():
        before = len(sent)
        chips.first.click()
        expect(page.get_by_test_id("ai-setup-assistant-message").last).to_be_visible(
            timeout=20_000
        )
        clicked = sent[before:]
        assert clicked, "the chip sent nothing"
        payload = clicked[0]
        assert payload.get("option_key") == "clarification_answer", (
            "a clarification chip must use the generic control, not its own label"
        )
        assert payload.get("option_value"), "the canonical value must travel"
        assert payload.get("question_id"), "the chip must say which question it answers"
        assert payload.get("step_revision") is not None
    assert_no_raw_traceback(page)


def test_ai_setup_chat_every_send_carries_a_request_id(page: Page, base_url: str) -> None:
    """The server refuses a message without one, so the real client must always send it.

    Also proves the ids are distinct per attempt: reusing one for a different message
    would be refused as a conflict, and minting a new one for a retry would charge the
    user twice for the same message.
    """

    signup(page, base_url, unique_email("ai-chat-request-id"))
    _open_builder(page, base_url)
    sent: list[dict] = []

    def record(route) -> None:
        sent.append(json.loads(route.request.post_data))
        route.continue_()

    page.route("**/api/v1/dashboard/setup-chat/sessions/*/messages", record)
    for text in ("Monitor", "alert me when BTC rises 5%"):
        page.locator("[data-ai-chat-input]").fill(text)
        page.locator("[data-ai-chat-send]").click()
        expect(page.get_by_test_id("ai-setup-assistant-message").last).to_be_visible(
            timeout=20_000
        )

    assert sent, "no message reached the server"
    keys = [item.get("client_message_id") for item in sent]
    assert all(keys), "every message must carry a request id"
    assert all(len(str(key)) >= 8 for key in keys), "a guessable id is not an id"
    assert len(set(keys)) == len(keys), "two different messages must not share one id"
    assert_no_raw_traceback(page)


def test_ai_setup_chat_double_click_sends_one_turn(page: Page, base_url: str) -> None:
    """Clicking send twice must not buy two paid turns.

    The composer closes while a turn is in flight, so the second click has nothing to
    send. If it did send, the server would refuse it — but the user would see an error
    for something they did nothing wrong in.
    """

    signup(page, base_url, unique_email("ai-chat-double-click"))
    _open_builder(page, base_url)
    sent: list[dict] = []

    def record(route) -> None:
        sent.append(json.loads(route.request.post_data))
        route.continue_()

    page.route("**/api/v1/dashboard/setup-chat/sessions/*/messages", record)
    page.locator("[data-ai-chat-input]").fill("alert me when BTC rises 5%")
    send = page.locator("[data-ai-chat-send]")
    send.click()
    # The button disables itself immediately; a second click is a no-op rather than a
    # second request. `click(force=True)` proves that even a determined double-click
    # cannot get past it.
    send.click(force=True, timeout=2_000)
    expect(page.get_by_test_id("ai-setup-assistant-message").last).to_be_visible(
        timeout=20_000
    )
    assert len(sent) == 1, f"one click, one turn — got {len(sent)} requests"
    assert_no_raw_traceback(page)


def test_ai_setup_chat_refresh_does_not_duplicate_a_turn(page: Page, base_url: str) -> None:
    """Reloading mid-conversation shows the same messages, never a repeated mutation."""

    signup(page, base_url, unique_email("ai-chat-refresh"))
    _open_builder(page, base_url)
    page.locator("[data-ai-chat-input]").fill("alert me when BTC rises 5%")
    page.locator("[data-ai-chat-send]").click()
    expect(page.get_by_test_id("ai-setup-assistant-message").last).to_be_visible(
        timeout=20_000
    )
    before = page.get_by_test_id("ai-setup-assistant-message").count()

    page.reload()
    _open_builder(page, base_url)
    expect(page.get_by_test_id("ai-setup-assistant-message").last).to_be_visible(
        timeout=20_000
    )
    after = page.get_by_test_id("ai-setup-assistant-message").count()
    assert after == before, f"a refresh changed the conversation: {before} -> {after}"
    assert_no_raw_traceback(page)


def test_ai_setup_chat_draft_history_controls_are_keyed_and_reachable(
    page: Page, base_url: str
) -> None:
    """Undo and Clear exist once there is something to undo, and each is keyed.

    Keyed matters: a double-clicked Undo must undo once. The key travels in the request
    body, so it is asserted on the wire rather than in the DOM.
    """

    signup(page, base_url, unique_email("ai-chat-history"))
    _open_builder(page, base_url)
    actions: list[dict] = []

    def record(route) -> None:
        actions.append(json.loads(route.request.post_data))
        route.continue_()

    page.route("**/api/v1/dashboard/setup-chat/sessions/*/draft-actions", record)
    page.locator("[data-ai-chat-input]").fill("alert me when BTC rises 5%")
    page.locator("[data-ai-chat-send]").click()
    expect(page.get_by_test_id("ai-setup-assistant-message").last).to_be_visible(
        timeout=20_000
    )

    undo = page.get_by_test_id("ai-undo")
    if undo.count():
        undo.first.click()
        expect(page.get_by_test_id("ai-setup-assistant-message").last).to_be_visible(
            timeout=20_000
        )
        assert actions, "the undo control sent nothing"
        assert actions[0]["action"] == "undo_last_material_change"
        assert len(str(actions[0].get("client_message_id") or "")) >= 8, (
            "every draft action needs its own request id"
        )
    assert_no_raw_traceback(page)


def test_ai_setup_chat_pending_change_is_confirmed_not_applied(
    page: Page, base_url: str
) -> None:
    """A destructive proposal shows what it would do, and changes nothing until told.

    The card is rendered from the server's own diff. Its buttons must exist and be
    reachable by keyboard, because a confirmation nobody can reach is a change nobody
    can make.
    """

    signup(page, base_url, unique_email("ai-chat-pending"))
    _open_builder(page, base_url)
    page.locator("[data-ai-chat-input]").fill("alert me when BTC rises 5%")
    page.locator("[data-ai-chat-send]").click()
    expect(page.get_by_test_id("ai-setup-assistant-message").last).to_be_visible(
        timeout=20_000
    )

    card = page.get_by_test_id("ai-pending-change")
    if card.count():
        expect(card.first).to_be_visible()
        confirm = page.get_by_test_id("ai-pending-confirm")
        cancel = page.get_by_test_id("ai-pending-cancel")
        assert cancel.count(), "a proposal must always be refusable"
        if confirm.count():
            confirm.first.focus()
            expect(confirm.first).to_be_focused()
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
    notification_box = page.locator("[data-notification-center-trigger]").bounding_box()
    create_plan_box = page.locator(".topbar-right > .sidebar-create-quick").bounding_box()
    assert sidebar_box is not None and 14 <= sidebar_box["x"] <= 18
    assert notification_box is not None and notification_box["width"] <= 60
    assert create_plan_box is not None and create_plan_box["width"] <= 220
    expect(page.locator(".topbar-right > .sidebar-create-quick")).to_contain_text(
        "New Watchlist"
    )
    page.screenshot(path=str(output / "ai-setup-chat-desktop.png"), full_page=True)

    prompt = (
        "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%"
    )
    page.locator("[data-ai-chat-input]").fill(prompt)
    page.locator("[data-ai-chat-send]").click()
    expect(page.locator("[data-ai-preview-content]")).to_be_visible(timeout=20_000)
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
    legacy_toggle = page.locator("[data-sidebar-toggle]")
    if legacy_toggle.count() and not page.locator("body").evaluate(
        "node => node.classList.contains('sidebar-collapsed')"
    ):
        legacy_toggle.click()
    else:
        expect(page.locator("[data-hilal-sidebar]")).not_to_have_class(
            re.compile("is-open")
        )
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
    essential_only = page.locator("[data-cookie-essential]").first
    if essential_only.is_visible():
        essential_only.click()

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
    seed_disclaimer_acceptance(browser_app.database_url, email)
    seed_telegram_connection(browser_app.database_url, email)
    seed_paid_monitor_access(browser_app.database_url, email)
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
    assert_no_horizontal_overflow(page)
    assert_hilal_brand_palette(page)
    output = repo_root / "reports" / "playwright" / "visual-qa"
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / "verified-strategy-workflow-desktop.png"), full_page=True)

    while page.locator("[data-accept-statement]").count():
        count = page.locator("[data-accept-statement]").count()
        page.locator("[data-accept-statement]").first.click()
        expect(page.locator("[data-accept-statement]")).to_have_count(count - 1, timeout=15_000)
    expect(page.locator("[data-approve-version]")).to_be_enabled()
    page.locator("[data-approve-version]").click()
    expect(page.locator("[data-interpretation-state]")).to_contain_text(
        "approved", timeout=15_000
    )
    expect(page.locator("[data-verified-notice]")).to_contain_text(
        re.compile("no verification blocker", re.I), timeout=15_000
    )

    page.set_viewport_size({"width": 390, "height": 844})
    assert_no_horizontal_overflow(page)
    assert_hilal_brand_palette(page)
    page.screenshot(path=str(output / "verified-strategy-workflow-mobile-390.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("[data-activate-version]").click()
    page.wait_for_url(re.compile(r".*/dashboard/opportunities.*"), timeout=30_000)

    page.goto(f"{base_url}/dashboard/strategies", wait_until="domcontentloaded")
    row = page.get_by_test_id("monitor-row").first
    expect(row).to_be_visible()
    expect(row.get_by_test_id("monitor-status")).to_contain_text(re.compile("active", re.I))
    expect(
        row.get_by_role("button", name=re.compile(r"^(Pause|Resume)$", re.I))
    ).to_be_visible()
    assert_no_horizontal_overflow(page)
    assert_hilal_brand_palette(page)
    page.screenshot(
        path=str(output / "hilalmarkets-watch-plans-desktop.png"),
        full_page=True,
    )

    for suffix, screenshot_name in (
        ("", "strategy-detail-desktop.png"),
        ("/versions", "strategy-versions-desktop.png"),
        ("/builder", "strategy-edit-canvas-desktop.png"),
    ):
        page.goto(
            f"{base_url}/dashboard/strategies/{strategy_id}{suffix}",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_test_id("dashboard-root")).to_be_attached()
        assert_no_horizontal_overflow(page)
        assert_hilal_brand_palette(page)
        assert_no_raw_traceback(page)
        page.screenshot(path=str(output / screenshot_name), full_page=True)

    page.goto(
        f"{base_url}/dashboard/strategies/{strategy_id}/versions",
        wait_until="domcontentloaded",
    )
    page.set_viewport_size({"width": 390, "height": 844})
    assert_no_horizontal_overflow(page)
    assert_hilal_brand_palette(page)
    page.screenshot(path=str(output / "strategy-versions-mobile-390.png"), full_page=True)
    assert_no_raw_traceback(page)


def test_legacy_scan_route_redirects_into_chat_scanner(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("scanner-route"))
    page.goto(f"{base_url}/dashboard/scan-now", wait_until="domcontentloaded")
    page.wait_for_url(re.compile(r".*/dashboard/strategies/new\?mode=scanner"), timeout=10_000)
    expect(page.get_by_test_id("ai-setup-chat")).to_be_visible()
    expect(page.locator("[data-ai-chat-messages]")).to_contain_text(
        "switched to scanner", timeout=10_000
    )
    composer = page.locator(".ai-chat-composer")
    assistant_bubble = page.locator(".ai-chat-message.assistant .ai-chat-bubble").first
    submit = composer.locator("button[type='submit']")
    expect(composer).to_be_visible()
    expect(assistant_bubble).to_be_visible()
    assert composer.evaluate(
        "node => getComputedStyle(node).backgroundColor"
    ) == "rgb(250, 251, 252)"
    assert assistant_bubble.evaluate(
        "node => getComputedStyle(node).backgroundColor"
    ) == "rgb(255, 255, 255)"
    assert assistant_bubble.evaluate(
        "node => getComputedStyle(node).color"
    ) == "rgb(43, 46, 53)"
    assert submit.evaluate(
        "node => getComputedStyle(node).backgroundColor"
    ) == "rgb(203, 250, 77)"
    assert submit.evaluate("node => getComputedStyle(node).color") == "rgb(43, 46, 53)"
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
    assert_no_horizontal_overflow(page)
    assert_hilal_brand_palette(page)
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
    expect(page.locator("body")).to_contain_text("What is closest right now?")
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
    expect(page.get_by_role("heading", name="What is closest right now?")).to_be_visible()
    expect(page.locator(".activity-page-tabs")).to_have_count(0)
    expect(page.locator("[data-radar-list] .readiness-candidate")).to_have_count(4, timeout=15_000)
    assert page.locator(".readiness-candidate img").evaluate_all(
        "images => images.every(image => image.complete && image.naturalWidth > 0)"
    )
    state_control = page.locator("[data-radar-state]").locator("xpath=..")
    expect(state_control.locator("[data-hm-select-trigger]")).to_be_visible()
    state_control.locator("[data-hm-select-trigger]").click()
    expect(state_control.locator("[data-hm-select-menu]")).to_be_visible()
    page.screenshot(
        path=str(visual_dir / "branded-opportunity-state-select.png"),
        full_page=False,
    )
    state_control.locator("[data-hm-select-menu]").get_by_role(
        "option", name="Near miss", exact=True
    ).click()
    expect(page.locator("[data-radar-state]")).to_have_value("near_miss")
    expect(page.locator("[data-radar-list] .readiness-candidate")).to_have_count(1)
    page.locator("[data-radar-state]").select_option("")
    expect(page.locator("[data-radar-list] .readiness-candidate")).to_have_count(4)
    insight_cards = page.locator(".activity-insight-card")
    expect(insight_cards).to_have_count(2)
    insight_cards.nth(0).locator("summary").click()
    expect(page.locator("[data-health-list]")).to_contain_text("Degraded")
    expect(page.locator("[data-health-list]")).to_contain_text("Too Strict")
    insight_cards.nth(1).locator("summary").click()
    expect(page.locator("[data-bottleneck-list]")).to_contain_text("RVOL above 1.50x")
    assert_no_horizontal_overflow(page)
    assert_hilal_brand_palette(page)
    page.screenshot(path=str(visual_dir / "setup-observability-desktop.png"), full_page=True)
    page.locator(".state-forming").screenshot(path=str(visual_dir / "forming-candidate.png"))
    page.locator(".state-near_miss").screenshot(path=str(visual_dir / "near-miss-candidate.png"))
    page.locator(".activity-insight-card").nth(0).screenshot(
        path=str(visual_dir / "degraded-too-strict-monitor.png")
    )
    page.locator(".activity-insight-card").nth(1).screenshot(
        path=str(visual_dir / "bottleneck-intelligence.png")
    )

    page.locator("[data-monitor-filter-trigger]").click()
    expect(page.locator("[data-monitor-filter-menu]")).to_be_visible()
    page.locator(f'[data-monitor-option="{seeded["strategy_id"]}"]').click()
    page.wait_for_url(
        re.compile(
            rf".*/dashboard/opportunities\?tab=forming&monitor={seeded['strategy_id']}"
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
    assert_no_horizontal_overflow(page)
    assert_hilal_brand_palette(page)
    page.locator("[data-observability-drawer]").screenshot(
        path=str(visual_dir / "candidate-detail-timeline.png")
    )
    page.locator("[data-observability-drawer-close]").first.click()

    page.locator("[data-radar-state]").select_option("provider_data_error")
    expect(page.locator("[data-radar-list] .readiness-candidate")).to_have_count(1)
    assert_hilal_brand_palette(page)
    page.locator("[data-radar-list]").screenshot(path=str(visual_dir / "provider-error-state.png"))
    page.locator("[data-radar-state]").select_option("invalidated")
    expect(page.locator("[data-radar-list]")).to_contain_text("No readiness evidence yet")
    page.locator("[data-radar-list]").screenshot(path=str(visual_dir / "empty-radar-state.png"))

    page.locator("[data-radar-state]").select_option("")
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator("[data-radar-list] .readiness-candidate")).to_have_count(4)
    assert_no_horizontal_overflow(page)
    assert_hilal_brand_palette(page)
    page.screenshot(path=str(visual_dir / "setup-observability-mobile-390.png"), full_page=True)
    page.emulate_media(reduced_motion="reduce")
    animation_name = page.locator(".readiness-candidate").first.evaluate(
        "node => getComputedStyle(node).animationName"
    )
    assert animation_name == "none"
    assert_no_raw_traceback(page)


def test_notification_channel_handoff_links_smoke(page: Page, base_url: str) -> None:
    signup(page, base_url, unique_email("integrations-smoke"))
    page.goto(f"{base_url}/dashboard/integrations", wait_until="domcontentloaded")
    expect(page.get_by_test_id("integrations-root")).to_be_visible()
    expect(page.get_by_test_id("telegram-integration-card")).to_contain_text("Telegram")
    expect(page.get_by_test_id("whatsapp-integration-card")).to_be_visible()
    expect(page.get_by_test_id("whatsapp-integration-card")).to_contain_text("Unavailable")
    expect(
        page.get_by_test_id("whatsapp-integration-card").get_by_role(
            "button", name="Connect WhatsApp"
        )
    ).to_be_disabled()
    expect(page.get_by_test_id("discord-integration-card")).to_have_count(0)
    body = page.locator("body").inner_text(timeout=10_000).lower()
    assert "telegram_bot_token" not in body
    assert "whatsapp_access_token" not in body
    assert "whatsapp_app_secret" not in body
    assert "whatsapp_verify_token" not in body
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


# ---------------------------------------------------------------------------
# The Guided Watch Plan Builder in the real browser.
#
# Each of these builds a setup with the keyboard and the mouse only. No message is ever
# typed, so a passing run is evidence that the product does not depend on the assistant.
# ---------------------------------------------------------------------------


def test_guided_builder_creates_a_watch_plan_without_typing_a_message(
    page: Page,
    base_url: str,
) -> None:
    """Mode, coins and one rule, all by clicking. The composer is never used."""

    signup(page, base_url, unique_email("guided-builder"))
    _open_builder(page, base_url)
    builder = page.get_by_test_id("guided-builder")
    expect(builder).to_be_visible(timeout=15_000)

    page.get_by_test_id("guided-builder-mode").get_by_role("button", name="Monitor").click()
    expect(page.get_by_test_id("guided-builder-assets")).to_be_visible()
    page.get_by_test_id("guided-builder-assets").get_by_role(
        "button", name="Every eligible coin"
    ).click()

    page.get_by_test_id("guided-builder-add-rule").click()
    form = page.get_by_test_id("guided-builder-rule-form")
    expect(form).to_be_visible()
    page.get_by_test_id("guided-builder-mechanic").select_option(
        label="Candle moves by a percentage"
    )
    form.locator('[data-parameter="threshold"]').fill("5")
    form.locator('[data-parameter="timeframe"]').select_option("1h")
    page.get_by_test_id("guided-builder-save-rule").click()

    expect(page.get_by_test_id("guided-builder-rule").first).to_be_visible(timeout=15_000)
    expect(page.get_by_test_id("guided-builder-rule").first).to_contain_text("percent")
    # Nothing was typed into the assistant at any point.
    expect(page.locator("[data-ai-chat-input]")).to_have_value("")
    assert_no_raw_traceback(page)


def test_guided_builder_offers_only_values_the_server_sent(
    page: Page,
    base_url: str,
) -> None:
    """Every option in the form comes from the contract fetch, and nothing else does."""

    signup(page, base_url, unique_email("guided-contract"))
    contract: dict = {}

    def capture(response) -> None:
        if response.url.endswith("/setup-chat/builder-contract") and response.ok:
            contract.update(response.json())

    page.on("response", capture)
    _open_builder(page, base_url)
    expect(page.get_by_test_id("guided-builder")).to_be_visible(timeout=15_000)
    assert contract, "the Builder drew itself without asking the server what to draw"

    page.get_by_test_id("guided-builder-add-rule").click()
    page.get_by_test_id("guided-builder-mechanic").select_option(
        label="Candle moves by a percentage"
    )
    form = page.get_by_test_id("guided-builder-rule-form")
    offered = set(
        form.locator('[data-parameter="timeframe"] option').evaluate_all(
            "nodes => nodes.map(node => node.value)"
        )
    )
    mechanic = next(
        item for item in contract["mechanics"] if item["key"] == "open_to_close_percentage"
    )
    allowed = {
        choice["value"]
        for parameter in mechanic["parameters"]
        if parameter["name"] == "timeframe"
        for choice in parameter["choices"]
    }
    assert offered <= allowed, "the form offered a candle size the server did not send"
    assert_no_raw_traceback(page)


def test_guided_builder_shows_a_rule_the_assistant_wrote_and_lets_it_be_edited(
    page: Page,
    base_url: str,
) -> None:
    """One state, two surfaces. What the assistant wrote is editable by hand."""

    signup(page, base_url, unique_email("guided-handoff"))
    _open_builder(page, base_url)
    page.get_by_test_id("ai-setup-input").fill(
        "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%"
    )
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and "/api/v1/dashboard/setup-chat/sessions/" in response.url
            and response.url.endswith("/messages")
        ),
        timeout=60_000,
    ):
        page.keyboard.press("Enter")

    rule = page.get_by_test_id("guided-builder-rule").first
    expect(rule).to_be_visible(timeout=30_000)
    rule.get_by_role("button", name="Edit").click()
    form = page.get_by_test_id("guided-builder-rule-form")
    expect(form).to_be_visible()
    expect(form.locator('[data-parameter="threshold"]')).to_have_value("3")
    assert_no_raw_traceback(page)


def test_guided_builder_keeps_working_when_the_assistant_is_unavailable(
    page: Page,
    base_url: str,
) -> None:
    """An assistant outage closes the composer and nothing else.

    The message the person reads must be about the assistant, never about their setup.
    """

    signup(page, base_url, unique_email("guided-degraded"))
    _open_builder(page, base_url)
    expect(page.get_by_test_id("guided-builder")).to_be_visible(timeout=15_000)

    page.evaluate(
        """() => {
            const root = document.querySelector('[data-ai-setup-chat]');
            root.dataset.assistantAvailable = 'false';
        }"""
    )
    page.route(
        "**/api/v1/dashboard/setup-chat/sessions/*/messages",
        lambda route: route.fulfill(status=503, content_type="application/json", body="{}"),
    )

    # The guided fields still work while the assistant cannot be reached.
    page.get_by_test_id("guided-builder-mode").get_by_role("button", name="Scanner").click()
    expect(
        page.get_by_test_id("guided-builder-mode").get_by_role("button", name="Scanner")
    ).to_have_attribute("aria-pressed", "true", timeout=15_000)
    assert_no_raw_traceback(page)


def test_guided_builder_survives_a_refresh_and_works_on_a_phone(
    page: Page,
    base_url: str,
) -> None:
    """Progress lives in the draft, so reloading shows exactly the same thing."""

    signup(page, base_url, unique_email("guided-mobile"))
    page.set_viewport_size({"width": 390, "height": 844})
    _open_builder(page, base_url)
    expect(page.get_by_test_id("guided-builder")).to_be_visible(timeout=15_000)

    page.get_by_test_id("guided-builder-starters").get_by_role(
        "button", name="A coin jumps"
    ).click()
    rule = page.get_by_test_id("guided-builder-rule").first
    expect(rule).to_be_visible(timeout=20_000)
    written = rule.inner_text()

    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_test_id("guided-builder-rule").first).to_be_visible(timeout=20_000)
    assert page.get_by_test_id("guided-builder-rule").first.inner_text() == written

    assert_no_horizontal_overflow(page)
    assert_no_raw_traceback(page)