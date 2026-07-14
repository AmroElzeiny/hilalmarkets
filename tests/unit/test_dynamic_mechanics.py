import json
from datetime import UTC, datetime, timedelta

import pytest

from ai_market_monitor.engine.dynamic_mechanics import (
    DynamicMechanicValidationError,
    compile_dynamic_rule,
    evaluate_expression,
    evaluate_serialized_expression,
    expression_hash,
    required_history_candles,
    validate_expression,
    validate_expression_parameters,
)
from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.schemas.capability_extensions import MechanicDraft, MechanicParameterSpec
from ai_market_monitor.services.interfaces import Candle


def _candles(count: int = 60) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(
            timestamp=start + timedelta(minutes=index),
            open=100,
            high=102 if index % 10 == 0 else 101,
            low=99,
            close=101.8 if index % 10 == 0 else 100.1,
            volume=1000 + index,
        )
        for index in range(count)
    ]


def test_dynamic_expression_is_bounded_and_deterministic():
    expression = {
        "op": "and",
        "args": [
            {
                "op": "gt",
                "left": {"op": "candle_metric", "name": "body_percent", "offset": 0},
                "right": {"op": "constant", "value": 50},
            },
            {
                "op": "gt",
                "left": {"op": "field", "field": "close", "offset": 0},
                "right": {"op": "indicator", "name": "ema", "period": 20},
            },
        ],
    }
    validation = validate_expression(expression)
    first = evaluate_expression(expression, _candles(61))
    second = evaluate_expression(expression, _candles(61))

    assert validation.node_count == 7
    assert validation.warmup_candles >= 22
    assert first is True
    assert second is first


@pytest.mark.parametrize(
    "expression",
    [
        {"op": "python", "code": "import os"},
        {
            "op": "gt",
            "left": {"op": "field", "field": "close", "eval": "__import__('os')"},
            "right": {"op": "constant", "value": 1},
        },
        {
            "op": "gt",
            "left": {"op": "field", "field": "future_close", "offset": 0},
            "right": {"op": "constant", "value": 1},
        },
    ],
)
def test_dynamic_expression_rejects_code_and_unapproved_data(expression):
    with pytest.raises(DynamicMechanicValidationError):
        validate_expression(expression)


def test_dynamic_rule_pins_version_parameters_and_artifact_hash():
    expression = {
        "op": "gt",
        "left": {"op": "candle_metric", "name": "body_percent", "offset": 0},
        "right": {"op": "parameter", "name": "minimum_body"},
    }
    manifest = {"label": "Strong body", "timeframe": "15m"}
    artifact_hash = expression_hash(expression, manifest)
    rule = compile_dynamic_rule(
        capability_key="custom_strong_body",
        capability_version="0.1.0",
        artifact_hash=artifact_hash,
        label="Strong body",
        timeframe="15m",
        expression=expression,
        resolved_parameters={"minimum_body": 60},
        proof_template="Candle body was {actual}; required {required}.",
        source_fragment="Find candles with a strong body",
    )

    assert rule.capability_key == "custom_strong_body"
    assert rule.capability_version == "0.1.0"
    assert rule.capability_artifact_hash == artifact_hash
    assert rule.resolved_parameters == {"minimum_body": 60}
    assert rule.left.name == "certified_dynamic"


def test_division_by_zero_is_a_runtime_failure_not_a_match():
    expression = {
        "op": "gt",
        "left": {
            "op": "divide",
            "left": {"op": "field", "field": "close"},
            "right": {"op": "constant", "value": 0},
        },
        "right": {"op": "constant", "value": 1},
    }
    validate_expression(expression)
    with pytest.raises(DynamicMechanicValidationError, match="Division by zero"):
        evaluate_expression(expression, _candles())


def test_resolved_parameters_must_be_declared_used_typed_and_bounded():
    expression = {
        "op": "gt",
        "left": {"op": "candle_metric", "name": "body_percent"},
        "right": {"op": "parameter", "name": "minimum_body"},
    }
    validate_expression_parameters(expression, {"minimum_body": 60})
    with pytest.raises(DynamicMechanicValidationError, match="Missing resolved"):
        validate_expression_parameters(expression, {})
    with pytest.raises(DynamicMechanicValidationError, match="unused"):
        validate_expression_parameters(expression, {"minimum_body": 60, "hidden": 1})
    with pytest.raises(ValueError, match="exceeds its maximum"):
        MechanicDraft(
            label="Bounded body",
            deterministic_definition="Current candle body must exceed a visible threshold.",
            timeframe="15m",
            parameters=[
                MechanicParameterSpec(
                    name="minimum_body",
                    type="number",
                    description="Minimum body percentage",
                    default=60,
                    minimum=0,
                    maximum=100,
                )
            ],
            resolved_parameters={"minimum_body": 101},
            expression=expression,
            proof_template="Body percentage is checked against the threshold.",
            logic_fidelity_statement="The threshold directly matches the user request.",
        )


def test_indicator_options_cannot_be_silently_ignored():
    invalid = {
        "op": "gt",
        "left": {"op": "indicator", "name": "ema", "period": 20, "deviations": 3},
        "right": {"op": "field", "field": "close"},
    }
    with pytest.raises(DynamicMechanicValidationError, match="only to Bollinger"):
        validate_expression(invalid)


def test_serialized_runtime_rejects_hidden_parameters():
    expression = {
        "op": "gt",
        "left": {"op": "field", "field": "close"},
        "right": {"op": "constant", "value": 1},
    }
    with pytest.raises(DynamicMechanicValidationError, match="unused"):
        evaluate_serialized_expression(
            json.dumps(expression),
            _candles(),
            '{"unapproved":1}',
        )


def test_previous_period_history_depth_adapts_to_timeframe():
    expression = {
        "op": "gt",
        "left": {"op": "field", "field": "high"},
        "right": {
            "op": "previous_period",
            "period": "week",
            "side": "high",
            "timezone": "UTC",
        },
    }
    assert required_history_candles(expression, "15m", minimum=500) == 1349
    assert required_history_candles(expression, "1h", minimum=500) == 500


def test_certified_rule_exposes_exact_mechanic_evidence_for_proof():
    expression = {
        "op": "gt",
        "left": {"op": "field", "field": "close"},
        "right": {"op": "constant", "value": 100},
    }
    artifact_hash = expression_hash(expression, {"label": "Close over 100"})
    rule = compile_dynamic_rule(
        capability_key="custom_close_over_100",
        capability_version="0.1.0",
        artifact_hash=artifact_hash,
        label="Close over 100",
        timeframe="15m",
        expression=expression,
        resolved_parameters={},
        proof_template="The deterministic close threshold is {state}.",
        source_fragment="close over 100",
    )

    assert StrategyRuleEngine._mechanic_evidence(rule) == {
        "capability_key": "custom_close_over_100",
        "capability_version": "0.1.0",
        "artifact_hash": artifact_hash,
        "expression": expression,
        "resolved_parameters": {},
    }


def test_proof_template_rejects_unknown_or_unsafe_placeholders():
    with pytest.raises(ValueError, match="unsupported proof placeholder"):
        MechanicDraft(
            label="Unsafe proof",
            deterministic_definition="A deterministic definition long enough for validation.",
            timeframe="15m",
            expression={
                "op": "gt",
                "left": {"op": "field", "field": "close"},
                "right": {"op": "constant", "value": 100},
            },
            proof_template="Unexpected value {secret} is shown.",
            logic_fidelity_statement="This preserves the exact requested comparison.",
        )
