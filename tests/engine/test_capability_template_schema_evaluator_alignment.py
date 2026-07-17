from ai_market_monitor.engine.concept_e2e import concept_e2e_rows
from ai_market_monitor.engine.condition_registry import condition_registry_payload
from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.engine.models import EvaluationState
from ai_market_monitor.schemas.strategy import ConditionGroup, ConditionRule, LogicalOperator
from tests.factories import candle_sets, load_strategy, market


def test_green_capability_templates_validate_against_strategy_schema():
    payload = {item["key"]: item for item in condition_registry_payload()["items"]}

    for row in concept_e2e_rows():
        if row["current_status"] != "GREEN":
            continue
        condition = ConditionRule.model_validate(
            payload[row["capability_key"]]["condition_template"]
        )
        definition = load_strategy().model_copy(deep=True)
        definition.risk.enabled = False
        definition.universe.min_historical_candles = 1
        definition.conditions = ConditionGroup(
            key="single_capability",
            operator=LogicalOperator.AND,
            children=[condition],
        )

        assert definition.canonical_hash()


def test_green_capabilities_reach_non_error_evaluator_and_proof_state():
    payload = {item["key"]: item for item in condition_registry_payload()["items"]}
    history = candle_sets(volume_multiplier=1.6)
    evaluated_at = history["15m"][-1].timestamp

    for row in concept_e2e_rows():
        if row["current_status"] != "GREEN":
            continue
        condition = ConditionRule.model_validate(
            payload[row["capability_key"]]["condition_template"]
        )
        definition = load_strategy().model_copy(deep=True)
        definition.risk.enabled = False
        definition.universe.min_historical_candles = 1
        definition.conditions = ConditionGroup(
            key="single_capability",
            operator=LogicalOperator.AND,
            children=[condition],
        )
        result = StrategyRuleEngine().evaluate(
            definition,
            market(),
            history,
            evaluation_time=evaluated_at,
            strategy_version="capability-e2e",
            condition_context={
                "evaluation_time": evaluated_at,
                "last_symbol_triggered_at": None,
                "last_strategy_triggered_at": None,
                "alerts_last_hour": 0,
                "alerts_last_day": 0,
                "setup_state": "forming",
                "setup_first_detected_at": evaluated_at,
                "setup_entry_zone_active": True,
                "setup_state_changed": True,
            },
        )

        proof = result.proof_receipt()
        assert result.conditions[0].state != EvaluationState.ERROR, row["capability_key"]
        assert proof["conditions"][0]["condition_id"] == condition.key
        assert proof["conditions"][0]["state"]
