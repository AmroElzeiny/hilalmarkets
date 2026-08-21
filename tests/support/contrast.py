"""How readable one colour is on another, measured in exactly one place.

Four test modules had each written this sum out for themselves, and they had already
drifted apart — which is the failure this repository keeps repeating, this time inside
the tests that exist to catch it:

* ``test_invariant_email_header`` divided the front colour by the back one **without
  sorting them**. That is the real WCAG ratio only when the front colour is the lighter
  of the two. Called the other way round — dark text on a light card, which is what most
  of this product is — it returned a number below 1, and the check would have failed for
  a colour pair that is perfectly readable. Both of its call sites happened to pass the
  light colour first, so the mistake never showed.
* ``test_invariant_public_page_accessibility`` used ``0.03928`` as the point where the
  sRGB curve changes, and the other two used ``0.04045``. ``0.04045`` is the value in the
  WCAG errata and the one used here. The difference is tiny, but two guards answering
  slightly differently about the same colour pair is how a page ends up passing one
  check and failing the other.

One owner, so a colour pair gets one answer.
"""

from __future__ import annotations

__all__ = ["contrast", "flatten", "luminance"]

#: Where the sRGB transfer curve stops being a straight line. WCAG errata value.
_LINEAR_LIMIT = 0.04045


def _channel(value: int) -> float:
    """One 0–255 colour channel as light, rather than as a stored number."""

    part = value / 255.0
    return part / 12.92 if part <= _LINEAR_LIMIT else ((part + 0.055) / 1.055) ** 2.4


def _parts(colour: str) -> tuple[int, int, int]:
    raw = colour.lstrip("#")
    if len(raw) != 6:
        raise ValueError(f"{colour!r} is not a six-digit hex colour")
    return tuple(int(raw[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def luminance(colour: str) -> float:
    """How much light a colour gives off, 0 for black and 1 for white."""

    red, green, blue = _parts(colour)
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast(first: str, second: str) -> float:
    """The WCAG ratio between two colours, from 1 (identical) to 21 (black on white).

    The order of the two arguments does not matter. That is deliberate: a caller should
    never have to know which of its colours is the lighter one to get a true answer.
    """

    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def flatten(colour: str, alpha: float, behind: str) -> str:
    """A translucent colour as the eye actually sees it, over its background."""

    over = _parts(colour)
    under = _parts(behind)
    return "#" + "".join(
        f"{round(top * alpha + bottom * (1 - alpha)):02x}"
        for top, bottom in zip(over, under, strict=True)
    )
