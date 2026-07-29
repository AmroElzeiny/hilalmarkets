"""Read a stated comparison and level out of the clause that owns them.

Every indicator that accepts a user-supplied level had the same three defects, each
of which silently changes what a monitor fires on:

* **operator vocabulary** — only ``above|below|over|under`` were understood, so
  ``at most``, ``no more than`` and ``no less than`` fell through
* **clause scoping** — patterns reached across the whole prompt, so ``above`` in a
  later clause could set the operator for an earlier one
* **invented values** — unrecognised wording fell back to a hardcoded default, which
  turned ``RSI at most 30`` into ``RSI >= 50``: the opposite rule, at a level the
  trader never gave

Fixing those one indicator at a time would leave the next indicator broken, so the
reading lives here once and every caller shares it. The operator vocabulary is the
same tested, longest-phrase-first table the turn classifier uses, so ``at least``
never degrades to ``least`` and ``crosses above`` never degrades to ``above``.

This module never invents a level. When no number is stated it returns ``None`` and
the caller is expected to fail closed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ai_market_monitor.engine.comparators import comparator_terms, find_comparator
from ai_market_monitor.engine.prompt_semantics import SUPPORTED_TIMEFRAMES
from ai_market_monitor.schemas.strategy import Comparator

#: ``plain`` is a bare level (RSI 30), ``percent`` a proportion (2.5%), ``multiple``
#: a ratio of a reference (2x average). The unit changes what the number means, so
#: it is reported rather than guessed at by the caller.
Unit = Literal["plain", "percent", "multiple"]

#: A sentence break, but never the decimal point inside ``29.5``.
_SENTENCE_BREAK_RE = re.compile(r"[;\n]|\.(?!\d)")

#: Words that start a new requirement. The clause for a term ends where the next one
#: begins, which is what stops one indicator's operator leaking into another's.
_CONJUNCTION_RE = re.compile(
    r"\b(?:and|or|but|then|plus|also|while|with|when|whenever|if)\b",
    re.IGNORECASE,
)

#: Every comparison phrase, longest first, so a conjunction sitting *inside* an
#: operator is not mistaken for a clause boundary: `at or below` contains `or`, and
#: splitting there left the clause as `rsi at` with no level in it.
_OPERATOR_SPAN_RE = re.compile(
    "|".join(rf"(?<![a-z]){re.escape(term)}(?![a-z])" for term in comparator_terms()),
    re.IGNORECASE,
)

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
#: `%` is not a word character, so the word boundary must apply only to the spelled
#: forms. Anchoring it after `%` made `1.5%` at the end of a clause read as plain.
_PERCENT_SUFFIX_RE = re.compile(r"\s*(?:%|(?:percent|pct)\b)", re.IGNORECASE)
_MULTIPLE_SUFFIX_RE = re.compile(r"\s*(?:x\b|times\b|multiple\b)", re.IGNORECASE)
_TIMEFRAME_SUFFIX_RE = re.compile(r"(m|h|d)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class LevelReading:
    """A comparison and level that the prompt actually stated."""

    comparator: Comparator
    value: float
    unit: Unit = "plain"
    clause: str = ""
    #: False when the comparator came from a caller-supplied domain convention
    #: rather than from the trader's own words.
    operator_stated: bool = True


def clause_for(text: str, term: str) -> str | None:
    """Return the span of ``text`` that belongs to ``term``.

    Bounded by sentence breaks and by the conjunctions that start the neighbouring
    requirements, so ``RSI below 30 and volume above 2x average`` yields
    ``rsi below 30`` for ``rsi`` and ``volume above 2x average`` for ``volume``.
    """
    if not text or not term:
        return None
    best: str | None = None
    for match in re.finditer(re.escape(term), text, re.IGNORECASE):
        clause = _bounded(text, match.start(), match.end())
        if clause is None:
            continue
        if best is None:
            best = clause
        # Prefer the occurrence whose own clause carries a number; a bare earlier
        # mention should not hide the sentence that states the level.
        if _NUMBER_RE.search(clause):
            return clause
    return best


def read_level(
    text: str,
    term: str,
    *,
    default_comparator: Comparator | None = None,
    require_unit: Unit | None = None,
) -> LevelReading | None:
    """Read ``term``'s stated comparison and level, or ``None``.

    ``default_comparator`` is a documented domain convention for wording that states
    a level without an operator — ``volume 2x average`` means at least 2x. It is an
    explicit per-call decision, never a hidden global fallback, and it can only
    supply the operator: a missing *number* always returns ``None``.

    ``require_unit`` rejects a reading whose unit does not match, so a rule about
    multiples cannot silently consume a percentage.
    """
    clause = clause_for(text, term)
    if clause is None:
        return None

    found = find_comparator(clause)
    if found is not None:
        comparator, _start, search_from = found
        operator_stated = True
    else:
        if default_comparator is None:
            return None
        comparator = default_comparator
        operator_stated = False
        term_match = re.search(re.escape(term), clause, re.IGNORECASE)
        search_from = term_match.end() if term_match else 0

    number = _first_level(clause, search_from)
    if number is None:
        # The operator may sit after the level (`2x volume or more`), so fall back to
        # the whole clause before giving up.
        number = _first_level(clause, 0)
    if number is None:
        return None
    value, unit = number
    if require_unit is not None and unit != require_unit:
        return None
    return LevelReading(
        comparator=comparator,
        value=value,
        unit=unit,
        clause=clause,
        operator_stated=operator_stated,
    )


def _bounded(text: str, start: int, end: int) -> str | None:
    protected = [(m.start(), m.end()) for m in _OPERATOR_SPAN_RE.finditer(text)]

    def is_boundary(match: re.Match[str]) -> bool:
        return not any(
            span_start <= match.start() and match.end() <= span_end
            for span_start, span_end in protected
        )

    left = 0
    for match in _SENTENCE_BREAK_RE.finditer(text, 0, start):
        left = max(left, match.end())
    for match in _CONJUNCTION_RE.finditer(text, 0, start):
        if is_boundary(match):
            left = max(left, match.end())
    right = len(text)
    for match in _SENTENCE_BREAK_RE.finditer(text, end):
        right = min(right, match.start())
        break
    for match in _CONJUNCTION_RE.finditer(text, end):
        if is_boundary(match):
            right = min(right, match.start())
            break
    clause = text[left:right].strip()
    return clause or None


def _first_level(clause: str, offset: int) -> tuple[float, Unit] | None:
    """The first number that is a level, skipping periods and timeframes."""
    for match in _NUMBER_RE.finditer(clause, offset):
        if _is_parenthesised(clause, match.start(), match.end()):
            # `RSI(14)` states the indicator's period, not the level to compare to.
            continue
        unit = _unit_after(clause, match.group(0), match.end())
        if unit is None:
            continue
        return float(match.group(0)), unit
    return None


def _is_parenthesised(clause: str, start: int, end: int) -> bool:
    before = clause[start - 1] if start else ""
    after = clause[end] if end < len(clause) else ""
    return before == "(" and after == ")"


def _unit_after(clause: str, number: str, end: int) -> Unit | None:
    """Classify what follows the number, or ``None`` when it is not a level."""
    rest = clause[end:]
    timeframe = _TIMEFRAME_SUFFIX_RE.match(rest)
    if timeframe and f"{number}{timeframe.group(1).casefold()}" in SUPPORTED_TIMEFRAMES:
        # `15m` names a timeframe, not a level to compare against.
        return None
    if _PERCENT_SUFFIX_RE.match(rest):
        return "percent"
    if _MULTIPLE_SUFFIX_RE.match(rest):
        return "multiple"
    return "plain"
