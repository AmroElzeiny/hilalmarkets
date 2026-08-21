"""Every percentage formula must measure the move it is named after.

A monitor built on the dashboard with "tell me when a coin rises 0.1%" never found a
single coin. The rule compiled, the scan ran, and the measurement came back as exactly
``0.00%`` on every candle of every coin — because the card stored only the *name* of the
formula and the runtime, finding no fields beside it, compared a candle's close with its
own close.

The bug was not one card. Four separate modules each wrote out what a percentage formula
measures, and the two the dashboard used wrote a subset. So these tests assert the rule,
not the case:

* every percentage formula, from every producer, measures the real move;
* every producer stores the same measurement for the same formula;
* a formula nobody can measure is refused, never measured as nothing.

A fix that repaired only the reported card fails here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_market_monitor.engine.builder_operations import build_condition
from ai_market_monitor.engine.context_conditions import ContextDataUnavailable
from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.engine.formula_compiler import PercentageFormulaSpec
from ai_market_monitor.engine.indicators import IndicatorWarmupError
from ai_market_monitor.schemas.setup_agent import _canonicalize_core_operand_metadata
from ai_market_monitor.schemas.strategy import Comparator, Operand, OperandKind
from ai_market_monitor.schemas.strategy_draft_v2 import (
    FORMULA_BY_RUNTIME_NAME,
    PERCENTAGE_MEASUREMENTS,
    RUNTIME_NAME_BY_FORMULA,
    FormulaKind,
    measurement_for,
    percentage_runtime_parameters,
)
from ai_market_monitor.services.interfaces import Candle

#: Two closed candles. The second one rose 5% from its open, sits 5% above the previous
#: close, spans 10% from its low to its high, and 9.0909…% from its high down to its low.
#: Every number below is that candle read a different way, so a measurement that returns
#: zero — or another formula's answer — cannot pass.
_START = datetime(2026, 1, 1, tzinfo=UTC)
CANDLES: list[Candle] = [
    Candle(
        timestamp=_START,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000.0,
        is_closed=True,
    ),
    Candle(
        timestamp=_START + timedelta(minutes=15),
        open=100.0,
        high=110.0,
        low=100.0,
        close=105.0,
        volume=1000.0,
        is_closed=True,
    ),
]

#: What each formula must measure on the candles above. Signed, before the rule's own
#: direction is applied.
TRUE_MOVE: dict[FormulaKind, float] = {
    FormulaKind.OPEN_TO_CLOSE_PERCENTAGE: 5.0,
    FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE: 5.0,
    FormulaKind.HIGH_TO_LOW_PERCENTAGE: -100 / 11,
    FormulaKind.LOW_TO_HIGH_PERCENTAGE: 10.0,
    FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE: 5.0,
}

#: The formulas whose earlier price the trader chooses. They need one extra answer, and
#: the tests supply it exactly where a real screen would.
CHOSEN_REFERENCE: dict[FormulaKind, dict[str, object]] = {
    FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE: {
        "reference_field": "open",
        "lookback": 1,
    },
}

EVERY_PERCENTAGE_FORMULA = sorted(PERCENTAGE_MEASUREMENTS, key=lambda item: item.value)


def _measure(parameters: dict, candles: list[Candle] | None = None) -> float:
    history = CANDLES if candles is None else candles
    operand = Operand(
        kind=OperandKind.MARKET_METRIC,
        name="percentage_change",
        parameters=dict(parameters),
    )
    return StrategyRuleEngine._percentage_change(operand, history, {"15m": history})


# ---------------------------------------------------------------------------
# The measurement itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("formula", EVERY_PERCENTAGE_FORMULA)
def test_every_formula_measures_its_own_move(formula: FormulaKind) -> None:
    """The table that owns the measurement produces the real number, never zero."""

    stated = CHOSEN_REFERENCE.get(formula, {})
    measured = _measure(percentage_runtime_parameters(formula, **stated))  # type: ignore[arg-type]
    assert measured == pytest.approx(TRUE_MOVE[formula]), formula.value
    assert measured != 0.0, f"{formula.value} measured nothing"


@pytest.mark.parametrize(
    "formula",
    [
        item
        for item in EVERY_PERCENTAGE_FORMULA
        if not PERCENTAGE_MEASUREMENTS[item].reference_is_chosen
    ],
)
def test_the_formula_name_alone_is_enough_to_measure(formula: FormulaKind) -> None:
    """This is the defect, pinned.

    The rules already saved in the database carry nothing but the formula's name,
    because the card that wrote them stored nothing else. The runtime must know what
    that name means on its own. While it did not, every one of those rules measured
    0.00% for ever, and no monitor built on the dashboard could fire.

    A runtime that goes back to reading the fields out of the stored rule fails here.
    """

    measured = _measure({"formula": RUNTIME_NAME_BY_FORMULA[formula]})
    assert measured == pytest.approx(TRUE_MOVE[formula]), formula.value


# ---------------------------------------------------------------------------
# Every producer of the same operand
# ---------------------------------------------------------------------------


def _builder_parameters(formula: FormulaKind) -> dict:
    """What the Guided Builder and the monitor canvas store for this card."""

    measurement = PERCENTAGE_MEASUREMENTS[formula]
    values: dict[str, object] = {
        "direction": "down" if formula is FormulaKind.HIGH_TO_LOW_PERCENTAGE else "up",
        "comparator": "gte",
        "threshold": 0.1,
        "timeframe": "15m",
    }
    if measurement.reference_is_chosen:
        values.update(CHOSEN_REFERENCE[formula])
    node, _ = build_condition(
        mechanic_key=formula.value,
        values=values,
        source_turn_id="turn-1",
    )
    return dict(node.operands[0].parameters)


def _setup_chat_parameters(formula: FormulaKind) -> dict:
    """What the AI Setup Chat's canonicaliser stores for the same formula."""

    stated = CHOSEN_REFERENCE.get(formula, {})
    raw_node: dict[str, object] = {"formula": formula.value, "operands": []}
    if "lookback" in stated:
        raw_node["lookback"] = stated["lookback"]
    if "reference_field" in stated:
        raw_node["operands"] = [
            {
                "kind": "market_metric",
                "name": "percentage_change",
                "parameters": {"reference_field": stated["reference_field"]},
            }
        ]
    operands = _canonicalize_core_operand_metadata(raw_node)
    return dict(operands[0]["parameters"])  # type: ignore[index,call-overload]


def _typed_message_parameters(formula: FormulaKind) -> dict:
    """What the typed-message compiler stores for the same formula."""

    measurement = PERCENTAGE_MEASUREMENTS[formula]
    stated = CHOSEN_REFERENCE.get(formula, {})
    spec = PercentageFormulaSpec(
        formula=RUNTIME_NAME_BY_FORMULA[formula],  # type: ignore[arg-type]
        direction="up",
        comparator=Comparator.GREATER_THAN_OR_EQUAL,
        threshold_percent=0.1,
        timeframe="15m",
        reference_timeframe="15m",
        reference_field=str(stated.get("reference_field") or measurement.reference_field or ""),
        current_field=measurement.current_field,
        lookback=int(stated.get("lookback", measurement.default_lookback)),  # type: ignore[arg-type]
    )
    return dict(spec.parameters())


PRODUCERS = {
    "guided_builder_and_canvas": _builder_parameters,
    "ai_setup_chat": _setup_chat_parameters,
    "typed_message": _typed_message_parameters,
}


@pytest.mark.parametrize("producer", sorted(PRODUCERS))
@pytest.mark.parametrize("formula", EVERY_PERCENTAGE_FORMULA)
def test_every_producer_stores_a_measurable_rule(
    formula: FormulaKind,
    producer: str,
) -> None:
    """Whoever built the rule, the runtime measures the same real move."""

    parameters = PRODUCERS[producer](formula)
    measured = _measure(parameters)
    assert measured == pytest.approx(TRUE_MOVE[formula]), f"{producer}:{formula.value}"
    assert measured != 0.0, f"{producer} stored an unmeasurable {formula.value}"


@pytest.mark.parametrize("producer", sorted(PRODUCERS))
@pytest.mark.parametrize("formula", EVERY_PERCENTAGE_FORMULA)
def test_every_producer_writes_the_whole_measurement_down(
    formula: FormulaKind,
    producer: str,
) -> None:
    """The other half of the defect, pinned.

    Storing only ``{"formula": "open_to_close"}`` is what started this. Every producer
    must write the whole measurement into the rule, so the saved rule can be read by
    anything — an older worker, an export, a report — without having to know the table.
    """

    parameters = PRODUCERS[producer](formula)
    measurement = PERCENTAGE_MEASUREMENTS[formula]
    assert parameters.get("formula") == measurement.runtime_name
    assert parameters.get("current_field") == measurement.current_field
    assert parameters.get("reference_field") == (
        CHOSEN_REFERENCE[formula]["reference_field"]
        if measurement.reference_is_chosen
        else measurement.reference_field
    )


@pytest.mark.parametrize("formula", EVERY_PERCENTAGE_FORMULA)
def test_producers_agree_on_what_a_formula_measures(formula: FormulaKind) -> None:
    """No producer may write down a different reading of the same formula."""

    readings = {
        name: (
            build(formula).get("formula"),
            build(formula).get("reference_field"),
            build(formula).get("current_field"),
        )
        for name, build in PRODUCERS.items()
    }
    assert len(set(readings.values())) == 1, readings


# ---------------------------------------------------------------------------
# "An earlier price" must be an *earlier* candle.
# ---------------------------------------------------------------------------
#
# ``reference_to_current`` measures the move away from a price the trader names, and the
# card asks how far back it is: "1 means the candle before this one." The runtime read
# ``reference_candles[-lookback:]``, a window that **ends on** the candle being judged,
# so with the card's own default of 1 it compared the newest close with itself. The card
# "Move away from an earlier price" therefore answered exactly 0.00% on every candle of
# every coin, and no rule built on it could ever be true.
#
# The candles at the top of this file could not catch it: both of them open at 100.0, so
# "this candle's open" and "the previous candle's open" are the same number. These use a
# series where every candle differs from every other, and check every reference field
# and every distance back.

_STAIRCASE: list[Candle] = [
    Candle(
        timestamp=_START + timedelta(minutes=15 * index),
        open=100.0 + index * 10,
        high=104.0 + index * 10,
        low=96.0 + index * 10,
        close=102.0 + index * 10,
        volume=1000.0,
        is_closed=True,
    )
    for index in range(6)
]

#: field -> what the reference must be, given ``lookback`` candles back from the newest.
_EXPECTED_REFERENCE = {
    "open": lambda back: 100.0 + (5 - back) * 10,
    "close": lambda back: 102.0 + (5 - back) * 10,
    # High and low read across the whole window of earlier candles, so the extreme is
    # the newest of them for a rising series.
    "high": lambda back: 104.0 + (5 - 1) * 10,
    "low": lambda back: 96.0 + (5 - back) * 10,
}


@pytest.mark.parametrize("reference_field", sorted(_EXPECTED_REFERENCE))
@pytest.mark.parametrize("lookback", [1, 2, 3, 5])
def test_an_earlier_price_is_read_from_an_earlier_candle(
    reference_field: str,
    lookback: int,
) -> None:
    """Every reference field, at every distance back, must skip the current candle."""

    current = float(_STAIRCASE[-1].close)
    expected_reference = _EXPECTED_REFERENCE[reference_field](lookback)
    expected = ((current - expected_reference) / expected_reference) * 100
    measured = _measure(
        {
            "formula": "reference_to_current",
            "reference_field": reference_field,
            "current_field": "close",
            "lookback": lookback,
            "direction": "signed",
            "scale": "percent",
        },
        _STAIRCASE,
    )
    assert measured == pytest.approx(expected), (
        f"a move measured from the {reference_field} {lookback} candles back read "
        f"{measured}, not {expected}"
    )


@pytest.mark.parametrize("reference_field", sorted(_EXPECTED_REFERENCE))
def test_an_earlier_price_is_never_the_candle_being_judged(reference_field: str) -> None:
    """The exact shape of the bug: the answer must not be zero on a moving market."""

    measured = _measure(
        {
            "formula": "reference_to_current",
            "reference_field": reference_field,
            "current_field": "close",
            "lookback": 1,
            "direction": "signed",
            "scale": "percent",
        },
        _STAIRCASE,
    )
    assert measured != 0.0, (
        f"the move from the {reference_field} of the candle before this one measured "
        "exactly 0.00% on a market that rose every candle"
    )


@pytest.mark.parametrize("lookback", [0, -1])
def test_a_reference_with_no_distance_back_is_refused_not_measured_as_zero(
    lookback: int,
) -> None:
    """Nought candles back names the candle being judged, which is not earlier at all.

    Refused rather than answered, so the misunderstanding stays visible instead of the
    monitor quietly reporting no movement for ever.
    """

    with pytest.raises(ContextDataUnavailable):
        _measure(
            {
                "formula": "reference_to_current",
                "reference_field": "close",
                "current_field": "close",
                "lookback": lookback,
                "direction": "signed",
                "scale": "percent",
            },
            _STAIRCASE,
        )


def test_a_reference_further_back_than_the_history_is_a_warm_up() -> None:
    """Not enough candles yet resolves by waiting, so it must not be a hard refusal."""

    with pytest.raises(IndicatorWarmupError):
        _measure(
            {
                "formula": "reference_to_current",
                "reference_field": "close",
                "current_field": "close",
                "lookback": len(_STAIRCASE) + 5,
                "direction": "signed",
                "scale": "percent",
            },
            _STAIRCASE,
        )


# ---------------------------------------------------------------------------
# Nothing is measured as nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["", "candle_move", "open_to_close_pct", "percentage"])
def test_an_unknown_formula_is_refused_not_measured(name: str) -> None:
    """A name the platform cannot measure stops the rule; it never reads as 0%.

    Refused as *unavailable*, never as *warming up*. Warming up is shown to the owner
    as "forming", which reads as "wait a little longer" — and this never resolves.
    """

    with pytest.raises(ContextDataUnavailable):
        _measure({"formula": name})
    with pytest.raises(Exception) as caught:
        _measure({"formula": name})
    assert not isinstance(caught.value, IndicatorWarmupError)


@pytest.mark.parametrize(
    "formula",
    [
        item
        for item in EVERY_PERCENTAGE_FORMULA
        if PERCENTAGE_MEASUREMENTS[item].reference_is_chosen
    ],
)
def test_a_missing_earlier_price_is_refused(formula: FormulaKind) -> None:
    """"Move away from an earlier price" with no earlier price is not a measurement."""

    with pytest.raises(ContextDataUnavailable):
        _measure({"formula": RUNTIME_NAME_BY_FORMULA[formula]})


# ---------------------------------------------------------------------------
# The table itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("formula", EVERY_PERCENTAGE_FORMULA)
def test_runtime_names_round_trip(formula: FormulaKind) -> None:
    runtime_name = RUNTIME_NAME_BY_FORMULA[formula]
    assert FORMULA_BY_RUNTIME_NAME[runtime_name] is formula
    assert measurement_for(runtime_name) is PERCENTAGE_MEASUREMENTS[formula]
    assert measurement_for(formula.value) is PERCENTAGE_MEASUREMENTS[formula]


@pytest.mark.parametrize("formula", EVERY_PERCENTAGE_FORMULA)
def test_a_fixed_reading_cannot_be_overridden(formula: FormulaKind) -> None:
    """A stored field never replaces a reading the formula's own name fixes."""

    measurement = PERCENTAGE_MEASUREMENTS[formula]
    if measurement.reference_is_chosen:
        pytest.skip("this formula asks the trader which earlier price")
    parameters = percentage_runtime_parameters(formula, reference_field="close")
    assert parameters["reference_field"] == measurement.reference_field
