"""Rules the launch offer and the brand typefaces must follow on every surface.

Each test asserts a rule across a whole family, not the one case that was reported:

* every stylesheet a page actually loads names only self-hosted faces, so no rule can
  ask for a font the page never fetched;
* every shell that loads a stylesheet using the font tokens also loads the file that
  defines them;
* the countdown counts the same units, at the same rate, in both implementations;
* the countdown has one design, with no per-card colour variant to disagree with.

The dashboard used to render prices in the browser's default serif because a rule said
``font-family:"Manrope"`` and nothing on the dashboard had ever fetched Manrope. The same
sentence was written in eleven places, so fixing the price alone would have left ten.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "src" / "ai_market_monitor" / "static"
TEMPLATES = ROOT / "src" / "ai_market_monitor" / "templates"
LANDING = ROOT / "Hilal-Markets-Website" / "src"

FONTS_FILE = STATIC / "hilalmarkets-fonts.css"
OFFER_SCRIPT = STATIC / "hilalmarkets-offer.js"
SHARED_CSS = STATIC / "hilalmarkets.css"
LANDING_CSS = LANDING / "index.css"
LANDING_PRICING = LANDING / "components" / "Pricing.tsx"
BUILT_LANDING = STATIC / "landing" / "assets" / "landing.js"

#: Families that need no `@font-face`: the browser always has one.
GENERIC_FAMILIES = frozenset(
    {
        "inherit",
        "initial",
        "unset",
        "revert",
        "sans-serif",
        "serif",
        "monospace",
        "cursive",
        "fantasy",
        "system-ui",
        "ui-sans-serif",
        "ui-serif",
        "ui-monospace",
        "ui-rounded",
        "-apple-system",
        "blinkmacsystemfont",
        "segoe ui",
        "arial",
        "helvetica",
        "helvetica neue",
        "sfmono-regular",
        "menlo",
    }
)

FONT_FAMILY = re.compile(r"font-family\s*:\s*([^;}]+)")
STYLESHEET_LINK = re.compile(
    r"""path=['"]/([a-z0-9\-]+\.css)['"]|href="/static/([a-z0-9\-]+\.css)"""
)


def _self_hosted_families() -> frozenset[str]:
    """The faces the product ships, read from the one file that declares them."""

    declared = re.findall(r"font-family:\s*\"([^\"]+)\"", FONTS_FILE.read_text(encoding="utf-8"))
    assert declared, "no @font-face families are declared"
    return frozenset(name.casefold() for name in declared)


def _linked_stylesheets() -> set[str]:
    """Stylesheet file names that some template actually loads."""

    linked: set[str] = set()
    for template in TEMPLATES.rglob("*.html"):
        for direct, absolute in STYLESHEET_LINK.findall(template.read_text(encoding="utf-8")):
            linked.add(direct or absolute)
    assert "hilalmarkets.css" in linked
    return linked


def _first_family(value: str) -> str:
    return value.split(",")[0].strip().strip("\"'").casefold()


@pytest.mark.parametrize("stylesheet", sorted(_linked_stylesheets()))
def test_a_served_stylesheet_never_names_a_font_the_page_does_not_load(
    stylesheet: str,
) -> None:
    path = STATIC / stylesheet
    if not path.is_file():
        pytest.skip(f"{stylesheet} is not a local file")
    allowed = _self_hosted_families() | GENERIC_FAMILIES
    for declaration in FONT_FAMILY.findall(path.read_text(encoding="utf-8")):
        declaration = declaration.strip()
        if declaration.startswith("var(--hm-font-"):
            continue
        assert _first_family(declaration) in allowed, (
            f"{stylesheet} asks for {declaration!r}, which nothing self-hosts"
        )


def test_the_font_tokens_have_exactly_one_definition() -> None:
    """Two definitions is two chances to disagree about what the brand font is."""

    definitions = [
        path.name
        for path in STATIC.rglob("*.css")
        if "landing" not in path.as_posix()
        and re.search(r"--hm-font-display\s*:", path.read_text(encoding="utf-8"))
    ]
    assert definitions == [FONTS_FILE.name]


def test_the_typefaces_are_declared_once_for_the_server_rendered_pages() -> None:
    declaring = [
        path.name
        for path in STATIC.rglob("*.css")
        if "landing" not in path.as_posix() and "@font-face" in path.read_text(encoding="utf-8")
    ]
    assert declaring == [FONTS_FILE.name]


EXTENDS = re.compile(r"""\{%-?\s*extends\s+['"]([^'"]+)['"]""")


def _rendered_head(template: str) -> str:
    """A template plus everything it inherits from, so a base counts for its children."""

    text = (TEMPLATES / template).read_text(encoding="utf-8")
    parent = EXTENDS.search(text)
    return text if parent is None else text + _rendered_head(parent.group(1))


@pytest.mark.parametrize(
    "shell",
    sorted(
        path.relative_to(TEMPLATES).as_posix()
        for path in TEMPLATES.rglob("*.html")
        if re.search(
            r"(hilalmarkets|system-brain|dashboard)\.css", path.read_text(encoding="utf-8")
        )
    ),
)
def test_every_page_that_uses_the_font_tokens_also_defines_them(shell: str) -> None:
    """A token nobody defined leaves the browser to pick its own font."""

    assert FONTS_FILE.name in _rendered_head(shell), (
        f"{shell} uses the font tokens without loading them"
    )


UNITS = ("day", "hour", "minute", "second")


@pytest.mark.parametrize("unit", UNITS)
def test_both_countdowns_count_the_same_units(unit: str) -> None:
    """Days, hours, minutes and seconds, on the landing page and everywhere else."""

    assert f'"{unit}"' in OFFER_SCRIPT.read_text(encoding="utf-8")
    assert f"'{unit}'" in LANDING_PRICING.read_text(encoding="utf-8")


def test_both_countdowns_step_once_a_second() -> None:
    """A countdown showing seconds that updates once a minute is a stopped clock."""

    assert "var SECOND = 1000;" in OFFER_SCRIPT.read_text(encoding="utf-8")
    assert "setInterval(tickOne" not in OFFER_SCRIPT.read_text(encoding="utf-8")
    landing = LANDING_PRICING.read_text(encoding="utf-8")
    assert "const SECOND = 1_000" in landing
    assert "setInterval(() => setNow(Date.now()), everyMs)" in landing


def test_every_countdown_says_it_is_live_the_same_way() -> None:
    """One stylesheet decides whether a countdown is shown, so both must speak to it.

    `hilalmarkets.css` hides any `.offer-countdown` that does not carry
    `data-offer-live`, so a box with no numbers in it never flashes on screen. The
    server-rendered pages get the attribute from `hilalmarkets-offer.js`. The landing
    page draws its own countdown in React and did **not** set it — so the timer was
    inside the card, in the page, and hidden by the shared rule on every visit for as
    long as the offer ran. The mark is the contract; every implementation sets it.
    """

    shared = SHARED_CSS.read_text(encoding="utf-8")
    assert ".offer-countdown:not([data-offer-live])" in shared, (
        "the rule that makes this a contract is gone"
    )

    for name, source in (
        (OFFER_SCRIPT.name, OFFER_SCRIPT.read_text(encoding="utf-8")),
        (LANDING_PRICING.name, LANDING_PRICING.read_text(encoding="utf-8")),
        # What visitors are actually served, not only what the source says: the copy into
        # `static/landing/assets/` is done by hand.
        ("landing.js", BUILT_LANDING.read_text(encoding="utf-8")),
    ):
        assert "data-offer-live" in source, f"{name} draws a countdown nothing will show"


def test_the_countdown_has_no_per_card_colour_variant() -> None:
    """`is-featured` is a dark card on the public site and a light one in the dashboard.

    A colour written for one of them was always wrong on the other: the countdown used
    to paint white numbers on the dashboard's white card. One design, no variant.
    """

    shared = SHARED_CSS.read_text(encoding="utf-8")
    assert ".is-featured .offer-countdown" not in shared
    assert ".is-featured .price .price-original" not in shared


@pytest.mark.parametrize(
    ("declaration", "landing_declaration"),
    (
        ("border-radius:16px", "border-radius: 16px"),
        ("padding:12px 14px", "padding: 12px 14px"),
        ("font-variant-numeric:tabular-nums", "font-variant-numeric: tabular-nums"),
        ("min-width:2ch", "min-width: 2ch"),
        ("font-weight:500", "font-weight: 500"),
    ),
)
def test_the_countdown_looks_the_same_on_both_surfaces(
    declaration: str, landing_declaration: str
) -> None:
    shared = SHARED_CSS.read_text(encoding="utf-8")
    offer_block = shared[shared.index(".offer-countdown{") :]
    offer_block = offer_block[: offer_block.index(".public-plan-details")]
    landing = LANDING_CSS.read_text(encoding="utf-8")
    landing_block = landing[landing.index(".offer-countdown {") : landing.index(".annual-saving")]
    assert declaration in offer_block or declaration in shared
    assert landing_declaration in landing_block


def test_only_one_script_hides_the_countdown() -> None:
    """Two scripts setting the same flag take turns undoing each other every second."""

    billing = (STATIC / "hilalmarkets-billing.js").read_text(encoding="utf-8")
    assert "countdown.hidden" not in billing
    assert "original.hidden" not in billing
    assert "data-offer-inactive" in billing
    assert "data-offer-inactive" in SHARED_CSS.read_text(encoding="utf-8")
