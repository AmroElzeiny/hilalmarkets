"""Hilal, driven the way a person drives it.

Everything here is something only a running browser can answer: that the button is
where a thumb expects it, that the window opens and traps focus, that a refusal arrives
and reads like a person wrote it, that reporting and rating work, and that the whole
thing is reachable without a mouse.

The shared `page` fixture fails the test on any console error, any page error and any
failed request, so "no bugs" is enforced by the harness rather than by reading.

The questions asked are deliberately ones Hilal refuses. A refusal is produced by the
application itself with no provider call, so these tests exercise the real path end to
end without spending anything.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import (
    assert_no_horizontal_overflow,
    assert_no_raw_traceback,
    close_any_open_guide,
    signup,
)

MARKET = "/dashboard/market"
REFUSED = "should I buy bitcoin right now"


def _open_page(page: Page, base_url: str, path: str = MARKET) -> None:
    signup(page, base_url)
    close_any_open_guide(page)
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    close_any_open_guide(page)
    expect(page.locator("[data-hilal-open]")).to_be_visible()
    assert_no_raw_traceback(page)


def _open_chat(page: Page, base_url: str, path: str = MARKET) -> None:
    _open_page(page, base_url, path)
    page.locator("[data-hilal-open]").click()
    expect(page.locator("[data-hilal-window]")).to_be_visible()
    # The welcome is drawn once the history has been read.
    expect(page.locator("[data-hilal-thread] .hilal-msg")).to_have_count(1)


def _ask(page: Page, text: str) -> None:
    page.locator("[data-hilal-input]").fill(text)
    page.locator("[data-hilal-send]").click()


# ── Where it is, and what it looks like ──────────────────────────────────────


def test_the_button_sits_in_the_bottom_right_of_every_dashboard_test_page(
    page: Page, base_url: str
) -> None:
    for path in (MARKET, "/dashboard/monitor"):
        _open_page(page, base_url, path)
        box = page.locator("[data-hilal-open]").bounding_box()
        size = page.viewport_size
        assert box["x"] + box["width"] > size["width"] * 0.7, f"not on the right on {path}"
        assert box["y"] + box["height"] > size["height"] * 0.7, f"not at the bottom on {path}"
        # WCAG 2.2 SC 2.5.8, and a thumb.
        assert box["width"] >= 44 and box["height"] >= 44, box


def test_the_cookie_banner_never_buries_the_button(page: Page, base_url: str) -> None:
    """The banner is fixed across the bottom of the dashboard, above everything else.

    It covered the button completely: a first-time visitor could see Hilal and could
    not click it until they had answered the cookie question. The button now sits above
    the banner, by the banner's own measured height.
    """
    _open_page(page, base_url)
    banner = page.locator("[data-cookie-banner]")
    if banner.count() == 0 or not banner.is_visible():
        pytest.skip("this account has already answered the cookie question")

    over = page.evaluate(
        """() => {
          const orb = document.querySelector('[data-hilal-open]').getBoundingClientRect();
          const bar = document.querySelector('[data-cookie-banner]').getBoundingClientRect();
          const gap = !(orb.bottom < bar.top || orb.top > bar.bottom
                        || orb.right < bar.left || orb.left > bar.right);
          const centre = document.elementFromPoint(
            orb.left + orb.width / 2, orb.top + orb.height / 2);
          const hit = Boolean(centre && centre.closest('[data-hilal-open]'));
          return { overlapping: gap, reaches: hit };
        }"""
    )
    assert not over["overlapping"], "the cookie banner sits on top of the chat button"
    assert over["reaches"], "a click in the middle of the button lands on something else"

    # And it is still clickable, which is the thing that actually broke.
    page.locator("[data-hilal-open]").click()
    expect(page.locator("[data-hilal-window]")).to_be_visible()


def test_the_button_is_not_on_the_live_dashboard(page: Page, base_url: str) -> None:
    signup(page, base_url)
    close_any_open_guide(page)
    page.goto(f"{base_url}/dashboard", wait_until="domcontentloaded")
    close_any_open_guide(page)
    expect(page.locator("[data-hilal-open]")).to_have_count(0)


def test_the_button_carries_its_own_icon(page: Page, base_url: str) -> None:
    """Rule A6: a mark of its own, and one a person recognises.

    The count of paths is deliberately not asserted. It was, and it locked in a
    hand-drawn two-path glyph that read as a spiral at the size it is actually seen
    at. What matters is that something drew, and that it is not a mark that already
    means something else on this dashboard.
    """
    _open_page(page, base_url)
    drawn = page.locator("[data-hilal-open] svg").first
    expect(drawn).to_be_attached()
    assert drawn.locator("path").count() >= 1, "the button icon did not render"

    clash = page.evaluate(
        """() => {
          const mine = window.icon('hilal');
          const others = ['bot', 'support', 'info', 'guide', 'moon'];
          return others.filter(name => window.icon(name) === mine);
        }"""
    )
    assert clash == [], f"Hilal is using an icon that already means {clash}"


def test_the_button_icon_is_not_the_fallback(page: Page, base_url: str) -> None:
    """`window.icon` answers with `info` for a name it does not know.

    Without this, deleting the icon would leave every Hilal mark quietly rendering an
    information glyph, and every other test here would still pass.
    """
    _open_page(page, base_url)
    is_fallback = page.evaluate("() => window.icon('hilal') === window.icon('info')")
    assert not is_fallback, "the Hilal icon is missing and fell back to the info glyph"


def test_the_button_says_what_it_will_do(page: Page, base_url: str) -> None:
    _open_page(page, base_url)
    orb = page.locator("[data-hilal-open]")
    expect(orb).to_have_attribute("aria-expanded", "false")
    assert "Open" in orb.get_attribute("aria-label")
    orb.click()
    expect(orb).to_have_attribute("aria-expanded", "true")
    assert "Close" in orb.get_attribute("aria-label")


# ── The welcome ──────────────────────────────────────────────────────────────


def test_the_welcome_offers_help_and_says_any_language_is_fine(
    page: Page, base_url: str
) -> None:
    """Rules C6 and B1: what it is for, and that they may write in their own words."""
    _open_chat(page, base_url)
    welcome = page.locator("[data-hilal-thread] .hilal-bubble").first.inner_text().lower()
    assert "hilal" in welcome
    assert "language" in welcome, welcome
    for subject in ("shariah", "passport", "plan"):
        assert subject in welcome, f"the welcome never mentions {subject}"


def test_the_welcome_is_honest_about_what_it_will_not_do(page: Page, base_url: str) -> None:
    """Rule J5: said up front, not after three paragraphs of discovery."""
    _open_chat(page, base_url)
    welcome = page.locator("[data-hilal-thread] .hilal-bubble").first.inner_text().lower()
    assert "strategies" in welcome or "strategy" in welcome
    assert "buy" in welcome
    # And that this help is new. Somebody who is told once, at the start, and never
    # again, has been told nothing.
    assert "wrong" in welcome, "the welcome never says Hilal can make mistakes"


def test_every_page_says_this_is_new(page: Page, base_url: str) -> None:
    """The caveat lives in the frame, not in a sentence Hilal has to remember."""
    _open_chat(page, base_url)
    expect(page.locator(".hilal-beta")).to_have_text("Beta")
    note = page.locator("[data-hilal-note]").inner_text().lower()
    assert "wrong" in note, note


def test_the_welcome_is_not_a_wall_of_text(page: Page, base_url: str) -> None:
    """Rule J1. The one thing this product is not allowed to do to a beginner."""
    _open_chat(page, base_url)
    welcome = page.locator("[data-hilal-thread] .hilal-bubble").first.inner_text()
    assert len(welcome) < 700, f"the welcome is {len(welcome)} characters long"


def test_the_welcome_offers_things_to_ask(page: Page, base_url: str) -> None:
    _open_chat(page, base_url)
    chips = page.locator("[data-hilal-chips] .hilal-chip")
    expect(chips.first).to_be_visible()
    assert chips.count() <= 3, "too many suggestions to read at a glance"


def test_a_suggestion_asks_the_question_for_you(page: Page, base_url: str) -> None:
    _open_chat(page, base_url)
    wanted = page.locator("[data-hilal-chips] .hilal-chip").first.inner_text()
    page.locator("[data-hilal-chips] .hilal-chip").first.click()
    asked = page.locator("[data-hilal-thread] .hilal-msg[data-who='user']").last
    expect(asked).to_contain_text(wanted)


# ── Refusing, in front of a person ───────────────────────────────────────────


def test_asking_for_advice_gets_a_refusal_that_offers_something_else(
    page: Page, base_url: str
) -> None:
    _open_chat(page, base_url)
    _ask(page, REFUSED)

    answer = page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    expect(answer.locator(".hilal-bubble")).to_have_attribute(
        "data-mode", "REFUSAL", timeout=15_000
    )
    text = answer.inner_text().lower()
    assert "buy" in text
    # Rule I2: the refusal is marked in words, not only by its colour.
    expect(answer.locator(".hilal-msg-note")).to_be_visible()
    expect(page.locator("[data-hilal-chips] .hilal-chip").first).to_be_visible()


def test_asking_hilal_to_decide_a_strategy_offers_guidance_instead(
    page: Page, base_url: str
) -> None:
    _open_chat(page, base_url)
    _ask(page, "build me a trading strategy")
    answer = page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    expect(answer.locator(".hilal-bubble")).to_have_attribute(
        "data-mode", "REFUSAL", timeout=15_000
    )
    words = answer.inner_text().lower()
    assert "yours" in words, "the refusal never says whose decision it is"
    assert "show you" in words or "beside you" in words, (
        "the refusal closed the door instead of offering to walk them through it"
    )


def test_asking_how_to_use_the_canvas_is_not_refused(page: Page, base_url: str) -> None:
    """The change the whole of this pass is about.

    "Help me build a monitor" used to come back as "I don't build or judge trading
    strategies". It is somebody asking to be shown a page they are looking at, and
    there is nothing unsafe in it — the refusal was the product turning down its own
    job. This runs on the monitor page, where the answer is most useful.

    The model is a stand-in here, but it is a real one: it reads the evidence the
    application supplied and answers from it. So this checks the whole path — not
    refused, an answer came back, it is marked as guidance, and it carries the reminder
    that this help is new.
    """
    _open_chat(page, base_url, "/dashboard/monitor")
    _ask(page, "help me build a monitor")
    answer = page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    expect(answer.locator(".hilal-bubble")).to_have_attribute(
        "data-mode", "GUIDE", timeout=20_000
    )
    words = answer.inner_text().lower()
    assert "monitor" in words, words
    # Rule B11: guidance says it is new, in words, every time.
    expect(answer.locator(".hilal-msg-note")).to_be_visible()


def test_hilal_still_refuses_to_choose_a_number(page: Page, base_url: str) -> None:
    """The other half of the same line. Being shown how is help; being told what number
    to put in the box is somebody else deciding a person's position for them."""
    _open_chat(page, base_url, "/dashboard/monitor")
    _ask(page, "help me pick the best RSI value")
    answer = page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    expect(answer.locator(".hilal-bubble")).to_have_attribute(
        "data-mode", "REFUSAL", timeout=15_000
    )


def test_an_answer_never_shows_code_or_a_field_name(page: Page, base_url: str) -> None:
    """Rule J2, checked on what actually reached the screen."""
    _open_chat(page, base_url)
    _ask(page, REFUSED)
    expect(
        page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    ).to_be_visible()
    shown = page.locator("[data-hilal-thread]").inner_text()
    for forbidden in ("```", "{\"", "canonical_asset", "methodology_id", "<div", "null"):
        assert forbidden not in shown, f"{forbidden!r} reached the person"


# ── The allowance ────────────────────────────────────────────────────────────


def test_the_window_shows_what_is_left_of_today(page: Page, base_url: str) -> None:
    """Rule E4/E6. A person who watches it go down is never surprised by it stopping."""
    _open_chat(page, base_url)
    meter = page.locator("[data-hilal-meter]")
    expect(meter).to_be_visible()
    expect(meter).to_have_attribute("data-state", "ok", timeout=10_000)
    assert "message" in meter.inner_text().lower()


def test_the_status_keeps_being_asked_for_while_the_window_is_open(
    page: Page, base_url: str
) -> None:
    """Rule E6: once a second, so the box can lock and unlock on its own."""
    calls = []
    page.on(
        "request",
        lambda request: calls.append(request.url)
        if "hilal/status" in request.url
        else None,
    )
    _open_chat(page, base_url)
    before = len(calls)
    page.wait_for_timeout(2_600)
    assert len(calls) - before >= 2, f"only {len(calls) - before} status checks in 2.6 seconds"


def test_nothing_is_polled_once_the_window_is_closed(page: Page, base_url: str) -> None:
    """A closed window has nobody watching it. Asking anyway is load for nothing."""
    calls = []
    page.on(
        "request",
        lambda request: calls.append(request.url)
        if "hilal/status" in request.url
        else None,
    )
    _open_chat(page, base_url)
    page.locator("[data-hilal-open]").click()
    expect(page.locator("[data-hilal-window]")).to_be_hidden()
    page.wait_for_timeout(400)
    settled = len(calls)
    page.wait_for_timeout(2_500)
    assert len(calls) == settled, "the page kept polling after the chat was closed"


# ── Reporting ────────────────────────────────────────────────────────────────


def test_an_answer_can_be_reported_from_the_header(page: Page, base_url: str) -> None:
    """Rule F1: a report button in the chat header."""
    _open_chat(page, base_url)
    _ask(page, REFUSED)
    expect(
        page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    ).to_be_visible(timeout=15_000)

    page.locator("[data-hilal-report-open]").click()
    dialog = page.locator("[data-hilal-report]")
    expect(dialog).to_be_visible()
    # It shows which answer is being reported, so nobody reports the wrong one.
    expect(page.locator("[data-hilal-report-quote]")).not_to_be_empty()

    page.locator("input[name='hilal-reason'][value='confusing']").check()
    page.locator("[data-hilal-report-note]").fill("I did not follow it.")
    page.locator("[data-hilal-report-form] button[type='submit']").click()
    expect(dialog).to_be_hidden()
    expect(page.locator("[data-hilal-thread]")).to_contain_text("with the team")


def test_an_answer_can_also_be_reported_from_the_answer_itself(
    page: Page, base_url: str
) -> None:
    _open_chat(page, base_url)
    _ask(page, REFUSED)
    answer = page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    expect(answer).to_be_visible(timeout=15_000)
    # Held by its own id. Sending a report adds a thank-you line to the conversation, so
    # "the last assistant message" stops being the answer that was reported.
    reported = answer.get_attribute("data-message-id")
    assert reported

    answer.hover()
    answer.locator("[data-hilal-report-one]").click()
    expect(page.locator("[data-hilal-report]")).to_be_visible()
    page.locator("[data-hilal-report-form] button[type='submit']").click()
    expect(page.locator("[data-hilal-report]")).to_be_hidden()

    # And it says so afterwards, rather than leaving the button looking untouched.
    marked = page.locator(f"[data-message-id='{reported}'] [data-hilal-report-one]")
    expect(marked).to_have_attribute("data-done", "true")


# ── Rating on the way out ────────────────────────────────────────────────────


def test_closing_with_the_x_asks_how_it_went(page: Page, base_url: str) -> None:
    """Rule F2: five stars and a comment box."""
    _open_chat(page, base_url)
    _ask(page, REFUSED)
    expect(
        page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    ).to_be_visible(timeout=15_000)

    page.locator("[data-hilal-close]").click()
    rate = page.locator("[data-hilal-rate]")
    expect(rate).to_be_visible()
    expect(page.locator("[data-hilal-star]")).to_have_count(5)
    expect(page.locator("[data-hilal-rate-comment]")).to_be_visible()

    # Sending is refused until a rating is actually chosen.
    expect(page.locator("[data-hilal-rate-send]")).to_be_disabled()
    page.locator("[data-hilal-star='4']").click()
    expect(page.locator("[data-hilal-rate-send]")).to_be_enabled()
    expect(page.locator("[data-hilal-stars-word]")).not_to_be_empty()

    page.locator("[data-hilal-rate-comment]").fill("Clear and honest.")
    page.locator("[data-hilal-rate-send]").click()
    expect(rate).to_be_hidden()


def test_the_rating_can_be_skipped_and_still_closes(page: Page, base_url: str) -> None:
    """Rule F3. A popup you cannot get out of is worse than no popup."""
    _open_chat(page, base_url)
    _ask(page, REFUSED)
    expect(
        page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    ).to_be_visible(timeout=15_000)
    page.locator("[data-hilal-close]").click()
    expect(page.locator("[data-hilal-rate]")).to_be_visible()
    page.locator("[data-hilal-rate-form] [data-hilal-rate-skip]").click()
    expect(page.locator("[data-hilal-rate]")).to_be_hidden()
    expect(page.locator("[data-hilal-window]")).to_be_hidden()


def test_nobody_is_asked_to_rate_a_conversation_they_never_had(
    page: Page, base_url: str
) -> None:
    _open_chat(page, base_url)
    page.locator("[data-hilal-close]").click()
    expect(page.locator("[data-hilal-window]")).to_be_hidden()
    expect(page.locator("[data-hilal-rate]")).to_be_hidden()


def test_the_stars_light_up_to_the_one_being_pointed_at(page: Page, base_url: str) -> None:
    _open_chat(page, base_url)
    _ask(page, REFUSED)
    expect(
        page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    ).to_be_visible(timeout=15_000)
    page.locator("[data-hilal-close]").click()
    page.locator("[data-hilal-star='3']").click()
    lit = page.locator("[data-hilal-star][data-lit='true']")
    expect(lit).to_have_count(3)
    expect(page.locator("[data-hilal-star='3']")).to_have_attribute("aria-checked", "true")


# ── Keyboard and screen reader ───────────────────────────────────────────────


def test_the_whole_chat_works_without_a_mouse(page: Page, base_url: str) -> None:
    """Rule I3/I5."""
    _open_page(page, base_url)
    page.locator("[data-hilal-open]").focus()
    page.keyboard.press("Enter")
    expect(page.locator("[data-hilal-window]")).to_be_visible()

    # Focus lands where a person would want to type.
    expect(page.locator("[data-hilal-input]")).to_be_focused()
    page.keyboard.type(REFUSED)
    page.keyboard.press("Enter")
    expect(
        page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    ).to_be_visible(timeout=15_000)

    # Escape closes it and gives the keyboard back to the button.
    page.keyboard.press("Escape")
    expect(page.locator("[data-hilal-window]")).to_be_hidden()
    focused = page.evaluate("() => document.activeElement.dataset.hilalOpen !== undefined")
    assert focused, "the keyboard was left nowhere after the chat closed"


def test_shift_and_enter_writes_a_new_line_instead_of_sending(
    page: Page, base_url: str
) -> None:
    _open_chat(page, base_url)
    box = page.locator("[data-hilal-input]")
    box.click()
    page.keyboard.type("one")
    page.keyboard.press("Shift+Enter")
    page.keyboard.type("two")
    assert "\n" in box.input_value()
    expect(page.locator("[data-hilal-thread] .hilal-msg[data-who='user']")).to_have_count(0)


def test_tab_stays_inside_the_open_window(page: Page, base_url: str) -> None:
    """Rule I5. Tabbing out of an open dialog loses people."""
    _open_chat(page, base_url)
    for _ in range(14):
        page.keyboard.press("Tab")
        inside = page.evaluate(
            "() => document.querySelector('[data-hilal-window]').contains(document.activeElement)"
        )
        assert inside, "Tab escaped the chat window"


def test_the_conversation_is_a_real_list_a_screen_reader_can_follow(
    page: Page, base_url: str
) -> None:
    """Rule I8. Who said what has to survive being read aloud."""
    _open_chat(page, base_url)
    _ask(page, REFUSED)
    expect(
        page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    ).to_be_visible(timeout=15_000)

    shape = page.evaluate(
        """() => {
          const thread = document.querySelector('[data-hilal-thread]');
          return {
            list: thread.tagName,
            items: [...thread.children].map(node => node.tagName),
            live: document.querySelector('[data-hilal-scroll]').getAttribute('aria-live'),
          };
        }"""
    )
    assert shape["list"] == "OL"
    assert set(shape["items"]) == {"LI"}
    assert shape["live"] == "polite"


def test_every_control_in_the_window_has_a_name(page: Page, base_url: str) -> None:
    """Rule F4. An icon button with no name is a button nobody can use."""
    _open_chat(page, base_url)
    nameless = page.evaluate(
        """() => {
          const window_ = document.querySelector('[data-hilal-window]');
          return [...window_.querySelectorAll('button, a[href], textarea')]
            .filter(node => node.offsetParent !== null)
            .filter(node => !(
              (node.getAttribute('aria-label') || '').trim()
              || (node.textContent || '').trim()
              || (node.labels && node.labels.length)
            ))
            .map(node => node.outerHTML.slice(0, 90));
        }"""
    )
    assert nameless == [], nameless


# ── Everything else the harness cannot infer ─────────────────────────────────


def test_the_words_a_person_reads_are_dark_enough_to_read(page: Page, base_url: str) -> None:
    """Rule I1: 4.5:1 for body text, measured rather than assumed.

    Measured on the paragraph that actually holds the words, not on the bubble around
    it. It used to read the bubble, which was set to full-strength ink while every
    paragraph inside it was grey — so the test passed and the words were still faint.
    """
    _open_chat(page, base_url)
    _ask(page, REFUSED)
    expect(
        page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    ).to_be_visible(timeout=15_000)

    measured = page.evaluate(
        """() => {
          const luminance = (colour) => {
            const [r, g, b] = colour.match(/\\d+(\\.\\d+)?/g).slice(0, 3).map(Number);
            const channel = (value) => {
              const v = value / 255;
              return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
            };
            return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
          };
          const against = (node) => {
            let back = 'rgb(255,255,255)';
            for (let el = node; el; el = el.parentElement) {
              const value = getComputedStyle(el).backgroundColor;
              const alpha = value.match(/[\\d.]+\\)$/);
              if (value !== 'rgba(0, 0, 0, 0)' && (!alpha || Number(alpha[0].slice(0, -1)))) {
                back = value;
                break;
              }
            }
            const front = luminance(getComputedStyle(node).color);
            const behind = luminance(back);
            const light = Math.max(front, behind);
            const dark = Math.min(front, behind);
            return {
              ratio: (light + 0.05) / (dark + 0.05),
              colour: getComputedStyle(node).color,
            };
          };
          return [...document.querySelectorAll('.hilal-bubble p')].map(against);
        }"""
    )
    assert measured, "no words were drawn"
    for item in measured:
        assert item["ratio"] >= 4.5, f"answer text measures {item['ratio']:.2f}:1"
        # And it is the strong ink, not the quiet grey. What Hilal says is the reason
        # the window exists, so it must not read like a footnote beside the page.
        assert item["colour"] == "rgb(32, 35, 41)", (
            f"the words are drawn in {item['colour']}, not the strongest ink"
        )


# ── Nothing in the window is squashed, cut off or misplaced ──────────────────


CHIP_GEOMETRY = """() => {
  const chips = [...document.querySelectorAll('.hilal-chip')];
  return chips.map((chip) => {
    const range = document.createRange();
    range.selectNodeContents(chip);
    const pill = chip.getBoundingClientRect();
    const words = range.getBoundingClientRect();
    return {
      text: chip.textContent.slice(0, 30),
      inside: words.top >= pill.top - 1 && words.bottom <= pill.bottom + 1,
      centred_down: Math.abs((pill.top + pill.bottom) / 2
                             - (words.top + words.bottom) / 2) <= 1.5,
      centred_across: Math.abs((pill.left + pill.right) / 2
                               - (words.left + words.right) / 2) <= 1.5,
      height: Math.round(pill.height),
    };
  });
}"""


@pytest.mark.parametrize("width", [1440, 1024, 390])
def test_a_suggestion_keeps_its_words_inside_itself(
    page: Page, base_url: str, width: int
) -> None:
    """The reported break, and the whole family it came from.

    The window is a fixed-height column. The transcript refused to shrink below its own
    content, so the flex algorithm took the space out of every other row instead — and
    the row it took it from was the suggestions. The pills collapsed to fourteen pixels
    and their words fell out of the bottom.

    Checked after a real answer, not only on the welcome, because an empty window has
    nothing to push with and the welcome looked perfect while the rest was broken.
    """
    page.set_viewport_size({"width": width, "height": 820})
    _open_chat(page, base_url)
    _ask(page, REFUSED)
    expect(
        page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    ).to_be_visible(timeout=15_000)
    page.wait_for_timeout(600)

    chips = page.evaluate(CHIP_GEOMETRY)
    assert chips, "no suggestions were offered after the answer"
    for chip in chips:
        assert chip["inside"], f"the words fell outside the button: {chip}"
        assert chip["centred_down"], f"the words are not centred down the button: {chip}"
        assert chip["centred_across"], f"the words are not centred across it: {chip}"
        assert chip["height"] >= 30, f"the button was squashed to {chip['height']}px"


def test_no_row_in_the_window_is_squashed_by_a_long_conversation(
    page: Page, base_url: str
) -> None:
    """The same defect seen from the other end: every row keeps its own height."""
    _open_chat(page, base_url)
    for _ in range(3):
        _ask(page, REFUSED)
        page.wait_for_timeout(1200)
    expect(page.locator("[data-hilal-thread] .hilal-msg")).to_have_count(7, timeout=20_000)

    sizes = page.evaluate(
        """() => {
          const of = (selector) => {
            const node = document.querySelector(selector);
            return node ? Math.round(node.getBoundingClientRect().height) : 0;
          };
          return {
            head: of('.hilal-head'),
            meter: of('.hilal-meter'),
            compose: of('.hilal-compose'),
            send: of('.hilal-send'),
            foot: of('.hilal-foot'),
          };
        }"""
    )
    assert sizes["head"] >= 50, sizes
    assert sizes["meter"] >= 20, sizes
    assert sizes["compose"] >= 60, sizes
    # A 44px target that has been squeezed to 30 is no longer a 44px target.
    assert sizes["send"] >= 44, sizes
    assert sizes["foot"] >= 12, sizes


@pytest.mark.parametrize("width", [1440, 390])
def test_the_writing_box_has_no_scrollbar_when_it_is_empty(
    page: Page, base_url: str, width: int
) -> None:
    """A bar down the side of a box nobody has typed in reads as broken, because it is.

    The invitation inside the box was a full sentence. It wrapped onto a second line
    inside a one-line box, so the box was overflowing before anybody touched it.
    """
    page.set_viewport_size({"width": width, "height": 820})
    _open_chat(page, base_url)
    page.wait_for_timeout(400)
    box = page.evaluate(
        """() => {
          const input = document.querySelector('[data-hilal-input]');
          return {
            value: input.value,
            scrollHeight: input.scrollHeight,
            clientHeight: input.clientHeight,
            overflowY: getComputedStyle(input).overflowY,
          };
        }"""
    )
    assert box["value"] == ""
    assert box["scrollHeight"] <= box["clientHeight"] + 1, (
        f"the empty writing box overflows: {box}"
    )
    assert box["overflowY"] == "hidden", box


def test_the_writing_box_grows_and_only_then_scrolls(page: Page, base_url: str) -> None:
    """The other half of the rule: a bar appears only when there is something to scroll."""
    _open_chat(page, base_url)
    box = page.locator("[data-hilal-input]")
    box.click()
    page.keyboard.type("one line")
    short = page.evaluate(
        "() => getComputedStyle(document.querySelector('[data-hilal-input]')).overflowY"
    )
    assert short == "hidden"

    box.fill("\n".join(f"line number {n}" for n in range(1, 20)))
    page.wait_for_timeout(200)
    tall = page.evaluate(
        """() => {
          const input = document.querySelector('[data-hilal-input]');
          return {
            overflowY: getComputedStyle(input).overflowY,
            height: Math.round(input.getBoundingClientRect().height),
          };
        }"""
    )
    assert tall["overflowY"] == "auto", tall
    assert tall["height"] <= 140, "the box grew past the limit instead of scrolling"


def test_the_chat_uses_no_colour_that_is_not_the_brands(page: Page, base_url: str) -> None:
    """The highlight on the writing box was a blue ring over an apple-green edge.

    Two highlights at once, and the blue is a colour `brand guide.md` §9 keeps for
    connectors and small progress details — not for the most-used control in a window.
    Selected and focused states across this whole path are near-black, and this is now
    one of them.
    """
    _open_chat(page, base_url)
    page.locator("[data-hilal-input]").click()
    page.wait_for_timeout(300)

    found = page.evaluate(
        """() => {
          const seen = new Set();
          const widget = document.querySelector('.hm-hilal');
          for (const node of widget.querySelectorAll('*')) {
            const box = node.getBoundingClientRect();
            if (!box.width || !box.height) continue;
            const style = getComputedStyle(node);
            for (const property of ['color', 'backgroundColor', 'borderTopColor',
                                    'borderLeftColor', 'outlineColor', 'fill']) {
              const value = style[property];
              const match = String(value).match(
                /^rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([0-9.]+))?/);
              if (!match) continue;
              if (match[4] !== undefined && Number(match[4]) === 0) continue;
              if (style[property.replace('Color', 'Style')] === 'none') continue;
              seen.add(`${match[1]},${match[2]},${match[3]}`);
            }
          }
          return [...seen];
        }"""
    )
    # 42,143,195 is --hm-blue. It has no business being a focus ring here.
    assert "42,143,195" not in found, "the chat still highlights in blue"


@pytest.mark.parametrize("width", [1440, 1024, 760, 390])
def test_the_chat_never_pushes_the_page_sideways(
    page: Page, base_url: str, width: int
) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    _open_chat(page, base_url)
    assert_no_horizontal_overflow(page)
    box = page.locator("[data-hilal-window]").bounding_box()
    assert box["x"] >= -1, box
    assert box["x"] + box["width"] <= width + 1, box


def test_it_still_works_for_somebody_who_asked_for_less_motion(
    page: Page, base_url: str
) -> None:
    """Rule H6. Less movement, never less product."""
    page.emulate_media(reduced_motion="reduce")
    _open_chat(page, base_url)
    _ask(page, REFUSED)
    expect(
        page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    ).to_be_visible(timeout=15_000)
    page.locator("[data-hilal-close]").click()
    expect(page.locator("[data-hilal-rate]")).to_be_visible()
    page.locator("[data-hilal-star='5']").click()
    expect(page.locator("[data-hilal-rate-send]")).to_be_enabled()


def test_hilal_is_told_what_is_on_the_canvas(page: Page, base_url: str) -> None:
    """Rule C5, extended: the assistant sees the board the person is drawing.

    The canvas publishes its own words for it — the readout sentence, the checklist,
    the card labels — rather than the assistant reading the board and forming a second
    opinion about what a card means. What is asserted here is that the words actually
    travel, and that they are the page's own.
    """
    _open_page(page, base_url, "/dashboard/monitor")
    page.wait_for_timeout(1200)

    sent: list[dict] = []
    page.on(
        "request",
        lambda request: sent.append(request.post_data_json)
        if request.url.endswith("/dashboard/hilal/message") and request.post_data
        else None,
    )
    page.locator("[data-hilal-open]").click()
    expect(page.locator("[data-hilal-window]")).to_be_visible()
    _ask(page, REFUSED)
    expect(
        page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    ).to_be_visible(timeout=15_000)

    assert sent, "nothing was posted"
    view = sent[-1].get("view") or {}
    assert view.get("page"), f"the page was not sent: {view}"
    # Which part of the page is in front of them, named by the page itself.
    named = page.evaluate(
        "() => [...document.querySelectorAll('[data-hm-part]')].map(n => n.dataset.hmPart)"
    )
    assert view.get("section") in named, (
        f"the part in view was {view.get('section')!r}, which the page never named"
    )
    board = view.get("board")
    assert board is not None, f"the canvas was not described to Hilal: {view}"
    assert board.get("sentence"), "the board's own readout sentence was not sent"
    assert isinstance(board.get("checks"), list) and board["checks"], (
        "the page's own checklist was not sent"
    )
    assert board.get("controls"), "the names of the on-screen buttons were not sent"
    # And how the board is worked, read from the page's own written help. Hilal may
    # only describe a gesture the page documents, so this is what makes "drag the
    # circle onto empty space" something it is allowed to say.
    assert board.get("how_to"), "the page's own help was not sent"
    assert any("cancel" in line.lower() for line in board["how_to"]), board["how_to"]
    # The words are the page's, not the assistant's. The readout on screen says the
    # same sentence, so if these two ever differ, one of them is inventing.
    on_screen = page.locator("[data-sentence-text]").inner_text().strip()
    assert board["sentence"].strip() == on_screen, (
        f"Hilal was told {board['sentence']!r} while the page says {on_screen!r}"
    )


def test_the_board_is_not_described_on_a_page_that_has_none(
    page: Page, base_url: str
) -> None:
    """Fail closed. A page with no canvas says so rather than sending an empty one for
    Hilal to talk about as if it were real."""
    sent: list[dict] = []
    _open_page(page, base_url, MARKET)
    page.on(
        "request",
        lambda request: sent.append(request.post_data_json)
        if request.url.endswith("/dashboard/hilal/message") and request.post_data
        else None,
    )
    page.locator("[data-hilal-open]").click()
    expect(page.locator("[data-hilal-window]")).to_be_visible()
    _ask(page, REFUSED)
    expect(
        page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    ).to_be_visible(timeout=15_000)
    assert sent
    assert (sent[-1].get("view") or {}).get("board") is None


def test_the_way_out_of_a_used_up_day_looks_like_a_button(
    page: Page, base_url: str
) -> None:
    """It was a `.t-action`, and `.t-action` was scoped to the page wrapper only.

    Hilal sits beside the page rather than inside it, so the rule matched nothing: the
    only way forward offered to somebody out of allowance rendered as bare text with
    the icon stacked above it.
    """
    _open_chat(page, base_url)
    page.evaluate(
        """() => {
          document.querySelector('[data-hilal-locked]').hidden = false;
          document.querySelector('[data-hilal-upgrade]').hidden = false;
        }"""
    )
    page.wait_for_timeout(200)
    upgrade = page.locator("[data-hilal-upgrade]")
    expect(upgrade).to_be_visible()
    look = page.evaluate(
        """() => {
          const link = document.querySelector('[data-hilal-upgrade]');
          const style = getComputedStyle(link);
          const box = link.getBoundingClientRect();
          const svg = link.querySelector('svg').getBoundingClientRect();
          return {
            display: style.display,
            radius: Number.parseFloat(style.borderTopLeftRadius),
            padded: Number.parseFloat(style.paddingLeft),
            height: Math.round(box.height),
            iconOnItsOwnLine: svg.bottom <= box.top + box.height / 2 - 4,
          };
        }"""
    )
    assert look["display"] in {"inline-flex", "flex"}, look
    assert look["radius"] > 0, f"the upgrade link has no button shape: {look}"
    assert look["padded"] > 0, look
    assert look["height"] >= 34, look
    assert not look["iconOnItsOwnLine"], "the icon and its words are stacked"


def test_the_conversation_is_still_there_after_a_reload(page: Page, base_url: str) -> None:
    """Rule D2. The history came from the server, so a reload cannot lose it."""
    _open_chat(page, base_url)
    _ask(page, REFUSED)
    expect(
        page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    ).to_be_visible(timeout=15_000)

    page.reload(wait_until="domcontentloaded")
    close_any_open_guide(page)
    page.locator("[data-hilal-open]").click()
    expect(page.locator("[data-hilal-thread] .hilal-msg")).to_have_count(2)
    expect(page.locator("[data-hilal-thread]")).to_contain_text("buy")
