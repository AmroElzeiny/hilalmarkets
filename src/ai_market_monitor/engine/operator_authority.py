"""The one place that decides which comparison a trader's own words state.

``engine/comparators.py`` owns the vocabulary — which phrase means which
:class:`Comparator`, and which operator governs a value at a given position. This
module owns the *decision* built on top of it: given the exact span the trader wrote
and a comparator a model proposed for it, which one is authoritative.

The answer is always the trader's words. A model that returns ``lt`` for an exact
``at most`` phrase is corrected deterministically, before any canonical operation
exists, and the correction is recorded as ``DETERMINISTIC_OPERATOR_NORMALIZATION``.
That is not a paid model repair: no trader-controlled choice is still ambiguous, so
asking a model again could only agree or be wrong.

Evaluator run 20260803T000036Z is what this exists to stop. A trader wrote
``a bearish close-to-close move of at most 1%`` and, after five refusals, restated it
until the sentence happened to contain wording that compiled. What compiled was
``lt 1%`` — a strictly-below rule where an inclusive ceiling was asked for. The
monitor would then stay silent on the exact 1% move the trader wanted to see.

Three rules this module keeps:

* **Never invert.** ``at most`` is a ceiling. It is never expressed by negating the
  threshold or flipping the comparator onto the other side of the value.
* **Never guess.** When the words state no comparison, nothing is normalized and the
  model's own reading stands or is refused elsewhere; this module does not invent one.
* **Never resolve an ambiguity.** Two occurrences of the same number governed by two
  different comparisons is a question for the trader, not a coin flip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ai_market_monitor.engine.comparators import (
    LOWER_BOUND_COMPARATORS,
    OPERATOR_TERMS,
    UPPER_BOUND_COMPARATORS,
    find_comparator_for_value,
)
from ai_market_monitor.schemas.strategy import Comparator

__all__ = [
    "OPERATOR_PHRASE_AUTHORITY",
    "OperatorNormalization",
    "OperatorNormalizationKind",
    "OperatorReading",
    "comparator_is_inclusive",
    "comparator_label",
    "normalize_stated_comparator",
    "operator_phrases_for",
    "stated_comparator_for_threshold",
]


class OperatorNormalizationKind(StrEnum):
    """What the authority decided about one proposed comparator."""

    #: The words state no comparison, so nothing was decided here.
    NOT_STATED = "NOT_STATED"
    #: The words state exactly what the model proposed.
    AGREES = "AGREES"
    #: The words state something else. The words win.
    DETERMINISTIC_OPERATOR_NORMALIZATION = "DETERMINISTIC_OPERATOR_NORMALIZATION"
    #: The same value is governed by two different comparisons in the same span.
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class OperatorReading:
    """The comparison the trader's own words put on one value."""

    comparator: Comparator
    #: The exact substring that states it, for evidence the user can recognise.
    phrase: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class OperatorNormalization:
    """The authority's verdict on one proposed comparator."""

    kind: OperatorNormalizationKind
    proposed: Comparator | None
    #: What the words state. ``None`` when they state nothing, or are ambiguous.
    authoritative: Comparator | None
    reading: OperatorReading | None = None
    #: Every distinct comparison the words put on this value; >1 means ambiguous.
    competing: tuple[Comparator, ...] = ()

    @property
    def corrected(self) -> bool:
        return self.kind == OperatorNormalizationKind.DETERMINISTIC_OPERATOR_NORMALIZATION

    @property
    def resolved(self) -> Comparator | None:
        """The comparator to compile, or ``None`` when the caller must fail closed."""

        if self.kind == OperatorNormalizationKind.AMBIGUOUS:
            return None
        return self.authoritative or self.proposed

    def trace(self) -> str:
        """One safe line for the turn's operator trace."""

        phrase = self.reading.phrase if self.reading else ""
        return (
            f"{self.kind.value}:proposed={self.proposed.value if self.proposed else 'none'}"
            f":stated={self.authoritative.value if self.authoritative else 'none'}"
            f":phrase={phrase!r}"
        )


#: The phrase → comparator table this product promises, grouped for humans. It is
#: built from ``OPERATOR_TERMS`` so a phrase can never be documented here, shown in
#: the UI, or normalized by the evaluator while the compiler understands something
#: else. Adding a phrase in one place adds it everywhere.
OPERATOR_PHRASE_AUTHORITY: Final[dict[Comparator, tuple[str, ...]]] = {
    comparator: tuple(term for term, value in OPERATOR_TERMS if value == comparator)
    for comparator in Comparator
}


#: Plain-language labels. A beginner does not know what ``lte`` means, and the phrase
#: shown next to a rule has to say the same thing the compiler enforces.
_COMPARATOR_LABELS: Final[dict[Comparator, str]] = {
    Comparator.GREATER_THAN_OR_EQUAL: "at least",
    Comparator.GREATER_THAN: "above",
    Comparator.LESS_THAN_OR_EQUAL: "at most",
    Comparator.LESS_THAN: "below",
    Comparator.EQUAL: "exactly",
    Comparator.CROSSES_ABOVE: "crosses above",
    Comparator.CROSSES_BELOW: "crosses below",
    Comparator.IS_TRUE: "is true",
    Comparator.IS_FALSE: "is false",
}

#: Comparisons that include the value itself. ``at most 1%`` fires on exactly 1%;
#: ``below 1%`` does not. Losing this distinction is a silent change of meaning.
_INCLUSIVE: Final[frozenset[Comparator]] = frozenset(
    {
        Comparator.GREATER_THAN_OR_EQUAL,
        Comparator.LESS_THAN_OR_EQUAL,
        Comparator.EQUAL,
    }
)


def comparator_label(comparator: Comparator) -> str:
    """The plain-language wording for one comparator, for UI and replies."""

    return _COMPARATOR_LABELS.get(comparator, comparator.value)


def comparator_is_inclusive(comparator: Comparator) -> bool:
    """Whether the comparison includes the stated value itself."""

    return comparator in _INCLUSIVE


def operator_phrases_for(comparator: Comparator) -> tuple[str, ...]:
    """Every phrase that states this comparison, longest first."""

    return OPERATOR_PHRASE_AUTHORITY.get(comparator, ())


def _number_spans(text: str, value: float) -> list[tuple[int, int]]:
    """Every place ``text`` writes this exact number.

    ``2.5``, ``2.50`` and ``+2.5`` are the same value; ``25`` and ``2`` are not, so
    the match is on the parsed number rather than on the rendered string. This is why
    the caller can pass a float from a typed intent and still find the trader's digits.
    """

    spans: list[tuple[int, int]] = []
    for match in re.finditer(r"[-+]?\d+(?:\.\d+)?", text or ""):
        try:
            if float(match.group(0)) == value:
                spans.append((match.start(), match.end()))
        except ValueError:  # pragma: no cover - the pattern only matches numbers
            continue
    return spans


def stated_comparator_for_threshold(text: str, threshold: float) -> OperatorReading | None:
    """The comparison the words put on ``threshold``, or ``None``.

    Every occurrence of the number is resolved with the shared nearest-operator rule,
    so a comparison from a neighbouring clause can never claim this value. When two
    occurrences disagree the reading is ambiguous and ``None`` is returned; use
    :func:`normalize_stated_comparator` to tell that apart from "no operator stated".
    """

    readings = _all_readings(text, threshold)
    distinct = {reading.comparator for reading in readings}
    if len(distinct) != 1:
        return None
    return readings[0]


def _all_readings(text: str, threshold: float) -> list[OperatorReading]:
    readings: list[OperatorReading] = []
    for start, end in _number_spans(text, threshold):
        found = find_comparator_for_value(text, start, end)
        if found is None:
            continue
        comparator, phrase_start, phrase_end = found
        readings.append(
            OperatorReading(
                comparator=comparator,
                phrase=(text or "")[phrase_start:phrase_end],
                start=phrase_start,
                end=phrase_end,
            )
        )
    return readings


def normalize_stated_comparator(
    text: str,
    *,
    threshold: float | None,
    proposed: Comparator | None,
) -> OperatorNormalization:
    """Decide the comparator for one value, with the trader's words as authority.

    ``proposed`` is what the model returned. It is kept only when the words state
    nothing, or state the same thing. When the words state something else the words
    win and the difference is reported so the turn can record it.

    Bounded-direction comparisons (``crosses above``) are never normalized into or out
    of a plain threshold comparison: crossing a level and sitting above it are
    different mechanics, and swapping them would monitor a different event.
    """

    if threshold is None:
        return OperatorNormalization(
            kind=OperatorNormalizationKind.NOT_STATED,
            proposed=proposed,
            authoritative=None,
        )
    readings = _all_readings(text, threshold)
    distinct = tuple(dict.fromkeys(reading.comparator for reading in readings))
    if not distinct:
        return OperatorNormalization(
            kind=OperatorNormalizationKind.NOT_STATED,
            proposed=proposed,
            authoritative=None,
        )
    if len(distinct) > 1:
        return OperatorNormalization(
            kind=OperatorNormalizationKind.AMBIGUOUS,
            proposed=proposed,
            authoritative=None,
            competing=distinct,
        )
    stated = readings[0]
    if proposed is not None and _different_mechanic(proposed, stated.comparator):
        # A cross and a threshold are not two readings of one comparison. Silently
        # converting between them would replace the event the trader asked about.
        return OperatorNormalization(
            kind=OperatorNormalizationKind.NOT_STATED,
            proposed=proposed,
            authoritative=None,
            reading=stated,
        )
    if proposed == stated.comparator:
        return OperatorNormalization(
            kind=OperatorNormalizationKind.AGREES,
            proposed=proposed,
            authoritative=stated.comparator,
            reading=stated,
        )
    return OperatorNormalization(
        kind=OperatorNormalizationKind.DETERMINISTIC_OPERATOR_NORMALIZATION,
        proposed=proposed,
        authoritative=stated.comparator,
        reading=stated,
    )


_THRESHOLD_COMPARATORS: Final[frozenset[Comparator]] = (
    UPPER_BOUND_COMPARATORS | LOWER_BOUND_COMPARATORS | frozenset({Comparator.EQUAL})
)


def _different_mechanic(proposed: Comparator, stated: Comparator) -> bool:
    """True when the two comparators are not two readings of the same comparison."""

    return (proposed in _THRESHOLD_COMPARATORS) is not (stated in _THRESHOLD_COMPARATORS)
