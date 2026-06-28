from ai_market_monitor.engine.models import (
    ConditionEvaluation,
    EvaluationState,
    NearMissScore,
    ScoreTrend,
)
from ai_market_monitor.schemas.strategy import StrategyDefinition


class NearMissScoringEngine:
    def score(
        self,
        strategy: StrategyDefinition,
        conditions: list[ConditionEvaluation],
        *,
        previous_score: float | None = None,
        last_alert_score: float | None = None,
    ) -> NearMissScore:
        if not conditions:
            return NearMissScore(
                0, previous_score, ScoreTrend.NEW, [], [], None, False, None, False
            )
        total_weight = sum(condition.weight for condition in conditions)
        weighted_score = (
            sum(condition.proximity_score * condition.weight for condition in conditions)
            / total_weight
        )
        failed_mandatory_caps = [
            condition.cap_score_on_fail
            if condition.cap_score_on_fail is not None
            else strategy.near_miss.mandatory_fail_cap
            for condition in conditions
            if condition.mandatory and condition.state != EvaluationState.PASSED
        ]
        current_score = (
            min(weighted_score, *failed_mandatory_caps) if failed_mandatory_caps else weighted_score
        )
        passed = [
            condition for condition in conditions if condition.state == EvaluationState.PASSED
        ]
        missing = [
            condition for condition in conditions if condition.state != EvaluationState.PASSED
        ]
        closest = max(missing, key=lambda condition: condition.proximity_score, default=None)
        trend = self._trend(previous_score, current_score)
        threshold_crossed = self._threshold_crossed(
            strategy.near_miss.thresholds, previous_score, current_score
        )
        unsafe_mandatory = any(
            condition.mandatory
            and condition.state in {EvaluationState.ERROR, EvaluationState.UNAVAILABLE}
            for condition in conditions
        )
        one_remaining = len(missing) == 1 and missing[0].state == EvaluationState.FAILED
        threshold = strategy.alerts.near_miss_threshold
        repetitive = (
            last_alert_score is not None
            and abs(current_score - last_alert_score) < 1
            and strategy.alerts.suppress_repetitive_near_miss
        )
        should_alert = (
            strategy.near_miss.enabled
            and not unsafe_mandatory
            and not repetitive
            and (
                current_score >= threshold
                or threshold_crossed is not None
                or (
                    one_remaining
                    and strategy.near_miss.one_condition_remaining_enabled
                    and strategy.alerts.alert_on_one_condition_remaining
                )
            )
        )
        return NearMissScore(
            current_score=max(0, min(100, current_score)),
            previous_score=previous_score,
            trend=trend,
            passed_conditions=passed,
            missing_conditions=missing,
            closest_missing_condition=closest,
            one_condition_remaining=one_remaining,
            threshold_crossed=threshold_crossed,
            should_alert=should_alert,
        )

    @staticmethod
    def _trend(previous: float | None, current: float) -> ScoreTrend:
        if previous is None:
            return ScoreTrend.NEW
        if current > previous + 0.5:
            return ScoreTrend.IMPROVING
        if current < previous - 0.5:
            return ScoreTrend.WEAKENING
        return ScoreTrend.STABLE

    @staticmethod
    def _threshold_crossed(
        thresholds: list[int], previous: float | None, current: float
    ) -> int | None:
        for threshold in sorted(thresholds):
            if current >= threshold and (previous is None or previous < threshold):
                return threshold
        return None
