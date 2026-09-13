"""Invariant: every round assistant button in a corner wears the same "Ask AI" label.

Three families of page draw a round assistant button in the bottom-right corner - the
public site with the landing page, the signed-in dashboard, and the System Brain. Each
one carries a small sky-blue label reading "Ask AI" just above the circle. One rule in
``static/hm-ask-tag.css`` draws it for all three.

These tests hold the whole family together, not one page:

* every corner assistant carries the label, once, inside its own button;
* every page frame that draws one of those buttons loads the label's sheet;
* the button's spoken name starts with the words the label shows (WCAG 2.5.3), and no
  script renames it back to a name without them after the window closes;
* the label is a rounded rectangle, never a pill, in the Ask AI sky blue;
* it breathes, it stops for a pointer or the keyboard, and it never moves for anybody
  who asked for less motion;
* the sky-blue tokens say the same thing in both files that declare them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.support.contrast import contrast

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "ai_market_monitor"
STATIC = SRC / "static"
TEMPLATES = SRC / "templates"
TAG_CSS = STATIC / "hm-ask-tag.css"
BRAND_CSS = STATIC / "hilalmarkets-brand.css"
PUBLIC_TOKENS_CSS = STATIC / "hilalmarkets.css"
TAG = '<span class="hm-ask-tag" aria-hidden="true">Ask AI</span>'

#: Every corner assistant: the partial that draws its button, the button's class, and
#: the script that runs it.
HOSTS: dict[str, tuple[Path, str, Path]] = {
    "public site and landing page": (
        TEMPLATES / "hilal" / "partials" / "public_chat.html",
        "public-chat-launcher",
        STATIC / "hilalmarkets-public-chat.js",
    ),
    "dashboard": (
        TEMPLATES / "hilal" / "dashboard_test" / "partials" / "hilal_chat.html",
        "hilal-orb",
        STATIC / "hm-hilal-chat.js",
    ),
    "System Brain": (
        TEMPLATES / "system_brain_agent_dock.html",
        "brain-agent-orb",
        STATIC / "system-brain-agent.js",
    ),
}


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _host_button(partial: Path, css_class: str) -> str:
    """The markup of the host button, from its opening tag to its closing one."""

    source = _text(partial)
    found = re.search(
        rf'<button class="{re.escape(css_class)}"(.*?)</button>', source, re.DOTALL
    )
    assert found, f"{partial.name} no longer draws a button with class {css_class}"
    return found.group(0)


def _frames_including(partial: Path) -> list[Path]:
    """Every template that includes this partial, read from the templates themselves."""

    relative = partial.relative_to(TEMPLATES).as_posix()
    pattern = re.compile(r"{%-?\s*include\s+['\"]" + re.escape(relative) + r"['\"]")
    return sorted(
        path for path in TEMPLATES.rglob("*.html") if pattern.search(_text(path))
    )


def _rule(selector_tail: str) -> str:
    """The body of the first rule whose selector ends in ``selector_tail``."""

    css = _text(TAG_CSS)
    found = re.search(rf"{re.escape(selector_tail)}\s*\{{([^}}]*)\}}", css)
    assert found, f"hm-ask-tag.css has no rule ending in {selector_tail!r}"
    return found.group(1)


def _tokens(path: Path) -> dict[str, str]:
    return {
        name: value.lower()
        for name, value in re.findall(
            r"(--hm-sky(?:-strong|-soft)?)\s*:\s*(#[0-9a-fA-F]{6})\s*;", _text(path)
        )
    }


@pytest.mark.parametrize("surface", sorted(HOSTS))
def test_every_corner_assistant_carries_the_label_inside_its_own_button(
    surface: str,
) -> None:
    partial, css_class, _script = HOSTS[surface]
    assert _text(partial).count(TAG) == 1, f"{surface}: the label is missing or doubled"
    assert TAG in _host_button(partial, css_class), (
        f"{surface}: the label is not inside the round button, so it would not move "
        "with it and a click on it would not open the assistant"
    )


@pytest.mark.parametrize("surface", sorted(HOSTS))
def test_every_frame_that_draws_a_corner_assistant_loads_the_label_sheet(
    surface: str,
) -> None:
    partial, _css_class, _script = HOSTS[surface]
    frames = _frames_including(partial)
    assert frames, f"{surface}: no page includes {partial.name}"
    for frame in frames:
        assert "hm-ask-tag.css" in _text(frame), (
            f"{frame.name} draws the {surface} assistant but never loads the label's "
            "sheet, so the label would render as bare text inside the circle"
        )


@pytest.mark.parametrize("surface", sorted(HOSTS))
def test_the_button_name_starts_with_the_words_it_shows(surface: str) -> None:
    partial, css_class, _script = HOSTS[surface]
    name = re.search(r'aria-label="([^"]*)"', _host_button(partial, css_class))
    assert name, f"{surface}: the button has no accessible name"
    assert name.group(1).startswith("Ask AI"), (
        f"{surface}: the name {name.group(1)!r} does not start with 'Ask AI', the "
        "words the label shows (WCAG 2.5.3)"
    )


@pytest.mark.parametrize("surface", sorted(HOSTS))
def test_no_script_gives_the_shut_button_a_name_without_the_label(surface: str) -> None:
    """The dashboard's script used to reset the name to a second, shorter copy.

    After the first close the button was called "Open Hilal, your Hilal Markets
    assistant" - no "Ask AI", and no mention that it sees the page. A name written into
    a script as "Open ..." is always a second copy of the template's name.
    """

    _partial, _css_class, script = HOSTS[surface]
    literals = re.findall(r'setAttribute\(\s*"aria-label"\s*,\s*"([^"]*)"', _text(script))
    stale = [literal for literal in literals if literal.startswith("Open")]
    assert not stale, f"{script.name} still writes its own shut-button name: {stale}"


def test_the_sheet_names_exactly_the_corner_assistants() -> None:
    lists = re.findall(r":is\(([^)]*)\)\s*(?:\[[^\]]*\]|:is\([^)]*\))?\s*>", _text(TAG_CSS))
    assert lists, "hm-ask-tag.css does not name the buttons it styles"
    wanted = {f".{css_class}" for _partial, css_class, _script in HOSTS.values()}
    for listed in lists:
        assert {item.strip() for item in listed.split(",")} == wanted, listed


def test_the_label_is_a_rounded_rectangle_not_a_pill() -> None:
    body = _rule("> .hm-ask-tag")
    radius = re.search(r"border-radius:\s*(\d+)px", body)
    height = re.search(r"\bheight:\s*(\d+)px", body)
    assert radius and height, "the label needs a pixel radius and a pixel height"
    assert 4 <= int(radius.group(1)) <= 12, (
        "curved corners, but a rectangle: the radius must stay well under half the height"
    )
    assert int(radius.group(1)) * 2 < int(height.group(1))
    assert "999px" not in body and "50%" not in body.split("left", 1)[0]


def test_the_label_is_the_ask_ai_sky_blue_with_white_words() -> None:
    body = _rule("> .hm-ask-tag")
    assert "background: var(--hm-sky)" in body
    assert "color: var(--white)" in body
    hover = _rule(":is(:hover, :focus-visible) > .hm-ask-tag")
    assert "var(--hm-sky-strong)" in hover
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", _text(TAG_CSS)), (
        "the label writes a colour of its own instead of the token"
    )
    sky = _tokens(BRAND_CSS)
    for token in ("--hm-sky", "--hm-sky-strong"):
        ratio = contrast("#ffffff", sky[token])
        assert ratio >= 4.5, f"white on {token} measures {ratio:.2f}:1"


def test_the_label_breathes_and_can_be_stopped() -> None:
    css = _text(TAG_CSS)
    body = _rule("> .hm-ask-tag")
    assert re.search(r"animation:\s*hm-ask-tag-breathe\s+\d+ms\s+ease-in-out\s+infinite", body)
    keyframes = re.search(r"@keyframes hm-ask-tag-breathe\s*\{(.*?)\n\}", css, re.DOTALL)
    assert keyframes and "scale: 1;" in keyframes.group(1)
    assert re.search(r"scale:\s*1\.\d+", keyframes.group(1)), "it has to scale up and back"
    # It moves `scale` only, so the centring `translate` is never overwritten.
    assert "transform" not in keyframes.group(1)

    hover = _rule(":is(:hover, :focus-visible) > .hm-ask-tag")
    assert "animation-play-state: paused" in hover

    reduced = css.split("prefers-reduced-motion: reduce", 1)
    assert len(reduced) == 2, "no reduced-motion rule"
    assert "animation: none" in reduced[1].split("@media", 1)[0]


def test_the_label_goes_away_once_the_window_is_open() -> None:
    assert "display: none" in _rule('[aria-expanded="true"] > .hm-ask-tag')


def test_the_sky_tokens_say_the_same_thing_in_both_files() -> None:
    """The public pages never load the brand file, so they carry a copy of the tokens."""

    brand = _tokens(BRAND_CSS)
    public = _tokens(PUBLIC_TOKENS_CSS)
    assert set(brand) == {"--hm-sky", "--hm-sky-strong", "--hm-sky-soft"}, brand
    assert public == brand, f"the public copy {public} drifted from the brand file {brand}"
