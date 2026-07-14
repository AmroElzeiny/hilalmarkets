import hashlib
import json
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_market_monitor.db.models.enums import (
    ConditionType,
    LogicalOperator,
    MarketType,
    TriggerMode,
)

Timeframe = Annotated[str, Field(pattern=r"^(1|3|5|15|30)m$|^(1|2|4|6|8|12)h$|^1d$")]


class Comparator(StrEnum):
    GREATER_THAN = "gt"
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN = "lt"
    LESS_THAN_OR_EQUAL = "lte"
    EQUAL = "eq"
    CROSSES_ABOVE = "crosses_above"
    CROSSES_BELOW = "crosses_below"
    IS_TRUE = "is_true"
    IS_FALSE = "is_false"


class StrategyDirection(StrEnum):
    LONG = "long"
    SHORT = "short"
    BOTH = "both"


class OperandKind(StrEnum):
    INDICATOR = "indicator"
    PRICE = "price"
    CONSTANT = "constant"
    MARKET_METRIC = "market_metric"
    PRICE_ACTION = "price_action"
    CANDLE_PATTERN = "candle_pattern"
    RISK_METRIC = "risk_metric"


class Operand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: OperandKind
    name: str | None = None
    parameters: dict[
        str,
        int | float | str | bool | list[int | float | str | bool],
    ] = Field(default_factory=dict)
    field: str | None = None
    value: float | str | bool | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "Operand":
        if self.kind == OperandKind.CONSTANT and self.value is None:
            raise ValueError("constant operands require value")
        if self.kind != OperandKind.CONSTANT and not (self.name or self.field):
            raise ValueError(f"{self.kind.value} operands require name or field")
        return self


class ConditionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_type: Literal["condition"] = "condition"
    capability_key: str | None = Field(
        default=None,
        frozen=True,
        min_length=1,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    capability_version: str | None = Field(
        default=None,
        frozen=True,
        min_length=1,
        max_length=32,
        pattern=r"^[0-9]+(?:\.[0-9]+){0,2}(?:[-+][a-zA-Z0-9.-]+)?$",
    )
    capability_artifact_hash: str | None = Field(
        default=None,
        frozen=True,
        min_length=64,
        max_length=64,
        pattern=r"^[a-f0-9]{64}$",
    )
    resolved_parameters: dict[
        str,
        int | float | str | bool | list[int | float | str | bool],
    ] = Field(default_factory=dict)
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=240)
    condition_type: ConditionType
    timeframe: Timeframe
    left: Operand
    comparator: Comparator
    right: Operand | None = None
    required: bool = True
    weight: float = Field(default=1.0, gt=0, le=100)
    cap_score_on_fail: float | None = Field(default=None, ge=0, le=100)
    required_data: list[str] = Field(default_factory=list, max_length=20)
    explanation_template: str | None = Field(default=None, max_length=500)
    forming_tolerance_percent: float | None = Field(default=None, ge=0, le=100)
    notes: str | None = Field(default=None, max_length=500)
    source_fragment: str | None = Field(default=None, max_length=500)
    confidence: float | None = Field(default=None, ge=0, le=1)
    ai_interpreted: bool = False
    provider_required: bool = False
    availability: str = Field(default="available", max_length=40)
    approximation_note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_comparator(self) -> "ConditionRule":
        unary = {Comparator.IS_TRUE, Comparator.IS_FALSE}
        if self.comparator in unary and self.right is not None:
            raise ValueError("boolean comparators do not accept a right operand")
        if self.comparator not in unary and self.right is None:
            raise ValueError("comparison requires a right operand")
        return self


class ConditionGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_type: Literal["group"] = "group"
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    operator: LogicalOperator
    children: list["ConditionRule | ConditionGroup"] = Field(min_length=1)
    parameters: dict[str, int | float | str | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_operator_shape(self) -> "ConditionGroup":
        child_count = len(self.children)
        single_child = {
            LogicalOperator.NOT,
            LogicalOperator.WITHIN_LAST,
            LogicalOperator.PERSISTED_FOR,
            LogicalOperator.FIRST_TIME_TRUE,
            LogicalOperator.CHANGED_STATE,
            LogicalOperator.CROSS_WITH_CONFIRMATION,
        }
        if self.operator in single_child and child_count != 1:
            raise ValueError(f"{self.operator.value} requires exactly one child")
        if self.operator == LogicalOperator.SEQUENCE and child_count < 2:
            raise ValueError("sequence requires at least two children")
        if self.operator == LogicalOperator.CONDITIONAL_BRANCH and child_count != 3:
            raise ValueError("conditional_branch requires condition, then, and otherwise children")
        positive_parameters = {
            "max_candles_between",
            "lookback_candles",
            "candles_count",
            "minimum_pass_count",
            "confirmation_bars",
        }
        for key in positive_parameters:
            if key in self.parameters and int(self.parameters[key]) < 1:
                raise ValueError(f"{key} must be at least 1")
        if "cooldown_minutes" in self.parameters and int(self.parameters["cooldown_minutes"]) < 0:
            raise ValueError("cooldown_minutes cannot be negative")
        if self.operator == LogicalOperator.COUNT_OF:
            minimum = int(self.parameters.get("minimum_pass_count", 1))
            if minimum > child_count:
                raise ValueError("minimum_pass_count cannot exceed child count")
        if self.operator == LogicalOperator.COOLDOWN_CONDITION:
            scope = str(self.parameters.get("scope", "per_symbol"))
            if scope not in {"per_symbol", "per_strategy"}:
                raise ValueError("cooldown scope must be per_symbol or per_strategy")
        return self


ConditionGroup.model_rebuild()


class UniverseDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exchange: str = Field(min_length=2, max_length=40)
    market_type: MarketType = MarketType.SPOT
    quote_currencies: list[str] = Field(min_length=1, max_length=10)
    include_symbols: list[str] = Field(default_factory=list, max_length=100000)
    exclude_symbols: list[str] = Field(default_factory=list, max_length=100000)
    min_quote_volume_24h: float | None = Field(default=None, ge=0)
    min_listing_age_days: int | None = Field(default=None, ge=0)
    max_spread_bps: float | None = Field(default=None, ge=0, le=1000)
    min_order_book_depth: float | None = Field(default=None, ge=0)
    min_average_candle_volume: float | None = Field(default=None, ge=0)
    min_historical_candles: int = Field(default=50, ge=1, le=5000)
    exclude_stablecoins: bool = True
    exclude_leveraged_tokens: bool = True
    min_market_cap: float | None = Field(default=None, ge=0)
    max_symbols: int | None = Field(default=None, ge=1, le=100000)


class EntryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calculation: Literal["signal_close", "fixed_percent_zone", "indicator_zone"] = "signal_close"
    zone_percent: float | None = Field(default=None, ge=0, le=20)
    expires_after_candles: int = Field(default=3, ge=1, le=100)
    invalidate_if_price_moves_percent: float | None = Field(default=None, gt=0, le=100)


class StopPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal[
        "structure",
        "fixed_percent",
        "atr",
        "swing_low",
        "swing_high",
        "technical_invalidation",
    ] = "structure"
    value: float | None = Field(default=None, gt=0)
    atr_period: int = Field(default=14, ge=2, le=200)
    atr_multiplier: float = Field(default=1.5, gt=0, le=20)
    swing_lookback: int = Field(default=10, ge=2, le=200)


class TargetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="T1", min_length=1, max_length=40)
    method: Literal["risk_multiple", "fixed_percent", "structure"] = "risk_multiple"
    value: float = Field(gt=0)
    size_percent: float | None = Field(default=None, gt=0, le=100)


class RiskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    stop_method: Literal[
        "structure",
        "fixed_percent",
        "atr",
        "swing_low",
        "swing_high",
        "technical_invalidation",
    ] = "structure"
    stop_value: float | None = Field(default=None, gt=0)
    maximum_stop_percent: float | None = Field(default=None, gt=0, le=100)
    target_method: Literal["risk_multiple", "structure", "fixed_percent"] = "risk_multiple"
    target_value: float | None = Field(default=None, gt=0)
    minimum_reward_to_risk: float | None = Field(default=None, gt=0, le=50)
    account_risk_percent: float | None = Field(default=None, gt=0, le=100)
    estimated_fee_bps: float = Field(default=0, ge=0, le=1000)
    estimated_slippage_bps: float = Field(default=0, ge=0, le=1000)

    @model_validator(mode="after")
    def validate_values(self) -> "RiskPolicy":
        if not self.enabled:
            return self
        if self.maximum_stop_percent is None:
            raise ValueError("enabled risk policy requires maximum_stop_percent")
        if self.minimum_reward_to_risk is None:
            raise ValueError("enabled risk policy requires minimum_reward_to_risk")
        if self.stop_method == "fixed_percent" and self.stop_value is None:
            raise ValueError("selected stop method requires stop_value")
        if self.target_method in {"risk_multiple", "fixed_percent"} and self.target_value is None:
            raise ValueError("selected target method requires target_value")
        return self


class AlertPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forming_alerts: bool = True
    near_miss_threshold: float = Field(default=70, ge=1, le=100)
    channels: list[Literal["telegram", "discord", "web"]] = Field(min_length=1)
    cooldown_seconds: int = Field(default=900, ge=0, le=86400)
    maximum_alerts_per_hour: int = Field(default=50, ge=1, le=1000)
    daily_alert_budget: int | None = Field(default=None, ge=1, le=10000)
    suppress_repetitive_near_miss: bool = True
    alert_on_one_condition_remaining: bool = True


class NearMissPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    thresholds: list[int] = Field(default_factory=lambda: [70, 80, 90], max_length=10)
    mandatory_fail_cap: float = Field(default=90, ge=0, le=100)
    minimum_score_to_store: float = Field(default=40, ge=0, le=100)
    one_condition_remaining_enabled: bool = True


class ExpiryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expire_after_candles: int = Field(default=3, ge=1, le=500)
    expire_if_price_moves_beyond_entry_percent: float | None = Field(default=None, gt=0, le=100)


class ForwardTestPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    estimated_fee_bps: float = Field(default=0, ge=0, le=1000)
    estimated_slippage_bps: float = Field(default=0, ge=0, le=1000)


class PositionSizingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    account_risk_percent: float | None = Field(default=None, gt=0, le=100)
    store_account_balance: bool = False


class StrategyDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    direction: StrategyDirection = StrategyDirection.LONG
    base_timeframe: Timeframe
    supporting_timeframes: list[Timeframe] = Field(default_factory=list, max_length=10)
    trigger_mode: TriggerMode
    universe: UniverseDefinition
    conditions: ConditionGroup
    entry: EntryPolicy = Field(default_factory=EntryPolicy)
    stop: StopPolicy = Field(default_factory=StopPolicy)
    targets: list[TargetPolicy] = Field(default_factory=list)
    risk: RiskPolicy = Field(default_factory=lambda: RiskPolicy(enabled=False))
    near_miss: NearMissPolicy = Field(default_factory=NearMissPolicy)
    alerts: AlertPolicy
    expiry: ExpiryPolicy = Field(default_factory=ExpiryPolicy)
    forward_test: ForwardTestPolicy = Field(default_factory=ForwardTestPolicy)
    position_sizing: PositionSizingPolicy = Field(default_factory=PositionSizingPolicy)

    @model_validator(mode="before")
    @classmethod
    def normalize_external_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "strategy_name" not in value:
            return value
        logic = value.get("logic") or {"operator": "AND", "conditions": []}
        conditions = cls._normalize_logic(logic, value.get("primary_timeframe", "15m"))
        stop = value.get("stop") or {}
        risk_rules = value.get("risk_rules") or {}
        targets = value.get("targets") or []
        alert_rules = value.get("alert_rules") or {}
        near_miss_rules = value.get("near_miss_rules") or {}
        expiry_rules = value.get("expiry_rules") or {}
        liquidity_rules = value.get("liquidity_rules") or {}
        entry = value.get("entry") or {}
        exchanges = value.get("exchanges") or ["binance"]
        quote_assets = value.get("quote_assets") or ["USDT"]
        risk_enabled = bool(risk_rules.get("enabled", bool(risk_rules or stop or targets)))
        normalized_targets = [
            {
                "label": target.get("label", f"T{index + 1}"),
                "method": target.get("method", "risk_multiple"),
                "value": target.get("value", target.get("multiple", 2.0)),
                "size_percent": target.get("size_percent"),
            }
            for index, target in enumerate(targets)
        ] or ([{"label": "T1", "method": "risk_multiple", "value": 2.0}] if risk_enabled else [])
        stop_method = stop.get("method") or risk_rules.get("stop_method") or "structure"
        target_value = (
            normalized_targets[0]["value"]
            if normalized_targets
            else risk_rules.get("target_value", 2)
        )
        return {
            "schema_version": value.get("schema_version", "1.0"),
            "name": value["strategy_name"],
            "description": value.get("description"),
            "direction": value.get("direction", "long"),
            "base_timeframe": value.get("primary_timeframe", "15m"),
            "supporting_timeframes": value.get("higher_timeframes", []),
            "trigger_mode": str(value.get("trigger_mode", "candle_close")).lower(),
            "universe": {
                "exchange": exchanges[0],
                "market_type": value.get("market_type", "spot"),
                "quote_currencies": quote_assets,
                "include_symbols": value.get("symbols", []),
                "exclude_symbols": value.get("excluded_symbols", []),
                "min_quote_volume_24h": liquidity_rules.get("min_quote_volume_24h"),
                "min_average_candle_volume": liquidity_rules.get("min_average_candle_volume"),
                "max_spread_bps": liquidity_rules.get("max_spread_bps"),
                "min_listing_age_days": liquidity_rules.get("min_listing_age_days"),
                "min_historical_candles": liquidity_rules.get("min_historical_candles", 50),
                "exclude_stablecoins": liquidity_rules.get("exclude_stablecoins", True),
                "exclude_leveraged_tokens": liquidity_rules.get("exclude_leveraged_tokens", True),
                "min_market_cap": liquidity_rules.get("min_market_cap"),
            },
            "conditions": conditions,
            "entry": {
                "calculation": entry.get("calculation", "signal_close"),
                "zone_percent": entry.get("zone_percent"),
                "expires_after_candles": entry.get("expires_after_candles", 3),
                "invalidate_if_price_moves_percent": entry.get("invalidate_if_price_moves_percent"),
            },
            "stop": {
                "method": stop_method,
                "value": stop.get("value"),
                "atr_period": stop.get("atr_period", 14),
                "atr_multiplier": stop.get("atr_multiplier", 1.5),
                "swing_lookback": stop.get("swing_lookback", 10),
            },
            "targets": normalized_targets,
            "risk": {
                "enabled": risk_enabled,
                "stop_method": stop_method,
                "stop_value": stop.get("value"),
                "maximum_stop_percent": (
                    risk_rules.get("maximum_stop_percent", 100) if risk_enabled else None
                ),
                "target_method": risk_rules.get("target_method", "risk_multiple"),
                "target_value": risk_rules.get("target_value", target_value),
                "minimum_reward_to_risk": (
                    risk_rules.get("minimum_reward_to_risk", 1) if risk_enabled else None
                ),
                "account_risk_percent": risk_rules.get("account_risk_percent"),
                "estimated_fee_bps": risk_rules.get("estimated_fee_bps", 0),
                "estimated_slippage_bps": risk_rules.get("estimated_slippage_bps", 0),
            },
            "near_miss": {
                "enabled": near_miss_rules.get("enabled", True),
                "thresholds": near_miss_rules.get("thresholds", [70, 80, 90]),
                "mandatory_fail_cap": near_miss_rules.get("mandatory_fail_cap", 90),
                "minimum_score_to_store": near_miss_rules.get("minimum_score_to_store", 40),
                "one_condition_remaining_enabled": near_miss_rules.get(
                    "one_condition_remaining_enabled", True
                ),
            },
            "alerts": {
                "forming_alerts": alert_rules.get("forming_alerts", True),
                "near_miss_threshold": alert_rules.get("near_miss_threshold", 70),
                "channels": alert_rules.get("channels", ["telegram"]),
                "cooldown_seconds": alert_rules.get("cooldown_seconds", 900),
                "maximum_alerts_per_hour": alert_rules.get("maximum_alerts_per_hour", 50),
                "daily_alert_budget": alert_rules.get("daily_alert_budget"),
                "suppress_repetitive_near_miss": alert_rules.get(
                    "suppress_repetitive_near_miss", True
                ),
                "alert_on_one_condition_remaining": alert_rules.get(
                    "alert_on_one_condition_remaining", True
                ),
            },
            "expiry": {
                "expire_after_candles": expiry_rules.get("expire_after_candles", 3),
                "expire_if_price_moves_beyond_entry_percent": expiry_rules.get(
                    "expire_if_price_moves_beyond_entry_percent"
                ),
            },
            "forward_test": value.get("forward_test", {}),
            "position_sizing": value.get("position_sizing", {}),
        }

    @classmethod
    def _normalize_logic(cls, logic: dict[str, Any], fallback_timeframe: str) -> dict[str, Any]:
        operator = str(logic.get("operator", "AND")).lower()
        raw_conditions = logic.get("conditions", [])
        if not isinstance(raw_conditions, list):
            raw_conditions = [raw_conditions]
        children: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_conditions):
            if not isinstance(raw, dict):
                instruction = str(raw).strip() or "Unsupported condition"
                children.append(
                    {
                        "node_type": "condition",
                        "key": f"condition_{index + 1}",
                        "label": f"Clarify: {instruction[:220]}",
                        "condition_type": ConditionType.PRICE_ACTION.value,
                        "timeframe": fallback_timeframe,
                        "left": {
                            "kind": OperandKind.PRICE_ACTION.value,
                            "name": "unsupported_freeform_condition",
                            "field": "clarification_required",
                            "parameters": {"instruction": instruction[:500]},
                        },
                        "comparator": Comparator.IS_TRUE.value,
                        "right": None,
                        "required": True,
                        "weight": 1,
                        "cap_score_on_fail": 0,
                        "required_data": [],
                        "explanation_template": (
                            "This instruction needs a supported deterministic rule "
                            "before it can run."
                        ),
                        "notes": instruction[:500],
                        "source_fragment": instruction[:500],
                        "confidence": 0,
                        "ai_interpreted": True,
                        "provider_required": True,
                        "availability": "unsupported",
                    }
                )
                continue
            if "conditions" in raw:
                children.append(cls._normalize_logic(raw, fallback_timeframe))
                continue
            condition_id = raw.get("condition_id") or raw.get("id") or f"condition_{index + 1}"
            comparator = str(raw.get("operator", raw.get("comparator", "gte"))).lower()
            threshold = raw.get("threshold", raw.get("expected_state", raw.get("value")))
            condition_type = raw.get("type", raw.get("condition_type", "indicator"))
            name = raw.get("name", condition_id.replace("_", " ").title())
            left_kind = raw.get("operand_kind")
            if left_kind is None:
                left_kind = "market_metric" if condition_type == "market_filter" else condition_type
            left_operand = raw.get("left") or {
                "kind": left_kind,
                "name": raw.get("indicator") or raw.get("metric") or raw.get("pattern"),
                "field": raw.get("field"),
                "parameters": raw.get("parameters", {}),
            }
            right_operand = raw.get("right")
            if right_operand is None and comparator not in {"is_true", "is_false"}:
                right_operand = {"kind": "constant", "value": threshold}
            children.append(
                {
                    "node_type": "condition",
                    "capability_key": raw.get("capability_key"),
                    "key": condition_id,
                    "label": name,
                    "condition_type": condition_type,
                    "timeframe": raw.get("timeframe", fallback_timeframe),
                    "left": left_operand,
                    "comparator": comparator,
                    "right": right_operand,
                    "required": raw.get("mandatory", raw.get("required", True)),
                    "weight": raw.get("weight", 1),
                    "cap_score_on_fail": raw.get("cap_score_on_fail"),
                    "required_data": raw.get("required_data", []),
                    "explanation_template": raw.get("explanation_template"),
                    "forming_tolerance_percent": raw.get("forming_tolerance_percent"),
                    "source_fragment": raw.get("source_fragment"),
                    "confidence": raw.get("confidence"),
                    "ai_interpreted": raw.get("ai_interpreted", False),
                    "provider_required": raw.get("provider_required", False),
                    "availability": raw.get("availability", "available"),
                }
            )
        return {
            "node_type": "group",
            "key": logic.get("id", "entry_conditions"),
            "operator": operator,
            "parameters": logic.get("parameters", {}),
            "children": children,
        }

    @model_validator(mode="after")
    def validate_strategy(self) -> "StrategyDefinition":
        keys: list[str] = []
        timeframes: set[str] = set()

        def walk(node: ConditionRule | ConditionGroup) -> None:
            keys.append(node.key)
            if isinstance(node, ConditionRule):
                timeframes.add(node.timeframe)
            else:
                for child in node.children:
                    walk(child)

        walk(self.conditions)
        if len(keys) != len(set(keys)):
            raise ValueError("condition and group keys must be unique")
        missing = [
            timeframe
            for timeframe in sorted(timeframes - {self.base_timeframe})
            if timeframe not in self.supporting_timeframes
        ]
        if missing:
            self.supporting_timeframes = [*self.supporting_timeframes, *missing]
        if len(self.supporting_timeframes) > 10:
            raise ValueError("too many supporting timeframes")
        if self.universe.market_type != MarketType.SPOT:
            raise ValueError("version one supports spot markets only")
        return self

    def canonical_hash(self) -> str:
        payload_data = self.model_dump(mode="json", exclude_none=False, by_alias=True)

        def preserve_legacy_shape(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: preserve_legacy_shape(item)
                    for key, item in value.items()
                    if not (key == "capability_key" and item is None)
                }
            if isinstance(value, list):
                return [preserve_legacy_shape(item) for item in value]
            return value

        payload = json.dumps(
            preserve_legacy_shape(payload_data),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class InterpretationIssue(BaseModel):
    code: str
    message: str
    field: str | None = None
    options: list[str] = Field(default_factory=list)
    blocking: bool = True
    source_fragment: str | None = Field(default=None, max_length=500)


class InterpretationPreview(BaseModel):
    strategy: StrategyDefinition
    assumptions: list[str] = Field(default_factory=list)
    ambiguities: list[InterpretationIssue] = Field(default_factory=list)
    unsupported_conditions: list[InterpretationIssue] = Field(default_factory=list)
    interpreter: str
    raw_metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def activation_blocked(self) -> bool:
        return bool(
            self.ambiguities or any(issue.blocking for issue in self.unsupported_conditions)
        )
