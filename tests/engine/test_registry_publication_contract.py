from collections import Counter

import pytest

from ai_market_monitor.engine.builder_operations import (
    _probe_values,
    build_condition,
    mechanic_catalog,
)
from ai_market_monitor.engine.capabilities import CAPABILITIES
from ai_market_monitor.engine.capability_compatibility import compatibility_report
from ai_market_monitor.engine.condition_registry import condition_registry_payload
from ai_market_monitor.engine.strategy_compiler_v2 import compile_strategy_draft_v2
from ai_market_monitor.schemas.strategy import ConditionRule
from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2

CANDLE_INPUTS = {
    "min_body_percent": ("number", 25, 60),
    "max_body_percent": ("number", 40, 10),
    "wick_ratio": ("number", 2, 4),
    "trend_context_required": ("boolean", False, True),
    "confirmation_required": ("boolean", False, True),
}


def test_normal_registry_publishes_exactly_effective_available_contracts():
    payload = condition_registry_payload()
    specs = {spec.key: spec for spec in CAPABILITIES}
    keys = [item["key"] for item in payload["items"]]
    available = {row.key for row in compatibility_report() if row.availability == "available"}
    assert len(keys) == len(set(keys))
    assert set(keys) == available
    counts = Counter(item["builder_category"] for item in payload["items"])
    for category in payload["categories"]:
        expected = (
            len(payload["logic_operators"])
            if category["key"] == "advanced_logic"
            else counts[category["key"]]
        )
        assert category["count"] == expected
    for item in payload["items"]:
        assert item["availability"] == "available"
        assert item["parameter_schema"] == specs[item["key"]].parameter_schema
        ConditionRule.model_validate(item["condition_template"])
        assert {p["name"] for p in item["parameters"]} <= set(
            item["parameter_schema"]["properties"]
        ), item["key"]


@pytest.mark.parametrize("name", CANDLE_INPUTS)
def test_every_candle_input_has_one_published_contract(name):
    specs = {spec.key: spec for spec in CAPABILITIES}
    for item in condition_registry_payload()["items"]:
        if item["condition_type"] != "candle_pattern":
            continue
        expected_type, default, changed = CANDLE_INPUTS[name]
        metadata = {p["name"]: p for p in item["parameters"]}[name]
        schema = item["parameter_schema"]["properties"][name]
        assert metadata["type"] == schema["type"] == expected_type
        assert metadata["default"] == schema["default"] == default
        assert item["default_parameters"][name] == default
        assert specs[item["key"]].default_parameters[name] == default
        assert list(metadata["options"]) == schema.get("enum", [])
        for bound in ("minimum", "maximum"):
            assert metadata[bound] == schema.get(bound)
        template = item["condition_template"]
        assert template["left"]["parameters"][name] == default
        template["left"]["parameters"][name] = changed
        template["resolved_parameters"][name] = changed
        rule = ConditionRule.model_validate(template)
        reloaded = ConditionRule.model_validate_json(rule.model_dump_json())
        assert reloaded.left.parameters[name] == changed
        assert reloaded.resolved_parameters[name] == changed


@pytest.mark.parametrize("name", CANDLE_INPUTS)
def test_candle_form_inputs_survive_builder_validation_and_compilation(name):
    candles = {spec.key for spec in CAPABILITIES if spec.condition_type == "candle_pattern"}
    for mechanic in mechanic_catalog():
        if mechanic.capability_key not in candles or not mechanic.available:
            continue
        changed = CANDLE_INPUTS[name][2]
        assert mechanic.parameter(name) is not None, mechanic.key
        node, _ = build_condition(
            mechanic_key=mechanic.key,
            values={**_probe_values(mechanic), name: changed},
            source_turn_id="synthetic-candle-publication",
        )
        draft = StrategyDraftV2(condition_ast=node)
        reloaded = StrategyDraftV2.model_validate_json(draft.model_dump_json())
        definition = compile_strategy_draft_v2(reloaded)
        rule = definition.conditions.children[0]
        assert rule.left.parameters[name] == changed, mechanic.key
