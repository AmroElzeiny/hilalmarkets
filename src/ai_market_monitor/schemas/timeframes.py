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

#: The periods a question offers as buttons. A shortlist for readability only — it is
#: never a restriction, because every timeframe above can still be typed and executed.
#: This list used to be written out by hand in three unrelated modules, so a trader
#: could be shown one set of periods and validated against another.
COMMON_TIMEFRAMES: tuple[str, ...] = (
    TimeframeChoice.H1.value,
    TimeframeChoice.H4.value,
    TimeframeChoice.D1.value,
)

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

#: Bare period words that mean a timeframe **only when they are the whole answer**.
#:
#: These are deliberately kept out of ``WORD_TIMEFRAME_ALIASES``, which free-text
#: scanning searches inside longer sentences. A message that is exactly "minute" names
#: the one-minute candle; the word "minute" inside "over the past week on five minute"
#: does not, and putting it in the scanned map made that sentence read as one minute.
ANSWER_TIMEFRAME_ALIASES: dict[str, str] = {
    "minute": TimeframeChoice.M1.value,
    "heure": TimeframeChoice.H1.value,
    "une heure": TimeframeChoice.H1.value,
    "horaire": TimeframeChoice.H1.value,
    "quotidien": TimeframeChoice.D1.value,
    "journalier": TimeframeChoice.D1.value,
    "un jour": TimeframeChoice.D1.value,
    "minuto": TimeframeChoice.M1.value,
    "hora": TimeframeChoice.H1.value,
    "una hora": TimeframeChoice.H1.value,
    "diario": TimeframeChoice.D1.value,
    "un dia": TimeframeChoice.D1.value,
    "минута": TimeframeChoice.M1.value,
    "час": TimeframeChoice.H1.value,
    "часовой": TimeframeChoice.H1.value,
    "день": TimeframeChoice.D1.value,
    "дневной": TimeframeChoice.D1.value,
    "сутки": TimeframeChoice.D1.value,
    "دقيقة": TimeframeChoice.M1.value,
    "دقيقه": TimeframeChoice.M1.value,
    "ساعة": TimeframeChoice.H1.value,
    "ساعه": TimeframeChoice.H1.value,
    "يوم": TimeframeChoice.D1.value,
    "يومي": TimeframeChoice.D1.value,
}

#: How a period unit is written in each language the product answers in. Keeping the
#: unit words next to the canonical list is what stops a second, poorer timeframe
#: reader from being written somewhere else the moment a non-English answer arrives.
_MINUTE_WORDS: tuple[str, ...] = (
    "m", "min", "mins", "minute", "minutes",
    "minuto", "minutos",
    "мин", "минута", "минуты", "минут",
    "د", "دقيقة", "دقيقه", "دقائق",
)
_HOUR_WORDS: tuple[str, ...] = (
    "h", "hr", "hrs", "hour", "hours",
    "heure", "heures", "hora", "horas",
    "ч", "час", "часа", "часов",
    "س", "ساعة", "ساعه", "ساعات",
)
_DAY_WORDS: tuple[str, ...] = (
    "d", "day", "days",
    "jour", "jours", "dia", "dias", "día", "días",
    "д", "день", "дня", "дней", "сутки",
    "ي", "يوم", "ايام", "أيام",
)

_UNIT_MINUTES: dict[str, int] = {
    **{word: 1 for word in _MINUTE_WORDS},
    **{word: 60 for word in _HOUR_WORDS},
    **{word: 1440 for word in _DAY_WORDS},
}

_TIMEFRAME_TEXT_RE = re.compile(
    r"^(\d{1,4})\s*(" + "|".join(sorted(_UNIT_MINUTES, key=len, reverse=True)) + r")$",
    re.IGNORECASE,
)

#: Arabic writes the same letter several ways and adds vowel marks that carry no
#: meaning for a period word. Folding them here keeps one reader instead of two.
_ARABIC_FOLD = str.maketrans(
    {
        "ـ": "",  # tatweel
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ى": "ي",
        "ة": "ه",
        **{chr(mark): "" for mark in range(0x064B, 0x0653)},
    }
)


def fold_timeframe_text(value: str) -> str:
    """One spelling for text that means a period, whatever alphabet wrote it."""

    collapsed = " ".join(value.strip().casefold().replace("-", " ").replace("_", " ").split())
    return collapsed.translate(_ARABIC_FOLD)


def normalize_timeframe_alias(value: str) -> str | None:
    """Return the supported canonical timeframe represented by ``value``.

    This accepts semantic aliases at the model boundary (for example ``60m``,
    ``24h``, ``daily``, ``four-hour``, ``1 minuto`` and ``ساعة``) without expanding
    the executable timeframe set.  Unsupported durations still fail closed.
    """

    collapsed = fold_timeframe_text(value)
    if collapsed in SUPPORTED_TIMEFRAMES:
        return collapsed
    folded_words = {
        fold_timeframe_text(key): item
        for key, item in (*WORD_TIMEFRAME_ALIASES.items(), *ANSWER_TIMEFRAME_ALIASES.items())
    }
    if collapsed in folded_words:
        return folded_words[collapsed]
    match = _TIMEFRAME_TEXT_RE.fullmatch(collapsed)
    if match is None:
        return None
    minutes = int(match.group(1)) * _UNIT_MINUTES[fold_timeframe_text(match.group(2))]
    return next(
        (candidate for candidate, size in TIMEFRAME_MINUTES.items() if size == minutes),
        None,
    )
