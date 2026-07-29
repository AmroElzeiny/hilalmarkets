"""Deterministic compilation of common price-change formulas.

The capability registry identifies named trading mechanics. Arithmetic supplied by
the user is different: the formula, operands, comparator, and threshold are already
the mechanic and must be preserved exactly. Routing it through semantic retrieval can
replace it with an unrelated registered condition.

This module recognizes the bounded formula family supported by the runtime and emits
ordinary strategy DSL nodes. It never calls a model and never guesses a missing
measurement reference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal

from ai_market_monitor.db.models.enums import ConditionType, LogicalOperator
from ai_market_monitor.engine.comparators import detect_comparator, find_comparator_for_value
from ai_market_monitor.engine.grounded_patch import verify_grounding
from ai_market_monitor.engine.lookback import read_lookback
from ai_market_monitor.engine.price_movement import (
    movement_direction,
    movement_direction_before,
    stated_side,
)
from ai_market_monitor.engine.turn_fragments import (
    extract_timeframe_roles,
    extract_timeframes,
)
from ai_market_monitor.schemas.strategy import (
    Comparator,
    ConditionGroup,
    ConditionRule,
    Operand,
    OperandKind,
    StrategyDirection,
)

FormulaKind = Literal[
    "open_to_close",
    "close_to_close",
    "high_to_low",
    "reference_to_current",
]
FormulaDirection = Literal["up", "down", "signed"]

_PERCENT_RE = re.compile(r"(?P<value>-?\d+(?:\.\d+)?)\s*%")

#: "Today" anchors the move to the daily open. Arabic and Arabizi speakers write it
#: as often as English speakers do, and missing their wording silently changed the
#: measurement reference to the previous candle.
_TODAY_PHRASES = (
    "today",
    "since midnight",
    "daily move",
    "this day",
    "اليوم",
    "النهارده",
    "النهاردة",
    "من بداية اليوم",
    "el naharda",
    "elnaharda",
    "naharda",
    "el yom",
)
_EXPLICIT_OPERATOR_RE = re.compile(
    r"\b(?:operator|comparator)[\s*_:`]*(?:=|:)?[\s*_:`]*"
    r"(?P<operator>gte|lte|gt|lt|eq|crosses_above|crosses_below)\b",
    re.IGNORECASE,
)
_IMPLEMENTED_OPERATOR_RE = re.compile(
    r"\b(?:implemented|encoded|expressed|applied)\s+as[\s*_:`]*"
    r"(?P<operator>>=|<=|>|<|==|=|gte|lte|gt|lt|eq)",
    re.IGNORECASE,
)
_MAGNITUDE_COMPARISON_RE = re.compile(
    r"(?:\|\s*move\s*\||(?:bearish|bullish|move)?\s*magnitude)"
    r"[\s*_:`]*(?:must\s+be|is|of)?[\s*_:`]*"
    r"(?P<operator>>=|<=|>|<|==|=|gte|lte|gt|lt|eq|at\s+least|at\s+most)"
    r"[\s*_:`]*(?P<value>-?\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
#: The names traders give the measured move when they write it as an expression.
#: `%move` and `move%` were missing, so a fully specified `%move >= 7.5` was
#: reported as an instruction the compiler could not convert.
_MOVE_METRIC_NAMES = (
    # Written as an identifier…
    r"percent_move",
    r"percentage_move",
    r"move_pct",
    r"pct_change",
    r"pct_move",
    r"percentage_change",
    r"percent_change",
    # …with a percent sign…
    r"%\s*change",
    r"%\s*move",
    r"move\s*%",
    r"change\s*%",
    # …or in words. `the move percent must be <= 1.0` names the same quantity as
    # `percent_move <= 1.0`, and only the spelling differed.
    r"move\s+percent(?:age)?",
    r"percent(?:age)?\s+move",
    r"percent(?:age)?\s+change",
    r"change\s+percent(?:age)?",
)
#: Words that can sit between the metric and its comparison without changing either:
#: `the move percent must be <= 1.0` states the same rule as `move_pct <= 1.0`.
#: Markdown emphasis and punctuation are tolerated for the same reason.
_METRIC_TO_OPERATOR_FILLER = (
    r"[\s*_:`]*(?:must\s+be|should\s+be|has\s+to\s+be|needs?\s+to\s+be|"
    r"is|are|of|at)?[\s*_:`]*"
)
_FORMULA_COMPARISON_RE = re.compile(
    r"(?<![a-z_])(?:" + "|".join(_MOVE_METRIC_NAMES) + r")(?![a-z_])"
    + _METRIC_TO_OPERATOR_FILLER
    + r"(?P<operator>>=|<=|≥|≤|>|<|==|=|gte|lte|gt|lt|eq)[\s*_:`]*"
    r"(?P<value>-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_ASSIGNED_FORMULA_COMPARISON_RE = re.compile(
    r"(?<![a-z_])(?:" + "|".join(_MOVE_METRIC_NAMES) + r")(?![a-z_])"
    r"\s*=\s*(?:\([^)]{1,160}\)|[^;,\n]{1,160}?)\s*"
    r"(?P<operator>>=|<=|>|<|==|=|gte|lte|gt|lt|eq)\s*"
    r"(?P<value>-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_ARITHMETIC_COMPARISON_RE = re.compile(
    r"\)\s*/\s*(?:\d{1,2}\s*(?:m|h|d|w)\s+)?(?:open|close)"
    r"\s*(?P<operator>>=|<=|>|<|==|=|gte|lte|gt|lt|eq)\s*"
    r"(?P<value>-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_CLOSE_OPEN_RE = re.compile(
    r"\bclose(?:_now|_current)?\s*(?P<operator>>=|<=|>|<|==|=|gte|lte|gt|lt|eq)"
    r"\s*open\b",
    re.IGNORECASE,
)

_OPERATOR_BY_TOKEN: dict[str, Comparator] = {
    ">": Comparator.GREATER_THAN,
    "gt": Comparator.GREATER_THAN,
    ">=": Comparator.GREATER_THAN_OR_EQUAL,
    "≥": Comparator.GREATER_THAN_OR_EQUAL,
    "gte": Comparator.GREATER_THAN_OR_EQUAL,
    "<": Comparator.LESS_THAN,
    "lt": Comparator.LESS_THAN,
    "<=": Comparator.LESS_THAN_OR_EQUAL,
    "≤": Comparator.LESS_THAN_OR_EQUAL,
    "lte": Comparator.LESS_THAN_OR_EQUAL,
    "=": Comparator.EQUAL,
    "==": Comparator.EQUAL,
    "eq": Comparator.EQUAL,
    "crosses_above": Comparator.CROSSES_ABOVE,
    "crosses_below": Comparator.CROSSES_BELOW,
}


@dataclass(frozen=True, slots=True)
class PercentageFormulaSpec:
    """A fully measurable price-change instruction."""

    formula: FormulaKind
    direction: FormulaDirection
    comparator: Comparator
    threshold_percent: float
    timeframe: str
    reference_timeframe: str | None
    reference_field: str
    current_field: str
    #: Closed bars between the reference and the current value. ``1`` is the platform's
    #: documented convention — the immediately previous closed candle — and applies
    #: only when the trader states no window; :func:`_resolved` replaces it whenever
    #: they do.
    lookback: int = 1
    source_fragment: str = ""

    def parameters(
        self,
    ) -> dict[str, int | float | str | bool | list[int | float | str | bool]]:
        return {
            "formula": self.formula,
            "direction": self.direction,
            "reference_field": self.reference_field,
            "current_field": self.current_field,
            "reference_timeframe": self.reference_timeframe or self.timeframe,
            "lookback": self.lookback,
            "scale": "percent",
            "closed_only": True,
        }


def parse_percentage_formula(
    text: str,
    *,
    default_timeframe: str,
    default_direction: StrategyDirection,
) -> PercentageFormulaSpec | None:
    """Return a formula only when its measurement reference is explicit.

    A directional percentage with no other reference uses the immediately previous
    closed candle. This is the narrow platform convention; explicit open, daily, or
    lookback wording always overrides it.
    """

    collapsed = " ".join((text or "").split())
    lowered = collapsed.casefold()
    threshold = _threshold_percent(collapsed)
    if threshold is None:
        return None

    roles = extract_timeframe_roles(collapsed)
    timeframes = extract_timeframes(collapsed)
    timeframe = roles.trigger or (timeframes[-1] if timeframes else default_timeframe)
    reference_timeframe = roles.context[0] if roles.context else None
    direction = _formula_direction(
        lowered, default_direction, threshold_at=_threshold_position(collapsed)
    )
    comparator = _comparator(collapsed)
    if comparator is None and movement_direction(lowered) is not None:
        # A stated move with no stated operator conventionally means "at least this
        # much". The direction vocabulary is shared, so this no longer depends on a
        # hand-written subset that omitted `drops`, `down` and `sell-off`.
        comparator = Comparator.GREATER_THAN_OR_EQUAL
    if comparator is None:
        return None

    if any(phrase in lowered for phrase in _TODAY_PHRASES):
        return _resolved(
            PercentageFormulaSpec(
                formula="reference_to_current",
                direction=direction,
                comparator=comparator,
                threshold_percent=threshold,
                timeframe=timeframe,
                reference_timeframe="1d",
                reference_field="open",
                current_field=_current_field(lowered, default="close"),
                source_fragment=collapsed[:500],
            ),
            lowered,
        )
    if _is_close_to_close(lowered):
        return _resolved(
            PercentageFormulaSpec(
                formula="close_to_close",
                direction=direction,
                comparator=comparator,
                threshold_percent=threshold,
                timeframe=timeframe,
                reference_timeframe=timeframe,
                reference_field="close",
                current_field="close",
                source_fragment=collapsed[:500],
            ),
            lowered,
        )
    if _is_open_to_close(lowered):
        return _resolved(
            PercentageFormulaSpec(
                formula="open_to_close",
                direction=direction,
                comparator=comparator,
                threshold_percent=threshold,
                timeframe=timeframe,
                reference_timeframe=timeframe,
                reference_field="open",
                current_field=_current_field(lowered, default="close"),
                source_fragment=collapsed[:500],
            ),
            lowered,
        )
    if _is_high_to_low(lowered):
        return _resolved(
            PercentageFormulaSpec(
                formula="high_to_low",
                direction="down",
                comparator=comparator,
                threshold_percent=threshold,
                timeframe=timeframe,
                reference_timeframe=timeframe,
                reference_field="high",
                current_field="low",
                source_fragment=collapsed[:500],
            ),
            lowered,
        )
    if _is_reference_move(lowered):
        reference_field = (
            "swing_low" if "swing low" in lowered or "local low" in lowered else "swing_high"
        )
        return _resolved(
            PercentageFormulaSpec(
                formula="reference_to_current",
                direction=direction,
                comparator=comparator,
                threshold_percent=threshold,
                timeframe=timeframe,
                reference_timeframe=reference_timeframe or timeframe,
                reference_field=reference_field,
                current_field=_current_field(
                    lowered,
                    default="low" if direction == "down" else "high",
                ),
                # A swing reference with no stated window searches the platform's
                # documented 20-bar window. Stated wording overrides it in `_resolved`.
                lookback=_REFERENCE_SWING_WINDOW,
                source_fragment=collapsed[:500],
            ),
            lowered,
        )
    # A stated move direction, or an explicitly stated side (`direction=long`), is
    # enough to compile. Requiring a movement *word* rejected fully specified
    # instructions such as `%move >= 7.5 for direction=long with operator=gte`,
    # which were then reported back to the trader as unconvertible.
    if movement_direction(lowered) is not None or stated_side(lowered) is not None:
        return _resolved(
            PercentageFormulaSpec(
                formula="close_to_close",
                direction=direction,
                comparator=comparator,
                threshold_percent=threshold,
                timeframe=timeframe,
                reference_timeframe=timeframe,
                reference_field="close",
                current_field="close",
                source_fragment=collapsed[:500],
            ),
            lowered,
        )
    if default_direction in {StrategyDirection.LONG, StrategyDirection.SHORT}:
        # A directional percentage with no stated anchor uses the platform's
        # documented previous-closed-candle convention. The assumption remains
        # visible in the translation sheet and can be corrected by a later patch.
        # `direction` already resolved the stated side, so it is used rather than
        # re-derived — recomputing it here discarded a direction the trader gave.
        return _resolved(
            PercentageFormulaSpec(
                formula="close_to_close",
                direction=direction,
                comparator=comparator,
                threshold_percent=threshold,
                timeframe=timeframe,
                reference_timeframe=timeframe,
                reference_field="close",
                current_field="close",
                source_fragment=collapsed[:500],
            ),
            lowered,
        )
    return None


def grounding_violations(spec: PercentageFormulaSpec, source: str) -> tuple[str, ...]:
    """Claims in ``spec`` that ``source`` does not support.

    Applies to every spec regardless of who produced it. The deterministic parser
    reads only what it matched, so it passes by construction; a model-proposed spec
    has to earn the same standing. That symmetry is the point — there is one bar for
    entering the compiler, and "a model said so" is not a way over it.
    """
    return verify_grounding(
        source,
        threshold=spec.threshold_percent,
        comparator=spec.comparator,
        direction=spec.direction,
    ).violations


#: Every operand that expresses "price moved by X percent". The platform has more than
#: one because two parsers grew independently: `percentage_change` carries the
#: comparison on the condition, so it can state an upper bound; `percent_change_up` /
#: `percent_change_down` spell the side into the operand name and are fixed at "at
#: least".
#:
#: Declared here, in the module that owns the formula, so callers can ask "is this the
#: percent-move mechanic?" without re-listing the names. Two parsers each emitting one
#: of these for the same sentence produced a strategy carrying the same requirement
#: twice, joined with AND.
PERCENT_MOVE_OPERANDS: frozenset[str] = frozenset(
    {"percentage_change", "percent_change_up", "percent_change_down"}
)


def compile_percentage_formula(
    spec: PercentageFormulaSpec,
    *,
    key: str = "percentage_move",
) -> ConditionRule:
    """Compile one formula into an executable numeric DSL condition."""

    return ConditionRule(
        key=key,
        label=_formula_label(spec),
        condition_type=ConditionType.PRICE_ACTION,
        timeframe=spec.timeframe,
        left=Operand(
            kind=OperandKind.MARKET_METRIC,
            name="percentage_change",
            parameters=spec.parameters(),
        ),
        comparator=spec.comparator,
        right=Operand(kind=OperandKind.CONSTANT, value=spec.threshold_percent),
        required=True,
        weight=1.5,
        required_data=["ohlcv"],
        explanation_template=(
            "The measured percentage change must be "
            f"{spec.comparator.value} {spec.threshold_percent:g}%."
        ),
        source_fragment=spec.source_fragment,
        confidence=1.0,
        ai_interpreted=False,
    )


def compile_explicit_formula_group(
    text: str,
    *,
    timeframe: str,
) -> ConditionGroup | None:
    """Compile an explicitly parenthesized signed range without flattening it.

    Example: ``(close < open) AND (percent_move >= -1) AND
    (percent_move <= 0)`` where ``percent_move=((close-open)/open)*100``.
    """

    collapsed = " ".join((text or "").split())
    lowered = collapsed.casefold()
    comparisons = list(_FORMULA_COMPARISON_RE.finditer(lowered))
    close_open = _CLOSE_OPEN_RE.search(lowered)
    if len(comparisons) < 2 or "and" not in lowered:
        return None
    if not _is_open_to_close(lowered):
        return None

    children: list[ConditionRule | ConditionGroup] = []
    if close_open is not None:
        children.append(
            ConditionRule(
                key="candle_close_vs_open",
                label="Candle close is below open",
                condition_type=ConditionType.PRICE_ACTION,
                timeframe=timeframe,
                left=Operand(kind=OperandKind.PRICE, field="close"),
                comparator=_OPERATOR_BY_TOKEN[close_open.group("operator").casefold()],
                right=Operand(kind=OperandKind.PRICE, field="open"),
                required_data=["ohlcv"],
                source_fragment=close_open.group(0)[:500],
                confidence=1.0,
            )
        )

    for index, match in enumerate(comparisons, start=1):
        comparator = _OPERATOR_BY_TOKEN[match.group("operator").casefold()]
        value = float(match.group("value"))
        children.append(
            ConditionRule(
                key=f"percentage_move_bound_{index}",
                label=f"Signed percentage move {comparator.value} {value:g}%",
                condition_type=ConditionType.PRICE_ACTION,
                timeframe=timeframe,
                left=Operand(
                    kind=OperandKind.MARKET_METRIC,
                    name="percentage_change",
                    parameters={
                        "formula": "open_to_close",
                        "direction": "signed",
                        "reference_field": "open",
                        "current_field": "close",
                        "reference_timeframe": timeframe,
                        "lookback": 1,
                        "scale": "percent",
                        "closed_only": True,
                    },
                ),
                comparator=comparator,
                right=Operand(kind=OperandKind.CONSTANT, value=value),
                required_data=["ohlcv"],
                source_fragment=match.group(0)[:500],
                confidence=1.0,
            )
        )
    if len(children) < 2:
        return None
    return ConditionGroup(
        key="explicit_percentage_formula",
        operator=LogicalOperator.AND,
        children=children,
    )


def _threshold_percent(text: str) -> float | None:
    percentages = [float(match.group("value")) for match in _PERCENT_RE.finditer(text)]
    if percentages:
        # The actionable threshold is normally the last restated value. Formula range
        # bounds are handled by compile_explicit_formula_group instead.
        return abs(percentages[-1])
    comparison = list(_FORMULA_COMPARISON_RE.finditer(text))
    comparison.extend(_ASSIGNED_FORMULA_COMPARISON_RE.finditer(text))
    comparison.extend(_ARITHMETIC_COMPARISON_RE.finditer(text))
    if not comparison:
        return None
    value = abs(float(comparison[-1].group("value")))
    # Ratios such as 0.005 represent 0.5%; explicit percent values remain unchanged.
    return value * 100 if value <= 0.1 and "* 100" not in text else value


def _threshold_position(text: str) -> int | None:
    """Where the threshold `_threshold_percent` selected sits, for nearest-first reads."""
    percentages = list(_PERCENT_RE.finditer(text))
    if percentages:
        return percentages[-1].start()
    comparison = list(_FORMULA_COMPARISON_RE.finditer(text))
    comparison.extend(_ASSIGNED_FORMULA_COMPARISON_RE.finditer(text))
    comparison.extend(_ARITHMETIC_COMPARISON_RE.finditer(text))
    return comparison[-1].start("value") if comparison else None


def _comparator(text: str) -> Comparator | None:
    explicit = list(_EXPLICIT_OPERATOR_RE.finditer(text))
    if explicit:
        return _OPERATOR_BY_TOKEN[explicit[-1].group("operator").casefold()]
    implemented = list(_IMPLEMENTED_OPERATOR_RE.finditer(text))
    if implemented:
        return _OPERATOR_BY_TOKEN[implemented[-1].group("operator").casefold()]
    magnitude = list(_MAGNITUDE_COMPARISON_RE.finditer(text))
    if magnitude:
        token = magnitude[-1].group("operator").casefold()
        if token == "at least":
            return Comparator.GREATER_THAN_OR_EQUAL
        if token == "at most":
            return Comparator.LESS_THAN_OR_EQUAL
        return _OPERATOR_BY_TOKEN[token]
    comparisons = list(_FORMULA_COMPARISON_RE.finditer(text))
    comparisons.extend(_ASSIGNED_FORMULA_COMPARISON_RE.finditer(text))
    comparisons.extend(_ARITHMETIC_COMPARISON_RE.finditer(text))
    if comparisons:
        return _OPERATOR_BY_TOKEN[comparisons[-1].group("operator").casefold()]
    percentages = list(_PERCENT_RE.finditer(text))
    for percentage in reversed(percentages):
        # The operator that governs a threshold is the nearest one to it, on either
        # side. A character window around the number caught operators from
        # neighbouring clauses — `(close < open) AND (bearish % change >= 1.0%)` read
        # the `<` that defines the candle body and compiled the minimum move as a
        # maximum. Scanning only to the left then missed the postfix form entirely,
        # so `a bullish move of 7.5% or less` fell through to the "at least"
        # convention below and compiled the ceiling as a floor.
        found = find_comparator_for_value(text, percentage.start(), percentage.end())
        if found is not None:
            return found[0]
    if percentages:
        # A `close > open` elsewhere in the sentence establishes direction, not
        # the comparator for the percentage threshold.
        return None
    return detect_comparator(text)


def _formula_direction(
    text: str,
    default_direction: StrategyDirection,
    *,
    threshold_at: int | None = None,
) -> FormulaDirection:
    if re.search(r"\(\s*open\s*-\s*close\s*\)\s*/\s*open", text):
        return "down"
    if re.search(r"\(\s*close\s*-\s*open\s*\)\s*/\s*open", text):
        return "signed"
    # The move's own direction word wins, read nearest-first from the threshold so a
    # direction stated in a neighbouring clause cannot claim this one. Falls back to
    # the stated trade side, then to the caller's default.
    movement = (
        movement_direction_before(text, threshold_at)
        if threshold_at is not None
        else movement_direction(text)
    )
    if movement is not None:
        return movement
    side = stated_side(text)
    if side is not None:
        return side
    return "down" if default_direction is StrategyDirection.SHORT else "up"


def _is_open_to_close(text: str) -> bool:
    if re.search(r"\bopen_to_(?:close|current|low|high)\b", text):
        return True
    if re.search(r"\bopen[\s-]+to[\s-]+(?:close|current|low|high)\b", text):
        return True
    if re.search(
        r"\(\s*(?:\d{1,2}\s*(?:m|h|d|w)\s+)?(?:close|current|open)"
        r"\s*-\s*(?:\d{1,2}\s*(?:m|h|d|w)\s+)?(?:open|close|current)"
        r"\s*\)\s*/\s*(?:\d{1,2}\s*(?:m|h|d|w)\s+)?open",
        text,
    ):
        return True
    return bool(
        re.search(
            r"(?:open\s*(?:to|->|→)\s*(?:close|current|low|high))|"
            r"\(\s*(?:close|current|open)\s*-\s*(?:open|close|current)\s*\)\s*/\s*open|"
            r"(?:close|current)\s*/\s*open\s*-\s*1|"
            r"(?:trigger\s+)?candle\s+open",
            text,
        )
    )


def _is_close_to_close(text: str) -> bool:
    if re.search(r"\bclose_to_close\b", text):
        return True
    if re.search(r"\bclose[\s-]+to[\s-]+close\b", text):
        return True
    return bool(
        re.search(
            r"close[\s-]*(?:to|->|→)[\s-]*close|"
            r"(?:latest|current|now)[_\s]*close\s*/\s*(?:prev|previous)[_\s]*close|"
            r"(?:close_now|current_close)\s*-\s*(?:close_prev|previous_close)|"
            r"immediately\s+previous\s+\w*\s*close|"
            r"versus\s+(?:the\s+)?(?:immediately\s+)?previous\s+\w*\s*close",
            text,
        )
    )


def _is_high_to_low(text: str) -> bool:
    if re.search(r"\bhigh_to_low\b", text):
        return True
    if re.search(r"\bhigh[\s-]+to[\s-]+low\b", text):
        return True
    return bool(
        re.search(r"\bhigh\s*(?:to|->|→)\s*low\b|\(\s*high\s*-\s*low\s*\)\s*/\s*high", text)
    )


def _is_reference_move(text: str) -> bool:
    return bool(
        re.search(
            r"\breference_to_current\b|"
            r"(?:from|measured\s+from).{0,60}(?:swing|local|lookback|previous|prior).{0,30}"
            r"(?:high|low|level)|"
            r"\(\s*(?:swinghigh|swing_high|reference)\s*-\s*"
            r"(?:current|close|low|high|onehourlow|one_hour_low)\s*\)",
            text,
        )
    )


def _current_field(text: str, *, default: str) -> str:
    if re.search(r"\b(?:current|one\s*hour|1h)\s+low\b|\bonehourlow\b", text):
        return "low"
    if re.search(r"\b(?:current|one\s*hour|1h)\s+high\b", text):
        return "high"
    if "close" in text:
        return "close"
    return default


#: Bars searched for a swing reference when the trader names no window.
_REFERENCE_SWING_WINDOW = 20


def _resolved(spec: PercentageFormulaSpec, text: str) -> PercentageFormulaSpec:
    """Apply the window the trader stated, whichever branch built the spec.

    Every ``return`` in :func:`parse_percentage_formula` goes through here. The window
    used to be read on one branch out of six, so ``price moved up 2% over the last 3
    candles`` compiled a one-candle rule: the ``3`` was read by nobody on that path and
    the dataclass default silently took its place. Putting the read in one place means
    a branch cannot forget it, and a branch added later inherits it.

    The spec keeps whatever default its branch chose when the trader stated no window,
    so the platform convention stays visible at the point that chooses it.

    The window is converted on the timeframe it is *measured against*, not the one the
    rule fires on. `grew 5% or more today` anchors to the daily open, so `today` is one
    reference candle; converting it on the 15m trigger timeframe instead made it 96 and
    changed which bar the comparison started from.
    """
    reading = read_lookback(text, timeframe=spec.reference_timeframe or spec.timeframe)
    if reading is None:
        return spec
    return replace(spec, lookback=reading.candles)


def _formula_label(spec: PercentageFormulaSpec) -> str:
    measurement = {
        "open_to_close": "open-to-close",
        "close_to_close": "close-to-close",
        "high_to_low": "high-to-low",
        "reference_to_current": f"{spec.reference_field}-to-{spec.current_field}",
    }[spec.formula]
    return (
        f"{measurement.title()} {spec.direction} move "
        f"{spec.comparator.value} {spec.threshold_percent:g}%"
    )
