"""The canvas must be able to draw everything the server can offer.

The board reads the Builder contract, and the contract is written in Python while the
board is written in JavaScript. That is the same two-languages-one-vocabulary problem
`test_invariant_dashboard_test_tone.py` already guards for asset statuses, and it fails
the same way: the server grows a category, the browser has never heard of it, and a
whole family of rules reaches the board wearing a raw key like ``liquidity_smart_money``
as its heading — or asks for an icon that does not exist and draws nothing at all.

These tests read both sources and hold them to one content. They are parametrised over
the whole family, not over one example: every category the catalogue can emit, and every
icon name any monitor file asks for.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import ai_market_monitor
from ai_market_monitor.engine.builder_operations import mechanic_catalog

PACKAGE = Path(ai_market_monitor.__file__).parent
STATIC = PACKAGE / "static"
CATALOG_JS = STATIC / "hm-monitor-catalog.js"
ICONS_JS = STATIC / "hilalmarkets-icons.js"
MONITOR_FILES = (
    STATIC / "hm-monitor-catalog.js",
    STATIC / "hm-monitor-plan.js",
    STATIC / "hm-monitor-board.js",
    STATIC / "hm-monitor-test.js",
)
MONITOR_TEMPLATE = PACKAGE / "templates" / "hilal" / "dashboard_test" / "monitor.html"


def _category_look() -> dict[str, dict[str, str]]:
    """The plain name and icon the board gives each category."""

    source = CATALOG_JS.read_text(encoding="utf-8")
    block = re.search(r"CATEGORY_LOOK = Object\.freeze\(\{(.*?)\n\}\);", source, re.DOTALL)
    assert block, f"CATEGORY_LOOK is no longer declared in {CATALOG_JS.name}"
    entries: dict[str, dict[str, str]] = {}
    pattern = re.compile(
        r"""(?P<key>"[^"]+"|[A-Za-z_][\w]*)\s*:\s*\{\s*name:\s*"(?P<name>[^"]+)"\s*,"""
        r"""\s*icon:\s*"(?P<icon>[^"]+)"\s*\}"""
    )
    for match in pattern.finditer(block.group(1)):
        key = match.group("key").strip('"')
        entries[key] = {"name": match.group("name"), "icon": match.group("icon")}
    assert entries, "CATEGORY_LOOK was found but no entry could be read from it"
    return entries


def _icon_names() -> set[str]:
    source = ICONS_JS.read_text(encoding="utf-8")
    block = re.search(r"const ICONS = Object\.freeze\(\{(.*?)\n\}\);", source, re.DOTALL)
    assert block, f"ICONS is no longer declared in {ICONS_JS.name}"
    return set(re.findall(r"^\s*([A-Za-z_][\w]*)\s*:\s*'", block.group(1), re.MULTILINE))


def _server_categories() -> set[str]:
    return {mechanic.category for mechanic in mechanic_catalog()}


@pytest.mark.parametrize("category", sorted(_server_categories()))
def test_every_category_the_server_can_send_has_words_on_the_board(category):
    """A category with no entry would reach a beginner as a raw key."""
    assert category in _category_look(), (
        f"{category!r} has no plain name in {CATALOG_JS.name}. "
        "A beginner would read the internal key as the heading of the card."
    )


@pytest.mark.parametrize("category", sorted(_server_categories()))
def test_no_category_name_leaks_an_internal_key(category):
    """The words shown must be words, not a key with its underscores taken out."""
    name = _category_look()[category]["name"]
    assert "_" not in name
    assert name == name.strip()
    assert name[:1].isupper(), f"{name!r} is not written in sentence case"


@pytest.mark.parametrize("category", sorted(_server_categories()))
def test_every_category_icon_exists(category):
    icon = _category_look()[category]["icon"]
    assert icon in _icon_names(), f"{category!r} asks for an icon {icon!r} that does not exist"


@pytest.mark.parametrize("path", MONITOR_FILES, ids=lambda path: path.name)
def test_every_icon_the_canvas_asks_for_exists(path):
    """`icon("trsah")` draws the fallback silently. Named here, it fails loudly."""
    source = path.read_text(encoding="utf-8")
    asked = set(re.findall(r"""icon\(\s*"([a-z_]+)"\s*[,)]""", source))
    missing = sorted(asked - _icon_names())
    assert not missing, f"{path.name} asks for icons that do not exist: {missing}"


def test_every_reference_between_elements_on_the_page_resolves():
    """`aria-controls="m-check-list"` pointing at nothing is a silent failure.

    Nothing breaks on screen, nothing appears in the console, and a screen reader is
    simply told less than the page promised. Checked over every reference in the file
    rather than the one that was noticed missing.
    """
    source = MONITOR_TEMPLATE.read_text(encoding="utf-8")
    ids = set(re.findall(r'\bid="([^"]+)"', source))
    referenced = {
        (attribute, value)
        for attribute in ("aria-controls", "aria-labelledby", "aria-describedby", "for")
        for group in re.findall(rf'\b{attribute}="([^"]+)"', source)
        for value in group.split()
    }
    missing = sorted(f"{attribute}={value}" for attribute, value in referenced if value not in ids)
    assert not missing, f"monitor.html points at ids that do not exist: {missing}"


def test_the_template_only_asks_for_icons_that_exist():
    source = MONITOR_TEMPLATE.read_text(encoding="utf-8")
    asked = set(re.findall(r'data-icon="([a-z_]+)"', source))
    missing = sorted(asked - _icon_names())
    assert not missing, f"monitor.html asks for icons that do not exist: {missing}"


def _navigation_icons() -> list[tuple[str, str]]:
    from ai_market_monitor.core.site_content import DASHBOARD_NAVIGATION

    return [
        (item.label, item.icon or "")
        for group in DASHBOARD_NAVIGATION
        for item in group.items
    ]


@pytest.mark.parametrize(("label", "name"), _navigation_icons(), ids=lambda value: str(value))
def test_every_side_menu_entry_has_an_icon_that_exists(label, name):
    """A menu entry naming an icon nobody has draws an empty box, and nothing reports it.

    That happened when the "Monitor" entry was added: the page rendered, the link worked,
    and the menu simply showed a blank square. Checked for every entry, not the one that
    was noticed.

    The menu used to keep its own icons — eleven `.nav-icon-*` rules masking eleven files
    that nothing else on the site used — so an entry needed an icon in two places and a
    mismatch was invisible. It reads `hilalmarkets-icons.js` now, like everything else,
    which is why this looks there and the second system is gone.
    """
    assert name, f"{label} has no icon"
    available = _icon_names()
    assert name in available, (
        f"{label} uses icon {name!r}, which is not in hilalmarkets-icons.js"
    )


def test_the_board_never_fills_a_required_value_for_a_person():
    """Fail closed. A comparison nobody picked must stay empty, not become "at least".

    This is the invariant that keeps a drawn rule meaning what the person drew. It is
    checked against the source because the alternative — a default quietly appearing —
    produces a monitor that runs and watches the wrong thing.
    """
    source = (STATIC / "hm-monitor-plan.js").read_text(encoding="utf-8")
    body = re.search(r"export function startingValues\(.*?\n\}", source, re.DOTALL)
    assert body, "startingValues is no longer declared"
    text = body.group(0)
    assert "parameter.default === null" in text
    assert "continue" in text
    # Nothing in there may reach for a first option, a preferred operator, or a
    # comparison chosen on the reader's behalf.
    for forbidden in ("choices[0]", "operators[0]", '"gte"', '"gt"', "|| \"", "?? \""):
        assert forbidden not in text, f"startingValues invents a value: {forbidden}"


def test_the_contract_is_read_from_one_place():
    """Two readers of the same contract is how the two would come to disagree.

    The rule is about the **Builder contract** — what a person may add, choose or set —
    not about talking to the server at all. The canvas legitimately asks three other
    questions of its own: which screened coins match what somebody typed, which
    Favorites lists they have, and "turn this board into a monitor". None of those is a
    second opinion about the catalogue, and forbidding every ``fetch`` would have meant
    the only way to ask them was to copy the catalogue reader.
    """

    catalog = CATALOG_JS.read_text(encoding="utf-8")
    assert "fetch(" in catalog, "the catalogue reader no longer fetches anything"
    assert "loadCatalog" in catalog

    for path in MONITOR_FILES:
        source = path.read_text(encoding="utf-8")
        if path.name == "hm-monitor-catalog.js":
            continue
        # Handing the address over is not reading it. The page holds the attribute and
        # passes it to `loadCatalog`; what must never appear twice is a *call* for it.
        assert "loadCatalog(" in source or "builder-contract" not in source, (
            f"{path.name} names the contract address without going through loadCatalog"
        )
        # Every other call the canvas makes is to its own endpoints — never to a second
        # copy of the catalogue under a different address.
        for url in re.findall(r"""fetch\(\s*[`"']([^`"']+)""", source):
            assert url.startswith("/api/v1/dashboard/monitor-canvas/"), (
                f"{path.name} calls {url!r}, which is not one of the canvas's own endpoints"
            )


def test_the_canvas_ships_no_second_colour_or_type_scale():
    """`brand guide.md` sections 9 and 11: no new main colour, no third type family."""
    source = (STATIC / "hm-monitor-test.css").read_text(encoding="utf-8")
    stripped = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)

    literals = re.findall(r"#[0-9a-fA-F]{3,8}\b", stripped)
    assert not literals, f"a colour was written by hand instead of used from a token: {literals}"

    families = re.findall(r"font-family:\s*([^;]+);", stripped)
    for family in families:
        assert "var(--hm-font-" in family, f"a type family was named directly: {family.strip()}"
