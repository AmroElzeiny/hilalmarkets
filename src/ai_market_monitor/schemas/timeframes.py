"""The timeframes this platform supports. One list, and everything else derived.

The list used to exist twice: as a set in ``engine.timeframes`` and as a regular
expression in ``schemas.strategy``. They happened to agree, but nothing made them agree —
adding a timeframe to one and not the other gives a value that one reader accepts and
another refuses, which is a turn that fails for a reason the user cannot see.

This module has no imports of its own and lives under ``schemas`` so that both the schema
layer and the engine layer can read it. ``engine.timeframes`` re-exports every name, so
existing importers are unchanged.
"""

from __future__ import annotations

import re
from enum import StrEnum


class TimeframeChoice(StrEnum):
    """Every timeframe a strategy may be evaluated on."""

    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H2 = "2h"
    H4 = "4h"
    H6 = "6h"
    H8 = "8h"
    H12 = "12h"
    D1 = "1d"


#: Minutes per closed candle, used to convert a wall-clock window into a bar count.
TIMEFRAME_MINUTES: dict[str, int] = {
    TimeframeChoice.M1.value: 1,
    TimeframeChoice.M3.value: 3,
    TimeframeChoice.M5.value: 5,
    TimeframeChoice.M15.value: 15,
    TimeframeChoice.M30.value: 30,
    TimeframeChoice.H1.value: 60,
    TimeframeChoice.H2.value: 120,
    TimeframeChoice.H4.value: 240,
    TimeframeChoice.H6.value: 360,
    TimeframeChoice.H8.value: 480,
    TimeframeChoice.H12.value: 720,
    TimeframeChoice.D1.value: 1440,
}

#: Every supported timeframe, shortest first. Ordering matters wherever a
#: higher-timeframe rule has to be told apart from a lower-timeframe one.
ORDERED_TIMEFRAMES: tuple[str, ...] = tuple(
    sorted((item.value for item in TimeframeChoice), key=lambda item: TIMEFRAME_MINUTES[item])
)

#: Set form, for membership tests. Derived, never re-listed.
SUPPORTED_TIMEFRAMES: set[str] = set(ORDERED_TIMEFRAMES)

# Word forms accepted at the language boundary.  They deliberately live beside the
# canonical list so the planner validator and the deterministic text reader cannot
# drift apart.  Exact user wording remains in the verified PlannerSegment; this map
# changes representation, never provenance or semantic role.
WORD_TIMEFRAME_ALIASES: dict[str, str] = {
    "hourly": TimeframeChoice.H1.value,
    "one hour": TimeframeChoice.H1.value,
    "four hour": TimeframeChoice.H4.value,
    "daily": TimeframeChoice.D1.value,
    "one day": TimeframeChoice.D1.value,
}

_TIMEFRAME_TEXT_RE = re.compile(
    r"^(\d{1,4})\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$",
    re.IGNORECASE,
)


def normalize_timeframe_alias(value: str) -> str | None:
    """Return the supported canonical timeframe represented by ``value``.

    This accepts semantic aliases at the model boundary (for example ``60m``,
    ``24h``, ``daily`` and ``four-hour``) without expanding the executable
    timeframe set.  Unsupported durations still fail closed.
    """

    collapsed = " ".join(value.strip().casefold().replace("-", " ").split())
    if collapsed in SUPPORTED_TIMEFRAMES:
        return collapsed
    if collapsed in WORD_TIMEFRAME_ALIASES:
        return WORD_TIMEFRAME_ALIASES[collapsed]
    match = _TIMEFRAME_TEXT_RE.fullmatch(collapsed)
    if match is None:
        return None
    amount = int(match.group(1))
    unit = match.group(2).casefold()
    if unit.startswith(("m", "min")):
        minutes = amount
    elif unit.startswith(("h", "hr", "hour")):
        minutes = amount * 60
    else:
        minutes = amount * 1440
    return next(
        (candidate for candidate, size in TIMEFRAME_MINUTES.items() if size == minutes),
        None,
    )
