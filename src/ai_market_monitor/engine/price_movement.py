"""The one vocabulary for which way a price moved.

Two readers independently decided what ``drops``, ``down`` and ``bearish`` mean, and
each understood a different subset. ``price drops at least 3%`` compiled as an *up*
move because one reader's list held ``drop`` but not ``drops``, and ``down move no
more than 1.25%`` compiled as *up* because neither list held ``down`` at all. A
monitor built from either one alerts on the opposite of what the trader asked for.

The table lives here, on its own, so every reader shares the same words and the same
resolution rule. It has no dependencies, so any module can import it.

Resolution follows the same principle the comparison vocabulary uses: the wording
that governs a number is the wording *nearest to its left*. In
``(close > open) AND (bullish % change <= 2.5%)`` the ``close > open`` fixes the
candle body and ``bullish`` fixes the move, and only proximity tells them apart.
"""

from __future__ import annotations

import re
from typing import Literal

Movement = Literal["up", "down"]

#: Words that state an upward move, with their inflections. Kept explicit rather
#: than stemmed so a new word is a deliberate, reviewable addition.
UP_TERMS: tuple[str, ...] = (
    "bullish",
    "upside",
    "upward",
    "up",
    "gain",
    "gains",
    "gained",
    "grow",
    "grows",
    "grew",
    "growth",
    "increase",
    "increases",
    "increasing",
    "increased",
    "rise",
    "rises",
    "rising",
    "rose",
    "risen",
    "rally",
    "rallies",
    "rallied",
    "jump",
    "jumps",
    "jumped",
    "surge",
    "surges",
    "surged",
    "pump",
    "pumps",
    "pumped",
    "climb",
    "climbs",
    "climbed",
    "advance",
    "advances",
    "advanced",
    "appreciate",
    "appreciates",
    "appreciated",
    # Arabic. Listed as whole inflected forms rather than stems, for the same reason
    # the English terms are: a new word should be a deliberate, reviewable addition.
    "صعد",
    "صعدت",
    "صعود",
    "يصعد",
    "ارتفع",
    "ارتفعت",
    "يرتفع",
    "ارتفاع",
    "طلع",
    "طلعت",
    "يطلع",
    "زاد",
    "زادت",
    "يزيد",
    "زيادة",
    "صاعد",
    "صاعدة",
    "شراء",
    "شرائي",
    # Arabizi (Franco-Arabic). Traders write these far more often than Arabic script.
    "tele3",
    "tel3et",
    "tala3",
    "tal3et",
    "yetla3",
    "yetlaa3",
    "irtafa3",
    "yertafe3",
    "zad",
    "zayed",
    "sa3ed",
    "sa3da",
    "bullish",
    # More Arabic forms of "rises".
    "ترتفع",
    "يرتفع",
    "ارتفاع",
    "صعود",
    "صعدت",
    "يصعد",
    # French.
    "hausse",
    "monte",
    "montent",
    "monter",
    "augmente",
    "augmentent",
    "augmenter",
    "grimpe",
    "grimpent",
    # Spanish.
    "sube",
    "suben",
    "suba",
    "subida",
    "aumenta",
    "aumente",
    "aumentan",
    # Russian.
    "вырос",
    "выросла",
    "выросли",
    "растет",
    "растёт",
    "рост",
    "поднялся",
    "поднялась",
    "повысился",
    "повысилась",
)

#: Words that state a downward move, with their inflections.
DOWN_TERMS: tuple[str, ...] = (
    "bearish",
    "downside",
    "downward",
    "down",
    "lose",
    "loses",
    "losing",
    "lost",
    "loss",
    "losses",
    "fall",
    "falls",
    "falling",
    "fell",
    "fallen",
    "drop",
    "drops",
    "dropping",
    "dropped",
    "decline",
    "declines",
    "declining",
    "declined",
    "decrease",
    "decreases",
    "decreasing",
    "decreased",
    "dump",
    "dumps",
    "dumped",
    "crash",
    "crashes",
    "crashed",
    "plunge",
    "plunges",
    "plunged",
    "sink",
    "sinks",
    "sank",
    "retrace",
    "retraces",
    "retraced",
    "pullback",
    "sell-off",
    "selloff",
    "depreciate",
    "depreciates",
    "depreciated",
    # Arabic.
    "نزل",
    "نزلت",
    "ينزل",
    "نزول",
    "هبط",
    "هبطت",
    "يهبط",
    "هبوط",
    "انخفض",
    "انخفضت",
    "ينخفض",
    "انخفاض",
    "خسر",
    "خسرت",
    "يخسر",
    "خسارة",
    "طاح",
    "طاحت",
    "هابط",
    "هابطة",
    "بيع",
    "بيعي",
    # Arabizi.
    "nezel",
    "nezlet",
    "yenzel",
    "nazel",
    "habat",
    "yehbot",
    "habet",
    "khesr",
    "khesret",
    "ta7",
    "ta7et",
    "enkhafad",
    "bearish",
    # More Arabic forms of "falls".
    "تنخفض",
    "هبوط",
    "هبطت",
    "يهبط",
    # French.
    "baisse",
    "baissent",
    "baisser",
    "diminue",
    "diminuent",
    "diminuer",
    "chute",
    "chutent",
    "chuter",
    # Spanish.
    "baja",
    "bajan",
    "baje",
    "bajada",
    "cae",
    "caen",
    "caiga",
    "caída",
    "disminuye",
    "disminuya",
    # Russian.
    "снизился",
    "снизилась",
    "снижается",
    "падает",
    "падение",
    "упал",
    "упала",
    "упали",
)

_MOVEMENT_BY_TERM: dict[str, Movement] = {
    **{term: "up" for term in UP_TERMS},
    **{term: "down" for term in DOWN_TERMS},
}

#: A word boundary that holds for every script this vocabulary covers, not just Latin.
#: `[a-z]` alone does not bound an Arabic term, so `نزل` would have matched inside
#: longer words that merely contain those letters — and the same is true of Cyrillic
#: (`упал` inside `упало`) and of accented Latin (`caída`, `chuté`).
_WORD_LEFT = r"(?<![a-z؀-ۿЀ-ӿà-ÿ])"
_WORD_RIGHT = r"(?![a-z؀-ۿЀ-ӿà-ÿ])"

#: Verbs that make ``up`` or ``down`` part of a phrasal verb rather than a direction.
#: ``set up a scanner`` states no market move, and reading the ``up`` inside it as one
#: classified a scan request as a percentage-move formula. Only the short, ambiguous
#: particles need this guard; ``bullish`` and ``sell-off`` cannot be swallowed this way.
PHRASAL_PARTICLE_GUARD = (
    r"(?<!\bset )(?<!\bsets )(?<!\bsetting )(?<!\bback )(?<!\bgive )(?<!\bgiving )"
    r"(?<!\bgave )(?<!\bgiven )(?<!\bend )(?<!\bends )(?<!\bended )(?<!\bsum )"
    r"(?<!\bsums )(?<!\bwrap )(?<!\bwraps )(?<!\bfollow )(?<!\bfollows )"
    r"(?<!\bcatch )(?<!\bkeep )(?<!\bkeeps )(?<!\bhold )(?<!\bholds )"
    r"(?<!\bopen )(?<!\bopens )(?<!\bopened )(?<!\bpick )(?<!\bpicks )"
    r"(?<!\bcome )(?<!\bcomes )(?<!\bshow )(?<!\bshows )(?<!\bmix )"
)

#: Terms short enough to sit inside a phrasal verb. Anything longer is unambiguous.
_PARTICLE_TERMS = frozenset({"up", "down"})


def _term_pattern(term: str) -> str:
    guard = PHRASAL_PARTICLE_GUARD if term.casefold() in _PARTICLE_TERMS else ""
    return rf"{guard}{_WORD_LEFT}{re.escape(term)}{_WORD_RIGHT}"


#: Longest term first so ``sell-off`` is never read as the ``sell`` inside it and
#: ``downside`` is never read as ``down``.
_MOVEMENT_RE = re.compile(
    "|".join(_term_pattern(term) for term in sorted(_MOVEMENT_BY_TERM, key=len, reverse=True)),
    re.IGNORECASE,
)

#: Wording that names a *direction of trade* rather than a move. It settles the
#: default side when the text states no move of its own.
_SIDE_RE = re.compile(
    r"\bdirection\s*[:=]?\s*(?P<side>long|short)\b|\b(?P<bare>long|short)\s+only\b",
    re.IGNORECASE,
)


#: The vocabulary as a regex alternation, for callers that must embed it in a larger
#: pattern. Exposed so they share this exact word list instead of hand-writing a
#: subset that drifts away from it.
MOVEMENT_PATTERN: str = "|".join(
    re.escape(term) for term in sorted(_MOVEMENT_BY_TERM, key=len, reverse=True)
)


def movement_direction(text: str) -> Movement | None:
    """The first stated direction of movement, or ``None`` when the text states none."""
    match = _MOVEMENT_RE.search(text or "")
    if match is None:
        return None
    return _MOVEMENT_BY_TERM[match.group(0).casefold()]


def movement_direction_before(text: str, position: int) -> Movement | None:
    """The direction that governs the value at ``position`` — the nearest to its left.

    Falls back to the first direction stated anywhere, so wording that puts the
    direction after the number (``7.5% to the downside``) still resolves.
    """
    nearest: Movement | None = None
    for match in _MOVEMENT_RE.finditer(text or ""):
        if match.start() >= position:
            break
        nearest = _MOVEMENT_BY_TERM[match.group(0).casefold()]
    if nearest is not None:
        return nearest
    return movement_direction(text)


def stated_side(text: str) -> Movement | None:
    """``long``/``short`` read as an intended move direction, or ``None``."""
    match = _SIDE_RE.search(text or "")
    if match is None:
        return None
    side = (match.group("side") or match.group("bare") or "").casefold()
    if side == "long":
        return "up"
    if side == "short":
        return "down"
    return None
