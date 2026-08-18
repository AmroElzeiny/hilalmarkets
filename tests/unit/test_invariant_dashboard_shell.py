"""The rules the dashboard shell — the side menu and the topbar — must keep.

The shell is on every signed-in page, so a mistake here is a mistake on nine pages at
once. Every check below is one of the rules written down in
`docs/dashboard-shell-redesign-rules.md` before the work started, and each one exists
because the thing it forbids had really happened.

The browser suite proves what only a browser can (contrast, the marker really moving,
the accessible name a screen reader really gets). These run in milliseconds on every
commit and catch the change that actually happens: somebody edits the shell during
ordinary work and quietly removes one of these properties.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"
SHELL_CSS = ROOT / "static" / "hm-shell.css"
SHELL_JS = ROOT / "static" / "hm-shell.js"
SIDEBAR = ROOT / "templates" / "hilal" / "partials" / "dashboard_sidebar.html"
TOPBAR = ROOT / "templates" / "hilal" / "partials" / "dashboard_topbar.html"
BASE = ROOT / "templates" / "hilal" / "base_dashboard.html"
HILAL_CSS = ROOT / "static" / "hm-hilal-chat.css"
HILAL_PARTIAL = (
    ROOT / "templates" / "hilal" / "dashboard_test" / "partials" / "hilal_chat.html"
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rules(path: Path) -> str:
    """The stylesheet with its comments removed, so prose can never satisfy a check."""

    return re.sub(r"/\*.*?\*/", "", _text(path), flags=re.DOTALL)


# ---------------------------------------------------------------------------
# A name is moved out of sight, never out of the accessibility tree.
# ---------------------------------------------------------------------------


def test_every_side_menu_link_keeps_its_name_when_the_menu_is_minimized():
    """`display: none` on a label leaves the link with no accessible name at all.

    That was the state of the minimized menu: nine links, every one of them read out as
    a bare "link", because the only text inside each was hidden the one way that also
    removes it from the accessibility tree. The label is moved off screen instead.
    """

    rules = _rules(SHELL_CSS)
    collapsed = rules.split(".sidebar-collapsed", 1)
    assert len(collapsed) > 1, "the minimized menu has no rules at all"

    hidden = re.search(
        r"\.sidebar-collapsed :is\((?P<names>[^)]*hm-nav-text[^)]*)\)\s*\{(?P<body>[^}]*)\}",
        rules,
    )
    assert hidden, "the minimized menu no longer hides the link labels in one place"
    body = hidden.group("body")
    assert "display: none" not in body, (
        "a hidden label must stay in the accessibility tree; use the off-screen form"
    )
    assert "clip-path: inset(50%)" in body
    assert "position: absolute" in body

    # And nothing anywhere else in the shell reaches for the forbidden form on a label.
    for label in ("hm-nav-text", "hm-nav-toggle-label", "hm-top-action-label"):
        for match in re.finditer(rf"\.{label}[^{{]*\{{([^}}]*)\}}", rules):
            assert "display: none" not in match.group(1), label


def test_the_flyout_name_is_never_the_only_place_a_name_exists():
    """A tooltip is a second copy for the eye. The first copy has to be the real one."""

    sidebar = _text(SIDEBAR)
    # Every flyout is hidden from anything that reads rather than looks.
    for match in re.finditer(r'<span class="hm-nav-tip"([^>]*)>', sidebar):
        assert 'aria-hidden="true"' in match.group(1)
    # And the name it repeats is really on the control.
    assert '<span class="hm-nav-text">{{ item.label }}</span>' in sidebar
    assert '<span class="hm-nav-tip" aria-hidden="true">{{ item.label }}</span>' in sidebar


def test_the_current_page_is_announced_not_only_painted():
    sidebar = _text(SIDEBAR)
    assert 'aria-current="page"' in sidebar
    rules = _rules(SHELL_CSS)
    # Colour is never the only signal: the row is filled near-black as well as marked.
    active = re.search(r"\.hm-nav-link\.is-active\s*\{([^}]*)\}", rules)
    assert active and "background: var(--hm-ink)" in active.group(1)


def test_there_is_a_way_out_of_the_product_in_both_menu_states():
    """The sign-out form was one of the things `display: none` used to remove.

    So a person who minimized the menu could not sign out until they opened it again.
    It lives in a popover now, which the account button opens in either state.
    """

    sidebar = _text(SIDEBAR)
    assert "[data-hm-nav-user]" not in sidebar
    assert "data-hm-nav-user" in sidebar
    assert 'action="/logout"' in sidebar
    assert 'aria-haspopup="menu"' in sidebar
    assert 'aria-expanded="false"' in sidebar
    js = _text(SHELL_JS)
    assert 'event.key === "Escape"' in js
    assert "wireAccount" in js


# ---------------------------------------------------------------------------
# Targets, focus and contrast.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "selector",
    [
        ".hm-nav-toggle",
        ".hm-nav-link",
        ".hm-top-action",
        ".hm-top-icon-btn",
        ".hm-nav-account-row",
    ],
)
def test_every_shell_control_is_large_enough_to_press(selector: str):
    """44px is the target size this whole product works to, and the browser suite
    measures every control on every page against it.

    The rule that gives a control its *shape* is the one to read, and it is found by
    requiring the class to end there — followed by a comma, whitespace or the brace.
    Written as a bare search, the pattern for `.hm-top-icon-btn` matched
    `.hm-top-icon-btn.hm-top-menu { display: none }`, a modifier that hides one button
    and has no business setting a height. Whichever rule came first in the file decided
    the result, so this reported a 44px control as having no size at all.
    """

    rules = _rules(SHELL_CSS)
    # `(?![\w.:[-])` stops the match at the end of the class name, so a compound
    # selector — `.hm-top-icon-btn.hm-top-menu`, `.hm-top-icon-btn:hover`,
    # `.hm-top-icon-btn[hidden]` — is not mistaken for the base rule.
    block = re.search(
        rf"{re.escape(selector)}(?![\w.:\[-])[^{{;}}]*\{{([^}}]*)\}}",
        rules,
    )
    assert block, f"{selector} has no rule at all"
    sizes = re.findall(r"(?:min-)?height:\s*(?:var\(--hm-nav-row\)|(\d+)px)", block.group(1))
    assert sizes, f"{selector} does not set its own height"
    for value in sizes:
        if value:
            assert int(value) >= 44, f"{selector} is {value}px tall"


def _winning_display(rules: str, classes: set[str], *, width: int) -> str | None:
    """What the browser would really draw for one control at one window width.

    Resolves the cascade the way a browser does for this one property: every rule whose
    selector is satisfied by ``classes``, ordered by specificity and then by where it
    appears in the file, last one wins. Media queries are honoured, so a control can be
    asked about on a wide screen and a narrow one separately.

    It exists because reading the file top to bottom does not answer the question. A rule
    that names one control can be undone by a later rule that only describes its shape,
    and nothing about either rule looks wrong on its own.
    """

    # Every rule in this sheet is written under the two classes the signed-in `<body>`
    # always carries, so they are part of the context rather than something a caller has
    # to remember. `sidebar-collapsed` is deliberately not: that is a choice, not a fact.
    classes = classes | {"dashboard-body", "hilal-dashboard"}
    winner: tuple[tuple[int, int], int, str] | None = None
    for order, (query, selector, body) in enumerate(_declarations(rules)):
        if not _media_applies(query, width):
            continue
        display = re.findall(r"display:\s*([^;]+)", body)
        if not display:
            continue
        for one in selector.split(","):
            one = one.strip()
            if ":" in one or "[" in one or ">" in one:
                # Only the plain forms. A state or an attribute is a different question
                # from "is this control on screen at this width".
                continue
            names = set(re.findall(r"\.([a-zA-Z0-9_-]+)", one))
            if not names or not names <= classes:
                continue
            elements = len(re.findall(r"(?:^|\s)([a-z]+)(?=[.\s]|$)", one))
            weight = (len(names), elements)
            if winner is None or (weight, order) >= (winner[0], winner[1]):
                winner = (weight, order, display[-1].strip())
    return winner[2] if winner else None


def _declarations(rules: str):
    """Every ``(media query, selector, body)`` in the sheet, in the order they are read."""

    found: list[tuple[str, str, str]] = []
    media = ""
    depth = 0
    head = ""
    index = 0
    while index < len(rules):
        char = rules[index]
        if char == "{":
            name = head.strip()
            head = ""
            if name.startswith("@media"):
                media = name
                depth += 1
                index += 1
                continue
            close = rules.find("}", index)
            found.append((media, name, rules[index + 1 : close]))
            index = close + 1
            continue
        if char == "}":
            if depth:
                depth -= 1
                media = ""
            head = ""
            index += 1
            continue
        head += char
        index += 1
    return found


def _media_applies(query: str, width: int) -> bool:
    if not query:
        return True
    low = re.search(r"min-width:\s*(\d+)px", query)
    high = re.search(r"max-width:\s*(\d+)px", query)
    if low and width < int(low.group(1)):
        return False
    return not (high and width > int(high.group(1)))


def test_a_control_shown_in_one_menu_shape_is_hidden_in_the_other():
    """A rule that hides one control must beat the rule that shapes every control.

    `.hm-top-menu` opens the drawer and belongs to a small screen only. It said
    `display: none` in exactly as many words as the shared `.hm-top-icon-btn` shape rule
    said `display: grid`, and the shape rule comes later in the file — so it won, and the
    button that opens the menu sat in the topbar of every desktop page beside a menu that
    was already open.

    Both rules name the control *and* its shape class now, so neither can be undone by
    something that only says what the shape looks like. This resolves the cascade rather
    than reading for a string, so it fails whether the next collision comes from a
    reorder, a new rule, or a dropped class.
    """

    rules = _rules(SHELL_CSS)
    desktop, narrow = 1440, 700

    menu = {"hm-top-icon-btn", "hm-top-menu", "topbar-menu"}
    assert _winning_display(rules, menu, width=desktop) == "none"
    assert _winning_display(rules, menu, width=narrow) == "grid"

    close = {"hm-nav", "hm-nav-head", "hm-nav-icon-btn", "sidebar-close"}
    assert _winning_display(rules, close, width=desktop) == "none"
    assert _winning_display(rules, close, width=narrow) == "grid"

    # And the bell, which is on screen in both shapes, is not hidden by either.
    bell = {"hm-top-icon-btn", "notification-center-trigger"}
    assert _winning_display(rules, bell, width=desktop) == "grid"
    assert _winning_display(rules, bell, width=narrow) == "grid"


def test_the_shell_writes_no_focus_ring_of_its_own():
    """One focus indicator for the product, declared in `hilalmarkets-brand.css`.

    A component that writes its own is how the product once had three of them and no way
    to fix them together.
    """

    rules = _rules(SHELL_CSS)
    assert "outline:" not in rules
    assert "--hm-focus" not in rules


# ---------------------------------------------------------------------------
# Brand: no new colour, no new typeface, no new scale.
# ---------------------------------------------------------------------------


def test_the_shell_uses_only_the_two_brand_typefaces():
    """`brand guide.md` §11: Geometria for display, Onest for interface. Nothing else."""

    families = re.findall(r"font-family:\s*([^;]+);", _rules(SHELL_CSS))
    assert families, "the shell sets no font at all"
    for value in families:
        assert value.strip() in {
            "var(--hm-font-ui)",
            "var(--hm-font-display)",
            "inherit",
        }, value


def test_the_shell_invents_no_radius_shadow_or_colour_of_its_own():
    """Colours are held by the palette test. Shadows and the accent are held here.

    Every shadow is one of the four brand shadows, and the one bright-green surface in
    the whole shell is the primary topbar action — `brand guide.md` §9 asks for apple
    green as a single focal element rather than a decoration applied everywhere.
    """

    rules = _rules(SHELL_CSS)
    for shadow in re.findall(r"box-shadow:\s*([^;]+);", rules):
        value = shadow.strip()
        assert value.startswith("var(--hm-shadow") or value.startswith("0 "), value

    # Bright apple green appears exactly twice, and both are named here so a third one
    # cannot arrive quietly: the seven-pixel mark on the page you are on, and the one
    # button in the bar this page most wants pressed.
    painted = {
        selector.strip()
        for selector, body in re.findall(r"([^{}]+)\{([^}]*)\}", rules)
        if re.search(r"background:\s*var\(--hm-apple\)\s*;", body)
    }
    assert painted == {
        "body.hilal-dashboard .hm-nav-link.is-active .hm-nav-dot",
        "body.hilal-dashboard .hm-top-action.is-primary",
    }, painted


# ---------------------------------------------------------------------------
# Motion.
# ---------------------------------------------------------------------------


def test_the_shell_animates_through_the_shared_motion_layer():
    """No component owns a duration, an easing, or the reduced-motion decision."""

    js = _text(SHELL_JS)
    assert 'from "./hm-motion.js"' in js
    assert "matchMedia(\"(prefers-reduced-motion" not in js, (
        "the reduced-motion decision belongs to hm-motion.js, once, for everything"
    )
    # Durations are named, never written as numbers at a call site.
    assert not re.search(r"duration:\s*[\d.]+", js), "a raw duration was written here"


def test_the_shell_has_a_reduced_motion_form():
    rules = _rules(SHELL_CSS)
    assert "prefers-reduced-motion" in rules
    reduced = rules.split("prefers-reduced-motion", 1)[1]
    assert "transition: none" in reduced
    assert "transform: none" in reduced


def test_no_animation_in_this_product_is_three_dimensional():
    """A turn in space never explained a relationship here, and it cost a visible bug.

    The minimized menu's flyout name asked for `rotateY` with `transformPerspective: 700`
    — "draw the turn as if the viewer were 700px away". The vendored bundle is Motion 11,
    and **everything in a keyframes object is a value to travel to**, including that one.
    The element had no perspective to start from, so it travelled up from nothing, and for
    the first frames of every flyout the perspective was a handful of pixels. A perspective
    that small magnifies whatever leans towards the viewer enormously: the name shot out
    sideways across the page and then shrank back into place as the number climbed.
    Measured in Chromium at `perspective(134px)` a fifth of the way through a 5s run.

    The turn is gone rather than corrected, and none of the three keys may come back.
    `brand guide.md` section 15 asks that movement explain a relationship; a fade and a
    short slide already say "this name belongs to that row".
    """

    static = ROOT / "static"
    forbidden = ("transformPerspective", "rotateY", "rotateX")
    for path in sorted(static.glob("*.js")):
        source = _text(path)
        # Comments explain why these are forbidden, so prose must not be the thing that
        # fails. Only a real key in an object literal counts.
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.DOTALL)
        for key in forbidden:
            assert not re.search(rf"\b{key}\s*:", code), f"{path.name} animates in 3D"

    # And the flyout still moves, so the fix was not "delete the animation".
    js = _text(SHELL_JS)
    assert "opacity: [0, 1]" in js
    assert re.search(r"x:\s*\[", js), "the flyout no longer slides out of the menu"


def test_the_flyout_name_can_never_leave_the_window():
    """It is `position: fixed`, so a number written into `left` is a number on screen.

    Nothing clips it and nothing scrolls it back, which is why the placement is clamped
    rather than trusted. The menu is 84px wide on one machine and a drawer on another.
    """

    js = _text(SHELL_JS)
    assert "window.innerWidth" in js
    assert "window.innerHeight" in js
    assert js.count("Math.min(") >= 2, "the placement is not clamped on both axes"


def test_nothing_in_the_menu_or_the_bar_loops():
    """`brand guide.md` §15 rules out movement that repeats without meaning.

    The one repeating thing in the whole shell is the assistant's tag, and it is in a
    different file for exactly that reason — see the tests below it.
    """

    assert "infinite" not in _rules(SHELL_CSS)


# ---------------------------------------------------------------------------
# The topbar draws every page's actions, and pages do not draw their own.
# ---------------------------------------------------------------------------


def test_the_topbar_draws_the_actions_the_page_declared():
    topbar = _text(TOPBAR)
    assert "topbar_actions" in topbar
    assert "action.href" in topbar
    assert "action.icon" in topbar
    assert "action.label" in topbar
    # And it says where you are, from the navigation data rather than a second list.
    assert "page_identity" in topbar
    from ai_market_monitor.core.site_content import dashboard_page_identity

    assert dashboard_page_identity("watchlists") == {
        "group": "Your monitors",
        "label": "Monitors",
        "icon": "radar",
    }
    assert dashboard_page_identity("nothing-like-this") is None


def test_no_redesigned_page_draws_its_own_top_right_button():
    """One shape, one place. Two pages used to draw their own, in their own heading.

    The Monitors page only drew its create button when there was already a monitor, so
    the emptiest screen in the product was the one with no way to make anything.
    """

    pages = ROOT / "templates" / "hilal" / "dashboard_test"
    for path in sorted(pages.glob("*.html")):
        body = _text(path)
        assert "w-new" not in body, f"{path.name} still draws a heading action"


# ---------------------------------------------------------------------------
# One name, one page.
# ---------------------------------------------------------------------------


def _route_names(path: Path) -> dict[str, str]:
    """Every name this router registers, and the address behind it.

    A route is named after its function unless the decorator says otherwise, so both
    forms are read.
    """

    text = _text(path)
    found: dict[str, str] = {}
    for match in re.finditer(
        r"@router\.(?:get|post)\(\s*([^\n]*?)\s*(?:,|\))(?P<rest>.*?)\n(?:async )?def (\w+)",
        text,
        re.DOTALL,
    ):
        block = match.group(0)
        declared = re.search(r'name="([^"]+)"', block)
        found[declared.group(1) if declared else match.group(3)] = (
            match.group(1).strip().rstrip(",")
        )
    return found


def test_no_two_dashboard_routers_answer_to_the_same_name():
    """`url_for` resolves a name to whichever router registered it first.

    Five of these once meant two different pages each — the redesigned page and the older
    copy of it — and `dashboard.py` is registered first, so the side menu quietly opened
    the older one every time. Renaming the Monitors page would have changed a screen
    nobody could reach from the menu.

    Naming a route explicitly is the fix, and this is what keeps a sixth from appearing.
    """

    live = _route_names(ROOT / "api" / "routers" / "dashboard.py")
    redesigned = _route_names(ROOT / "api" / "routers" / "dashboard_test.py")
    front = _route_names(ROOT / "api" / "routers" / "main_dashboard.py")

    assert len(live) > 10 and len(redesigned) > 5, "the routers could not be read"
    shared = sorted((set(live) & set(redesigned)) | (set(front) & set(live)))
    assert not shared, (
        f"these names mean two different pages, and the first router registered wins: "
        f"{shared}"
    )


def test_the_side_menu_names_a_route_that_exists():
    """A menu entry naming a route nobody registered raises on every dashboard page."""

    from ai_market_monitor.core.site_content import DASHBOARD_NAVIGATION

    known = set()
    for router in ("dashboard.py", "dashboard_test.py", "main_dashboard.py"):
        known |= set(_route_names(ROOT / "api" / "routers" / router))

    missing = sorted(
        item.endpoint
        for group in DASHBOARD_NAVIGATION
        for item in group.items
        if item.endpoint not in known
    )
    assert not missing, f"the menu names routes that do not exist: {missing}"


# ---------------------------------------------------------------------------
# Icons chosen in Python.
# ---------------------------------------------------------------------------


def test_every_icon_a_router_hands_to_a_template_really_exists():
    """A name with no icon behind it draws an empty box and nothing reports it.

    The templates are already covered: `data-icon="..."` is a literal a test can read.
    These are not — the router writes the name into the context and the template prints
    whatever arrives, so a typo reaches a customer as a blank square. Every delivery
    channel, every tile and every menu entry picks its icon this way.
    """

    icons = set(
        re.findall(
            r"^\s*([a-z0-9_]+):",
            _text(ROOT / "static" / "hilalmarkets-icons.js"),
            re.MULTILINE,
        )
    )
    assert "dashboard" in icons, "the icon table itself could not be read"

    routers = (
        ROOT / "api" / "routers" / "dashboard_test.py",
        ROOT / "api" / "routers" / "main_dashboard.py",
    )
    asked: dict[str, str] = {}
    for path in routers:
        for name in re.findall(r'(?:"icon":|icon=)\s*"([a-z0-9_]+)"', _text(path)):
            asked[name] = path.name

    # The menu and the topbar actions declare theirs as data, so they are read as data
    # rather than guessed at from the shape of the source.
    from ai_market_monitor.core.site_content import DASHBOARD_NAVIGATION

    for group in DASHBOARD_NAVIGATION:
        for item in group.items:
            asked[item.icon or ""] = "site_content.py"

    assert len(asked) > 10, f"only {len(asked)} icon names were found; the scan is broken"
    missing = sorted(name for name in asked if name not in icons)
    assert not missing, f"these are handed to a template but do not exist: {missing}"


# ---------------------------------------------------------------------------
# The assistant.
# ---------------------------------------------------------------------------


def test_the_assistant_reads_as_software_rather_than_a_person():
    """A plain speech bubble beside a Support link reads as somebody waiting to reply.

    The mark is `spark`, and it is the same mark before and after signing in. There
    were briefly two: `hilal_ai` for the dashboard and `spark` for the landing page, so
    one helper wore two faces and neither knew about the other. `hilal_ai` was withdrawn
    and this now checks the surviving one — asserting the withdrawn name kept this test
    failing after the merge that removed it, which says nothing about the rule it is for.
    """

    partial = _text(HILAL_PARTIAL)
    landing = _text(ROOT / "templates" / "hilal" / "partials" / "public_chat.html")
    icons = _text(ROOT / "static" / "hilalmarkets-icons.js")
    assert 'data-icon="spark"' in partial
    # The same mark on the public site, which is the whole point of there being one.
    assert 'data-icon="spark"' in landing
    assert 'data-icon="hilal"' not in partial
    # One mark, so the retired one must not come back beside it.
    assert 'data-icon="hilal_ai"' not in partial
    assert "spark:" in icons
    # `brand guide.md` §13 rules out AI brains and robots by name, so the mark is a
    # speech bubble with a spark in it and never a machine. Icon names only — the note
    # in that file explaining the choice must not be the thing that fails.
    names = set(re.findall(r"^\s*([a-z0-9_]+):", icons, re.MULTILINE))
    assert not names & {"robot", "brain", "ai_brain"}, sorted(names)
    # Withdrawn from the table as well as from the pages. An icon named for the assistant
    # sitting there unused is an invitation for a second face to come back.
    assert "hilal_ai" not in names


def test_both_assistants_draw_their_mark_at_the_same_share_of_their_button():
    """One mark at one size, or it is two marks that only happen to look alike.

    48 per cent of the button on both: 27px on the landing page's 56px circle, 29px on the
    dashboard's 60px one. The landing launcher has no grey disc behind the star any more
    either — that was a second circle inside the first, drawn around the only thing in the
    button, and the dashboard's launcher never had one.
    """

    public = _rules(ROOT / "static" / "hilalmarkets-public-chat.css")
    dashboard = _rules(HILAL_CSS)

    landing_icon = re.search(r"\.public-chat-launcher \.icon\s*\{([^}]*)\}", public)
    assert landing_icon, "the landing launcher no longer sizes its own mark"
    assert "width:27px" in landing_icon.group(1).replace(" ", "")

    orb_icon = re.search(r"\.hilal-orb-face \.icon\s*\{([^}]*)\}", dashboard)
    assert orb_icon, "the dashboard launcher no longer sizes its own mark"
    assert "width: 29px" in orb_icon.group(1)

    # And nothing paints a surface behind the star.
    painted = [
        body
        for selector, body in re.findall(r"([^{}]+)\{([^}]*)\}", public)
        if ".public-chat-launcher-icon" in selector and "background" in body
    ]
    assert not painted, painted


def test_the_assistant_tag_is_one_line_in_a_box_that_is_narrower_than_it():
    rules = _rules(HILAL_CSS)
    tag = re.search(r"\.hilal-tag\s*\{([^}]*)\}", rules)
    assert tag, "the tag has no rule"
    assert "white-space: nowrap" in tag.group(1), "the line must never wrap"
    assert "overflow: hidden" in tag.group(1)
    assert re.search(r"width:\s*\d+px", tag.group(1)), "the box needs a width of its own"

    run = re.search(r"\.hilal-tag-run\s*\{([^}]*)\}", rules)
    assert run and "infinite" in run.group(1), "the line has to start over for ever"

    # Two identical copies and a shift of exactly half the track: that is what makes the
    # loop seamless rather than a visible jump back to the start.
    partial = _text(HILAL_PARTIAL)
    assert partial.count('<span class="hilal-tag-line">') == 2
    assert "translateX(-50%)" in rules


def test_the_moving_tag_can_be_stopped():
    """WCAG 2.2.2. Moving text that starts by itself must be stoppable."""

    rules = _rules(HILAL_CSS)
    assert "animation-play-state: paused" in rules
    hover = re.search(
        r"\.hm-hilal:hover \.hilal-tag-run,\s*[^{]*:focus-within \.hilal-tag-run\s*\{",
        rules,
    )
    assert hover, "the tag must stop for a pointer and for the keyboard"
    reduced = rules.split("prefers-reduced-motion", 1)[1]
    assert ".hilal-tag-run { animation: none; }" in reduced


def test_the_tag_says_only_what_the_assistant_really_does():
    """It claims to see the page. That claim has to be true where it is made.

    `hm-page-context.js` sends the page, the section on screen and the coin being looked
    at with every message, so it is. If that ever stops being sent, this fails rather
    than leaving a false promise in the corner of every screen.
    """

    partial = _text(HILAL_PARTIAL)
    assert "sees the page you are on" in partial
    context = _text(ROOT / "static" / "hm-page-context.js")
    for field in ("page:", "section:", "subject:"):
        assert field in context
    chat = _text(ROOT / "static" / "hm-hilal-chat.js")
    assert "snapshot" in chat, "the assistant no longer sends what is on screen"


# ---------------------------------------------------------------------------
# The shell is wired up at all.
# ---------------------------------------------------------------------------


def test_the_shell_is_loaded_after_the_sheet_it_replaces():
    base = _text(BASE)
    assert "hm-shell.css" in base
    assert "hm-shell.js" in base
    assert base.index("hilalmarkets-dashboard-v2.css") < base.index("hm-shell.css")
