"""INV-V2: what a launch draft is allowed to say, asserted across the whole family.

Four defects shared one shape — a rule written for one member of a family and not
for its siblings, so the family disagreed with itself:

* the gate in front of the deterministic parser hand-listed operators. It knew
  ``above`` but not ``equal to``, so ``price is equal to 3500`` was refused by the
  gate and never reached the parser that understood it.
* ``sweeps below the previous candle low and reclaims it`` is one mechanic, but the
  fragment reader cut it at ``and``. The gate saw a pierce with no reclaim.
* nothing checked that a formula's operator, unit and direction belong together, so
  ``cross`` compared with ``gte`` and ``sweep_and_reclaim`` carrying a percentage
  both compiled into executable rules.
* one marker word gave its role to every timeframe in the clause, so ``using the 4h
  as context when the 15m candle rises`` produced two context timeframes and no
  trigger at all — and ``confirmed on the 1h`` made the confirming candle the one
  that fires.

Each test below states the rule over every member of its family, so a fix that only
helps the sentence that was reported fails here.
"""

from __future__ import annotations

import json
import re

import httpx
import pytest

from ai_market_monitor.engine.comparators import OPERATOR_TERMS, comparator_alternation
from ai_market_monitor.engine.strategy_draft_v2 import (
    apply_strategy_patch,
    validate_draft_semantics,
)
from ai_market_monitor.schemas.strategy import Comparator
from ai_market_monitor.schemas.strategy_draft_v2 import (
    FORMULA_CONTRACTS,
    ConditionNodeType,
    ConditionNodeV2,
    FormulaKind,
    MovementDirection,
    OperandV2,
    StrategyBias,
    StrategyDraftV2,
)
from ai_market_monitor.services.ai_setup_chat import setup_chat_error_envelope
from ai_market_monitor.services.strategy_patch_extractor import (
    deterministic_strategy_patch,
)

EVERY_UNIT = ("percent", "price", "ratio", "count", "index", "boolean", "none")


def _operands_for(formula: FormulaKind) -> list[OperandV2]:
    percentage_names = {
        FormulaKind.OPEN_TO_CLOSE_PERCENTAGE: "open_to_close",
        FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE: "close_to_close",
        FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE: "reference_to_current",
        FormulaKind.HIGH_TO_LOW_PERCENTAGE: "high_to_low",
        FormulaKind.LOW_TO_HIGH_PERCENTAGE: "low_to_high",
    }
    if formula in percentage_names:
        return [
            OperandV2(
                role="measured_value",
                kind="market_metric",
                name="percentage_change",
                parameters={"formula": percentage_names[formula]},
            )
        ]
    if formula is FormulaKind.SWEEP_AND_RECLAIM:
        return [
            OperandV2(
                role="sweep_state",
                kind="market_metric",
                name="sweep_and_reclaim",
                parameters={"pierce_required": True, "reclaim_required": True},
            )
        ]
    if formula is FormulaKind.CAPABILITY:
        return [OperandV2(role="value", kind="market_metric", name="registered_capability")]
    return [
        OperandV2(role="left", kind="price", field="close"),
        OperandV2(role="right", kind="reference", name="previous_candle_close"),
    ]


def _node(**overrides) -> ConditionNodeV2:
    payload: dict = {
        "node_type": ConditionNodeType.CONDITION,
        "source_turn_id": "turn-1",
        "source_fragment": "fragment",
        "formula": FormulaKind.OPEN_TO_CLOSE_PERCENTAGE,
        "operator": Comparator.GREATER_THAN_OR_EQUAL,
        "threshold": 5.0,
        "unit": "percent",
        "trigger_timeframe": "15m",
        "movement_direction": MovementDirection.UP,
    }
    payload.update(overrides)
    payload.setdefault("operands", _operands_for(payload["formula"]))
    if payload.get("formula") is FormulaKind.CAPABILITY:
        payload.setdefault("capability_key", "registered_capability")
    return ConditionNodeV2(**payload)


def _violations(**overrides) -> list[str]:
    return validate_draft_semantics(StrategyDraftV2(condition_ast=_node(**overrides)))


# --------------------------------------------------------------------------------
# Every formula states which operators, units and sides it can carry.
# --------------------------------------------------------------------------------


def test_every_formula_has_a_contract() -> None:
    """A formula with no contract cannot be checked, so none may be missing."""
    assert set(FORMULA_CONTRACTS) == set(FormulaKind)


@pytest.mark.parametrize("formula", list(FormulaKind))
@pytest.mark.parametrize("operator", list(Comparator))
def test_only_the_operators_a_formula_owns_are_accepted(
    formula: FormulaKind, operator: Comparator
) -> None:
    contract = FORMULA_CONTRACTS[formula]
    unit = sorted(contract.units)[0]
    threshold = None if operator in {Comparator.IS_TRUE, Comparator.IS_FALSE} else 5.0
    movement_direction = next(
        side for side in MovementDirection if side not in contract.forbidden_directions
    )
    errors = _violations(
        formula=formula,
        operator=operator,
        unit=unit,
        threshold=threshold,
        movement_direction=movement_direction,
        operands=[
            OperandV2(role="a", kind="price", field="close"),
            OperandV2(role="b", kind="reference", name="previous_candle_close"),
        ],
    )
    mismatched = [item for item in errors if item.startswith("formula_operator_mismatch")]
    assert bool(mismatched) is (operator not in contract.operators), (
        formula,
        operator,
        errors,
    )


@pytest.mark.parametrize("formula", list(FormulaKind))
@pytest.mark.parametrize("unit", EVERY_UNIT)
def test_only_the_units_a_formula_owns_are_accepted(
    formula: FormulaKind, unit: str
) -> None:
    contract = FORMULA_CONTRACTS[formula]
    operator = sorted(contract.operators, key=lambda item: item.value)[0]
    threshold = None if operator in {Comparator.IS_TRUE, Comparator.IS_FALSE} else 5.0
    movement_direction = next(
        side for side in MovementDirection if side not in contract.forbidden_directions
    )
    errors = _violations(
        formula=formula,
        operator=operator,
        unit=unit,
        threshold=threshold,
        movement_direction=movement_direction,
    )
    mismatched = [item for item in errors if item.startswith("formula_unit_mismatch")]
    assert bool(mismatched) is (unit not in contract.units), (formula, unit, errors)


@pytest.mark.parametrize("formula", list(FormulaKind))
@pytest.mark.parametrize("movement_direction", list(MovementDirection))
def test_a_formula_never_measures_a_side_it_cannot_measure(
    formula: FormulaKind, movement_direction: MovementDirection
) -> None:
    contract = FORMULA_CONTRACTS[formula]
    operator = sorted(contract.operators, key=lambda item: item.value)[0]
    threshold = None if operator in {Comparator.IS_TRUE, Comparator.IS_FALSE} else 5.0
    errors = _violations(
        formula=formula,
        operator=operator,
        unit=sorted(contract.units)[0],
        threshold=threshold,
        movement_direction=movement_direction,
    )
    mismatched = [item for item in errors if item.startswith("formula_direction_mismatch")]
    assert bool(mismatched) is (
        movement_direction in contract.forbidden_directions
    ), (
        formula,
        movement_direction,
        errors,
    )


def test_a_signed_threshold_is_kept_exactly_as_the_trader_stated_it() -> None:
    """`-2%` with a long bias is a dip rule, not a defect. The sign is never overruled."""
    assert not [
        item
        for item in _violations(
            movement_direction=MovementDirection.DOWN,
            strategy_bias=StrategyBias.LONG,
            threshold=-2.0,
        )
        if "threshold" in item
    ]


# --------------------------------------------------------------------------------
# Every timeframe holds exactly one role.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["context_timeframes", "confirmation_timeframes"])
def test_the_trigger_timeframe_can_never_hold_a_second_role(role: str) -> None:
    errors = _violations(trigger_timeframe="15m", **{role: ["15m"]})
    assert any(item.startswith("timeframe_role_collision") for item in errors), errors


@pytest.mark.parametrize("role", ["context_timeframes", "confirmation_timeframes"])
def test_a_distinct_supporting_timeframe_blocks_until_it_is_executable(role: str) -> None:
    errors = _violations(trigger_timeframe="15m", **{role: ["4h"]})
    expected = (
        "context_timeframe_not_executable"
        if role == "context_timeframes"
        else "confirmation_timeframe_not_executable"
    )
    assert any(item.startswith(expected) for item in errors), errors


def test_the_same_timeframe_cannot_be_both_context_and_confirmation() -> None:
    errors = _violations(
        trigger_timeframe="15m",
        context_timeframes=["4h"],
        confirmation_timeframes=["4h"],
    )
    assert any(item.startswith("timeframe_role_collision") for item in errors), errors


def test_a_condition_without_its_own_trigger_timeframe_is_refused() -> None:
    """Otherwise it silently borrows the trigger of a neighbouring condition."""
    errors = _violations(trigger_timeframe=None)
    assert any(item.startswith("missing_trigger_timeframe") for item in errors), errors


@pytest.mark.parametrize(
    ("message", "trigger", "context", "confirmation"),
    [
        (
            "Monitor BTC/USDT using the 4h chart as context when the 15m candle "
            "rises open-to-close by at least 2%",
            "15m",
            ("4h",),
            (),
        ),
        (
            "Monitor BTC/USDT when the 15m candle rises open-to-close by at least "
            "2%, confirmed on the 1h",
            "15m",
            (),
            ("1h",),
        ),
        (
            "Monitor BTC/USDT when the 15m candle rises open-to-close by at least "
            "2% with 1h confirmation",
            "15m",
            (),
            ("1h",),
        ),
        (
            "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 2%",
            "15m",
            (),
            (),
        ),
    ],
)
def test_a_supporting_timeframe_never_replaces_the_trigger(
    message: str,
    trigger: str,
    context: tuple[str, ...],
    confirmation: tuple[str, ...],
) -> None:
    patch = deterministic_strategy_patch(
        StrategyDraftV2(), message, source_turn_id="turn-role"
    )
    assert patch is not None, message
    draft = apply_strategy_patch(StrategyDraftV2(), patch).draft
    assert draft.condition_ast is not None
    conditions = [
        item
        for item in draft.condition_ast.walk()
        if item.node_type is ConditionNodeType.CONDITION
    ]
    assert len(conditions) == 1, message
    condition = conditions[0]
    assert condition.trigger_timeframe == trigger, message
    assert tuple(condition.context_timeframes) == context, message
    assert tuple(condition.confirmation_timeframes) == confirmation, message
    errors = validate_draft_semantics(draft)
    if context:
        assert any(
            item.startswith("context_timeframe_not_executable") for item in errors
        ), message
    elif confirmation:
        assert any(
            item.startswith("confirmation_timeframe_not_executable") for item in errors
        ), message
    else:
        assert errors == [], message


# --------------------------------------------------------------------------------
# The gate in front of the deterministic parser shares its operator vocabulary.
# --------------------------------------------------------------------------------


def test_the_primitive_gate_uses_the_shared_comparison_vocabulary() -> None:
    """Every phrase in the one table is available to the launch parser's own regex."""
    alternation = comparator_alternation()
    compiled = re.compile(alternation, re.IGNORECASE)
    for term, _comparator in OPERATOR_TERMS:
        assert re.escape(term) in alternation, term
        assert compiled.search(f" {term} "), term


@pytest.mark.parametrize(
    ("phrase", "operator"),
    [
        ("is equal to", Comparator.EQUAL),
        ("is above", Comparator.GREATER_THAN),
        ("is below", Comparator.LESS_THAN),
        ("is greater than", Comparator.GREATER_THAN),
        ("is less than", Comparator.LESS_THAN),
        ("is at least", Comparator.GREATER_THAN_OR_EQUAL),
        ("is at most", Comparator.LESS_THAN_OR_EQUAL),
        ("is no more than", Comparator.LESS_THAN_OR_EQUAL),
        ("is no less than", Comparator.GREATER_THAN_OR_EQUAL),
        ("is at or above", Comparator.GREATER_THAN_OR_EQUAL),
        ("is at or below", Comparator.LESS_THAN_OR_EQUAL),
        ("crosses above", Comparator.CROSSES_ABOVE),
        ("crosses below", Comparator.CROSSES_BELOW),
    ],
)
def test_every_comparison_phrase_reaches_a_fixed_level_primitive(
    phrase: str, operator: Comparator
) -> None:
    message = f"Monitor BTC/USDT when price {phrase} 3500 on 1h"
    patch = deterministic_strategy_patch(
        StrategyDraftV2(), message, source_turn_id="turn-op"
    )
    assert patch is not None, message
    draft = apply_strategy_patch(StrategyDraftV2(), patch).draft
    assert draft.condition_ast is not None, message
    assert draft.condition_ast.operator is operator, message
    assert draft.condition_ast.threshold == 3500, message
    assert validate_draft_semantics(draft) == [], message


@pytest.mark.parametrize(
    "message",
    [
        "Monitor BTC/USDT when price sweeps below the previous candle low and "
        "reclaims it on 15m",
        "Monitor BTC/USDT when price sweeps below the previous candle low, then "
        "reclaims it, on 15m",
    ],
)
def test_one_mechanic_split_across_fragments_is_still_one_primitive(
    message: str,
) -> None:
    patch = deterministic_strategy_patch(
        StrategyDraftV2(), message, source_turn_id="turn-sweep"
    )
    assert patch is not None, message
    draft = apply_strategy_patch(StrategyDraftV2(), patch).draft
    assert draft.condition_ast is not None
    assert draft.condition_ast.formula is FormulaKind.SWEEP_AND_RECLAIM, message
    assert draft.condition_ast.operator is Comparator.IS_TRUE, message


def test_a_pierce_without_its_reclaim_is_not_treated_as_sweep_and_reclaim() -> None:
    """The reclaim half must be stated. It is never assumed from the pierce alone."""
    patch = deterministic_strategy_patch(
        StrategyDraftV2(),
        "Monitor BTC/USDT when price sweeps below the previous candle low on 15m",
        source_turn_id="turn-pierce",
    )
    assert patch is None


# --------------------------------------------------------------------------------
# A symbol named in order to exclude it is never also included.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "Monitor BTC/USDT only and exclude LTC/USDT on 15m when price is above 3500",
        "Monitor BTCUSDT, exclude LTCUSDT, on 15m when price is above 3500",
        "Watch BTC/USDT on 15m when price is above 3500 but not LTC/USDT",
        "Monitor BTC/USDT on 15m when price is above 3500, excluding LTC/USDT",
        "نحتاج Watchlist لـ BTCUSDT فقط ونستبعد LTCUSDT on 15m when price is above 3500",
    ],
)
def test_an_excluded_symbol_is_never_also_added_as_an_inclusion(message: str) -> None:
    """Both lists holding the same symbol made the whole patch illegal.

    `apply_strategy_patch` rejects a patch that includes and excludes one symbol, so
    a builder that copied every *mentioned* symbol into the inclusions threw away the
    trader's entire instruction instead of applying it.
    """
    patch = deterministic_strategy_patch(
        StrategyDraftV2(), message, source_turn_id="turn-universe"
    )
    assert patch is not None, message
    assert not set(patch.add_inclusions) & set(patch.add_exclusions), message
    draft = apply_strategy_patch(StrategyDraftV2(), patch).draft
    assert "LTC/USDT" in draft.universe.excluded_symbols, message
    assert "LTC/USDT" not in draft.universe.included_symbols, message
    assert validate_draft_semantics(draft) == [], message


# --------------------------------------------------------------------------------
# A transport failure is never reported as a strategy defect.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "exc"),
    [
        ("connection refused", httpx.ConnectError("All connection attempts failed")),
        (
            "server disconnect",
            httpx.RemoteProtocolError("Server disconnected without sending a response."),
        ),
        ("partial write", httpx.WriteError("connection broken")),
        ("read error", httpx.ReadError("peer closed connection")),
        ("connect timeout", httpx.ConnectTimeout("connect timed out")),
        ("read timeout", httpx.ReadTimeout("read timed out")),
        ("pool timeout", httpx.PoolTimeout("no free connection")),
        ("dns failure", httpx.ConnectError("[Errno -2] Name or service not known")),
        ("invalid json", json.JSONDecodeError("Expecting value", "{truncated", 0)),
        ("os level refusal", ConnectionRefusedError("refused")),
    ],
)
def test_every_transport_failure_is_reported_as_retryable_infrastructure(
    label: str, exc: BaseException
) -> None:
    envelope = setup_chat_error_envelope(exc)
    assert envelope.stage == "provider", label
    assert envelope.retryable is True, label
    assert envelope.error_code.startswith("TARGET_"), label
    assert envelope.message, label


@pytest.mark.parametrize(
    ("label", "exc"),
    [
        ("wrapped connect error", httpx.ConnectError("All connection attempts failed")),
        (
            "wrapped disconnect",
            httpx.RemoteProtocolError("Server disconnected without sending a response."),
        ),
    ],
)
def test_a_transport_failure_is_found_even_when_another_error_wraps_it(
    label: str, exc: BaseException
) -> None:
    try:
        raise exc
    except type(exc) as inner:  # noqa: PERF203 - the chain is the point of the test
        wrapper = RuntimeError("the setup turn failed")
        wrapper.__cause__ = inner
    envelope = setup_chat_error_envelope(wrapper)
    assert envelope.stage == "provider", label
    assert envelope.retryable is True, label


def test_a_real_compile_failure_is_still_reported_as_a_compile_failure() -> None:
    """Widening transport detection must not swallow genuine strategy defects."""
    envelope = setup_chat_error_envelope(ValueError("the draft has no conditions"))
    assert envelope.stage == "compile"
    assert envelope.retryable is False
