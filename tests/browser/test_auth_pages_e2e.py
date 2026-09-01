"""The six sign-in pages, measured in a real browser.

Everything here is a claim only a browser can settle. A stylesheet can *declare* a
3:1 edge and a later rule can quietly win; a page can *contain* the code for a countdown
and never start it; an animation can be created and do nothing. Each of those has
happened in this repository, so each is measured on the rendered page rather than read
out of the source.

The one that made this file necessary: every error on every one of these pages was
painted in the **success** colours, because the error rule was written inside `:where()`
and lost the cascade to the plain rule above it. Nothing offline could see that. Neither
could anything offline see that the cookie banner and its settings window rendered as
raw unstyled blocks under the form, because both stylesheets that draw them were loaded
by other pages.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import (
    assert_no_horizontal_overflow,
    signup_to_verify,
    unique_email,
)

#: Every address the redesign covers, in the state a visitor first meets it.
PAGES = (
    "/signup",
    "/signin",
    "/signin/code",
    "/reset-password",
)

#: Where the middle step of signing up lives. It needs an address to work on, because
#: nobody can choose a password for an email nobody has given us.
SIGNUP_PASSWORD = "/signup/password?email=someone%40example.com"

#: The same pages plus the later step of each multi-part flow, which is where the code
#: boxes, the countdown, the password checklist and the "sent to" line live. A rule
#: proved on the first step is not proved on the second until the second is measured too.
ALL_STATES = (
    *PAGES,
    SIGNUP_PASSWORD,
    "/signup/verify?message=code_sent&email=someone%40example.com",
    "/signin/code?message=code_sent&email=someone%40example.com",
    "/reset-password?message=code_sent&email=someone%40example.com",
)

#: The laptop screens this card is promised to fit inside without a scrollbar.
#:
#: 1366×768 is the commonest laptop screen there is, and 1280×720 is about the smallest
#: still sold. The two-panel layout this replaced pushed the button off the bottom of
#: both, which is the whole reason the left panel was taken away.
LAPTOPS = ((1366, 768), (1280, 720))


def _open(page: Page, base_url: str, path: str) -> None:
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    expect(page.locator("h1")).to_be_visible(timeout=10_000)


def _contrast(page: Page, front: str, back: str) -> float:
    return page.evaluate(
        """([front, back]) => {
          const toParts = (value) => value.match(/\\d+(\\.\\d+)?/g).map(Number);
          const channel = (v) => {
            v /= 255;
            return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
          };
          const flatten = (over, under) => over.length > 3 && over[3] < 1
            ? over.slice(0, 3).map((c, i) => c * over[3] + under[i] * (1 - over[3]))
            : over.slice(0, 3);
          const lum = (rgb) =>
            0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
          const b = toParts(back);
          const f = flatten(toParts(front), b);
          const [hi, lo] = [lum(f), lum(b)].sort((x, y) => y - x);
          return (hi + 0.05) / (lo + 0.05);
        }""",
        [front, back],
    )


def _painted_background(page: Page, selector: str) -> str:
    """The colour actually behind an element, walking up past anything transparent."""

    return page.evaluate(
        """(selector) => {
          let node = document.querySelector(selector);
          while (node) {
            const colour = getComputedStyle(node).backgroundColor;
            const parts = colour.match(/\\d+(\\.\\d+)?/g);
            if (parts && (parts.length < 4 || Number(parts[3]) > 0.95)) return colour;
            node = node.parentElement;
          }
          return "rgb(255, 255, 255)";
        }""",
        selector,
    )


# ---------------------------------------------------------------------------
# It renders at all.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ALL_STATES)
def test_the_page_renders_and_fits_a_phone(page: Page, base_url: str, path: str) -> None:
    """First, because a module that throws on import leaves a blank white screen.

    That has shipped here before: TypeScript passed, the build passed, the server
    answered 200, every offline test passed, and three pages rendered nothing.
    """

    page.set_viewport_size({"width": 360, "height": 780})
    # Listening *before* the page loads, because the error that mattered here happened at
    # the top level of a module and would have been over before a later listener existed.
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _open(page, base_url, path)
    expect(page.locator("h1")).not_to_be_empty()
    assert_no_horizontal_overflow(page)
    page.wait_for_timeout(400)
    assert not errors, errors


@pytest.mark.parametrize("path", ALL_STATES)
@pytest.mark.parametrize(("width", "height"), LAPTOPS)
def test_the_whole_page_fits_one_laptop_screen(
    page: Page, base_url: str, path: str, width: int, height: int
) -> None:
    """One screen, no scrollbar. This is the claim the redesign exists to make.

    Nothing offline can settle it. A stylesheet can declare ``min-height: 100dvh`` and
    still overflow, because what decides is the height of what is inside it — and that
    changes per page: the password step carries a checklist, the code steps carry six
    boxes and a countdown, and every page carries three ticks and the legal row.

    The cookie banner is measured with the page rather than dismissed first, because
    somebody signing up is meeting this page for the first time and the banner is on
    screen while they do. It is fixed to the bottom and the card reserves exactly its
    height, so if that sum does not fit, the person who has never been here before is the
    one who has to scroll to reach the button.
    """

    page.set_viewport_size({"width": width, "height": height})
    _open(page, base_url, path)
    # The banner measures itself and publishes its height, and the card reserves that
    # room from it. Both have to have happened before the page height means anything.
    page.wait_for_timeout(700)
    overflow = page.evaluate(
        "() => document.documentElement.scrollHeight - window.innerHeight"
    )
    # One pixel of tolerance for sub-pixel rounding, and no more: two pixels is a
    # scrollbar.
    assert overflow <= 1, f"{path} at {width}x{height} scrolls by {overflow}px"


@pytest.mark.parametrize("path", ALL_STATES)
def test_there_is_exactly_one_first_level_heading(page: Page, base_url: str, path: str) -> None:
    _open(page, base_url, path)
    expect(page.locator("h1")).to_have_count(1)


@pytest.mark.parametrize("path", ALL_STATES)
def test_the_shared_icon_set_drew_every_icon(page: Page, base_url: str, path: str) -> None:
    """An unknown name falls back to the "i" mark, so a typo is a silent wrong icon."""

    _open(page, base_url, path)
    page.wait_for_timeout(200)
    empty = page.evaluate(
        "Array.from(document.querySelectorAll('[data-icon]'))"
        ".filter((node) => !node.querySelector('svg'))"
        ".map((node) => node.dataset.icon)"
    )
    assert empty == [], empty
    unknown = page.evaluate(
        "Array.from(document.querySelectorAll('[data-icon]'))"
        ".filter((node) => !window.iconNames().includes(node.dataset.icon))"
        ".map((node) => node.dataset.icon)"
    )
    assert unknown == [], unknown


@pytest.mark.parametrize("path", ALL_STATES)
def test_the_page_is_full_of_icons(page: Page, base_url: str, path: str) -> None:
    _open(page, base_url, path)
    page.wait_for_timeout(200)
    assert page.locator("[data-icon] svg").count() >= 12


# ---------------------------------------------------------------------------
# Contrast, on the rendered page.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ALL_STATES)
def test_every_word_on_the_rendered_page_is_readable(
    page: Page, base_url: str, path: str
) -> None:
    """Computed from what the browser actually painted, not from the stylesheet."""

    _open(page, base_url, path)
    page.wait_for_timeout(300)
    failures = page.evaluate(
        """() => {
          const channel = (v) => {
            v /= 255;
            return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
          };
          const parts = (value) => (value.match(/\\d+(\\.\\d+)?/g) || []).map(Number);
          const lum = (rgb) =>
            0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
          const behind = (node) => {
            let cursor = node;
            while (cursor) {
              const value = parts(getComputedStyle(cursor).backgroundColor);
              if (value.length && (value.length < 4 || value[3] > 0.95)) return value;
              cursor = cursor.parentElement;
            }
            return [255, 255, 255];
          };
          const out = [];
          for (const node of document.querySelectorAll('body *')) {
            const text = Array.from(node.childNodes)
              .filter((child) => child.nodeType === 3)
              .map((child) => child.textContent.trim())
              .join('');
            if (!text) continue;
            const style = getComputedStyle(node);
            if (style.visibility === 'hidden' || style.display === 'none') continue;
            if (!node.getClientRects().length) continue;
            if (Number(style.opacity) < 0.9) continue;
            const size = parseFloat(style.fontSize);
            const weight = Number(style.fontWeight) || 400;
            const large = size >= 24 || (size >= 18.66 && weight >= 700);
            const need = large ? 3 : 4.5;
            const back = behind(node);
            let front = parts(style.color);
            if (front.length > 3 && front[3] < 1) {
              front = front.slice(0, 3).map((c, i) => c * front[3] + back[i] * (1 - front[3]));
            }
            const [hi, lo] = [lum(front), lum(back)].sort((a, b) => b - a);
            const ratio = (hi + 0.05) / (lo + 0.05);
            if (ratio < need) {
              out.push([
                text.slice(0, 40), '::', style.color,
                'on', 'rgb(' + back + ')', '=', ratio.toFixed(2),
                'needs', need,
              ].join(' '));
            }
          }
          return out;
        }"""
    )
    assert failures == [], failures


@pytest.mark.parametrize("path", ALL_STATES)
def test_every_text_box_has_an_edge_a_person_can_see(
    page: Page, base_url: str, path: str
) -> None:
    """WCAG 1.4.11. The old boxes measured 1.41:1 — drawn in the code, absent on screen."""

    _open(page, base_url, path)
    boxes = page.locator(".auth-input, .auth-code-cell")
    assert boxes.count() >= 1
    for index in range(boxes.count()):
        box = boxes.nth(index)
        edge = box.evaluate("node => getComputedStyle(node).borderTopColor")
        fill = box.evaluate("node => getComputedStyle(node).backgroundColor")
        assert _contrast(page, edge, fill) >= 3.0, f"{edge} on {fill}"


@pytest.mark.parametrize("path", ALL_STATES)
def test_the_keyboard_can_always_see_where_it_is(page: Page, base_url: str, path: str) -> None:
    """One indicator, on every surface these pages paint.

    The product draws **two** rings, and that is the point of them: a near-black inner
    ring that is visible on white, and an apple-green halo that is visible on near-black.
    Whichever surface the control sits on, one of the two clears 3:1 against it — so the
    check is "at least one ring", not "the outline", which would fail on the dark panel
    for an indicator that is in fact perfectly visible there.

    The ring is drawn *outside* the control, so it is measured against whatever the
    control is sitting **on**, not against the control's own fill.
    """

    _open(page, base_url, path)
    seen = 0
    for _ in range(20):
        page.keyboard.press("Tab")
        marked = page.evaluate(
            """() => {
              const node = document.activeElement;
              if (!node || node === document.body) return null;
              const style = getComputedStyle(node);
              const behind = (start) => {
                let cursor = start;
                while (cursor) {
                  const colour = getComputedStyle(cursor).backgroundColor;
                  const parts = (colour.match(/\\d+(\\.\\d+)?/g) || []).map(Number);
                  if (parts.length && (parts.length < 4 || parts[3] > 0.95)) return colour;
                  cursor = cursor.parentElement;
                }
                return 'rgb(255, 255, 255)';
              };
              const halo = (style.boxShadow.match(/rgba?\\([^)]*\\)/) || [])[0] || '';
              return {
                width: style.outlineWidth,
                ring: style.outlineColor,
                halo,
                surface: behind(node.parentElement),
                tag: node.tagName,
                cls: node.className,
              };
            }"""
        )
        if not marked:
            continue
        if not marked["cls"] or "cookie" not in str(marked["cls"]):
            seen += 1
        assert marked["width"] != "0px", marked
        rings = [marked["ring"]]
        if marked["halo"]:
            rings.append(marked["halo"])
        best = max(_contrast(page, colour, marked["surface"]) for colour in rings)
        assert best >= 3.0, {**marked, "measured": best}
    assert seen >= 6, "the form cannot be walked with a keyboard"


@pytest.mark.parametrize("path", ALL_STATES)
def test_every_target_is_big_enough_to_press(page: Page, base_url: str, path: str) -> None:
    """WCAG 2.5.8 asks for 44x44. The old legal row sat at 32px.

    The success criterion exempts a target that sits inside a sentence, which is what a
    `display: inline` link is — "Already have an account? **Sign in**" cannot be 44px tall
    without breaking the line it belongs to. Every target that is its own control is held
    to the rule.
    """

    _open(page, base_url, path)
    # The card animates in, and a box measured while it is still moving is smaller than
    # the one anybody ever presses: a 44px link reads as 43.5px mid-flight. That made
    # this test fail on a different page on each run. Measure the page at rest, which is
    # the page a person actually meets.
    page.wait_for_timeout(700)
    small = page.evaluate(
        """() => {
          const out = [];
          for (const node of document.querySelectorAll('a, button, input, [role=button]')) {
            const style = getComputedStyle(node);
            if (style.display === 'none' || style.visibility === 'hidden') continue;
            if (style.display === 'inline') continue;
            const box = node.getBoundingClientRect();
            if (!box.width || !box.height) continue;
            if (node.closest('.cookie-banner, .cookie-modal-backdrop')) continue;
            if (box.height < 44 || box.width < 24) {
              out.push([
                node.tagName + '.' + node.className,
                Math.round(box.width) + 'x' + Math.round(box.height),
              ].join(' '));
            }
          }
          return out;
        }"""
    )
    assert small == [], small


# ---------------------------------------------------------------------------
# The banner, which used to be green whatever it said.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expect_title"),
    [
        ("/signin?error=invalid_login", "That email and password do not match"),
        ("/signup?error=account_exists", "You already have an account"),
        ("/signin/code?error=invalid_code&email=a%40b.com", "That code did not work"),
        ("/reset-password?error=account_not_registered", "We cannot find that account"),
        ("/signin?error=smtp_authentication_failed", "We could not send the email"),
        ("/signin?error=a_code_nobody_has_ever_seen", "Something went wrong"),
    ],
)
def test_a_failure_looks_like_a_failure(
    page: Page, base_url: str, path: str, expect_title: str
) -> None:
    """It did not. Every error was painted in the success colours.

    `.hilal-auth-page :where(.dash-flash.error, ...)` has the specificity of a single
    class, and the plain `.dash-flash` rule above it had two — so the override lost, on
    all five pages, for every failure the product can report.
    """

    _open(page, base_url, path)
    banner = page.locator("[data-auth-alert]")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text(expect_title)
    assert banner.get_attribute("data-tone") == "error"

    fill = banner.evaluate("node => getComputedStyle(node).backgroundColor")
    success = page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--hm-apple-soft').trim()"
    )
    assert success, "the palette did not load"
    # Red family, not green: more red than green in the panel behind the words.
    red, green, blue = [int(value) for value in re.findall(r"\d+", fill)[:3]]
    assert red > green and red > blue, f"an error is painted {fill}"


def test_an_error_says_it_is_an_error_without_using_colour(
    page: Page, base_url: str
) -> None:
    _open(page, base_url, "/signin?error=invalid_login")
    assert "Problem:" in page.locator("[data-auth-alert] strong").inner_text()
    assert page.locator("[data-auth-alert] [data-icon]").count() >= 1


def test_a_failure_takes_the_keyboard_to_itself(page: Page, base_url: str) -> None:
    """A message already in the page when it loads is not announced by `role=alert`."""

    _open(page, base_url, "/signin?error=invalid_login")
    page.wait_for_timeout(400)
    focused = page.evaluate("document.activeElement?.dataset?.authAlert !== undefined")
    assert focused


def test_a_refusal_offers_the_next_step_and_keeps_the_plan(
    page: Page, base_url: str
) -> None:
    # A plan the product currently sells: a code it does not is dropped on purpose, so
    # asking for one here would prove nothing about carrying a real choice through.
    _open(
        page,
        base_url,
        "/signup?error=account_exists&plan_code=trader&billing_interval=monthly",
    )
    action = page.locator("[data-auth-alert] a.auth-alert-action")
    expect(action).to_be_visible()
    href = action.get_attribute("href") or ""
    assert href.startswith("/signin")
    assert "plan_code=trader" in href
    # And the hidden field on the form carries it too, so pressing Send keeps it.
    assert page.locator("form.auth-form input[name='plan_code']").input_value() == "trader"


def test_a_bad_code_offers_a_new_one_from_inside_the_banner(
    page: Page, base_url: str
) -> None:
    """The button in the banner drives the resend form further down the page."""

    _open(page, base_url, "/signin/code?error=invalid_code&email=a%40b.com")
    action = page.locator("[data-auth-alert] button.auth-alert-action")
    expect(action).to_be_visible()
    assert action.get_attribute("form") == "auth-resend-form"
    assert page.locator("form#auth-resend-form").count() == 1


def test_a_success_looks_like_a_success(page: Page, base_url: str) -> None:
    _open(page, base_url, "/signin?message=password_reset_successful")
    banner = page.locator("[data-auth-alert]")
    assert banner.get_attribute("data-tone") == "success"
    expect(banner).to_contain_text("Your password is changed")


# ---------------------------------------------------------------------------
# The journey.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "here", "counter"),
    [
        ("/signup", "Your details", "Step 1 of 3"),
        (SIGNUP_PASSWORD, "Your password", "Step 2 of 3"),
        ("/signup/verify?message=code_sent&email=a%40b.com", "Confirm your email", "Step 3 of 3"),
        ("/signin/code", "Ask for a code", "Step 1 of 2"),
        ("/signin/code?message=code_sent&email=a%40b.com", "Enter the code", "Step 2 of 2"),
        ("/reset-password", "Ask for a code", "Step 1 of 2"),
        (
            "/reset-password?message=code_sent&email=a%40b.com",
            "Choose a new password",
            "Step 2 of 2",
        ),
    ],
)
def test_the_page_says_where_you_are(
    page: Page, base_url: str, path: str, here: str, counter: str
) -> None:
    """Signing up is three pages, and the chip above the heading says which one.

    It used to be a tall list down the side of the screen. The list is gone with the
    panel that carried it; the sentence it was really saying — "step 2 of 3, your
    password" — is now beside the heading, where somebody reads it.
    """

    _open(page, base_url, path)
    chip = page.locator(".auth-step-chip")
    expect(chip).to_have_count(1)
    expect(chip).to_contain_text(counter)
    expect(chip).to_contain_text(here)


def test_a_single_step_is_not_dressed_up_as_a_journey(page: Page, base_url: str) -> None:
    _open(page, base_url, "/signin")
    expect(page.locator(".auth-step-chip")).to_have_count(0)


@pytest.mark.parametrize("path", ALL_STATES)
def test_three_ticks_sit_under_the_button_on_every_page(
    page: Page, base_url: str, path: str
) -> None:
    """Two things the product does, one thing it never does.

    They used to live in a dark column opposite the form, where nobody read them. Under
    the button is where a person looks just before pressing it — so that is where the
    "we never place an order" boundary is now, on every page and in every state.
    """

    _open(page, base_url, path)
    promises = page.locator(".auth-promise")
    expect(promises).to_have_count(3)
    # The boundary is in the title now. The sentence that used to carry it — "never
    # places an order and never holds your money" — was small print under a tick, and
    # three of those made the card taller than a short window.
    expect(page.locator(".auth-promises")).to_contain_text("Watching, never trading")

    # And they really are below the button, not above it.
    button = page.locator(".auth-submit").first.bounding_box()
    ticks = page.locator(".auth-promises").bounding_box()
    assert button is not None and ticks is not None
    assert ticks["y"] > button["y"], "the promises are above the button"


# ---------------------------------------------------------------------------
# The password box.
# ---------------------------------------------------------------------------


def test_the_checklist_ticks_itself_off_as_you_type(page: Page, base_url: str) -> None:
    """Four rules, from the same module the server checks with.

    There were five. The fifth asked for a symbol and it is gone — an ordinary
    letters-and-numbers password finishes the list now, and the last box below proves
    the page agrees with the server about that.
    """

    _open(page, base_url, SIGNUP_PASSWORD)
    field = page.get_by_test_id("auth-password")
    rules = page.locator(".auth-rule")
    expect(rules).to_have_count(4)
    assert page.locator('.auth-rule[data-met="true"]').count() == 0

    field.fill("a")
    assert page.locator('.auth-rule[data-met="true"]').count() == 1
    field.fill("aaaaaa")
    assert page.locator('.auth-rule[data-met="true"]').count() == 2
    field.fill("aaaaaA")
    assert page.locator('.auth-rule[data-met="true"]').count() == 3
    field.fill("aaaaaA7")
    assert page.locator('.auth-rule[data-met="true"]').count() == 4
    # No punctuation anywhere, and the list is finished.
    field.fill("Halal2026")
    assert page.locator('.auth-rule[data-met="true"]').count() == 4


def test_the_browser_and_the_server_agree_about_every_password(
    page: Page, base_url: str
) -> None:
    """The two must never disagree.

    A browser that ticks a rule the server refuses sends somebody back with no idea
    what changed; a browser stricter than the server only ever asks for a slightly
    stronger password. So the browser is allowed to be stricter and never looser — and
    that is what is measured here, over letters from outside the ASCII range, which is
    exactly where an ASCII-only pattern would have been looser than `str.islower()`.
    """

    from ai_market_monitor.core.auth_pages import PASSWORD_RULES

    samples = [
        "TraceEdge1!",
        "aaaaaA7!",
        "ÉÉÉÉÉé7!",  # Latin letters with accents: cased, and not a-z or A-Z
        "аааааА7!",  # Cyrillic: `islower()` is true, `[a-z]` is not
        "AAAAA7!!",
        "aaaaa7!!",
        "aaaaaA!!",
        "aaaaaA77",
        "aA7!",
        "١٢٣٤٥٦aA!",  # Arabic-Indic digits: `isdigit()` is true
        "",
    ]
    _open(page, base_url, SIGNUP_PASSWORD)
    measured = page.evaluate(
        """(samples) => {
          const rules = window.HilalMarketsAuth.passwordRules.map(
            (rule) => [rule.key, new RegExp(rule.pattern, 'u')],
          );
          return samples.map((value) =>
            rules.filter(([, test]) => value.length > 0 && test.test(value)).map(([key]) => key),
          );
        }""",
        samples,
    )
    for value, browser_met in zip(samples, measured, strict=True):
        for rule in PASSWORD_RULES:
            server_met = bool(value) and rule.check(value)
            if rule.key in browser_met:
                assert server_met, (
                    f"the browser ticked {rule.key} for {value!r} and the server refuses it"
                )


def test_the_password_can_be_looked_at(page: Page, base_url: str) -> None:
    _open(page, base_url, SIGNUP_PASSWORD)
    field = page.get_by_test_id("auth-password")
    field.fill("TraceEdge1!")
    reveal = page.locator("[data-reveal]").first
    assert field.get_attribute("type") == "password"
    reveal.click()
    assert field.get_attribute("type") == "text"
    assert reveal.get_attribute("aria-pressed") == "true"
    expect(reveal).to_contain_text("Hide")
    reveal.click()
    assert field.get_attribute("type") == "password"
    assert reveal.get_attribute("aria-pressed") == "false"


def test_the_reveal_button_always_has_a_name(page: Page, base_url: str) -> None:
    """On a phone the word is clipped, never removed — `display: none` erases the name."""

    page.set_viewport_size({"width": 360, "height": 780})
    _open(page, base_url, SIGNUP_PASSWORD)
    name = page.locator("[data-reveal]").first.evaluate(
        "node => node.innerText || node.textContent"
    )
    assert "Show" in (name or "")


def test_the_two_passwords_are_compared_while_you_type(page: Page, base_url: str) -> None:
    _open(page, base_url, SIGNUP_PASSWORD)
    page.get_by_test_id("auth-password").fill("TraceEdge1!")
    page.get_by_test_id("auth-repeat-password").fill("TraceEdge1")
    note = page.locator("[data-password-match]")
    expect(note).to_be_visible()
    expect(note).to_contain_text("not the same")
    page.get_by_test_id("auth-repeat-password").fill("TraceEdge1!")
    expect(note).to_contain_text("the same")


def test_an_empty_form_says_which_box_and_puts_the_cursor_in_it(
    page: Page, base_url: str
) -> None:
    _open(page, base_url, "/signup")
    page.get_by_test_id("auth-submit").click()
    expect(page.locator("[data-field-error]:not([hidden])").first).to_be_visible()
    # The *first* empty box, which is the name — the cursor goes to the top of the form
    # rather than to whichever field the script happened to check last.
    focused = page.evaluate("document.activeElement?.name")
    assert focused == "display_name"
    assert page.url.endswith("/signup"), "the form was sent with nothing in it"

    # And once the name is filled in, the same press moves to the next empty box rather
    # than sending a form that is still incomplete.
    page.get_by_test_id("auth-name").fill("Amina Yusuf")
    page.get_by_test_id("auth-submit").click()
    assert page.evaluate("document.activeElement?.name") == "email"
    assert page.url.endswith("/signup"), "the form was sent with no address in it"


def test_a_typo_in_an_email_is_caught_beside_the_box(page: Page, base_url: str) -> None:
    _open(page, base_url, "/signin")
    page.get_by_test_id("auth-email").fill("someone-at-example.com")
    page.get_by_test_id("auth-password").click()
    error = page.locator('.auth-field[data-field="email"] [data-field-error]')
    expect(error).to_be_visible()
    expect(error).to_contain_text("@")
    assert page.get_by_test_id("auth-email").get_attribute("aria-invalid") == "true"


# ---------------------------------------------------------------------------
# Six digits.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/signup/verify?message=code_sent&email=a%40b.com",
        "/signin/code?message=code_sent&email=a%40b.com",
        "/reset-password?message=code_sent&email=a%40b.com",
    ],
)
def test_the_code_lands_in_six_boxes(page: Page, base_url: str, path: str) -> None:
    _open(page, base_url, path)
    field = page.locator("input[name='code']")
    expect(page.locator(".auth-code-cell")).to_have_count(6)
    field.fill("123456")
    digits = page.locator(".auth-code-cell").all_inner_texts()
    assert digits == ["1", "2", "3", "4", "5", "6"]
    assert page.locator('.auth-code-cell[data-filled="true"]').count() == 6


def test_a_pasted_code_with_a_space_in_it_still_works(page: Page, base_url: str) -> None:
    """`maxlength=6` cut "123 456" down to "123 45" before anything could clean it."""

    _open(page, base_url, "/signin/code?message=code_sent&email=a%40b.com")
    page.locator("input[name='code']").click()
    page.evaluate(
        """() => {
          const field = document.querySelector("input[name='code']");
          const data = new DataTransfer();
          data.setData('text/plain', 'Your code is 123 456');
          const event = new ClipboardEvent('paste', {
            clipboardData: data,
            bubbles: true,
            cancelable: true,
          });
          field.dispatchEvent(event);
        }"""
    )
    assert page.locator("input[name='code']").input_value() == "123456"


def test_letters_never_reach_the_code_box(page: Page, base_url: str) -> None:
    _open(page, base_url, "/signin/code?message=code_sent&email=a%40b.com")
    field = page.locator("input[name='code']")
    field.fill("12ab34cd56")
    assert field.input_value() == "123456"


def test_the_code_boxes_survive_with_no_script(page: Page, base_url: str) -> None:
    """One input, not six, so it is still an ordinary usable box without JavaScript."""

    _open(page, base_url, "/signin/code?message=code_sent&email=a%40b.com")
    assert page.locator("input[name='code']").count() == 1
    assert page.locator("input[name='code']").is_visible()


def test_the_page_says_which_address_the_code_went_to(page: Page, base_url: str) -> None:
    _open(page, base_url, "/signup/verify?message=code_sent&email=typo%40exmaple.com")
    expect(page.locator(".auth-sentto")).to_contain_text("typo@exmaple.com")


# ---------------------------------------------------------------------------
# Asking for another code.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/signup/verify?message=code_sent&email=a%40b.com",
        "/signin/code?message=code_sent&email=a%40b.com",
        "/reset-password?message=code_sent&email=a%40b.com",
    ],
)
def test_the_resend_button_counts_the_real_wait_down(
    page: Page, base_url: str, path: str
) -> None:
    """Sixty seconds, from the constant the server refuses an early request with."""

    _open(page, base_url, path)
    button = page.locator("[data-resend]")
    expect(button).to_be_visible()
    expect(button).to_be_disabled()
    first = button.inner_text()
    assert re.search(r"\d+s", first), first
    page.wait_for_timeout(2200)
    assert button.inner_text() != first, "the countdown is not counting"


def test_a_new_code_really_arrives(page: Page, base_url: str) -> None:
    """The confirm step used to offer only "Start again" and an empty form."""

    signup_to_verify(
        page,
        base_url,
        email=unique_email("auth-resend"),
        name="Resend Tester",
        password="TraceEdge1!",
    )

    # The wait is real, so the button is refused until it has passed. Sending the form
    # directly is what a person does a minute later.
    page.evaluate("() => document.querySelector('#auth-resend-form').submit()")
    page.wait_for_url(re.compile(r".*/signup/verify\?.*"), timeout=20_000)
    banner = page.locator("[data-auth-alert]")
    expect(banner).to_be_visible()
    # Either the code went out again, or it was refused because one had just been sent.
    # Both are correct answers; a page that says nothing is not.
    assert banner.get_attribute("data-tone") in {"success", "info"}
    page.locator("input[name='code']").fill("123456")
    page.get_by_role("button", name=re.compile("Verify and create account", re.I)).click()
    page.wait_for_url(re.compile(r".*/(dashboard|home)(\?.*)?$"), timeout=20_000)


# ---------------------------------------------------------------------------
# The cookie banner, which had no styling here at all.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", PAGES)
def test_the_cookie_banner_is_a_floating_panel_not_a_block_of_text(
    page: Page, base_url: str, path: str
) -> None:
    """It rendered raw on all four pages, because neither stylesheet that draws it loads here."""

    _open(page, base_url, path)
    banner = page.locator("[data-cookie-banner]")
    expect(banner).to_be_visible()
    assert banner.evaluate("node => getComputedStyle(node).position") == "fixed"
    box = banner.bounding_box()
    assert box is not None
    assert box["y"] + box["height"] <= page.viewport_size["height"] + 2


@pytest.mark.parametrize("path", PAGES)
def test_nothing_hidden_from_a_screen_reader_is_on_the_screen(
    page: Page, base_url: str, path: str
) -> None:
    """The settings window carried `aria-hidden` and was laid out in the page anyway.

    Tabbing off the sign-in button walked into six controls a screen reader had been
    told were not there.
    """

    _open(page, base_url, path)
    visible_but_hidden = page.evaluate(
        """() => Array.from(document.querySelectorAll('[aria-hidden="true"]'))
             .filter((node) => node.querySelector('a, button, input, select, textarea'))
             .filter((node) => node.getClientRects().length > 0)
             .map((node) => node.className || node.tagName)"""
    )
    assert visible_but_hidden == [], visible_but_hidden


@pytest.mark.parametrize("path", PAGES)
def test_the_consent_switches_are_a_real_size(page: Page, base_url: str, path: str) -> None:
    """The rule that sized them was thrown away by the browser: its comment was malformed."""

    _open(page, base_url, path)
    page.locator("[data-cookie-customize]").first.click()
    boxes = page.locator(".cookie-category input")
    assert boxes.count() >= 2
    for index in range(boxes.count()):
        box = boxes.nth(index).bounding_box()
        assert box is not None
        assert box["width"] >= 20 and box["height"] >= 20, box


# ---------------------------------------------------------------------------
# Motion.
# ---------------------------------------------------------------------------


def test_the_page_really_animates(page: Page, base_url: str) -> None:
    """A library call can be made and do nothing. `spring()` once threw on import here."""

    page.goto(f"{base_url}/signup", wait_until="domcontentloaded")
    running = page.evaluate(
        "document.getAnimations().filter((animation) => animation.playState !== 'idle').length"
    )
    assert running > 0, "nothing on the page moved"


@pytest.mark.parametrize(
    ("path", "selector"),
    [
        ("/signup", ".auth-submit"),
        ("/signup", ".auth-legal a"),
        ("/signin", ".auth-alt"),
        ("/signin", ".auth-forgot"),
        ("/signin", ".auth-back"),
        ("/signin", ".auth-input"),
        ("/signin/code?message=code_sent&email=a%40b.com", ".auth-switch a"),
        ("/signin?error=invalid_login", ".auth-alert-action"),
    ],
)
def test_everything_answers_the_pointer(
    page: Page, base_url: str, path: str, selector: str
) -> None:
    """"Motion everywhere, hover included" — checked by hovering and measuring.

    A `transition` in a stylesheet proves nothing on its own: the property it names may
    never change. This hovers the real element and insists something visible moved.
    """

    _open(page, base_url, path)
    # Wait for the entrance to finish first. The cards arrive with a short slide, so a
    # pointer placed mid-animation is left behind when the element settles — and the
    # hover it was testing is lost with it.
    page.wait_for_function(
        "() => document.getAnimations().every((one) => one.playState !== 'running')",
        timeout=5_000,
    )
    target = page.locator(selector).first
    expect(target).to_be_visible()
    before = target.evaluate(
        """node => {
          const style = getComputedStyle(node);
          return [style.backgroundColor, style.borderTopColor, style.color, style.transform]
            .join('|');
        }"""
    )
    target.hover()
    page.wait_for_timeout(400)
    after = target.evaluate(
        """node => {
          const style = getComputedStyle(node);
          return [style.backgroundColor, style.borderTopColor, style.color, style.transform]
            .join('|');
        }"""
    )
    assert before != after, f"{selector} on {path} does not answer the pointer"


def test_asking_for_less_motion_stops_all_of_it(page: Page, base_url: str) -> None:
    context = page.context
    context.clear_cookies()
    reduced = context.new_page()
    reduced.emulate_media(reduced_motion="reduce")
    reduced.goto(f"{base_url}/signup", wait_until="domcontentloaded")
    reduced.wait_for_timeout(500)
    moving = reduced.evaluate(
        """() => Array.from(document.querySelectorAll('body *'))
             .filter((node) => {
               const style = getComputedStyle(node);
               return style.transitionDuration.split(',').some((value) => parseFloat(value) > 0.05)
                 || style.animationDuration.split(',').some((value) => parseFloat(value) > 0.05);
             })
             .map((node) => node.className)
             .slice(0, 5)"""
    )
    assert moving == [], moving
    # And the page is still complete: nothing was left invisible by an animation that
    # never ran.
    assert reduced.locator("h1").is_visible()
    assert reduced.locator(".auth-promise").first.is_visible()
    reduced.close()


# ---------------------------------------------------------------------------
# The whole way in, end to end.
# ---------------------------------------------------------------------------


def _sign_up_through_three_steps(
    page: Page, base_url: str, email: str, name: str = "Amina Yusuf"
) -> None:
    """Name and email, then password, then the code.

    The walk itself lives in `conftest.signup_to_verify`, which every browser file uses.
    Three copies of it existed and only this one was updated when the pages became three
    screens, so the whole browser suite sat waiting for a box that no page had.

    The password here has no punctuation on purpose: the symbol rule is gone, and this
    is the proof it is gone on the real server and not only in the checklist.
    """

    signup_to_verify(page, base_url, email=email, name=name, password="Halal2026")


def test_an_email_already_in_use_is_caught_before_a_password_is_chosen(
    page: Page, base_url: str
) -> None:
    """The whole reason the first step asks one question.

    On the old single screen a person invented a password, typed it twice, waited for
    the round trip and only then learned the address was taken — and the password they
    had just chosen was thrown away with the page.
    """

    email = unique_email("auth-taken")
    _sign_up_through_three_steps(page, base_url, email)
    page.locator("input[name='code']").fill("123456")
    page.get_by_role("button", name=re.compile("Verify and create account", re.I)).click()
    page.wait_for_url(re.compile(r".*/(dashboard|home)(\?.*)?$"), timeout=20_000)

    page.goto(f"{base_url}/signup", wait_until="domcontentloaded")
    page.get_by_test_id("auth-name").fill("Amina Yusuf")
    page.get_by_test_id("auth-email").fill(email)
    page.get_by_test_id("auth-submit").click()
    page.wait_for_url(re.compile(r".*/signup\?.*"), timeout=20_000)
    expect(page.locator("[data-auth-alert]")).to_contain_text("already have an account")
    # Still on step one, and no password box was ever shown.
    expect(page.get_by_test_id("auth-password")).to_have_count(0)


def test_a_person_can_sign_up_sign_out_and_sign_back_in(page: Page, base_url: str) -> None:
    """The journey the redesign is for, walked once in full."""

    email = unique_email("auth-e2e")
    _sign_up_through_three_steps(page, base_url, email)
    expect(page.locator(".auth-sentto")).to_contain_text(email)
    page.locator("input[name='code']").fill("123456")
    page.get_by_role("button", name=re.compile("Verify and create account", re.I)).click()
    page.wait_for_url(re.compile(r".*/(dashboard|home)(\?.*)?$"), timeout=20_000)

    page.goto(f"{base_url}/signin", wait_until="domcontentloaded")
    page.get_by_test_id("auth-email").fill(email)
    page.get_by_test_id("auth-password").fill("WrongOne1")
    page.get_by_test_id("auth-submit").click()
    page.wait_for_url(re.compile(r".*/signin\?.*"), timeout=20_000)
    # The address survives the refusal instead of having to be typed again.
    assert page.get_by_test_id("auth-email").input_value() == email
    expect(page.locator("[data-auth-alert]")).to_contain_text("do not match")

    page.get_by_test_id("auth-password").fill("Halal2026")
    page.get_by_test_id("auth-submit").click()
    page.wait_for_url(re.compile(r".*/(dashboard|home)(\?.*)?$"), timeout=20_000)


def test_the_one_time_code_door_works_from_end_to_end(page: Page, base_url: str) -> None:
    email = unique_email("auth-code")
    _sign_up_through_three_steps(page, base_url, email)
    page.locator("input[name='code']").fill("123456")
    page.get_by_role("button", name=re.compile("Verify and create account", re.I)).click()
    page.wait_for_url(re.compile(r".*/(dashboard|home)(\?.*)?$"), timeout=20_000)
    page.goto(f"{base_url}/signin", wait_until="domcontentloaded")

    page.locator("a.auth-alt").click()
    page.wait_for_url(re.compile(r".*/signin/code(\?.*)?$"), timeout=20_000)
    page.get_by_test_id("auth-email").fill(email)
    page.get_by_test_id("auth-submit").click()
    page.wait_for_url(re.compile(r".*/signin/code\?.*"), timeout=20_000)
    expect(page.locator("[data-auth-alert]")).to_contain_text("Code sent")
    page.locator("input[name='code']").fill("123456")
    page.get_by_test_id("auth-submit").click()
    page.wait_for_url(re.compile(r".*/(dashboard|home)(\?.*)?$"), timeout=20_000)


# ---------------------------------------------------------------------------
# The Google door.
# ---------------------------------------------------------------------------


def test_the_google_button_is_not_drawn_when_it_could_not_work(
    page: Page, base_url: str
) -> None:
    """The test server has no Google keys, so the button must not be on the page.

    A button that opens a window and then fails is worse than no button. This is the
    "offered but not runnable" fault written as a browser measurement, because whether
    a control is *drawn* is a question only the rendered page can answer.
    """

    _open(page, base_url, "/signup")
    expect(page.get_by_test_id("auth-google")).to_have_count(0)
    _open(page, base_url, "/signin")
    expect(page.get_by_test_id("auth-google")).to_have_count(0)
    # The other door on the sign-in page is still there, so this is not "nothing renders".
    expect(page.get_by_test_id("auth-code-door")).to_have_count(1)


def test_the_google_route_refuses_in_plain_words_when_it_is_not_configured(
    page: Page, base_url: str
) -> None:
    """Somebody with an old link, or a kept bookmark, still gets a sentence.

    It used to be possible for a route like this to answer with a stack trace or a bare
    code. It lands back on the sign-up page with an alert that names no OAuth vocabulary.
    """

    page.goto(f"{base_url}/auth/google/start?mode=signup", wait_until="domcontentloaded")
    page.wait_for_url(re.compile(r".*/signup\?.*error=google_disabled.*"), timeout=20_000)
    alert = page.locator("[data-auth-alert]")
    expect(alert).to_be_visible()
    words = alert.inner_text().lower()
    assert "switched off" in words
    for jargon in ("oauth", "client", "token", "redirect"):
        assert jargon not in words, f"the refusal leaks {jargon!r}"
