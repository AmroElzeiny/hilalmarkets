"""The shared comparison-and-level reader.

Every indicator that accepts a user-supplied level used to read it with its own
regex, and each of those regexes had the same three defects: a four-phrase operator
vocabulary, no clause boundary, and a hardcoded fallback level. Fixing them one
indicator at a time leaves the next one broken, so the reading lives in one place
and these cases pin it there.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.numeric_clause import clause_for, read_level
from ai_market_monitor.schemas.strategy import Comparator

GT = Comparator.GREATER_THAN
GTE = Comparator.GREATER_THAN_OR_EQUAL
LT = Comparator.LESS_THAN
LTE = Comparator.LESS_THAN_OR_EQUAL

#: The rule under test is about wording, not about any one indicator, so every
#: operator case runs against each of these terms.
TERMS = ["rsi", "adx", "stochastic", "mfi", "cci", "atr", "volume", "momentum"]

OPERATORS = [
    ("below", LT),
    ("under", LT),
    ("less than", LT),
    ("lower than", LT),
    ("strictly below", LT),
    ("at most", LTE),
    ("no more than", LTE),
    ("not more than", LTE),
    ("at or below", LTE),
    ("above", GT),
    ("over", GT),
    ("greater than", GT),
    ("higher than", GT),
    ("strictly above", GT),
    ("at least", GTE),
    ("no less than", GTE),
    ("not less than", GTE),
    ("at or above", GTE),
]


@pytest.mark.parametrize(("phrase", "comparator"), OPERATORS)
@pytest.mark.parametrize("term", TERMS)
def test_every_operator_reads_the_same_for_every_indicator(
    term: str, phrase: str, comparator: Comparator
) -> None:
    reading = read_level(f"{term} {phrase} 42", term)
    assert reading is not None, (term, phrase)
    assert reading.comparator is comparator
    assert reading.value == pytest.approx(42.0)
    assert reading.operator_stated is True


@pytest.mark.parametrize("term", TERMS)
@pytest.mark.parametrize("value", ["30", "29.5", "0.5", "0.05", "100", "1.25", "-100"])
def test_decimals_are_never_truncated_for_any_indicator(term: str, value: str) -> None:
    reading = read_level(f"{term} above {value}", term)
    assert reading is not None
    assert reading.value == pytest.approx(float(value))


@pytest.mark.parametrize("term", TERMS)
def test_a_longer_phrase_is_never_read_as_the_phrase_inside_it(term: str) -> None:
    """`no less than` must not be read as the `less than` it contains."""
    assert read_level(f"{term} no less than 5", term).comparator is GTE  # type: ignore[union-attr]
    assert read_level(f"{term} no more than 5", term).comparator is LTE  # type: ignore[union-attr]
    assert read_level(f"{term} at least 5", term).comparator is GTE  # type: ignore[union-attr]


@pytest.mark.parametrize("term", TERMS)
def test_an_indicator_without_a_level_reads_as_nothing(term: str) -> None:
    """A level the trader never gave is not a rule."""
    assert read_level(f"use {term}", term) is None
    assert read_level(f"{term} looks interesting", term) is None


@pytest.mark.parametrize("term", TERMS)
def test_a_default_comparator_supplies_the_operator_but_never_the_level(term: str) -> None:
    assert read_level(f"use {term}", term, default_comparator=GTE) is None
    reading = read_level(f"{term} 2", term, default_comparator=GTE)
    assert reading is not None
    assert reading.comparator is GTE
    assert reading.value == pytest.approx(2.0)
    assert reading.operator_stated is False


def test_a_stated_operator_always_beats_the_default() -> None:
    reading = read_level("volume at most 2x average", "volume", default_comparator=GTE)
    assert reading is not None
    assert reading.comparator is LTE
    assert reading.operator_stated is True


@pytest.mark.parametrize(
    ("text", "term", "comparator", "value"),
    [
        ("rsi below 30 and volume above 2x average", "rsi", LT, 30.0),
        ("rsi below 30 and volume above 2x average", "volume", GT, 2.0),
        ("volume above 2x average and rsi below 30", "rsi", LT, 30.0),
        ("volume above 2x average and rsi below 30", "volume", GT, 2.0),
        ("adx above 25; rsi at most 30", "adx", GT, 25.0),
        ("adx above 25; rsi at most 30", "rsi", LTE, 30.0),
    ],
)
def test_one_clause_never_borrows_another_clause_s_operator(
    text: str, term: str, comparator: Comparator, value: float
) -> None:
    reading = read_level(text, term, default_comparator=GTE)
    assert reading is not None
    assert reading.comparator is comparator
    assert reading.value == pytest.approx(value)


@pytest.mark.parametrize("term", TERMS)
def test_an_indicator_period_in_brackets_is_not_the_level(term: str) -> None:
    reading = read_level(f"{term}(14) below 30", term)
    assert reading is not None
    assert reading.value == pytest.approx(30.0)


@pytest.mark.parametrize("timeframe", ["1m", "5m", "15m", "30m", "1h", "4h", "1d"])
def test_a_timeframe_is_never_read_as_a_level(timeframe: str) -> None:
    assert read_level(f"atr on the {timeframe}", "atr", default_comparator=GTE) is None
    reading = read_level(f"rsi below 30 on the {timeframe}", "rsi")
    assert reading is not None
    assert reading.value == pytest.approx(30.0)


@pytest.mark.parametrize(
    ("text", "unit"),
    [
        ("volume above 2x average", "multiple"),
        ("volume above 2 times average", "multiple"),
        ("atr at least 1.5%", "percent"),
        ("atr at least 1.5 percent", "percent"),
        ("rsi below 30", "plain"),
    ],
)
def test_the_unit_of_the_level_is_reported_not_guessed(text: str, unit: str) -> None:
    term = text.split()[0]
    reading = read_level(text, term)
    assert reading is not None
    assert reading.unit == unit


def test_a_required_unit_rejects_a_mismatched_reading() -> None:
    """A rule about multiples must not silently consume a percentage."""
    assert read_level("volume at least 5%", "volume", require_unit="multiple") is None
    assert read_level("volume at least 5x", "volume", require_unit="multiple") is not None


def test_a_clause_is_bounded_by_its_neighbours() -> None:
    assert clause_for("rsi below 30 and volume above 2x", "rsi") == "rsi below 30"
    assert clause_for("rsi below 30 and volume above 2x", "volume") == "volume above 2x"
    assert clause_for("nothing here", "rsi") is None


def test_the_occurrence_carrying_the_level_is_the_one_that_is_read() -> None:
    """A bare earlier mention must not hide the sentence that states the level."""
    reading = read_level("I like rsi. Use rsi below 30", "rsi")
    assert reading is not None
    assert reading.value == pytest.approx(30.0)
    assert reading.comparator is LT


def test_an_empty_or_absent_term_reads_as_nothing() -> None:
    assert read_level("", "rsi") is None
    assert read_level("rsi below 30", "") is None
    assert clause_for("", "rsi") is None
