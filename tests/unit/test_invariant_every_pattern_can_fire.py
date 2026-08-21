"""A pattern nobody can ever trigger is not a pattern.

``detect_candle_pattern`` decides how many candles a pattern needs, then hands it to the
one-candle, two-candle, three-candle or five-candle reader. The list it decided from was
written out by hand inside that function — a second copy of what ``_single_pattern``
already knew how to read — and the two drifted.

``green_candle`` was on the hand-written list. ``bullish_candle`` is the identical
reading, ``close > open``, and it was not. So ``bullish_candle`` and ``bearish_candle``
were sent to the two-candle reader, which has no branch for either name, and quietly
answered **no**. Every monitor built on "the candle closed up" was silent on every candle
of every coin, for ever, while "green candle" sat beside it answering correctly.

Nothing could see it. The card was offered, it compiled, it evaluated, it returned a
real boolean and never errored — it was simply always the same boolean.

So this file asks the only question that catches it: show the detector every shape a
candle can have, and check it says yes at least once. A reader that cannot be reached is
found whatever the reason — a routing list that forgot it, a branch that was deleted, a
condition that can never hold.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from ai_market_monitor.engine.candle_patterns import (
    SINGLE_CANDLE_PATTERNS,
    detect_candle_pattern,
    pattern_names,
)
from ai_market_monitor.engine.indicators import IndicatorWarmupError
from ai_market_monitor.services.interfaces import Candle

_START = datetime(2026, 1, 1, tzinfo=UTC)

#: Enough shapes that the rarest pattern here — a kicking pattern, which needs a gap
#: between two full-bodied candles of opposite colour — is found many times over. Fixed
#: seed, so this either always passes or always fails; it never flickers.
_SEQUENCES = 20_000
_SEED = 20260821

#: Bodies, gaps and wicks spanning nothing-at-all to very large, so doji, marubozu,
#: pin bars and gap patterns are all reachable from the same generator.
_BODIES = (0.0, 0.0005, 0.002, 0.01, 0.03, 0.08)
_GAPS = (0.0, 0.0, 0.01, -0.01, 0.04, -0.04)
_WICKS = (0.0, 0.0002, 0.002, 0.01, 0.05, 0.12)

#: Settings a trader can send. Included because a pattern can also be made unreachable
#: by a threshold no candle can satisfy, not only by bad routing.
_SETTINGS = (
    {},
    {"pattern_strength": "weak"},
    {"min_body_percent": 0, "max_body_percent": 100, "wick_ratio": 0.1},
    {"min_body_percent": 25, "max_body_percent": 40, "wick_ratio": 2},
    {"tolerance_percent": 5},
    {"lookback": 3},
)


def _sequence(generator: random.Random, length: int = 12) -> list[Candle]:
    price = 100.0
    rows: list[Candle] = []
    for index in range(length):
        body = generator.choice(_BODIES) * generator.choice((-1, 1))
        open_price = price * (1 + generator.choice(_GAPS))
        close = open_price * (1 + body)
        top, bottom = max(open_price, close), min(open_price, close)
        rows.append(
            Candle(
                timestamp=_START + timedelta(minutes=15 * index),
                open=open_price,
                high=max(top * (1 + generator.choice(_WICKS)), top),
                low=min(bottom * (1 - generator.choice(_WICKS)), bottom),
                close=close,
                volume=generator.uniform(100, 10_000),
                is_closed=True,
            )
        )
        price = close
    return rows


def _firing_counts() -> dict[str, int]:
    generator = random.Random(_SEED)
    counts = dict.fromkeys(pattern_names(), 0)
    for attempt in range(_SEQUENCES):
        candles = _sequence(generator)
        settings = _SETTINGS[attempt % len(_SETTINGS)]
        for name in counts:
            try:
                if detect_candle_pattern(name, candles, dict(settings)):
                    counts[name] += 1
            except IndicatorWarmupError:
                pass
    return counts


#: One search, shared by every case below. It is the expensive part of this file.
_COUNTS = _firing_counts()


def test_there_are_patterns_to_check() -> None:
    """Guards the cases below against passing because the list came back empty."""

    assert len(_COUNTS) >= 50


@pytest.mark.parametrize("name", sorted(pattern_names()))
def test_the_pattern_says_yes_to_something(name: str) -> None:
    """Across every shape a candle can take, a real detector matches at least once."""

    assert _COUNTS[name] > 0, (
        f"{name} never matched in {_SEQUENCES} sequences covering every body, wick and "
        "gap size. Nothing a market can do will trigger this card, so a monitor built "
        "on it is silent for ever."
    )


@pytest.mark.parametrize("name", sorted(pattern_names()))
def test_the_pattern_says_no_to_something(name: str) -> None:
    """A detector that matches everything is not filtering anything either."""

    assert _COUNTS[name] < _SEQUENCES, f"{name} matched every one of {_SEQUENCES} sequences"


@pytest.mark.parametrize("name", sorted(SINGLE_CANDLE_PATTERNS))
def test_a_one_candle_pattern_is_readable_from_one_candle(name: str) -> None:
    """The routing list and the reader agree, which is what drifted apart before."""

    assert name in pattern_names(), f"{name} is routed as a pattern but is not offered"
    generator = random.Random(_SEED)
    for _ in range(400):
        single = _sequence(generator, length=1)
        try:
            detect_candle_pattern(name, single, {})
        except IndicatorWarmupError as error:  # pragma: no cover - the failure itself
            pytest.fail(
                f"{name} is listed as a one-candle pattern but asked for more: {error}"
            )
