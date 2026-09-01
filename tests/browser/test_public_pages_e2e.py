"""The rebuilt public pages, measured in a real browser.

Everything here is a claim that only a browser can settle. A stylesheet can *declare* a
3:1 border and a later rule can override it; a dialog can *contain* the code for a focus
trap and still not trap focus; an animation can be created and do nothing. Each of those
has happened in this repository, so each is measured on the rendered page rather than
read from the source.

The suite covers the landing page, `/contact`, `/privacy`, `/terms`, `/cookies`,
`/features` and `/how-it-works`. They share one header, one footer and one set of cards
and controls, so a rule proved on one of them is only proved on the rest when the rest
are measured too — which is why almost every check below is parametrised over all of
them rather than written against the page it was first noticed on.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import (
    assert_no_horizontal_overflow,
    assert_no_raw_traceback,
    unique_email,
)

#: The pages built from the shared design system, measured under every rule below.
#:
#: `/cookies`, `/features` and `/how-it-works` joined the other three when they were
#: rebuilt as React pages. They use the same header, footer, cards and controls, so a
#: rule proved on one of them is only proved on the rest if the rest are measured too.
PAGES = (
    "/contact",
    "/privacy",
    "/terms",
    "/cookies",
    "/features",
    "/how-it-works",
    # The published screening standard. It is here rather than exempted because it is
    # the page most likely to grow a palette of its own: it has to show "clean",
    # "problem" and "not judged" side by side, and the temptation is a second green and
    # a second red. It paints the product's existing semantic tones instead, and this is
    # what keeps it that way.
    "/hilal-methodology",
)

#: Every public page a visitor can open, including the landing page.
#:
#: The landing page is not in `PAGES` because it still renders the imported design-file
#: sections, which carry their own colours — the exchange logos among them. It has to
#: render and fit a phone like everything else, so it is measured for that.
ALL_PUBLIC_PAGES = ("/", *PAGES)

#: Every colour the three pages may paint, as the browser reports it.
#:
#: This is the rule "no new main colours", written so a machine can check it. The five
#: brand colours, the neutrals derived from them, and the semantic tones already used
#: elsewhere in the product. A sixth hue appearing here is a colour somebody invented.
ALLOWED_COLOURS = {
    # Brand
    "43,46,53",  # ink
    "203,250,77",  # apple
    "85,113,42",  # apple-deep
    "42,143,195",  # accent blue
    "255,255,255",  # surface
    "245,248,251",  # canvas
    # Neutrals in the same blue-grey family
    "32,35,41",  # focus ring
    "31,34,41",  # ink hover
    "74,80,90",  # reading text
    "69,75,85",  # icon badge ink
    "92,100,110",  # ink-soft
    "121,130,141",  # control boundary
    "109,116,128",  # placeholder
    "107,116,128",  # input hover
    "195,202,211",  # card hover edge
    "207,214,221",  # spent quota mark
    "219,225,231",  # soft card edge
    "225,229,234",  # hairline
    "228,233,238",  # divider
    "238,241,244",  # neutral badge
    "243,246,249",  # quiet button hover
    "247,249,251",  # row hover
    "250,251,252",  # panel foot
    "251,252,253",  # card hover fill
    # Semantic tones, all already used elsewhere in the product
    "232,251,191",  # apple badge fill
    "63,82,25",  # apple badge ink
    "244,250,234",  # success fill
    "151,185,95",  # success edge
    "51,66,28",  # success ink
    "61,77,34",  # success body
    "43,58,21",  # success heading
    "245,250,236",  # in-short fill
    "246,251,236",  # chosen subject fill
    "244,249,232",  # rail current fill
    "253,247,236",  # warning fill
    "216,171,90",  # warning edge
    "109,77,16",  # warning ink
    "90,63,11",  # warning link
    "253,244,242",  # stop fill
    "215,155,147",  # stop edge
    "121,40,31",  # stop ink
    "141,48,41",  # stop text
    "168,52,42",  # invalid edge
    "255,250,249",  # invalid fill
    "241,247,251",  # info fill
    "169,198,216",  # info edge
    "28,74,99",  # info ink
    "226,241,249",  # blue badge fill
    "28,95,128",  # blue badge ink
    "122,84,16",  # counter near limit
    "248,250,251",  # review panel fill
    # `.hm-chip-live` — the "working today" availability chip. An apple-family tint, in
    # the shared stylesheet and on the landing page since the ecosystem section shipped.
    # It only became measurable when `/hilal-methodology` joined `PAGES` and used the
    # chip inside `main`; the landing page is in `ALL_PUBLIC_PAGES` only, which does not
    # run the palette rule. Recorded, not an exception: this is a colour the product
    # already paints, and the list is meant to be what it paints.
    "241,248,224",  # availability chip fill
}


def _open(page: Page, base_url: str, path: str) -> None:
    """Open a page and wait for the thing that means it is ready.

    Not "wait until the network goes quiet": the page keeps a chat widget and web fonts
    loading, so that measures the slowest thing on the page rather than the page. The
    heading and one drawn icon are what every check below actually needs, and they are
    both there long before the network settles.
    """

    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    page.wait_for_selector("#root h1", timeout=20_000)
    page.wait_for_function(
        "() => document.querySelectorAll('main svg').length > 5",
        timeout=15_000,
    )
    assert_no_raw_traceback(page)


# --------------------------------------------------------------------------- #
#  Every page                                                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ALL_PUBLIC_PAGES)
def test_the_page_renders_and_fits_the_screen(page: Page, base_url: str, path: str) -> None:
    """The bundle loaded *and ran*, and the page fits a phone.

    One throwing call at the top level of any module leaves every React page blank while
    the build succeeds, the server returns 200 and the file downloads. Waiting for the
    heading is the cheapest thing that catches it, so every public page waits for one.

    Three widths, because the header collapses at 1024 and the rest of the page at 900,
    and a layout that fits either side of a breakpoint can still overflow between them.
    """

    _open(page, base_url, path)
    expect(page.locator("h1")).to_have_count(1)
    assert_no_horizontal_overflow(page)
    for width in (960, 390, 360):
        page.set_viewport_size({"width": width, "height": 850})
        page.wait_for_timeout(400)
        assert_no_horizontal_overflow(page)


@pytest.mark.parametrize("path", PAGES)
def test_the_page_invents_no_colour(page: Page, base_url: str, path: str) -> None:
    """No sixth hue. The brand palette plus its own neutrals, and nothing else.

    Scoped to `main`, which is the part these three pages own. The header and the
    footer are shared chrome drawn from the Figma prototype and used by the landing
    page too; they were not rebuilt here, and checking them would be checking somebody
    else's work through this test.
    """

    _open(page, base_url, path)
    unexpected = page.evaluate(
        """(allowed) => {
            const ok = new Set(allowed);
            const parse = (value) => {
                const m = String(value || '').match(
                    /^rgba?\\(\\s*(\\d+)\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)(?:\\s*,\\s*([0-9.]+))?\\s*\\)$/
                );
                if (!m || (m[4] !== undefined && Number(m[4]) < 0.06)) return null;
                return `${m[1]},${m[2]},${m[3]}`;
            };
            const found = new Map();
            for (const el of document.querySelectorAll('main *')) {
                const box = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                if (box.width === 0 || box.height === 0) continue;
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                const properties = ['color', 'backgroundColor'];
                for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
                    if (
                        style[`border${side}Style`] !== 'none' &&
                        parseFloat(style[`border${side}Width`]) > 0
                    ) properties.push(`border${side}Color`);
                }
                for (const property of properties) {
                    const rgb = parse(style[property]);
                    if (!rgb || ok.has(rgb)) continue;
                    if (!found.has(rgb)) {
                        found.set(rgb, `${el.tagName.toLowerCase()}.${
                            [...el.classList].slice(0, 3).join('.')
                        } (${property})`);
                    }
                }
            }
            return [...found].map(([colour, where]) => `${colour} on ${where}`);
        }""",
        sorted(ALLOWED_COLOURS),
    )
    assert unexpected == [], (
        f"{path} paints colours that are not in the palette: {unexpected}. "
        "The brand rules allow five colours and the neutrals derived from them."
    )


@pytest.mark.parametrize("path", PAGES)
def test_every_reading_size_of_text_is_readable(page: Page, base_url: str, path: str) -> None:
    """Measured on the rendered page, so a later rule cannot quietly undo a token.

    Scoped to `main` for the same reason as the palette check above: these three pages
    own their body, and the shared header and footer were not part of this work.
    """

    _open(page, base_url, path)
    faint = page.evaluate(
        """() => {
            const channel = (v) => {
                const p = v / 255;
                return p <= 0.03928 ? p / 12.92 : Math.pow((p + 0.055) / 1.055, 2.4);
            };
            const lum = (colour) => {
                const [r, g, b] = colour.match(/[\\d.]+/g).map(Number);
                return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
            };
            const behind = (el) => {
                for (let n = el; n; n = n.parentElement) {
                    const c = getComputedStyle(n).backgroundColor;
                    const parts = (c.match(/[\\d.]+/g) || []);
                    if (parts.length < 4 || Number(parts[3]) > 0.99) return c;
                }
                return 'rgb(255,255,255)';
            };
            const bad = [];
            for (const el of document.querySelectorAll('main *')) {
                if (!el.childNodes.length) continue;
                const text = [...el.childNodes]
                    .filter((n) => n.nodeType === 3)
                    .map((n) => n.textContent.trim())
                    .join('');
                if (text.length < 2) continue;
                const style = getComputedStyle(el);
                const box = el.getBoundingClientRect();
                if (box.width === 0 || box.height === 0) continue;
                if (style.visibility === 'hidden' || Number(style.opacity) < 0.5) continue;
                const size = parseFloat(style.fontSize);
                const weight = Number(style.fontWeight) || 400;
                // WCAG: 24px, or 18.66px at 700+, may use 3:1.
                const large = size >= 24 || (size >= 18.66 && weight >= 700);
                const need = large ? 3 : 4.5;
                const a = lum(style.color);
                const b = lum(behind(el));
                const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
                if (ratio + 0.005 < need) {
                    bad.push({
                        text: text.slice(0, 45),
                        size: Math.round(size * 10) / 10,
                        ratio: Math.round(ratio * 100) / 100,
                        need,
                    });
                }
            }
            return bad.slice(0, 12);
        }"""
    )
    assert faint == [], f"{path} has text under the contrast it needs: {faint}"


@pytest.mark.parametrize("path", PAGES)
def test_the_shared_icon_set_actually_drew_the_icons(
    page: Page, base_url: str, path: str
) -> None:
    """The set arrives in a separate script. An icon left on its fallback means it did not.

    This is exactly the kind of thing that looks fine in the source and ships broken:
    React renders before that script runs, so without the retry every mark on the page
    would be the "unknown icon" circle.
    """

    _open(page, base_url, path)
    marks = page.evaluate(
        """() => {
            const svgs = [...document.querySelectorAll('main svg')];
            return {
                total: svgs.length,
                empty: svgs.filter((s) => s.innerHTML.trim() === '').length,
                // The component says when it used the fallback. Recognising it by its
                // shape does not work: `info`, `radar` and `methodology` are also a
                // circle of radius nine, so a shape check called four correct icons
                // broken.
                missing: svgs
                    .filter((s) => s.dataset.iconMissing === 'true')
                    .map((s) => s.dataset.icon),
            };
        }"""
    )
    assert marks["total"] >= 12, f"{path} draws only {marks['total']} icons"
    assert marks["empty"] == 0, f"{path} has {marks['empty']} empty icons"
    assert marks["missing"] == [], (
        f"{path} asked the shared set for {marks['missing']} and did not get it. "
        "Either the name is a typo or the icon script did not load."
    )


@pytest.mark.parametrize("path", PAGES)
def test_the_keyboard_can_see_where_it_is(page: Page, base_url: str, path: str) -> None:
    """Every control in the page draws the product's one focus indicator.

    Measured on the controls this page owns rather than on "whatever the third Tab
    happens to reach" — the cookie banner and the chat launcher sit in the tab order
    too, and which one a fixed number of presses lands on is not a fact about the page.
    Every focusable control inside `main` is walked instead, which is a stronger claim
    and a stable one.
    """

    _open(page, base_url, path)

    # Walked with the Tab key rather than by calling `focus()`.
    #
    # `:focus-visible` is deliberately not the same thing as `:focus` — a browser shows
    # it for a keyboard, and often not for a script. Measuring after `el.focus()`
    # therefore reads the element's ordinary style and calls a perfectly good ring
    # missing. Pressing Tab is what a keyboard user does, so it is what is measured.
    rings = []
    for _ in range(40):
        page.keyboard.press("Tab")
        # The fields fade their shadow in over 180ms. Reading it the instant Tab lands
        # catches the start of that fade — a transparent shadow — and reports a
        # perfectly good halo as missing. Measured once it has settled.
        page.wait_for_timeout(260)
        measured = page.evaluate(
            """() => {
                const el = document.activeElement;
                if (!el || !el.closest('main')) return null;
                const style = getComputedStyle(el);
                return {
                    what: el.tagName.toLowerCase() + '.' + [...el.classList].slice(0, 2).join('.'),
                    width: parseFloat(style.outlineWidth),
                    style: style.outlineStyle,
                    colour: style.outlineColor,
                    halo: style.boxShadow,
                };
            }"""
        )
        if measured is not None:
            rings.append(measured)
        if len(rings) >= 8:
            break

    assert len(rings) >= 5, f"{path}: only {len(rings)} controls reached by Tab"
    for ring in rings:
        assert ring["style"] != "none", f"{path}: {ring['what']} has no focus outline"
        assert ring["width"] >= 3, f"{path}: {ring['what']} ring is {ring['width']}px"
        assert "32, 35, 41" in ring["colour"], (
            f"{path}: {ring['what']} draws {ring['colour']}, not the product's near-black"
        )
        assert "203, 250, 77" in ring["halo"], (
            f"{path}: {ring['what']} has no accent halo behind its ring"
        )


# --------------------------------------------------------------------------- #
#  Contact                                                                     #
# --------------------------------------------------------------------------- #
def test_the_form_fields_have_an_edge_a_person_can_see(page: Page, base_url: str) -> None:
    """1.4.11, measured. The old fields drew their border at 1.21:1 against their fill."""

    _open(page, base_url, "/contact")
    # A measurement over nothing passes. Count the fields first, so an empty result
    # can only ever mean "every field was fine".
    fields = page.locator("main input, main textarea")
    assert fields.count() >= 4, f"only {fields.count()} fields on the page"
    faint = page.evaluate(
        """() => {
            const channel = (v) => {
                const p = v / 255;
                return p <= 0.03928 ? p / 12.92 : Math.pow((p + 0.055) / 1.055, 2.4);
            };
            const lum = (c) => {
                const [r, g, b] = c.match(/[\\d.]+/g).map(Number);
                return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
            };
            const bad = [];
            for (const el of document.querySelectorAll('main input, main textarea')) {
                const style = getComputedStyle(el);
                if (parseFloat(style.borderTopWidth) === 0) continue;
                const a = lum(style.borderTopColor);
                const b = lum(style.backgroundColor);
                const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
                if (ratio + 0.005 < 3) {
                    bad.push({
                        name: el.getAttribute('id') || el.tagName,
                        border: style.borderTopColor,
                        fill: style.backgroundColor,
                        ratio: Math.round(ratio * 100) / 100,
                    });
                }
            }
            return bad;
        }"""
    )
    assert faint == [], f"Form fields whose edge cannot be seen: {faint}"


def test_choosing_a_subject_changes_what_the_form_asks_for(page: Page, base_url: str) -> None:
    """The subject is not a label on the message; it changes the placeholder in it."""

    _open(page, base_url, "/contact")
    message = page.locator("main [id$='-description']")
    before = message.get_attribute("placeholder") or ""
    page.get_by_text("Plans and payment", exact=True).click()
    page.wait_for_timeout(200)
    after = message.get_attribute("placeholder") or ""
    assert before != after, "The message placeholder did not change with the subject"
    assert "card details" in after.lower()


def test_a_secret_is_caught_before_it_leaves_the_browser(page: Page, base_url: str) -> None:
    """A warning, not a block — and it appears while typing, not after sending."""

    _open(page, base_url, "/contact")
    page.locator("main [id$='-description']").fill(
        "here is my key, api_key = sk_live_9d8f7a6b5c4d3e2f1a0b"
    )
    warning = page.locator("[data-secret-warning]")
    expect(warning).to_be_visible(timeout=4000)
    assert "API key" in warning.inner_text()
    # It warns; it does not take the decision away.
    expect(page.get_by_role("button", name=re.compile("Check and send", re.I))).to_be_enabled()


def test_an_empty_form_says_which_field_and_puts_the_cursor_there(
    page: Page, base_url: str
) -> None:
    """Not one summary at the top. The message is beside the field, and focus moves."""

    _open(page, base_url, "/contact")
    page.get_by_role("button", name=re.compile("Check and send", re.I)).click()
    page.wait_for_timeout(300)
    expect(page.locator("main [id$='-title-error']")).to_be_visible()
    focused = page.evaluate("() => document.activeElement?.getAttribute('data-field')")
    assert focused == "title", f"focus went to {focused!r} instead of the first problem"
    assert page.locator("main [id$='-title']").get_attribute("aria-invalid") == "true"


def test_the_review_window_traps_focus_and_gives_it_back(page: Page, base_url: str) -> None:
    """Eleven rules in the source; this measures the three that strand somebody.

    A dialog that does not hold focus leaves a keyboard user tabbing through the page
    behind it. A dialog that does not give focus back leaves them at the top of the
    document with no idea where they were.
    """

    _open(page, base_url, "/contact")
    page.locator("main [id$='-title']").fill("A question about screening")
    page.locator("main [id$='-email']").fill(unique_email("browser-contact"))
    page.locator("main [id$='-description']").fill(
        "Please explain how the review date on an asset is decided."
    )
    opener = page.get_by_role("button", name=re.compile("Check and send", re.I))
    opener.click()

    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible(timeout=5000)
    assert dialog.get_attribute("aria-modal") == "true"
    # The page behind is hidden from screen readers — and the window is *not*, which
    # only holds because it is rendered outside the application's own root. Written the
    # obvious way, the window sat inside `#root` and was hidden along with it, leaving
    # a screen reader with an empty screen.
    assert page.locator("#root").get_attribute("aria-hidden") == "true"
    assert page.evaluate(
        """() => {
            // `[role="dialog"]` alone finds the cookie banner first — it is earlier in
            // the document and hidden, which is why Playwright's role query skips it and
            // a raw querySelector does not.
            const panel = document.querySelector('.hm-dialog-ground [role="dialog"]');
            if (!panel) return false;
            for (let n = panel; n; n = n.parentElement) {
                if (n.getAttribute('aria-hidden') === 'true') return false;
            }
            return true;
        }"""
    ), "the window is inside something marked aria-hidden, so nothing announces it"

    # Tab all the way round: focus must never leave the window.
    escaped = page.evaluate(
        """() => {
            const panel = document.querySelector('.hm-dialog-ground [role="dialog"]');
            return !panel.contains(document.activeElement);
        }"""
    )
    assert not escaped, "focus was outside the window as it opened"
    for _ in range(12):
        page.keyboard.press("Tab")
    inside = page.evaluate(
        """() => {
            const panel = document.querySelector('.hm-dialog-ground [role="dialog"]');
            return panel.contains(document.activeElement);
        }"""
    )
    assert inside, "Tab walked out of the window"

    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden(timeout=4000)
    returned = page.evaluate("() => document.activeElement?.textContent || ''")
    assert "Check and send" in returned, (
        f"focus went to {returned!r} instead of back to the button that opened it"
    )


def test_a_message_is_sent_and_the_remaining_allowance_is_stated(
    page: Page, base_url: str
) -> None:
    """The whole path, and the number the person is told afterwards.

    The allowance is the point: somebody who has one message left should learn that
    now, not by writing a second one and having it refused.
    """

    _open(page, base_url, "/contact")
    page.locator("main [id$='-title']").fill("How does the evidence Passport work")
    page.locator("main [id$='-email']").fill(unique_email("browser-contact"))
    page.locator("main [id$='-description']").fill(
        "I would like to understand where the sources on an asset come from."
    )
    page.get_by_role("button", name=re.compile("Check and send", re.I)).click()
    expect(page.get_by_role("dialog")).to_be_visible(timeout=5000)
    page.get_by_role("button", name=re.compile("Send message", re.I)).click()

    result = page.locator("[data-contact-result]")
    expect(result).to_be_visible(timeout=15_000)
    assert "Your message was sent." in result.inner_text()
    assert re.search(r"send \d+ more message", result.inner_text()), (
        f"the result does not say what is left: {result.inner_text()!r}"
    )
    # The result takes focus, so it cannot be missed.
    assert page.evaluate(
        "() => document.activeElement?.hasAttribute('data-contact-result')"
    )


@pytest.mark.deliberate_console_errors("429")
def test_the_third_message_is_refused_with_a_reason_and_a_time(
    page: Page, base_url: str
) -> None:
    """Two per email. The third gets a sentence a person can act on, not a bare error."""

    address = unique_email("browser-limit")

    def send(title: str) -> None:
        _open(page, base_url, "/contact")
        page.locator("main [id$='-title']").fill(title)
        page.locator("main [id$='-email']").fill(address)
        page.locator("main [id$='-description']").fill(
            "A question about how the monitoring rules are approved."
        )
        page.get_by_role("button", name=re.compile("Check and send", re.I)).click()
        expect(page.get_by_role("dialog")).to_be_visible(timeout=5000)
        page.get_by_role("button", name=re.compile("Send message", re.I)).click()
        page.wait_for_timeout(1200)

    send("First question")
    send("Second question")
    send("Third question")

    result = page.locator("[data-contact-result]")
    expect(result).to_be_visible(timeout=10_000)
    text = result.inner_text()
    assert "reached the message limit" in text, text
    # It says how long, and what to do instead.
    assert re.search(r"about \d+ minutes?", text), text
    assert "Email us instead" in text


# --------------------------------------------------------------------------- #
#  Privacy and Terms                                                           #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ("/privacy", "/terms"))
def test_the_reader_is_told_where_they_are_and_how_much_is_left(
    page: Page, base_url: str, path: str
) -> None:
    """Twenty identical cards, and before this nothing said which one you were in."""

    _open(page, base_url, path)
    rail = page.locator(".hm-rail-link")
    expect(rail.first).to_be_visible()
    first = page.locator("[aria-current='true']").first.inner_text()

    page.mouse.wheel(0, 4200)
    page.wait_for_timeout(700)
    later = page.locator("[aria-current='true']").first.inner_text()
    assert first != later, "the rail did not follow the reader down the page"

    progress = page.evaluate(
        "() => document.querySelector('.hm-rail-progress > span').style.width"
    )
    assert progress and progress != "0%", f"the progress line stayed at {progress!r}"


@pytest.mark.parametrize("path", ("/privacy", "/terms"))
def test_the_plain_summary_opens_first_and_the_full_text_is_one_press_away(
    page: Page, base_url: str, path: str
) -> None:
    """A beginner reads the summaries; a lawyer presses one button and reads it all."""

    _open(page, base_url, path)
    # Every section shows its plain sentence from the start.
    summaries = page.locator(".hm-inshort")
    assert summaries.count() >= 15, summaries.count()
    expect(summaries.first).to_be_visible()

    # And the clauses are closed until asked for.
    closed = page.evaluate(
        "() => [...document.querySelectorAll('.hm-collapse')]"
        ".filter(n => n.dataset.open === 'true').length"
    )
    assert closed == 0, f"{closed} clauses were open before anybody asked"

    page.get_by_role("button", name=re.compile("Full text", re.I)).click()
    page.wait_for_timeout(600)
    opened = page.evaluate(
        "() => [...document.querySelectorAll('.hm-collapse')]"
        ".filter(n => n.dataset.open === 'true').length"
    )
    assert opened == summaries.count(), (
        f"Full text opened {opened} of {summaries.count()} clauses"
    )
    # And the clause really has height, rather than being open in name only.
    height = page.evaluate(
        "() => document.querySelector('.hm-collapse > div').getBoundingClientRect().height"
    )
    assert height > 20, f"an open clause measured {height}px"


@pytest.mark.parametrize(
    "path,needle",
    [
        # A word each document really contains. One word for both found nothing in
        # Terms, which made the search look broken when it was the test that was.
        ("/privacy", "delete"),
        ("/terms", "payment"),
    ],
)
def test_searching_narrows_the_document_and_says_how_much(
    page: Page, base_url: str, path: str, needle: str
) -> None:
    _open(page, base_url, path)
    total = page.locator(".hm-legal-section").count()
    page.locator("#legal-search").fill(needle)
    page.wait_for_timeout(400)
    shown = page.locator(".hm-legal-section").count()
    assert 0 < shown < total, f"searching {needle!r} showed {shown} of {total}"

    page.locator("#legal-search").fill("zzzzzz")
    page.wait_for_timeout(400)
    assert page.locator(".hm-legal-section").count() == 0
    assert "No section mentions" in page.locator("#root").inner_text()


@pytest.mark.parametrize("path", ("/privacy", "/terms"))
def test_a_deep_link_lands_on_the_open_clause(page: Page, base_url: str, path: str) -> None:
    """A link sent to somebody must show them the wording, not a closed summary of it."""

    page.goto(f"{base_url}{path}#security" if path == "/privacy" else f"{base_url}{path}#billing")
    page.wait_for_selector(".hm-legal-section", timeout=15_000)
    page.wait_for_timeout(900)
    target = "security" if path == "/privacy" else "billing"
    assert page.evaluate(
        f"() => document.querySelector('#{target}-full').dataset.open === 'true'"
    ), "the linked clause was closed"
    top = page.evaluate(f"() => document.getElementById('{target}').getBoundingClientRect().top")
    assert top < 400, f"the page did not move to the section (top was {top})"


@pytest.mark.parametrize("path", ("/privacy", "/terms"))
def test_the_document_shows_when_it_was_last_changed(
    page: Page, base_url: str, path: str
) -> None:
    """Its own text refers to this date, and the old page never displayed one."""

    _open(page, base_url, path)
    header = page.locator("#root").inner_text()
    assert "Last updated" in header
    assert re.search(r"Last updated:?\s*\d{1,2} \w+ \d{4}", header), header


@pytest.mark.parametrize("path", ("/privacy", "/terms"))
def test_no_page_asks_the_reader_to_sign_up_for_something_else(
    page: Page, base_url: str, path: str
) -> None:
    """The waitlist band is gone from the body of both documents."""

    _open(page, base_url, path)
    body = page.locator("main").inner_text()
    for beta in ("private beta", "Private beta", "invite-only"):
        assert beta not in body, f"{path} still says {beta!r}"
    # And what replaced it points at the sibling documents.
    assert "The rest of the paperwork" in body


# ---------------------------------------------------------------------------
# The header on a phone.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ALL_PUBLIC_PAGES)
def test_the_phone_menu_opens_closes_and_says_which_it_does(
    page: Page, base_url: str, path: str
) -> None:
    """The control that opens the menu is a mark, so its name has to be spoken.

    It used to render the word `Menu` in a pill, which is not a shape anybody
    recognises as a menu. It is the standard two-rule mark now — and the moment the
    only thing carrying the message is a drawing, the accessible name stops being a
    nicety. So this checks the name changes with the state, the panel really opens,
    and a closed panel is out of the tab order rather than merely invisible.
    """

    page.set_viewport_size({"width": 390, "height": 850})
    _open(page, base_url, path)

    toggle = page.locator(".hm-menu-toggle")
    panel = page.locator("#site-mobile-menu")
    expect(toggle).to_be_visible()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(toggle).to_have_attribute("aria-label", "Open menu")

    # Closed means unreachable, not just unseen. `visibility: hidden` is what takes the
    # links out of the tab order; opacity alone would leave every one of them focusable
    # behind a menu that looks shut.
    assert page.evaluate(
        "() => getComputedStyle(document.querySelector('#site-mobile-menu')).visibility"
    ) == "hidden"

    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(toggle).to_have_attribute("aria-label", "Close menu")
    expect(panel).to_be_visible()
    # Every link in the panel is a real target a finger can hit.
    for box in page.evaluate(
        "() => [...document.querySelectorAll('#site-mobile-menu a')]"
        ".map(el => el.getBoundingClientRect().height)"
    ):
        assert box >= 44, f"a menu link is only {box}px tall"

    # Escape closes it and hands the keyboard back to the control that opened it.
    page.keyboard.press("Escape")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    assert page.evaluate(
        "() => document.activeElement === document.querySelector('.hm-menu-toggle')"
    )


@pytest.mark.parametrize("path", ALL_PUBLIC_PAGES)
def test_the_footer_is_the_same_on_every_page(page: Page, base_url: str, path: str) -> None:
    """One footer, three groups, and the three channels — everywhere except the dashboard.

    The menu comes from `core/site_content.py` and is rendered twice, once in Jinja and
    once in React. This measures the rendered result rather than either source, because
    "both read the same list" is only worth anything if both really drew it.
    """

    _open(page, base_url, path)
    footer = page.locator("footer.hm-footer")
    expect(footer).to_have_count(1)

    groups = page.evaluate(
        "() => [...document.querySelectorAll('footer.hm-footer .hm-footer-menus h2')]"
        ".map(el => el.textContent.trim())"
    )
    assert groups == ["Product", "Legal", "Contact"], groups

    # Legal carries the three documents the user asked to be grouped there.
    legal = page.evaluate(
        """() => {
            const nav = [...document.querySelectorAll(
                'footer.hm-footer .hm-footer-menus nav'
            )].find((n) => n.querySelector('h2').textContent.trim() === 'Legal');
            return [...nav.querySelectorAll('a')].map((a) => new URL(a.href).pathname);
        }"""
    )
    for required in ("/terms", "/privacy", "/cookies"):
        assert required in legal, (required, legal)

    # And the way back to the cookie choice — a real link, with a real address, that
    # really opens the panel.
    #
    # It used to check only that a `<button>` with the right attribute existed, which is
    # the weakest thing it could have checked: this control is drawn by two different
    # renderers and wired by a script that runs before one of them, so "the element is
    # there" says nothing about whether pressing it does anything. It presses it now, on
    # every page, and waits for the panel.
    settings = page.locator("footer.hm-footer a[data-cookie-settings]")
    expect(settings).to_have_count(1)
    assert page.evaluate(
        "() => new URL(document.querySelector("
        "'footer.hm-footer a[data-cookie-settings]').href).search"
    ) == "?settings=1", "the link has to reach the panel with no script running"

    settings.click()
    expect(page.locator("[data-cookie-modal].is-visible")).to_have_count(1, timeout=5_000)
    page.keyboard.press("Escape")

    # The three channels, each announced by name because the mark is the whole message.
    channels = page.evaluate(
        "() => [...document.querySelectorAll('footer.hm-footer .hm-social-link')]"
        ".map(el => el.getAttribute('aria-label'))"
    )
    assert channels == [
        "Hilal Markets on Instagram",
        "Hilal Markets on X",
        "Hilal Markets on Threads",
    ], channels

# ---------------------------------------------------------------------------
# The launch offer, on the page a visitor decides on.
# ---------------------------------------------------------------------------


def test_the_launch_price_and_its_timer_are_really_on_the_pricing_page(
    page: Page, base_url: str
) -> None:
    """A countdown can be rendered and never move. Only a browser settles that.

    The price, the crossed-out price and the deadline all come from `core/plans.py`, so
    this reads them from there rather than typing numbers that go stale the next time
    the offer changes.
    """

    from ai_market_monitor.core.plans import (
        effective_monthly_price,
        original_monthly_price,
        promotion_is_active,
    )

    page.goto(f"{base_url}/pricing", wait_until="domcontentloaded")
    assert_no_raw_traceback(page)

    was = original_monthly_price("trader")
    if not promotion_is_active():
        # No offer running: no old price, no timer. The card is a plain price.
        assert was is None
        expect(page.locator(".offer-countdown")).to_have_count(0)
        expect(page.locator(".price-original")).to_have_count(0)
        return

    assert was is not None
    struck = page.locator(".price-original").first
    expect(struck).to_have_text(f"${int(was)}")
    assert f"${int(effective_monthly_price('trader'))}" in page.locator(
        ".price-card.is-featured .price"
    ).inner_text()

    # The timer is built by the script, shown only once it holds a real count, and steps
    # once a second. A stopped clock beside a price is worse than no clock.
    countdown = page.locator(".offer-countdown[data-offer-live]").first
    expect(countdown).to_be_visible(timeout=5_000)
    expect(countdown).to_contain_text("Launch price ends in")
    seconds = countdown.locator(".offer-countdown-part").last
    first_reading = seconds.inner_text()
    page.wait_for_timeout(1600)
    assert seconds.inner_text() != first_reading, "the countdown is not counting"


def test_the_landing_pricing_card_carries_the_live_countdown(
    page: Page, base_url: str
) -> None:
    """The same timer, on the card, on the page most visitors actually meet.

    The landing page draws its own countdown in React while `/pricing` is drawn by the
    server and stepped by a script. Two implementations is two chances for one of them
    to sit still, so each is measured on its own page.
    """

    from ai_market_monitor.core.plans import original_monthly_price, promotion_is_active

    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.locator("#pricing").scroll_into_view_if_needed()

    if not promotion_is_active():
        assert original_monthly_price("trader") is None
        expect(page.locator("#pricing .offer-countdown")).to_have_count(0)
        return

    # Inside the card, not somewhere else on the page.
    countdown = page.locator("#pricing .pricing-card .offer-countdown").first
    expect(countdown).to_be_visible(timeout=5_000)
    expect(countdown).to_contain_text("Launch price ends in")
    # Only the card whose price is discounted carries one.
    assert page.locator("#pricing .offer-countdown").count() == 1
    expect(page.locator("#pricing .pricing-card .plan-price-original").first).to_be_visible()

    seconds = countdown.locator(".offer-countdown-part").last
    first_reading = seconds.inner_text()
    page.wait_for_timeout(1600)
    assert seconds.inner_text() != first_reading, "the landing countdown is not counting"
