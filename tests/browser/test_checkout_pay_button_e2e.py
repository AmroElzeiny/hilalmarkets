"""Browser tests for the checkout pay button and the move between paid plans.

Proves with a real headless browser that:
1. The pay button starts disabled and becomes enabled when a method is chosen
2. The checked state is visible to the user
3. Pressing pay starts the handoff to the payment provider
4. Anybody holding a paid plan - crypto or card - is offered a live Pay button for the
   other plan, and never a switch form (the owner's rule of 2026-09-10)
5. The tick box of that payment says what happens to the plan held now
6. Moving up and moving down each say, before payment, when the new plan starts

Also captures screenshots at three viewport sizes for visual regression.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Locator, Page, expect

from ai_market_monitor.core.plans import PURCHASABLE_PLAN_CODES, plan_name
from ai_market_monitor.services.plan_replacements import (
    CONSENT_PLAN_REPLACEMENT,
    manual_return_window_words,
)
from tests.browser.conftest import (
    RunningApp,
    assert_no_horizontal_overflow,
    assert_no_raw_traceback,
    close_any_open_guide,
    seed_paid_monitor_access,
    signup,
    unique_email,
)
from tests.support.contrast import contrast, flatten

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


#: The tiles on the checkout and billing pages that hold words in a box of their own. At
#: 1024px a plan tile's number and a payment card's note ran out of their boxes, and
#: nothing measured it: the page as a whole did not scroll sideways. The billing page's
#: plan facts cut their words short with "…" instead, which the same check catches, and
#: so did the payment history ("Card via St…", "Monthly Au…").
_TILES = (
    ".checkout-limit-grid article, .billing-method-card, .billing-current-meta > span, "
    ".billing-history-primary, .billing-history-facts > div"
)

_POKING_OUT = """selector => {
  const found = [];
  for (const tile of document.querySelectorAll(selector)) {
    const t = tile.getBoundingClientRect();
    if (t.width <= 1) continue;
    for (const el of [tile, ...tile.querySelectorAll('*')]) {
      const e = el.getBoundingClientRect();
      // Nothing drawn, or drawn 1px wide to be read aloud only.
      if (e.width <= 1 || e.height <= 1) continue;
      const wider = getComputedStyle(el).display !== 'inline'
        && el.scrollWidth > el.clientWidth + 1;
      const outside = e.left < t.left - 1 || e.right > t.right + 1;
      if (wider || outside) {
        found.push({
          tile: tile.className,
          element: el.tagName.toLowerCase(),
          text: (el.textContent || '').trim().slice(0, 60),
          wider,
          outside,
        });
      }
    }
  }
  return found;
}"""


def _renewal_tile(page: Page) -> Locator:
    """The billing page's own "Renewal" tile: what it says about the plan held."""
    return (
        page.locator(".billing-current-meta > span")
        .filter(has=page.locator("small", has_text=re.compile(r"^Renewal$")))
        .locator("strong")
    )


#: Every word drawn on a choice that cannot be made: its colour, the opacity it is drawn
#: at (its own times every parent's), and the first solid ground behind it.
_UNAVAILABLE_WORDS = """() => {
  const read = (css) => {
    const parts = css.match(/[\\d.]+/g).map(Number);
    return {
      hex: '#' + parts.slice(0, 3)
        .map(v => Math.round(v).toString(16).padStart(2, '0')).join(''),
      alpha: parts.length > 3 ? parts[3] : 1,
    };
  };
  const ground = (el) => {
    for (let node = el; node; node = node.parentElement) {
      const bg = read(getComputedStyle(node).backgroundColor);
      if (bg.alpha === 1) return bg.hex;
    }
    return '#ffffff';
  };
  const found = [];
  for (const el of document.querySelectorAll('.is-unavailable :is(strong, small, span)')) {
    const text = (el.textContent || '').trim();
    const box = el.getBoundingClientRect();
    if (!text || box.width <= 1 || box.height <= 1) continue;
    let opacity = 1;
    for (let node = el; node; node = node.parentElement) {
      opacity *= parseFloat(getComputedStyle(node).opacity);
    }
    const colour = read(getComputedStyle(el).color);
    found.push({
      text: text.slice(0, 60), colour: colour.hex, alpha: colour.alpha * opacity,
      ground: ground(el),
    });
  }
  return found;
}"""


def _assert_unavailable_words_are_readable(page: Page, where: str) -> None:
    """The words on a way of paying, or an interval, that cannot be chosen stay readable.

    They are the only place that says why it cannot be chosen, so V7's 4.5:1 applies.
    The card they sat on was faded to 55%, which took them to 2.2:1. Measured with
    `tests/support/contrast.py`, the one owner of this sum, never judged by eye.
    """

    for item in page.evaluate(_UNAVAILABLE_WORDS):
        seen = flatten(item["colour"], item["alpha"], item["ground"])
        ratio = contrast(seen, item["ground"])
        assert ratio >= 4.5, f"{where}: {item['text']!r} is {ratio:.2f}:1 ({item})"


def _take_screenshots(
    page: Page,
    state_name: str,
    viewports: list[dict[str, int]],
) -> list[Path]:
    """Take screenshots at multiple viewport sizes and return their paths.

    Before the pictures, every word on a choice that cannot be made must be readable; and
    before each one, no word may run out of the tile that holds it, at that size.
    """
    _assert_unavailable_words_are_readable(page, state_name)
    paths = []
    for vp in viewports:
        page.set_viewport_size(vp)
        page.wait_for_timeout(400)
        poking_out = page.evaluate(_POKING_OUT, _TILES)
        assert not poking_out, f"{state_name} at {vp['width']}px: {poking_out}"
        filename = f"{state_name}-{vp['width']}x{vp['height']}.png"
        path = SCREENSHOT_DIR / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
        paths.append(path)
    return paths


def _open_billing_as_holder(
    page: Page,
    base_url: str,
    app: RunningApp,
    *,
    tag: str,
    provider: str,
    plan_code: str = "trader",
) -> None:
    """Sign up, hold a paid plan, and open the billing page."""
    email = unique_email(tag)
    signup(page, base_url, email)
    close_any_open_guide(page)
    seed_paid_monitor_access(app.database_url, email, provider=provider, plan_code=plan_code)
    page.goto(f"{base_url}/dashboard/billing", wait_until="domcontentloaded")
    close_any_open_guide(page)
    _settle_cookie_choice(page)


def _pay_note(page: Page, plan_code: str):
    """The sentence straight under a plan card's Pay button."""
    pay = page.locator(f'[data-billing-dialog-trigger][data-plan-code="{plan_code}"]')
    return pay.locator("xpath=following-sibling::p[contains(@class,'dashboard-trial-note')][1]")


def _assert_the_move_is_a_payment(page: Page, target_code: str) -> None:
    """A live Pay button, the sentence under it, and no switch control on the page."""
    pay = page.locator(f'[data-billing-dialog-trigger][data-plan-code="{target_code}"]')
    expect(pay).to_be_visible(timeout=15_000)
    expect(pay).to_be_enabled()
    note = _pay_note(page, target_code)
    expect(note).to_be_visible()
    expect(note).to_contain_text(f"You pay the {plan_name(target_code)} price today")
    expect(note).to_contain_text("It starts only after that payment is confirmed")
    expect(note).to_contain_text("stays active if you leave or the payment fails")
    expect(note).to_contain_text(manual_return_window_words())
    expect(page.locator("[data-plan-switch-trigger]")).to_have_count(0)
    expect(page.locator("#plan-switch-dialog")).to_have_count(0)


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


def test_a_card_holder_is_offered_the_payment_popup_not_a_switch_form(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """The other half of the rule, on the billing page.

    A card subscription used to be re-priced through a switch form. That charged a
    prorated difference, which the owner ruled out on 2026-09-10: a different paid plan is
    bought at its full price on the normal payment page, and the plan held now ends only
    when that payment is confirmed. So a card holder gets the same live Pay button as
    anybody else, the tick box of the popup it opens says what happens to the plan they
    hold, and there is no switch control anywhere on the page.
    """

    _open_billing_as_holder(
        page, paid_base_url, paid_app, tag="pay-e2e-card-holder", provider="creem"
    )
    _assert_the_move_is_a_payment(page, "pro")

    page.locator('[data-billing-dialog-trigger][data-plan-code="pro"]').click()
    dialog = page.locator("#billing-checkout-dialog")
    expect(dialog).to_be_visible(timeout=15_000)
    expect(dialog.locator(".checkout-consent")).to_contain_text(CONSENT_PLAN_REPLACEMENT)
    assert_no_raw_traceback(page)


def test_the_checkout_page_offers_no_disabled_path_to_a_card_holder(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """The standalone review page gives a card holder the real payment form.

    It used to refuse a card holder, because a card subscription was re-priced through a
    switch form instead. Since 2026-09-10 a different paid plan is bought here at its full
    price, so the page shows the form - and the tick box on it says, in the owner's
    words, what happens to the plan the person holds before they agree to anything.
    """

    email = unique_email("pay-e2e-checkout-card-holder")
    signup(page, paid_base_url, email)
    close_any_open_guide(page)
    seed_paid_monitor_access(paid_app.database_url, email, provider="creem")

    page.goto(
        f"{paid_base_url}/dashboard/billing/checkout?plan_code=pro",
        wait_until="domcontentloaded",
    )
    close_any_open_guide(page)
    _settle_cookie_choice(page)

    form = page.locator("form.checkout-confirm-form")
    expect(form).to_have_count(1)
    expect(page.locator("aside .notice.notice-error")).to_have_count(0)
    expect(form.locator(".checkout-consent")).to_contain_text(CONSENT_PLAN_REPLACEMENT)
    expect(form.locator("button[type='submit']")).to_be_enabled()
    assert_no_raw_traceback(page)


# ── Moving up and moving down ────────────────────────────────────────────────


def test_upgrading_trader_to_pro_says_what_the_payment_does_before_it_is_taken(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """Moving up, as the person sees it before any money is taken.

    This used to walk the switch form to a "plan upgraded" notice. There is no switch form
    since 2026-09-10. The Pro card carries a Pay button, and the sentence under it says
    the full price is taken today, that Pro starts only once that payment is confirmed,
    that Plus keeps working if they leave or the payment fails, and that a person sends
    back the value of the unused time within the window. The popup says the same in the
    tick box.
    """

    _open_billing_as_holder(
        page, paid_base_url, paid_app, tag="pay-e2e-upgrade", provider="stripe"
    )
    _assert_the_move_is_a_payment(page, "pro")
    # Nothing is booked: a payment page is the only way, so nothing changes until it is paid.
    expect(page.locator(".billing-pending-change")).to_have_count(0)

    page.locator('[data-billing-dialog-trigger][data-plan-code="pro"]').click()
    dialog = page.locator("#billing-checkout-dialog")
    expect(dialog).to_be_visible(timeout=15_000)
    expect(dialog.locator(".checkout-consent")).to_contain_text(CONSENT_PLAN_REPLACEMENT)
    assert_no_raw_traceback(page)


def test_downgrading_pro_to_trader_names_the_time_it_happens(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """Moving down names when it happens: the day its payment is confirmed.

    This used to book the smaller plan for the end of the paid period through the switch
    form. Since 2026-09-10 a smaller plan is bought like any other, so the Plus card on a
    Pro holder's page says that Plus starts only once its payment is confirmed, and that
    the unused Pro time is sent back by a person within the window.
    """

    _open_billing_as_holder(
        page,
        paid_base_url,
        paid_app,
        tag="pay-e2e-downgrade",
        provider="stripe",
        plan_code="pro",
    )
    _assert_the_move_is_a_payment(page, "trader")
    expect(page.locator(".billing-pending-change")).to_have_count(0)
    assert_no_raw_traceback(page)
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


def test_screenshot_s4_billing_pro_card_for_a_crypto_holder(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """Screenshot S4: the billing page's own Pro card, seen by a crypto holder.

    This used to open the same address as S7, so two of the recorded states were one
    state photographed twice and the billing page was never pictured at all. The two
    surfaces are different and both had to be proved: S4 is the plan card and its
    button, S7 is the review page.

    The file name says what is photographed: a live Pay button and its sentence. The
    older S4 shots ("s4-trader-refusal-*") are stale — they were named for a refusal
    this state no longer shows, and were taken at the S7 address.

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
    # The plan held was not bought by card, so it must not promise a card renewal. The
    # tile used to be decided by the company this server sells through, not by the plan
    # held; S5 and S6 below are the card plans it got wrong.
    expect(_renewal_tile(page)).to_have_text("Manual 30-day renewal")

    paths = _take_screenshots(page, "s4-billing-pro-card-crypto-holder", VIEWPORTS)
    assert all(path.exists() for path in paths), f"Screenshots should exist: {paths}"


def test_screenshot_s7_pro_review_page_for_a_card_holder(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """Screenshot S7: the Pro review page opened by a card holder - the payment form,
    with the plan-replacement sentence in its tick box.

    S7 used to picture a refusal on this page. That refusal ended on 2026-09-10, so the
    older S7 shots ("s7-pro-refusal-*") are stale; this state is photographed under its
    own name so the two can never be mistaken for each other.
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

    # The state the screenshot is supposed to record: the form, its tick box carrying the
    # owner's sentence, and no refusal.
    form = page.locator("form.checkout-confirm-form")
    expect(form).to_have_count(1)
    expect(form.locator(".checkout-consent")).to_contain_text(CONSENT_PLAN_REPLACEMENT)
    expect(page.locator("aside .notice.notice-error")).to_have_count(0)

    paths = _take_screenshots(page, "s7-pro-review-card-holder", VIEWPORTS)
    assert all(path.exists() for path in paths), f"Screenshots should exist: {paths}"


def test_screenshot_s5_billing_pay_buttons_for_a_card_holder(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """Screenshot S5: /dashboard/billing for a card holder - a Pay button and its
    sentence on the other plan, and no switch buttons (there are none since 2026-09-10).
    """

    _open_billing_as_holder(
        page, paid_base_url, paid_app, tag="pay-e2e-s5", provider="stripe"
    )
    _assert_the_move_is_a_payment(page, "pro")
    # A monthly card plan that still renews, named as one.
    expect(_renewal_tile(page)).to_have_text("Automatic renewal, every month")

    paths = _take_screenshots(page, "s5-billing-pay-for-card-holder", VIEWPORTS)
    assert all(path.exists() for path in paths), f"Screenshots should exist: {paths}"

    # At phone viewport, check no horizontal overflow
    page.set_viewport_size(PHONE)
    assert_no_horizontal_overflow(page)


def test_screenshot_s6_downgrade_pay_note(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """Screenshot S6: a Pro holder's billing page - the Plus card offers a full-price
    payment and says Plus starts once that payment is confirmed. It used to picture a
    downgrade booked through the switch form, which nobody can book now.
    """

    _open_billing_as_holder(
        page,
        paid_base_url,
        paid_app,
        tag="pay-e2e-s6",
        provider="stripe",
        plan_code="pro",
    )
    _assert_the_move_is_a_payment(page, "trader")
    expect(_renewal_tile(page)).to_have_text("Automatic renewal, every month")

    paths = _take_screenshots(page, "s6-downgrade-pay-note", VIEWPORTS)
    assert all(path.exists() for path in paths), f"Screenshots should exist: {paths}"


# ── Visual-defect reproducers (run 20260912T152825Z-d6d10e2a) ────────────────


_METHOD_NOTES = [
    ("card", "Handled by a test payment page."),
    ("crypto", "Paying by crypto is switched off just now."),
]


_MEASURE_METHOD_NOTE = """args => {
  const el = document.querySelector(args.selector);
  if (!el) return {missing: true};
  const text = el.textContent.trim();
  const words = text.split(/\\s+/).filter(Boolean);
  const style = getComputedStyle(el);
  const testSpan = document.createElement('span');
  testSpan.style.cssText = (
    'position:absolute;visibility:hidden;white-space:nowrap;' +
    'font:' + style.font + ';' +
    'letter-spacing:' + style.letterSpacing + ';'
  );
  document.body.appendChild(testSpan);
  let maxWordWidth = 0;
  for (const word of words) {
    testSpan.textContent = word;
    maxWordWidth = Math.max(maxWordWidth, testSpan.getBoundingClientRect().width);
  }
  testSpan.remove();
  const rects = el.getClientRects();
  return {
    text,
    expected: args.expected,
    words,
    clientWidth: el.clientWidth,
    maxWordWidth: Math.ceil(maxWordWidth),
    lineCount: rects.length,
    wordCount: words.length,
  };
}"""


@pytest.mark.parametrize("method, expected_note", _METHOD_NOTES)
def test_review_page_method_notes_wrap_at_word_boundaries(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
    method: str,
    expected_note: str,
) -> None:
    """The sentence under Card/Crypto must not break mid-word.

    `overflow-wrap: anywhere` let the grid column collapse to one-character width,
    so the note rendered as "Han dled by a test pay men t pag e". `break-word`
    keeps the column wide enough for the longest word and only breaks a word when it
    genuinely does not fit.
    """

    email = unique_email(f"pay-e2e-method-note-{method}")
    signup(page, paid_base_url, email)
    close_any_open_guide(page)
    seed_paid_monitor_access(paid_app.database_url, email, provider="creem")

    page.goto(
        f"{paid_base_url}/dashboard/billing/checkout?plan_code=pro",
        wait_until="domcontentloaded",
    )
    close_any_open_guide(page)
    _settle_cookie_choice(page)

    for vp in VIEWPORTS:
        page.set_viewport_size(vp)
        page.wait_for_timeout(400)
        selector = f'input[name="payment_method"][value="{method}"] + .billing-method-card small'
        measured = page.evaluate(
            _MEASURE_METHOD_NOTE, {"selector": selector, "expected": expected_note}
        )
        assert not measured.get("missing"), f"{method} note not found at {vp['width']}px"
        assert measured["text"] == expected_note, (
            f"{method} note text mismatch at {vp['width']}px: {measured}"
        )
        # The element must be at least as wide as its longest word, otherwise a word
        # cannot fit unbroken.
        assert measured["clientWidth"] >= measured["maxWordWidth"], (
            f"{method} note column too narrow at {vp['width']}px: {measured}"
        )
        # A broken word would create one rect per character; normal wrapping has no more
        # lines than words.
        assert measured["lineCount"] <= measured["wordCount"], (
            f"{method} note appears broken mid-word at {vp['width']}px: "
            f"lineCount={measured['lineCount']} wordCount={measured['wordCount']}"
        )


def test_review_page_launch_price_does_not_overlap_countdown(
    page: Page,
    paid_base_url: str,
    paid_app: RunningApp,
) -> None:
    """The launch-price paragraph must sit above the countdown box, not under it.

    The countdown had a negative top margin that pulled it up over the paragraph on
    the review page at 1440x900.
    """

    email = unique_email("pay-e2e-launch-overlap")
    signup(page, paid_base_url, email)
    close_any_open_guide(page)
    seed_paid_monitor_access(paid_app.database_url, email, provider="creem")

    page.goto(
        f"{paid_base_url}/dashboard/billing/checkout?plan_code=pro",
        wait_until="domcontentloaded",
    )
    close_any_open_guide(page)
    _settle_cookie_choice(page)

    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(400)

    boxes = page.evaluate("""
        () => {
          const price = document.querySelector('.checkout-action-card .checkout-small');
          const countdown = document.querySelector('.checkout-action-card .offer-countdown');
          const rect = el => {
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {top: r.top, bottom: r.bottom, left: r.left, right: r.right};
          };
          return {price: rect(price), countdown: rect(countdown)};
        }
    """)

    assert boxes["price"] is not None, "launch-price paragraph not found"
    assert boxes["countdown"] is not None, "countdown box not found"
    assert boxes["price"]["bottom"] <= boxes["countdown"]["top"] + 1, (
        f"launch price paragraph overlaps countdown: {boxes}"
    )
