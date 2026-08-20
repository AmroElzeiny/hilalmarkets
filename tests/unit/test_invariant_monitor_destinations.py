"""Where a person lands after they act on a monitor.

Every one of these actions — pause, start again, put away, open a waiting repair, throw
one away, switch a custom rule off, switch it back on — used to end on the AI Setup Chat
page, at an anchor naming a section of that page that is marked ``hidden``. So the page
somebody landed on reopened a conversation about something else and showed no monitor at
all. The same address was written into Telegram buttons, WhatsApp replies and the repair
notice, none of which can be corrected once the message has been sent.

That page has since been deleted outright: the canvas is the only place a monitor is
authored, and the same page changes one. Its two addresses stay as permanent redirects
because they are written into messages already sent — but nothing in the product may
*point* at them any more, which is what rule 1 now measures.

The rules here are about the whole family, not about the one action that was reported:

1. Nothing shipped links to the assistant page, at any of its addresses.
2. No redirect that carries a confirmation may reopen it.
3. Putting a monitor away opens the canvas; every other monitor action opens the list.
4. Every confirmation a dashboard page can receive has a sentence a beginner can read.
5. Changing a monitor opens the canvas on that monitor.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src/ai_market_monitor"

DASHBOARD = SRC / "api/routers/dashboard.py"
DASHBOARD_JS = SRC / "static/dashboard.js"
SHELL = SRC / "templates/hilal/base_dashboard.html"

#: The address this whole file exists to keep out of the product.
#:
#: It is the assistant page, which is gone. The hidden anchor that started this — the
#: monitors section of that page, which was marked ``hidden`` — cannot be linked to
#: because there is nothing left to link to, so the rule is now the whole address.
ASSISTANT_PAGE = "/dashboard/strategies/new"

#: The two places the address is allowed to appear: the constant that defines it, and the
#: comments that explain why it still answers. Everything else pointing at it is a link.
ASSISTANT_PAGE_OWNERS = {
    "src/ai_market_monitor/core/dashboard_paths.py",
}

#: Every page the product serves to a signed-in person, by the constant that owns it.
#: A redirect target has to start with one of these or it points at nothing.
KNOWN_DESTINATIONS = (
    "/dashboard",
    "/home",
    "{_AFTER_MONITOR_ACTION}",
    "{_AFTER_MONITOR_DELETED}",
    "{HOME_PATH}",
    "{CONNECTIONS_PATH}",
    "{INTEGRATIONS_PATH}",
    "{LIFECYCLES_PATH}",
    "{MONITOR_PATH}",
    "{MONITORS_PATH}",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _shipped_files() -> list[Path]:
    return sorted(
        path
        for suffix in ("*.py", "*.js", "*.html")
        for path in SRC.rglob(suffix)
    )


def _redirect_targets() -> list[str]:
    """Every literal address ``dashboard.py`` sends somebody to."""

    return re.findall(r"_redirect\(\s*f?\"([^\"]+)\"", _read(DASHBOARD))


def _browser_targets() -> list[str]:
    """Every address the dashboard script sends the browser to."""

    return re.findall(r"window\.location\.href = `(/[^`]*)`", _read(DASHBOARD_JS))


def _route_body(name: str) -> str:
    source = _read(DASHBOARD)
    start = source.index(f"async def {name}(")
    end = source.index("\n@router.", start)
    return source[start:end]


def _plain_words() -> dict[str, str]:
    """The sentences the dashboard shell shows after something worked."""

    block = re.search(
        r"set dashboard_messages = \{(?P<body>.*?)\n        \} %\}",
        _read(SHELL),
        re.DOTALL,
    )
    assert block is not None, "the dashboard shell has no dashboard_messages map"
    return dict(re.findall(r"'([a-z_]+)': '([^']+)'", block.group("body")))


def _confirmation_keys() -> set[str]:
    """Every ``message=`` key that can arrive on a page drawn by the dashboard shell."""

    keys: set[str] = set()
    for target in _redirect_targets() + _browser_targets():
        if not target.startswith(KNOWN_DESTINATIONS):
            # Sign-in, verification and password pages draw their own words from
            # `page_copy`, not from the dashboard shell, so their keys are not asked for
            # here.
            continue
        keys.update(re.findall(r"[?&]message=([a-z_]+)", target))
    return keys


# ── The reason the rule exists ───────────────────────────────────────────────


def test_the_assistant_page_is_gone_and_its_address_forwards_to_the_canvas() -> None:
    """One page authors a monitor. The old address still answers, and only forwards.

    If this ever stops being true the rest of this file is arguing about nothing, so it
    is measured rather than remembered.
    """

    from ai_market_monitor.api.routers import dashboard
    from ai_market_monitor.core.dashboard_paths import LEGACY_ASSISTANT_PATH

    assert LEGACY_ASSISTANT_PATH == ASSISTANT_PAGE
    assert not (SRC / "templates/hilal/dashboard/builder.html").exists()
    assert not (SRC / "templates/hilal/dashboard/partials/builder_workspace.html").exists()

    paths = {getattr(route, "path", "") for route in dashboard.router.routes}
    assert ASSISTANT_PAGE in paths, "the old address must keep answering, not 404"


# ── 1. Nothing links to it ───────────────────────────────────────────────────


def test_nothing_shipped_links_to_the_assistant_page() -> None:
    """Every reference outside its own constant is a link somebody can follow.

    A comment mentioning the address is not a link, so only lines carrying it inside an
    `href`, a redirect or a quoted path count.
    """

    offenders = []
    for path in _shipped_files():
        name = path.relative_to(ROOT).as_posix()
        if name in ASSISTANT_PAGE_OWNERS:
            continue
        for line in _read(path).splitlines():
            stripped = line.strip()
            if ASSISTANT_PAGE not in stripped:
                continue
            if stripped.startswith(("#", "#:", "*", "{#", "//", "/*")):
                continue
            offenders.append(f"{name}: {stripped[:100]}")
    assert offenders == []


# ── 2. No confirmation reopens the assistant page ────────────────────────────


@pytest.mark.parametrize("target", sorted(set(_redirect_targets() + _browser_targets())))
def test_no_redirect_carrying_a_confirmation_reopens_the_assistant_page(target: str) -> None:
    if "message=" not in target and "error=" not in target:
        return
    assert ASSISTANT_PAGE not in target


# ── 3. Each action opens the page it is about ────────────────────────────────


@pytest.mark.parametrize(
    "route",
    [
        "pause_monitor",
        "resume_monitor",
        "discard_capability_repair",
        "quarantine_capability_extension",
        "restore_capability_extension",
    ],
)
def test_a_monitor_action_opens_the_list_of_monitors(route: str) -> None:
    body = _route_body(route)
    sent = re.findall(r"_redirect\(\s*f?\"([^\"]+)\"", body)
    assert sent, route
    for target in sent:
        assert target.startswith("{_AFTER_MONITOR_ACTION}"), (route, target)


def test_putting_a_monitor_away_opens_the_canvas() -> None:
    """The one place a fresh canvas is the right answer: the monitor is gone."""

    body = _route_body("delete_monitor")
    sent = re.findall(r"_redirect\(\s*f?\"([^\"]+)\"", body)
    assert "{_AFTER_MONITOR_DELETED}?message=monitor_deleted" in sent
    # The failure still lands on the list, because nothing was put away.
    assert "{_AFTER_MONITOR_ACTION}?error={exc.code}" in sent


def test_the_two_destinations_are_the_pages_that_own_those_addresses() -> None:
    from ai_market_monitor.api.routers import dashboard
    from ai_market_monitor.core.dashboard_paths import MONITOR_PATH, MONITORS_PATH

    assert dashboard._AFTER_MONITOR_ACTION == MONITORS_PATH
    assert dashboard._AFTER_MONITOR_DELETED == MONITOR_PATH


# ── 5. Changing a monitor opens the canvas on that monitor ───────────────────


def test_changing_a_monitor_opens_the_canvas_on_that_monitor() -> None:
    """One address for authoring, whether a monitor is being made or changed.

    Written as a rule about the address rather than about the Monitors page, because
    five different surfaces offer "change this monitor" and each one used to write the
    older assistant page's address by hand.
    """

    from ai_market_monitor.core.dashboard_paths import MONITOR_PATH, monitor_edit_path

    made = monitor_edit_path("abc")
    assert made.startswith(f"{MONITOR_PATH}?")
    assert made.endswith("monitor=abc")


def test_every_surface_that_changes_a_monitor_uses_that_one_address() -> None:
    """No shipped file may build the canvas edit address out of its own pieces."""

    from ai_market_monitor.core.dashboard_paths import MONITOR_PATH

    owners = {
        "src/ai_market_monitor/core/dashboard_paths.py",
        # The one script that has no import of its own: it draws links inside strings.
        "src/ai_market_monitor/static/dashboard.js",
    }
    offenders = []
    for path in _shipped_files():
        name = path.relative_to(ROOT).as_posix()
        if name in owners:
            continue
        for line in _read(path).splitlines():
            if f"{MONITOR_PATH}?monitor=" in line:
                offenders.append(f"{name}: {line.strip()[:100]}")
    assert offenders == []


# ── 4. Every confirmation is readable ────────────────────────────────────────


@pytest.mark.parametrize("key", sorted(_confirmation_keys()))
def test_every_confirmation_a_dashboard_page_receives_has_plain_words(key: str) -> None:
    words = _plain_words()
    assert key in words, f"{key} would be shown to a beginner as its own internal name"
    sentence = words[key]
    assert sentence[0].isupper() and sentence.endswith("."), sentence
    assert "_" not in sentence
