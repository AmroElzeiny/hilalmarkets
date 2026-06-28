from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ai_market_monitor.db.models.enums import ConditionType, LogicalOperator
from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.engine.models import EvaluationState
from ai_market_monitor.schemas.strategy import (
    Comparator,
    ConditionGroup,
    ConditionRule,
    Operand,
    OperandKind,
)
from ai_market_monitor.services.interfaces import Candle
from tests.factories import load_strategy, market


def _rule(key: str, comparator: Comparator, threshold: float) -> ConditionRule:
    return ConditionRule(
        key=key,
        label=key.replace("_", " ").title(),
        condition_type=ConditionType.INDICATOR,
        timeframe="15m",
        left=Operand(kind=OperandKind.PRICE, field="close"),
        comparator=comparator,
        right=Operand(kind=OperandKind.CONSTANT, value=threshold),
    )


def _candles(closes: list[float]) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(
            timestamp=start + timedelta(minutes=index * 15),
            open=close - 0.2,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=1000,
            is_closed=True,
        )
        for index, close in enumerate(closes)
    ]


def _evaluate(group: ConditionGroup, closes: list[float], **context):
    strategy = load_strategy()
    strategy.base_timeframe = "15m"
    strategy.supporting_timeframes = []
    strategy.conditions = group
    strategy.risk.enabled = False
    strategy.universe.min_historical_candles = 1
    history = _candles(closes)
    return StrategyRuleEngine().evaluate(
        strategy,
        market(),
        {"15m": history},
        evaluation_time=history[-1].timestamp,
        strategy_version="logic-test",
        condition_context=context,
    ).condition_tree


def test_within_last_and_persisted_for_use_closed_candle_history():
    within = ConditionGroup(
        key="recent",
        operator=LogicalOperator.WITHIN_LAST,
        parameters={"lookback_candles": 3},
        children=[_rule("above_100", Comparator.GREATER_THAN, 100)],
    )
    persisted = ConditionGroup(
        key="persistent",
        operator=LogicalOperator.PERSISTED_FOR,
        parameters={"candles_count": 3},
        children=[_rule("above_100", Comparator.GREATER_THAN, 100)],
    )
    closes = ([90.0] * 36) + [99, 101, 99, 98]
    assert _evaluate(within, closes).state == EvaluationState.PASSED
    assert _evaluate(persisted, ([90.0] * 37) + [101, 102, 103]).state == EvaluationState.PASSED
    assert _evaluate(persisted, closes).state == EvaluationState.FAILED


def test_sequence_count_and_conditional_branch_are_deterministic():
    sequence = ConditionGroup(
        key="ordered",
        operator=LogicalOperator.SEQUENCE,
        parameters={"max_candles_between": 3},
        children=[
            _rule("first_above_100", Comparator.GREATER_THAN, 100),
            _rule("then_above_105", Comparator.GREATER_THAN, 105),
        ],
    )
    count = ConditionGroup(
        key="two_of_three",
        operator=LogicalOperator.COUNT_OF,
        parameters={"minimum_pass_count": 2},
        children=[
            _rule("above_100", Comparator.GREATER_THAN, 100),
            _rule("above_105", Comparator.GREATER_THAN, 105),
            _rule("above_110", Comparator.GREATER_THAN, 110),
        ],
    )
    branch = ConditionGroup(
        key="branch",
        operator=LogicalOperator.CONDITIONAL_BRANCH,
        children=[
            _rule("if_above_100", Comparator.GREATER_THAN, 100),
            _rule("then_above_105", Comparator.GREATER_THAN, 105),
            _rule("otherwise_below_95", Comparator.LESS_THAN, 95),
        ],
    )
    closes = ([90.0] * 37) + [101, 102, 106]
    assert _evaluate(sequence, closes).state == EvaluationState.PASSED
    assert _evaluate(count, closes).state == EvaluationState.PASSED
    branch_result = _evaluate(branch, closes)
    assert branch_result.state == EvaluationState.PASSED
    assert branch_result.selected_child_id == "then_above_105"


def test_first_true_confirmed_cross_and_cooldown_context():
    first = ConditionGroup(
        key="first",
        operator=LogicalOperator.FIRST_TIME_TRUE,
        children=[_rule("above_100", Comparator.GREATER_THAN, 100)],
    )
    confirmed = ConditionGroup(
        key="confirmed_cross",
        operator=LogicalOperator.CROSS_WITH_CONFIRMATION,
        parameters={"confirmation_bars": 2},
        children=[_rule("cross_100", Comparator.CROSSES_ABOVE, 100)],
    )
    cooldown = ConditionGroup(
        key="cooldown",
        operator=LogicalOperator.COOLDOWN_CONDITION,
        parameters={"cooldown_minutes": 120, "scope": "per_symbol"},
        children=[_rule("above_100", Comparator.GREATER_THAN, 100)],
    )
    closes = ([90.0] * 37) + [99, 101, 102]
    assert _evaluate(first, closes).state == EvaluationState.FAILED
    first_closes = ([90.0] * 38) + [99, 101]
    assert _evaluate(first, first_closes).state == EvaluationState.PASSED
    assert _evaluate(confirmed, closes).state == EvaluationState.PASSED
    evaluation_time = _candles(closes)[-1].timestamp
    assert _evaluate(
        cooldown,
        closes,
        last_symbol_triggered_at=evaluation_time - timedelta(minutes=30),
    ).state == EvaluationState.FAILED
    assert _evaluate(
        cooldown,
        closes,
        last_symbol_triggered_at=evaluation_time - timedelta(minutes=121),
    ).state == EvaluationState.PASSED


def test_logic_operator_shape_validation_rejects_impossible_groups():
    with pytest.raises(ValidationError, match="exactly one child"):
        ConditionGroup(
            key="bad_not",
            operator=LogicalOperator.NOT,
            children=[
                _rule("one", Comparator.GREATER_THAN, 1),
                _rule("two", Comparator.GREATER_THAN, 2),
            ],
        )
    with pytest.raises(ValidationError, match="cannot exceed child count"):
        ConditionGroup(
            key="bad_count",
            operator=LogicalOperator.COUNT_OF,
            parameters={"minimum_pass_count": 2},
            children=[_rule("one", Comparator.GREATER_THAN, 1)],
        )
