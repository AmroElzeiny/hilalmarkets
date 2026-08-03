from dataclasses import replace
from uuid import uuid4

from sqlalchemy import select

from ai_market_monitor.db.models import Alert
from ai_market_monitor.db.models.enums import (
    AlertType,
    LogicalOperator,
    ScanOutcome,
    StrategyStatus,
)
from ai_market_monitor.engine.dedup import AlertFatigueGuard
from ai_market_monitor.engine.evaluator import (
    StrategyRuleEngine,
    strategy_evaluation_directions,
)
from ai_market_monitor.engine.forensics import AlertEvidence, ForensicInvestigationService
from ai_market_monitor.engine.forward_test import ForwardTestEngine
from ai_market_monitor.engine.models import EvaluationState
from ai_market_monitor.engine.risk import RiskCalculator
from ai_market_monitor.schemas.strategy import (
    Comparator,
    ConditionGroup,
    ConditionRule,
    Operand,
    OperandKind,
    StrategyDefinition,
    StrategyDirection,
)
from tests.factories import candle_sets, load_strategy, market


def test_identical_market_data_produces_identical_proofs():
    strategy = load_strategy()
    evaluation_time = candle_sets()["15m"][-1].timestamp
    engine = StrategyRuleEngine()
    first = engine.evaluate(
        strategy,
        market(),
        candle_sets(),
        evaluation_time=evaluation_time,
        strategy_version="1",
        previous_score=80,
    )
    second = engine.evaluate(
        strategy,
        market(),
        candle_sets(),
        evaluation_time=evaluation_time,
        strategy_version="1",
        previous_score=80,
    )
    assert first.proof_receipt() == second.proof_receipt()
    assert first.near_miss.current_score == 90
    assert first.near_miss.closest_missing_condition.condition_id == "relative_volume"
    assert first.conditions[-1].actual_value == 1.42
    assert first.conditions[-1].required_value == 1.5
    assert first.conditions[-1].state == EvaluationState.FAILED


def test_evaluator_proof_retains_compiled_semantic_contract_and_provenance():
    strategy = load_strategy()
    strategy.risk.enabled = False
    rule = ConditionRule(
        key="provenance_rule",
        label="Close above zero",
        condition_type=strategy.conditions.children[0].condition_type,
        timeframe="15m",
        left=Operand(kind=OperandKind.PRICE, field="close"),
        comparator=Comparator.GREATER_THAN,
        right=Operand(kind=OperandKind.CONSTANT, value=0),
        resolved_parameters={
            "formula": "fixed_reference_level",
            "movement_direction": "neutral",
            "strategy_bias": "neutral",
            "unit": "price",
            "reference_timeframe": "15m",
            "reference_definition": "fixed price level 0",
            "lookback": 20,
        },
        source_turn_id="turn-proof-1234",
        source_fragment="close is above zero",
        ast_path=[1, 0],
    )
    strategy.conditions = ConditionGroup(
        key="proof_root",
        operator=LogicalOperator.AND,
        children=[rule],
    )
    sets = candle_sets()

    result = StrategyRuleEngine().evaluate(
        strategy,
        market(),
        sets,
        evaluation_time=sets["15m"][-1].timestamp,
        strategy_version="semantic-proof",
    )

    contract = result.proof_receipt()["conditions"][0]["semantic_contract"]
    assert contract == {
        "formula": "fixed_reference_level",
        "operator": "gt",
        "threshold": 0,
        "movement_direction": "neutral",
        "strategy_bias": "neutral",
        "unit": "price",
        "timeframe_role": "trigger",
        "trigger_timeframe": "15m",
        "context_timeframes": [],
        "confirmation_timeframes": [],
        "reference_timeframe": "15m",
        "reference_definition": "fixed price level 0",
        "lookback": 20,
        "source_operands": [],
        "condition_symbols": [],
        "ast_path": [1, 0],
        "source_turn_id": "turn-proof-1234",
        "source_fragment": "close is above zero",
    }


def test_confirmed_setup_generates_complete_proof_and_risk():
    strategy = load_strategy()
    sets = candle_sets(volume_multiplier=1.6)
    result = StrategyRuleEngine().evaluate(
        strategy,
        market(),
        sets,
        evaluation_time=sets["15m"][-1].timestamp,
        strategy_version="2",
        previous_score=89,
        chart_reference="chart://sol-proof",
    )
    proof = result.proof_receipt()
    assert result.outcome == ScanOutcome.CONFIRMED
    assert all(condition["actual_value"] is not None for condition in proof["conditions"])
    assert proof["chart_reference"] == "chart://sol-proof"
    assert proof["entry_zone"]["stop_distance_percent"] <= 2
    assert proof["reward_to_risk"] >= 2
    assert proof["alert_trust_score"]["deterministic"] is True
    assert proof["alert_trust_score"]["score"] > 0


def test_candle_close_mode_does_not_look_ahead_into_active_candle():
    strategy = load_strategy()
    sets = candle_sets(volume_multiplier=1.0, include_active_lookahead=True)
    active_time = sets["15m"][-1].timestamp
    result = StrategyRuleEngine().evaluate(
        strategy,
        market(),
        sets,
        evaluation_time=active_time,
        strategy_version="1",
    )
    volume_condition = next(c for c in result.conditions if c.condition_id == "relative_volume")
    assert volume_condition.actual_value == 1.0
    assert result.outcome != ScanOutcome.CONFIRMED


def test_missing_history_and_unsupported_condition_fail_safely():
    strategy = load_strategy()
    short_sets = {"15m": candle_sets()["15m"][:10], "4h": candle_sets()["4h"][:10]}
    skipped = StrategyRuleEngine().evaluate(
        strategy,
        market(),
        short_sets,
        evaluation_time=short_sets["15m"][-1].timestamp,
        strategy_version="1",
    )
    assert skipped.outcome == ScanOutcome.SKIPPED
    assert "insufficient_historical_candles" in skipped.market_filters.reasons
    assert skipped.setup_state is None

    broken = strategy.model_copy(deep=True)
    broken.conditions.children[0].right.name = "not_supported"
    result = StrategyRuleEngine().evaluate(
        broken,
        market(),
        candle_sets(),
        evaluation_time=candle_sets()["15m"][-1].timestamp,
        strategy_version="broken",
    )
    assert result.conditions[0].state == EvaluationState.ERROR
    assert result.near_miss.should_alert is False
    assert result.outcome == ScanOutcome.ERROR


def test_risk_calculator_position_size_uses_explicit_balance_only():
    strategy = load_strategy()
    strategy.position_sizing.enabled = True
    strategy.position_sizing.account_risk_percent = 1
    result_without_balance = RiskCalculator().calculate(strategy, candle_sets()["15m"])
    result_with_balance = RiskCalculator().calculate(
        strategy, candle_sets()["15m"], account_balance=10_000
    )
    assert result_without_balance.position_size is None
    assert result_with_balance.position_size is not None


def test_nested_or_requires_one_complete_branch():
    strategy = load_strategy()
    ema = strategy.conditions.children[0].model_copy(deep=True)
    sweep = strategy.conditions.children[1].model_copy(deep=True)
    volume_a = strategy.conditions.children[2].model_copy(deep=True)
    volume_b = strategy.conditions.children[2].model_copy(
        deep=True,
        update={"key": "relative_volume_branch_b"},
    )
    strategy.conditions = ConditionGroup(
        key="entry_conditions",
        operator=LogicalOperator.OR,
        children=[
            ConditionGroup(
                key="branch_a",
                operator=LogicalOperator.AND,
                children=[ema, volume_a],
            ),
            ConditionGroup(
                key="branch_b",
                operator=LogicalOperator.AND,
                children=[sweep, volume_b],
            ),
        ],
    )
    result = StrategyRuleEngine().evaluate(
        strategy,
        market(),
        candle_sets(volume_multiplier=1.0),
        evaluation_time=candle_sets()["15m"][-1].timestamp,
        strategy_version="nested-or",
    )
    assert result.outcome != ScanOutcome.CONFIRMED
    assert result.condition_tree is not None
    assert result.condition_tree.state == EvaluationState.FAILED
    proof_tree = result.proof_receipt()["condition_tree"]
    assert proof_tree["operator"] == "or"
    assert proof_tree["selected_child_id"] in {"branch_a", "branch_b"}


def test_visual_builder_not_and_sequence_groups_are_deterministic():
    strategy = load_strategy()
    passed_price_rule = ConditionRule(
        key="price_above_zero",
        label="Price above zero",
        condition_type=strategy.conditions.children[0].condition_type,
        timeframe="15m",
        left=Operand(kind=OperandKind.PRICE, field="close"),
        comparator=Comparator.GREATER_THAN,
        right=Operand(kind=OperandKind.CONSTANT, value=0),
    )
    strategy.conditions = ConditionGroup(
        key="entry_conditions",
        operator=LogicalOperator.NOT,
        children=[passed_price_rule],
    )
    not_result = StrategyRuleEngine().evaluate(
        strategy,
        market(),
        candle_sets(volume_multiplier=1.6),
        evaluation_time=candle_sets()["15m"][-1].timestamp,
        strategy_version="not-group",
    )
    assert not_result.condition_tree is not None
    assert not_result.condition_tree.state == EvaluationState.FAILED
    assert not_result.proof_receipt()["condition_tree"]["operator"] == "not"

    sequenced = load_strategy()
    sequenced.conditions = ConditionGroup(
        key="entry_conditions",
        operator=LogicalOperator.SEQUENCE,
        children=[
            sequenced.conditions.children[0].model_copy(deep=True),
            sequenced.conditions.children[2].model_copy(deep=True),
        ],
    )
    sequence_result = StrategyRuleEngine().evaluate(
        sequenced,
        market(),
        candle_sets(volume_multiplier=1.6),
        evaluation_time=candle_sets()["15m"][-1].timestamp,
        strategy_version="sequence-group",
    )
    assert sequence_result.condition_tree is not None
    assert sequence_result.condition_tree.state == EvaluationState.PASSED
    assert sequence_result.proof_receipt()["condition_tree"]["operator"] == "sequence"


def test_crosses_above_requires_previous_value_on_other_side():
    strategy = load_strategy()
    strategy.conditions = ConditionGroup(
        key="entry_conditions",
        operator=LogicalOperator.AND,
        children=[
            ConditionRule(
                key="price_cross",
                label="Price crosses 100",
                condition_type=strategy.conditions.children[0].condition_type,
                timeframe="15m",
                left=Operand(kind=OperandKind.PRICE, field="close"),
                comparator=Comparator.CROSSES_ABOVE,
                right=Operand(kind=OperandKind.CONSTANT, value=100),
            )
        ],
    )
    sets = candle_sets(volume_multiplier=1.6)
    previous = sets["15m"][-2]
    current = sets["15m"][-1]
    sets["15m"][-2] = previous.__class__(
        timestamp=previous.timestamp,
        open=99,
        high=100,
        low=98,
        close=99,
        volume=previous.volume,
        is_closed=True,
    )
    sets["15m"][-1] = current.__class__(
        timestamp=current.timestamp,
        open=99,
        high=102,
        low=98,
        close=101,
        volume=current.volume,
        is_closed=True,
    )
    crossed = StrategyRuleEngine().evaluate(
        strategy,
        market(),
        sets,
        evaluation_time=sets["15m"][-1].timestamp,
        strategy_version="cross",
    )
    condition = crossed.conditions[0]
    assert condition.state == EvaluationState.PASSED
    assert condition.previous_actual_value == 99
    assert condition.actual_value == 101

    sets["15m"][-2] = sets["15m"][-2].__class__(
        timestamp=sets["15m"][-2].timestamp,
        open=100,
        high=102,
        low=99,
        close=101,
        volume=sets["15m"][-2].volume,
        is_closed=True,
    )
    already_above = StrategyRuleEngine().evaluate(
        strategy,
        market(),
        sets,
        evaluation_time=sets["15m"][-1].timestamp,
        strategy_version="cross",
    )
    assert already_above.conditions[0].state == EvaluationState.FAILED


def test_short_risk_geometry_places_stop_above_and_targets_below():
    strategy = load_strategy()
    strategy.direction = StrategyDirection.SHORT
    calculation = RiskCalculator().calculate(strategy, candle_sets()["15m"])
    assert calculation.direction == "short"
    assert calculation.stop_price > calculation.entry_price
    assert all(float(target["price"]) < calculation.entry_price for target in calculation.targets)


def test_both_direction_strategy_can_be_evaluated_for_each_explicit_side():
    strategy = load_strategy()
    strategy.direction = StrategyDirection.BOTH
    sets = candle_sets(volume_multiplier=1.6)
    long_result = StrategyRuleEngine().evaluate(
        strategy,
        market(),
        sets,
        evaluation_time=sets["15m"][-1].timestamp,
        strategy_version="both",
        evaluation_direction=StrategyDirection.LONG,
    )
    short_result = StrategyRuleEngine().evaluate(
        strategy,
        market(),
        sets,
        evaluation_time=sets["15m"][-1].timestamp,
        strategy_version="both",
        evaluation_direction=StrategyDirection.SHORT,
    )
    assert long_result.direction == "long"
    assert short_result.direction == "short"
    assert long_result.strategy_schema_hash == short_result.strategy_schema_hash
    assert long_result.risk.stop_price < long_result.risk.entry_price
    assert short_result.risk.stop_price > short_result.risk.entry_price


def test_neutral_direction_is_one_runtime_evaluation_not_long_and_short():
    strategy = load_strategy()
    strategy.direction = StrategyDirection.NEUTRAL

    assert strategy_evaluation_directions(strategy) == (None,)


def test_context_and_confirmation_timeframes_are_independent_runtime_prerequisites():
    strategy = load_strategy()
    strategy.risk.enabled = False
    rule = ConditionRule(
        key="multi_timeframe_rule",
        label="Close above zero on every role",
        condition_type=strategy.conditions.children[0].condition_type,
        timeframe="15m",
        context_timeframes=["4h"],
        confirmation_timeframes=["1h"],
        left=Operand(kind=OperandKind.PRICE, field="close"),
        comparator=Comparator.GREATER_THAN,
        right=Operand(kind=OperandKind.CONSTANT, value=0),
        resolved_parameters={"formula": "fixed_reference_level"},
    )
    strategy.conditions = ConditionGroup(
        key="multi_timeframe_root",
        operator=LogicalOperator.AND,
        children=[rule],
    )
    strategy.supporting_timeframes = ["1h", "4h"]
    sets = candle_sets()
    sets["1h"] = list(sets["4h"])

    result = StrategyRuleEngine().evaluate(
        strategy,
        market(),
        sets,
        evaluation_time=sets["15m"][-1].timestamp,
        strategy_version="multi-timeframe",
    )

    condition = result.conditions[0]
    assert condition.passed
    role_evidence = condition.semantic_contract["timeframe_role_evaluations"]
    assert {(item["role"], item["timeframe"]) for item in role_evidence} == {
        ("context", "4h"),
        ("confirmation", "1h"),
    }


def test_risk_failure_is_preserved_in_proof_and_blocks_confirmation():
    strategy = load_strategy()
    strategy.direction = StrategyDirection.SHORT
    strategy.stop.method = "swing_low"
    sets = candle_sets(volume_multiplier=1.6)
    result = StrategyRuleEngine().evaluate(
        strategy,
        market(),
        sets,
        evaluation_time=sets["15m"][-1].timestamp,
        strategy_version="invalid-risk",
    )
    assert result.outcome != ScanOutcome.CONFIRMED
    assert result.risk is None
    assert result.risk_validation is not None
    assert result.risk_validation.state == EvaluationState.FAILED
    assert result.proof_receipt()["risk_validation"]["error_code"] == "stop_direction_invalid"


def test_strategy_hash_is_stable_across_mapping_order():
    first = load_strategy()
    payload = first.model_dump(mode="json")
    parameters = payload["conditions"]["children"][0]["right"]["parameters"]
    payload["conditions"]["children"][0]["right"]["parameters"] = {
        key: parameters[key] for key in reversed(parameters)
    }
    second = StrategyDefinition.model_validate(payload)
    assert first.canonical_hash() == second.canonical_hash()


def test_forensic_investigation_explains_cooldown_without_guessing():
    strategy = load_strategy()
    sets = candle_sets(volume_multiplier=1.6)
    investigation = ForensicInvestigationService().investigate(
        strategy=strategy,
        strategy_version="3",
        strategy_status=StrategyStatus.ACTIVE,
        market=market(),
        candle_sets=sets,
        approximate_time=sets["15m"][-1].timestamp,
        evidence=AlertEvidence(cooldown_blocked=True),
    )
    assert investigation.evaluated is True
    assert investigation.cooldown_prevented_delivery is True
    assert "cooldown" in investigation.explanation
    assert investigation.proof is not None


def test_forward_testing_records_hypothetical_not_executed_trade():
    strategy = load_strategy()
    assert strategy.forward_test.enabled is True
    sets = candle_sets(volume_multiplier=1.6)
    record = ForwardTestEngine().evaluate_live_tick(
        strategy,
        market(),
        sets,
        evaluation_time=sets["15m"][-1].timestamp,
        strategy_version="ft-1",
    )
    assert record is not None
    assert record.hypothetical_entry is not None
    assert record.proof["strategy_version"] == "ft-1"


async def test_alert_fatigue_guard_suppresses_duplicate_event_hash(test_context):
    strategy = load_strategy()
    sets = candle_sets(volume_multiplier=1.6)
    result = StrategyRuleEngine().evaluate(
        strategy,
        market(),
        sets,
        evaluation_time=sets["15m"][-1].timestamp,
        strategy_version="dedup",
    )
    async with test_context["session_factory"]() as session:
        guard = AlertFatigueGuard(session)
        user_id = uuid4()
        strategy_version_id = uuid4()
        first = await guard.check(
            result,
            user_id=user_id,
            strategy_version_id=strategy_version_id,
            alert_type=AlertType.CONFIRMED,
            cooldown_seconds=0,
            maximum_alerts_per_hour=10,
            daily_alert_budget=None,
        )
        assert first.allowed is True
        session.add(
            Alert(
                user_id=user_id,
                strategy_version_id=strategy_version_id,
                alert_type=AlertType.CONFIRMED,
                deduplication_key=first.deduplication_key,
                title="Confirmed",
                body="Body",
                proof_receipt=result.proof_receipt(),
            )
        )
        await session.commit()
        second = await guard.check(
            result,
            user_id=user_id,
            strategy_version_id=strategy_version_id,
            alert_type=AlertType.CONFIRMED,
            cooldown_seconds=0,
            maximum_alerts_per_hour=10,
            daily_alert_budget=None,
        )
        assert second.allowed is False
        assert second.reason == "duplicate_event_hash"
        assert await session.scalar(select(Alert.id)) is not None


async def test_alert_fatigue_guard_applies_weekly_budget_across_monitors(test_context):
    strategy = load_strategy()
    sets = candle_sets(volume_multiplier=1.6)
    result = StrategyRuleEngine().evaluate(
        strategy,
        market(),
        sets,
        evaluation_time=sets["15m"][-1].timestamp,
        strategy_version="weekly-budget",
    )
    async with test_context["session_factory"]() as session:
        user_id = uuid4()
        session.add_all(
            [
                Alert(
                    user_id=user_id,
                    strategy_version_id=uuid4(),
                    alert_type=AlertType.CONFIRMED,
                    deduplication_key=f"weekly-alert-{index}",
                    title="Confirmed",
                    body="Body",
                    proof_receipt={"symbol": f"COIN{index}/USDT"},
                )
                for index in range(2)
            ]
        )
        await session.commit()

        decision = await AlertFatigueGuard(session).check(
            result,
            user_id=user_id,
            strategy_version_id=uuid4(),
            alert_type=AlertType.CONFIRMED,
            cooldown_seconds=0,
            maximum_alerts_per_hour=10,
            daily_alert_budget=None,
            weekly_alert_budget=2,
        )

        assert decision.allowed is False
        assert decision.reason == "weekly_alert_budget"


async def test_alert_fatigue_guard_cools_down_same_symbol_only(test_context):
    strategy = load_strategy()
    sets = candle_sets(volume_multiplier=1.6)
    result = StrategyRuleEngine().evaluate(
        strategy,
        market(),
        sets,
        evaluation_time=sets["15m"][-1].timestamp,
        strategy_version="symbol-cooldown",
    )
    async with test_context["session_factory"]() as session:
        guard = AlertFatigueGuard(session)
        user_id = uuid4()
        strategy_version_id = uuid4()
        first = await guard.check(
            result,
            user_id=user_id,
            strategy_version_id=strategy_version_id,
            alert_type=AlertType.CONFIRMED,
            cooldown_seconds=900,
            maximum_alerts_per_hour=10,
            daily_alert_budget=None,
        )
        assert first.allowed is True
        session.add(
            Alert(
                user_id=user_id,
                strategy_version_id=strategy_version_id,
                alert_type=AlertType.CONFIRMED,
                deduplication_key=first.deduplication_key,
                title="BTC/USDT - Confirmed",
                body="Body",
                proof_receipt=result.proof_receipt(),
            )
        )
        await session.commit()

        same_symbol = replace(
            result,
            market_data_timestamp=sets["15m"][-2].timestamp,
            evaluation_time=sets["15m"][-2].timestamp,
        )
        same_decision = await guard.check(
            same_symbol,
            user_id=user_id,
            strategy_version_id=strategy_version_id,
            alert_type=AlertType.CONFIRMED,
            cooldown_seconds=900,
            maximum_alerts_per_hour=10,
            daily_alert_budget=None,
        )
        assert same_decision.allowed is False
        assert same_decision.reason == "symbol_cooldown"

        other_symbol = replace(
            same_symbol,
            symbol="ETH/USDT",
        )
        other_decision = await guard.check(
            other_symbol,
            user_id=user_id,
            strategy_version_id=strategy_version_id,
            alert_type=AlertType.CONFIRMED,
            cooldown_seconds=900,
            maximum_alerts_per_hour=10,
            daily_alert_budget=None,
        )
        assert other_decision.allowed is True
