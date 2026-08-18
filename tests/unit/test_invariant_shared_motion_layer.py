"""The shared motion layer talks to the library it actually ships.

`hm-motion.js` is the one owner of duration, easing and the reduced-motion decision for
every redesigned page. Two things in it were wrong in the same way — written against the
older Motion One API, silently ignored by the vendored Motion 11 bundle, and reported by
nothing because neither one throws:

* every animation passed `easing`, which Motion 11 does not read, so the brand curve was
  never applied anywhere despite this file claiming CSS and script could not drift;
* `countTo` passed a callback as the first argument to `animate`, which Motion 11 does
  nothing with, so every counted number in the product sat frozen at zero.

The browser suite proves the animations now move. These tests stop the *shape* of the
mistake coming back, and they are cheap enough to run on every commit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor" / "static"
MOTION = STATIC / "hm-motion.js"


def _source() -> str:
    return MOTION.read_text(encoding="utf-8")


def _code() -> str:
    """The file with its comments removed, so prose about a bug is not read as the bug."""

    without_block = re.sub(r"/\*.*?\*/", "", _source(), flags=re.S)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.M)


def test_no_animation_passes_an_option_the_bundle_ignores() -> None:
    """`easing:` is the Motion One name. Motion 11 reads `ease:` and ignores the rest."""

    assert "easing:" not in _code(), (
        "hm-motion.js passes `easing:` to the vendored bundle, which does not read it. "
        "The brand curve is then silently dropped. Use `ease:` through `withEase()`."
    )


def test_the_easing_name_is_written_once() -> None:
    """One place decides what the option is called, so it cannot be got wrong twice."""

    assert 'EASE_OPTION = "ease"' in _code()
    # Every animator goes through the helper rather than building options itself.
    assert _code().count("withEase(") >= 6


def test_counting_uses_a_call_shape_the_bundle_supports() -> None:
    """`animate(from, to, { onUpdate })`, never `animate(callback, options)`.

    The callback form belongs to an API this product does not ship. It does not throw,
    so a page using it looks finished and shows a frozen number for ever.
    """

    body = _code().split("export function countTo", 1)
    assert len(body) == 2, "countTo is no longer exported from the shared motion layer"
    countTo = body[1].split("\nexport ", 1)[0]

    assert "onUpdate" in countTo, "countTo does not drive the value through `onUpdate`"
    assert not re.search(r"motionAnimate\(\s*\(", countTo), (
        "countTo passes a function as the first argument to `animate`. The vendored "
        "bundle accepts an element, a number pair or a motion value there, and does "
        "nothing at all with a function."
    )


def test_the_final_value_is_written_rather_than_left_to_a_frame() -> None:
    """A person reads the number that was asked for, not the frame it stopped on."""

    assert "finished" in _code().split("export function countTo", 1)[1].split("\nexport ", 1)[0]


@pytest.mark.parametrize(
    "animator", ["animate", "settleIn", "reveal", "dismiss", "countTo", "drawPath", "attention"]
)
def test_every_animator_still_answers_reduced_motion(animator: str) -> None:
    """Rule D6, checked at the layer that owns it rather than at each page.

    `animate` and `settleIn` and `countTo` each decide for themselves; the rest are
    built on `animate`, so they inherit it. Either way the name has to appear.
    """

    source = _code()
    start = source.index(f"export function {animator}")
    body = source[start:].split("\nexport ", 1)[0]
    inherits = "animate(" in body and animator != "animate"
    assert "prefersReducedMotion" in body or inherits, (
        f"{animator} neither checks reduced motion nor goes through something that does"
    )
