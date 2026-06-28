from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ai_market_monitor.db.models.enums import ScanOutcome, StrategyStatus
from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.engine.models import MarketSnapshot
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.interfaces import Candle


@dataclass(frozen=True, slots=True)
class AlertEvidence:
    alert_created: bool = False
    delivery_failed: bool = False
    cooldown_blocked: bool = False
    duplicate_blocked: bool = False
    technical_error: str | None = None


@dataclass(frozen=True, slots=True)
class InvestigationResult:
    evaluated: bool
    strategy_version: str | None
    data_available: bool
    conditions_passed: list[str]
    conditions_failed: list[str]
    near_miss_threshold_reached: bool
    alert_created: bool
    delivery_failed: bool
    cooldown_prevented_delivery: bool
    symbol_excluded: bool
    subscription_limited: bool
    strategy_inactive: bool
    system_error: str | None
    explanation: str
    proof: dict[str, Any] | None = None
    chart_reference: str | None = None


class ForensicInvestigationService:
    def __init__(self, engine: StrategyRuleEngine | None = None) -> None:
        self.engine = engine or StrategyRuleEngine()

    def investigate(
        self,
        *,
        strategy: StrategyDefinition,
        strategy_version: str,
        strategy_status: StrategyStatus,
        market: MarketSnapshot,
        candle_sets: dict[str, list[Candle]],
        approximate_time: datetime,
        subscription_allowed: bool = True,
        evidence: AlertEvidence | None = None,
        previous_score: float | None = None,
        chart_reference: str | None = None,
    ) -> InvestigationResult:
        evidence = evidence or AlertEvidence()
        if strategy_status != StrategyStatus.ACTIVE:
            return InvestigationResult(
                evaluated=False,
                strategy_version=strategy_version,
                data_available=bool(candle_sets),
                conditions_passed=[],
                conditions_failed=[],
                near_miss_threshold_reached=False,
                alert_created=False,
                delivery_failed=False,
                cooldown_prevented_delivery=False,
                symbol_excluded=False,
                subscription_limited=False,
                strategy_inactive=True,
                system_error=None,
                explanation="The strategy was not active at the requested time.",
            )
        if not subscription_allowed:
            return InvestigationResult(
                evaluated=False,
                strategy_version=strategy_version,
                data_available=bool(candle_sets),
                conditions_passed=[],
                conditions_failed=[],
                near_miss_threshold_reached=False,
                alert_created=False,
                delivery_failed=False,
                cooldown_prevented_delivery=False,
                symbol_excluded=False,
                subscription_limited=True,
                strategy_inactive=False,
                system_error=None,
                explanation="Subscription limits prevented this market from being scanned.",
            )
        result = self.engine.evaluate(
            strategy,
            market,
            candle_sets,
            evaluation_time=approximate_time,
            strategy_version=strategy_version,
            previous_score=previous_score,
            chart_reference=chart_reference,
        )
        symbol_excluded = result.outcome == ScanOutcome.SKIPPED and bool(
            result.market_filters.reasons
        )
        passed = [condition.condition_id for condition in result.conditions if condition.passed]
        failed = [condition.condition_id for condition in result.conditions if not condition.passed]
        if evidence.cooldown_blocked:
            explanation = "The setup evaluated, but alert delivery was blocked by cooldown rules."
        elif evidence.duplicate_blocked:
            explanation = "The setup evaluated, but duplicate suppression blocked a repeated alert."
        elif evidence.delivery_failed:
            explanation = "An alert was created, but delivery failed."
        elif evidence.alert_created:
            explanation = "An alert was created for this evaluation."
        elif symbol_excluded:
            explanation = f"The market was skipped: {', '.join(result.market_filters.reasons)}."
        elif result.near_miss.current_score < strategy.alerts.near_miss_threshold:
            explanation = "No alert was sent because the Near-Miss threshold was not reached."
        else:
            explanation = "The deterministic evaluation did not meet alert creation rules."
        return InvestigationResult(
            evaluated=True,
            strategy_version=strategy_version,
            data_available=bool(candle_sets),
            conditions_passed=passed,
            conditions_failed=failed,
            near_miss_threshold_reached=(
                result.near_miss.current_score >= strategy.alerts.near_miss_threshold
            ),
            alert_created=evidence.alert_created,
            delivery_failed=evidence.delivery_failed,
            cooldown_prevented_delivery=evidence.cooldown_blocked,
            symbol_excluded=symbol_excluded,
            subscription_limited=False,
            strategy_inactive=False,
            system_error=evidence.technical_error,
            explanation=explanation,
            proof=result.proof_receipt(),
            chart_reference=chart_reference,
        )
