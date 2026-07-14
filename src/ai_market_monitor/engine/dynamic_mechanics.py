from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from ai_market_monitor.db.models.enums import ConditionType
from ai_market_monitor.engine.indicators import (
    atr,
    average_volume,
    bollinger_band,
    ema,
    rsi,
    sma,
    vwap,
)
from ai_market_monitor.schemas.strategy import Comparator, ConditionRule, Operand, OperandKind
from ai_market_monitor.services.interfaces import Candle

BOOLEAN_OPS = {
    "and",
    "or",
    "not",
    "gt",
    "gte",
    "lt",
    "lte",
    "eq",
    "crosses_above",
    "crosses_below",
}
VALUE_OPS = {
    "constant",
    "parameter",
    "field",
    "indicator",
    "aggregate",
    "previous_period",
    "candle_metric",
    "add",
    "subtract",
    "multiply",
    "divide",
    "abs",
    "min",
    "max",
}
ALLOWED_OPS = BOOLEAN_OPS | VALUE_OPS
FIELDS = {"open", "high", "low", "close", "volume", "quote_volume"}
INDICATORS = {"sma", "ema", "rsi", "atr", "vwap", "average_volume", "bollinger"}
AGGREGATES = {"highest", "lowest", "mean", "sum"}
CANDLE_METRICS = {
    "body_percent",
    "range_percent",
    "upper_wick_percent",
    "lower_wick_percent",
    "bullish",
    "bearish",
    "doji",
}


class DynamicMechanicValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExpressionValidation:
    node_count: int
    max_depth: int
    warmup_candles: int
    output_type: str


def validate_expression(
    expression: dict[str, Any],
    *,
    max_nodes: int = 80,
    max_depth: int = 12,
) -> ExpressionValidation:
    state = {"nodes": 0, "depth": 0, "warmup": 1}

    def walk(node: Any, depth: int) -> str:
        if not isinstance(node, dict):
            raise DynamicMechanicValidationError("Every expression node must be an object")
        state["nodes"] += 1
        state["depth"] = max(state["depth"], depth)
        if state["nodes"] > max_nodes:
            raise DynamicMechanicValidationError(f"Expression exceeds {max_nodes} nodes")
        if depth > max_depth:
            raise DynamicMechanicValidationError(f"Expression exceeds depth {max_depth}")
        op = str(node.get("op") or "")
        if op not in ALLOWED_OPS:
            raise DynamicMechanicValidationError(f"Unsupported expression operation: {op}")
        _reject_unknown_keys(node, op)
        if op == "constant":
            value = node.get("value")
            if not isinstance(value, (int, float, bool)) or not math.isfinite(float(value)):
                raise DynamicMechanicValidationError("constant requires a finite number or boolean")
            return "boolean" if isinstance(value, bool) else "number"
        if op == "parameter":
            name = str(node.get("name") or "")
            if not name or not name.replace("_", "").isalnum():
                raise DynamicMechanicValidationError("parameter requires a safe name")
            return "number"
        if op == "field":
            _validate_field(node)
            offset = _bounded_int(node.get("offset", 0), "offset", 0, 2000)
            state["warmup"] = max(state["warmup"], offset + 1)
            return "number"
        if op == "indicator":
            name = str(node.get("name") or "")
            if name not in INDICATORS:
                raise DynamicMechanicValidationError(f"Unsupported indicator: {name}")
            period = _bounded_int(node.get("period", 14), "period", 2, 2000)
            state["warmup"] = max(state["warmup"], period + 2)
            if node.get("field") is not None:
                _validate_field(node)
            if name == "bollinger":
                if node.get("component", "middle") not in {
                    "lower",
                    "middle",
                    "upper",
                    "width",
                }:
                    raise DynamicMechanicValidationError("Invalid Bollinger component")
                deviations = node.get("deviations", 2)
                if (
                    isinstance(deviations, bool)
                    or not isinstance(deviations, int | float)
                    or not math.isfinite(float(deviations))
                    or not 0.1 <= float(deviations) <= 10
                ):
                    raise DynamicMechanicValidationError(
                        "Bollinger deviations must be between 0.1 and 10"
                    )
            elif "component" in node or "deviations" in node:
                raise DynamicMechanicValidationError(
                    "component and deviations apply only to Bollinger bands"
                )
            return "number"
        if op == "aggregate":
            if node.get("name") not in AGGREGATES:
                raise DynamicMechanicValidationError("Invalid aggregate")
            _validate_field(node)
            lookback = _bounded_int(node.get("lookback"), "lookback", 1, 2000)
            offset = int(bool(node.get("exclude_current", False)))
            state["warmup"] = max(state["warmup"], lookback + offset)
            return "number"
        if op == "previous_period":
            if node.get("period") not in {"day", "week", "month"}:
                raise DynamicMechanicValidationError("previous_period requires day, week, or month")
            if node.get("side") not in {"high", "low", "open", "close"}:
                raise DynamicMechanicValidationError(
                    "previous_period side must be high, low, open, or close"
                )
            try:
                ZoneInfo(str(node.get("timezone", "UTC")))
            except Exception as exc:
                raise DynamicMechanicValidationError("Invalid previous-period timezone") from exc
            state["warmup"] = max(state["warmup"], 3)
            return "number"
        if op == "candle_metric":
            if node.get("name") not in CANDLE_METRICS:
                raise DynamicMechanicValidationError("Invalid candle metric")
            offset = _bounded_int(node.get("offset", 0), "offset", 0, 2000)
            state["warmup"] = max(state["warmup"], offset + 1)
            if "threshold_percent" in node:
                threshold = node["threshold_percent"]
                if node.get("name") != "doji":
                    raise DynamicMechanicValidationError(
                        "threshold_percent applies only to the doji candle metric"
                    )
                if (
                    isinstance(threshold, bool)
                    or not isinstance(threshold, int | float)
                    or not math.isfinite(float(threshold))
                    or not 0 <= float(threshold) <= 100
                ):
                    raise DynamicMechanicValidationError(
                        "Doji threshold_percent must be between 0 and 100"
                    )
            return "boolean" if node.get("name") in {"bullish", "bearish", "doji"} else "number"
        if op in {"and", "or"}:
            args = node.get("args")
            if not isinstance(args, list) or len(args) < 2 or len(args) > 20:
                raise DynamicMechanicValidationError(f"{op} requires 2 to 20 arguments")
            if any(walk(child, depth + 1) != "boolean" for child in args):
                raise DynamicMechanicValidationError(f"{op} accepts only boolean arguments")
            return "boolean"
        if op == "not":
            if walk(node.get("arg"), depth + 1) != "boolean":
                raise DynamicMechanicValidationError("not requires a boolean argument")
            return "boolean"
        if op in {"gt", "gte", "lt", "lte", "eq", "crosses_above", "crosses_below"}:
            left_type = walk(node.get("left"), depth + 1)
            right_type = walk(node.get("right"), depth + 1)
            if left_type != right_type and "boolean" in {left_type, right_type}:
                raise DynamicMechanicValidationError(f"{op} operands must have compatible types")
            if op != "eq" and "boolean" in {left_type, right_type}:
                raise DynamicMechanicValidationError(f"{op} requires numeric operands")
            if op.startswith("crosses_"):
                state["warmup"] += 1
            return "boolean"
        if op in {"add", "subtract", "multiply", "divide", "min", "max"}:
            if walk(node.get("left"), depth + 1) != "number" or walk(
                node.get("right"), depth + 1
            ) != "number":
                raise DynamicMechanicValidationError(f"{op} requires numeric operands")
            return "number"
        if op == "abs":
            if walk(node.get("arg"), depth + 1) != "number":
                raise DynamicMechanicValidationError("abs requires a numeric argument")
            return "number"
        raise DynamicMechanicValidationError(f"Unsupported operation: {op}")

    output_type = walk(expression, 1)
    if output_type != "boolean":
        raise DynamicMechanicValidationError("The top-level mechanic expression must be boolean")
    return ExpressionValidation(
        node_count=state["nodes"],
        max_depth=state["depth"],
        warmup_candles=state["warmup"],
        output_type=output_type,
    )


def evaluate_expression(
    expression: dict[str, Any],
    candles: list[Candle],
    parameters: dict[str, Any] | None = None,
) -> bool:
    if not candles:
        raise DynamicMechanicValidationError("Dynamic mechanic requires candle history")
    result = _evaluate(expression, candles, parameters or {})
    if not isinstance(result, bool):
        raise DynamicMechanicValidationError("Dynamic mechanic did not return a boolean")
    return result


def validate_expression_parameters(
    expression: dict[str, Any],
    parameters: dict[str, int | float | bool],
) -> None:
    referenced: set[str] = set()

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("op") == "parameter":
            referenced.add(str(node.get("name") or ""))
        for value in node.values():
            if isinstance(value, dict):
                walk(value)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

    walk(expression)
    missing = sorted(referenced - set(parameters))
    unused = sorted(set(parameters) - referenced)
    if missing:
        raise DynamicMechanicValidationError(
            f"Missing resolved mechanic parameters: {', '.join(missing)}"
        )
    if unused:
        raise DynamicMechanicValidationError(
            f"Resolved mechanic parameters are unused: {', '.join(unused)}"
        )
    for name, value in parameters.items():
        if not isinstance(value, int | float | bool):
            raise DynamicMechanicValidationError(f"Mechanic parameter {name} has an invalid type")
        if not isinstance(value, bool) and not math.isfinite(float(value)):
            raise DynamicMechanicValidationError(f"Mechanic parameter {name} must be finite")


def required_history_candles(
    expression: dict[str, Any],
    timeframe: str,
    *,
    minimum: int,
) -> int:
    minutes = {
        "1m": 1,
        "3m": 3,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "2h": 120,
        "4h": 240,
        "6h": 360,
        "8h": 480,
        "12h": 720,
        "1d": 1440,
    }.get(timeframe)
    if minutes is None:
        raise DynamicMechanicValidationError(f"Unsupported mechanic timeframe: {timeframe}")
    required_minutes = 0

    def walk(node: Any) -> None:
        nonlocal required_minutes
        if not isinstance(node, dict):
            return
        if node.get("op") == "previous_period":
            required_minutes = max(
                required_minutes,
                {"day": 2 * 1440, "week": 14 * 1440, "month": 62 * 1440}[
                    str(node.get("period"))
                ],
            )
        for value in node.values():
            if isinstance(value, dict):
                walk(value)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

    walk(expression)
    period_requirement = math.ceil(required_minutes / minutes) + 5 if required_minutes else 0
    return max(minimum, period_requirement)


def evaluate_serialized_expression(
    expression_json: str,
    candles: list[Candle],
    parameters_json: str = "{}",
) -> bool:
    expression = _validated_serialized_expression(expression_json)
    parameters = json.loads(parameters_json or "{}")
    if not isinstance(parameters, dict):
        raise DynamicMechanicValidationError("Mechanic parameters must be an object")
    validate_expression_parameters(expression, parameters)
    return evaluate_expression(expression, candles, parameters)


@lru_cache(maxsize=512)
def _validated_serialized_expression(expression_json: str) -> dict[str, Any]:
    expression = json.loads(expression_json)
    if not isinstance(expression, dict):
        raise DynamicMechanicValidationError("Mechanic expression must be an object")
    validate_expression(expression)
    return expression


def expression_hash(expression: dict[str, Any], manifest: dict[str, Any]) -> str:
    payload = json.dumps(
        {"expression": expression, "manifest": manifest},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compile_dynamic_rule(
    *,
    capability_key: str,
    capability_version: str,
    artifact_hash: str,
    label: str,
    timeframe: str,
    expression: dict[str, Any],
    resolved_parameters: dict[str, Any],
    proof_template: str,
    source_fragment: str,
) -> ConditionRule:
    validate_expression(expression)
    validate_expression_parameters(expression, resolved_parameters)
    return ConditionRule(
        capability_key=capability_key,
        capability_version=capability_version,
        capability_artifact_hash=artifact_hash,
        resolved_parameters=resolved_parameters,
        key=capability_key,
        label=label,
        condition_type=ConditionType.PRICE_ACTION,
        timeframe=timeframe,
        left=Operand(
            kind=OperandKind.PRICE_ACTION,
            name="certified_dynamic",
            parameters={
                "expression_json": json.dumps(expression, sort_keys=True, separators=(",", ":")),
                "parameters_json": json.dumps(
                    resolved_parameters, sort_keys=True, separators=(",", ":")
                ),
                "artifact_hash": artifact_hash,
                "capability_version": capability_version,
            },
        ),
        comparator=Comparator.IS_TRUE,
        right=None,
        required=True,
        required_data=["ohlcv"],
        explanation_template=proof_template,
        source_fragment=source_fragment[:500],
        confidence=0.9,
        ai_interpreted=True,
    )


def _evaluate(node: dict[str, Any], candles: list[Candle], parameters: dict[str, Any]) -> Any:
    op = node["op"]
    if op == "constant":
        return node["value"]
    if op == "parameter":
        name = str(node["name"])
        if name not in parameters:
            raise DynamicMechanicValidationError(f"Missing mechanic parameter: {name}")
        return parameters[name]
    if op == "field":
        candle = _candle(candles, int(node.get("offset", 0)))
        value = getattr(candle, str(node["field"]))
        if value is None:
            raise DynamicMechanicValidationError(f"Candle field {node['field']} is unavailable")
        return float(value)
    if op == "indicator":
        name = str(node["name"])
        period = int(node.get("period", 14))
        field = str(node.get("field", "close"))
        if name == "sma":
            return sma(candles, period=period, field=field)
        if name == "ema":
            return ema(candles, period=period, field=field)
        if name == "rsi":
            return rsi(candles, period=period, field=field)
        if name == "atr":
            return atr(candles, period=period)
        if name == "vwap":
            return vwap(candles, period=period)
        if name == "average_volume":
            return average_volume(candles, period=period)
        component = str(node.get("component", "middle"))
        deviations = float(node.get("deviations", 2))
        return bollinger_band(
            candles,
            period=period,
            standard_deviations=deviations,
            field=field,
            component=component,
        )
    if op == "aggregate":
        end = -1 if node.get("exclude_current", False) else None
        lookback = int(node["lookback"])
        selected = candles[:end][-lookback:]
        if len(selected) < lookback:
            raise DynamicMechanicValidationError(f"aggregate requires {lookback} candles")
        values = [float(getattr(candle, str(node["field"]))) for candle in selected]
        if node["name"] == "highest":
            return max(values)
        if node["name"] == "lowest":
            return min(values)
        if node["name"] == "sum":
            return sum(values)
        return sum(values) / len(values)
    if op == "previous_period":
        return _previous_period_value(candles, node)
    if op == "candle_metric":
        candle = _candle(candles, int(node.get("offset", 0)))
        candle_range = max(0.0, float(candle.high) - float(candle.low))
        body = abs(float(candle.close) - float(candle.open))
        name = node["name"]
        if name == "bullish":
            return candle.close > candle.open
        if name == "bearish":
            return candle.close < candle.open
        if name == "doji":
            threshold = float(node.get("threshold_percent", 10))
            return candle_range > 0 and body / candle_range * 100 <= threshold
        if name == "body_percent":
            return body / candle_range * 100 if candle_range else 0.0
        if name == "range_percent":
            return candle_range / candle.open * 100 if candle.open else 0.0
        upper = candle.high - max(candle.open, candle.close)
        lower = min(candle.open, candle.close) - candle.low
        if name == "upper_wick_percent":
            return upper / candle_range * 100 if candle_range else 0.0
        return lower / candle_range * 100 if candle_range else 0.0
    if op in {"and", "or"}:
        values = [bool(_evaluate(child, candles, parameters)) for child in node["args"]]
        return all(values) if op == "and" else any(values)
    if op == "not":
        return not bool(_evaluate(node["arg"], candles, parameters))
    if op in {"crosses_above", "crosses_below"}:
        if len(candles) < 2:
            raise DynamicMechanicValidationError(f"{op} requires two candles")
        current_left = float(_evaluate(node["left"], candles, parameters))
        current_right = float(_evaluate(node["right"], candles, parameters))
        previous_left = float(_evaluate(node["left"], candles[:-1], parameters))
        previous_right = float(_evaluate(node["right"], candles[:-1], parameters))
        if op == "crosses_above":
            return previous_left <= previous_right and current_left > current_right
        return previous_left >= previous_right and current_left < current_right
    if op in {"gt", "gte", "lt", "lte", "eq"}:
        left = _evaluate(node["left"], candles, parameters)
        right = _evaluate(node["right"], candles, parameters)
        return {
            "gt": left > right,
            "gte": left >= right,
            "lt": left < right,
            "lte": left <= right,
            "eq": left == right,
        }[op]
    if op == "abs":
        return abs(float(_evaluate(node["arg"], candles, parameters)))
    left = float(_evaluate(node["left"], candles, parameters))
    right = float(_evaluate(node["right"], candles, parameters))
    if op == "add":
        return left + right
    if op == "subtract":
        return left - right
    if op == "multiply":
        return left * right
    if op == "divide":
        if right == 0:
            raise DynamicMechanicValidationError("Division by zero")
        return left / right
    if op == "min":
        return min(left, right)
    if op == "max":
        return max(left, right)
    raise DynamicMechanicValidationError(f"Unsupported operation: {op}")


def _candle(candles: list[Candle], offset: int) -> Candle:
    if len(candles) <= offset:
        raise DynamicMechanicValidationError(f"Candle offset {offset} is unavailable")
    return candles[-offset - 1]


def _previous_period_value(candles: list[Candle], node: dict[str, Any]) -> float:
    timezone = ZoneInfo(str(node.get("timezone", "UTC")))
    period = str(node["period"])
    current = candles[-1].timestamp.astimezone(timezone)
    if period == "day":
        start = datetime.combine(current.date(), datetime.min.time(), tzinfo=timezone)
        previous_start = start - timedelta(days=1)
    elif period == "week":
        start = datetime.combine(
            current.date() - timedelta(days=current.weekday()),
            datetime.min.time(),
            tzinfo=timezone,
        )
        previous_start = start - timedelta(days=7)
    else:
        start = datetime(current.year, current.month, 1, tzinfo=timezone)
        if current.month == 1:
            previous_start = datetime(current.year - 1, 12, 1, tzinfo=timezone)
        else:
            previous_start = datetime(current.year, current.month - 1, 1, tzinfo=timezone)
    previous = [
        candle
        for candle in candles
        if previous_start.astimezone(UTC)
        <= candle.timestamp.astimezone(UTC)
        < start.astimezone(UTC)
    ]
    if not previous:
        raise DynamicMechanicValidationError("Previous period candle data is unavailable")
    side = str(node["side"])
    if side == "high":
        return max(float(candle.high) for candle in previous)
    if side == "low":
        return min(float(candle.low) for candle in previous)
    if side == "open":
        return float(previous[0].open)
    return float(previous[-1].close)


def _validate_field(node: dict[str, Any]) -> None:
    if node.get("field", "close") not in FIELDS:
        raise DynamicMechanicValidationError(f"Unsupported candle field: {node.get('field')}")


def _bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise DynamicMechanicValidationError(f"{name} must be {minimum} to {maximum}")
    return value


def _reject_unknown_keys(node: dict[str, Any], op: str) -> None:
    common = {"op"}
    binary = common | {"left", "right"}
    allowed = {
        "constant": common | {"value"},
        "parameter": common | {"name"},
        "field": common | {"field", "offset"},
        "indicator": common | {"name", "period", "field", "component", "deviations"},
        "aggregate": common | {"name", "field", "lookback", "exclude_current"},
        "previous_period": common | {"period", "side", "timezone"},
        "candle_metric": common | {"name", "offset", "threshold_percent"},
        "and": common | {"args"},
        "or": common | {"args"},
        "not": common | {"arg"},
        "abs": common | {"arg"},
        "gt": binary,
        "gte": binary,
        "lt": binary,
        "lte": binary,
        "eq": binary,
        "crosses_above": binary,
        "crosses_below": binary,
        "add": binary,
        "subtract": binary,
        "multiply": binary,
        "divide": binary,
        "min": binary,
        "max": binary,
    }[op]
    unknown = sorted(set(node) - allowed)
    if unknown:
        raise DynamicMechanicValidationError(
            f"Operation {op} received unknown fields: {', '.join(unknown)}"
        )
