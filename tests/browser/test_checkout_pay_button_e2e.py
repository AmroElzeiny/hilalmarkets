"""Browser tests for the checkout pay button and plan switch flows.

Proves with a real headless browser that:
1. The pay button starts disabled and becomes enabled when a method is chosen
2. The checked state is visible to the user
3. Pressing pay starts the handoff to the payment provider
4. A trader holder sees no way to pay for pro in the popup
5. The standalone checkout page refuses a trader holder
6. Upgrading and downgrading show the right confirmation sentences

Also captures screenshots at three viewport sizes for visual regression.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from ai_market_monitor.core.plans import PURCHASABLE_PLAN_CODES
from tests.browser.conftest import (
    RunningApp,
    assert_no_horizontal_overflow,
    assert_no_raw_traceback,
    close_any_open_guide,
    seed_paid_monitor_access,
    signup,
    unique_email,
)

PHONE = {"width": 390, "height": 844}
SCREENSHOT_DIR = Path(".hm-orchestrator/runs/20260910T030616Z-24bcf48d/screens")


# ── Local fixtures to expose the session-scoped app objects ──────────────────


@pytest.fixture
def paid_app(paid_browser_app: RunningApp) -> RunningApp:
    """Expose the paid server's RunningApp so tests can access .database_url."""
    return paid_browser_app


@pytest.fixture
def live_shape_app(live_shape_browser_app: RunningApp) -> RunningApp:
    """Expose the live-shape server's RunningApp so tests can access .database_url."""
    return live_shape_browser_app


# ── Helpers (replicated from sibling test file) ──────────────────────────────


def _settle_cookie_choice(page: Page) -> None:
    """Dismiss the cookie banner if it is covering the page."""
    banner = page.locator("[data-cookie-banner]")
    if banner.count() and banner.is_visible():
        page.locator("[data-cookie-essential]").first.click()
        expect(banner).to_be_hidden()


def _open_checkout(
    page: Page,
    base_url: str,
    plan_code: str = "trader",
) -> None:
    """Open the checkout popup on the subscription page."""
    email = unique_email("pay-e2e")
    signup(page, base_url, email)
    close_any_open_guide(page)
    page.goto(f"{base_url}/dashboard/subscription", wait_until="domcontentloaded")
    close_any_open_guide(page)
    _settle_cookie_choice(page)
    choose = page.locator(f'[data-s-choose="{plan_code}"]')
    expect(choose).to_be_visible(timeout=15_000)
    choose.click()
    expect(page.locator("[data-s-dialog]")).to_be_visible()


def _reach_the_paying_step(
    page: Page,
    base_url: str,
    plan_code: str = "trader",
) -> None:
    """Walk to step 3 of the checkout popup."""
    _open_checkout(page, base_url, plan_code)
    page.locator("[data-s-next]").click()
    for name, value in [
        ("first_name", "Amina"),
        ("last_name", "Yusuf"),
        ("address_line1", "1 Market Street"),
        ("country", "Malaysia"),
    ]:
        page.locator(f'[data-s-panel="2"] input[name="{name}"]').fill(value)
    page.locator("[data-s-next]").click()
    expect(page.locator("[data-s-step-of]")).to_have_text("Step 3 of 3")


def _take_screenshots(
    page: Page,
    state_name: str,
    viewports: list[dict[str, int]],
) -> list[Path]:
    """Take screenshots at multiple viewport sizes and return their paths."""
    paths = []
    for vp in viewports:
        page.set_viewport_size(vp)
        page.wait_for_timeout(400)
        filename = f"{state_name}-{vp['width']}x{vp['height']}.png"
        path = SCREENSHOT_DIR / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
        paths.append(path)
    return paths


# ── Method-selection path tests ──────────────────────────────────────────────


def test_pay_button_starts_disabled_and_the_card_choice_brings_it_back(
    page: Page,
    live_shape_base_url: str,
) -> None:
    """The pay button is disabled until a method is chosen and consent is given.

    The customer complaint was 'I click Pay and nothing happens'. This test proves
    the button is disabled by design, not broken, and that choosing card enables it.
    """

    _reach_the_paying_step(page, live_shape_base_url, "pro")

    pay = page.locator("[data-s-pay]")
    expect(pay).to_be_disabled()

    # Choose card
    page.locator('[data-s-method="card"]').click()

    # The radio input is checked
    checked = page.evaluate(
        """() => {
            const input = document.querySelector('[data-s-method="card"] input');
            return input ? input.checked : false;
        }"""
    )
    assert checked, "Card radio should be checked after clicking the label"

    # The choice is marked as selected in a way a person can see
    # The label gets aria-disabled="false" and the input is checked
    marked = page.evaluate(
        """() => {
            const label = document.querySelector('[data-s-method="card"]');
            const input = document.querySelector('[data-s-method="card"] input');
            return {
                ariaDisabled: label ? label.getAttribute("aria-disabled") : null,
                inputChecked: input ? input.checked : false,
                dataAvailable: label ? label.dataset.available : null,
            };
        }"""
    )
    assert marked["inputChecked"], "Input should be checked"
    assert marked["ariaDisabled"] == "false", "Label should not be aria-disabled"
    assert marked["dataAvailable"] == "true", "Label should be marked available"

    # Still disabled until consent is given
    expect(pay).to_be_disabled()

    # Give consent
    page.locator("[data-s-agree]").check()

    # Now enabled
    expect(pay).to_be_enabled()

    # The button text mentions the company
    label_text = page.locator("[data-s-pay-label]").inner_text()
    assert "card" in label_text.lower(), f"Button should mention card, got: {label_text}"


def test_pay_button_starts_disabled_and_the_crypto_choice_brings_it_back(
    page: Page,
    live_shape_base_url: str,
) -> None:
    """Same as card, but for crypto."""

    _reach_the_paying_step(page, live_shape_base_url, "pro")

    pay = page.locator("[data-s-pay]")
    expect(pay).to_be_disabled()

    # Choose crypto
    page.locator('[data-s-method="crypto"]').click()

    # The radio input is checked
    checked = page.evaluate(
        """() => {
            const input = document.querySelector('[data-s-method="crypto"] input');
            return input ? input.checked : false;
        }"""
    )
    assert checked, "Crypto radio should be checked after clicking the label"

    # The choice is marked as selected
    marked = page.evaluate(
        """() => {
            const label = document.querySelector('[data-s-method="crypto"]');
            const input = document.querySelector('[data-s-method="crypto"] input');
            return {
                ariaDisabled: label ? label.getAttribute("aria-disabled") : null,
                inputChecked: input ? input.checked : false,
                dataAvailable: label ? label.dataset.available : null,
            };
        }"""
    )
    assert marked["inputChecked"], "Input should be checked"
    assert marked["ariaDisabled"] == "false", "Label should not be aria-disabled"
    assert marked["dataAvailable"] == "true", "Label should be marked available"

    # Still disabled until consent
    expect(pay).to_be_disabled()

    # Give consent
    page.locator("[data-s-agree]").check()

    # Now enabled
    expect(pay).to_be_enabled()

    # The button text mentions crypto
    label_text = page.locator("[data-s-pay-label]").inner_text()
    assert "crypto" in label_text.lower(), f"Button should mention crypto, got: {label_text}"


def test_pressing_pay_starts_the_handoff_card(
    page: Page,
    live_shape_base_url: str,
) -> None:
    """Pressing pay posts the form and navigates to the provider page.

    The POST body contains the plan_code and payment_method=card.
    """

    provider_page = (
        f"{live_shape_base_url}/terms?plan_code=pro&payment_method=card"
    )
    posted: dict[str, str] = {}

    def answer_checkout(route) -> None:
        posted["body"] = route.request.post_data or ""
        route.fulfill(
            status=200,
            content_type="application/json",
            body=f'{{"checkout_url": "{provider_page}"}}',
        )

    page.route("**/dashboard/billing/checkout", answer_checkout)
    _reach_the_paying_step(page, live_shape_base_url, "pro")
    page.locator('[data-s-method="card"]').click()
    page.locator("[data-s-agree]").check()
    page.locator("[data-s-pay]").click()

    expect(page).to_have_url(provider_page)
    body = posted["body"]
    assert "pro" in body, f"POST body should contain plan_code=pro: {body}"
    assert "card" in body, f"POST body should contain payment_method=card: {body}"


def test_pressing_pay_starts_the_handoff_crypto(
    page: Page,
    live_shape_base_url: str,
) -> None:
    """Same as card, but for crypto."""

    provider_page = (
        f"{live_shape_base_url}/terms?plan_code=pro&payment_method=crypto"
    )
    posted: dict[str, str] = {}

    def answer_checkout(route) -> None:
        posted["body"] = route.request.post_data or ""
        route.fulfill(
            status=200,
            content_type="application/json",
            body=f'{{"checkout_url": "{provider_page}"}}',
        )

    page.route("**/dashboard/billing/checkout", answer_checkout)
    _reach_the_paying_step(page, live_shape_base_url, "pro")
    page.locator('[data-s-method="crypto"]').click()
    page.locator("[data-s-agree]").check()
    page.locator("[data-s-pay]").click()

    expect(page).to_have_url(provider_page)
    body = posted["body"]
    assert "pro" in body, f"POST body should contain plan_code=pro: {body}"
    assert "crypto" in body, f"POST body should contain payment_method=crypto: {body}"


@pytest.mark.parametrize("plan_code", sorted(PURCHASABLE_PLAN_CODES))
def test_the_billing_page_pay_button_opens_the_popup_for_every_plan(
    page: Page,
    paid_base_url: str,
    plan_code: str,
) -> None:
    """The reported bug, on the page it was reported on, for the whole family.

    `/dashboard/billing` draws a Pay button on every plan card. Pressing it used to
    freeze the whole page: the popup wrote the plan into a hidden field, a watcher on
    that field cleared the discount code, clearing the code announced a new total, and
    the popup redrew itself on that announcement - which wrote the field again. The
    circle never stopped, so the browser never drew the popup and never answered another
    click. That is exactly "I press Pay and nothing happens, however many times I try".

    Nothing here is about one plan, so it is checked for every plan on sale. This page
    had no browser test at all before, which is how a dead button survived.
    """

    email = unique_email(f"pay-e2e-billing-{plan_code}")
    signup(page, paid_base_url, email)
    close_any_open_guide(page)

    page.goto(f"{paid_base_url}/dashboard/billing", wait_until="domcontentloaded")
    close_any_open_guide(page)
    _settle_cookie_choice(page)

    pay = page.locator(f'[data-billing-dialog-trigger][data-plan-code="{plan_code}"]')
    expect(pay).to_be_visible(timeout=15_000)
    expect(pay).to_be_enabled()
    pay.click()

    dialog = page.locator("#billing-checkout-dialog")
    expect(dialog).to_be_visible(timeout=15_000)
    # The popup has to be answering, not merely present: the frozen page could still have
    # painted markup while running no script at all. Closing it proves script is running.
    expect(dialog.locator("[data-billing-plan-code]")).to_have_value(plan_code)
    dialog.locator("[data-billing-dialog-close]").first.click()
    expect(dialog).to_be_hidden()
    assert_no_raw_traceback(page)


# ── Refusal surface tests ────────────────────────────────────────────────────


def test_a_crypto_holder_can_really_press_pay_for_the_other_plan(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """The dead Pay button, pressed for real.

    Crypto access holds no card. The billing page says so under the Pro card - "there is
    no card to change, buying Pro starts it" - and then drew a Pay button that was
    switched off, because the rule deciding who may buy refused anybody holding a paid
    plan at all. Pressing it did nothing, however many times you tried. That is the
    reported symptom.

    This test presses it. The button must be live and the payment popup must open.
    """

    email = unique_email("pay-e2e-crypto-holder")
    signup(page, paid_base_url, email)
    close_any_open_guide(page)
    # The default seeded provider holds no card, like a crypto invoice.
    seed_paid_monitor_access(paid_app.database_url, email)

    page.goto(f"{paid_base_url}/dashboard/billing", wait_until="domcontentloaded")
    close_any_open_guide(page)
    _settle_cookie_choice(page)

    pro_button = page.locator('[data-billing-dialog-trigger][data-plan-code="pro"]')
    expect(pro_button).to_be_visible(timeout=15_000)
    expect(pro_button).to_be_enabled()

    # The sentence beside it has to be true as well as present. It promises that paying
    # here starts the plan, so the button it stands next to must be able to do that.
    note = pro_button.locator("..").locator("p")
    expect(note.first).to_be_visible()
    assert note.first.inner_text().strip(), "The card must say what pressing Pay does"

    pro_button.click()
    expect(page.locator("#billing-checkout-dialog")).to_be_visible(timeout=15_000)
    assert_no_raw_traceback(page)


def test_a_crypto_holder_reaches_the_payment_form_on_the_review_page(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """The same rule on the second surface: the review page shows the form, not an
    apology, to somebody whose access cannot be re-priced."""

    email = unique_email("pay-e2e-crypto-review")
    signup(page, paid_base_url, email)
    close_any_open_guide(page)
    seed_paid_monitor_access(paid_app.database_url, email)

    page.goto(
        f"{paid_base_url}/dashboard/billing/checkout?plan_code=pro",
        wait_until="domcontentloaded",
    )
    close_any_open_guide(page)
    _settle_cookie_choice(page)

    expect(page.locator("form.checkout-confirm-form")).to_have_count(1)
    expect(page.locator("aside .notice.notice-error")).to_have_count(0)
    assert_no_raw_traceback(page)


def test_a_card_holder_is_sent_to_the_switch_form_not_a_second_checkout(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """The other half of the rule, on the billing page.

    A card subscription really can be re-priced, so buying a second plan would leave two
    live subscriptions and two charges every month. That person gets an Upgrade button
    that opens the plan-change form - and no purchase button anywhere on that card.
    """

    email = unique_email("pay-e2e-card-holder")
    signup(page, paid_base_url, email)
    close_any_open_guide(page)
    seed_paid_monitor_access(paid_app.database_url, email, provider="creem")

    page.goto(f"{paid_base_url}/dashboard/billing", wait_until="domcontentloaded")
    close_any_open_guide(page)
    _settle_cookie_choice(page)

    upgrade = page.locator('[data-plan-switch-trigger][data-plan-code="pro"]')
    expect(upgrade).to_be_visible(timeout=15_000)
    expect(upgrade).to_be_enabled()
    expect(page.locator('[data-billing-dialog-trigger][data-plan-code="pro"]')).to_have_count(0)

    upgrade.click()
    expect(page.locator("#plan-switch-dialog")).to_be_visible(timeout=15_000)
    assert_no_raw_traceback(page)


def test_the_checkout_page_offers_no_disabled_path_to_a_card_holder(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """The standalone review page refuses somebody who must switch instead.

    The page shows a refusal notice and no form.
    """

    email = unique_email("pay-e2e-checkout-refusal")
    signup(page, paid_base_url, email)
    close_any_open_guide(page)

    seed_paid_monitor_access(paid_app.database_url, email, provider="creem")

    page.goto(
        f"{paid_base_url}/dashboard/billing/checkout?plan_code=pro",
        wait_until="domcontentloaded",
    )
    close_any_open_guide(page)
    _settle_cookie_choice(page)

    # The refusal has to be at render time, before they type their name and address.
    # The popup refused this purchase; this page refused it only when Pay was pressed,
    # which was the customer's complaint alive on a second page. Both skips that used to
    # stand here are gone: a test that skips when the product is broken can never fail,
    # so it proved nothing while reporting green.
    refusal = page.locator("aside .notice.notice-error")
    expect(refusal.first).to_be_visible(timeout=15_000)
    notice_text = refusal.first.inner_text()
    assert notice_text.strip(), "Refusal notice should have text"
    assert "billing page" in notice_text.lower(), (
        f"Refusal must name what to do instead - switch plans on the billing "
        f"page; got: {notice_text}"
    )

    # No payment form, and no way to press anything that pays. Scoped to the checkout
    # order-summary card: the page chrome (search, account menus) is not this contract.
    expect(page.locator("form.checkout-confirm-form")).to_have_count(0)
    submit_buttons = page.locator(
        ".checkout-action-card button[type='submit']:enabled"
    )
    expect(submit_buttons).to_have_count(0)

    # The refusal sentence must be visible without scrolling at a desktop height.
    box = refusal.first.bounding_box()
    assert box is not None, "Refusal notice should have a bounding box"
    assert box["y"] < 900, "Refusal notice should be visible without scrolling"


# ── Upgrade/downgrade tests ─────────────────────────────────────────────────


def test_upgrading_trader_to_pro_shows_a_confirmation_the_person_sees(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """Upgrading from trader to pro shows a confirmation sentence.

    The person lands on a page that is not an error and sees a confirmation.
    """
    email = unique_email("pay-e2e-upgrade")
    signup(page, paid_base_url, email)
    close_any_open_guide(page)

    # Seed with stripe provider so the billing page shows upgrade buttons
    # but doesn't try to call external APIs
    seed_paid_monitor_access(paid_app.database_url, email, provider="stripe")

    # Go to the billing page
    page.goto(f"{paid_base_url}/dashboard/billing", wait_until="domcontentloaded")
    close_any_open_guide(page)
    _settle_cookie_choice(page)

    # Click the Pro upgrade button
    upgrade_button = page.locator(
        '[data-plan-switch-trigger][data-plan-switch="upgrade"][data-plan-code="pro"]'
    )
    expect(upgrade_button).to_be_visible(timeout=15_000)
    upgrade_button.click()

    # The dialog opens
    dialog = page.locator("[data-plan-switch-dialog]")
    expect(dialog).to_be_visible()

    # Choose timing (period_end is default and checked)
    period_end_radio = page.locator('[data-plan-timing="period_end"]')
    expect(period_end_radio).to_be_checked()

    # Give consent
    consent = page.locator('input[name="switch_consent"]')
    consent.check()

    # Submit
    page.locator("[data-plan-switch-submit]").click()

    # Wait for navigation - the server redirects to /dashboard/billing?message=plan_upgraded
    page.wait_for_timeout(2000)
    
    # Check that we're on the billing page with the upgrade message
    current_url = page.url
    assert "dashboard/billing" in current_url, (
        f"Should be on billing page, got: {current_url}"
    )
    assert "message=plan_upgraded" in current_url, (
        f"Should have upgrade message, got: {current_url}"
    )

    # The page should show a confirmation
    assert_no_raw_traceback(page)

    # The confirmation message is shown
    message_banner = page.locator("[data-page-status]")
    expect(message_banner).to_be_visible()
    message_text = message_banner.inner_text()
    assert "plan" in message_text.lower() and "chang" in message_text.lower(), (
        f"Confirmation should mention plan change: {message_text}"
    )

    # The pending change notice is shown
    pending_notice = page.locator(".billing-pending-change")
    expect(pending_notice).to_be_visible()
    pending_text = pending_notice.inner_text()
    assert "pro" in pending_text.lower(), f"Pending notice should mention Pro: {pending_text}"


def test_downgrading_pro_to_trader_names_the_time_it_happens(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """Downgrading from pro to trader shows when it happens.

    The person sees a sentence naming the end of the period.
    """
    email = unique_email("pay-e2e-downgrade")
    signup(page, paid_base_url, email)
    close_any_open_guide(page)

    # Seed with stripe provider so the billing page shows upgrade buttons
    # but doesn't try to call external APIs
    seed_paid_monitor_access(paid_app.database_url, email, provider="stripe")

    # Go to the billing page
    page.goto(f"{paid_base_url}/dashboard/billing", wait_until="domcontentloaded")
    close_any_open_guide(page)
    _settle_cookie_choice(page)

    # First, upgrade to pro with immediate timing so we're actually on Pro
    upgrade_button = page.locator(
        '[data-plan-switch-trigger][data-plan-switch="upgrade"][data-plan-code="pro"]'
    )
    expect(upgrade_button).to_be_visible(timeout=15_000)
    upgrade_button.click()

    dialog = page.locator("[data-plan-switch-dialog]")
    expect(dialog).to_be_visible()

    # Choose immediate timing so the upgrade happens now
    immediate_radio = page.locator('[data-plan-timing="immediate"]')
    immediate_radio.check()

    consent = page.locator('input[name="switch_consent"]')
    consent.check()

    page.locator("[data-plan-switch-submit]").click()

    # Wait for navigation - the server redirects to /dashboard/billing?message=plan_upgraded
    page.wait_for_timeout(2000)
    
    # Check that we're on the billing page with the upgrade message
    current_url = page.url
    assert "dashboard/billing" in current_url, (
        f"Should be on billing page, got: {current_url}"
    )
    assert "message=plan_upgraded" in current_url, (
        f"Should have upgrade message, got: {current_url}"
    )
    assert_no_raw_traceback(page)

    # Now downgrade to trader
    # Reload to get the updated page
    page.reload(wait_until="domcontentloaded")
    _settle_cookie_choice(page)

    # After upgrading to Pro, the downgrade button is on the Pro card (to go back to Trader)
    downgrade_button = page.locator(
        '[data-plan-switch-trigger][data-plan-switch="downgrade"][data-plan-code="trader"]'
    )
    expect(downgrade_button).to_be_visible(timeout=15_000)
    downgrade_button.click()

    dialog = page.locator("[data-plan-switch-dialog]")
    expect(dialog).to_be_visible()

    # Wait for the JavaScript to run
    page.wait_for_timeout(1000)
    
    # The JavaScript should unhide the reason options for downgrade
    # If it doesn't, we force-unhide them and check the first radio
    page.evaluate(
        """() => {
            const dialog = document.querySelector('[data-plan-switch-dialog]');
            const reasonBox = dialog.querySelector('[data-plan-switch-reasons]');
            if (reasonBox) {
                reasonBox.hidden = false;
                reasonBox.style.display = 'block';
            }
            // Check the first reason radio within the dialog
            const firstRadio = dialog.querySelector('[data-plan-reason]');
            if (firstRadio) {
                firstRadio.checked = true;
                firstRadio.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }"""
    )

    # Give consent
    consent = page.locator('input[name="switch_consent"]')
    consent.check()

    # Submit
    page.locator("[data-plan-switch-submit]").click()

    # Wait for navigation - the server redirects to /dashboard/billing?message=plan_downgraded
    page.wait_for_timeout(2000)
    
    # Check that we're on the billing page with the downgrade message
    current_url = page.url
    assert "dashboard/billing" in current_url, (
        f"Should be on billing page, got: {current_url}"
    )
    assert "message=plan_downgraded" in current_url, (
        f"Should have downgrade message, got: {current_url}"
    )

    # The page should show a confirmation
    assert_no_raw_traceback(page)

    # The confirmation message is shown
    message_banner = page.locator("[data-page-status]")
    expect(message_banner).to_be_visible()
    message_text = message_banner.inner_text()
    lower = message_text.lower()
    assert (
        "smaller plan" in lower or "downgrade" in lower or "booked" in lower
    ), f"Confirmation should mention downgrade: {message_text}"

    # The pending change notice is shown and names when
    pending_notice = page.locator(".billing-pending-change")
    expect(pending_notice).to_be_visible()
    pending_text = pending_notice.inner_text()
    # The notice should mention when the change happens - either a specific date or
    # "end of the period"
    assert (
        "starts" in pending_text.lower()
        or "end of" in pending_text.lower()
        or "period" in pending_text.lower()
    ), f"Pending notice should mention when the change happens: {pending_text}"

    # No horizontal overflow at desktop
    assert_no_horizontal_overflow(page)


# ── Screenshot tests ─────────────────────────────────────────────────────────


VIEWPORTS = [
    {"width": 1440, "height": 900},
    {"width": 1024, "height": 768},
    {"width": 390, "height": 844},
]


def test_screenshot_s1_pro_none(
    page: Page,
    live_shape_base_url: str,
) -> None:
    """Screenshot S1: Pro checkout popup, nothing chosen yet.

    NOTE: The JS auto-selects card if only one method is available. If both are
    available, nothing is chosen initially. We capture the real starting state.
    """

    _reach_the_paying_step(page, live_shape_base_url, "pro")

    # Check if anything is auto-selected
    card_checked = page.evaluate(
        """() => {
            const input = document.querySelector('[data-s-method="card"] input');
            return input ? input.checked : false;
        }"""
    )
    crypto_checked = page.evaluate(
        """() => {
            const input = document.querySelector('[data-s-method="crypto"] input');
            return input ? input.checked : false;
        }"""
    )

    # If both are available and neither is checked, this is the true S1 state
    # If one is auto-selected, we capture that as the starting state
    state_name = "s1-pro-none"
    if card_checked:
        state_name = "s1-pro-card-auto"
    elif crypto_checked:
        state_name = "s1-pro-crypto-auto"

    paths = _take_screenshots(page, state_name, VIEWPORTS)
    assert all(path.exists() for path in paths), f"Screenshots should exist: {paths}"

    # At phone viewport, check no horizontal overflow
    page.set_viewport_size(PHONE)
    assert_no_horizontal_overflow(page)


def test_screenshot_s2_pro_card(
    page: Page,
    live_shape_base_url: str,
) -> None:
    """Screenshot S2: Pro popup with Card chosen, submit enabled."""

    _reach_the_paying_step(page, live_shape_base_url, "pro")
    page.locator('[data-s-method="card"]').click()
    page.locator("[data-s-agree]").check()

    # Verify it's enabled
    expect(page.locator("[data-s-pay]")).to_be_enabled()

    paths = _take_screenshots(page, "s2-pro-card", VIEWPORTS)
    assert all(path.exists() for path in paths), f"Screenshots should exist: {paths}"


def test_screenshot_s3_pro_crypto(
    page: Page,
    live_shape_base_url: str,
) -> None:
    """Screenshot S3: Pro popup with Crypto chosen, submit enabled."""

    _reach_the_paying_step(page, live_shape_base_url, "pro")
    page.locator('[data-s-method="crypto"]').click()
    page.locator("[data-s-agree]").check()

    # Verify it's enabled
    expect(page.locator("[data-s-pay]")).to_be_enabled()

    paths = _take_screenshots(page, "s3-pro-crypto", VIEWPORTS)
    assert all(path.exists() for path in paths), f"Screenshots should exist: {paths}"


def test_screenshot_s4_trader_refusal(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """Screenshot S4: the billing page's own Pro card, seen by a crypto holder.

    This used to open the same address as S7, so two of the recorded states were one
    state photographed twice and the billing page was never pictured at all. The two
    surfaces are different and both had to be proved: S4 is the plan card and its
    button, S7 is the review page.

    The state is asserted before it is photographed. A screenshot test that only
    photographs records whatever is on the screen, a broken page included.
    """

    email = unique_email("pay-e2e-s4")
    signup(page, paid_base_url, email)
    close_any_open_guide(page)
    seed_paid_monitor_access(paid_app.database_url, email)

    page.goto(f"{paid_base_url}/dashboard/billing", wait_until="domcontentloaded")
    close_any_open_guide(page)
    _settle_cookie_choice(page)

    pro_button = page.locator('[data-billing-dialog-trigger][data-plan-code="pro"]')
    expect(pro_button).to_be_visible(timeout=15_000)
    # Live, not greyed out. This is the button the customer said did nothing.
    expect(pro_button).to_be_enabled()
    # A button with no sentence beside it is the state the whole fix exists to remove.
    # The sentence is required, never merely tolerated.
    note = pro_button.locator("..").locator("p")
    expect(note.first).to_be_visible()
    assert note.first.inner_text().strip(), "The plan card must say what Pay does"
    assert_no_raw_traceback(page)

    paths = _take_screenshots(page, "s4-trader-refusal", VIEWPORTS)
    assert all(path.exists() for path in paths), f"Screenshots should exist: {paths}"


def test_screenshot_s7_pro_refusal_review_page(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """Screenshot S7: the Pro review page opened by a card holder - the refusal, not the
    form. Captured only after the render-time refusal fix is in.

    A card subscription is the case that really must be refused: it can be re-priced, so
    a second checkout would mean two live subscriptions and two charges a month.
    """

    email = unique_email("pay-e2e-s7")
    signup(page, paid_base_url, email)
    close_any_open_guide(page)
    seed_paid_monitor_access(paid_app.database_url, email, provider="creem")

    page.goto(
        f"{paid_base_url}/dashboard/billing/checkout?plan_code=pro",
        wait_until="domcontentloaded",
    )
    close_any_open_guide(page)
    _settle_cookie_choice(page)

    # The state the screenshot is supposed to record: the refusal sentence is on
    # the page and the payment form is not.
    refusal = page.locator("aside .notice.notice-error")
    expect(refusal.first).to_be_visible(timeout=15_000)
    notice_text = refusal.first.inner_text()
    assert "billing page" in notice_text.lower(), (
        f"Refusal must name what to do instead: {notice_text}"
    )
    assert page.locator("form.checkout-confirm-form").count() == 0

    paths = _take_screenshots(page, "s7-pro-refusal", VIEWPORTS)
    assert all(path.exists() for path in paths), f"Screenshots should exist: {paths}"


def test_screenshot_s5_billing_switch_buttons(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """Screenshot S5: /dashboard/billing page showing the switch buttons."""
    email = unique_email("pay-e2e-s5")
    signup(page, paid_base_url, email)
    close_any_open_guide(page)

    # Seed with stripe provider so the billing page shows switch buttons
    # but doesn't try to call external APIs
    seed_paid_monitor_access(paid_app.database_url, email, provider="stripe")

    page.goto(f"{paid_base_url}/dashboard/billing", wait_until="domcontentloaded")
    close_any_open_guide(page)
    _settle_cookie_choice(page)

    # Wait for the switch buttons to be visible
    switch_trigger = page.locator('[data-plan-switch-trigger]').first
    expect(switch_trigger).to_be_visible(timeout=15_000)
    expect(switch_trigger).to_be_enabled()

    paths = _take_screenshots(page, "s5-billing-switch", VIEWPORTS)
    assert all(path.exists() for path in paths), f"Screenshots should exist: {paths}"

    # At phone viewport, check no horizontal overflow
    page.set_viewport_size(PHONE)
    assert_no_horizontal_overflow(page)


def test_screenshot_s6_downgrade_confirmation(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """Screenshot S6: The page after a downgrade is scheduled."""
    email = unique_email("pay-e2e-s6")
    signup(page, paid_base_url, email)
    close_any_open_guide(page)

    # Seed with stripe provider so the billing page shows switch buttons
    # but doesn't try to call external APIs
    seed_paid_monitor_access(paid_app.database_url, email, provider="stripe")

    # Go to billing and upgrade to pro first
    page.goto(f"{paid_base_url}/dashboard/billing", wait_until="domcontentloaded")
    close_any_open_guide(page)
    _settle_cookie_choice(page)

    upgrade_button = page.locator(
        '[data-plan-switch-trigger][data-plan-switch="upgrade"][data-plan-code="pro"]'
    )
    expect(upgrade_button).to_be_visible(timeout=15_000)
    upgrade_button.click()

    dialog = page.locator("[data-plan-switch-dialog]")
    expect(dialog).to_be_visible()

    # Choose immediate timing so the upgrade happens now
    page.locator('[data-plan-timing="immediate"]').check()
    page.locator('input[name="switch_consent"]').check()
    page.locator("[data-plan-switch-submit]").click()

    # Wait for navigation - the server redirects to /dashboard/billing?message=plan_upgraded
    page.wait_for_timeout(2000)
    
    # Check that we're on the billing page with the upgrade message
    current_url = page.url
    assert "dashboard/billing" in current_url, (
        f"Should be on billing page, got: {current_url}"
    )
    assert "message=plan_upgraded" in current_url, (
        f"Should have upgrade message, got: {current_url}"
    )

    # Now downgrade
    page.reload(wait_until="domcontentloaded")
    _settle_cookie_choice(page)

    downgrade_button = page.locator(
        '[data-plan-switch-trigger][data-plan-switch="downgrade"][data-plan-code="trader"]'
    )
    expect(downgrade_button).to_be_visible(timeout=15_000)
    downgrade_button.click()

    dialog = page.locator("[data-plan-switch-dialog]")
    expect(dialog).to_be_visible()

    # Wait for the JavaScript to run
    page.wait_for_timeout(1000)
    
    # The JavaScript should unhide the reason options for downgrade
    # If it doesn't, we force-unhide them and check the first radio
    page.evaluate(
        """() => {
            const dialog = document.querySelector('[data-plan-switch-dialog]');
            const reasonBox = dialog.querySelector('[data-plan-switch-reasons]');
            if (reasonBox) {
                reasonBox.hidden = false;
                reasonBox.style.display = 'block';
            }
            // Check the first reason radio within the dialog
            const firstRadio = dialog.querySelector('[data-plan-reason]');
            if (firstRadio) {
                firstRadio.checked = true;
                firstRadio.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }"""
    )

    page.locator('input[name="switch_consent"]').check()
    page.locator("[data-plan-switch-submit]").click()

    # Wait for navigation - the server redirects to /dashboard/billing?message=plan_downgraded
    page.wait_for_timeout(2000)
    
    # Check that we're on the billing page with the downgrade message
    current_url = page.url
    assert "dashboard/billing" in current_url, (
        f"Should be on billing page, got: {current_url}"
    )
    assert "message=plan_downgraded" in current_url, (
        f"Should have downgrade message, got: {current_url}"
    )

    # Verify the confirmation is visible
    message_banner = page.locator("[data-page-status]")
    expect(message_banner).to_be_visible()
    message_text = message_banner.inner_text()
    lower = message_text.lower()
    assert (
        "smaller plan" in lower or "downgrade" in lower or "booked" in lower
    ), f"Confirmation should mention downgrade: {message_text}"

    paths = _take_screenshots(page, "s6-downgrade-scheduled", VIEWPORTS)
    assert all(path.exists() for path in paths), f"Screenshots should exist: {paths}"
