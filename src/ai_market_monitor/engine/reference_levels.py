"""The price levels a rule can be compared against, named and read in one place.

A rule like "tell me when the price breaks above the last candle's high" has two halves:
the price now, and **the level it is compared with**. The level is not a pattern and not
an indicator — it is one number read off the candles.

Three screens produced such a level and each wrote the name itself:

* the Guided Builder and the monitor canvas wrote ``previous_candle_high`` and
  ``lookback_high``;
* the typed-message reader wrote ``previous_candle_price`` — which is not even a candle
  reading;
* **the runtime knew none of them.**

The compiler files these operands under "price action", so an unknown name fell through
to ``raise KeyError("Unsupported price action: previous_candle_high")``. The condition
became an error, the whole evaluation became an error, and three of the ten cards a
person can put on a board could never fire — on any coin, on any candle.

So the names live here, next to the readings they stand for. A producer asks for the
name; the runtime asks for the number. Neither writes its own version, and a reading
this module cannot make is refused rather than guessed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from ai_market_monitor.engine.indicators import IndicatorWarmupError
from ai_market_monitor.services.interfaces import Candle

#: The readings a candle actually has.
REFERENCE_FIELDS: Final[tuple[str, ...]] = ("open", "high", "low", "close")

#: Words people and earlier readers use for a reading, and the field each one means.
#:
#: "the previous candle's price" is the previous candle's **close** — that is what a
#: price *was* when a candle ended. The typed-message reader already accepted the word
#: and built ``previous_candle_price`` out of it, which no reader could evaluate.
_FIELD_ALIASES: Final[dict[str, str]] = {
    "price": "close",
    "closing": "close",
    "closing_price": "close",
    "opening": "open",
    "opening_price": "open",
    "highest": "high",
    "highest_price": "high",
    "lowest": "low",
    "lowest_price": "low",
    "swing_high": "high",
    "swing_low": "low",
}

#: Only the top and the bottom of a stretch of candles have one obvious meaning.
#:
#: "The open of the last twenty candles" does not: it could be where the stretch began
#: or where each candle began. Refused rather than picked, because a level nobody can
#: state exactly is a rule that watches something the person did not ask for.
LOOKBACK_FIELDS: Final[tuple[str, ...]] = ("high", "low")

_PREVIOUS_CANDLE_PREFIX: Final = "previous_candle_"
_LOOKBACK_PREFIX: Final = "lookback_"


def reference_field(word: str | None) -> str | None:
    """The candle reading a word names, or ``None`` when it names none."""

    if not word:
        return None
    cleaned = str(word).strip().casefold().replace(" ", "_").replace("-", "_")
    if cleaned in REFERENCE_FIELDS:
        return cleaned
    return _FIELD_ALIASES.get(cleaned)


def previous_candle_level_name(field: str | None) -> str:
    """The name for "this reading, on the candle before this one"."""

    resolved = reference_field(field)
    if resolved is None:
        raise ValueError(f"not a candle reading: {field!r}")
    return f"{_PREVIOUS_CANDLE_PREFIX}{resolved}"


def lookback_level_name(field: str | None) -> str:
    """The name for "the top or the bottom of the last N candles"."""

    resolved = reference_field(field)
    if resolved not in LOOKBACK_FIELDS:
        raise ValueError(f"a stretch of candles has no single {field!r}")
    return f"{_LOOKBACK_PREFIX}{resolved}"


#: Every level name the runtime can read. Built from the readings, never listed by hand.
REFERENCE_LEVEL_NAMES: Final[frozenset[str]] = frozenset(
    {
        *(f"{_PREVIOUS_CANDLE_PREFIX}{field}" for field in REFERENCE_FIELDS),
        *(f"{_LOOKBACK_PREFIX}{field}" for field in LOOKBACK_FIELDS),
    }
)


def supports_reference_level(name: str | None) -> bool:
    """Whether this operand names a level this module can read."""

    return bool(name) and name in REFERENCE_LEVEL_NAMES


def evaluate_reference_level(
    name: str,
    candles: list[Candle],
    parameters: Mapping[str, Any] | None = None,
) -> float:
    """The number this level stands for, on this history.

    Both readings deliberately **exclude the candle being judged**. "Breaks above the
    last candle's high" is about the candle before; "passes the highest point of the
    last twenty" is about the twenty behind it. Including the current candle would make
    the second one impossible to satisfy — a close can never exceed its own high — and
    the rule would simply never fire.
    """

    parameters = parameters or {}
    if name.startswith(_PREVIOUS_CANDLE_PREFIX):
        field = name[len(_PREVIOUS_CANDLE_PREFIX) :]
        if field not in REFERENCE_FIELDS:
            raise KeyError(f"Unsupported reference level: {name}")
        if len(candles) < 2:
            raise IndicatorWarmupError(f"{name} needs the candle before this one")
        return float(getattr(candles[-2], field))
    if name.startswith(_LOOKBACK_PREFIX):
        field = name[len(_LOOKBACK_PREFIX) :]
        if field not in LOOKBACK_FIELDS:
            raise KeyError(f"Unsupported reference level: {name}")
        raw = parameters.get("lookback", 20)
        try:
            lookback = int(raw)
        except (TypeError, ValueError) as exc:
            raise IndicatorWarmupError(f"{name} needs a whole number of candles") from exc
        if lookback < 1:
            raise IndicatorWarmupError(f"{name} needs at least one candle to look back over")
        if len(candles) < lookback + 1:
            raise IndicatorWarmupError(f"{name} requires {lookback + 1} candles")
        window = candles[-lookback - 1 : -1]
        if field == "high":
            return max(float(candle.high) for candle in window)
        return min(float(candle.low) for candle in window)
    raise KeyError(f"Unsupported reference level: {name}")
