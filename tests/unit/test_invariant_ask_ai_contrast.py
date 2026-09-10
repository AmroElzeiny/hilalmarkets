"""Contrast for the Ask AI button colour family, measured in one place.

The contract gives exact pairs and ratios. This file proves them with the repository's
single contrast owner, `tests/support/contrast.py`, rather than asserting by eye.

Two assertions run per pair, and each guards a different promise:

* the exact measured ratio — the visual contract's number, so a silent colour edit
  is caught even when the new value is still readable;
* the WCAG floor — the rule the exact number exists to satisfy (R5): 4.5:1 for text,
  3.0:1 for the non-text pair (the button fill against the page, WCAG 1.4.11). The
  floor holds if the contract number is ever deliberately renegotiated upward; the
  exact assertion always stays at least as strict, never weaker.
"""

from __future__ import annotations

import pytest

from tests.support.contrast import contrast

#: The WCAG AA floor for normal text (1.4.3).
TEXT_FLOOR = 4.5

#: The WCAG AA floor for non-text content — a UI component or graphical object
#: against its own background (1.4.11). The button fill on the page canvas is this,
#: not text.
NON_TEXT_FLOOR = 3.0


@pytest.mark.parametrize(
    ("foreground", "background", "expected", "wcag_floor", "role"),
    [
        pytest.param(
            "#ffffff",
            "#0e78af",
            4.86,
            TEXT_FLOOR,
            "white text on the Ask AI button fill (--hm-sky)",
            id="white_on_sky",
        ),
        pytest.param(
            "#0e78af",
            "#f5f8fb",
            4.56,
            NON_TEXT_FLOOR,
            "the Ask AI button fill on the canvas (--hm-canvas) — non-text",
            id="sky_on_canvas",
        ),
        pytest.param(
            "#ffffff",
            "#0a6086",
            6.93,
            TEXT_FLOOR,
            "white text on the Ask AI hover/active fill (--hm-sky-strong)",
            id="white_on_sky_strong",
        ),
        pytest.param(
            "#2b2e35",
            "#e6f4fb",
            12.10,
            TEXT_FLOOR,
            "ink text on the Ask AI soft tint (--hm-sky-soft)",
            id="ink_on_sky_soft",
        ),
    ],
)
def test_ask_ai_colour_contrast_reproduces_the_contract(
    foreground: str, background: str, expected: float, wcag_floor: float, role: str
) -> None:
    """Each measured pair must reproduce exactly and sit above its WCAG floor."""

    ratio = contrast(foreground, background)
    assert round(ratio, 2) == expected, (
        f"{role} measured {ratio:.2f}:1, expected {expected}:1"
    )
    assert ratio >= wcag_floor, (
        f"{role} measured {ratio:.2f}:1, below the WCAG floor {wcag_floor}:1"
    )
