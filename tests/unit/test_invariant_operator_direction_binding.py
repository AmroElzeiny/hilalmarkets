"""INV-07: formula, direction, operator and threshold are read as one unit.

`bearish % change >= 1.0%` compiled as `lt 1.0` because the comparator was read from
a character window around the number, and the window reached into the neighbouring
`(close < open)` clause that defines the candle body. `price drops at least 3%`
compiled as an *up* move because one direction list held `drop` but not `drops`.

Both are the same defect class: a value read from wording that does not govern it.
These cases assert the binding rules across operators, directions and orderings, so
a fix that only helps one phrasing fails here.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.comparators import (
    OPERATOR_TERMS,
    POSTFIX_TERMS,
    find_comparator_before,
    find_comparator_for_value,
)
from ai_market_monitor.engine.formula_compiler import parse_percentage_formula
from ai_market_monitor.engine.price_movement import (
    DOWN_TERMS,
    UP_TERMS,
    movement_direction,
    movement_direction_before,
)
from ai_market_monitor.schemas.strategy import Comparator, StrategyDirection

GT = Comparator.GREATER_THAN
GTE = Comparator.GREATER_THAN_OR_EQUAL
LT = Comparator.LESS_THAN
LTE = Comparator.LESS_THAN_OR_EQUAL


def _spec(text: str, direction: StrategyDirection = StrategyDirection.LONG):
    return parse_percentage_formula(
        text, default_timeframe="15m", default_direction=direction
    )


@pytest.mark.parametrize(
    ("text", "comparator"),
    [
        ("(close < open) AND (bearish % change >= 1.0%)", GTE),
        ("(close > open) AND (bullish % change <= 2.5%)", LTE),
        ("(close < open) AND (move of at least 1%)", GTE),
        ("(close > open) AND (move of at most 4%)", LTE),
        ("close above open and the move is under 3%", LT),
        ("close below open and the move is over 3%", GT),
    ],
)
def test_an_operator_in_a_neighbouring_clause_never_claims_the_threshold(
    text: str, comparator: Comparator
) -> None:
    spec = _spec(text)
    assert spec is not None
    assert spec.comparator is comparator, spec.comparator


@pytest.mark.parametrize("phrase", ["at least", "no less than", "at or above"])
@pytest.mark.parametrize("movement", ["bearish move", "bullish move", "drops", "rises"])
def test_inclusive_lower_bounds_survive_every_direction_word(
    phrase: str, movement: str
) -> None:
    spec = _spec(f"{movement} {phrase} 7.5%")
    assert spec is not None
    assert spec.comparator is GTE
    assert spec.threshold_percent == pytest.approx(7.5)


@pytest.mark.parametrize("phrase", ["at most", "no more than", "at or below"])
@pytest.mark.parametrize("movement", ["bearish move", "bullish move", "drops", "rises"])
def test_inclusive_upper_bounds_survive_every_direction_word(
    phrase: str, movement: str
) -> None:
    spec = _spec(f"{movement} {phrase} 7.5%")
    assert spec is not None
    assert spec.comparator is LTE
    assert spec.threshold_percent == pytest.approx(7.5)


@pytest.mark.parametrize(
    ("text", "direction"),
    [
        ("a bearish move of at least 7.5%", "down"),
        ("a bullish move of at least 7.5%", "up"),
        ("price drops at least 3%", "down"),
        ("price dropped at least 3%", "down"),
        ("price falls at least 3%", "down"),
        ("price fell at least 3%", "down"),
        ("price declines at least 3%", "down"),
        ("down move no more than 1.25%", "down"),
        ("a sell-off of at least 4%", "down"),
        ("price rises at least 3%", "up"),
        ("price rose at least 3%", "up"),
        ("price gained at least 3%", "up"),
        ("a rally of at least 4%", "up"),
        ("up move of at least 2%", "up"),
    ],
)
def test_the_stated_direction_is_never_replaced_by_the_default(
    text: str, direction: str
) -> None:
    """The caller's default side must never overwrite a direction the trader gave."""
    for default in (StrategyDirection.LONG, StrategyDirection.SHORT):
        spec = _spec(text, default)
        assert spec is not None, text
        assert spec.direction == direction, (text, default, spec.direction)


@pytest.mark.parametrize("term", UP_TERMS)
def test_every_up_term_reads_as_up(term: str) -> None:
    assert movement_direction(f"price {term} today") == "up"


@pytest.mark.parametrize("term", DOWN_TERMS)
def test_every_down_term_reads_as_down(term: str) -> None:
    assert movement_direction(f"price {term} today") == "down"


def test_the_direction_nearest_the_number_governs_it() -> None:
    text = "bullish context but the bearish leg must reach 2%"
    assert movement_direction_before(text, text.index("2%")) == "down"


def test_the_operator_nearest_the_number_governs_it() -> None:
    text = "close < open and the move is >= 1.5%"
    found = find_comparator_before(text, text.index("1.5"))
    assert found is not None
    assert found[0] is Comparator.GREATER_THAN_OR_EQUAL


def test_a_longer_operator_phrase_wins_over_the_one_inside_it() -> None:
    text = "the move must be no less than 2%"
    found = find_comparator_before(text, text.index("2%"))
    assert found is not None
    assert found[0] is Comparator.GREATER_THAN_OR_EQUAL


#: Every phrase in the shared vocabulary that follows the value it governs, split by
#: the bound it states. Derived from the table so a phrase added there without a
#: position recorded here fails this test rather than silently inverting.
POSTFIX_LOWER = tuple(
    sorted(term for term, c in OPERATOR_TERMS if term in POSTFIX_TERMS and c is GTE)
)
POSTFIX_UPPER = tuple(
    sorted(term for term, c in OPERATOR_TERMS if term in POSTFIX_TERMS and c is LTE)
)


def test_every_postfix_phrase_is_in_the_shared_table() -> None:
    """A postfix phrase the table does not know cannot be read at all."""
    assert not POSTFIX_TERMS - {term for term, _c in OPERATOR_TERMS}
    assert set(POSTFIX_LOWER) | set(POSTFIX_UPPER) == POSTFIX_TERMS


@pytest.mark.parametrize("phrase", POSTFIX_UPPER)
@pytest.mark.parametrize("movement", ["bearish move", "bullish move", "drops", "rises"])
def test_a_ceiling_stated_after_the_number_is_never_read_as_a_floor(
    phrase: str, movement: str
) -> None:
    """`7.5% or less` is the same ceiling as `at most 7.5%`.

    Reading only to the left of the number made the postfix form invisible, and the
    "a bare move means at least this much" convention then compiled it as `>= 7.5` —
    a floor where the trader stated a ceiling, which fires on the opposite market.
    """
    spec = _spec(f"{movement} of 7.5% {phrase}")
    assert spec is not None, phrase
    assert spec.comparator is LTE, (phrase, spec.comparator)
    assert spec.threshold_percent == pytest.approx(7.5)


@pytest.mark.parametrize("phrase", POSTFIX_LOWER)
@pytest.mark.parametrize("movement", ["bearish move", "bullish move", "drops", "rises"])
def test_a_floor_stated_after_the_number_is_never_read_as_a_ceiling(
    phrase: str, movement: str
) -> None:
    spec = _spec(f"{movement} of 7.5% {phrase}")
    assert spec is not None, phrase
    assert spec.comparator is GTE, (phrase, spec.comparator)
    assert spec.threshold_percent == pytest.approx(7.5)


@pytest.mark.parametrize("phrase", POSTFIX_UPPER + POSTFIX_LOWER)
def test_a_postfix_phrase_reaches_the_compiled_condition(phrase: str) -> None:
    """The reading must survive the whole compile, not only the formula parser."""
    spec = _spec(f"a bullish move of 7.5% {phrase} on the 1d")
    assert spec is not None, phrase
    expected = LTE if phrase in POSTFIX_UPPER else GTE
    assert spec.comparator is expected, (phrase, spec.comparator)


def test_a_plus_sign_after_the_number_states_a_floor() -> None:
    spec = _spec("a bullish move of 7.5%+")
    assert spec is not None
    assert spec.comparator is GTE
    assert spec.threshold_percent == pytest.approx(7.5)


def test_a_phrase_introducing_the_next_level_is_not_read_as_postfix() -> None:
    """`and above 200` names a second comparison, not this move's floor."""
    found = find_comparator_for_value(
        "drops 5% and above 200 ema", len("drops "), len("drops 5")
    )
    assert found is None or found[0] is not GTE or found[2] <= len("drops 5")


@pytest.mark.parametrize(
    ("text", "comparator"),
    [
        ("bearish % change 1.0% or more", GTE),
        ("bearish % change 1.0% or less", LTE),
        ("(close < open) and the move is 1.0% or less", LTE),
        ("(close > open) and the move is 1.0% or more", GTE),
    ],
)
def test_a_postfix_phrase_wins_over_an_operator_in_a_neighbouring_clause(
    text: str, comparator: Comparator
) -> None:
    spec = _spec(text)
    assert spec is not None, text
    assert spec.comparator is comparator, (text, spec.comparator)


@pytest.mark.parametrize(
    "text",
    [
        "%move >= 7.5 for direction=long with operator=gte",
        "percent_move >= 7.5 direction=long",
        "move% >= 7.5 for direction=long",
    ],
)
def test_a_fully_specified_expression_compiles(text: str) -> None:
    """An expression that states metric, operator, value and side leaves nothing to
    infer, so it must never be reported back as unconvertible."""
    spec = _spec(text)
    assert spec is not None, text
    assert spec.comparator is GTE
    assert spec.threshold_percent == pytest.approx(7.5)
    assert spec.direction == "up"
