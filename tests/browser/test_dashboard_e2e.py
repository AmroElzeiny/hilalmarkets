from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, expect

from tests.browser.conftest import (
    assert_hilal_brand_palette,
    assert_no_horizontal_overflow,
    assert_no_raw_traceback,
    close_any_open_guide,
    seed_alert_proof,
    seed_paid_monitor_access,
    seed_setup_observability,
    seed_sharia_screened_market,
    seed_system_brain_reviewer,
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
    # The assistant is a button in the corner now, and its window opens from it.
    expect(page.locator("[data-brain-agent-open]")).to_be_visible()
    page.locator("[data-brain-agent-open]").click()
    expect(page.locator("[data-brain-agent-window]")).to_be_visible()
    page.locator("[data-brain-agent-close]").click()
    expect(page.locator("[data-brain-agent-window]")).to_be_hidden()
    # Inbox, Cases, Stats, Operations, Governance, Users, Audit & Settings.
    expect(page.locator(".brain-sidebar nav a")).to_have_count(7)
    expect(page.get_by_role("heading", name="Needs attention")).to_be_visible()
    assert_no_horizontal_overflow(page)
    assert_no_raw_traceback(page)
    page.screenshot(path=str(output / "system-brain-desktop.png"), full_page=True)

    # On a phone the menu is a drawer rather than a strip of unlabelled icons: the
    # labels come back, and every link has a readable accessible name.
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_role("button", name="Open menu")).to_be_visible()
    page.get_by_role("button", name="Open menu").click()
    expect(page.locator(".brain-sidebar nav a").get_by_text("Stats")).to_be_visible()
    assert_no_horizontal_overflow(page)
    page.screenshot(path=str(output / "system-brain-menu-390.png"), full_page=True)
    page.get_by_role("button", name="Close menu").click()
    page.set_viewport_size({"width": 1440, "height": 900})

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
    # The heading the React source really carries. The shipped bundle had been built from
    # older source and still said "How can we help?", so this test was measuring a stale
    # file rather than the page. Rebuilding the bundle to ship the Help Center removal
    # brought the two back into step.
    expect(
        page.get_by_role(
            "heading", name="Tell us what you need."
        )
    ).to_be_visible()
    expect(page.locator("[data-contact-form]")).to_be_visible()
    page.screenshot(path=str(output / "hilalmarkets-contact-mobile-390.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.screenshot(path=str(output / "hilalmarkets-contact-desktop.png"), full_page=True)

    page.goto(f"{base_url}/signup", wait_until="domcontentloaded")
    expect(page.locator(".auth-shell")).to_be_visible()
    # The white panel the form sits in. It was `.auth-form-wrap` before the sign-in pages
    # were rebuilt; it is `<main class="auth-main">` now, which is also a real landmark
    # rather than a bare div.
    expect(page.locator("main.auth-main")).to_be_visible()
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
    # A new account lands on Main with the guide already open, and the guide's overlay
    # takes the clicks aimed at the page under it. That is the guide working. Anything
    # that then drives the dashboard has to close it first.
    close_any_open_guide(page)

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
    # Icon width. Written as the rule rather than as one number: the row inside is a 44px
    # target with room around it, so the exact figure belongs to the design.
    assert collapsed_width <= 96
    assert collapsed_width < expanded_width / 2
    expect(page.locator("[data-sidebar-collapse]")).to_have_attribute(
        "aria-label", "Open the menu"
    )
    page.locator("[data-sidebar-collapse]").click()
    # The way to build something is the menu entry that says so. It used to be a button
    # in the topbar on every page, including the pages where it made no sense.
    page.get_by_role("link", name="Create a monitor").click()
    page.wait_for_url(re.compile(r".*/dashboard/create-monitor.*"), timeout=10_000)
    # The canvas, and nothing else: the assistant page that used to sit beside it is
    # gone, and its address lands here rather than on a chat box.
    expect(page.locator("[data-monitor-root]")).to_be_attached()
    page.goto(f"{base_url}/dashboard/strategies/new", wait_until="domcontentloaded")
    expect(page.locator("[data-monitor-root]")).to_be_attached()
    assert page.locator("[data-ai-setup-chat]").count() == 0
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
    # The canvas is a menu entry, so it is already in `routes`. The assistant page used
    # to be appended here because it was not in the menu; it no longer exists.

    for route in routes:
        page.set_viewport_size({"width": 1440, "height": 1000})
        target = route if route.startswith("http") else f"{base_url}{route}"
        page.goto(target, wait_until="domcontentloaded")
        expect(page.get_by_test_id("dashboard-root")).to_be_attached()
        expect(page.locator("body")).to_have_attribute(
            "data-brand-system", "hilal-markets-v2"
        )
        # The wordmark. The minimized menu shows the symbol on its own instead, so the
        # brand link carries both pictures and this is the first of them.
        expect(page.locator(".sidebar .dashboard-logo-art")).to_have_attribute(
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
    # This test was still written against the market page as it looked before the
    # redesign: `.live-market-row`, `[data-live-market-*]`, a "Show passport" button and
    # the old saved-assets dialog. None of those has existed since `/dashboard/market`
    # started serving the redesigned page, so the test was failing on markup instead of
    # on the rule it exists to protect. `test_adversarial_qa_e2e.py` was repaired the
    # same way earlier. The rules below are unchanged; only the place to look moved.
    card = page.locator(".t-asset", has_text="SOL").first
    expect(card).to_be_visible(timeout=15_000)
    expect(card).to_contain_text("Shariah-compliant")
    expect(page.locator("[data-quote-error]")).to_be_hidden()
    # "live" or "stale" both mean the prices arrived; only "down" means they did not.
    # Pinning this to "live" would fail whenever the stub answers with a held snapshot.
    expect(page.locator("[data-live-pill]")).not_to_have_attribute("data-state", "down")
    assert_no_horizontal_overflow(page)
    assert_hilal_brand_palette(page)
    page.locator("[data-search]").fill("SOL")
    expect(card).to_be_visible()
    expect(page.locator(".t-asset:visible")).to_have_count(1)
    passport_button = card.locator("[data-quick-view]")
    passport_button.click()
    passport_dialog = page.locator("[data-passport-dialog]")
    expect(passport_dialog).to_be_visible()
    expect(passport_dialog).to_contain_text("Shariah-compliant")
    expect(
        passport_dialog.get_by_role("link", name="Open the full Passport")
    ).to_be_visible()
    assert_no_horizontal_overflow(page)
    assert_hilal_brand_palette(page)
    page.screenshot(
        path=str(visual_dir / "passport-quick-view-desktop.png"),
        full_page=False,
    )
    page.keyboard.press("Escape")
    expect(passport_dialog).to_be_hidden()
    expect(passport_button).to_be_focused()

    # Following a coin is the heart on the card now, not a row in a saved-assets popup.
    # The seed puts SOL in the default watchlist, so the card arrives already followed:
    # the round trip below is unfollow, then follow again.
    heart = card.locator("[data-favorite]")
    favorite_count = page.locator("[data-favorite-count]")
    expect(heart).to_have_attribute("aria-pressed", "true")
    expect(favorite_count).to_have_text("1")
    heart.click()
    expect(heart).to_have_attribute("aria-pressed", "false")
    expect(favorite_count).to_be_hidden()
    heart.click()
    expect(heart).to_have_attribute("aria-pressed", "true")
    expect(favorite_count).to_have_text("1")

    page.locator("[data-open-favorites]").click()
    favorites_dialog = page.locator("[data-favorites-dialog]")
    expect(favorites_dialog).to_be_visible()
    expect(favorites_dialog.locator("[data-favorite-row]", has_text="SOL")).to_be_visible()
    favorites_dialog.locator("[data-favorites-close]").click()
    expect(favorites_dialog).to_be_hidden()

    page.screenshot(
        path=str(visual_dir / "screened-market-live-table-desktop.png"),
        full_page=True,
    )

    passport_button.click()
    passport_dialog.get_by_role("link", name="Open the full Passport").click()
    expect(page.locator(".t-head h1")).to_be_visible()
    expect(page.get_by_role("link", name="Back to the list")).to_be_visible()
    # The status is stated and its evidence is reachable on the same page. The seeded
    # evidence source is named in the Evidence section, so a Passport that lost its
    # sources fails here rather than passing on a status with nothing behind it.
    expect(page.get_by_role("heading", name="Evidence")).to_be_visible()
    expect(page.get_by_text("Official browser-test disclosure")).to_be_visible()
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
    card = page.locator(".t-asset", has_text="SOL").first
    expect(card).to_be_visible(timeout=15_000)
    assert card.bounding_box()["width"] <= 390
    card.locator("[data-quick-view]").click()
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
    # The investigation drawer is a Monitor-plan feature, so the template does not
    # render it at all for a free account. The test reached the point of clicking
    # "investigate" and then looked for a drawer the page had never been given.
    seed_paid_monitor_access(browser_app.database_url, email)
    seeded = seed_setup_observability(browser_app.database_url, email)
    visual_dir = repo_root / "reports" / "playwright" / "visual-qa"
    visual_dir.mkdir(parents=True, exist_ok=True)

    page.goto(f"{base_url}/dashboard/lifecycles", wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="What is closest right now?")).to_be_visible()
    expect(page.locator(".activity-page-tabs")).to_have_count(0)
    expect(page.locator("[data-radar-list] .readiness-candidate")).to_have_count(4, timeout=15_000)
    # The cards existing is not the same as their pictures having arrived. The count
    # above waits for the cards; nothing waited for the images inside them, so this
    # sampled `complete` at one instant and could catch the last logo mid-flight on
    # a machine busy running the rest of the suite. That is the whole flake: the page
    # was about to be correct and was measured a moment too early.
    #
    # Requiring at least one image closes a second hole. `every` on an empty list is
    # true, so a page that rendered no logos at all passed this check.
    page.wait_for_function(
        "() => { const images = Array.from("
        "document.querySelectorAll('.readiness-candidate img')); "
        "return images.length > 0 && images.every("
        "image => image.complete && image.naturalWidth > 0); }",
        timeout=15_000,
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
    # Each filter change is a round trip. The first count on this page already allows
    # 15s for it; these two were left on the 5s default, which is the same race with
    # a smaller margin.
    expect(page.locator("[data-radar-list] .readiness-candidate")).to_have_count(
        1, timeout=15_000
    )
    page.locator("[data-radar-state]").select_option("")
    expect(page.locator("[data-radar-list] .readiness-candidate")).to_have_count(
        4, timeout=15_000
    )
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


def _first_condition_board_node(page: Page):
    return page.locator('[data-testid="strategy-board-node"][data-board-action="condition"]').first


# ---------------------------------------------------------------------------
# The Guided Watch Plan Builder in the real browser.
#
# Each of these builds a setup with the keyboard and the mouse only. No message is ever
# typed, so a passing run is evidence that the product does not depend on the assistant.
# ---------------------------------------------------------------------------




def test_the_billing_page_carries_the_live_countdown_on_the_plan_card(
    page: Page,
    base_url: str,
) -> None:
    """The third surface that shows a price shows the same timer, counting.

    Signed in or signed out, the offer is one fact. The dashboard draws its cards from
    the same offer the public pages use, so if the timer stops here it has stopped for a
    paying customer looking at the plan they are about to buy.
    """

    from ai_market_monitor.core.plans import original_monthly_price, promotion_is_active

    signup(page, base_url, unique_email("billing-countdown"))
    page.goto(f"{base_url}/dashboard/billing")
    expect(page.locator(".dashboard-price-card").first).to_be_visible()

    if not promotion_is_active():
        assert original_monthly_price("trader") is None
        expect(page.locator(".offer-countdown[data-offer-live]")).to_have_count(0)
        return

    countdown = page.locator(".dashboard-price-card .offer-countdown[data-offer-live]").first
    expect(countdown).to_be_visible(timeout=5_000)
    expect(countdown).to_contain_text("Launch price ends in")
    seconds = countdown.locator(".offer-countdown-part").last
    first_reading = seconds.inner_text()
    page.wait_for_timeout(1600)
    assert seconds.inner_text() != first_reading, "the billing countdown is not counting"
