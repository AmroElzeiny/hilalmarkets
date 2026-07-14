import pytest
from pydantic import ValidationError

from ai_market_monitor.engine.builder_templates import condition_template
from ai_market_monitor.engine.capabilities import all_capabilities, capability_by_key
from ai_market_monitor.engine.capability_resolver import CapabilityResolver
from ai_market_monitor.schemas.strategy import ConditionRule
from tests.factories import load_strategy


def test_every_capability_exposes_resolver_metadata_contract():
    required_fields = {
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
    for capability in all_capabilities():
        payload = capability.to_dict()
        assert required_fields.issubset(payload), capability.key
        assert capability.semantic_tags, capability.key
        assert capability.intent_examples, capability.key
        assert set(capability.direction_support) <= {"bullish", "bearish", "neutral"}
        assert capability.temporal_behavior in {
            "current_candle",
            "previous_candle",
            "within_n_candles",
        }
        assert capability.parameter_schema["type"] == "object"
        assert capability.capability_version
        assert capability.proof_template
        assert capability.resource_cost in {"low", "medium", "high"}


def test_pdl_aliases_resolve_to_one_immutable_capability():
    resolver = CapabilityResolver()
    for prompt in (
        "coins which swept PDL",
        "sweep the previous daily low",
        "previous day low sweep",
    ):
        fragment = resolver.resolve_prompt(prompt).fragments[0]
        assert fragment.status == "matched"
        assert fragment.candidates[0].capability_key == "previous_daily_low_sweep"

    rule = resolver.validate_selection(
        capability_key="previous_daily_low_sweep",
        parameters={"timezone": "UTC"},
        timeframe="15m",
        required=True,
        source_fragment="coins which swept PDL",
    )
    assert rule.capability_key == "previous_daily_low_sweep"
    assert rule.left.name == "daily_low_swept"
    assert rule.left.parameters["timezone"] == "UTC"
    with pytest.raises(ValidationError):
        rule.capability_key = "previous_daily_high_sweep"


def test_unknown_terms_require_clarification_but_clear_inputs_do_not():
    resolver = CapabilityResolver()
    clear = resolver.resolve_prompt("RSI below 30 on 15m Binance USDT spot pairs")
    assert clear.needs_clarification is False
    assert clear.candidate_keys[0] == "rsi_threshold"

    unknown = resolver.resolve_prompt("RSI below 30 with XYZ confirmation")
    assert unknown.needs_clarification is True
    assert "XYZ" in unknown.fragments[0].unknown_terms


@pytest.mark.parametrize(
    ("prompt", "expected_keys"),
    [
        ("volume is at least 1.5x its 20 candle average", {"volume_ratio"}),
        (
            "Bollinger squeeze with a bullish engulfing candle",
            {"bollinger_squeeze", "bullish_engulfing"},
        ),
        ("coins up 5% today", {"percent_change_lookback"}),
        ("five red daily candles in a row", {"red_candle"}),
        ("only during New York session", {"new_york_session"}),
    ],
)
def test_clear_trading_phrases_do_not_trigger_noisy_clarification(
    prompt,
    expected_keys,
):
    report = CapabilityResolver().resolve_prompt(prompt)
    assert report.needs_clarification is False
    assert expected_keys.issubset(set(report.candidate_keys))


def test_unknown_words_are_not_hidden_by_a_known_condition():
    report = CapabilityResolver().resolve_prompt(
        "RSI below 30 with frobnicate alpha confirmation"
    )
    assert report.needs_clarification is True
    assert report.fragments[0].unknown_terms == ("frobnicate alpha",)


@pytest.mark.parametrize(
    "prompt",
    [
        "Tell me about RSI below 30 on 15m",
        "RSI below 30, then alert me after two candles",
        "I want RSI below 30 on 15m",
    ],
)
def test_ordinary_conversational_wording_does_not_create_unknown_terms(prompt):
    report = CapabilityResolver().resolve_prompt(prompt)
    assert report.needs_clarification is False
    assert report.candidate_keys[0] == "rsi_threshold"


def test_wholly_unknown_mechanic_is_asked_as_a_phrase_not_an_english_word():
    report = CapabilityResolver().resolve_prompt("Explain the moon wobble candidates")
    assert report.needs_clarification is True
    fragment = report.fragments[0]
    assert fragment.status == "unknown"
    assert fragment.unknown_terms == ()
    assert fragment.clarification_question == (
        "How should TraceEdge measure 'Explain the moon wobble candidates'?"
    )


def test_registry_rejects_unknown_keys_parameters_and_invalid_thresholds():
    resolver = CapabilityResolver()
    with pytest.raises(ValueError, match="Unknown capability_key"):
        resolver.validate_selection(
            capability_key="invented_alpha",
            parameters={},
            timeframe="15m",
            required=True,
            source_fragment="invented alpha",
        )
    with pytest.raises(ValueError, match="unknown parameters"):
        resolver.validate_selection(
            capability_key="rsi_threshold",
            parameters={"magic_period": 14},
            timeframe="15m",
            required=True,
            source_fragment="RSI below 30",
        )
    with pytest.raises(ValueError, match="does not accept a numeric threshold"):
        resolver.validate_selection(
            capability_key="previous_daily_low_sweep",
            parameters={"threshold": 10},
            timeframe="15m",
            required=True,
            source_fragment="PDL sweep",
        )


def test_parameterized_percent_change_uses_registry_controlled_direction():
    rule = CapabilityResolver().validate_selection(
        capability_key="percent_change_lookback",
        parameters={"direction": "down", "threshold_percent": 4, "lookback": 7},
        timeframe="1d",
        required=True,
        source_fragment="coins down 4% this week",
    )
    assert rule.capability_key == "percent_change_lookback"
    assert rule.left.name == "percent_change_down"
    assert rule.left.parameters == {
        "direction": "down",
        "threshold_percent": 4,
        "lookback": 7,
    }


def test_ai_operands_are_rebuilt_from_capability_key():
    resolver = CapabilityResolver()
    capability = capability_by_key()["price_above_ema"]
    payload = condition_template(capability, timeframe="1h")
    payload["left"] = {
        "kind": "indicator",
        "name": "ai_invented_operand",
        "parameters": {},
    }
    payload["right"] = {
        "kind": "indicator",
        "name": "ema",
        "parameters": {"period": 50},
    }
    ai_rule = ConditionRule.model_validate(payload)
    strategy = load_strategy().model_copy(
        update={
            "base_timeframe": "1h",
            "conditions": load_strategy().conditions.model_copy(
                update={"children": [ai_rule]}
            ),
        }
    )

    canonical = resolver.canonicalize_ai_strategy(strategy)
    rule = canonical.conditions.children[0]
    assert rule.capability_key == "price_above_ema"
    assert rule.left.kind.value == "price"
    assert rule.left.field == "close"
    assert rule.right.name == "ema"
    assert rule.right.parameters["period"] == 50
