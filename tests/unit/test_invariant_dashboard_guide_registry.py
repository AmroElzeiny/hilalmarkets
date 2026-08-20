"""Rules the contextual page guide must keep, checked without a browser.

The Playwright suite proves the guide works in a live DOM. This one runs in
milliseconds on every commit, and catches the failure that actually happens: someone
renames or deletes a marker during ordinary UI work, and a guide silently loses a step
in production.

Everything here is derived from the shipped files. Nothing is a second copy of the step
list — a test that restated the registry would pass while the registry was wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUIDE_SCRIPT = ROOT / "src/ai_market_monitor/static/hilalmarkets-guide.js"
GUIDE_STYLES = ROOT / "src/ai_market_monitor/static/hilalmarkets-guide.css"
TEMPLATES = ROOT / "src/ai_market_monitor/templates"
DASHBOARD_SHELL = TEMPLATES / "hilal/base_dashboard.html"
TOPBAR = TEMPLATES / "hilal/partials/dashboard_topbar.html"
SIDEBAR = TEMPLATES / "hilal/partials/dashboard_sidebar.html"
ICONS = ROOT / "src/ai_market_monitor/static/hilalmarkets-icons.js"
SITE_CONTENT = ROOT / "src/ai_market_monitor/core/site_content.py"

MARKER_ATTRIBUTE = "data-hm-guide-target"

#: Words a guide may never use. Each one is either a claim the platform cannot support,
#: or the kind of filler the brief excludes.
FORBIDDEN_WORDS = re.compile(
    r"\b(halal|haram|shariah-compliant|profit|guaranteed|guarantee|"
    r"will rise|will fall|buy now|sell now|next button|this is the)\b",
    re.IGNORECASE,
)


def _script() -> str:
    return GUIDE_SCRIPT.read_text(encoding="utf-8")


def _steps() -> list[tuple[str, str, str]]:
    """Every (target, title, body) the registry ships, read from the file itself."""

    pattern = re.compile(
        r'target:\s*"(?P<target>[a-z0-9-]+)",\s*'
        r'title:\s*"(?P<title>[^"]+)",\s*'
        r'body:\s*"(?P<body>[^"]+)",',
        re.MULTILINE,
    )
    found = [
        (match["target"], match["title"], match["body"])
        for match in pattern.finditer(_script())
    ]
    assert found, "no guide steps could be read out of hilalmarkets-guide.js"
    return found


def _markers() -> dict[str, list[str]]:
    """Every marker the product can render, and where each one is declared.

    Two declaration sites, both of which place a marker exactly once:

    * a literal attribute in a template;
    * ``NavigationItem.guide_target`` in the site content. The side menu renders its
      links from a loop, so a literal marker there could never be unique. Naming it in
      the navigation data keeps one declaration per name, which is the property the
      guide's exact targeting depends on.
    """

    placed: dict[str, list[str]] = {}
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for name in re.findall(rf'{MARKER_ATTRIBUTE}="([a-z0-9-]+)"', text):
            placed.setdefault(name, []).append(path.name)
    for name in re.findall(r'guide_target="([a-z0-9-]+)"', SITE_CONTENT.read_text("utf-8")):
        placed.setdefault(name, []).append(SITE_CONTENT.name)
    return placed


# ---------------------------------------------------------------------------
# Targeting is exact, in both directions.
# ---------------------------------------------------------------------------


def _separated_by_a_branch(target: str) -> bool:
    """Whether every declaration of ``target`` in one template is in its own branch.

    A page with two layouts — an empty account and a populated one — has to carry the
    marker in both, or the guide points at nothing in whichever layout is missing it.
    Only one of them is ever rendered, so that is still exactly one element on screen.

    What is checked is that an ``{% else %}`` stands between the declarations. Two
    markers inside the same branch would both render, and the guide would highlight
    whichever came first.
    """

    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        found = [match.start() for match in re.finditer(rf'{MARKER_ATTRIBUTE}="{target}"', text)]
        if len(found) < 2:
            continue
        for left, right in zip(found, found[1:], strict=False):
            if "{% else %}" not in text[left:right]:
                return False
    return True


@pytest.mark.parametrize("target", sorted({item[0] for item in _steps()}))
def test_every_configured_target_is_placed_exactly_once(target: str) -> None:
    """Zero matches shows nothing; two rendered matches point at the wrong one."""

    placed = _markers().get(target, [])
    assert placed, f"'{target}' is configured but no template carries the marker"
    assert len(set(placed)) == 1, f"'{target}' is in several templates: {sorted(set(placed))}"
    assert _separated_by_a_branch(target), (
        f"'{target}' is declared more than once in the same branch, so two elements "
        "would render and the guide would point at whichever came first"
    )


def test_no_marker_is_placed_without_a_step_to_use_it() -> None:
    """A marker nobody references is dead markup that survives every refactor."""

    configured = {item[0] for item in _steps()}
    orphans = sorted(name for name in _markers() if name not in configured)
    assert not orphans, f"markers with no guide step: {orphans}"


def test_markers_are_never_placed_inside_a_repeating_block() -> None:
    """A marker in a loop or a macro renders many times, so it can never be unique.

    Checked structurally rather than by counting the rendered page: a loop that happens
    to run once today is still a duplicate waiting for the second row of data.

    The one allowed exception is the side menu, where the value comes from the item
    being rendered rather than being written into the loop. It is checked separately by
    ``test_the_side_menu_marker_reads_its_name_from_the_navigation_data``.
    """

    problems: list[str] = []
    for path in TEMPLATES.rglob("*.html"):
        depth = 0
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            opens = len(re.findall(r"{%-?\s*(?:for|macro)\b", line))
            closes = len(re.findall(r"{%-?\s*end(?:for|macro)\s*-?%}", line))
            literal = re.search(rf'{MARKER_ATTRIBUTE}="[a-z0-9-]+"', line)
            if literal and depth > 0:
                problems.append(f"{path.name}:{number} is inside a loop or macro")
            depth += opens - closes
    assert not problems, "guide markers cannot be unique here:\n" + "\n".join(problems)


def test_the_side_menu_marker_reads_its_name_from_the_navigation_data() -> None:
    """The menu is a loop, so its marker must name one item, never a fixed string."""

    sidebar = SIDEBAR.read_text(encoding="utf-8")
    assert f'{MARKER_ATTRIBUTE}="{{{{ item.guide_target }}}}"' in sidebar
    assert not re.search(rf'{MARKER_ATTRIBUTE}="[a-z0-9-]+"', sidebar), (
        "a fixed marker in the side menu would render once per link"
    )
    declared = re.findall(r'guide_target="([a-z0-9-]+)"', SITE_CONTENT.read_text("utf-8"))
    repeated = sorted({name for name in declared if declared.count(name) > 1})
    assert not repeated, f"a navigation guide target is declared twice: {repeated}"


# ---------------------------------------------------------------------------
# Copy rules.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("target", "title", "body"), _steps())
def test_step_copy_stays_short_and_says_nothing_the_product_cannot(
    target: str,
    title: str,
    body: str,
) -> None:
    assert len(title.split()) <= 5, f"{target}: title is {len(title.split())} words"
    sentences = [item for item in re.split(r"(?<=[.!?])\s+", body.strip()) if item]
    assert len(sentences) <= 2, f"{target}: body is {len(sentences)} sentences"
    assert 8 <= len(body.split()) <= 34, f"{target}: body is {len(body.split())} words"
    assert not FORBIDDEN_WORDS.search(f"{title} {body}"), f"{target}: forbidden wording"


def test_no_two_steps_repeat_the_same_explanation() -> None:
    """The brief excludes repeated versions of a concept already explained."""

    bodies = [item[2] for item in _steps()]
    duplicates = {item for item in bodies if bodies.count(item) > 1}
    assert not duplicates, f"the same explanation appears twice: {duplicates}"


# ---------------------------------------------------------------------------
# Wiring the shell has to keep.
# ---------------------------------------------------------------------------


def test_the_page_key_comes_from_the_server_not_the_url() -> None:
    shell = DASHBOARD_SHELL.read_text(encoding="utf-8")
    assert 'data-hm-guide-page="{{ guide_page_key }}"' in shell
    # The key is looked up from `page`, which the router sets. Nothing reads the address
    # bar. There used to be one exception — the assistant page, where `?mode=scanner`
    # picked a second guide — and that page is gone, so the exception is gone with it.
    assert "request.query_params" not in shell.split("set guide_page_key")[1].split("%}")[0]
    assert "hilalmarkets-guide.js" in shell
    assert "hilalmarkets-guide.css" in shell


def test_every_page_key_the_shell_can_emit_has_a_guide() -> None:
    """A key with no guide hides the launcher, which looks like a bug to a user."""

    shell = DASHBOARD_SHELL.read_text(encoding="utf-8")
    mapping = re.search(r"set guide_page_key = \{(.+?)\}\.get\(page", shell, re.DOTALL)
    assert mapping, "the page-key map could not be read out of the shell"
    emitted = set(re.findall(r"'([a-z-]+)'\s*(?=[,}])", mapping.group(1)))
    emitted |= set(re.findall(r"guide_page_key = '([a-z-]+)'", shell))
    registered = set(re.findall(r'^\s+"?([a-z-]+)"?:\s*\{\s*$', _script(), re.MULTILINE))
    registered |= set(re.findall(r'id:\s*"([a-z-]+)"', _script()))
    missing = sorted(key for key in emitted if key not in registered)
    assert not missing, f"the shell can emit these keys with no guide behind them: {missing}"


def test_the_new_user_signal_is_server_owned() -> None:
    """Auto-start must rest on a recorded onboarding step, never on empty data."""

    shell = DASHBOARD_SHELL.read_text(encoding="utf-8")
    assert "data-hm-guide-new-user" in shell
    assert "onboarding_complete" in shell
    router = (ROOT / "src/ai_market_monitor/api/routers/dashboard.py").read_text(
        encoding="utf-8"
    )
    assert '"onboarding_complete": bool(user and user.onboarding_completed_at)' in router
    script = _script()
    for guessed in ("counts.strategies", "created_at", "watchlists.length"):
        assert guessed not in script, f"the guide inferred a new user from {guessed}"


def test_one_launcher_serves_every_page() -> None:
    """A second launcher implementation per template is the thing to avoid."""

    launchers = [
        path.name
        for path in TEMPLATES.rglob("*.html")
        if "data-hm-guide-launcher" in path.read_text(encoding="utf-8")
    ]
    assert launchers == [TOPBAR.name], f"the launcher is defined in {launchers}"
    topbar = TOPBAR.read_text(encoding="utf-8")
    assert 'aria-label="Guide this page"' in topbar
    assert 'data-icon="guide"' in topbar
    assert "guide:" in ICONS.read_text(encoding="utf-8"), "the guide icon is missing"


# ---------------------------------------------------------------------------
# The engine's own promises.
# ---------------------------------------------------------------------------


def test_the_engine_refuses_zero_or_many_matches_rather_than_guessing() -> None:
    script = _script()
    assert "matches.length === 0" in script
    assert "matches.length > 1" in script
    for guess in (":nth-child", "closest(", "parentElement", "firstElementChild"):
        assert guess not in script, f"the engine falls back to {guess}"


def test_the_engine_never_touches_the_element_it_is_describing() -> None:
    """Dim around it, never over it: four panels and a real hole."""

    script = _script()
    for panel in ("top", "bottom", "left", "right"):
        assert f'data-hm-guide-panel="{panel}"' in script
    styles = GUIDE_STYLES.read_text(encoding="utf-8")
    assert "backdrop-filter" in styles
    # The blur belongs to the surrounding panels only.
    spotlight = styles.split(".hm-guide-spotlight", 1)[1].split("}", 1)[0]
    assert "filter" not in spotlight, "the spotlight must not filter the target"


def test_no_observer_can_be_woken_by_its_own_writes() -> None:
    """An observer that reacts to a change it makes itself never stops.

    This one is not theoretical. The launcher check wrote `hidden` on every pass while
    watching attribute changes across the page, so each write scheduled the next. Those
    callbacks are microtasks, so the loop starved the event loop: the page never finished
    loading, no timer ran, and the browser sat on it for ever.

    Two rules keep that shut, and each one alone is enough:

    * the launcher watcher looks for elements arriving, not for attribute changes;
    * `hidden` is written only when the value actually differs.
    """

    script = _script()
    watcher = script.split("Content may still be rendering", 1)[1].split("}\n", 1)[0]
    assert "attributes: true" not in watcher, (
        "the launcher watcher must not react to attribute changes it can cause"
    )
    assert "if (launcher.hidden === show) launcher.hidden = !show;" in script, (
        "the launcher's hidden attribute must only be written when it really changes"
    )
    # The repositioning observer writes inline styles on its own panels.
    assert "records.every((record) => this.root.contains(record.target))" in script, (
        "repositioning must ignore the guide's own attribute writes"
    )


def test_reduced_motion_and_focus_rules_are_present() -> None:
    styles = GUIDE_STYLES.read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in styles
    assert "min-height: 44px" in styles, "controls must stay tappable on mobile"
    script = _script()
    assert 'role="dialog"' in script
    assert 'aria-modal="true"' in script
    assert 'aria-live="polite"' in script
    assert "trapFocus" in script
    assert '"Escape"' in script


def test_completion_keys_carry_the_guide_version() -> None:
    """Changing a guide must offer it again instead of staying suppressed forever."""

    script = _script()
    assert "`hm-guide:${guide.id}:v${guide.version}`" in script
    versions = re.findall(r"version:\s*(\d+)", script)
    assert versions, "no guide declares a version"
    assert all(int(item) >= 1 for item in versions)


def test_the_guide_adds_no_third_party_tour_dependency() -> None:
    script = _script()
    for library in ("driver.js", "shepherd", "intro.js", "import ", "require("):
        assert library not in script.lower(), f"the guide pulled in {library}"
