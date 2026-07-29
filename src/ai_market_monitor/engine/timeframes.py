"""The timeframes this platform supports, and what each one is worth in minutes.

Kept in its own module with no imports of its own. It used to live in
``prompt_semantics``, which meant every reader that needed only this table had to
depend on the whole semantic parser — and a new shared reader could not be imported
back into it without a cycle.

``prompt_semantics`` re-exports both names, so existing importers are unaffected.
"""

from __future__ import annotations

#: Every timeframe a strategy may be evaluated on.
SUPPORTED_TIMEFRAMES: set[str] = {
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "6h",
    "8h",
    "12h",
    "1d",
}

#: Minutes per closed candle, used to convert a wall-clock window into a bar count.
TIMEFRAME_MINUTES: dict[str, int] = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
}
