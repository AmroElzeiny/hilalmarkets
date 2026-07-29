"""One owner for "how far back does this instruction look?".

Six readers answered this question independently, and each understood a different
subset of the wording:

* ``formula_compiler._lookback`` — ``last|previous|lookback N candles`` only, default 20
* ``interpreter._lookback_candles`` — months/weeks/days/hours/minutes, default 100
* ``interpreter._percent_move`` — an inline ``(last|past) N candle`` scan, default 1
* ``interpreter._lookback_label`` — a fourth, for wording only
* ``interpreter._period_near`` — a fifth, for indicator periods
* ``prompt_semantics._lookback_candles`` — a sixth

Worse than the disagreement: ``PercentageFormulaSpec.lookback`` defaulted to ``1`` and
was only ever *set* on one of six compile branches. ``price moved up 2% over the last 3
candles`` therefore compiled a one-candle rule — the trader's ``3`` was read by nobody
on that path, and an invented ``1`` took its place with nothing to show it had.

This module is the single vocabulary and the single resolution rule. It returns
``None`` when the text states no window, so a caller must decide its default in the
open rather than inheriting one silently.

Nothing here calls a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ai_market_monitor.engine.timeframes import TIMEFRAME_MINUTES

#: The bar itself, in every spelling traders use, including Arabic and Arabizi.
CANDLE_TERMS: tuple[str, ...] = (
    "candle",
    "candles",
    "candlestick",
    "candlesticks",
    "bar",
    "bars",
    "shama",
    "shamaa",
    "شمعة",
    "شموع",
)

#: Wall-clock windows, with how many minutes one of each is worth.
_DURATION_MINUTES: dict[str, int] = {
    "minute": 1,
    "minutes": 1,
    "min": 1,
    "mins": 1,
    "hour": 60,
    "hours": 60,
    "hr": 60,
    "hrs": 60,
    "day": 1440,
    "days": 1440,
    "week": 10080,
    "weeks": 10080,
    "month": 43200,
    "months": 43200,
    "year": 525600,
    "years": 525600,
    "دقيقة": 1,
    "دقائق": 1,
    "ساعة": 60,
    "ساعات": 60,
    "يوم": 1440,
    "أيام": 1440,
    "ايام": 1440,
    "اسبوع": 10080,
    "أسبوع": 10080,
    "شهر": 43200,
    "شهور": 43200,
}

#: Words that introduce a backward-looking window. They are optional — `3 candles ago`
#: and `over 3 candles` mean the same as `over the last 3 candles`.
_WINDOW_MARKERS = (
    "last",
    "past",
    "previous",
    "prior",
    "recent",
    "trailing",
    "over",
    "within",
    "during",
    "in",
    "across",
    "آخر",
    "اخر",
    "خلال",
)

#: A bare count in words. `a`/`an` mean one.
_WORD_NUMBERS: dict[str, int] = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "twelve": 12,
    "twenty": 20,
    "thirty": 30,
    "fifty": 50,
    "hundred": 100,
}

#: Phrases meaning "since the start of today".
_TODAY_TERMS = (
    "today",
    "since midnight",
    "so far today",
    "daily move",
    "this day",
    "intraday",
    "النهارده",
    "اليوم",
)

#: The platform floor and ceiling for a window, in bars. A request outside this range
#: is refused by :func:`read_lookback` rather than clamped — clamping would monitor a
#: window the trader never asked for.
MIN_CANDLES = 1
MAX_CANDLES = 50_000

_MARKER_ALTERNATION = "|".join(re.escape(term) for term in _WINDOW_MARKERS)
_CANDLE_ALTERNATION = "|".join(
    re.escape(term) for term in sorted(CANDLE_TERMS, key=len, reverse=True)
)
_DURATION_ALTERNATION = "|".join(
    re.escape(term) for term in sorted(_DURATION_MINUTES, key=len, reverse=True)
)
_COUNT_ALTERNATION = r"\d{1,5}|" + "|".join(
    re.escape(word) for word in sorted(_WORD_NUMBERS, key=len, reverse=True)
)

#: Exposed so callers embed this exact vocabulary instead of hand-writing a subset.
LOOKBACK_PATTERN = (
    rf"(?:(?:{_MARKER_ALTERNATION})\s+)?(?:the\s+)?(?:{_COUNT_ALTERNATION})\s*"
    rf"[-\s]?(?:{_CANDLE_ALTERNATION}|{_DURATION_ALTERNATION})"
)

_CANDLE_RE = re.compile(
    rf"(?:(?:{_MARKER_ALTERNATION})\s+)?(?:the\s+)?"
    rf"(?P<count>{_COUNT_ALTERNATION})\s*[-\s]?(?:{_CANDLE_ALTERNATION})\b",
    re.IGNORECASE,
)

_DURATION_RE = re.compile(
    rf"(?:(?P<marker>{_MARKER_ALTERNATION})\s+)?(?:the\s+)?"
    rf"(?P<count>{_COUNT_ALTERNATION})?\s*[-\s]?(?P<unit>{_DURATION_ALTERNATION})\b",
    re.IGNORECASE,
)

#: A duration naming the *bar size* rather than a window: `a 1 minute candle`, `the 5
#: minute chart`, `on the 15 minute timeframe`. Reading these as windows made
#: `a 1 minute candle that had a value of 1% over the past week` search one bar instead
#: of the week the trader asked for — the timeframe was matched first and won.
_TIMEFRAME_DESCRIPTOR_RE = re.compile(
    r"\s*(?:candles?|candlesticks?|bars?|chart|charts|timeframe|time\s*frame|tf)\b",
    re.IGNORECASE,
)

_TODAY_RE = re.compile(
    "|".join(re.escape(term) for term in _TODAY_TERMS),
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class LookbackReading:
    """A window the trader actually stated, and the words that stated it."""

    candles: int
    #: The exact wording this was read from, so a caller can show its work.
    source: str
    #: ``candles`` when counted in bars, otherwise the duration unit it was converted
    #: from. A caller that must not convert can check this.
    unit: str

    @property
    def counted_in_candles(self) -> bool:
        return self.unit == "candles"


def _count_from(value: str | None) -> int | None:
    if not value:
        return None
    text = value.strip().casefold()
    if text.isdigit():
        return int(text)
    return _WORD_NUMBERS.get(text)


def _in_range(candles: int) -> bool:
    return MIN_CANDLES <= candles <= MAX_CANDLES


def read_lookback(text: str, *, timeframe: str) -> LookbackReading | None:
    """How many closed bars the text asks to look back over, or ``None``.

    ``None`` means *the trader stated no window*. It never means "use one bar": a
    caller that needs a value must choose its default visibly, so that the choice can
    be recorded as an assumption instead of passing for something the trader said.

    A count of bars wins over a wall-clock duration, because `over the last 3 candles
    on the 1h` states the window in bars directly and converting the `1h` as well would
    read the timeframe twice.
    """
    if not text:
        return None
    minutes = TIMEFRAME_MINUTES.get(timeframe, 15)

    candle_match = _CANDLE_RE.search(text)
    if candle_match:
        count = _count_from(candle_match.group("count"))
        if count is not None and _in_range(count):
            return LookbackReading(
                candles=count,
                source=candle_match.group(0).strip(),
                unit="candles",
            )

    for match in _DURATION_RE.finditer(text):
        unit = (match.group("unit") or "").casefold()
        per_unit = _DURATION_MINUTES.get(unit)
        if per_unit is None:
            continue
        # A duration only states a *window* when something introduces it as one. On its
        # own it names the bar size — `a 1 minute candle`, `the 4 hour chart`, `on the
        # 1h` — and reading that as "look back one minute" searched a single bar for a
        # week-long request.
        if not match.group("marker"):
            continue
        if _TIMEFRAME_DESCRIPTOR_RE.match(text, match.end()):
            continue
        count = _count_from(match.group("count")) or 1
        candles = int((count * per_unit) / minutes)
        if _in_range(candles):
            return LookbackReading(
                candles=candles,
                source=match.group(0).strip(),
                unit=unit,
            )

    today = _TODAY_RE.search(text)
    if today:
        candles = int(1440 / minutes)
        if _in_range(candles):
            return LookbackReading(candles=candles, source=today.group(0), unit="day")
    return None


def read_lookback_before(
    text: str,
    position: int,
    *,
    timeframe: str,
) -> LookbackReading | None:
    """The window stated to the left of ``position``, inside the clause that owns it.

    Same resolution rule the comparator and direction readers use: the window that
    governs a number is the nearest one stated before it, not whichever appears first
    in the whole message.
    """
    if position <= 0:
        return read_lookback(text, timeframe=timeframe)
    return read_lookback(text[:position], timeframe=timeframe) or read_lookback(
        text, timeframe=timeframe
    )
