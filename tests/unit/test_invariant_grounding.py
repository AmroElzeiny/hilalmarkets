"""INV-02/INV-03: a compiled value must be findable in the trader's own words.

Grounding is the safety rule for AI-assisted reading. A model may fill fields of a
type the compiler already has, but every value it fills is checked against the source
text here. Confidence is the model's opinion of itself; grounding is a fact about the
text, and only the second one can catch a hallucinated threshold.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.formula_compiler import (
    grounding_violations,
    parse_percentage_formula,
)
from ai_market_monitor.engine.grounded_patch import (
    comparator_is_grounded,
    direction_is_grounded,
    number_is_grounded,
    verify_grounding,
)
from ai_market_monitor.schemas.strategy import Comparator, StrategyDirection


@pytest.mark.parametrize(
    ("value", "source", "grounded"),
    [
        (7.5, "a bearish move of at least 7.5%", True),
        (7.5, "a bearish move of at least 7.50%", True),
        (7.5, "move of -7.5%", True),
        (7.5, "a bearish move of at least 5%", False),
        (7.0, "wait 7 days then alert", True),
        (30.0, "RSI below 30", True),
        (50.0, "RSI below 30", False),
    ],
)
def test_a_threshold_must_appear_in_the_request(
    value: float, source: str, grounded: bool
) -> None:
    assert number_is_grounded(value, source) is grounded


@pytest.mark.parametrize(
    ("comparator", "source", "grounded"),
    [
        (Comparator.GREATER_THAN_OR_EQUAL, "at least 5%", True),
        (Comparator.GREATER_THAN_OR_EQUAL, "no less than 5%", True),
        (Comparator.GREATER_THAN_OR_EQUAL, ">= 5%", True),
        (Comparator.LESS_THAN_OR_EQUAL, "at most 5%", True),
        (Comparator.LESS_THAN_OR_EQUAL, "at least 5%", False),
        (Comparator.LESS_THAN, "below 30", True),
        (Comparator.GREATER_THAN, "below 30", False),
    ],
)
def test_a_comparison_must_appear_in_the_request(
    comparator: Comparator, source: str, grounded: bool
) -> None:
    assert comparator_is_grounded(comparator, source) is grounded


@pytest.mark.parametrize(
    ("direction", "source", "grounded"),
    [
        ("down", "a bearish move of 5%", True),
        ("down", "price drops 5%", True),
        ("up", "a bearish move of 5%", False),
        ("up", "price rallied 5%", True),
    ],
)
def test_a_direction_must_appear_in_the_request(
    direction: str, source: str, grounded: bool
) -> None:
    assert direction_is_grounded(direction, source) is grounded


def test_an_unstated_comparison_is_a_named_convention_not_a_violation() -> None:
    """`up 5%` states no operator. Reading it as "at least" is the platform's
    documented convention, so it is reported rather than hidden — but it is not
    treated as a hallucination."""
    report = verify_grounding(
        "coins up 5% today",
        threshold=5.0,
        comparator=Comparator.GREATER_THAN_OR_EQUAL,
        direction="up",
    )
    assert report.grounded is True
    assert report.conventions


def test_an_unstated_upper_bound_is_a_violation() -> None:
    """Turning an unstated comparison into a ceiling reverses the alert, so it can
    never be supplied by convention."""
    report = verify_grounding(
        "coins up 5% today",
        threshold=5.0,
        comparator=Comparator.LESS_THAN_OR_EQUAL,
        direction="up",
    )
    assert report.grounded is False


@pytest.mark.parametrize(
    "text",
    [
        "a bearish move of at least 7.5%",
        "price drops at least 3% on the 15m",
        "(close < open) AND (bearish % change >= 1.0%)",
        "bullish move no more than 2.5%",
    ],
)
def test_every_deterministically_parsed_formula_is_grounded(text: str) -> None:
    """The parser reads only what it matched, so it passes by construction. A model
    proposal has to clear the same bar — there is one way into the compiler."""
    spec = parse_percentage_formula(
        text, default_timeframe="15m", default_direction=StrategyDirection.LONG
    )
    assert spec is not None, text
    assert grounding_violations(spec, text) == (), grounding_violations(spec, text)


def test_a_fabricated_threshold_is_refused() -> None:
    """The failure a confidence score cannot catch: a well-formed spec whose number
    the trader never wrote."""
    source = "a bearish move of at least 7.5%"
    spec = parse_percentage_formula(
        source, default_timeframe="15m", default_direction=StrategyDirection.SHORT
    )
    assert spec is not None
    hallucinated = spec.__class__(
        **{**{f: getattr(spec, f) for f in spec.__slots__}, "threshold_percent": 12.0}
    )
    assert grounding_violations(hallucinated, source)
