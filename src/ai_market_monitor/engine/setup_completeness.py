"""Deterministic completeness check for a described strategy setup.

Evaluator run ``20260725T122105Z`` captured a structured strategy object in 1 of 42
cases. The cause was not a compiler failure: the interviewer model owns the
``ready_to_compile`` flag, so a session could hold every requirement the compiler
needs and still loop on clarification questions forever, never producing a draft.

This module answers one question without a model call: *does the accumulated setup
text already specify the fields the compiler requires?* When it does, the caller
compiles and returns the inactive draft. Compiling early is safe because the
compiler fails closed — wording it cannot represent surfaces as an unresolved
condition and blocking lint, so an incomplete draft is never approvable.

Nothing here decides approval. It only decides whether a draft must exist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ai_market_monitor.engine.turn_fragments import (
    classify_turn,
    extract_timeframes,
)
from ai_market_monitor.schemas.strategy import StrategyDirection

RequirementField = Literal["universe", "timeframe", "trigger_condition"]

#: Fields the compiler cannot invent a sound default for. Direction, trigger mode
#: and delivery all have documented defaults, so they are never required here.
REQUIRED_FIELDS: tuple[RequirementField, ...] = (
    "universe",
    "timeframe",
    "trigger_condition",
)

REQUIREMENT_LABELS: dict[RequirementField, str] = {
    "universe": "which markets to watch",
    "timeframe": "which timeframe to evaluate",
    "trigger_condition": "the market event to detect",
}

UniverseScope = Literal["explicit_symbols", "market_wide", "curated_list", "screened"]

#: Wording that scopes the universe without naming individual symbols. Each entry is
#: matched as a whole phrase so ``all coins`` counts while a bare ``all`` does not.
_MARKET_WIDE_PATTERNS: tuple[str, ...] = (
    r"\ball\s+(?:the\s+)?(?:coins?|pairs?|symbols?|assets?|markets?|tickers?)\b",
    r"\bevery\s+(?:coin|pair|symbol|asset|market|ticker)\b",
    r"\bany\s+(?:coin|pair|symbol|asset|market|ticker)\b",
    r"\b(?:whole|entire|full)\s+market\b",
    r"\bmarket[\s-]?wide\b",
    r"\bacross\s+(?:the\s+)?market\b",
    r"\btop\s+\d+\b",
    r"\ball\s+[a-z]{2,10}\s+pairs?\b",
    r"\bwhatever\s+(?:coin|pair|market)s?\b",
)

_CURATED_LIST_PATTERNS: tuple[str, ...] = (
    r"\bmy\s+(?:favou?rites?|watchlist|list|portfolio|holdings)\b",
    r"\bfavou?rites?\s+(?:list|only)\b",
    r"\bsaved\s+(?:list|watchlist|coins?|pairs?)\b",
)

_SCREENED_PATTERNS: tuple[str, ...] = (
    r"\bscreened\s+(?:universe|market|list)\b",
    r"\b(?:halal|shariah?|sharia)[\s-]?(?:compliant|screened|universe|only)\b",
    r"\bcompliant\s+(?:coins?|pairs?|assets?|universe)\b",
)

_MARKET_WIDE_RE = re.compile("|".join(_MARKET_WIDE_PATTERNS), re.IGNORECASE)
_CURATED_LIST_RE = re.compile("|".join(_CURATED_LIST_PATTERNS), re.IGNORECASE)
_SCREENED_RE = re.compile("|".join(_SCREENED_PATTERNS), re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SetupRequirements:
    """What the accumulated setup text specifies, and what it still lacks."""

    satisfied: frozenset[RequirementField]
    missing: tuple[RequirementField, ...]
    universe_scope: UniverseScope | None = None
    symbols: tuple[str, ...] = ()
    excluded_symbols: tuple[str, ...] = ()
    timeframes: tuple[str, ...] = ()
    trigger_fragments: tuple[str, ...] = ()
    direction: StrategyDirection | None = None

    @property
    def is_complete(self) -> bool:
        """True when every compiler-required field is present in the description."""
        return not self.missing

    @property
    def missing_labels(self) -> tuple[str, ...]:
        return tuple(REQUIREMENT_LABELS[field] for field in self.missing)


def detect_universe_scope(text: str) -> UniverseScope | None:
    """Classify how ``text`` scopes the market universe, if it does at all."""
    if _SCREENED_RE.search(text):
        return "screened"
    if _CURATED_LIST_RE.search(text):
        return "curated_list"
    if _MARKET_WIDE_RE.search(text):
        return "market_wide"
    return None


def assess_setup_requirements(
    text: str,
    *,
    known_fields: frozenset[RequirementField] = frozenset(),
) -> SetupRequirements:
    """Report which compiler-required fields ``text`` specifies.

    ``known_fields`` carries requirements already settled outside the free text —
    a timeframe picked in guided setup, or a screened universe chosen through the
    application's own selection flow. They count as satisfied because the compiler
    genuinely has those values, not because the wording implied them.
    """
    report = classify_turn(text)
    symbols = report.symbols
    scope: UniverseScope | None = "explicit_symbols" if symbols else detect_universe_scope(text)
    timeframes = extract_timeframes(text)
    triggers = tuple(
        item.text
        for item in report.fragments
        if item.enters_capability_resolution or item.category == "FORMULA"
    )

    present: set[RequirementField] = set(known_fields)
    if scope is not None:
        present.add("universe")
    if timeframes:
        present.add("timeframe")
    if triggers:
        present.add("trigger_condition")

    satisfied = frozenset(field for field in REQUIRED_FIELDS if field in present)
    return SetupRequirements(
        satisfied=satisfied,
        missing=tuple(field for field in REQUIRED_FIELDS if field not in satisfied),
        universe_scope=scope,
        symbols=symbols,
        excluded_symbols=report.excluded_symbols,
        timeframes=timeframes,
        trigger_fragments=triggers,
        direction=report.direction,
    )
