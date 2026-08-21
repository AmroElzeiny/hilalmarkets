"""A card that asks the trader for a number must be judged against *that* number.

``correlation_filter`` asked "how closely must this coin move with Bitcoin?" and offered
0.7 as a starting point. Whatever number the trader typed, the answer was decided
against 0.7, because:

* the producer looked its threshold up under a **sibling's** key, so the trader's number
  never arrived and the built-in 0.7 was always used; and
* the producer then handed the rule a **yes/no** — already decided — which the
  comparator went on to compare with the trader's number as if it were a number.
  ``True >= 0.95`` is true in Python, so the yes survived any threshold at all.

Measured on real correlation of 0.82: a monitor set to "at least 0.95" fired, and a
monitor set to "at least 0.3" would have stayed silent at a measured 0.5. Both are the
product answering a question the person did not ask.

The rule asserted here is the general one: **the value that reaches a numeric
comparison must be a measurement, never a verdict.** A yes/no cannot carry a threshold,
so a card offering one and receiving one is always this bug.
"""

from __future__ import annotations

import asyncio

import pytest

from ai_market_monitor.engine.builder_operations import mechanic_catalog
from tests.unit.test_invariant_offered_card_has_a_producer import _evaluate

#: Comparators that put the reading and the trader's number on a number line.
NUMERIC_COMPARATORS = {"gt", "gte", "lt", "lte"}


def _asks_for_a_number(mechanic: object) -> bool:
    return any(
        parameter.name == "threshold" and parameter.kind == "number"
        for parameter in mechanic.parameters  # type: ignore[attr-defined]
    )


OFFERED = [mechanic for mechanic in mechanic_catalog() if mechanic.available]
NUMBER_CARDS = [mechanic.key for mechanic in OFFERED if _asks_for_a_number(mechanic)]


def test_some_cards_ask_the_trader_for_a_number() -> None:
    """Guards this file against passing because it found nothing to check."""

    assert len(NUMBER_CARDS) > 20


@pytest.mark.parametrize("key", NUMBER_CARDS)
def test_a_card_that_asks_for_a_number_is_given_a_measurement(key: str) -> None:
    """The engine must receive something a threshold can actually be applied to."""

    leaf = asyncio.run(_evaluate(key))
    if leaf.operator not in NUMERIC_COMPARATORS:
        return
    assert not isinstance(leaf.actual_value, bool), (
        f"{key} asks the trader for a number and compares with {leaf.operator}, but the "
        f"engine was handed the yes/no {leaf.actual_value!r}. A yes/no cannot be "
        "measured against a threshold; whatever number the trader typed is discarded."
    )


@pytest.mark.parametrize("key", NUMBER_CARDS)
def test_the_verdict_agrees_with_the_card_s_own_numbers(key: str) -> None:
    """Passed or failed must be what the reading and the threshold actually say."""

    leaf = asyncio.run(_evaluate(key))
    if leaf.operator not in NUMERIC_COMPARATORS:
        return
    actual, required = leaf.actual_value, leaf.required_value
    if isinstance(actual, bool) or isinstance(required, bool):
        return
    if not isinstance(actual, int | float) or not isinstance(required, int | float):
        return
    if leaf.state.value not in {"passed", "failed"}:
        return
    expected = {
        "gt": actual > required,
        "gte": actual >= required,
        "lt": actual < required,
        "lte": actual <= required,
    }[leaf.operator]
    assert expected is (leaf.state.value == "passed"), (
        f"{key} says {leaf.state.value} for {actual!r} {leaf.operator} {required!r}"
    )
