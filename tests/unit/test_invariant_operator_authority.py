"""INV-OP: the comparison a trader wrote is the comparison the monitor uses.

Evaluator run 20260803T000036Z, case ``operator_mapping-026-512624184``. The trader
wrote ``a bearish close-to-close move of at most 1%`` and received five consecutive
HTTP 422s. They restated it four more times, each time a little differently, until one
phrasing finally compiled — ``strictly below`` — and the monitor shipped with ``lt 1%``.

``at most 1%`` fires on exactly 1%. ``below 1%`` does not. The rule the trader asked to
see was the one the monitor would stay silent on, and nothing in the pipeline compared
the model's comparator against the words it came from.

These tests assert the whole family, in every language the product accepts: each phrase
the operator authority promises, prefix and postfix, symbols, Arabic and Arabizi — not
the one sentence that was reported.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.operator_authority import (
    OPERATOR_PHRASE_AUTHORITY,
    OperatorNormalizationKind,
    comparator_is_inclusive,
    comparator_label,
    normalize_stated_comparator,
    stated_comparator_for_threshold,
)
from ai_market_monitor.engine.planner_intent_compiler import compile_planner_intents
from ai_market_monitor.schemas.planner_intent import PlannerIntentEnvelope
from ai_market_monitor.schemas.strategy import Comparator
from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2

#: Every phrase the closure brief names, plus the ones already in the vocabulary.
#: Each is tested at the same threshold so only the wording varies.
PHRASES: tuple[tuple[str, Comparator], ...] = (
    ("at most", Comparator.LESS_THAN_OR_EQUAL),
    ("no more than", Comparator.LESS_THAN_OR_EQUAL),
    ("not more than", Comparator.LESS_THAN_OR_EQUAL),
    ("up to", Comparator.LESS_THAN_OR_EQUAL),
    ("not exceeding", Comparator.LESS_THAN_OR_EQUAL),
    ("capped at", Comparator.LESS_THAN_OR_EQUAL),
    ("less than or equal to", Comparator.LESS_THAN_OR_EQUAL),
    ("no higher than", Comparator.LESS_THAN_OR_EQUAL),
    ("maximum of", Comparator.LESS_THAN_OR_EQUAL),
    ("at least", Comparator.GREATER_THAN_OR_EQUAL),
    ("no less than", Comparator.GREATER_THAN_OR_EQUAL),
    ("not less than", Comparator.GREATER_THAN_OR_EQUAL),
    ("minimum of", Comparator.GREATER_THAN_OR_EQUAL),
    ("greater than or equal to", Comparator.GREATER_THAN_OR_EQUAL),
    ("no lower than", Comparator.GREATER_THAN_OR_EQUAL),
    ("below", Comparator.LESS_THAN),
    ("less than", Comparator.LESS_THAN),
    ("strictly below", Comparator.LESS_THAN),
    ("under", Comparator.LESS_THAN),
    ("above", Comparator.GREATER_THAN),
    ("greater than", Comparator.GREATER_THAN),
    ("strictly above", Comparator.GREATER_THAN),
    ("over", Comparator.GREATER_THAN),
    ("exactly", Comparator.EQUAL),
    ("equal to", Comparator.EQUAL),
)

SYMBOLS: tuple[tuple[str, Comparator], ...] = (
    ("<=", Comparator.LESS_THAN_OR_EQUAL),
    ("≤", Comparator.LESS_THAN_OR_EQUAL),
    (">=", Comparator.GREATER_THAN_OR_EQUAL),
    ("≥", Comparator.GREATER_THAN_OR_EQUAL),
    ("<", Comparator.LESS_THAN),
    (">", Comparator.GREATER_THAN),
    ("=", Comparator.EQUAL),
)

#: Arabic, Egyptian Arabic and Arabizi. A trader who writes their own language must get
#: the same comparison as one who writes English; a phrase understood in one and not
#: the other is a silently different monitor for the same request.
NON_ENGLISH: tuple[tuple[str, float, Comparator], ...] = (
    ("حركة هابطة بحد أقصى 1%", 1.0, Comparator.LESS_THAN_OR_EQUAL),
    ("حركة هابطة لا تزيد عن 1%", 1.0, Comparator.LESS_THAN_OR_EQUAL),
    ("حركة صاعدة على الأقل 2.5%", 2.5, Comparator.GREATER_THAN_OR_EQUAL),
    ("حركة صاعدة بحد أدنى 2.5%", 2.5, Comparator.GREATER_THAN_OR_EQUAL),
    ("harka bearish 3ala el aktar 1%", 1.0, Comparator.LESS_THAN_OR_EQUAL),
    ("harka bullish 3ala el a2al 2.5%", 2.5, Comparator.GREATER_THAN_OR_EQUAL),
    ("move akbar men 2.5%", 2.5, Comparator.GREATER_THAN),
    ("move a2al men 1%", 1.0, Comparator.LESS_THAN),
    ("bearish move 1% aw a2al", 1.0, Comparator.LESS_THAN_OR_EQUAL),
    ("bullish move 2.5% aw aktar", 2.5, Comparator.GREATER_THAN_OR_EQUAL),
)

#: Wording that states the comparison *after* the value. A reader that only looked left
#: saw nothing here, and the caller's "a bare move means at least this much" convention
#: then compiled a stated ceiling as a floor: the opposite alert.
POSTFIX: tuple[tuple[str, Comparator], ...] = (
    ("or less", Comparator.LESS_THAN_OR_EQUAL),
    ("or lower", Comparator.LESS_THAN_OR_EQUAL),
    ("or below", Comparator.LESS_THAN_OR_EQUAL),
    ("or more", Comparator.GREATER_THAN_OR_EQUAL),
    ("or higher", Comparator.GREATER_THAN_OR_EQUAL),
    ("or above", Comparator.GREATER_THAN_OR_EQUAL),
)


@pytest.mark.parametrize(("phrase", "expected"), PHRASES)
def test_every_phrase_states_its_own_comparison(phrase: str, expected: Comparator) -> None:
    reading = stated_comparator_for_threshold(f"a bearish move of {phrase} 1%", 1.0)
    assert reading is not None, phrase
    assert reading.comparator is expected


@pytest.mark.parametrize(("token", "expected"), SYMBOLS)
def test_every_symbol_states_its_own_comparison(token: str, expected: Comparator) -> None:
    reading = stated_comparator_for_threshold(f"RSI {token} 30", 30.0)
    assert reading is not None, token
    assert reading.comparator is expected


@pytest.mark.parametrize(("text", "threshold", "expected"), NON_ENGLISH)
def test_arabic_and_arabizi_state_the_same_comparison(
    text: str, threshold: float, expected: Comparator
) -> None:
    reading = stated_comparator_for_threshold(text, threshold)
    assert reading is not None, text
    assert reading.comparator is expected


@pytest.mark.parametrize(("phrase", "expected"), POSTFIX)
def test_a_comparison_stated_after_the_value_is_still_read(
    phrase: str, expected: Comparator
) -> None:
    reading = stated_comparator_for_threshold(f"a bullish move of 7.5% {phrase}", 7.5)
    assert reading is not None, phrase
    assert reading.comparator is expected


@pytest.mark.parametrize(("phrase", "stated"), PHRASES)
@pytest.mark.parametrize("proposed", list(Comparator))
def test_the_words_always_beat_the_model(
    phrase: str, stated: Comparator, proposed: Comparator
) -> None:
    """Whatever the model returns, the trader's own words decide.

    Every phrase against every comparator the model could possibly return. A fix that
    only helps ``at most`` versus ``lt`` would pass a one-case test and leave the rest
    of the family exactly as broken as it was.
    """

    result = normalize_stated_comparator(
        f"a bearish move of {phrase} 1%", threshold=1.0, proposed=proposed
    )
    if proposed in {
        Comparator.CROSSES_ABOVE,
        Comparator.CROSSES_BELOW,
        Comparator.IS_TRUE,
        Comparator.IS_FALSE,
    }:
        # A cross, a true/false pattern and a threshold are different mechanics.
        # Converting between them would replace the event being watched, so neither
        # may overrule the other; a genuine mismatch is refused elsewhere by the
        # formula contract, not silently rewritten here.
        assert result.kind is OperatorNormalizationKind.NOT_STATED
        assert result.resolved is proposed
        return
    assert result.resolved is stated
    assert result.corrected == (proposed is not stated)


def test_inclusive_and_exclusive_are_never_confused() -> None:
    assert comparator_is_inclusive(Comparator.LESS_THAN_OR_EQUAL)
    assert comparator_is_inclusive(Comparator.GREATER_THAN_OR_EQUAL)
    assert not comparator_is_inclusive(Comparator.LESS_THAN)
    assert not comparator_is_inclusive(Comparator.GREATER_THAN)


def test_two_different_comparisons_on_one_value_are_refused_not_guessed() -> None:
    result = normalize_stated_comparator(
        "at least 1% but below 1%", threshold=1.0, proposed=Comparator.GREATER_THAN_OR_EQUAL
    )
    assert result.kind is OperatorNormalizationKind.AMBIGUOUS
    assert result.resolved is None


def test_direction_is_never_encoded_by_negating_the_threshold() -> None:
    """``bearish at least 2.5%`` is down + gte + 2.5, never lte + -2.5."""

    reading = stated_comparator_for_threshold("a bearish move of at least 2.5%", 2.5)
    assert reading is not None
    assert reading.comparator is Comparator.GREATER_THAN_OR_EQUAL
    assert stated_comparator_for_threshold("a bearish move of at least 2.5%", -2.5) is None


@pytest.mark.parametrize("comparator", list(Comparator))
def test_every_comparator_has_a_plain_label_and_a_phrase_list(comparator: Comparator) -> None:
    """A beginner never sees ``lte``, and every label is backed by real wording."""

    label = comparator_label(comparator)
    assert label and label != comparator.value or comparator in {
        Comparator.IS_TRUE,
        Comparator.IS_FALSE,
    }
    assert comparator in OPERATOR_PHRASE_AUTHORITY


# ---------------------------------------------------------------------------------
# Through the real compiler
# ---------------------------------------------------------------------------------


def _condition_envelope(message: str, comparator: str, threshold: float) -> PlannerIntentEnvelope:
    return PlannerIntentEnvelope.model_validate(
        {
            "segments": [
                {
                    "segment_ref": "s1",
                    "exact_source_text": message,
                    "segment_kind": "STRATEGY_INSTRUCTION",
                }
            ],
            "semantic_intents": [
                {
                    "segment_ref": "s1",
                    "payload": {
                        "action": "add_condition",
                        "condition": {
                            "source_quote": message,
                            "formula_key": "close_to_close_percentage",
                            "movement_direction": "down",
                            "comparator": comparator,
                            "threshold": threshold,
                            "unit": "percent",
                            "trigger_timeframe": "15m",
                        },
                    },
                }
            ],
            "overall_confidence": 0.99,
        }
    )


def test_operator_mapping_026_compiles_the_inclusive_ceiling_the_trader_wrote() -> None:
    """The exact turn that shipped ``lt 1%`` after five refusals."""

    message = "Require a bearish close-to-close move of at most 1% on the 15m chart."
    compiled = compile_planner_intents(
        # The model's wrong answer, deliberately: the point is that it loses.
        _condition_envelope(message, "lt", 1.0),
        draft=StrategyDraftV2(),
        message=message,
        source_turn_id="turn-operator-026",
    )
    node = compiled.plan.operations[0].condition
    assert node is not None
    assert node.operator is Comparator.LESS_THAN_OR_EQUAL
    assert node.threshold == 1.0
    assert any("DETERMINISTIC_OPERATOR_NORMALIZATION" in item for item in compiled.derivations)


def test_a_correct_model_answer_is_left_alone() -> None:
    message = "Require a bearish close-to-close move of at most 1% on the 15m chart."
    compiled = compile_planner_intents(
        _condition_envelope(message, "lte", 1.0),
        draft=StrategyDraftV2(),
        message=message,
        source_turn_id="turn-operator-ok",
    )
    node = compiled.plan.operations[0].condition
    assert node is not None
    assert node.operator is Comparator.LESS_THAN_OR_EQUAL
    assert not any("DETERMINISTIC_OPERATOR_NORMALIZATION" in item for item in compiled.derivations)


def test_below_stays_below_and_is_not_widened_into_at_most() -> None:
    """The correction runs both ways, or it is just a different bias."""

    message = "Require a bearish close-to-close move strictly below 1% on the 15m chart."
    compiled = compile_planner_intents(
        _condition_envelope(message, "lte", 1.0),
        draft=StrategyDraftV2(),
        message=message,
        source_turn_id="turn-operator-strict",
    )
    node = compiled.plan.operations[0].condition
    assert node is not None
    assert node.operator is Comparator.LESS_THAN
