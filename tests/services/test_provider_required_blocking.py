from uuid import uuid4

from ai_market_monitor.cockpit_service import StrategyCockpitService
from ai_market_monitor.engine.condition_registry import condition_registry_payload
from ai_market_monitor.schemas.strategy import ConditionGroup, ConditionRule, LogicalOperator
from tests.factories import load_strategy


def _provider_required_condition() -> ConditionRule:
    payload = condition_registry_payload(include_provider_required=True)
    item = next(item for item in payload["items"] if item["availability"] == "provider_required")
    condition = ConditionRule.model_validate(item["condition_template"])
    return condition.model_copy(update={"required": True})


async def test_provider_required_mandatory_condition_blocks_activation_validation():
    condition = _provider_required_condition()
    definition = load_strategy().model_copy(deep=True)
    definition.risk.enabled = False
    definition.conditions = ConditionGroup(
        key="provider_required_group",
        operator=LogicalOperator.AND,
        children=[condition],
    )

    result = await StrategyCockpitService(None).validate_definition(
        user_id=uuid4(),
        definition=definition,
        persist=False,
    )

    assert result["blocking"] is True
    assert result["critical_count"] >= 1
    assert any(
        finding["code"] == "required_data_unavailable"
        for finding in result["findings"]
    )


async def test_provider_required_optional_condition_warns_but_does_not_block():
    condition = _provider_required_condition().model_copy(update={"required": False})
    definition = load_strategy().model_copy(deep=True)
    definition.risk.enabled = False
    definition.conditions = ConditionGroup(
        key="provider_required_group",
        operator=LogicalOperator.AND,
        children=[condition],
    )

    result = await StrategyCockpitService(None).validate_definition(
        user_id=uuid4(),
        definition=definition,
        persist=False,
    )

    assert result["blocking"] is False
    assert result["warning_count"] >= 1
    assert any(
        finding["code"] == "required_data_unavailable"
        and finding["severity"] == "warning"
        for finding in result["findings"]
    )
