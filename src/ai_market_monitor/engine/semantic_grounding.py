"""Is this exact value actually in what the trader wrote?

Substring containment answered that question badly enough to be dangerous:

* ``str(1) in "on the 15m"`` is true, so a 1-candle lookback grounded itself on a
  timeframe
* ``str(2) in "over 20 candles"`` is true, so a 2 grounded itself on a 20
* ``"5" in "5m"`` is true, so a 5 **percent** move grounded itself on a 5 **minute**
  timeframe — same digits, different quantity, opposite rule
* ``"gte" in ...`` is false for ``at least``, so a correctly-read operator looked
  ungrounded and a correct turn was refused

Every check here goes through the reader that owns that kind of value, so the grounding
test understands the trader's words exactly as well as the compiler does: comparators
through :mod:`comparators`, timeframes through the turn reader's normaliser, formulas
through the percentage parser, directions through the movement vocabulary, windows
through :mod:`lookback`. A number is matched on token boundaries **and** on its unit,
so a percentage can only ever be grounded by a percentage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ai_market_monitor.engine.comparators import detect_comparator, find_comparator
from ai_market_monitor.engine.formula_compiler import parse_percentage_formula
from ai_market_monitor.engine.lookback import read_lookback
from ai_market_monitor.engine.price_movement import movement_direction
from ai_market_monitor.engine.timeframes import SUPPORTED_TIMEFRAMES
from ai_market_monitor.engine.turn_fragments import (
    extract_symbols,
    extract_timeframes,
    split_symbol,
)
from ai_market_monitor.schemas.strategy import Comparator, StrategyDirection
from ai_market_monitor.schemas.strategy_draft_v2 import (
    FormulaKind,
    MovementDirection,
    StrategyBias,
)

#: What a bare number in the text is measuring. A number only grounds a value of the
#: same unit, so `5%` and `5m` can never stand in for each other.
NumberUnit = str

#: A percentage: `5%`, `5 percent`, `5 pct`.
_PERCENT_RE = re.compile(
    r"(?<![\w.])([-+]?\d+(?:\.\d+)?)\s*(?:%|percent\b|pct\b)",
    re.IGNORECASE,
)

#: A multiple: `2x`, `2 times`.
_MULTIPLE_RE = re.compile(
    r"(?<![\w.])([-+]?\d+(?:\.\d+)?)\s*(?:x\b|times\b)",
    re.IGNORECASE,
)

#: Any number that is not immediately a timeframe, a percentage or a multiple. These
#: are price levels, candle counts and indicator periods.
_PLAIN_NUMBER_RE = re.compile(r"(?<![\w.])([-+]?\d+(?:\.\d+)?)(?![\d.])")

#: A number glued to a timeframe unit. Matched first so `15m` never yields a plain 15.
_TIMEFRAME_NUMBER_RE = re.compile(
    r"(?<![\w.])(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)"
    r"(?![a-z])",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class GroundingScope:
    """The text a value is allowed to be grounded in.

    ``authorizing_text`` is the one segment that authorised this mutation. Grounding a
    value anywhere in the whole message let a number the user wrote about a *different*
    rule justify a change to this one, which is precisely what section 3 forbids.

    ``inherited`` names the fields an edit legitimately keeps from the rule it is
    editing: ``change that to at least 8%`` does not restate the timeframe.
    """

    authorizing_text: str
    inherited: frozenset[str] = frozenset()

    @property
    def collapsed(self) -> str:
        return " ".join((self.authorizing_text or "").split())


def _numbers_with_unit(text: str) -> dict[NumberUnit, set[float]]:
    """Every number in ``text``, filed under what it measures."""

    found: dict[NumberUnit, set[float]] = {
        "percent": set(),
        "multiple": set(),
        "timeframe": set(),
        "plain": set(),
    }
    consumed: list[tuple[int, int]] = []
    for pattern, unit in (
        (_PERCENT_RE, "percent"),
        (_MULTIPLE_RE, "multiple"),
        (_TIMEFRAME_NUMBER_RE, "timeframe"),
    ):
        for match in pattern.finditer(text):
            found[unit].add(float(match.group(1)))
            consumed.append((match.start(), match.end()))
    for match in _PLAIN_NUMBER_RE.finditer(text):
        if any(start <= match.start() < end for start, end in consumed):
            # Already claimed by a unit-bearing pattern: `15` inside `15m` is not a
            # standalone number and must not ground a candle count or a price.
            continue
        found["plain"].add(float(match.group(1)))
    return found


def grounds_number(text: str, value: float, *, unit: NumberUnit) -> bool:
    """True when ``value`` appears in ``text`` measuring ``unit``.

    Token-bounded and unit-matched. ``5`` in ``5m`` does not ground a 5 percent move,
    and ``2`` does not ground itself on the ``20`` inside ``20 candles``.
    """
    numbers = _numbers_with_unit(text)
    if unit == "price":
        # A price level is written plainly. A multiple is a different quantity.
        candidates = numbers["plain"]
    elif unit in {"count", "index", "period"}:
        candidates = numbers["plain"]
    else:
        candidates = numbers.get(unit, set())
    return any(abs(candidate - value) < 1e-9 for candidate in candidates)


def grounds_symbol(text: str, symbol: str) -> bool:
    """True when ``text`` names this market, in any spelling the reader accepts."""

    wanted = symbol.upper().replace("/", "").replace("-", "").replace("_", "")
    markets = {item.upper() for item in extract_symbols(text)}
    if wanted in markets:
        return True
    # The canonical draft stores base assets in its inclusion and condition scopes,
    # while the trader naturally names a market pair. BTC/USDT therefore authorizes
    # BTC, but never USDT or an unrelated base.
    return any(
        parts is not None and parts[0] == wanted
        for market in markets
        if (parts := split_symbol(market)) is not None
    )


def grounds_timeframe(text: str, timeframe: str) -> bool:
    """True when ``text`` names this timeframe, through the shared normaliser.

    ``60 minutes``, ``1h`` and ``hourly`` are the same timeframe; a raw substring test
    saw three different strings.
    """
    if timeframe not in SUPPORTED_TIMEFRAMES:
        return False
    return timeframe in extract_timeframes(text)


def grounds_text_value(text: str, value: str) -> bool:
    """Ground a trader-controlled string or enum without substring accidents."""

    normalized_text = re.sub(r"[_-]+", " ", text.casefold())
    normalized_value = re.sub(r"[_-]+", " ", value.casefold()).strip()
    aliases = {
        "close": ("close", "closing price", "سعر الاغلاق", "سعر الإغلاق", "2fl"),
        "open": ("open", "opening price", "سعر الفتح", "سعر الافتتاح"),
        "high": ("high", "highest price", "اعلى", "أعلى"),
        "low": ("low", "lowest price", "اقل", "أقل"),
        "bullish": ("bullish", "صاعد", "tale3", "tal3"),
        "bearish": ("bearish", "هابط", "نازل", "nazel"),
        "neutral": ("neutral", "محايد"),
    }
    candidates = aliases.get(normalized_value, (normalized_value,))
    return any(
        bool(re.search(rf"(?<!\w){re.escape(candidate)}(?!\w)", normalized_text))
        for candidate in candidates
    )


def grounds_boolean(text: str, value: bool) -> bool:
    """Ground an explicit boolean choice in English, Arabic, or common Arabizi."""

    lowered = re.sub(r"[_-]+", " ", text.casefold())
    patterns = (
        (
            r"\b(?:yes|true|enable|enabled|use|required|must|on)\b"
            r"|(?:نعم|ايوه|أيوه|مطلوب|فعّل|فعل)"
            r"|\b(?:aywa|ah|fa3al|shaghal)\b"
        )
        if value
        else (
            r"\b(?:no|false|disable|disabled|do not|don't|optional|off)\b"
            r"|(?:لا|مش|اختياري|عطّل|الغ)"
            r"|\b(?:la2|la|msh|ekhtiyary|optional)\b"
        )
    )
    return bool(re.search(patterns, lowered, re.IGNORECASE))


def grounds_operator(text: str, operator: Comparator) -> bool:
    """True when ``text`` states this comparison, in words or symbols.

    ``at least`` grounds ``gte``; ``no more than`` grounds ``lte``; ``crosses above``
    grounds ``crosses_above``. Boolean operators need no wording — a mechanic that is
    either true or false carries its own comparison.
    """
    if operator in {Comparator.IS_TRUE, Comparator.IS_FALSE}:
        return True
    stated = detect_comparator(text)
    if stated == operator:
        return True
    # A clause can hold more than one comparison word; any of them may be the one.
    remaining = text
    while remaining:
        found = find_comparator(remaining)
        if found is None:
            return False
        if found[0] == operator:
            return True
        remaining = remaining[found[2] :]
    return False


def grounds_direction(text: str, direction: MovementDirection) -> bool:
    """True when ``text`` states this side, through the movement vocabulary.

    ``neutral`` needs no wording: it is the absence of a stated side.
    """
    if direction in {MovementDirection.NEUTRAL, MovementDirection.NOT_APPLICABLE}:
        return True
    movement = movement_direction(text)
    if movement is not None:
        return (movement == "up") == (direction == MovementDirection.UP)
    return False


def grounds_strategy_bias(text: str, bias: StrategyBias) -> bool:
    """Ground an explicit trade-side intent without inferring it from price movement."""

    if bias == StrategyBias.NEUTRAL:
        return True
    lowered = text.casefold()
    if bias == StrategyBias.LONG:
        return bool(re.search(r"\blong\b|\bbuy bias\b|\bbullish bias\b", lowered))
    return bool(re.search(r"\bshort\b|\bsell bias\b|\bbearish bias\b", lowered))


#: Wording that names each formula, read through the canonical parser rather than a
#: hand-written phrase list.
_FORMULA_MARKERS: dict[FormulaKind, tuple[str, ...]] = {
    FormulaKind.PREVIOUS_CANDLE_REFERENCE: ("previous candle", "prior candle", "last closed"),
    FormulaKind.FIXED_REFERENCE_LEVEL: (),
    FormulaKind.LOOKBACK_REFERENCE_LEVEL: ("highest high", "lowest low"),
    FormulaKind.CROSS: ("cross",),
    FormulaKind.SWEEP_AND_RECLAIM: ("sweep",),
    FormulaKind.LOW_TO_HIGH_PERCENTAGE: ("low to high", "low-to-high"),
}

_PERCENTAGE_FORMULAS: dict[str, FormulaKind] = {
    "open_to_close": FormulaKind.OPEN_TO_CLOSE_PERCENTAGE,
    "close_to_close": FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE,
    "reference_to_current": FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE,
    "high_to_low": FormulaKind.HIGH_TO_LOW_PERCENTAGE,
}


def grounds_formula(text: str, formula: FormulaKind) -> bool:
    """True when ``text`` describes this formula.

    Percentage formulas go through :func:`parse_percentage_formula`, which is the same
    reader the compiler uses, so ``open to close``, ``open-to-close`` and ``o2c`` all
    ground ``open_to_close_percentage`` without a second phrase list to drift.
    """
    if formula == FormulaKind.CAPABILITY:
        # A registered capability is grounded by its own key and parameters, checked by
        # the registry validator, not by prose.
        return True
    if formula in _PERCENTAGE_FORMULAS.values():
        parsed = parse_percentage_formula(
            text,
            default_timeframe="15m",
            default_direction=StrategyDirection.BOTH,
        )
        if parsed is not None and _PERCENTAGE_FORMULAS.get(parsed.formula) == formula:
            return True
    markers = _FORMULA_MARKERS.get(formula, ())
    lowered = text.casefold()
    if markers and any(marker in lowered for marker in markers):
        return True
    if formula == FormulaKind.FIXED_REFERENCE_LEVEL:
        # A fixed level is grounded by a price the user wrote plus a comparison.
        return bool(_numbers_with_unit(text)["plain"]) and find_comparator(text) is not None
    return False


def grounds_lookback(text: str, candles: int, *, timeframe: str | None) -> bool:
    """True when ``text`` states this window, through the one window reader."""

    reading = read_lookback(text, timeframe=timeframe or "15m")
    if reading is not None and reading.candles == candles:
        return True
    # A single reference candle is the default when no window is stated at all.
    return candles == 1 and reading is None


def grounds_boolean_shape(text: str, shape: str) -> bool:
    """True when the turn's wording can support this Boolean structure.

    Only the operators are checked: a shape containing ``or`` needs the trader to have
    written a choice, and a ``not`` needs a negation. Structure the user never wrote is
    structure the model invented.
    """
    lowered = text.casefold()
    if "and(" in shape and not re.search(r"\band\b|&&|\bboth\b|\ball\b", lowered):
        return False
    if "or(" in shape and not re.search(r"\bor\b|\|\||\beither\b", lowered):
        return False
    return not (
        "not(" in shape
        and not re.search(
            r"\bnot\b|\bwithout\b|\bavoid\b|\bunless\b|\bexcept\b|\bno\b", lowered
        )
    )
