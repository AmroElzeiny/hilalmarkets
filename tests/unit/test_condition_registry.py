from ai_market_monitor.engine.builder_templates import condition_template
from ai_market_monitor.engine.condition_registry import (
    CONDITION_REGISTRY,
    condition_registry_payload,
)
from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.schemas.strategy import ConditionRule
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter


def test_condition_registry_has_unique_rich_builder_ready_entries():
    payload = condition_registry_payload()
    keys = [item["key"] for item in payload["items"]]
    required_fields = {
        "key",
        "category",
        "display_name",
        "description",
        "supported_markets",
        "required_data",
        "supported_timeframes",
        "parameters",
        "default_parameters",
        "outputs",
        "supported_comparators",
        "example_sentence",
        "prompt_aliases",
        "visual_card_sentence",
        "risk_notes",
        "implementation_status",
        "evaluator_function",
        "warmup_candles",
        "test_cases",
        "condition_template",
        "semantic_tags",
        "intent_examples",
        "negative_examples",
        "direction_support",
        "temporal_behavior",
        "parameter_schema",
        "conflicts_with",
        "composes_with",
        "provider_requirements",
        "capability_version",
        "proof_template",
        "resource_cost",
    }
    assert len(keys) == len(set(keys))
    assert payload["schema_version"] == "2.0"
    assert len(payload["guidebook_categories"]) == 16
    assert payload["guidebook_categories"][0]["key"] == "popular"
    assert {
        "price",
        "indicator",
        "price_action",
        "candle_pattern",
        "time_session",
        "advanced_logic",
    }.issubset({category["key"] for category in payload["guidebook_categories"]})
    assert payload["counts"]["logic_operators"] == 12
    assert required_fields.issubset(payload["items"][0])
    assert payload["items"][0]["condition_template"]["capability_key"] == payload["items"][0]["key"]
    assert {
        "stochastic_rsi",
        "money_flow_index",
        "ichimoku_cloud",
        "supertrend",
        "keltner_channels",
        "donchian_channels",
        "bollinger_percent_b",
    }.issubset(keys)
    assert {"sma", "ema", "adx", "not", "sequence"}.issubset(
        payload["deduplication"]["already_present"]
    )
    assert all(item["availability"] == "available" for item in payload["items"])
    assert payload["hidden_provider_required"]["count"] > 0
    assert payload["hidden_provider_required"]["hidden_from_normal_ui"] is True


def test_condition_registry_can_include_provider_required_for_audit_only():
    payload = condition_registry_payload(include_provider_required=True)
    provider_required = [
        item for item in payload["items"] if item["availability"] == "provider_required"
    ]

    assert provider_required
    assert payload["hidden_provider_required"]["hidden_from_normal_ui"] is False
    assert payload["hidden_provider_required"]["count"] == len(provider_required)


def test_every_executable_capability_emits_a_valid_condition_template():
    for capability in CONDITION_REGISTRY.search(executable_only=True):
        condition = ConditionRule.model_validate(condition_template(capability))
        assert condition.key == capability.key
        assert condition.required_data == list(capability.required_data)


def test_condition_registry_search_uses_names_and_prompt_aliases():
    assert CONDITION_REGISTRY.search("mfi")[0].key == "money_flow_index"
    assert CONDITION_REGISTRY.search("kumo cloud")[0].key == "ichimoku_cloud"
    assert all(item.category == "volatility" for item in CONDITION_REGISTRY.search(
        "", category="volatility"
    ))


async def test_prompt_aliases_create_extended_deterministic_conditions_without_price_noise():
    preview = await RuleBasedStrategyInterpreter().interpret(
        GuidedSetupRequest(
            exchange="binance",
            quote_currency="USDT",
            timeframe="15m",
            setup_mode="free_text",
            setup_text="MFI below 20 and price above HMA 55 on 1h",
            trigger_mode="candle_close",
            delivery_channels=["web"],
        )
    )
    conditions = preview.strategy.conditions.children
    assert preview.activation_blocked is False
    assert [condition.key for condition in conditions] == [
        "money_flow_index",
        "hull_moving_average",
    ]
    assert conditions[0].left.name == "money_flow_index"
    assert conditions[0].right.value == 20
    assert conditions[1].right.name == "hull_moving_average"
    assert conditions[1].right.parameters["period"] == 55
