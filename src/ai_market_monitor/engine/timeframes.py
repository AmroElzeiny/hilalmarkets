"""The timeframes this platform supports, and what each one is worth in minutes.

The vocabulary itself now lives in :mod:`ai_market_monitor.schemas.timeframes`, which has
no imports of its own. It had to move: ``schemas.strategy`` needs the same list to build
its ``Timeframe`` pattern, and importing anything under ``engine`` pulls in
``engine/__init__``, which imports the evaluator and everything below it — a cycle.

Every name is re-exported here, so importers of ``engine.timeframes`` are unaffected.
``prompt_semantics`` re-exports them in turn.
"""

from __future__ import annotations

from ai_market_monitor.schemas.timeframes import (
    ORDERED_TIMEFRAMES,
    SUPPORTED_TIMEFRAMES,
    TIMEFRAME_MINUTES,
    WORD_TIMEFRAME_ALIASES,
    TimeframeChoice,
    normalize_timeframe_alias,
)

__all__ = [
    "ORDERED_TIMEFRAMES",
    "SUPPORTED_TIMEFRAMES",
    "TIMEFRAME_MINUTES",
    "TimeframeChoice",
    "WORD_TIMEFRAME_ALIASES",
    "normalize_timeframe_alias",
]
