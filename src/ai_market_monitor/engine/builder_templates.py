from __future__ import annotations

from copy import deepcopy
from typing import Any

from ai_market_monitor.engine.capabilities import CapabilitySpec

UNARY_COMPARATORS = {"is_true", "is_false"}
MOVING_AVERAGE_KEYS = {
    "weighted_moving_average",
    "hull_moving_average",
    "double_exponential_moving_average",
    "triple_exponential_moving_average",
    "kaufman_adaptive_moving_average",
    "volume_weighted_moving_average",
    "linear_regression_moving_average",
    "zero_lag_ema",
}
PAIR_COMPONENTS = {
    "stochastic_rsi": ("k", "d"),
    "true_strength_index": ("tsi", "signal"),
    "relative_vigor_index": ("rvi", "signal"),
    "directional_movement_components": ("plus_di", "minus_di"),
    "macd_line_cross_signal": ("line", "signal"),
    "stochastic_kd_cross": ("k", "d"),
}
PRICE_INDICATOR_TEMPLATES: dict[str, tuple[str, str, dict[str, Any]]] = {
    "price_above_ema": ("ema", "gt", {"period": 200}),
    "price_below_ema": ("ema", "lt", {"period": 200}),
    "price_above_sma": ("sma", "gt", {"period": 200}),
    "price_below_sma": ("sma", "lt", {"period": 200}),
    "ma_reclaim": ("ema", "crosses_above", {"period": 20}),
    "price_vs_vwap": ("vwap", "gt", {"period": 20}),
    "vwap_reclaim": ("vwap", "crosses_above", {"period": 20}),
    "bollinger_touch": (
        "bollinger_band",
        "gte",
        {"period": 20, "standard_deviations": 2, "component": "upper"},
    ),
    "bollinger_close_outside": (
        "bollinger_band",
        "gt",
        {"period": 20, "standard_deviations": 2, "component": "upper"},
    ),
    "pivot_points": ("pivot_points", "gt", {"lookback": 1, "component": "r1"}),
}


def _operand(kind: str, name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    if kind == "price":
        return {"kind": "price", "field": name or "close", "parameters": parameters}
    return {"kind": kind, "name": name, "parameters": parameters}


def condition_template(
    capability: CapabilitySpec,
    *,
    timeframe: str = "15m",
    key: str | None = None,
) -> dict[str, Any]:
    parameters = deepcopy(capability.default_parameters)
    if capability.condition_type == "candle_pattern":
        parameters = {
            "min_body_percent": 25,
            "max_body_percent": 40,
            "wick_ratio": 2,
            "trend_context_required": False,
            "confirmation_required": False,
            "pattern_strength": "medium",
            "direction": "neutral",
            **parameters,
        }
    comparator = capability.default_comparator
    left_kind = capability.operand_kind or {
        "market_filter": "market_metric",
        "risk": "risk_metric",
    }.get(capability.condition_type, capability.condition_type)
    if left_kind == "market_filter":
        left_kind = "market_metric"
    if left_kind == "risk":
        left_kind = "risk_metric"
    left_name = capability.operand_name or capability.key
    left = _operand(left_kind, left_name, parameters)
    right: dict[str, Any] | None = None

    if capability.key in PRICE_INDICATOR_TEMPLATES:
        indicator, comparator, right_parameters = PRICE_INDICATOR_TEMPLATES[capability.key]
        left = _operand("price", "close", {})
        right = _operand("indicator", indicator, right_parameters)
    elif capability.key in {"ema_crossover", "sma_crossover"}:
        indicator = "ema" if capability.key == "ema_crossover" else "sma"
        left = _operand("indicator", indicator, {"period": 20})
        right = _operand("indicator", indicator, {"period": 50})
        comparator = "crosses_above"
    elif capability.key in MOVING_AVERAGE_KEYS:
        left = _operand("price", "close", {})
        right = _operand("indicator", left_name, parameters)
        comparator = "gt"
    elif capability.key == "keltner_channels":
        left = _operand("price", "close", {})
        right = _operand(
            "indicator",
            "keltner_channel",
            {**parameters, "component": "upper"},
        )
        comparator = "gt"
    elif capability.key == "donchian_channels":
        left = _operand("price", "close", {})
        right = _operand(
            "indicator",
            "donchian_channel",
            {**parameters, "component": "upper"},
        )
        comparator = "gt"
    elif capability.key in PAIR_COMPONENTS:
        left_component, right_component = PAIR_COMPONENTS[capability.key]
        left = _operand(
            "indicator",
            left_name,
            {**parameters, "component": left_component},
        )
        right = _operand(
            "indicator",
            left_name,
            {**parameters, "component": right_component},
        )
        comparator = "crosses_above"
    elif comparator not in UNARY_COMPARATORS:
        right = {
            "kind": "constant",
            "value": (
                capability.default_threshold if capability.default_threshold is not None else 0
            ),
        }

    condition_type = capability.condition_type
    if condition_type == "market_filter":
        condition_type = "market_filter"
    return {
        "node_type": "condition",
        "key": key or capability.key,
        "label": capability.label,
        "condition_type": condition_type,
        "timeframe": timeframe,
        "left": left,
        "comparator": comparator,
        "right": right,
        "required": not capability.optional_only,
        "weight": 1,
        "cap_score_on_fail": None,
        "required_data": list(capability.required_data),
        "explanation_template": (
            f"{capability.label}: actual {{actual}}; required {{required}}; {{state}}."
        ),
        "forming_tolerance_percent": 10,
        "notes": capability.risk_notes or None,
        "source_fragment": capability.examples[0] if capability.examples else capability.label,
        "confidence": 0.9 if capability.availability == "available" else 0.55,
        "ai_interpreted": False,
        "provider_required": bool(capability.provider_required),
        "availability": (
            "provider_required" if capability.provider_required else capability.availability
        ),
    }


def builder_template_payload(capability: CapabilitySpec) -> dict[str, Any]:
    return condition_template(capability)
