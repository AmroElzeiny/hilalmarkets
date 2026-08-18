"""The focus indicator is visible on every surface, in every stylesheet.

A keyboard user can only see where they are if the focus ring has real contrast against
what is behind it. WCAG 2.2 asks for 3:1 (1.4.11 Non-text Contrast, and 2.4.11 Focus
Appearance). The product shipped with a ring that measured **1.11:1** — bright apple
green at 78% opacity — declared once globally and then copied, fainter each time, into
nine more stylesheets. Two of the copies removed the ring altogether by sharing a rule
with `:hover` and writing `outline: none`.

These tests assert the rule rather than the nine instances:

* no `:focus-visible` rule anywhere may remove the outline;
* no `:focus-visible` rule may paint the outline in a see-through colour;
* the bright accent may carry the outline only where a contrasting halo is drawn behind
  it, which is the only way it is readable on a light surface;
* the two files that declare the ring hold the same value.

A tenth stylesheet with the same mistake fails here, before it reaches a browser.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "src" / "ai_market_monitor" / "static"
#: The public site's own stylesheet, before it is built into the landing bundle.
#: It declares the ring a third time, because the React pages load neither of the two
#: files below — and for a while it declared a *different* one, so a keyboard user met
#: one indicator on the dashboard and another on the public pages.
REACT_STYLES = ROOT / "Hilal-Markets-Website" / "src" / "index.css"

#: One CSS rule: everything up to `{`, then the body.
_RULE = re.compile(r"(?P<selector>[^{}]*?)\{(?P<body>[^{}]*)\}", re.S)

#: `rgba(...)` / `hsla(...)` with an alpha below 1, or a colour mixed with transparency.
#: Both are how every faint ring in this product was written.
_SEE_THROUGH = re.compile(
    r"rgba?\([^)]*,\s*(0|0?\.\d+)\s*\)|hsla?\([^)]*,\s*(0|0?\.\d+)\s*\)|color-mix\([^)]*transparent[^)]*\)",
    re.I,
)

#: Colours that measure under 3:1 on white and can therefore never carry a ring alone.
#: `brand guide.md` section 9 already says bright apple green must not carry small marks
#: on white; these are that rule, written as values a test can check.
_TOO_PALE = (
    "#cbfa4d",
    "var(--hm-apple)",
    "var(--hm-chat-apple)",
    "var(--hm-apple-soft)",
    "var(--hm-apple-pale)",
    "#f1fadf",
    "#e8fbbf",
)

_OUTLINE = re.compile(r"(?:^|;)\s*outline\s*:\s*(?P<value>[^;]+)", re.I | re.M)


def _stylesheets() -> list[Path]:
    #: The vendored landing bundle is built from the React source and is not edited here.
    return [
        path
        for path in sorted(STATIC.rglob("*.css"))
        if "landing" not in path.parts and "vendor" not in path.parts
    ]


def _focus_rules() -> list[tuple[str, str, str]]:
    """Every rule that styles a focused element, as (file, selector, body)."""

    found: list[tuple[str, str, str]] = []
    for path in _stylesheets():
        text = path.read_text(encoding="utf-8")
        for match in _RULE.finditer(text):
            selector = match.group("selector")
            if ":focus-visible" not in selector and ":focus-within" not in selector:
                continue
            found.append((path.name, " ".join(selector.split()), match.group("body")))
    return found


def test_there_are_focus_rules_to_check() -> None:
    """The parser finds rules at all, so a silent zero can never be a pass."""

    assert len(_focus_rules()) >= 15


def _wrappers_that_draw_a_ring(stylesheet: str) -> list[str]:
    """Selectors in one file that draw a ring when something inside them is focused.

    A control inside such a wrapper is allowed to drop its own outline: the indicator
    has moved outwards, it has not disappeared, and two rings around one field is worse
    than one. This is the only reason `outline: none` on a focus rule is ever correct.
    """

    drawn = []
    for name, selector, body in _focus_rules():
        if name != stylesheet or ":focus-within" not in selector:
            continue
        outline = _OUTLINE.search(body)
        if outline and outline.group("value").strip().lower() not in {"none", "0", "0px"}:
            drawn.append(selector.replace(":focus-within", "").strip())
    return drawn


@pytest.mark.parametrize("stylesheet,selector,body", _focus_rules())
def test_no_focus_rule_removes_the_outline(stylesheet: str, selector: str, body: str) -> None:
    outline = _OUTLINE.search(body)
    if outline is None:
        return
    value = outline.group("value").strip().lower()
    if value not in {"none", "0", "0px"}:
        return
    inner = selector.replace(":focus-visible", "").strip()
    moved_outwards = any(
        inner.startswith(f"{wrapper} ") for wrapper in _wrappers_that_draw_a_ring(stylesheet)
    )
    assert moved_outwards, (
        f"{stylesheet} removes the focus outline on `{selector}`. "
        "A keyboard user then has no indicator at all. Give focus its own rule with "
        "`outline: var(--hm-focus-ring)` instead of sharing one with :hover — or, if the "
        "ring belongs on a wrapper, draw it there with `:focus-within`."
    )


@pytest.mark.parametrize("stylesheet,selector,body", _focus_rules())
def test_no_focus_ring_is_see_through(stylesheet: str, selector: str, body: str) -> None:
    outline = _OUTLINE.search(body)
    if outline is None:
        return
    value = outline.group("value")
    assert not _SEE_THROUGH.search(value), (
        f"{stylesheet} paints the focus ring on `{selector}` in a see-through colour "
        f"({value.strip()}). Every one of these measured under 1.5:1. Use "
        "`var(--hm-focus-ring)`."
    )


@pytest.mark.parametrize("stylesheet,selector,body", _focus_rules())
def test_a_pale_focus_ring_carries_a_halo(stylesheet: str, selector: str, body: str) -> None:
    outline = _OUTLINE.search(body)
    if outline is None:
        return
    value = outline.group("value").lower()
    if not any(pale in value for pale in _TOO_PALE):
        return
    assert "box-shadow" in body.lower(), (
        f"{stylesheet} paints the focus ring on `{selector}` in a colour that measures "
        "under 3:1 on a light surface, with nothing behind it. Either use "
        "`var(--hm-focus-ring)`, or keep the accent and draw a contrasting halo behind "
        "it with `box-shadow`, the way the near-black band does."
    )


def test_every_declaration_site_agrees() -> None:
    """The ring is declared three times, on purpose, and the three must never drift.

    `hilalmarkets-brand.css` owns it. `hilalmarkets.css` carries the same value because
    the public Jinja pages load that file and never load the brand file. The React
    site's `index.css` carries it because those pages load neither.

    Three copies is three chances to fix one and miss the others, so they are held
    together here. The React one was missed: while the rest of the product drew a
    near-black ring with an accent halo behind it, the public pages drew a plain green
    outline — a different indicator on the same site.
    """

    wanted = re.compile(
        r"--hm-focus-ring:\s*(?P<ring>[^;]+);\s*\n?\s*--hm-focus-halo:\s*(?P<halo>[^;]+);"
    )
    sources = {
        "hilalmarkets-brand.css": STATIC / "hilalmarkets-brand.css",
        "hilalmarkets.css": STATIC / "hilalmarkets.css",
        "Hilal-Markets-Website/src/index.css": REACT_STYLES,
    }
    values = {}
    for name, path in sources.items():
        match = wanted.search(path.read_text(encoding="utf-8"))
        assert match is not None, f"{name} does not declare --hm-focus-ring and --hm-focus-halo."
        values[name] = (match.group("ring").strip(), match.group("halo").strip())

    assert len(set(values.values())) == 1, (
        "The focus ring is written differently in the files that declare it: "
        f"{values}. A keyboard user would then meet a different indicator depending on "
        "which half of the site they are on."
    )
    # Near-black, so it is readable on white. Never a translucent value.
    ring = next(iter(values.values()))[0]
    assert not _SEE_THROUGH.search(ring), ring


def test_the_public_site_gives_every_control_the_ring_by_default() -> None:
    """One rule on the elements, not one rule per component.

    A per-component focus style is a style a new component ships without. The public
    stylesheet declares it once, for every kind of control at once, so a control added
    tomorrow already has an indicator.
    """

    text = REACT_STYLES.read_text(encoding="utf-8")
    match = re.search(
        r":where\(([^)]*)\):focus-visible\s*\{(?P<body>[^}]*)\}", text
    )
    assert match is not None, (
        "Hilal-Markets-Website/src/index.css has no blanket :focus-visible rule. "
        "Without one, every new control needs its own, and one of them will not get it."
    )
    elements = {name.strip() for name in match.group(1).split(",")}
    for control in ("a", "button", "input", "select", "textarea"):
        assert control in elements, control
    assert "var(--hm-focus-ring)" in match.group("body")
    assert "var(--hm-focus-halo)" in match.group("body")
