from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ai_market_monitor.db.models.enums import LogicalOperator, ScanOutcome, SetupLifecycleState
from ai_market_monitor.engine.candle_patterns import (
    detect_candle_pattern,
    pattern_names,
)
from ai_market_monitor.engine.context_conditions import (
    TIME_CONDITION_NAMES,
    ContextDataUnavailable,
    context_metric,
    evaluate_time_condition,
)
from ai_market_monitor.engine.indicators import (
    IndicatorRegistry,
    IndicatorWarmupError,
    bollinger_band,
    ema,
    sma,
    volume_ratio,
    vwap,
)
from ai_market_monitor.engine.market_filters import MarketFilterEngine
from ai_market_monitor.engine.models import (
    ConditionEvaluation,
    ConditionTreeEvaluation,
    EvaluationResult,
    EvaluationState,
    MarketSnapshot,
    ensure_aware,
)
from ai_market_monitor.engine.price_action import (
    evaluate_price_action,
    supports_price_action,
)
from ai_market_monitor.engine.risk import RiskCalculation, RiskCalculationError, RiskCalculator
from ai_market_monitor.engine.scoring import NearMissScoringEngine
from ai_market_monitor.schemas.strategy import (
    Comparator,
    ConditionGroup,
    ConditionRule,
    Operand,
    OperandKind,
    StrategyDefinition,
    StrategyDirection,
)
from ai_market_monitor.services.interfaces import Candle


class StrategyRuleEngine:
    def __init__(
        self,
        *,
        indicators: IndicatorRegistry | None = None,
        filters: MarketFilterEngine | None = None,
        risk: RiskCalculator | None = None,
        scorer: NearMissScoringEngine | None = None,
    ) -> None:
        self.indicators = indicators or IndicatorRegistry()
        self.filters = filters or MarketFilterEngine()
        self.risk = risk or RiskCalculator(self.indicators)
        self.scorer = scorer or NearMissScoringEngine()

    def evaluate(
        self,
        strategy: StrategyDefinition,
        market: MarketSnapshot,
        candle_sets: dict[str, list[Candle]],
        *,
        evaluation_time: datetime,
        strategy_version: str,
        strategy_id: str | None = None,
        strategy_version_id: str | None = None,
        strategy_version_number: int | None = None,
        market_data_provider: str = "unknown",
        evaluation_direction: StrategyDirection | str | None = None,
        previous_score: float | None = None,
        last_near_miss_alert_score: float | None = None,
        chart_reference: str | None = None,
        account_balance: float | None = None,
        condition_context: dict[str, Any] | None = None,
    ) -> EvaluationResult:
        strategy_schema_hash = strategy.canonical_hash()
        if evaluation_direction is not None:
            evaluation_direction = StrategyDirection(evaluation_direction)
            if (
                strategy.direction != StrategyDirection.BOTH
                and strategy.direction != evaluation_direction
            ):
                raise ValueError("Evaluation direction conflicts with strategy direction")
            strategy = strategy.model_copy(update={"direction": evaluation_direction})
        evaluation_time = ensure_aware(evaluation_time)
        filtered_candles = {
            timeframe: self._history(candles, evaluation_time, strategy.trigger_mode.value)
            for timeframe, candles in candle_sets.items()
        }
        market_filters = self.filters.evaluate(strategy, market, filtered_candles, evaluation_time)
        if not market_filters.passed:
            conditions: list[ConditionEvaluation] = []
            near_miss = self.scorer.score(strategy, conditions, previous_score=previous_score)
            return EvaluationResult(
                strategy_id=strategy_id,
                strategy_name=strategy.name,
                strategy_version=strategy_version,
                strategy_version_id=strategy_version_id,
                strategy_version_number=strategy_version_number,
                strategy_schema_hash=strategy_schema_hash,
                direction=strategy.direction.value,
                exchange=market.exchange,
                symbol=market.symbol,
                market_type=market.market_type,
                timeframe=strategy.base_timeframe,
                evaluation_time=evaluation_time,
                market_data_timestamp=self._latest_timestamp(filtered_candles),
                data_latency_ms=self._latency_ms(evaluation_time, filtered_candles),
                market_data_provider=market_data_provider,
                candle_closed=self._base_candle_closed(strategy, filtered_candles),
                conditions=conditions,
                condition_tree=None,
                risk_validation=None,
                near_miss=near_miss,
                risk=None,
                market_filters=market_filters,
                outcome=ScanOutcome.SKIPPED,
                setup_state=None,
                setup_transition=None,
                reliability_warnings=list(market_filters.reasons),
                chart_reference=chart_reference,
            )
        risk_result: RiskCalculation | None = None
        risk_validation: ConditionEvaluation | None = None
        if strategy.risk.enabled:
            risk_result, risk_validation = self._evaluate_risk(
                strategy,
                filtered_candles,
                evaluation_time,
                account_balance,
            )
        category = str(market.metadata.get("category") or "").strip().casefold()
        market_context = {
            **dict((condition_context or {}).get("market_context", {})),
            "market_cap_minimum": market.market_cap,
        }
        if category:
            market_context["meme_coin_exclusion"] = category not in {
                "meme",
                "meme coin",
                "memecoin",
            }
        merged_context = {
            **(condition_context or {}),
            "evaluation_time": evaluation_time,
            "data_latency_ms": self._latency_ms(evaluation_time, filtered_candles),
            "market_context": market_context,
        }
        merged_context["risk_context"] = self._risk_context(
            strategy,
            market,
            filtered_candles,
            evaluation_time,
            risk_result,
            risk_validation,
            merged_context,
        )
        condition_tree = self._evaluate_node(
            strategy.conditions,
            strategy,
            filtered_candles,
            evaluation_time,
            merged_context,
        )
        conditions = condition_tree.all_leaves()
        scoring_conditions = condition_tree.scoring_leaves()
        if risk_validation is not None:
            scoring_conditions.append(risk_validation)
        near_miss = self.scorer.score(
            strategy,
            scoring_conditions,
            previous_score=previous_score,
            last_alert_score=last_near_miss_alert_score,
        )
        risk_passed = risk_validation.passed if risk_validation is not None else True
        all_passed = condition_tree.passed and risk_passed
        any_pending = any(
            condition.state == EvaluationState.PENDING for condition in scoring_conditions
        )
        unsafe_evaluation = any(
            condition.mandatory
            and condition.state in {EvaluationState.ERROR, EvaluationState.UNAVAILABLE}
            for condition in scoring_conditions
        )
        if unsafe_evaluation:
            outcome = ScanOutcome.ERROR
            setup_state = None
        elif all_passed:
            outcome = ScanOutcome.CONFIRMED
            setup_state = SetupLifecycleState.CONFIRMED
        elif any_pending:
            outcome = ScanOutcome.FORMING
            setup_state = SetupLifecycleState.FORMING
        elif near_miss.current_score >= strategy.alerts.near_miss_threshold:
            outcome = ScanOutcome.NEAR_MISS
            setup_state = SetupLifecycleState.NEAR_CONFIRMATION
        else:
            outcome = ScanOutcome.SKIPPED
            setup_state = SetupLifecycleState.FORMING
        return EvaluationResult(
            strategy_id=strategy_id,
            strategy_name=strategy.name,
            strategy_version=strategy_version,
            strategy_version_id=strategy_version_id,
            strategy_version_number=strategy_version_number,
            strategy_schema_hash=strategy_schema_hash,
            direction=strategy.direction.value,
            exchange=market.exchange,
            symbol=market.symbol,
            market_type=market.market_type,
            timeframe=strategy.base_timeframe,
            evaluation_time=evaluation_time,
            market_data_timestamp=self._latest_timestamp(filtered_candles),
            data_latency_ms=self._latency_ms(evaluation_time, filtered_candles),
            market_data_provider=market_data_provider,
            candle_closed=self._base_candle_closed(strategy, filtered_candles),
            conditions=conditions,
            condition_tree=condition_tree,
            risk_validation=risk_validation,
            near_miss=near_miss,
            risk=risk_result,
            market_filters=market_filters,
            outcome=outcome,
            setup_state=setup_state,
            setup_transition=(
                {"from": None, "to": setup_state.value, "reason": outcome.value}
                if setup_state
                else None
            ),
            reliability_warnings=list(market.metadata.get("reliability_warnings", [])),
            chart_reference=chart_reference,
        )

    def _evaluate_node(
        self,
        node: ConditionRule | ConditionGroup,
        strategy: StrategyDefinition,
        candle_sets: dict[str, list[Candle]],
        evaluation_time: datetime,
        condition_context: dict[str, Any] | None = None,
    ) -> ConditionTreeEvaluation:
        condition_context = condition_context or {}
        if isinstance(node, ConditionRule):
            evaluation = self._condition(
                node,
                strategy,
                candle_sets,
                evaluation_time,
                condition_context,
            )
            return ConditionTreeEvaluation(
                node_id=node.key,
                node_type="condition",
                state=evaluation.state,
                score=evaluation.proximity_score,
                blocking=node.required,
                condition=evaluation,
            )
        children = tuple(
            self._evaluate_node(
                child,
                strategy,
                candle_sets,
                evaluation_time,
                condition_context,
            )
            for child in node.children
        )
        if node.operator == LogicalOperator.OR:
            passed_children = [child for child in children if child.passed]
            candidates = passed_children or list(children)
            selected = max(candidates, key=lambda child: child.score)
            state = (
                EvaluationState.PASSED
                if passed_children
                else self._aggregate_failure_state(children)
            )
            return ConditionTreeEvaluation(
                node_id=node.key,
                node_type="group",
                operator=node.operator.value,
                state=state,
                score=selected.score,
                blocking=True,
                children=children,
                selected_child_id=selected.node_id,
                parameters=dict(node.parameters),
            )
        if node.operator == LogicalOperator.NOT:
            if any(
                child.state
                in {EvaluationState.ERROR, EvaluationState.UNAVAILABLE, EvaluationState.PENDING}
                for child in children
            ):
                state = self._aggregate_failure_state(children)
            elif any(child.passed for child in children):
                state = EvaluationState.FAILED
            else:
                state = EvaluationState.PASSED
            score = 100.0 if state == EvaluationState.PASSED else 0.0
            return ConditionTreeEvaluation(
                node_id=node.key,
                node_type="group",
                operator=node.operator.value,
                state=state,
                score=score,
                blocking=True,
                children=children,
                parameters=dict(node.parameters),
            )
        if node.operator == LogicalOperator.COUNT_OF:
            minimum = int(node.parameters.get("minimum_pass_count", 1))
            passed_count = sum(child.passed for child in children)
            state = (
                EvaluationState.PASSED
                if passed_count >= minimum
                else self._aggregate_failure_state(children)
            )
            return ConditionTreeEvaluation(
                node_id=node.key,
                node_type="group",
                operator=node.operator.value,
                state=state,
                score=min(100.0, (passed_count / minimum) * 100),
                blocking=True,
                children=children,
                parameters={**node.parameters, "actual_pass_count": passed_count},
            )
        if node.operator == LogicalOperator.CONDITIONAL_BRANCH:
            condition, then_branch, otherwise_branch = children
            if condition.state in {EvaluationState.ERROR, EvaluationState.UNAVAILABLE}:
                selected = condition
            else:
                selected = then_branch if condition.passed else otherwise_branch
            return ConditionTreeEvaluation(
                node_id=node.key,
                node_type="group",
                operator=node.operator.value,
                state=selected.state,
                score=selected.score,
                blocking=True,
                children=children,
                selected_child_id=selected.node_id,
                parameters=dict(node.parameters),
            )
        if node.operator == LogicalOperator.SEQUENCE:
            state, score, matched_indexes = self._evaluate_sequence(
                node,
                strategy,
                candle_sets,
                evaluation_time,
                condition_context,
            )
            return ConditionTreeEvaluation(
                node_id=node.key,
                node_type="group",
                operator=node.operator.value,
                state=state,
                score=score,
                blocking=True,
                children=children,
                parameters={**node.parameters, "matched_candle_indexes": matched_indexes},
            )
        if node.operator in {
            LogicalOperator.WITHIN_LAST,
            LogicalOperator.PERSISTED_FOR,
            LogicalOperator.FIRST_TIME_TRUE,
            LogicalOperator.CHANGED_STATE,
            LogicalOperator.CROSS_WITH_CONFIRMATION,
        }:
            state, score, evidence = self._evaluate_temporal_group(
                node,
                strategy,
                candle_sets,
                evaluation_time,
                condition_context,
            )
            return ConditionTreeEvaluation(
                node_id=node.key,
                node_type="group",
                operator=node.operator.value,
                state=state,
                score=score,
                blocking=True,
                children=children,
                parameters={**node.parameters, **evidence},
            )
        if node.operator == LogicalOperator.COOLDOWN_CONDITION:
            blocking_children = [child for child in children if child.blocking]
            child_state = (
                EvaluationState.PASSED
                if all(child.passed for child in blocking_children)
                else self._aggregate_failure_state(tuple(blocking_children))
            )
            cooldown_minutes = int(node.parameters.get("cooldown_minutes", 120))
            scope = str(node.parameters.get("scope", "per_symbol"))
            context_key = (
                "last_symbol_triggered_at"
                if scope == "per_symbol"
                else "last_strategy_triggered_at"
            )
            last_triggered = condition_context.get(context_key)
            if last_triggered is None:
                last_triggered = condition_context.get("last_triggered_at")
            if isinstance(last_triggered, str):
                last_triggered = ensure_aware(datetime.fromisoformat(last_triggered))
            cooldown_active = bool(
                last_triggered
                and evaluation_time
                < ensure_aware(last_triggered) + timedelta(minutes=cooldown_minutes)
            )
            state = (
                EvaluationState.FAILED
                if child_state == EvaluationState.PASSED and cooldown_active
                else child_state
            )
            return ConditionTreeEvaluation(
                node_id=node.key,
                node_type="group",
                operator=node.operator.value,
                state=state,
                score=0.0 if cooldown_active else self._weighted_tree_score(children),
                blocking=True,
                children=children,
                parameters={
                    **node.parameters,
                    "scope": scope,
                    "cooldown_active": cooldown_active,
                    "last_triggered_at": (
                        ensure_aware(last_triggered).isoformat() if last_triggered else None
                    ),
                },
            )
        blocking_children = [child for child in children if child.blocking]
        state = (
            EvaluationState.PASSED
            if all(child.passed for child in blocking_children)
            else self._aggregate_failure_state(tuple(blocking_children))
        )
        leaves = [leaf for child in children for leaf in child.scoring_leaves()]
        total_weight = sum(leaf.weight for leaf in leaves)
        score = (
            sum(leaf.proximity_score * leaf.weight for leaf in leaves) / total_weight
            if total_weight
            else 100.0
        )
        return ConditionTreeEvaluation(
            node_id=node.key,
            node_type="group",
            operator=node.operator.value,
            state=state,
            score=score,
            blocking=True,
            children=children,
            parameters=dict(node.parameters),
        )

    def _evaluate_sequence(
        self,
        node: ConditionGroup,
        strategy: StrategyDefinition,
        candle_sets: dict[str, list[Candle]],
        evaluation_time: datetime,
        condition_context: dict[str, Any],
    ) -> tuple[EvaluationState, float, list[int]]:
        maximum_gap = int(node.parameters.get("max_candles_between", 5))
        default_lookback = (maximum_gap * max(1, len(node.children) - 1)) + len(node.children)
        lookback = int(node.parameters.get("lookback_candles", default_lookback))
        points = self._temporal_points(strategy, candle_sets, evaluation_time, lookback)
        if len(points) < len(node.children):
            return EvaluationState.PENDING, 0.0, []
        cache: dict[tuple[int, int], ConditionTreeEvaluation] = {}

        def evaluation(child_index: int, point_index: int) -> ConditionTreeEvaluation:
            key = (child_index, point_index)
            if key not in cache:
                cache[key] = self._node_at_time(
                    node.children[child_index],
                    strategy,
                    candle_sets,
                    points[point_index],
                    condition_context,
                )
            return cache[key]

        def search(
            child_index: int,
            start_index: int,
            previous_index: int | None,
        ) -> list[int] | None:
            if child_index >= len(node.children):
                return []
            end_index = len(points)
            if previous_index is not None:
                end_index = min(end_index, previous_index + maximum_gap + 1)
            for point_index in range(start_index, end_index):
                if evaluation(child_index, point_index).passed:
                    suffix = search(child_index + 1, point_index + 1, point_index)
                    if suffix is not None:
                        return [point_index, *suffix]
            return None

        matched = search(0, 0, None)
        if matched is not None:
            return EvaluationState.PASSED, 100.0, matched
        attempted = tuple(cache.values())
        state = self._aggregate_failure_state(attempted) if attempted else EvaluationState.FAILED
        progress = 0
        for child_index in range(len(node.children)):
            if any(
                result.passed
                for (cached_child, _), result in cache.items()
                if cached_child == child_index
            ):
                progress += 1
            else:
                break
        return state, (progress / len(node.children)) * 100, []

    def _evaluate_temporal_group(
        self,
        node: ConditionGroup,
        strategy: StrategyDefinition,
        candle_sets: dict[str, list[Candle]],
        evaluation_time: datetime,
        condition_context: dict[str, Any],
    ) -> tuple[EvaluationState, float, dict[str, Any]]:
        child = node.children[0]
        if node.operator == LogicalOperator.WITHIN_LAST:
            count = int(node.parameters.get("lookback_candles", 3))
            points = self._temporal_points(strategy, candle_sets, evaluation_time, count)
            evaluations = [
                self._node_at_time(
                    child,
                    strategy,
                    candle_sets,
                    point,
                    condition_context,
                )
                for point in points
            ]
            passed_indexes = [index for index, result in enumerate(evaluations) if result.passed]
            state = (
                EvaluationState.PASSED
                if passed_indexes
                else self._aggregate_failure_state(tuple(evaluations))
            )
            return (
                state,
                max((result.score for result in evaluations), default=0.0),
                {"matched_candle_indexes": passed_indexes},
            )

        if node.operator == LogicalOperator.PERSISTED_FOR:
            count = int(node.parameters.get("candles_count", 3))
            points = self._temporal_points(strategy, candle_sets, evaluation_time, count)
            if len(points) < count:
                return EvaluationState.PENDING, 0.0, {"evaluated_candles": len(points)}
            evaluations = [
                self._node_at_time(
                    child,
                    strategy,
                    candle_sets,
                    point,
                    condition_context,
                )
                for point in points
            ]
            passed_count = sum(result.passed for result in evaluations)
            state = (
                EvaluationState.PASSED
                if passed_count == count
                else self._aggregate_failure_state(tuple(evaluations))
            )
            return state, (passed_count / count) * 100, {"evaluated_candles": count}

        if node.operator in {
            LogicalOperator.FIRST_TIME_TRUE,
            LogicalOperator.CHANGED_STATE,
        }:
            points = self._temporal_points(strategy, candle_sets, evaluation_time, 2)
            if len(points) < 2:
                return EvaluationState.PENDING, 0.0, {"transition": None}
            previous = self._node_at_time(
                child,
                strategy,
                candle_sets,
                points[-2],
                condition_context,
            )
            current = self._node_at_time(
                child,
                strategy,
                candle_sets,
                points[-1],
                condition_context,
            )
            passed = current.passed and not previous.passed
            state = (
                EvaluationState.PASSED
                if passed
                else (current.state if not current.passed else EvaluationState.FAILED)
            )
            return (
                state,
                100.0 if passed else 0.0,
                {"transition": f"{previous.state.value}_to_{current.state.value}"},
            )

        confirmation_bars = int(node.parameters.get("confirmation_bars", 2))
        points = self._temporal_points(
            strategy,
            candle_sets,
            evaluation_time,
            confirmation_bars,
        )
        if len(points) < confirmation_bars:
            return EvaluationState.PENDING, 0.0, {"confirmed_bars": 0}
        evaluations: list[ConditionTreeEvaluation] = []
        if isinstance(child, ConditionRule) and child.comparator in {
            Comparator.CROSSES_ABOVE,
            Comparator.CROSSES_BELOW,
        }:
            evaluations.append(
                self._node_at_time(
                    child,
                    strategy,
                    candle_sets,
                    points[0],
                    condition_context,
                )
            )
            steady = child.model_copy(
                update={
                    "comparator": (
                        Comparator.GREATER_THAN
                        if child.comparator == Comparator.CROSSES_ABOVE
                        else Comparator.LESS_THAN
                    )
                }
            )
            evaluations.extend(
                self._node_at_time(
                    steady,
                    strategy,
                    candle_sets,
                    point,
                    condition_context,
                )
                for point in points[1:]
            )
        else:
            evaluations = [
                self._node_at_time(
                    child,
                    strategy,
                    candle_sets,
                    point,
                    condition_context,
                )
                for point in points
            ]
        passed_count = sum(result.passed for result in evaluations)
        state = (
            EvaluationState.PASSED
            if passed_count == confirmation_bars
            else self._aggregate_failure_state(tuple(evaluations))
        )
        return state, (passed_count / confirmation_bars) * 100, {"confirmed_bars": passed_count}

    @staticmethod
    def _temporal_points(
        strategy: StrategyDefinition,
        candle_sets: dict[str, list[Candle]],
        evaluation_time: datetime,
        count: int,
    ) -> list[datetime]:
        base = candle_sets.get(strategy.base_timeframe, [])
        points = [
            ensure_aware(candle.timestamp)
            for candle in base
            if ensure_aware(candle.timestamp) <= evaluation_time
        ]
        return points[-max(1, count) :]

    def _node_at_time(
        self,
        node: ConditionRule | ConditionGroup,
        strategy: StrategyDefinition,
        candle_sets: dict[str, list[Candle]],
        evaluation_time: datetime,
        condition_context: dict[str, Any],
    ) -> ConditionTreeEvaluation:
        truncated = {
            timeframe: [
                candle for candle in candles if ensure_aware(candle.timestamp) <= evaluation_time
            ]
            for timeframe, candles in candle_sets.items()
        }
        return self._evaluate_node(
            node,
            strategy,
            truncated,
            evaluation_time,
            condition_context,
        )

    @staticmethod
    def _weighted_tree_score(children: tuple[ConditionTreeEvaluation, ...]) -> float:
        leaves = [leaf for child in children for leaf in child.scoring_leaves()]
        total_weight = sum(leaf.weight for leaf in leaves)
        if not total_weight:
            return 100.0 if all(child.passed for child in children) else 0.0
        return sum(leaf.proximity_score * leaf.weight for leaf in leaves) / total_weight

    @staticmethod
    def _aggregate_failure_state(
        children: tuple[ConditionTreeEvaluation, ...],
    ) -> EvaluationState:
        states = {child.state for child in children}
        for state in (
            EvaluationState.ERROR,
            EvaluationState.UNAVAILABLE,
            EvaluationState.PENDING,
            EvaluationState.FAILED,
        ):
            if state in states:
                return state
        return EvaluationState.FAILED

    def _condition(
        self,
        condition: ConditionRule,
        strategy: StrategyDefinition,
        candle_sets: dict[str, list[Candle]],
        evaluation_time: datetime,
        condition_context: dict[str, Any] | None = None,
    ) -> ConditionEvaluation:
        condition_context = {
            **(condition_context or {}),
            "current_condition_key": condition.key,
        }
        candles = candle_sets.get(condition.timeframe, [])
        market_timestamp = candles[-1].timestamp if candles else None
        latency = (
            int((evaluation_time - ensure_aware(market_timestamp)).total_seconds() * 1000)
            if market_timestamp
            else None
        )
        if not candles:
            return self._unavailable(
                condition, evaluation_time, "missing_history", market_timestamp, latency
            )
        if strategy.trigger_mode.value == "candle_close" and not candles[-1].is_closed:
            return self._pending(
                condition, evaluation_time, "candle_incomplete", market_timestamp, latency
            )
        try:
            crossing = condition.comparator in {
                Comparator.CROSSES_ABOVE,
                Comparator.CROSSES_BELOW,
            }
            if crossing:
                previous_actual, actual = self._operand_pair(
                    condition.left,
                    candles,
                    candle_sets,
                    condition_context,
                )
            else:
                previous_actual = None
                actual = self._operand(
                    condition.left,
                    candles,
                    candle_sets,
                    condition_context,
                )
            if condition.right:
                if crossing:
                    previous_required, required = self._operand_pair(
                        condition.right,
                        candles,
                        candle_sets,
                        condition_context,
                    )
                else:
                    previous_required = None
                    required = self._operand(
                        condition.right,
                        candles,
                        candle_sets,
                        condition_context,
                    )
            else:
                previous_required, required = None, condition.comparator.value
            passed = self._compare(
                condition.comparator,
                actual,
                required,
                previous_actual=previous_actual,
                previous_required=previous_required,
            )
            state = EvaluationState.PASSED if passed else EvaluationState.FAILED
            proximity = self._proximity(condition.comparator, actual, required, passed)
            explanation = self._explain(condition, actual, required, state)
            return ConditionEvaluation(
                condition_id=condition.key,
                name=condition.label,
                condition_type=condition.condition_type.value,
                operator=condition.comparator.value,
                timeframe=condition.timeframe,
                required_value=required,
                actual_value=actual,
                state=state,
                weight=condition.weight,
                mandatory=condition.required,
                required_data=condition.required_data,
                evaluation_time=evaluation_time,
                market_data_timestamp=market_timestamp,
                data_latency_ms=latency,
                explanation=explanation,
                proximity_score=proximity,
                cap_score_on_fail=condition.cap_score_on_fail,
                previous_actual_value=previous_actual,
                previous_required_value=previous_required,
            )
        except IndicatorWarmupError as exc:
            return self._pending(condition, evaluation_time, str(exc), market_timestamp, latency)
        except ContextDataUnavailable as exc:
            return self._unavailable(
                condition,
                evaluation_time,
                str(exc),
                market_timestamp,
                latency,
            )
        except Exception as exc:
            return self._error(
                condition, evaluation_time, type(exc).__name__, market_timestamp, latency
            )

    def _operand_pair(
        self,
        operand: Operand,
        candles: list[Candle],
        candle_sets: dict[str, list[Candle]],
        condition_context: dict[str, Any] | None = None,
    ) -> tuple[Any, Any]:
        condition_context = condition_context or {}
        current = self._operand(operand, candles, candle_sets, condition_context)
        if operand.kind == OperandKind.CONSTANT:
            return current, current
        if len(candles) < 2:
            raise IndicatorWarmupError("crossing evaluation requires at least 2 candles")
        previous = self._operand(
            operand,
            candles[:-1],
            candle_sets,
            condition_context,
        )
        return previous, current

    def _operand(
        self,
        operand: Operand | None,
        candles: list[Candle],
        candle_sets: dict[str, list[Candle]],
        condition_context: dict[str, Any] | None = None,
    ) -> Any:
        condition_context = condition_context or {}
        if operand is None:
            return None
        if operand.kind == OperandKind.CONSTANT:
            return operand.value
        if operand.kind == OperandKind.PRICE:
            search_parameters = self._search_parameters(operand)
            if search_parameters:
                searched = self._search_candles(candles, search_parameters)
                if not searched:
                    raise IndicatorWarmupError("price search window contained no candles")
                field = operand.field or "close"
                values = [getattr(candle, field) for candle in searched]
                aggregate = str(operand.parameters.get("aggregate", "latest"))
                if aggregate == "max":
                    return max(values)
                if aggregate == "min":
                    return min(values)
                return values[-1]
            return getattr(candles[-1], operand.field or "close")
        if operand.kind == OperandKind.INDICATOR:
            name = operand.name or ""
            return self.indicators.calculate(name, candles, **operand.parameters)
        if operand.kind == OperandKind.PRICE_ACTION:
            return self._price_action(operand, candles)
        if operand.kind == OperandKind.CANDLE_PATTERN:
            return self._candle_pattern(operand, candles)
        if operand.kind == OperandKind.MARKET_METRIC:
            if operand.name in TIME_CONDITION_NAMES:
                return evaluate_time_condition(
                    operand.name or "",
                    candles,
                    dict(operand.parameters),
                    condition_context,
                )
            if operand.name == "historical_candles":
                return len(candles)
            if operand.name == "average_volume":
                period = int(operand.parameters.get("period", min(20, len(candles))))
                return sum(candle.volume for candle in candles[-period:]) / period
            if operand.name in {"volume_multiplier", "volume_ratio"}:
                period = int(operand.parameters.get("period", 20))
                return volume_ratio(candles, period=period)
            if operand.name in {"average_candle_volume", "min_average_candle_volume"}:
                period = int(operand.parameters.get("period", min(20, len(candles))))
                return sum(candle.volume for candle in candles[-period:]) / period
            return context_metric(
                operand.name or "",
                dict(operand.parameters),
                condition_context,
            )
        if operand.kind == OperandKind.RISK_METRIC:
            return context_metric(
                operand.name or "",
                {
                    **dict(operand.parameters),
                    "context_category": "risk_context",
                },
                condition_context,
            )
        raise KeyError(f"Unsupported operand: {operand.kind.value}:{operand.name}")

    @staticmethod
    def _price_action(operand: Operand, candles: list[Candle]) -> bool:
        search_parameters = StrategyRuleEngine._search_parameters(operand)
        if search_parameters:
            candidate = operand.model_copy(
                update={
                    "parameters": {
                        key: value
                        for key, value in operand.parameters.items()
                        if key not in {"search_lookback", "search_start", "search_end"}
                    }
                }
            )
            return StrategyRuleEngine._any_search_match(
                candidate,
                candles,
                search_parameters,
                StrategyRuleEngine._price_action,
            )
        lookback = int(operand.parameters.get("lookback", 20))
        if len(candles) < lookback + 1:
            raise IndicatorWarmupError(f"{operand.name} requires {lookback + 1} candles")
        current = candles[-1]
        prior = candles[-lookback - 1 : -1]
        if operand.name == "bullish_liquidity_sweep":
            prior_low = min(candle.low for candle in prior)
            return current.low < prior_low and current.close > prior_low
        if operand.name == "bearish_liquidity_sweep":
            prior_high = max(candle.high for candle in prior)
            return current.high > prior_high and current.close < prior_high
        if operand.name == "previous_low_sweep":
            prior_low = min(candle.low for candle in prior)
            return current.low < prior_low and current.close > prior_low
        if operand.name == "previous_high_sweep":
            prior_high = max(candle.high for candle in prior)
            return current.high > prior_high and current.close < prior_high
        if operand.name == "higher_high":
            return current.high > max(candle.high for candle in prior)
        if operand.name == "higher_low":
            return current.low > min(candle.low for candle in prior)
        if operand.name == "lower_high":
            return current.high < max(candle.high for candle in prior)
        if operand.name == "lower_low":
            return current.low < min(candle.low for candle in prior)
        if operand.name in {
            "range_breakout",
            "break_of_structure_bullish",
            "change_of_character_bullish",
            "pivot_break",
        }:
            return current.close > max(candle.high for candle in prior)
        if operand.name in {
            "range_breakdown",
            "break_of_structure_bearish",
            "change_of_character_bearish",
        }:
            return current.close < min(candle.low for candle in prior)
        tolerance = float(operand.parameters.get("tolerance_percent", 0.2)) / 100
        if operand.name == "breakout_retest":
            resistance = max(candle.high for candle in prior)
            return current.low <= resistance * (1 + tolerance) and current.close > resistance
        if operand.name == "support_retest":
            support = min(candle.low for candle in prior)
            return current.low <= support * (1 + tolerance) and current.close > support
        if operand.name == "resistance_retest":
            resistance = max(candle.high for candle in prior)
            return current.high >= resistance * (1 - tolerance) and current.close < resistance
        if operand.name == "equal_highs":
            high = max(candle.high for candle in prior)
            touches = sum(1 for candle in prior if abs(candle.high - high) / high <= tolerance)
            return touches >= 2 and current.high >= high * (1 - tolerance)
        if operand.name == "equal_lows":
            low = min(candle.low for candle in prior)
            if low == 0:
                raise IndicatorWarmupError("equal_lows reference low is zero")
            touches = sum(1 for candle in prior if abs(candle.low - low) / low <= tolerance)
            return touches >= 2 and current.low <= low * (1 + tolerance)
        if operand.name == "consolidation_range":
            maximum_range_percent = float(operand.parameters.get("maximum_range_percent", 5))
            high = max(candle.high for candle in prior)
            low = min(candle.low for candle in prior)
            midpoint = (high + low) / 2
            if midpoint == 0:
                raise IndicatorWarmupError("consolidation midpoint is zero")
            return ((high - low) / midpoint) * 100 <= maximum_range_percent
        if operand.name == "impulse_candle":
            multiplier = float(operand.parameters.get("range_multiplier", 1.5))
            average_range = sum(candle.high - candle.low for candle in prior) / lookback
            if average_range <= 0:
                raise IndicatorWarmupError("average candle range is zero")
            candle_range = current.high - current.low
            close_position = (current.close - current.low) / candle_range if candle_range > 0 else 0
            direction = str(operand.parameters.get("direction", "long"))
            closes_well = close_position >= 0.7 if direction != "short" else close_position <= 0.3
            return candle_range >= average_range * multiplier and closes_well
        if operand.name == "ma_retest":
            average = str(operand.parameters.get("average", "ema"))
            period = int(operand.parameters.get("period", 20))
            value = ema(candles, period=period) if average == "ema" else sma(candles, period=period)
            direction = str(operand.parameters.get("direction", "long"))
            if direction == "short":
                return current.high >= value * (1 - tolerance) and current.close < value
            return current.low <= value * (1 + tolerance) and current.close > value
        if operand.name == "vwap_retest":
            period = int(operand.parameters.get("period", 20))
            value = vwap(candles, period=period)
            direction = str(operand.parameters.get("direction", "long"))
            if direction == "short":
                return current.high >= value * (1 - tolerance) and current.close < value
            return current.low <= value * (1 + tolerance) and current.close > value
        if operand.name == "bollinger_squeeze":
            period = int(operand.parameters.get("period", 20))
            threshold = float(operand.parameters.get("max_bandwidth_percent", 5))
            width_percent = bollinger_band(candles, period=period, component="width") * 100
            return width_percent <= threshold
        if operand.name == "bollinger_reentry":
            period = int(operand.parameters.get("period", 20))
            if len(candles) < period + 1:
                raise IndicatorWarmupError(f"bollinger_reentry requires {period + 1} candles")
            previous = candles[-2]
            previous_upper = bollinger_band(candles[:-1], period=period, component="upper")
            previous_lower = bollinger_band(candles[:-1], period=period, component="lower")
            current_upper = bollinger_band(candles, period=period, component="upper")
            current_lower = bollinger_band(candles, period=period, component="lower")
            previous_outside = previous.close > previous_upper or previous.close < previous_lower
            current_inside = current_lower <= current.close <= current_upper
            return previous_outside and current_inside
        if operand.name in {"percent_change_up", "percent_change_down"}:
            threshold = float(operand.parameters.get("threshold_percent", 1))
            reference = candles[-lookback - 1].close
            if reference == 0:
                raise IndicatorWarmupError("percent change reference close is zero")
            change = ((current.close - reference) / reference) * 100
            if operand.name == "percent_change_up":
                return change >= threshold
            return change <= -threshold
        if operand.name == "time_window":
            timezone = ZoneInfo(str(operand.parameters.get("timezone", "UTC")))
            timestamp = ensure_aware(current.timestamp).astimezone(timezone)
            start_hour = float(operand.parameters.get("start_hour", 0))
            end_hour = float(operand.parameters.get("end_hour", 24))
            hour_value = timestamp.hour + timestamp.minute / 60
            if start_hour <= end_hour:
                return start_hour <= hour_value <= end_hour
            return hour_value >= start_hour or hour_value <= end_hour
        if supports_price_action(operand.name):
            return evaluate_price_action(
                operand.name or "",
                candles,
                dict(operand.parameters),
            )
        raise KeyError(f"Unsupported price action: {operand.name}")

    @staticmethod
    def _candle_pattern(operand: Operand, candles: list[Candle]) -> bool:
        search_parameters = StrategyRuleEngine._search_parameters(operand)
        if search_parameters:
            candidate = operand.model_copy(
                update={
                    "parameters": {
                        key: value
                        for key, value in operand.parameters.items()
                        if key not in {"search_lookback", "search_start", "search_end"}
                    }
                }
            )
            return StrategyRuleEngine._any_search_match(
                candidate,
                candles,
                search_parameters,
                StrategyRuleEngine._candle_pattern,
            )
        offset = int(operand.parameters.get("offset", 0))
        if offset:
            if len(candles) <= offset:
                raise IndicatorWarmupError(f"{operand.name} requires at least {offset + 1} candles")
            operand = operand.model_copy(
                update={
                    "parameters": {
                        key: value for key, value in operand.parameters.items() if key != "offset"
                    }
                }
            )
            candles = candles[:-offset]
        if not candles:
            return False
        if operand.name in pattern_names():
            return detect_candle_pattern(
                operand.name or "",
                candles,
                dict(operand.parameters),
            )
        candle = candles[-1]
        body = abs(candle.close - candle.open)
        range_size = candle.high - candle.low
        if range_size <= 0:
            return False
        if operand.name == "bullish_engulfing":
            if len(candles) < 2:
                raise IndicatorWarmupError("bullish_engulfing requires 2 candles")
            previous = candles[-2]
            return (
                previous.close < previous.open
                and candle.close > candle.open
                and candle.close >= previous.open
                and candle.open <= previous.close
            )
        if operand.name == "bearish_engulfing":
            if len(candles) < 2:
                raise IndicatorWarmupError("bearish_engulfing requires 2 candles")
            previous = candles[-2]
            return (
                previous.close > previous.open
                and candle.close < candle.open
                and candle.open >= previous.close
                and candle.close <= previous.open
            )
        upper_wick = candle.high - max(candle.open, candle.close)
        lower_wick = min(candle.open, candle.close) - candle.low
        if operand.name == "green_candle":
            return candle.close > candle.open
        if operand.name == "red_candle":
            return candle.close < candle.open
        if operand.name == "candle_change_percent":
            if candle.open == 0:
                raise IndicatorWarmupError("candle change reference open is zero")
            change = ((candle.close - candle.open) / candle.open) * 100
            threshold = float(operand.parameters.get("threshold_percent", 1))
            direction = str(operand.parameters.get("direction", "absolute"))
            if direction == "up":
                return change >= threshold
            if direction == "down":
                return change <= -threshold
            return abs(change) >= threshold
        if operand.name in {"pin_bar", "hammer"}:
            return lower_wick >= body * 2 and body / range_size <= 0.35
        if operand.name == "shooting_star":
            return upper_wick >= body * 2 and body / range_size <= 0.35
        if operand.name == "doji":
            maximum_body_percent = float(operand.parameters.get("maximum_body_percent", 10))
            return (body / range_size) * 100 <= maximum_body_percent
        if operand.name in {"inside_bar", "outside_bar"}:
            if len(candles) < 2:
                raise IndicatorWarmupError(f"{operand.name} requires 2 candles")
            previous = candles[-2]
            if operand.name == "inside_bar":
                return candle.high < previous.high and candle.low > previous.low
            return candle.high > previous.high and candle.low < previous.low
        if operand.name == "strong_close_near_high":
            minimum_close_percent = float(operand.parameters.get("minimum_close_percent", 75))
            return ((candle.close - candle.low) / range_size) * 100 >= minimum_close_percent
        if operand.name == "strong_close_near_low":
            maximum_close_percent = float(operand.parameters.get("maximum_close_percent", 25))
            return ((candle.close - candle.low) / range_size) * 100 <= maximum_close_percent
        if operand.name == "range_expansion_candle":
            period = int(operand.parameters.get("period", 20))
            multiplier = float(operand.parameters.get("range_multiplier", 1.5))
            if len(candles) < period + 1:
                raise IndicatorWarmupError(f"range_expansion_candle requires {period + 1} candles")
            average_range = sum(c.high - c.low for c in candles[-period - 1 : -1]) / period
            if average_range <= 0:
                raise IndicatorWarmupError("average candle range is zero")
            return range_size >= average_range * multiplier
        raise KeyError(f"Unsupported candle pattern: {operand.name}")

    @staticmethod
    def _search_parameters(operand: Operand) -> dict[str, int | str]:
        return {
            key: value
            for key, value in operand.parameters.items()
            if key in {"search_lookback", "search_start", "search_end"}
        }

    @staticmethod
    def _search_end_indexes(
        candles: list[Candle],
        parameters: dict[str, int | str],
    ) -> range | list[int]:
        if not candles:
            return []
        start_value = parameters.get("search_start")
        end_value = parameters.get("search_end")
        if isinstance(start_value, str) or isinstance(end_value, str):
            start = ensure_aware(datetime.fromisoformat(str(start_value))) if start_value else None
            end = ensure_aware(datetime.fromisoformat(str(end_value))) if end_value else None
            return [
                index + 1
                for index, candle in enumerate(candles)
                if (start is None or ensure_aware(candle.timestamp) >= start)
                and (end is None or ensure_aware(candle.timestamp) < end)
            ]
        lookback = max(1, int(parameters.get("search_lookback", 1)))
        start_index = max(0, len(candles) - lookback)
        return range(start_index + 1, len(candles) + 1)

    @staticmethod
    def _search_candles(
        candles: list[Candle],
        parameters: dict[str, int | str],
    ) -> list[Candle]:
        indexes = list(StrategyRuleEngine._search_end_indexes(candles, parameters))
        return [candles[index - 1] for index in indexes if index > 0]

    @staticmethod
    def _any_search_match(
        operand: Operand,
        candles: list[Candle],
        parameters: dict[str, int | str],
        evaluator,
    ) -> bool:
        evaluated = False
        for end in StrategyRuleEngine._search_end_indexes(candles, parameters):
            try:
                evaluated = True
                if evaluator(operand, candles[:end]):
                    return True
            except IndicatorWarmupError:
                continue
        if not evaluated:
            raise IndicatorWarmupError(f"{operand.name} search window contained no candles")
        return False

    @staticmethod
    def _compare(
        comparator: Comparator,
        actual: Any,
        required: Any,
        *,
        previous_actual: Any = None,
        previous_required: Any = None,
    ) -> bool:
        if actual is None:
            return False
        if comparator == Comparator.IS_TRUE:
            return actual is True
        if comparator == Comparator.IS_FALSE:
            return actual is False
        if required is None:
            return False
        if comparator == Comparator.CROSSES_ABOVE:
            return (
                previous_actual is not None
                and previous_required is not None
                and previous_actual <= previous_required
                and actual > required
            )
        if comparator == Comparator.CROSSES_BELOW:
            return (
                previous_actual is not None
                and previous_required is not None
                and previous_actual >= previous_required
                and actual < required
            )
        return {
            Comparator.GREATER_THAN: lambda: actual > required,
            Comparator.GREATER_THAN_OR_EQUAL: lambda: actual >= required,
            Comparator.LESS_THAN: lambda: actual < required,
            Comparator.LESS_THAN_OR_EQUAL: lambda: actual <= required,
            Comparator.EQUAL: lambda: actual == required,
        }[comparator]()

    @staticmethod
    def _proximity(comparator: Comparator, actual: Any, required: Any, passed: bool) -> float:
        if passed:
            return 100.0
        if not isinstance(actual, int | float) or isinstance(actual, bool):
            return 0.0
        if not isinstance(required, int | float) or required == 0 or isinstance(required, bool):
            return 0.0
        actual_float = float(actual)
        required_float = float(required)
        if comparator in {Comparator.GREATER_THAN, Comparator.GREATER_THAN_OR_EQUAL}:
            return max(0, min(99, (actual_float / required_float) * 100))
        if comparator in {Comparator.LESS_THAN, Comparator.LESS_THAN_OR_EQUAL}:
            if actual_float <= 0:
                return 0
            return max(0, min(99, (required_float / actual_float) * 100))
        distance = abs(actual_float - required_float) / abs(required_float)
        return max(0, min(99, 100 * (1 - distance)))

    @staticmethod
    def _explain(
        condition: ConditionRule, actual: Any, required: Any, state: EvaluationState
    ) -> str:
        if condition.explanation_template:
            return condition.explanation_template.format(
                actual=actual, required=required, state=state.value
            )
        verb = "passed" if state == EvaluationState.PASSED else "failed"
        return f"{condition.label} {verb}: actual {actual}; required {required}."

    def _pending(
        self,
        condition: ConditionRule,
        evaluation_time: datetime,
        code: str,
        market_timestamp: datetime | None,
        latency: int | None,
    ) -> ConditionEvaluation:
        return self._state(
            condition, evaluation_time, EvaluationState.PENDING, code, market_timestamp, latency
        )

    def _unavailable(
        self,
        condition: ConditionRule,
        evaluation_time: datetime,
        code: str,
        market_timestamp: datetime | None,
        latency: int | None,
    ) -> ConditionEvaluation:
        return self._state(
            condition, evaluation_time, EvaluationState.UNAVAILABLE, code, market_timestamp, latency
        )

    def _error(
        self,
        condition: ConditionRule,
        evaluation_time: datetime,
        code: str,
        market_timestamp: datetime | None,
        latency: int | None,
    ) -> ConditionEvaluation:
        return self._state(
            condition, evaluation_time, EvaluationState.ERROR, code, market_timestamp, latency
        )

    @staticmethod
    def _state(
        condition: ConditionRule,
        evaluation_time: datetime,
        state: EvaluationState,
        code: str,
        market_timestamp: datetime | None,
        latency: int | None,
    ) -> ConditionEvaluation:
        return ConditionEvaluation(
            condition_id=condition.key,
            name=condition.label,
            condition_type=condition.condition_type.value,
            operator=condition.comparator.value,
            timeframe=condition.timeframe,
            required_value=condition.right.model_dump(mode="json") if condition.right else None,
            actual_value=None,
            state=state,
            weight=condition.weight,
            mandatory=condition.required,
            required_data=condition.required_data,
            evaluation_time=evaluation_time,
            market_data_timestamp=market_timestamp,
            data_latency_ms=latency,
            explanation=f"{condition.label}: {code}.",
            proximity_score=0,
            cap_score_on_fail=condition.cap_score_on_fail,
            error_code=code,
        )

    def _evaluate_risk(
        self,
        strategy: StrategyDefinition,
        candle_sets: dict[str, list[Candle]],
        evaluation_time: datetime,
        account_balance: float | None,
    ) -> tuple[RiskCalculation | None, ConditionEvaluation]:
        candles = candle_sets.get(strategy.base_timeframe, [])
        market_timestamp = candles[-1].timestamp if candles else None
        latency = (
            int((evaluation_time - ensure_aware(market_timestamp)).total_seconds() * 1000)
            if market_timestamp
            else None
        )
        required = {
            "maximum_stop_percent": strategy.risk.maximum_stop_percent,
            "minimum_reward_to_risk": strategy.risk.minimum_reward_to_risk,
            "direction": strategy.direction.value,
        }
        try:
            calculation = self.risk.calculate(
                strategy,
                candles,
                account_balance=account_balance,
                enforce_limits=False,
            )
            failed_checks: list[tuple[str, str]] = []
            if (
                strategy.risk.maximum_stop_percent is not None
                and calculation.stop_distance_percent > strategy.risk.maximum_stop_percent
            ):
                failed_checks.append(
                    (
                        "stop_distance_within_maximum",
                        "stop_distance_exceeded",
                    )
                )
            if (
                strategy.risk.minimum_reward_to_risk is not None
                and calculation.reward_to_risk < strategy.risk.minimum_reward_to_risk
            ):
                failed_checks.append(
                    (
                        "reward_to_risk_within_minimum",
                        "reward_to_risk_below_minimum",
                    )
                )
            checks = {
                "stop_calculable": "passed",
                "stop_direction_valid": "passed",
                "stop_distance_within_maximum": "passed",
                "reward_to_risk_within_minimum": "passed",
                "target_direction_valid": "passed",
                "position_sizing_calculable": (
                    "passed"
                    if calculation.position_size is not None
                    else (
                        "pending_balance"
                        if strategy.position_sizing.enabled
                        else "not_requested"
                    )
                ),
            }
            for check, _code in failed_checks:
                checks[check] = "failed"
            validation_state = (
                EvaluationState.FAILED if failed_checks else EvaluationState.PASSED
            )
            validation = ConditionEvaluation(
                condition_id="risk_policy_validation",
                name="Risk policy validation",
                condition_type="risk",
                operator="valid",
                timeframe=strategy.base_timeframe,
                required_value=required,
                actual_value={
                    "stop_distance_percent": calculation.stop_distance_percent,
                    "reward_to_risk": calculation.reward_to_risk,
                    "stop_price": calculation.stop_price,
                    "targets": calculation.targets,
                    "checks": checks,
                },
                state=validation_state,
                weight=1,
                mandatory=True,
                required_data=["entry_price", "stop_price", "target_prices"],
                evaluation_time=evaluation_time,
                market_data_timestamp=market_timestamp,
                data_latency_ms=latency,
                explanation=(
                    "Risk geometry and configured limits passed."
                    if not failed_checks
                    else "Risk geometry was calculated, but one or more configured limits failed."
                ),
                proximity_score=100 if not failed_checks else 0,
                error_code=failed_checks[0][1] if failed_checks else None,
            )
            return calculation, validation
        except RiskCalculationError as exc:
            message = str(exc)
            code = message.split(":", 1)[0].strip().lower().replace(" ", "_")
            unavailable = "requires" in message or "without candles" in message
            checks = {
                "stop_calculable": "unknown",
                "stop_direction_valid": "unknown",
                "stop_distance_within_maximum": "unknown",
                "reward_to_risk_within_minimum": "unknown",
                "target_direction_valid": "unknown",
                "position_sizing_calculable": "unknown",
            }
            failed_check = {
                "stop_direction_invalid": "stop_direction_valid",
                "stop_distance_exceeded": "stop_distance_within_maximum",
                "reward_to_risk_below_minimum": "reward_to_risk_within_minimum",
                "target_direction_invalid": "target_direction_valid",
            }.get(code, "stop_calculable")
            checks[failed_check] = "failed"
            validation = ConditionEvaluation(
                condition_id="risk_policy_validation",
                name="Risk policy validation",
                condition_type="risk",
                operator="valid",
                timeframe=strategy.base_timeframe,
                required_value=required,
                actual_value={"error": message, "checks": checks},
                state=(EvaluationState.UNAVAILABLE if unavailable else EvaluationState.FAILED),
                weight=1,
                mandatory=True,
                required_data=["entry_price", "stop_price", "target_prices"],
                evaluation_time=evaluation_time,
                market_data_timestamp=market_timestamp,
                data_latency_ms=latency,
                explanation=f"Risk validation failed: {message}.",
                proximity_score=0,
                error_code=code,
            )
            return None, validation

    def _risk_context(
        self,
        strategy: StrategyDefinition,
        market: MarketSnapshot,
        candle_sets: dict[str, list[Candle]],
        evaluation_time: datetime,
        calculation: RiskCalculation | None,
        validation: ConditionEvaluation | None,
        condition_context: dict[str, Any],
    ) -> dict[str, Any]:
        candles = candle_sets.get(strategy.base_timeframe, [])
        if not candles:
            return {}
        current = candles[-1]
        values: dict[str, Any] = {
            **dict(condition_context.get("risk_context", {})),
            "maximum_data_latency": max(
                0,
                int(
                    (
                        evaluation_time - ensure_aware(current.timestamp)
                    ).total_seconds()
                    * 1000
                ),
            ),
            "minimum_candle_liquidity": (
                current.quote_volume
                if current.quote_volume is not None
                else current.close * current.volume
            ),
            "spread_too_wide_at_alert": market.spread_bps,
            "risk_context_incomplete": calculation is None,
            "invalidation_not_calculable": calculation is None,
        }
        try:
            atr_value = float(
                self.indicators.calculate(
                    "atr",
                    candles,
                    period=min(14, max(2, len(candles) - 1)),
                )
            )
        except (IndicatorWarmupError, ValueError, TypeError):
            atr_value = 0
        if atr_value > 0:
            values["volatility_atr_percent"] = atr_value / current.close * 100
        if calculation is None:
            return values
        stop_distance = abs(calculation.entry_price - calculation.stop_price)
        values.update(
            {
                "stop_distance_atr_units": (
                    stop_distance / atr_value if atr_value > 0 else None
                ),
                "stop_distance_too_tight": calculation.stop_distance_percent
                < float(condition_context.get("minimum_stop_percent", 0.1)),
                "stop_distance_too_wide": (
                    strategy.risk.maximum_stop_percent is not None
                    and calculation.stop_distance_percent
                    > strategy.risk.maximum_stop_percent
                ),
                "reward_to_risk_after_fees": _net_reward_to_risk(
                    calculation,
                    calculation.estimated_fee_bps,
                ),
                "reward_to_risk_after_slippage": _net_reward_to_risk(
                    calculation,
                    calculation.estimated_fee_bps
                    + calculation.estimated_slippage_bps,
                ),
                "fibonacci_extension_targets": _target_matches_fibonacci_extension(
                    candles,
                    calculation,
                ),
            }
        )
        lookback = candles[-min(100, len(candles)) :]
        if strategy.direction == StrategyDirection.SHORT:
            obstacles = sorted(
                {candle.low for candle in lookback if candle.low < calculation.entry_price},
                reverse=True,
            )
        else:
            obstacles = sorted(
                {candle.high for candle in lookback if candle.high > calculation.entry_price}
            )
        obstacle = obstacles[0] if obstacles else None
        if obstacle is not None and stop_distance > 0:
            distance = abs(obstacle - calculation.entry_price)
            values.update(
                {
                    "target_distance_next_resistance": (
                        distance
                        if strategy.direction != StrategyDirection.SHORT
                        else None
                    ),
                    "target_distance_next_support": (
                        distance
                        if strategy.direction == StrategyDirection.SHORT
                        else None
                    ),
                    "r_multiple_before_obstacle": distance / stop_distance,
                    "liquidity_obstacle_before_target": bool(
                        calculation.targets
                        and (
                            obstacle < float(calculation.targets[0]["price"])
                            if strategy.direction != StrategyDirection.SHORT
                            else obstacle > float(calculation.targets[0]["price"])
                        )
                    ),
                    "minimum_clean_path_to_target": distance / stop_distance,
                    "target_overlaps_obstacle": bool(
                        calculation.targets
                        and abs(float(calculation.targets[0]["price"]) - obstacle)
                        <= max(atr_value, stop_distance * 0.1)
                    ),
                }
            )
        trigger_price = condition_context.get("trigger_price", calculation.entry_price)
        values["price_moved_too_far_from_trigger"] = (
            abs(current.close - float(trigger_price)) / float(trigger_price) * 100
            if float(trigger_price)
            else None
        )
        candle_range = current.high - current.low
        values["candle_overextended"] = (
            candle_range / atr_value if atr_value > 0 else None
        )
        setup_first = condition_context.get("setup_first_detected_at")
        if setup_first is not None:
            parsed = (
                ensure_aware(datetime.fromisoformat(setup_first))
                if isinstance(setup_first, str)
                else ensure_aware(setup_first)
            )
            values["setup_age_too_old"] = (
                evaluation_time - parsed
            ).total_seconds() / 60
        values["maximum_alert_lateness"] = values["maximum_data_latency"]
        values["volatility_too_high"] = values.get("volatility_atr_percent")
        values["volatility_too_low"] = values.get("volatility_atr_percent")
        if validation is not None:
            values["_risk_validation_state"] = validation.state.value
        return {key: value for key, value in values.items() if value is not None}

    @staticmethod
    def _history(
        candles: list[Candle], evaluation_time: datetime, trigger_mode: str
    ) -> list[Candle]:
        history = [
            candle for candle in candles if ensure_aware(candle.timestamp) <= evaluation_time
        ]
        if trigger_mode == "candle_close":
            history = [candle for candle in history if candle.is_closed]
        return history

    @staticmethod
    def _latest_timestamp(candle_sets: dict[str, list[Candle]]) -> datetime | None:
        timestamps = [candles[-1].timestamp for candles in candle_sets.values() if candles]
        return max(timestamps) if timestamps else None

    @staticmethod
    def _base_candle_closed(
        strategy: StrategyDefinition, candle_sets: dict[str, list[Candle]]
    ) -> bool | None:
        candles = candle_sets.get(strategy.base_timeframe, [])
        return candles[-1].is_closed if candles else None

    @staticmethod
    def _latency_ms(evaluation_time: datetime, candle_sets: dict[str, list[Candle]]) -> int | None:
        latest = StrategyRuleEngine._latest_timestamp(candle_sets)
        if latest is None:
            return None
        return int((evaluation_time - ensure_aware(latest)).total_seconds() * 1000)


def _net_reward_to_risk(
    calculation: RiskCalculation,
    total_cost_bps: float,
) -> float:
    risk = abs(calculation.entry_price - calculation.stop_price)
    if risk <= 0 or not calculation.targets:
        return 0
    gross_reward = abs(float(calculation.targets[0]["price"]) - calculation.entry_price)
    estimated_cost = calculation.entry_price * total_cost_bps / 10_000
    return max(0, gross_reward - estimated_cost) / (risk + estimated_cost)


def _target_matches_fibonacci_extension(
    candles: list[Candle],
    calculation: RiskCalculation,
) -> bool:
    if len(candles) < 10 or not calculation.targets:
        return False
    recent = candles[-min(50, len(candles)) :]
    swing = max(candle.high for candle in recent) - min(candle.low for candle in recent)
    if swing <= 0:
        return False
    target_distance = abs(
        float(calculation.targets[0]["price"]) - calculation.entry_price
    )
    ratio = target_distance / swing
    return any(
        abs(ratio - extension) <= 0.08
        for extension in (0.618, 1.0, 1.272, 1.618, 2.0)
    )
