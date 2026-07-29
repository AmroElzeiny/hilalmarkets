"""The one comparison vocabulary the whole compiler shares.

Several modules independently decided what ``above``, ``at most`` and ``no less
than`` mean, and each understood a different subset. A phrase the turn classifier
recognised could be invisible to the compiler's own patterns, which is how
``RSI at most 30`` reached a code path that fell back to ``RSI >= 50``.

The table lives here, on its own, so every reader shares both the mapping and the
ordering. It has no dependencies beyond the strategy schema, so any module can
import it without creating a cycle.
"""

from __future__ import annotations

import re

from ai_market_monitor.schemas.strategy import Comparator

#: Ordered longest-phrase-first so ``at least`` wins over ``least``, ``crosses
#: above`` wins over ``above``, and ``no less than`` is never read as the ``less
#: than`` inside it. Order is part of the contract, not an implementation detail.
OPERATOR_TERMS: tuple[tuple[str, Comparator], ...] = (
    ("crosses above", Comparator.CROSSES_ABOVE),
    ("crossing above", Comparator.CROSSES_ABOVE),
    ("crosses over", Comparator.CROSSES_ABOVE),
    ("crosses up through", Comparator.CROSSES_ABOVE),
    ("crosses below", Comparator.CROSSES_BELOW),
    ("crossing below", Comparator.CROSSES_BELOW),
    ("crosses under", Comparator.CROSSES_BELOW),
    ("crosses down through", Comparator.CROSSES_BELOW),
    ("sweeps below", Comparator.CROSSES_BELOW),
    ("sweeps above", Comparator.CROSSES_ABOVE),
    ("at least", Comparator.GREATER_THAN_OR_EQUAL),
    ("no less than", Comparator.GREATER_THAN_OR_EQUAL),
    ("not less than", Comparator.GREATER_THAN_OR_EQUAL),
    ("greater than or equal", Comparator.GREATER_THAN_OR_EQUAL),
    ("at or above", Comparator.GREATER_THAN_OR_EQUAL),
    ("or more", Comparator.GREATER_THAN_OR_EQUAL),
    ("or higher", Comparator.GREATER_THAN_OR_EQUAL),
    ("or greater", Comparator.GREATER_THAN_OR_EQUAL),
    ("or above", Comparator.GREATER_THAN_OR_EQUAL),
    ("or over", Comparator.GREATER_THAN_OR_EQUAL),
    ("and above", Comparator.GREATER_THAN_OR_EQUAL),
    ("and higher", Comparator.GREATER_THAN_OR_EQUAL),
    ("and up", Comparator.GREATER_THAN_OR_EQUAL),
    ("minimum", Comparator.GREATER_THAN_OR_EQUAL),
    ("at most", Comparator.LESS_THAN_OR_EQUAL),
    ("no more than", Comparator.LESS_THAN_OR_EQUAL),
    ("not more than", Comparator.LESS_THAN_OR_EQUAL),
    ("less than or equal", Comparator.LESS_THAN_OR_EQUAL),
    ("at or below", Comparator.LESS_THAN_OR_EQUAL),
    ("or less", Comparator.LESS_THAN_OR_EQUAL),
    ("or lower", Comparator.LESS_THAN_OR_EQUAL),
    ("or below", Comparator.LESS_THAN_OR_EQUAL),
    ("or under", Comparator.LESS_THAN_OR_EQUAL),
    ("and below", Comparator.LESS_THAN_OR_EQUAL),
    ("and lower", Comparator.LESS_THAN_OR_EQUAL),
    ("maximum", Comparator.LESS_THAN_OR_EQUAL),
    ("\u0644\u0627 \u062a\u062a\u062c\u0627\u0648\u0632", Comparator.LESS_THAN_OR_EQUAL),
    ("\u0645\u0627 \u062a\u062a\u062c\u0627\u0648\u0632", Comparator.LESS_THAN_OR_EQUAL),
    ("gte", Comparator.GREATER_THAN_OR_EQUAL),
    ("lte", Comparator.LESS_THAN_OR_EQUAL),
    ("gt", Comparator.GREATER_THAN),
    ("lt", Comparator.LESS_THAN),
    ("eq", Comparator.EQUAL),
    ("greater than", Comparator.GREATER_THAN),
    ("more than", Comparator.GREATER_THAN),
    ("higher than", Comparator.GREATER_THAN),
    ("strictly above", Comparator.GREATER_THAN),
    ("less than", Comparator.LESS_THAN),
    ("lower than", Comparator.LESS_THAN),
    ("strictly below", Comparator.LESS_THAN),
    ("equal to", Comparator.EQUAL),
    ("above", Comparator.GREATER_THAN),
    ("over", Comparator.GREATER_THAN),
    ("below", Comparator.LESS_THAN),
    ("under", Comparator.LESS_THAN),
    ("crosses", Comparator.CROSSES_ABOVE),
    ("sweeps", Comparator.CROSSES_BELOW),
    # Arabic and Arabizi. Longest phrase first, same as the English entries: `على
    # الأقل` (at least) must win over the bare `أقل` (less) inside it.
    ("على الأقل", Comparator.GREATER_THAN_OR_EQUAL),
    ("على الاقل", Comparator.GREATER_THAN_OR_EQUAL),
    ("بحد أدنى", Comparator.GREATER_THAN_OR_EQUAL),
    ("بحد ادنى", Comparator.GREATER_THAN_OR_EQUAL),
    ("لا يقل عن", Comparator.GREATER_THAN_OR_EQUAL),
    ("3ala el a2al", Comparator.GREATER_THAN_OR_EQUAL),
    ("3al a2al", Comparator.GREATER_THAN_OR_EQUAL),
    ("على الأكثر", Comparator.LESS_THAN_OR_EQUAL),
    ("على الاكثر", Comparator.LESS_THAN_OR_EQUAL),
    ("بحد أقصى", Comparator.LESS_THAN_OR_EQUAL),
    ("بحد اقصى", Comparator.LESS_THAN_OR_EQUAL),
    ("لا يزيد عن", Comparator.LESS_THAN_OR_EQUAL),
    ("3ala el aktar", Comparator.LESS_THAN_OR_EQUAL),
    ("أو أكثر", Comparator.GREATER_THAN_OR_EQUAL),
    ("او اكثر", Comparator.GREATER_THAN_OR_EQUAL),
    ("فأكثر", Comparator.GREATER_THAN_OR_EQUAL),
    ("فاكثر", Comparator.GREATER_THAN_OR_EQUAL),
    ("aw aktar", Comparator.GREATER_THAN_OR_EQUAL),
    ("أو أقل", Comparator.LESS_THAN_OR_EQUAL),
    ("او اقل", Comparator.LESS_THAN_OR_EQUAL),
    ("فأقل", Comparator.LESS_THAN_OR_EQUAL),
    ("فاقل", Comparator.LESS_THAN_OR_EQUAL),
    ("aw a2al", Comparator.LESS_THAN_OR_EQUAL),
    ("أكبر من", Comparator.GREATER_THAN),
    ("اكبر من", Comparator.GREATER_THAN),
    ("أعلى من", Comparator.GREATER_THAN),
    ("اعلى من", Comparator.GREATER_THAN),
    ("فوق", Comparator.GREATER_THAN),
    ("akbar men", Comparator.GREATER_THAN),
    ("fo2", Comparator.GREATER_THAN),
    ("أصغر من", Comparator.LESS_THAN),
    ("اصغر من", Comparator.LESS_THAN),
    ("أقل من", Comparator.LESS_THAN),
    ("اقل من", Comparator.LESS_THAN),
    ("تحت", Comparator.LESS_THAN),
    ("a2al men", Comparator.LESS_THAN),
    ("ta7t", Comparator.LESS_THAN),
    ("يخترق صعودا", Comparator.CROSSES_ABOVE),
    ("يخترق هبوطا", Comparator.CROSSES_BELOW),
    ("يساوي", Comparator.EQUAL),
)

#: The phrases that follow the value they govern: ``7.5% or less``, ``3% and up``.
#: Every other phrase in the table precedes its value.
#:
#: Position is part of the vocabulary, not of any one caller. The nearest-operator
#: rule scans to the *left* of a number, so a postfix phrase was invisible to it and
#: the reading fell through to a caller default: ``a bullish move of 7.5% or less``
#: compiled as ``>= 7.5`` — a floor where the trader stated a ceiling, which is the
#: opposite alert. Recording which phrases sit on the right is what lets one reader
#: find the governing operator on either side.
POSTFIX_TERMS: frozenset[str] = frozenset(
    {
        "or more",
        "or higher",
        "or greater",
        "or above",
        "or over",
        "and above",
        "and higher",
        "and up",
        "or less",
        "or lower",
        "or below",
        "or under",
        "and below",
        "and lower",
        "أو أكثر",
        "او اكثر",
        "فأكثر",
        "فاكثر",
        "aw aktar",
        "أو أقل",
        "او اقل",
        "فأقل",
        "فاقل",
        "aw a2al",
    }
)

#: Comparators that place a ceiling on a value. A capability that only expresses
#: "at least" cannot represent these, and must refuse rather than invert them.
UPPER_BOUND_COMPARATORS: frozenset[Comparator] = frozenset(
    {Comparator.LESS_THAN, Comparator.LESS_THAN_OR_EQUAL}
)

#: Comparators that place a floor on a value.
LOWER_BOUND_COMPARATORS: frozenset[Comparator] = frozenset(
    {Comparator.GREATER_THAN, Comparator.GREATER_THAN_OR_EQUAL}
)


def comparator_terms() -> tuple[str, ...]:
    """Every recognised comparison phrase, longest first.

    Exposed so callers that must build their own regex alternation share this exact
    vocabulary and ordering instead of hand-writing a subset that drifts.
    """
    return tuple(term for term, _comparator in OPERATOR_TERMS)


def find_comparator(text: str) -> tuple[Comparator, int, int] | None:
    """Locate the comparison wording and return ``(comparator, start, end)``.

    Callers that need the threshold belonging to this operator use the span to look
    only after it, which keeps an indicator period (``RSI(14) below 30``) from being
    read as the level.
    """
    lowered = " ".join((text or "").split()).casefold()
    symbolic = re.search(r"(?<![-=<>])(?:>=|≤|≥|<=|==|>|<)(?![-=<>])", lowered)
    if symbolic:
        token = symbolic.group(0)
        comparator = {
            ">=": Comparator.GREATER_THAN_OR_EQUAL,
            "≥": Comparator.GREATER_THAN_OR_EQUAL,
            "<=": Comparator.LESS_THAN_OR_EQUAL,
            "≤": Comparator.LESS_THAN_OR_EQUAL,
            ">": Comparator.GREATER_THAN,
            "<": Comparator.LESS_THAN,
            "==": Comparator.EQUAL,
        }[token]
        return comparator, symbolic.start(), symbolic.end()
    for term, comparator in OPERATOR_TERMS:
        match = re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", lowered)
        if match:
            return comparator, match.start(), match.end()
    return None


#: Every symbolic operator, and every phrase from the table, as one alternation.
#: Built from ``OPERATOR_TERMS`` in table order so the longest phrase wins at any
#: given position: ``no less than`` is never read as the ``less than`` inside it.
_ANY_OPERATOR_RE = re.compile(
    r"(?<![-=<>])(?:>=|≤|≥|<=|==|>|<)(?![-=<>])|"
    + "|".join(rf"(?<![a-z]){re.escape(term)}(?![a-z])" for term, _c in OPERATOR_TERMS),
    re.IGNORECASE,
)

_SYMBOLIC_COMPARATORS: dict[str, Comparator] = {
    ">=": Comparator.GREATER_THAN_OR_EQUAL,
    "≥": Comparator.GREATER_THAN_OR_EQUAL,
    "<=": Comparator.LESS_THAN_OR_EQUAL,
    "≤": Comparator.LESS_THAN_OR_EQUAL,
    ">": Comparator.GREATER_THAN,
    "<": Comparator.LESS_THAN,
    "==": Comparator.EQUAL,
}

_PHRASE_COMPARATORS: dict[str, Comparator] = {
    term: comparator for term, comparator in OPERATOR_TERMS
}


def _comparator_for(token: str) -> Comparator | None:
    symbolic = _SYMBOLIC_COMPARATORS.get(token)
    if symbolic is not None:
        return symbolic
    return _PHRASE_COMPARATORS.get(token.casefold())


def find_comparator_before(text: str, position: int) -> tuple[Comparator, int, int] | None:
    """The comparison that governs the value at ``position``.

    A threshold is governed by the *nearest* operator to its left, not by whichever
    operator happens to appear first in the sentence. Scanning a whole window instead
    let an operator from a neighbouring clause claim the threshold: in
    ``(close < open) AND (bearish % change ≥ 1.0%)`` the leading ``<`` defines the
    candle's direction, and reading it as the threshold's operator compiled a
    *minimum* 1% bearish move as a *maximum* — the opposite alert.
    """
    nearest: tuple[Comparator, int, int] | None = None
    for match in _ANY_OPERATOR_RE.finditer(text or ""):
        if match.end() > position:
            break
        comparator = _comparator_for(match.group(0))
        if comparator is not None:
            nearest = (comparator, match.start(), match.end())
    return nearest


#: What may sit between a number and the postfix phrase that governs it: the unit,
#: and whitespace. Anything longer belongs to a different clause.
_POSTFIX_GAP = r"[\s,]*(?:%|percent|pct|x)?[\s,]*"

_POSTFIX_RE = re.compile(
    _POSTFIX_GAP
    + r"(?:(?P<plus>\+)|(?P<phrase>"
    # Table order, so the longest phrase still wins: `or higher` is never read as
    # the `higher` inside it.
    + "|".join(
        re.escape(term) for term, _comparator in OPERATOR_TERMS if term in POSTFIX_TERMS
    )
    # A number after the phrase means it introduces the *next* comparison, not this
    # value's ceiling: in `drops 5% and above 200 ema`, `and above` belongs to 200.
    + r"))(?![a-z])(?!\s*[-+]?\d)",
    re.IGNORECASE,
)


def find_comparator_for_value(
    text: str, start: int, end: int
) -> tuple[Comparator, int, int] | None:
    """The comparison that governs the value spanning ``start``..``end``.

    Comparison wording sits on either side of its value. ``at most 7.5%`` states the
    ceiling before the number; ``7.5% or less`` states the same ceiling after it. A
    reader that only looked left saw nothing in the second form, and the caller's
    "a stated move with no stated operator means at least this much" convention then
    compiled it as ``>= 7.5``: a floor where the trader asked for a ceiling.

    A postfix phrase must sit immediately after the value — only the unit and
    whitespace may come between — so wording from the next clause cannot claim it.
    That adjacency also makes it the nearest operator, which is why it wins over
    anything further to the left.
    """
    postfix = _POSTFIX_RE.match(text or "", end)
    if postfix is not None:
        if postfix.group("plus") is not None:
            return Comparator.GREATER_THAN_OR_EQUAL, postfix.start(), postfix.end()
        comparator = _PHRASE_COMPARATORS.get(postfix.group("phrase").casefold())
        if comparator is not None:
            return comparator, postfix.start(), postfix.end()
    return find_comparator_before(text, start)


def detect_comparator(text: str) -> Comparator | None:
    """Map comparison wording to an exact comparator."""
    found = find_comparator(text)
    return found[0] if found else None


def states_upper_bound(text: str) -> bool:
    """True when ``text`` states a ceiling rather than a floor."""
    found = find_comparator(text)
    return found is not None and found[0] in UPPER_BOUND_COMPARATORS
