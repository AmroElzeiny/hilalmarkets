"""Grounding: the rule that lets a model read wording without inventing meaning.

Deterministic parsing resolves most wording, but not all of it, and the gap is real:
a trader can phrase a move in words no table anticipates. The tempting fix is to let
a model decide, and that is exactly how ``weekly_high_low`` — a condition nobody
asked for — reached a compiled strategy at 0.78 confidence.

The safe form of an AI call is narrow, and this module states it:

* the model may only **fill fields of a type the compiler already has**. It never
  names a capability, never invents a mechanic, and never returns free text that
  becomes a rule.
* every value it fills must be **grounded**: the number, the comparison and the
  direction it claims must each be findable in the trader's own words.
* anything ungrounded is **refused**, not softened into a default.

Grounding is what makes the difference between reading and guessing. A model that
reads ``a bearish move of at least 7.5%`` and returns ``down / >= / 7.5`` has claimed
nothing the text does not say, and each claim is checkable here without trusting the
model. A model that returns ``7.5`` for a text containing no ``7.5`` is hallucinating,
and no confidence score it reports can tell us that — only the source text can.

This is deliberately not a confidence threshold. Confidence is the model's opinion of
itself; grounding is a fact about the text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ai_market_monitor.engine.comparators import OPERATOR_TERMS
from ai_market_monitor.engine.price_movement import DOWN_TERMS, UP_TERMS
from ai_market_monitor.schemas.strategy import Comparator

#: Comparators a text may state without naming them. `up 5%` means "at least 5%" by
#: the platform's documented convention, so a model returning `>=` there is reading
#: the convention rather than inventing a bound. Every other comparator must be
#: stated: silently turning an unstated comparison into `<=` reverses the alert.
CONVENTIONAL_COMPARATORS: frozenset[Comparator] = frozenset({Comparator.GREATER_THAN_OR_EQUAL})

_COMPARATOR_PHRASES: dict[Comparator, tuple[str, ...]] = {}
for _term, _comparator in OPERATOR_TERMS:
    _COMPARATOR_PHRASES.setdefault(_comparator, ())
    _COMPARATOR_PHRASES[_comparator] += (_term,)

_SYMBOLIC_PHRASES: dict[Comparator, tuple[str, ...]] = {
    Comparator.GREATER_THAN_OR_EQUAL: (">=", "≥"),
    Comparator.LESS_THAN_OR_EQUAL: ("<=", "≤"),
    Comparator.GREATER_THAN: (">",),
    Comparator.LESS_THAN: ("<",),
    Comparator.EQUAL: ("==", "="),
}


#: Parameter names that hold **the size of a move the trader is choosing**. The
#: capability registry ships an example value for each of these so the builder UI has
#: something to render, and that example must never survive into a compiled rule
#: unless the trader's own words supply it: `alert me on a dump this week` matched the
#: alias `dump` and compiled `threshold_percent: 5`, a size stated nowhere.
#:
#: Deliberately limited to the percent-of-a-move family. A `period` of 14 or a wick
#: `multiple` of 2 is part of what the mechanic *is* — the trader who says "RSI" is
#: asking for the standard definition — whereas "price moves by X percent" has no
#: meaning until X is given. Widening this set would start refusing named mechanics
#: that are fully specified by their own name.
TRADER_CHOSEN_QUANTITIES: frozenset[str] = frozenset(
    {
        "threshold_percent",
        "threshold",
        "percent",
        "percent_threshold",
        "minimum_percent",
        "maximum_percent",
        "min_percent",
        "max_percent",
    }
)


def ungrounded_quantities(parameters: dict[str, object], source: str) -> tuple[str, ...]:
    """Move sizes in ``parameters`` that ``source`` does not state.

    The same bar the model-filled path has to clear, applied to values the
    deterministic path takes from the capability catalogue. There is one rule for what
    may enter a compiled strategy, and "the registry shipped a default" is no more a
    way over it than "a model said so".
    """
    violations: list[str] = []
    for name, value in parameters.items():
        if name not in TRADER_CHOSEN_QUANTITIES:
            continue
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        if not number_is_grounded(float(value), source):
            violations.append(name)
    return tuple(violations)


@dataclass(frozen=True, slots=True)
class GroundingReport:
    """Which claimed values the source text actually supports."""

    violations: tuple[str, ...] = field(default_factory=tuple)
    #: Values the platform's documented convention supplied rather than the trader.
    conventions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def grounded(self) -> bool:
        return not self.violations


def _normalized(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def number_is_grounded(value: float, source: str) -> bool:
    """True when ``value`` appears in ``source`` as a number the trader wrote.

    ``7.5`` matches ``7.5``, ``7.50`` and ``+7.5``; it does not match the ``7`` in
    ``7 days``. Numeric equality is used rather than string matching so formatting
    differences do not masquerade as hallucinations.
    """
    for match in re.finditer(r"[-+]?\d+(?:\.\d+)?", source or ""):
        try:
            candidate = float(match.group(0))
        except ValueError:  # pragma: no cover - the pattern guarantees a number
            continue
        if abs(abs(candidate) - abs(value)) < 1e-9:
            return True
    return False


def comparator_is_grounded(comparator: Comparator, source: str) -> bool:
    """True when the source states this comparison in words or symbols."""
    lowered = _normalized(source)
    for phrase in _COMPARATOR_PHRASES.get(comparator, ()):
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", lowered):
            return True
    return any(symbol in lowered for symbol in _SYMBOLIC_PHRASES.get(comparator, ()))


def direction_is_grounded(direction: str, source: str) -> bool:
    """True when the source states this direction of movement."""
    lowered = _normalized(source)
    terms = UP_TERMS if direction == "up" else DOWN_TERMS if direction == "down" else ()
    if not terms:
        # `signed` claims no side, so there is nothing to ground.
        return direction == "signed"
    return any(re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", lowered) for term in terms)


def verify_grounding(
    source: str,
    *,
    threshold: float | None = None,
    comparator: Comparator | None = None,
    direction: str | None = None,
    timeframe: str | None = None,
) -> GroundingReport:
    """Check every claimed value against the trader's own words.

    Returns the claims the text does not support. A caller with any violation must
    refuse the reading and ask, rather than compile it: an ungrounded threshold or
    comparator is a monitor that fires on something the trader never described.
    """
    violations: list[str] = []
    conventions: list[str] = []

    if threshold is not None and not number_is_grounded(threshold, source):
        violations.append(f"threshold {threshold:g} does not appear in the request")

    if comparator is not None and not comparator_is_grounded(comparator, source):
        if comparator in CONVENTIONAL_COMPARATORS:
            conventions.append(
                f"comparison '{comparator.value}' follows the platform convention for "
                "a stated move; the request did not name it"
            )
        else:
            violations.append(f"comparison '{comparator.value}' does not appear in the request")

    if direction is not None and not direction_is_grounded(direction, source):
        violations.append(f"direction '{direction}' does not appear in the request")

    if timeframe is not None and timeframe.casefold() not in _normalized(source):
        conventions.append(f"timeframe '{timeframe}' was carried from the setup, not stated here")

    return GroundingReport(violations=tuple(violations), conventions=tuple(conventions))
