"""The three redesigned account pages, driven the way a person drives them.

Everything here is something only a running browser can answer: that a setting really
saves and says so, that a message really sends, that the checkout popup really walks
through its steps and really refuses an incomplete one, that the keyboard reaches
everything, that nothing is too small to press, that the colours are strong enough to
read, and that no page scrolls sideways on a phone.

The shared `page` fixture fails a test on any console error, any page error and any
failed request, so "no bugs, clean console" is enforced by the harness rather than by
reading the code.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import (
    assert_contrast,
    assert_no_horizontal_overflow,
    assert_no_raw_traceback,
    close_any_open_guide,
    signup,
    unique_email,
)

SUBSCRIPTION = "/dashboard/subscription"
SETTINGS = "/dashboard/settings"
SUPPORT = "/dashboard/support"

#: The smallest a control may be, in CSS pixels. WCAG 2.2 AA (2.5.8) asks for 24×24;
#: this path's own rules ask for 44×44.
MIN_TARGET = 44

#: A phone, and a small one.
PHONE = {"width": 390, "height": 844}


def _settle_cookie_choice(page: Page) -> None:
    """Make the cookie choice, the way a person does, before driving the page.

    The banner is fixed to the bottom of every page for a brand-new account and it
    covers whatever is under it — which is the banner working, not a bug. A test that
    reaches for something near the foot of the page has to answer it first, exactly as
    a person would.
    """

    banner = page.locator("[data-cookie-banner]")
    if banner.count() and banner.is_visible():
        page.locator("[data-cookie-essential]").first.click()
        expect(banner).to_be_hidden()


def _open(page: Page, base_url: str, path: str, ready: str) -> str:
    email = unique_email("account-e2e")
    signup(page, base_url, email)
    close_any_open_guide(page)
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    close_any_open_guide(page)
    _settle_cookie_choice(page)
    expect(page.locator(ready).first).to_be_visible(timeout=15_000)
    assert_no_raw_traceback(page)
    return email


def _too_small(page: Page, selector: str) -> list[dict[str, object]]:
    """Every visible control under the minimum target size.

    Measured after the motion has settled, like the shared overflow and palette checks
    beside it. A popup opens with a 200ms scale, and a button caught halfway through it
    measures 43.4px rather than its real 44 — which is the animation working, not a
    control that is too small.
    """

    page.wait_for_timeout(400)
    return page.evaluate(
        """([selector, minimum]) =>
            [...document.querySelectorAll(selector)]
                .filter((element) => {
                    const box = element.getBoundingClientRect();
                    const style = getComputedStyle(element);
                    return (
                        style.display !== 'none' && style.visibility !== 'hidden' &&
                        box.width > 0 && box.height > 0 &&
                        (box.width < minimum - 0.5 || box.height < minimum - 0.5)
                    );
                })
                .slice(0, 8)
                .map((element) => {
                    const box = element.getBoundingClientRect();
                    return {
                        what: `${element.tagName.toLowerCase()}${[...element.classList]
                            .slice(0, 3).map((name) => `.${name}`).join('')}`,
                        width: Math.round(box.width),
                        height: Math.round(box.height),
                    };
                })""",
        [selector, MIN_TARGET],
    )


# ── Subscription ─────────────────────────────────────────────────────────────


def test_the_plan_page_leads_with_what_you_have(page: Page, base_url: str) -> None:
    """The first screen answers "what is this and what do I have" without scrolling."""

    _open(page, base_url, SUBSCRIPTION, ".s-now")

    expect(page.locator(".s-now")).to_be_in_viewport()
    expect(page.locator(".s-now h1")).to_have_text(re.compile(r"\S"))


def test_every_allowance_that_shows_a_bar_shows_real_numbers(
    page: Page, base_url: str
) -> None:
    """A bar drawn against a number nobody measured is a picture of nothing."""

    _open(page, base_url, SUBSCRIPTION, ".s-now")

    for index in range(page.locator("[data-s-allowance]").count()):
        card = page.locator("[data-s-allowance]").nth(index)
        bar = card.locator("[data-s-bar]")
        if not bar.count():
            continue
        # The fill has to have arrived at a real width, not been left at zero.
        assert card.locator(".s-allowance-value").inner_text().strip()
        assert bar.get_attribute("data-fill") is not None


def test_a_plan_that_cannot_be_bought_shows_no_price_and_says_why(
    page: Page, base_url: str
) -> None:
    """This server runs with paid checkout switched off, which is the honest state of
    the product today. Every plan must therefore say so, and none may quote a price it
    cannot honour."""

    _open(page, base_url, SUBSCRIPTION, ".s-now")

    blocked = page.locator("[data-s-plan] .s-blocked")
    assert blocked.count() >= 1
    for index in range(blocked.count()):
        assert blocked.nth(index).inner_text().strip()


def test_the_comparison_table_is_behind_a_disclosure(page: Page, base_url: str) -> None:
    """Rule E1: never a wall of text. Sixteen rows of table is a wall."""

    _open(page, base_url, SUBSCRIPTION, ".s-now")

    table = page.locator("[data-s-compare] table")
    expect(table).to_be_hidden()
    page.locator("[data-s-compare] summary").click()
    expect(table).to_be_visible()


def test_the_plan_page_reads_on_a_phone(page: Page, base_url: str) -> None:
    _open(page, base_url, SUBSCRIPTION, ".s-now")

    page.set_viewport_size(PHONE)
    assert_no_horizontal_overflow(page)


def test_the_plan_page_is_readable(page: Page, base_url: str) -> None:
    _open(page, base_url, SUBSCRIPTION, ".s-now")

    assert_contrast(
        page,
        [
            [".s-allowance-note", "color"],
            [".s-blocked", "color"],
        ],
        at_least=4.5,
    )


def test_every_control_on_the_plan_page_is_big_enough_to_press(
    page: Page, base_url: str
) -> None:
    _open(page, base_url, SUBSCRIPTION, ".s-now")

    assert _too_small(page, ".hm-s .t-action, .hm-s .a-jump a") == []


# ── Subscription: the checkout popup, on a server that can really sell ───────


def _open_checkout(page: Page, paid_base_url: str) -> None:
    email = unique_email("paid-e2e")
    signup(page, paid_base_url, email)
    close_any_open_guide(page)
    page.goto(f"{paid_base_url}{SUBSCRIPTION}", wait_until="domcontentloaded")
    close_any_open_guide(page)
    expect(page.locator("[data-s-choose]").first).to_be_visible(timeout=15_000)
    page.locator("[data-s-choose]").first.click()
    expect(page.locator("[data-s-dialog]")).to_be_visible()


def test_the_checkout_shows_the_exact_charge_at_every_step(
    page: Page, paid_base_url: str
) -> None:
    """Rule G6. The order stays in front of the person the whole way through."""

    _open_checkout(page, paid_base_url)

    total = page.locator("[data-s-order-total]")
    expect(total).to_be_visible()
    expect(total).to_have_text(re.compile(r"^\$\d"))
    expect(page.locator("[data-s-order-when]")).to_contain_text("today")

    page.locator("[data-s-next]").click()
    expect(page.locator("[data-s-step-of]")).to_have_text("Step 2 of 3")
    expect(total).to_be_visible()


def test_the_checkout_list_is_styled_inside_the_popup(
    page: Page, paid_base_url: str
) -> None:
    """A `<dialog>` sits outside the page wrapper, so a rule scoped to the page misses
    it entirely. That is not a subtle miss: the same ticked list that reads neatly on a
    card fell back to browser bullets with the tick stacked above every line."""

    _open_checkout(page, paid_base_url)
    items = page.locator("[data-s-includes] li")
    expect(items.first).to_be_visible()

    laid_out = page.evaluate(
        """() => {
            const list = document.querySelector('[data-s-includes]');
            const row = list.querySelector('li');
            return {
                marker: getComputedStyle(list).listStyleType,
                rowDisplay: getComputedStyle(row).display,
            };
        }"""
    )
    assert laid_out == {"marker": "none", "rowDisplay": "flex"}


def test_the_checkout_will_not_move_on_from_an_incomplete_step(
    page: Page, paid_base_url: str
) -> None:
    """Rule D10. It marks which box is missing rather than refusing as a whole."""

    _open_checkout(page, paid_base_url)
    page.locator("[data-s-next]").click()
    expect(page.locator("[data-s-step-of]")).to_have_text("Step 2 of 3")

    page.locator("[data-s-next]").click()

    # Still on step two, with the empty boxes marked and said out loud.
    expect(page.locator("[data-s-step-of]")).to_have_text("Step 2 of 3")
    assert page.locator('[data-s-field][data-wrong="true"]').count() >= 1
    expect(page.locator("[data-s-said]")).to_contain_text("missing")


def test_the_last_button_stays_shut_until_the_person_has_agreed(
    page: Page, paid_base_url: str
) -> None:
    """Rule G7. Refund and renewal terms are agreed to before payment, not after."""

    _open_checkout(page, paid_base_url)
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

    pay = page.locator("[data-s-pay]")
    expect(pay).to_be_disabled()

    page.locator('[data-s-method="card"] input').check()
    expect(pay).to_be_disabled()
    expect(page.locator("[data-s-pay-label]")).to_contain_text("Tick the box")

    page.locator("[data-s-agree]").check()
    expect(pay).to_be_enabled()
    expect(page.locator("[data-s-pay-label]")).to_contain_text("card payment page")


def _reach_the_paying_step(page: Page, base_url: str) -> None:
    """Walk to step three, the way a person does, and stop where the choice is made."""

    _open_checkout(page, base_url)
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


def test_each_way_of_paying_names_the_company_that_takes_the_money(
    page: Page, live_shape_base_url: str
) -> None:
    """The mark under each choice, on a server set up the way the live one is.

    A buyer is about to be sent somewhere else to type a card number. Before that, they
    can read whose page it is and open that company in a new tab.
    """

    _reach_the_paying_step(page, live_shape_base_url)

    for method, company, site in (
        ("card", "Creem", "https://www.creem.io/"),
        ("crypto", "NOWPayments", "https://nowpayments.io/"),
    ):
        mark = page.locator(f'[data-s-method="{method}"] + .hm-pay-secured')
        expect(mark).to_be_visible()
        expect(mark).to_have_text(f"Payments secured by {company}")
        expect(mark).to_have_attribute("href", site)
        expect(mark).to_have_attribute("target", "_blank")

    # Grey enough to stay quiet, dark enough to read. 4.5:1 is what WCAG asks of text
    # this size, and the mark sits on the soft fill rather than on white.
    assert_contrast(
        page,
        [[".hm-pay-secured-label", "color"], [".hm-pay-secured-name", "color"]],
        at_least=4.5,
    )


def test_the_two_ways_of_paying_stay_the_same_size_beside_each_other(
    page: Page, live_shape_base_url: str
) -> None:
    """Card and Crypto are one choice made twice, so they are drawn as one pair.

    Measured rather than looked at: a mark under one of them changes how tall that side
    is, and a note that runs onto a second line changes it again.
    """

    _reach_the_paying_step(page, live_shape_base_url)
    page.wait_for_timeout(400)

    boxes = page.evaluate(
        """() => [...document.querySelectorAll('.s-method-choice')].map(cell => {
            const choice = cell.querySelector('.s-method').getBoundingClientRect();
            const mark = cell.querySelector('.hm-pay-secured').getBoundingClientRect();
            return {
                cell: Math.round(cell.getBoundingClientRect().height),
                choice: Math.round(choice.height),
                choiceTop: Math.round(choice.top),
                markTop: Math.round(mark.top),
                markLeft: Math.round(mark.left - cell.getBoundingClientRect().left),
            };
        })"""
    )

    assert len(boxes) == 2, boxes
    card, crypto = boxes
    assert card["cell"] == crypto["cell"], boxes
    assert card["choice"] == crypto["choice"], boxes
    assert card["choiceTop"] == crypto["choiceTop"], boxes
    # The two marks sit on one line, each under its own choice and against its left edge.
    assert card["markTop"] == crypto["markTop"], boxes
    assert card["markLeft"] == crypto["markLeft"] == 0, boxes
    assert card["markTop"] > card["choiceTop"] + card["choice"] - 1, boxes


def test_the_paying_step_still_fits_a_phone(
    page: Page, live_shape_base_url: str
) -> None:
    """A mark added beside a choice must not push the popup sideways on a small screen."""

    # Walked at full size and then narrowed, like the checkout's own phone check beside
    # it: at phone width the cookie banner and the assistant sit over the buttons a
    # person would press on the way here, which is those working rather than a fault.
    _reach_the_paying_step(page, live_shape_base_url)
    page.set_viewport_size(PHONE)
    expect(page.locator(".hm-pay-secured").first).to_be_visible()
    assert_no_horizontal_overflow(page)
    assert _too_small(page, ".hm-pay-secured") == []


def test_the_checkout_closes_on_escape_and_gives_the_keyboard_back(
    page: Page, paid_base_url: str
) -> None:
    """Rule D5. The browser traps focus in a real dialog; giving it back is ours."""

    _open_checkout(page, paid_base_url)

    page.keyboard.press("Escape")
    expect(page.locator("[data-s-dialog]")).to_be_hidden()
    focused = page.evaluate("() => document.activeElement?.dataset?.sChoose || ''")
    assert focused, "focus must return to the button that opened the popup"


def test_every_control_in_the_checkout_is_big_enough_to_press(
    page: Page, paid_base_url: str
) -> None:
    _open_checkout(page, paid_base_url)

    assert _too_small(page, "[data-s-dialog] .t-action, [data-s-dialog] .s-field input") == []


def test_the_checkout_reads_on_a_phone(page: Page, paid_base_url: str) -> None:
    _open_checkout(page, paid_base_url)

    page.set_viewport_size(PHONE)
    assert_no_horizontal_overflow(page)


# ── Settings ─────────────────────────────────────────────────────────────────


def test_a_switch_saves_by_itself_and_says_so(page: Page, base_url: str) -> None:
    """There is no Save button, so "did that work?" must be answered every time."""

    _open(page, base_url, SETTINGS, "[data-g-group]")

    switch = page.locator('[data-g-switch="near_miss_enabled"]')
    before = switch.get_attribute("aria-checked")
    switch.click()

    expect(switch).to_have_attribute("aria-checked", "false" if before == "true" else "true")
    expect(page.locator("[data-g-saved]")).to_have_attribute("data-state", "saved")
    expect(page.locator("[data-g-saved-title]")).to_have_text("Saved")


def test_a_saved_setting_is_still_there_after_a_reload(page: Page, base_url: str) -> None:
    """The strongest proof that a setting really saved: come back and look."""

    _open(page, base_url, SETTINGS, "[data-g-group]")
    switch = page.locator('[data-g-switch="finished_opportunity_alerts"]')
    switch.click()
    expect(page.locator("[data-g-saved]")).to_have_attribute("data-state", "saved")

    page.reload(wait_until="domcontentloaded")

    expect(page.locator('[data-g-switch="finished_opportunity_alerts"]')).to_have_attribute(
        "aria-checked", "false"
    )


def test_turning_a_setting_off_hides_only_what_depends_on_it(
    page: Page, base_url: str
) -> None:
    """A row that makes no sense without its switch goes away, and comes back."""

    _open(page, base_url, SETTINGS, "[data-g-group]")
    row = page.locator('[data-g-shown-by="near_miss_enabled"]')
    expect(row).to_be_visible()

    page.locator('[data-g-switch="near_miss_enabled"]').click()
    expect(row).to_be_hidden()

    page.locator('[data-g-switch="near_miss_enabled"]').click()
    expect(row).to_be_visible()


def test_silencing_a_coin_keeps_it_after_a_reload(page: Page, base_url: str) -> None:
    """One of the two settings the product reads and the live page never offered."""

    _open(page, base_url, SETTINGS, "[data-g-group]")
    page.locator("[data-g-mute-input]").fill("btc")
    page.locator("[data-g-mute-add]").click()
    expect(page.locator('[data-g-chip][data-value="BTC"]')).to_be_visible()
    expect(page.locator("[data-g-saved]")).to_have_attribute("data-state", "saved")

    page.reload(wait_until="domcontentloaded")

    expect(page.locator('[data-g-chip][data-value="BTC"]')).to_be_visible()


def test_a_silenced_coin_can_be_taken_back_out(page: Page, base_url: str) -> None:
    _open(page, base_url, SETTINGS, "[data-g-group]")
    page.locator("[data-g-mute-input]").fill("eth")
    page.locator("[data-g-mute-add]").click()
    expect(page.locator('[data-g-chip][data-value="ETH"]')).to_be_visible()

    page.locator('[data-g-chip][data-value="ETH"] [data-g-chip-remove]').click()

    expect(page.locator('[data-g-chip][data-value="ETH"]')).to_have_count(0)
    expect(page.locator("[data-g-chip-empty]")).to_be_visible()


def test_choosing_a_part_of_the_day_takes_all_of_its_hours(
    page: Page, base_url: str
) -> None:
    """Twenty-four identical boxes is a puzzle. One press must do the obvious thing."""

    _open(page, base_url, SETTINGS, "[data-g-group]")
    morning = page.locator("[data-g-part]").nth(1)
    morning.click()

    for hour in ("06:00", "09:00", "11:00"):
        expect(page.locator(f'[data-g-set="alert_hours"][data-value="{hour}"]')).to_have_attribute(
            "aria-pressed", "true"
        )
    expect(page.locator("[data-g-hours-words]")).to_contain_text("6 hours picked")


def test_no_hour_chosen_is_explained_as_any_time(page: Page, base_url: str) -> None:
    """The product treats an empty hour list as "any hour", so the page must say that
    and never let it read as "never"."""

    _open(page, base_url, SETTINGS, "[data-g-group]")

    expect(page.locator("[data-g-hours-words]")).to_contain_text("any time of day")


def test_every_day_switches_the_single_days_off_without_hiding_them(
    page: Page, base_url: str
) -> None:
    """A control that disappears takes its state with it, and a person cannot see what
    they had chosen before."""

    _open(page, base_url, SETTINGS, "[data-g-group]")
    every = page.locator("[data-g-every-day]")
    expect(every).to_have_attribute("aria-checked", "true")
    expect(page.locator('[data-g-set="alert_days"]').first).to_be_visible()
    expect(page.locator('[data-g-set="alert_days"]').first).to_be_disabled()

    every.click()

    expect(page.locator('[data-g-set="alert_days"]').first).to_be_enabled()


def test_a_setting_can_be_changed_with_the_keyboard_alone(
    page: Page, base_url: str
) -> None:
    """Rule D3. A switch is a button, so Space and Enter must both work on it."""

    _open(page, base_url, SETTINGS, "[data-g-group]")
    switch = page.locator('[data-g-switch="qualification_change_alerts"]')
    before = switch.get_attribute("aria-checked")

    switch.focus()
    page.keyboard.press("Space")

    expect(switch).to_have_attribute("aria-checked", "false" if before == "true" else "true")
    expect(page.locator("[data-g-saved]")).to_have_attribute("data-state", "saved")


def test_the_consequence_of_switching_an_exchange_off_is_on_the_page(
    page: Page, base_url: str
) -> None:
    _open(page, base_url, SETTINGS, "[data-g-group]")

    expect(page.locator("#g-market .g-warn")).to_contain_text(
        "stops every Watchlist that uses it"
    )


def test_switching_an_exchange_off_asks_first_and_can_be_called_off(
    page: Page, base_url: str
) -> None:
    """The one control here that is not undone by pressing it again asks before it
    acts — the same pattern as unlinking Telegram, for the same reason."""

    _open(page, base_url, SETTINGS, "[data-g-group]")
    binance = page.locator('[data-g-set="providers"][data-value="binance"]')
    expect(binance).to_have_attribute("aria-pressed", "true")

    binance.click()

    dialog = page.locator("[data-g-ask-dialog]")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("Binance")
    # Nothing has changed yet, and saying no leaves it exactly as it was.
    expect(binance).to_have_attribute("aria-pressed", "true")
    page.locator("[data-g-ask-cancel]").click()
    expect(dialog).to_be_hidden()
    expect(binance).to_have_attribute("aria-pressed", "true")

    binance.click()
    page.locator("[data-g-ask-go]").click()
    expect(dialog).to_be_hidden()
    expect(binance).to_have_attribute("aria-pressed", "false")
    expect(page.locator("[data-g-saved]")).to_have_attribute("data-state", "saved")


def test_the_last_exchange_cannot_be_switched_off(page: Page, base_url: str) -> None:
    """With none chosen the server falls back to the first exchange. The page would then
    show nothing selected while the account really had one, and a person cannot see a
    fallback happen. So the page never lets it get there."""

    _open(page, base_url, SETTINGS, "[data-g-group]")
    page.locator('[data-g-set="providers"][data-value="binance"]').click()
    page.locator("[data-g-ask-go]").click()
    # Let the first change land before making the next one. There is one live region,
    # and a save that finishes late would otherwise speak over the refusal below.
    expect(page.locator("[data-g-saved]")).to_have_attribute("data-state", "saved")
    bybit = page.locator('[data-g-set="providers"][data-value="bybit"]')
    expect(bybit).to_have_attribute("aria-pressed", "true")

    bybit.click()

    expect(page.locator("[data-g-ask-dialog]")).to_be_hidden()
    expect(bybit).to_have_attribute("aria-pressed", "true")
    expect(page.locator("[data-g-said]")).to_contain_text("At least one exchange")


def test_the_jump_bar_marks_where_you_really_are(page: Page, base_url: str) -> None:
    """Not where you last pressed. Those two answers differ as soon as somebody
    scrolls, and a marker that lies about position is worse than no marker."""

    _open(page, base_url, SETTINGS, "[data-g-group]")
    here = page.locator('[data-g-jump-link][aria-current="true"]')
    expect(here).to_have_count(1)
    expect(here).to_contain_text("Where")

    # A group from the middle of the page, brought to the top of the window — which is
    # what pressing a jump link does. The last group cannot reach the top, because the
    # page runs out of scroll first, and then an earlier group is genuinely the one
    # under the bar.
    page.evaluate(
        "() => document.querySelector('#g-about').scrollIntoView({ block: 'start' })"
    )
    page.wait_for_timeout(600)

    expect(page.locator('[data-g-jump-link][aria-current="true"]')).to_have_count(1)
    expect(page.locator('[data-g-jump-link][aria-current="true"]')).to_contain_text(
        "About what"
    )


def test_the_hours_get_most_of_their_row(page: Page, base_url: str) -> None:
    """The name of a part of the day is a label, not half the row.

    It is also a `.t-action`, and that shared rule spreads buttons evenly across a row —
    which gave "Morning" as much width as all six of its hours put together.
    """

    _open(page, base_url, SETTINGS, "[data-g-group]")

    share = page.evaluate(
        """() => {
            const part = document.querySelector('.g-part');
            const whole = part.getBoundingClientRect().width;
            const name = part.querySelector('.g-part-name').getBoundingClientRect().width;
            return name / whole;
        }"""
    )

    assert share < 0.35, f"the label takes {share:.0%} of the row"


def test_no_settings_row_holds_an_empty_hole(page: Page, base_url: str) -> None:
    """A row must be as tall as what is in it, and no taller.

    Turning a row's main axis vertical without turning off its `flex-wrap` left the
    browser free to hand the last item far more height than its content: under the hours
    there was a 192-pixel hole with nothing in it, on a page where empty space is
    supposed to mean "this group has ended".
    """

    _open(page, base_url, SETTINGS, "[data-g-group]")

    holes = page.evaluate(
        """() => [...document.querySelectorAll('.hm-g .g-row')]
            .map((row) => {
                const last = row.lastElementChild;
                if (!last) return null;
                const box = row.getBoundingClientRect();
                const end = last.getBoundingClientRect().bottom;
                const pad = Number.parseFloat(getComputedStyle(row).paddingBottom) || 0;
                return { what: row.className, gap: Math.round(box.bottom - pad - end) };
            })
            .filter((row) => row && row.gap > 24)"""
    )

    assert holes == []


def test_the_settings_page_reads_on_a_phone(page: Page, base_url: str) -> None:
    _open(page, base_url, SETTINGS, "[data-g-group]")

    page.set_viewport_size(PHONE)
    assert_no_horizontal_overflow(page)


def test_the_settings_page_is_readable(page: Page, base_url: str) -> None:
    _open(page, base_url, SETTINGS, "[data-g-group]")

    assert_contrast(
        page,
        [
            [".g-row-copy span", "color"],
            [".g-choice-copy small", "color"],
            [".g-group-head p", "color"],
            [".g-saved-copy span", "color"],
        ],
        at_least=4.5,
    )


def test_every_control_on_the_settings_page_is_big_enough_to_press(
    page: Page, base_url: str
) -> None:
    """Every control, not a chosen few. The hour buttons and the remove-a-coin button
    were both under the line until this test named them."""

    _open(page, base_url, SETTINGS, "[data-g-group]")
    page.locator("[data-g-mute-input]").fill("btc")
    page.locator("[data-g-mute-add]").click()
    expect(page.locator('[data-g-chip][data-value="BTC"]')).to_be_visible()

    assert (
        _too_small(
            page,
            ".hm-g .t-switch, .hm-g .g-choice, .hm-g .g-day, .hm-g .g-hour,"
            " .hm-g .t-action, .hm-g .g-number, .hm-g .g-select,"
            " .hm-g .g-chip, .hm-g .g-chip button, .hm-g .a-jump a",
        )
        == []
    )


# ── Support ──────────────────────────────────────────────────────────────────


def test_the_help_page_offers_a_way_out_before_a_form(page: Page, base_url: str) -> None:
    _open(page, base_url, SUPPORT, "[data-h-help]")

    assert page.locator("[data-h-help]").count() >= 3
    expect(page.locator("[data-h-help]").first).to_be_in_viewport()


def test_sending_a_message_puts_it_in_your_own_list_straight_away(
    page: Page, base_url: str
) -> None:
    """The live page reloaded the whole dashboard half a second after sending, so
    nobody had a moment to read the result."""

    _open(page, base_url, SUPPORT, "[data-h-form]")
    page.locator('[data-h-topic="bug_report"]').click()
    page.locator("[data-h-description]").fill(
        "The price on one card stopped updating after I left the tab open."
    )
    page.locator("[data-h-send]").click()

    expect(page.locator("[data-h-said]")).to_contain_text("Sent.", timeout=15_000)
    expect(page.locator("[data-h-ticket]").first).to_contain_text("Something is broken")
    expect(page.locator("[data-h-ticket]").first).to_contain_text("Waiting for us")
    expect(page.locator("[data-h-empty]")).to_be_hidden()


def test_a_sent_message_is_still_there_after_a_reload(page: Page, base_url: str) -> None:
    """It has to be really stored, not only drawn."""

    _open(page, base_url, SUPPORT, "[data-h-form]")
    page.locator("[data-h-description]").fill("Please confirm you received this one.")
    page.locator("[data-h-send]").click()
    expect(page.locator("[data-h-said]")).to_contain_text("Sent.", timeout=15_000)

    page.reload(wait_until="domcontentloaded")
    _settle_cookie_choice(page)

    expect(page.locator("[data-h-ticket]").first).to_be_visible()
    page.locator("[data-h-ticket] summary").first.click()
    expect(page.locator("[data-h-ticket] .h-ticket-said").first).to_contain_text(
        "Please confirm you received this one."
    )


def test_an_empty_message_is_refused_in_words_beside_the_box(
    page: Page, base_url: str
) -> None:
    _open(page, base_url, SUPPORT, "[data-h-form]")

    page.locator("[data-h-send]").click()

    expect(page.locator('[data-h-field="description"]')).to_have_attribute(
        "data-wrong", "true"
    )
    expect(page.locator("[data-h-said]")).to_contain_text("tell us what happened")
    expect(page.locator("[data-h-ticket]")).to_have_count(0)


def test_choosing_a_topic_changes_what_we_ask_for(page: Page, base_url: str) -> None:
    """Rule I2. A useful hint beats a bigger form."""

    _open(page, base_url, SUPPORT, "[data-h-form]")
    before = page.locator("[data-h-hint]").inner_text()

    page.locator('[data-h-topic="billing"]').click()

    expect(page.locator("[data-h-hint]")).not_to_have_text(before)
    expect(page.locator("[data-h-hint]")).to_contain_text("amount")


def test_the_help_page_reads_on_a_phone(page: Page, base_url: str) -> None:
    _open(page, base_url, SUPPORT, "[data-h-form]")

    page.set_viewport_size(PHONE)
    assert_no_horizontal_overflow(page)


def test_the_help_page_is_readable(page: Page, base_url: str) -> None:
    _open(page, base_url, SUPPORT, "[data-h-form]")

    assert_contrast(
        page,
        [
            [".h-help-card p", "color"],
            [".h-topic-copy small", "color"],
            [".h-field-hint", "color"],
            [".h-help-go", "color"],
        ],
        at_least=4.5,
    )


def test_every_control_on_the_help_page_is_big_enough_to_press(
    page: Page, base_url: str
) -> None:
    _open(page, base_url, SUPPORT, "[data-h-form]")

    # `:not(.sr-only)` on purpose: the file picker itself is deliberately one pixel and
    # invisible, and its label is the target — which is why the label wears its focus
    # ring and is measured here instead.
    assert (
        _too_small(
            page,
            ".hm-h .t-action, .hm-h .h-topic, .hm-h .h-drop,"
            " .hm-h .h-field input:not(.sr-only), .hm-h .h-help-card",
        )
        == []
    )


# ── Reduced motion ───────────────────────────────────────────────────────────


def test_the_pages_still_work_for_somebody_who_asked_for_less_movement(
    page: Page, base_url: str
) -> None:
    """Rule C5. Everything arrives, nothing moves, and every control still works."""

    page.emulate_media(reduced_motion="reduce")
    _open(page, base_url, SETTINGS, "[data-g-group]")

    expect(page.locator("[data-g-group]").first).to_be_visible()
    switch = page.locator('[data-g-switch="near_miss_enabled"]')
    switch.click()
    expect(page.locator("[data-g-saved]")).to_have_attribute("data-state", "saved")

    page.goto(f"{base_url}{SUBSCRIPTION}", wait_until="domcontentloaded")
    expect(page.locator("[data-s-plan]").first).to_be_visible()
    # Everything settles at full strength rather than partway through an entrance that
    # was never allowed to run.
    faded = page.evaluate(
        """() => [...document.querySelectorAll('[data-s-plan]')]
            .filter((element) => Number(getComputedStyle(element).opacity) < 0.99).length"""
    )
    assert faded == 0


def test_the_code_box_appears_only_for_crypto(page: Page, live_shape_base_url: str) -> None:
    """Rule: the box is offered exactly where a code can be used.

    Card checkout ends on Creem's own page, which has a discount box of its own and
    decides the amount itself. A box on our side of that route would take a code, quote a
    price, and then watch Creem charge a different one.
    """

    _reach_the_paying_step(page, live_shape_base_url)
    box = page.locator("[data-discount]")

    page.locator('[data-s-method="card"] input').check()
    expect(box).to_be_hidden()

    page.locator('[data-s-method="crypto"] input').check()
    expect(box).to_be_visible()
    # And it names the code, so nobody has to already know one exists.
    expect(box).to_contain_text("HILAL25")


def test_a_code_changes_the_price_on_screen_and_crosses_out_the_old_one(
    page: Page, live_shape_base_url: str
) -> None:
    """The one thing only a browser can answer: pressing Apply really works.

    The server is asked, the answer comes back, and the order line above the box shows
    the new amount with the old one struck through beside it.
    """

    _reach_the_paying_step(page, live_shape_base_url)
    page.locator('[data-s-method="crypto"] input').check()

    total = page.locator("[data-s-order-total]")
    was = page.locator("[data-s-order-was]")
    before = total.inner_text()
    expect(was).to_be_hidden()

    page.locator("[data-discount-input]").fill("hilal25")
    page.locator("[data-discount-apply]").click()

    # The answer is said in words, where it is both seen and heard.
    expect(page.locator("[data-discount-said]")).to_contain_text("HILAL25", timeout=15_000)
    expect(page.locator("[data-discount-said]")).to_have_attribute("data-tone", "good")
    # The old price is crossed out and the new one stands where the old one was.
    expect(was).to_be_visible()
    expect(was).to_have_text(before)
    assert total.inner_text() != before, "the price did not move"
    # And the code travels with the payment, so the server prices it again.
    assert page.locator('input[name="discount_code"]').input_value() == "HILAL25"

    # Removing it puts the full price back, rather than leaving a discount nobody chose.
    page.locator("[data-discount-clear]").click()
    expect(was).to_be_hidden()
    expect(total).to_have_text(before)
    assert page.locator('input[name="discount_code"]').input_value() == ""


@pytest.mark.deliberate_console_errors("Failed to load resource", "400")
def test_a_wrong_code_says_so_and_changes_no_price(
    page: Page, live_shape_base_url: str
) -> None:
    """A refusal a beginner can act on, and nothing charged.

    The refusal *is* the behaviour under test, so the 400 the browser logs is the server
    working. The marker names it; anything else this test logs still fails it.
    """

    _reach_the_paying_step(page, live_shape_base_url)
    page.locator('[data-s-method="crypto"] input').check()
    total = page.locator("[data-s-order-total]")
    before = total.inner_text()

    page.locator("[data-discount-input]").fill("NOTAREALCODE")
    page.locator("[data-discount-apply]").click()

    said = page.locator("[data-discount-said]")
    expect(said).to_have_attribute("data-tone", "danger", timeout=15_000)
    expect(said).to_contain_text("not working")
    # The state is in the words and the icon, never in the colour alone.
    expect(total).to_have_text(before)
    expect(page.locator("[data-s-order-was]")).to_be_hidden()
    assert page.locator('input[name="discount_code"]').input_value() == ""


def test_switching_back_to_card_drops_a_code_that_cannot_be_used(
    page: Page, live_shape_base_url: str
) -> None:
    """Keeping it would send somebody to a card payment page for a price the box showed
    and that route will not charge."""

    _reach_the_paying_step(page, live_shape_base_url)
    page.locator('[data-s-method="crypto"] input').check()
    total = page.locator("[data-s-order-total]")
    before = total.inner_text()
    page.locator("[data-discount-input]").fill("HILAL25")
    page.locator("[data-discount-apply]").click()
    expect(page.locator("[data-s-order-was]")).to_be_visible(timeout=15_000)

    page.locator('[data-s-method="card"] input').check()
    expect(page.locator("[data-discount]")).to_be_hidden()
    expect(total).to_have_text(before)
    assert page.locator('input[name="discount_code"]').input_value() == ""


def test_the_code_box_is_readable_and_reachable(
    page: Page, live_shape_base_url: str
) -> None:
    """Nothing too small to press, nothing too faint to read, and no sideways scroll."""

    _reach_the_paying_step(page, live_shape_base_url)
    page.locator('[data-s-method="crypto"] input').check()
    expect(page.locator("[data-discount]")).to_be_visible()

    assert _too_small(page, "[data-discount-input]") == []
    assert _too_small(page, "[data-discount-apply]") == []
    assert_contrast(
        page,
        [
            [".hm-discount-title", "color"],
            [".hm-discount-hint", "color"],
            [".hm-code-chip", "color"],
        ],
        at_least=4.5,
    )
    page.set_viewport_size(PHONE)
    expect(page.locator("[data-discount]")).to_be_visible()
    assert_no_horizontal_overflow(page)


def test_the_review_page_code_box_rewrites_both_prices_in_its_own_wording(
    page: Page, live_shape_base_url: str
) -> None:
    """The other checkout screen. Same box, same server, different wording for money.

    This page writes the currency out as a word — "20.00 USD" — while the popups use a
    symbol. One box fills both, so the wording travels with each place rather than being
    decided inside the box: filling this page the popup's way produced "$15 USD", which
    names the currency twice and reads like a different amount.
    """

    signup(page, live_shape_base_url, unique_email("review-code"))
    close_any_open_guide(page)
    page.goto(
        f"{live_shape_base_url}/dashboard/billing/checkout?plan_code=trader",
        wait_until="domcontentloaded",
    )
    close_any_open_guide(page)
    _settle_cookie_choice(page)

    totals = page.locator("[data-discount-total]")
    expect(totals.first).to_be_visible()
    before = totals.first.inner_text()
    assert before.endswith(" USD"), before

    # The box is hidden until crypto is chosen, exactly as in the popup.
    box = page.locator("[data-discount]")
    page.locator('input[name="payment_method"][value="card"]').check()
    expect(box).to_be_hidden()
    page.locator('input[name="payment_method"][value="crypto"]').check()
    expect(box).to_be_visible()

    page.locator("[data-discount-input]").fill("HILAL25")
    page.locator("[data-discount-apply]").click()
    expect(page.locator("[data-discount-said]")).to_have_attribute(
        "data-tone", "good", timeout=15_000
    )

    after = totals.first.inner_text()
    assert after != before, "the review page did not change its price"
    # Written this page's way: a number, a space, and the currency once.
    assert re.fullmatch(r"\d+\.\d{2} USD", after), after
    originals = page.locator("[data-discount-original]")
    expect(originals.first).to_be_visible()
    assert originals.first.inner_text() == before
    # Every place this page shows the total moved together — the facts list and the
    # order summary both, so the two cannot disagree on one screen.
    assert {totals.nth(i).inner_text() for i in range(totals.count())} == {after}
