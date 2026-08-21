"""The three rebuilt public pages are usable by everybody, measured rather than assumed.

`/contact`, `/privacy` and `/terms` were rebuilt together. Every rule below is one the
old pages broke, and every one is checked as a **rule across the whole family** rather
than as the single instance that was reported.

The largest group is contrast, and it is *computed* here rather than eyeballed. That
matters because the failures were invisible by inspection: the text fields had a border
of `#e1e5ea` on a `#f8fafb` fill, which measures 1.21:1 — an edge that is there in the
markup and not there on the screen. Nobody spots that by looking; a calculator spots it
every time.

The other groups — dialogs, motion, icons, labels, targets — each hold a rule that a
new component could otherwise ship without.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.support.contrast import contrast

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "Hilal-Markets-Website" / "src"
STYLES = FRONTEND / "index.css"
CONTACT = FRONTEND / "pages" / "ContactPage.tsx"
LEGAL = FRONTEND / "pages" / "LegalPage.tsx"
DOCUMENTS = FRONTEND / "legal" / "documents.tsx"
DIALOG = FRONTEND / "components" / "Dialog.tsx"
ICON = FRONTEND / "components" / "Icon.tsx"
MOTION = FRONTEND / "motion.ts"
INTERACTIONS = FRONTEND / "components" / "interactions.ts"
ICON_SET = ROOT / "src" / "ai_market_monitor" / "static" / "hilalmarkets-icons.js"

PAGES = {"ContactPage.tsx": CONTACT, "LegalPage.tsx": LEGAL}


# --------------------------------------------------------------------------- #
#  Contrast, computed                                                          #
# --------------------------------------------------------------------------- #
#  The sum itself lives in `tests/support/contrast.py`. It used to live here as well,
#  with a slightly different curve from the other two copies in this suite.


def _token(name: str) -> str:
    """One colour from the site's own theme, read from the stylesheet.

    Read rather than repeated. A test holding its own copy of the palette passes while
    the palette says something else, which is the exact failure it exists to prevent.
    """

    text = STYLES.read_text(encoding="utf-8")
    match = re.search(rf"--color-{name}:\s*(#[0-9a-fA-F]{{6}})", text)
    assert match is not None, f"--color-{name} is not declared in index.css"
    return match.group(1).lower()


#: The surfaces these three pages actually put text on.
SURFACES = {
    "white card": "#ffffff",
    "page ground": "#f5f8fb",
    "apple panel": "#cbfa4d",
}

#: Every token used for reading text, and the smallest size it is used at. Body text
#: needs 4.5:1; nothing here is large enough to qualify for the 3:1 allowance.
TEXT_TOKENS = ("ink", "ink-soft", "apple-deep")

#: Tokens that draw the edge of something a person operates. WCAG 1.4.11 asks 3:1.
CONTROL_TOKENS = ("control",)


@pytest.mark.parametrize("token", TEXT_TOKENS)
@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_every_text_colour_is_readable_on_every_surface_it_is_used_on(
    token: str, surface: str
) -> None:
    """4.5:1 or it is not text, on all three surfaces rather than only on white.

    `ink-soft` measured 3.98:1 on white and 3.73:1 on the page ground. It was the
    colour of the helper text under the contact form's fields — the sentence explaining
    what would happen to the message.
    """

    measured = contrast(_token(token), SURFACES[surface])
    assert measured >= 4.5, (
        f"--color-{token} measures {measured:.2f}:1 on the {surface}, and body text "
        "needs 4.5:1. Darken the token rather than the one place it was noticed: it is "
        "used everywhere."
    )


@pytest.mark.parametrize("token", CONTROL_TOKENS)
@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_every_control_boundary_is_visible_on_every_surface(
    token: str, surface: str
) -> None:
    """3:1 for the edge of anything a person operates (WCAG 1.4.11).

    The form fields used the decorative hairline, which measures 1.21:1 against their
    own fill. The box was there and the edge of it was not.
    """

    measured = contrast(_token(token), SURFACES[surface])
    assert measured >= 3.0, (
        f"--color-{token} measures {measured:.2f}:1 on the {surface}. A control whose "
        "boundary cannot be seen is a control somebody has to guess at."
    )


def test_the_decorative_hairline_is_never_used_as_a_control_boundary() -> None:
    """It is 1.27:1 on white. It may edge a card; it may not edge an input.

    This is the rule, not the instance: any future field written with the hairline
    fails here, whichever component it is in.
    """

    text = STYLES.read_text(encoding="utf-8")
    assert contrast(_token("hairline"), "#ffffff") < 3.0, (
        "The hairline now passes 3:1, so this test is guarding nothing. Either it "
        "became the control colour, in which case merge the two, or it drifted."
    )
    for rule in (".hm-input", ".hm-textarea", ".hm-segment"):
        block = re.search(rf"{re.escape(rule)}[^{{]*\{{(?P<body>[^}}]*)\}}", text)
        assert block is not None, rule
        border = re.search(r"border:[^;]*", block.group("body"))
        if border is None:
            continue
        assert "--color-hairline" not in border.group(0), (
            f"{rule} draws its border with the decorative hairline. Use "
            "`var(--color-control)`, which is the one that can be seen."
        )


def test_the_bright_accent_never_carries_text_or_an_edge_alone() -> None:
    """Apple green measures 1.21:1 on white. `brand guide.md` section 9 already says so.

    It may be a background with near-black on it — which measures 11.21:1 — and it may
    be a halo behind a near-black ring. It may never be the ink.
    """

    apple = _token("apple")
    assert contrast(apple, "#ffffff") < 3.0
    assert contrast(_token("ink"), apple) >= 4.5

    for name, path in PAGES.items():
        source = path.read_text(encoding="utf-8")
        # `text-apple` would paint text in the bright accent. `text-apple-deep` is the
        # accessible one and is spelled differently, so the check must not match it.
        assert not re.search(r"\btext-apple\b(?!-)", source), (
            f"{name} paints text in the bright accent, which measures 1.21:1 on white."
        )


def test_the_rare_blue_is_never_used_for_small_text() -> None:
    """3.61:1 on white: fine for a line or an icon, not for a sentence."""

    blue = _token("accent-blue")
    assert contrast(blue, "#ffffff") < 4.5
    for name, path in PAGES.items():
        assert "text-accent-blue" not in path.read_text(encoding="utf-8"), name


# --------------------------------------------------------------------------- #
#  Dialogs                                                                     #
# --------------------------------------------------------------------------- #
#: Every rule a window that opens over the page has to obey, and the thing in the
#: source that proves it. Each one strands somebody when it is missing.
DIALOG_RULES = (
    ("it is announced as a window", 'role="dialog"'),
    ("the page behind is inert to assistive software", 'aria-modal="true"'),
    ("it has a name", "aria-labelledby={titleId}"),
    ("focus moves into it", "panel?.focus()"),
    ("Escape closes it", "event.key === 'Escape'"),
    ("Tab cannot leave it", "event.key !== 'Tab'"),
    ("Shift+Tab wraps backwards", "event.shiftKey"),
    ("focus returns to whatever opened it", "openerRef.current?.focus?.()"),
    ("the page behind cannot scroll", "document.body.style.overflow = 'hidden'"),
    ("the page behind is hidden from screen readers", "aria-hidden"),
    ("a click on the ground closes it", "event.target === event.currentTarget"),
)


@pytest.mark.parametrize("rule,evidence", DIALOG_RULES)
def test_the_dialog_obeys_every_rule_a_modal_window_has(rule: str, evidence: str) -> None:
    source = DIALOG.read_text(encoding="utf-8")
    assert evidence in source, f"The dialog does not do this: {rule}."


def test_there_is_one_dialog_and_every_page_uses_it() -> None:
    """A second modal is a second set of these eleven rules to get right."""

    for name, path in PAGES.items():
        source = path.read_text(encoding="utf-8")
        if "role=\"dialog\"" in source or "aria-modal" in source:
            pytest.fail(
                f"{name} builds its own modal. Use `components/Dialog.tsx`, which "
                "already handles focus, Escape and the page behind."
            )


# --------------------------------------------------------------------------- #
#  Motion                                                                      #
# --------------------------------------------------------------------------- #
def test_animation_comes_from_the_library_the_product_already_vendors() -> None:
    """One animation library for the whole product, not one per half of the site."""

    config = (ROOT / "Hilal-Markets-Website" / "vite.config.ts").read_text(encoding="utf-8")
    assert "static/vendor/motion.min.js" in config, (
        "The site should import the vendored Motion bundle the dashboard already uses. "
        "A second copy is a second version to keep in step."
    )
    assert "from 'motion'" in MOTION.read_text(encoding="utf-8")


def test_only_one_module_talks_to_the_animation_library() -> None:
    """Every rule about motion is enforced in one place or in none.

    Reduced motion, the easing spelling and committing the end state are three rules
    that a caller reaching for the library directly would each have to remember.
    """

    for path in sorted(FRONTEND.rglob("*.ts*")):
        if path in {MOTION, FRONTEND / "types" / "motion.d.ts"}:
            continue
        source = path.read_text(encoding="utf-8")
        assert "from 'motion'" not in source, (
            f"{path.name} imports the animation library directly. Go through "
            "`motion.ts`, which is where reduced motion and the easing option are "
            "handled once."
        )


def test_the_easing_option_is_spelled_the_way_this_library_reads_it() -> None:
    """Motion 11 accepts `ease` and silently ignores `easing`.

    Silently is the problem. Written the old way the animation still runs, still
    reports success, and runs on the wrong curve — so nothing fails and nobody looks.
    """

    source = MOTION.read_text(encoding="utf-8")
    assert "ease: EASE" in source
    assert not re.search(r"\beasing\s*:", source), (
        "`easing` is the older name and this library ignores it. The option is `ease`."
    )


def test_a_number_that_counts_up_is_animated_between_two_numbers() -> None:
    """`animate(fn, ...)` does nothing in this library. It needs `animate(from, to)`."""

    source = MOTION.read_text(encoding="utf-8")
    assert "animate(0, to," in source
    assert not re.search(r"animate\(\s*\(", source)


@pytest.mark.parametrize(
    "helper",
    ["move", "moveEach", "countTo", "whenSeen"],
)
def test_every_motion_helper_honours_a_request_for_less_movement(helper: str) -> None:
    """Not one blanket media query — each helper, because each one can run early.

    A CSS media query cannot stop an animation a script has already started. Every
    entry point checks the preference itself and puts the element straight into its end
    state instead.
    """

    source = MOTION.read_text(encoding="utf-8")
    body = source.split(f"export function {helper}(", 1)
    assert len(body) == 2, f"{helper} is not exported from motion.ts"
    # Up to the next exported function.
    block = re.split(r"\nexport (?:function|const) ", body[1])[0]
    assert "prefersReducedMotion()" in block, (
        f"`{helper}` starts an animation without asking whether the person wants one."
    )


def test_the_stylesheet_also_stops_the_movement_it_owns() -> None:
    """The helpers cover script; this covers what CSS starts on its own."""

    text = STYLES.read_text(encoding="utf-8")
    reduced = text.split("@media (prefers-reduced-motion: reduce)", 1)
    assert len(reduced) == 2
    block = reduced[1]
    # The 3D tilt has no reduced form: turning an element in space is exactly what this
    # setting asks not to happen.
    assert ".hm-tilt" in block
    assert ".hm-btn:hover" in block
    assert ".hm-collapse" in block


def test_the_pointer_tilt_is_never_given_to_a_finger() -> None:
    """A touch has no hover. A card that tips on tap only delays the tap."""

    source = INTERACTIONS.read_text(encoding="utf-8")
    assert "event.pointerType !== 'mouse'" in source
    assert "prefersReducedMotion()" in source


# --------------------------------------------------------------------------- #
#  Icons                                                                       #
# --------------------------------------------------------------------------- #
def test_icons_come_from_the_products_one_set() -> None:
    """No second icon set. Two sets stop matching, and nobody notices which one drifted."""

    assert "window.iconBody" in ICON.read_text(encoding="utf-8")
    assert "window.iconBody = function" in ICON_SET.read_text(encoding="utf-8")


@pytest.mark.parametrize("page", sorted(PAGES))
def test_no_page_draws_its_own_icon(page: str) -> None:
    """An inline `<svg>` on a page is an icon the shared set does not know about."""

    source = PAGES[page].read_text(encoding="utf-8")
    inline = re.findall(r"<svg\b", source)
    assert not inline, (
        f"{page} draws {len(inline)} icon(s) of its own. Add the name to "
        "`static/hilalmarkets-icons.js` and use `<Icon name=... />`, so the dashboard "
        "and the public site keep the same marks."
    )


def test_a_decorative_icon_is_hidden_and_a_meaningful_one_is_named() -> None:
    """Repeating the label beside it would make a screen reader say everything twice."""

    source = ICON.read_text(encoding="utf-8")
    assert "aria-hidden={title ? undefined : true}" in source
    assert "role={title ? 'img' : undefined}" in source


def test_an_unknown_icon_name_still_draws_something_and_says_so() -> None:
    """A typo must be visible, and findable.

    Visible, because an empty box is a typo nobody notices. Findable, because the
    fallback cannot be told apart by looking at it — `info`, `radar` and `methodology`
    are also a circle of radius nine — so the component marks the element instead of
    leaving a test to guess from the shape.
    """

    icon = ICON.read_text(encoding="utf-8")
    assert "const FALLBACK" in icon
    assert "data-icon-missing" in icon
    assert "|| ICONS.info" in ICON_SET.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
#  Forms, targets and announcements                                            #
# --------------------------------------------------------------------------- #
def test_every_field_on_the_contact_form_has_a_label_bound_to_it() -> None:
    """A placeholder is not a label: it disappears the moment somebody types."""

    source = CONTACT.read_text(encoding="utf-8")
    inputs = re.findall(r'id=\{`\$\{fieldId\}-([a-z]+)`\}', source)
    for field in ("title", "email", "description", "search"):
        assert field in inputs, field
        assert f'htmlFor={{`${{fieldId}}-{field}`}}' in source, field


@pytest.mark.parametrize("field", ["title", "email", "description"])
def test_every_field_reports_its_own_problem_where_the_problem_is(field: str) -> None:
    """Not one summary at the top: the message belongs beside the field it is about."""

    source = CONTACT.read_text(encoding="utf-8")
    assert f"errors.{field}" in source
    assert f"{field}-error" in source
    assert f"aria-invalid={{errors.{field} ? true : undefined}}" in source


@pytest.mark.parametrize(
    "rule,evidence",
    [
        ("a result is announced", 'role="status"'),
        ("a failure is announced", 'role="alert"'),
        ("a search result count is announced", 'aria-live="polite"'),
        ("the secret warning is announced as it appears", 'aria-live="polite"'),
    ],
)
def test_the_contact_page_says_out_loud_what_it_shows(rule: str, evidence: str) -> None:
    assert evidence in CONTACT.read_text(encoding="utf-8"), rule


def test_every_control_is_large_enough_to_press() -> None:
    """44 CSS pixels. Smaller than that is a control somebody with shaky hands misses.

    The selector has to be matched where a *rule starts*, not wherever the name appears.
    Written as a bare search, the pattern for ``.hm-btn`` also matched
    ``.hm-menu-actions .hm-btn`` — a descendant override that sets width and alignment
    and has no business declaring a height. Whichever of the two came first in the file
    decided whether this test passed, so adding a scoped override anywhere above the
    base rule failed it while the real button was still 48px tall.

    ``(?:^|\\}|,)\\s*`` anchors each name to the beginning of a selector list, which is
    where the base declaration lives.
    """

    text = STYLES.read_text(encoding="utf-8")
    for rule in (".hm-btn", ".hm-dialog-close", ".hm-rail-link", ".hm-disclose"):
        block = re.search(
            rf"(?:^|\}}|,)\s*{re.escape(rule)}\s*\{{(?P<body>[^}}]*)\}}",
            text,
            re.MULTILINE,
        )
        assert block is not None, f"{rule} has no base rule of its own."
        size = re.search(r"(?:min-height|height):\s*(\d+)px", block.group("body"))
        assert size is not None, f"{rule} sets no height, so nothing keeps it pressable."
        assert int(size.group(1)) >= 44, f"{rule} is {size.group(1)}px; 44 is the minimum."


def test_a_status_is_never_carried_by_colour_alone() -> None:
    """Brand rule 10, and WCAG 1.4.1. Colour plus a word, or a mark, every time."""

    contact = CONTACT.read_text(encoding="utf-8")
    legal = LEGAL.read_text(encoding="utf-8")

    # The chosen subject is green *and* ticked.
    assert "selected && <Icon name=\"check\"" in contact
    # The character counter changes colour *and* changes its words.
    assert "characters left" in contact
    assert "No characters left" in contact
    # The section being read is coloured, weighted, barred and marked `aria-current`.
    assert "aria-current={active === section.id ? 'true' : undefined}" in legal
    assert "hm-rail-link[aria-current='true']::before" in STYLES.read_text(encoding="utf-8")


def test_a_long_document_can_be_read_kept_and_navigated() -> None:
    """The three things a legal page has to allow and this one used not to."""

    legal = LEGAL.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    # Read: which section am I in, and how much is left.
    assert "useScrollSpy" in legal
    assert "useReadingProgress" in legal
    # Kept: it prints, and printing opens every clause whatever was open on screen.
    assert "window.print()" in legal
    assert "@media print" in styles
    assert "grid-template-rows: 1fr !important" in styles
    # Navigated: a deep link lands on an open clause rather than a closed summary.
    assert "window.location.hash.slice(1)" in legal
    assert "new Set(current).add(target)" in legal


def test_the_title_box_never_offers_more_room_than_the_server_accepts() -> None:
    """The subject is sent as a prefix, so the two together have to fit.

    Offering the full 180 characters let somebody fill the box and then have the
    message refused for a reason nothing on the page had mentioned. The limit is worked
    out from the longest subject, so it is right whichever one is chosen.
    """

    contact = CONTACT.read_text(encoding="utf-8")
    schema = (
        ROOT / "src" / "ai_market_monitor" / "schemas" / "public_forms.py"
    ).read_text(encoding="utf-8")

    # The page's ceiling is the server's, named rather than guessed.
    server = re.search(r"title: str = Field\(min_length=3, max_length=(\d+)\)", schema)
    assert server is not None, "the contact schema no longer states a title length"
    assert f"const SERVER_TITLE_LIMIT = {server.group(1)}" in contact, (
        f"the page allows a different title length from the server's {server.group(1)}"
    )
    # And it subtracts the longest subject rather than a fixed guess.
    assert "Math.max(...TOPICS.map(" in contact
    assert "maxLength={TITLE_LIMIT}" in contact


def test_the_document_shows_the_date_its_own_text_refers_to() -> None:
    """The old text said the date changes when the policy is revised, and showed none.

    A document that points at information it does not display is telling the reader to
    look for something that is not there.
    """

    documents = DOCUMENTS.read_text(encoding="utf-8")
    legal = LEGAL.read_text(encoding="utf-8")
    assert re.search(r"const UPDATED = '\d{1,2} \w+ \d{4}'", documents)
    assert "updated: UPDATED" in documents
    assert "document_.updated" in legal
    assert "Last updated" in legal
