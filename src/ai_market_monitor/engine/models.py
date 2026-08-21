from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ai_market_monitor.db.models.enums import ScanOutcome, SetupLifecycleState


class EvaluationState(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    PENDING = "pending"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


class ScoreTrend(StrEnum):
    IMPROVING = "improving"
    WEAKENING = "weakening"
    STABLE = "stable"
    NEW = "new"


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    exchange: str
    symbol: str
    quote_asset: str
    base_asset: str | None = None
    market_type: str = "spot"
    quote_volume_24h: float | None = None
    average_candle_volume: float | None = None
    spread_bps: float | None = None
    listed_at: datetime | None = None
    market_cap: float | None = None
    data_quality_ok: bool = True
    exchange_available: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConditionEvaluation:
    condition_id: str
    name: str
    condition_type: str
    operator: str
    timeframe: str
    required_value: Any
    actual_value: Any
    state: EvaluationState
    weight: float
    mandatory: bool
    required_data: list[str]
    evaluation_time: datetime
    market_data_timestamp: datetime | None
    data_latency_ms: int | None
    explanation: str
    proximity_score: float
    cap_score_on_fail: float | None = None
    error_code: str | None = None
    previous_actual_value: Any = None
    previous_required_value: Any = None
    mechanic_evidence: dict[str, Any] | None = None
    semantic_contract: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        return self.state == EvaluationState.PASSED

    def to_proof_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "name": self.name,
            "type": self.condition_type,
            "operator": self.operator,
            "timeframe": self.timeframe,
            "required_value": self.required_value,
            "actual_value": self.actual_value,
            "previous_actual_value": self.previous_actual_value,
            "previous_required_value": self.previous_required_value,
            "state": self.state.value,
            "weight": self.weight,
            "mandatory": self.mandatory,
            "blocking": self.mandatory,
            "required_data": self.required_data,
            "evaluation_time": self.evaluation_time.isoformat(),
            "market_data_timestamp": (
                self.market_data_timestamp.isoformat() if self.market_data_timestamp else None
            ),
            "data_latency_ms": self.data_latency_ms,
            "explanation": self.explanation,
            "proximity_score": round(self.proximity_score, 4),
            "error_code": self.error_code,
            "mechanic_evidence": self.mechanic_evidence,
            "semantic_contract": self.semantic_contract,
        }


@dataclass(frozen=True, slots=True)
class ConditionTreeEvaluation:
    node_id: str
    node_type: str
    state: EvaluationState
    score: float
    blocking: bool
    operator: str | None = None
    condition: ConditionEvaluation | None = None
    children: tuple["ConditionTreeEvaluation", ...] = ()
    selected_child_id: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.state == EvaluationState.PASSED

    def all_leaves(self) -> list[ConditionEvaluation]:
        if self.condition is not None:
            return [self.condition]
        return [leaf for child in self.children for leaf in child.all_leaves()]

    def scoring_leaves(self) -> list[ConditionEvaluation]:
        if self.condition is not None:
            return [self.condition]
        if self.operator == "or" and self.selected_child_id:
            selected = next(
                (child for child in self.children if child.node_id == self.selected_child_id),
                None,
            )
            return selected.scoring_leaves() if selected else []
        if self.operator == "not":
            return []
        return [leaf for child in self.children for leaf in child.scoring_leaves()]

    def to_proof_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "state": self.state.value,
            "score": round(self.score, 4),
            "blocking": self.blocking,
        }
        if self.operator is not None:
            payload["operator"] = self.operator
            payload["parameters"] = self.parameters
            payload["selected_child_id"] = self.selected_child_id
            payload["children"] = [child.to_proof_dict() for child in self.children]
        elif self.condition is not None:
            payload["condition"] = self.condition.to_proof_dict()
        return payload


@dataclass(frozen=True, slots=True)
class NearMissScore:
    current_score: float
    previous_score: float | None
    trend: ScoreTrend
    passed_conditions: list[ConditionEvaluation]
    missing_conditions: list[ConditionEvaluation]
    closest_missing_condition: ConditionEvaluation | None
    one_condition_remaining: bool
    threshold_crossed: int | None
    should_alert: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_score": round(self.current_score, 3),
            "previous_score": round(self.previous_score, 3)
            if self.previous_score is not None
            else None,
            "trend": self.trend.value,
            "passed_conditions": [condition.condition_id for condition in self.passed_conditions],
            "missing_conditions": [
                condition.to_proof_dict() for condition in self.missing_conditions
            ],
            "closest_missing_condition": (
                self.closest_missing_condition.to_proof_dict()
                if self.closest_missing_condition
                else None
            ),
            "one_condition_remaining": self.one_condition_remaining,
            "threshold_crossed": self.threshold_crossed,
            "should_alert": self.should_alert,
        }


@dataclass(frozen=True, slots=True)
class RiskCalculation:
    direction: str
    entry_price: float
    entry_zone_low: float
    entry_zone_high: float
    invalidation_level: float
    stop_price: float
    stop_distance_percent: float
    targets: list[dict[str, float | str | None]]
    reward_to_risk: float
    estimated_fee_bps: float
    estimated_slippage_bps: float
    position_size: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "entry_price": self.entry_price,
            "entry_zone_low": self.entry_zone_low,
            "entry_zone_high": self.entry_zone_high,
            "invalidation_level": self.invalidation_level,
            "stop_price": self.stop_price,
            "stop_distance_percent": round(self.stop_distance_percent, 6),
            "targets": self.targets,
            "reward_to_risk": round(self.reward_to_risk, 6),
            "estimated_fee_bps": self.estimated_fee_bps,
            "estimated_slippage_bps": self.estimated_slippage_bps,
            "position_size": self.position_size,
        }


@dataclass(frozen=True, slots=True)
class MarketFilterResult:
    passed: bool
    reasons: list[str]
    metrics: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    strategy_id: str | None
    strategy_name: str
    strategy_version: str
    strategy_version_id: str | None
    strategy_version_number: int | None
    strategy_schema_hash: str
    direction: str
    exchange: str
    symbol: str
    market_type: str
    timeframe: str
    evaluation_time: datetime
    market_data_timestamp: datetime | None
    data_latency_ms: int | None
    market_data_provider: str
    candle_closed: bool | None
    conditions: list[ConditionEvaluation]
    condition_tree: ConditionTreeEvaluation | None
    risk_validation: ConditionEvaluation | None
    near_miss: NearMissScore
    risk: RiskCalculation | None
    market_filters: MarketFilterResult
    outcome: ScanOutcome
    setup_state: SetupLifecycleState | None
    setup_transition: dict[str, Any] | None
    reliability_warnings: list[str]
    chart_reference: str | None = None
    #: How many candles had closed since the newest one this check read, across every
    #: period it read. ``0`` means nothing newer existed anywhere.
    #:
    #: ``data_latency_ms`` alone cannot answer that question: it is a length of time, and
    #: whether a length of time is late depends entirely on the candle period. Readers
    #: grading freshness use this count; the milliseconds stay as the raw measurement.
    data_candles_behind: int | None = None
    #: The period ``data_latency_ms`` was measured on. Not always the base timeframe — a
    #: rule that also reads an hourly candle is only as fresh as its worst feed.
    data_freshness_timeframe: str | None = None

    def proof_receipt(self) -> dict[str, Any]:
        required_conditions = [condition for condition in self.conditions if condition.mandatory]
        optional_conditions = [
            condition for condition in self.conditions if not condition.mandatory
        ]
        required_passed = [
            condition
            for condition in required_conditions
            if condition.state == EvaluationState.PASSED
        ]
        optional_passed = [
            condition
            for condition in optional_conditions
            if condition.state == EvaluationState.PASSED
        ]
        optional_failed = [
            condition
            for condition in optional_conditions
            if condition.state != EvaluationState.PASSED
        ]
        required_completion_percent = (
            round((len(required_passed) / len(required_conditions)) * 100, 3)
            if required_conditions
            else 0
        )
        mandatory_unavailable = any(
            condition.state in {EvaluationState.ERROR, EvaluationState.UNAVAILABLE}
            for condition in required_conditions
        )
        if mandatory_unavailable or self.outcome == ScanOutcome.ERROR:
            match_status = "blocked"
        elif self.outcome == ScanOutcome.CONFIRMED:
            match_status = "confirmed_match"
        elif self.outcome in {ScanOutcome.FORMING, ScanOutcome.NEAR_MISS}:
            match_status = self.outcome.value
        else:
            match_status = "no_match"
        receipt = {
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "strategy_version": self.strategy_version,
            "strategy_version_id": self.strategy_version_id,
            "strategy_version_number": self.strategy_version_number,
            "strategy_schema_hash": self.strategy_schema_hash,
            "direction": self.direction,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "market_type": self.market_type,
            "timeframe": self.timeframe,
            "evaluation_time": self.evaluation_time.isoformat(),
            "market_data_timestamp": (
                self.market_data_timestamp.isoformat() if self.market_data_timestamp else None
            ),
            "data_latency_ms": self.data_latency_ms,
            "data_candles_behind": self.data_candles_behind,
            "data_freshness_timeframe": self.data_freshness_timeframe,
            "market_data_provider": self.market_data_provider,
            "candle_closed": self.candle_closed,
            "research_monitor": True,
            "monitor_mode": "trade_setup" if self.risk is not None else "research",
            "match_status": match_status,
            "required_conditions_total": len(required_conditions),
            "required_conditions_passed": len(required_passed),
            "required_completion_percent": required_completion_percent,
            "optional_conditions_total": len(optional_conditions),
            "optional_conditions_passed": len(optional_passed),
            "optional_conditions_failed": len(optional_failed),
            "match_rule": "100% of required monitored conditions must pass",
            "conditions": [condition.to_proof_dict() for condition in self.conditions],
            "condition_tree": self.condition_tree.to_proof_dict() if self.condition_tree else None,
            "risk_validation": self.risk_validation.to_proof_dict()
            if self.risk_validation
            else None,
            "entry_zone": (
                {
                    "low": self.risk.entry_zone_low,
                    "high": self.risk.entry_zone_high,
                    "entry_price": self.risk.entry_price,
                    "stop_distance_percent": self.risk.stop_distance_percent,
                }
                if self.risk
                else None
            ),
            "risk_calculation": self.risk.to_dict() if self.risk else None,
            "invalidation_level": self.risk.invalidation_level if self.risk else None,
            "stop_distance": self.risk.stop_distance_percent if self.risk else None,
            "target_levels": self.risk.targets if self.risk else [],
            "reward_to_risk": self.risk.reward_to_risk if self.risk else None,
            "liquidity_information": self.market_filters.metrics,
            "spread_bps": self.market_filters.metrics.get("spread_bps"),
            "setup_completion_score": round(self.near_miss.current_score, 3),
            "setup_state_transition": self.setup_transition,
            "setup_state": self.setup_state.value if self.setup_state else None,
            "chart_reference": self.chart_reference,
            "reliability_warnings": self.reliability_warnings,
        }
        from ai_market_monitor.engine.quality import alert_trust_score_from_proof

        receipt["alert_trust_score"] = alert_trust_score_from_proof(receipt)
        return receipt


def ensure_aware(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp
