from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import Integer, and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    CandidateReadinessSnapshot,
    ConditionObservabilityAggregate,
    DiscordConnection,
    MarketDataHealth,
    MonitorEvaluationCycle,
    MonitorHealthSummary,
    ObservabilityExplanation,
    ScanJob,
    ScanResult,
    SetupConditionResult,
    SetupInstance,
    SetupLifecycleEvent,
    Strategy,
    StrategyCondition,
    StrategyVersion,
    TelegramConnection,
    WhatsAppConnection,
)
from ai_market_monitor.db.models.enums import (
    ConditionOutcome,
    ConnectionStatus,
    DeliveryStatus,
    ScanJobStatus,
    ScanOutcome,
    SetupLifecycleState,
)
from ai_market_monitor.engine.models import EvaluationResult
from ai_market_monitor.schemas.strategy import ConditionGroup, ConditionRule, StrategyDefinition
from ai_market_monitor.services.market_preview import timeframe_duration
from ai_market_monitor.strategy_cockpit import validate_strategy_conflicts

SUCCESSFUL_DELIVERY = {DeliveryStatus.SENT, DeliveryStatus.DELIVERED}
FAILED_DELIVERY = {
    DeliveryStatus.FAILED,
    DeliveryStatus.FAILED_RETRYABLE,
    DeliveryStatus.FAILED_PERMANENT,
}
TERMINAL_RADAR_STATES = {"confirmed", "invalidated", "expired", "provider_data_error"}
STAGE_RANK = {
    "not_started": 0,
    "forming": 1,
    "near_miss": 2,
    "confirmation_pending": 3,
    "confirmed": 4,
    "invalidated": -1,
    "expired": -2,
    "provider_data_error": -3,
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, dict):
        value = value.get("value")
    try:
        result = float(value)
    except (TypeError, ValueError, InvalidOperation):
        return None
    return result if math.isfinite(result) else None


def _rule_roles(definition: StrategyDefinition) -> dict[str, str]:
    rules: list[ConditionRule] = []

    def walk(node: ConditionRule | ConditionGroup) -> None:
        if isinstance(node, ConditionRule):
            rules.append(node)
            return
        for child in node.children:
            walk(child)

    walk(definition.conditions)
    roles: dict[str, str] = {}
    primary_seen = False
    for rule in rules:
        if not rule.required:
            roles[rule.key] = "optional_suggestion"
        elif not primary_seen:
            roles[rule.key] = "primary_trigger"
            primary_seen = True
        elif rule.condition_type.value in {"indicator", "candle_pattern"}:
            roles[rule.key] = "required_confirmation"
        else:
            roles[rule.key] = "required_filter"
    return roles


def _candidate_state(result: EvaluationResult) -> str:
    if result.outcome == ScanOutcome.ERROR or any(
        item.mandatory and item.state.value in {"unavailable", "error"}
        for item in result.conditions
    ):
        return "provider_data_error"
    if result.outcome == ScanOutcome.CONFIRMED:
        return "confirmed"
    if result.outcome == ScanOutcome.EXPIRED:
        return "expired"
    if result.outcome == ScanOutcome.INVALID:
        return "invalidated"
    missing_required = [item for item in result.conditions if item.mandatory and not item.passed]
    passed_required = [item for item in result.conditions if item.mandatory and item.passed]
    if len(missing_required) == 1 and passed_required:
        return "confirmation_pending"
    if result.outcome == ScanOutcome.NEAR_MISS:
        return "near_miss"
    if passed_required:
        return "forming"
    return "not_started"


def _best_blocker(result: EvaluationResult):
    blockers = [item for item in result.conditions if item.mandatory and not item.passed]
    if not blockers:
        return None
    severity = {"error": 0, "unavailable": 1, "failed": 2, "pending": 3}
    return sorted(
        blockers,
        key=lambda item: (
            severity.get(item.state.value, 4),
            -float(item.proximity_score or 0),
            item.condition_id,
        ),
    )[0]


def _numeric_distance(actual: Any, required: Any, operator: str) -> tuple[float | None, str | None]:
    actual_number = _number(actual)
    required_number = _number(required)
    if actual_number is None or required_number is None:
        return None, None
    if operator in {"gt", "gte", "crosses_above"}:
        return max(0.0, required_number - actual_number), "absolute"
    if operator in {"lt", "lte", "crosses_below"}:
        return max(0.0, actual_number - required_number), "absolute"
    if operator == "eq":
        return abs(actual_number - required_number), "absolute"
    return None, None


class SetupObservabilityService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def sync_cycle(
        self,
        *,
        job: ScanJob,
        strategy: Strategy,
        version: StrategyVersion,
        provider: str,
    ) -> MonitorEvaluationCycle:
        cycle = await self.session.scalar(
            select(MonitorEvaluationCycle).where(MonitorEvaluationCycle.scan_job_id == job.id)
        )
        failures = list((job.metrics or {}).get("failures") or [])
        market_health = list(
            (
                await self.session.scalars(
                    select(MarketDataHealth).where(
                        MarketDataHealth.exchange == provider,
                        MarketDataHealth.checked_at >= (job.started_at or job.scheduled_for),
                    )
                )
            ).all()
        )
        stale_candles = sum(
            1
            for item in market_health
            if item.status.value in {"degraded", "down"}
            or (item.data_age_seconds or 0) > self.settings.observability_candidate_stale_seconds
        )
        missing_candles = sum(item.missing_candle_count for item in market_health)
        provider_errors = sum(1 for item in failures if item.get("retryable"))
        rate_limits = sum(
            1
            for item in failures
            if "rate" in str(item.get("detail") or "").casefold()
            or "429" in str(item.get("detail") or "")
        )
        interval = await self._scan_interval(version.id)
        payload = {
            "status": job.status.value,
            "worker_id": job.worker_id,
            "provider": provider,
            "started_at": job.started_at or job.scheduled_for,
            "heartbeat_at": job.heartbeat_at,
            "completed_at": job.completed_at,
            "next_expected_at": (job.completed_at or job.scheduled_for)
            + timedelta(seconds=interval),
            "symbols_expected": job.symbols_planned,
            "symbols_scanned": job.symbols_scanned,
            "provider_errors": provider_errors,
            "rate_limit_incidents": rate_limits,
            "scanner_failures": len(failures),
            "stale_candles": stale_candles,
            "missing_candles": missing_candles,
            "delayed_evaluations": int(
                bool(
                    job.started_at
                    and job.started_at > job.scheduled_for + timedelta(seconds=interval)
                )
            ),
            "metrics": {
                "matches_found": job.matches_found,
                "attempt_count": job.attempt_count,
                "error_code": job.error_code,
                "failures": failures[:50],
            },
        }
        if cycle is None:
            cycle = MonitorEvaluationCycle(
                user_id=strategy.user_id,
                strategy_id=strategy.id,
                strategy_version_id=version.id,
                scan_job_id=job.id,
                **payload,
            )
            self.session.add(cycle)
        else:
            for key, value in payload.items():
                setattr(cycle, key, value)
        await self.session.flush()
        return cycle

    async def record_candidate(
        self,
        *,
        strategy: Strategy,
        version: StrategyVersion,
        definition: StrategyDefinition,
        scan_result: ScanResult,
        setup: SetupInstance | None,
        result: EvaluationResult,
    ) -> CandidateReadinessSnapshot:
        snapshot = await self.session.scalar(
            select(CandidateReadinessSnapshot).where(
                CandidateReadinessSnapshot.strategy_version_id == version.id,
                CandidateReadinessSnapshot.exchange == result.exchange,
                CandidateReadinessSnapshot.symbol == result.symbol,
                CandidateReadinessSnapshot.timeframe == result.timeframe,
                CandidateReadinessSnapshot.direction == result.direction,
            )
        )
        previous_state = snapshot.lifecycle_state if snapshot else None
        previous_blocker = snapshot.blocker_key if snapshot else None
        state = _candidate_state(result)
        blocker = _best_blocker(result)
        required = [item for item in result.conditions if item.mandatory]
        optional = [item for item in result.conditions if not item.mandatory]
        distance, unit = (
            _numeric_distance(blocker.actual_value, blocker.required_value, blocker.operator)
            if blocker
            else (None, None)
        )
        changed = previous_state != state or previous_blocker != (
            blocker.condition_id if blocker else None
        )
        if previous_state != state:
            change = f"State changed from {previous_state or 'new'} to {state}."
        elif previous_blocker != (blocker.condition_id if blocker else None):
            change = f"Current blocker changed to {blocker.name if blocker else 'none'}."
        else:
            change = "Latest approved rules evaluated."
        next_close = (result.market_data_timestamp or result.evaluation_time) + timeframe_duration(
            result.timeframe
        )
        values = [
            {
                "key": item.condition_id,
                "label": item.name,
                "role": _rule_roles(definition).get(item.condition_id, "optional_suggestion"),
                "required": item.mandatory,
                "outcome": item.state.value,
                "actual": item.actual_value,
                "required_value": item.required_value,
                "operator": item.operator,
                "timeframe": item.timeframe,
                "explanation": item.explanation,
                "evaluated_at": item.evaluation_time.isoformat(),
            }
            for item in result.conditions
        ]
        payload = {
            "user_id": strategy.user_id,
            "strategy_id": strategy.id,
            "strategy_version_id": version.id,
            "setup_instance_id": setup.id if setup else None,
            "scan_result_id": scan_result.id,
            "exchange": result.exchange,
            "symbol": result.symbol,
            "timeframe": result.timeframe,
            "direction": result.direction,
            "lifecycle_state": state,
            "stage_rank": STAGE_RANK[state],
            "required_total": len(required),
            "required_passed": sum(item.passed for item in required),
            "optional_total": len(optional),
            "optional_passed": sum(item.passed for item in optional),
            "blocker_key": blocker.condition_id if blocker else None,
            "blocker_label": blocker.name if blocker else None,
            "blocker_outcome": blocker.state.value if blocker else None,
            "blocker_actual": {"value": blocker.actual_value} if blocker else {},
            "blocker_required": {"value": blocker.required_value} if blocker else {},
            "blocker_distance": Decimal(str(distance)) if distance is not None else None,
            "blocker_unit": unit,
            "most_recent_change": change,
            "last_changed_at": result.evaluation_time
            if changed or snapshot is None
            else snapshot.last_changed_at,
            "last_evaluated_at": result.evaluation_time,
            "data_freshness_ms": max(0, result.data_latency_ms or 0),
            "data_health": (
                "error"
                if state == "provider_data_error"
                else "stale"
                if (result.data_latency_ms or 0)
                > self.settings.observability_candidate_stale_seconds * 1000
                else "healthy"
            ),
            "next_candle_close_at": next_close,
            "expires_at": setup.expires_at if setup else None,
            "condition_tree": result.condition_tree.to_proof_dict()
            if result.condition_tree
            else {},
            "latest_values": values,
        }
        if snapshot is None:
            snapshot = CandidateReadinessSnapshot(notification_status="not_attempted", **payload)
            self.session.add(snapshot)
        else:
            for key, value in payload.items():
                setattr(snapshot, key, value)
        await self.session.flush()
        return snapshot

    async def radar(
        self,
        user_id: UUID,
        *,
        monitor_id: UUID | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        state: str | None = None,
        blocker: str | None = None,
        data_health: str | None = None,
        sort: str = "readiness",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        filters = [CandidateReadinessSnapshot.user_id == user_id]
        if monitor_id:
            filters.append(CandidateReadinessSnapshot.strategy_id == monitor_id)
        if symbol:
            filters.append(CandidateReadinessSnapshot.symbol == symbol.upper())
        if timeframe:
            filters.append(CandidateReadinessSnapshot.timeframe == timeframe)
        if state:
            filters.append(CandidateReadinessSnapshot.lifecycle_state == state)
        if blocker:
            filters.append(CandidateReadinessSnapshot.blocker_key == blocker)
        if data_health:
            filters.append(CandidateReadinessSnapshot.data_health == data_health)
        total = int(
            await self.session.scalar(
                select(func.count(CandidateReadinessSnapshot.id)).where(*filters)
            )
            or 0
        )
        ordering = {
            "newest_change": (CandidateReadinessSnapshot.last_changed_at.desc(),),
            "monitor": (Strategy.name.asc(), CandidateReadinessSnapshot.stage_rank.desc()),
            "symbol": (CandidateReadinessSnapshot.symbol.asc(),),
            "timeframe": (CandidateReadinessSnapshot.timeframe.asc(),),
            "lifecycle_state": (CandidateReadinessSnapshot.lifecycle_state.asc(),),
            "blocker": (CandidateReadinessSnapshot.blocker_label.asc(),),
            "data_health": (CandidateReadinessSnapshot.data_health.asc(),),
        }.get(
            sort,
            (
                CandidateReadinessSnapshot.stage_rank.desc(),
                CandidateReadinessSnapshot.required_passed.desc(),
                CandidateReadinessSnapshot.last_changed_at.desc(),
            ),
        )
        rows = (
            await self.session.execute(
                select(CandidateReadinessSnapshot, Strategy.name, StrategyVersion.version_number)
                .join(Strategy, Strategy.id == CandidateReadinessSnapshot.strategy_id)
                .join(
                    StrategyVersion,
                    StrategyVersion.id == CandidateReadinessSnapshot.strategy_version_id,
                )
                .where(*filters)
                .order_by(*ordering)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return {
            "items": [self._candidate_payload(item, name, version) for item, name, version in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, math.ceil(total / page_size)),
            "updated_at": max((item.updated_at for item, _, _ in rows), default=None),
        }

    async def aggregate_version(
        self,
        strategy: Strategy,
        version: StrategyVersion,
        *,
        now: datetime | None = None,
    ) -> list[ConditionObservabilityAggregate]:
        now = now or utcnow()
        window_start = now - timedelta(days=self.settings.observability_aggregate_window_days)
        condition_rows = (
            await self.session.scalars(
                select(StrategyCondition).where(
                    StrategyCondition.strategy_version_id == version.id,
                    StrategyCondition.node_type == "condition",
                )
            )
        ).all()
        result_rows = (
            await self.session.execute(
                select(SetupConditionResult, ScanResult, SetupInstance.state)
                .join(ScanResult, ScanResult.id == SetupConditionResult.scan_result_id)
                .join(SetupInstance, SetupInstance.id == SetupConditionResult.setup_instance_id)
                .where(
                    ScanResult.strategy_version_id == version.id,
                    SetupConditionResult.evaluated_at >= window_start,
                    SetupConditionResult.evaluated_at <= now,
                )
            )
        ).all()
        definition = StrategyDefinition.model_validate(version.schema_json)
        roles = _rule_roles(definition)
        definitions = {row.condition_key: row for row in condition_rows}
        await self.session.execute(
            delete(ConditionObservabilityAggregate).where(
                ConditionObservabilityAggregate.strategy_version_id == version.id
            )
        )
        grouped: dict[str, list[tuple[SetupConditionResult, ScanResult, SetupLifecycleState]]] = (
            defaultdict(list)
        )
        by_scan: dict[UUID, list[SetupConditionResult]] = defaultdict(list)
        for result, scan, setup_state in result_rows:
            grouped[result.condition_key].append((result, scan, setup_state))
            by_scan[result.scan_result_id].append(result)
        final_blockers: Counter[str] = Counter()
        co_occurrence: dict[str, Counter[str]] = defaultdict(Counter)
        for scan_results in by_scan.values():
            failed_required = [
                item
                for item in scan_results
                if definitions.get(item.condition_key)
                and definitions[item.condition_key].is_required
                and item.outcome != ConditionOutcome.PASSED
            ]
            if len(failed_required) == 1:
                final_blockers[failed_required[0].condition_key] += 1
            failed_keys = sorted(item.condition_key for item in failed_required)
            for key in failed_keys:
                for other in failed_keys:
                    if other != key:
                        co_occurrence[key][other] += 1
        previous = await self._previous_version_aggregates(strategy.id, version.version_number)
        aggregates: list[ConditionObservabilityAggregate] = []
        for key, definition_row in definitions.items():
            values = grouped.get(key, [])
            outcomes = Counter(item.outcome.value for item, _, _ in values)
            actuals = [_number(item.actual_value) for item, _, _ in values]
            requireds = [_number(item.required_value) for item, _, _ in values]
            distances = [_number(item.distance_to_pass) for item, _, _ in values]
            blocked_actuals = [
                _number(item.actual_value)
                for item, _, _ in values
                if item.outcome != ConditionOutcome.PASSED
            ]
            numeric_actuals = [value for value in actuals if value is not None]
            numeric_required = [value for value in requireds if value is not None]
            numeric_distances = [value for value in distances if value is not None]
            numeric_blocked = [value for value in blocked_actuals if value is not None]
            evaluation_count = len(values)
            prior = previous.get(key)
            pass_rate = outcomes["passed"] / evaluation_count * 100 if evaluation_count else 0
            prior_rate = (
                float(prior.pass_count / prior.evaluation_count * 100)
                if prior and prior.evaluation_count
                else None
            )
            counterfactual: dict[str, Any] = {}
            if (
                evaluation_count >= self.settings.observability_minimum_sample_size
                and numeric_blocked
                and numeric_required
                and definition_row.comparator in {"gt", "gte", "lt", "lte"}
            ):
                proposed = statistics.median(numeric_blocked)
                if definition_row.comparator in {"gt", "gte"}:
                    additional = sum(value >= proposed for value in numeric_blocked)
                else:
                    additional = sum(value <= proposed for value in numeric_blocked)
                counterfactual = {
                    "preview_only": True,
                    "current_threshold": statistics.fmean(numeric_required),
                    "proposed_threshold": proposed,
                    "additional_historical_completions": additional,
                    "sample_count": evaluation_count,
                }
            row = ConditionObservabilityAggregate(
                user_id=strategy.user_id,
                strategy_id=strategy.id,
                strategy_version_id=version.id,
                strategy_condition_id=definition_row.id,
                condition_key=key,
                condition_label=definition_row.label,
                rule_role=roles.get(key, "optional_suggestion"),
                is_required=definition_row.is_required,
                timeframe=definition_row.timeframe,
                evaluation_count=evaluation_count,
                pass_count=outcomes["passed"],
                fail_count=outcomes["failed"],
                pending_count=outcomes["pending"],
                unavailable_count=outcomes["unavailable"],
                error_count=outcomes["error"],
                final_blocker_count=final_blockers[key],
                near_miss_blocker_count=sum(
                    1
                    for item, scan, _ in values
                    if scan.outcome == ScanOutcome.NEAR_MISS
                    and item.outcome != ConditionOutcome.PASSED
                ),
                invalidation_count=sum(
                    1
                    for item, _, setup_state in values
                    if setup_state == SetupLifecycleState.INVALIDATED
                    and item.outcome != ConditionOutcome.PASSED
                ),
                average_actual=Decimal(str(statistics.fmean(numeric_actuals)))
                if numeric_actuals
                else None,
                median_actual_when_blocked=Decimal(str(statistics.median(numeric_blocked)))
                if numeric_blocked
                else None,
                average_required=Decimal(str(statistics.fmean(numeric_required)))
                if numeric_required
                else None,
                average_distance=Decimal(str(statistics.fmean(numeric_distances)))
                if numeric_distances
                else None,
                co_occurrence=dict(co_occurrence[key].most_common(10)),
                previous_version_delta={
                    "pass_rate_points": round(pass_rate - prior_rate, 3)
                    if prior_rate is not None
                    else None,
                    "previous_version": version.version_number - 1 if prior else None,
                },
                counterfactual_preview=counterfactual,
                sample_status=(
                    "sufficient"
                    if evaluation_count >= self.settings.observability_minimum_sample_size
                    else "low_sample"
                ),
                window_started_at=window_start,
                window_ended_at=now,
                calculated_at=now,
            )
            self.session.add(row)
            aggregates.append(row)
        await self.session.flush()
        await self.refresh_health(strategy, version, aggregates=aggregates, now=now)
        return aggregates

    async def refresh_health(
        self,
        strategy: Strategy,
        version: StrategyVersion,
        *,
        aggregates: list[ConditionObservabilityAggregate] | None = None,
        now: datetime | None = None,
    ) -> MonitorHealthSummary:
        now = now or utcnow()
        aggregates = aggregates or await self._latest_aggregates(version.id)
        cycle = await self.session.scalar(
            select(MonitorEvaluationCycle)
            .where(MonitorEvaluationCycle.strategy_version_id == version.id)
            .order_by(MonitorEvaluationCycle.started_at.desc())
            .limit(1)
        )
        interval = await self._scan_interval(version.id)
        technical_causes: list[dict[str, Any]] = []
        technical = "healthy"
        if cycle is None:
            technical = "offline"
            technical_causes.append(
                {
                    "code": "no_worker_cycle",
                    "message": "No worker evaluation cycle has been recorded yet.",
                }
            )
        else:
            last_seen = cycle.heartbeat_at or cycle.completed_at or cycle.started_at
            if last_seen < now - timedelta(
                seconds=max(interval * 3, self.settings.observability_candidate_stale_seconds)
            ):
                technical = "offline"
                technical_causes.append(
                    {
                        "code": "worker_heartbeat_stale",
                        "message": "The latest worker heartbeat is overdue.",
                    }
                )
            coverage = (
                cycle.symbols_scanned / cycle.symbols_expected if cycle.symbols_expected else 0
            )
            if cycle.status in {ScanJobStatus.FAILED.value, ScanJobStatus.CANCELED.value}:
                technical = "offline"
                technical_causes.append(
                    {
                        "code": "latest_cycle_failed",
                        "message": "The latest scan cycle did not complete.",
                    }
                )
            elif coverage < 0.95 or cycle.provider_errors or cycle.delayed_evaluations:
                technical = "degraded"
                technical_causes.append(
                    {
                        "code": "cycle_incomplete",
                        "message": (
                            f"{cycle.symbols_scanned}/{cycle.symbols_expected} symbols "
                            "were evaluated in the latest cycle."
                        ),
                    }
                )
            if cycle.provider_errors:
                technical_causes.append(
                    {
                        "code": "provider_errors",
                        "message": (
                            f"{cycle.provider_errors} provider errors occurred in the "
                            "latest cycle."
                        ),
                    }
                )
            if cycle.stale_candles or cycle.missing_candles:
                technical = "degraded" if technical == "healthy" else technical
                technical_causes.append(
                    {
                        "code": "candle_quality",
                        "message": (
                            f"{cycle.stale_candles} stale data checks and "
                            f"{cycle.missing_candles} missing candles were recorded."
                        ),
                    }
                )
        unsupported = len(version.unsupported_conditions or [])
        definition = StrategyDefinition.model_validate(version.schema_json)
        contradictions = [
            item for item in validate_strategy_conflicts(definition) if item.severity == "critical"
        ]
        channels = await self._notification_channels(strategy.user_id)
        if unsupported:
            technical = "misconfigured"
            technical_causes.append(
                {
                    "code": "unsupported_rules",
                    "message": (
                        f"{unsupported} unsupported rule"
                        f"{'s' if unsupported != 1 else ''} block reliable monitoring."
                    ),
                }
            )
        if not channels:
            technical = "misconfigured"
            technical_causes.append(
                {
                    "code": "notification_channel_missing",
                    "message": "No active notification channel is connected.",
                }
            )
        if not technical_causes:
            technical_causes.append(
                {
                    "code": "operational",
                    "message": (
                        "Worker, provider coverage, and notification configuration are "
                        "operating normally."
                    ),
                }
            )

        scan_count, confirmed_count, near_miss_count = (
            await self.session.execute(
                select(
                    func.count(ScanResult.id),
                    func.sum((ScanResult.outcome == ScanOutcome.CONFIRMED).cast(Integer)),
                    func.sum((ScanResult.outcome == ScanOutcome.NEAR_MISS).cast(Integer)),
                ).where(
                    ScanResult.strategy_version_id == version.id,
                    ScanResult.evaluated_at >= now - timedelta(days=14),
                )
            )
        ).one()
        alerts = int(
            await self.session.scalar(
                select(func.count(Alert.id)).where(
                    Alert.strategy_version_id == version.id,
                    Alert.created_at >= now - timedelta(days=1),
                )
            )
            or 0
        )
        unavailable = sum(item.unavailable_count + item.error_count for item in aggregates)
        evaluations = sum(item.evaluation_count for item in aggregates)
        final_blockers = sum(item.final_blocker_count for item in aggregates)
        strategy_status = "healthy"
        strategy_causes: list[dict[str, Any]] = []
        if contradictions:
            strategy_status = "contradictory"
            strategy_causes.append(
                {
                    "code": "contradictory_conditions",
                    "message": contradictions[0].message,
                }
            )
        elif unsupported:
            strategy_status = "provider_limited"
            strategy_causes.append(
                {
                    "code": "unsupported_conditions",
                    "message": (
                        "The approved version contains unsupported or provider-limited "
                        "conditions."
                    ),
                }
            )
        elif unavailable and evaluations and unavailable / evaluations >= 0.2:
            strategy_status = "provider_limited"
            strategy_causes.append(
                {
                    "code": "provider_limited",
                    "message": (
                        f"{round(unavailable / evaluations * 100)}% of condition "
                        "evaluations lacked required data."
                    ),
                }
            )
        elif int(scan_count or 0) < self.settings.observability_minimum_sample_size:
            strategy_status = "insufficient_history"
            strategy_causes.append(
                {
                    "code": "low_sample",
                    "message": (
                        f"Only {int(scan_count or 0)} evaluations are available; "
                        f"{self.settings.observability_minimum_sample_size} are needed "
                        "for a stable classification."
                    ),
                }
            )
        elif not int(confirmed_count or 0) and int(near_miss_count or 0) >= 3:
            strategy_status = "too_strict"
            top = max(aggregates, key=lambda item: item.final_blocker_count, default=None)
            strategy_causes.append(
                {
                    "code": "no_confirmations",
                    "message": (
                        "No confirmations in 14 days; "
                        f"{top.condition_label if top else 'one required rule'} blocked "
                        "most near-misses."
                    ),
                }
            )
        elif int(scan_count or 0) and int(confirmed_count or 0) / int(scan_count) >= 0.25:
            strategy_status = "too_broad"
            strategy_causes.append(
                {
                    "code": "high_confirmation_rate",
                    "message": (
                        f"{round(int(confirmed_count or 0) / int(scan_count) * 100)}% "
                        "of evaluated candidates confirmed in the last 14 days."
                    ),
                }
            )
        elif alerts >= 50:
            strategy_status = "potentially_noisy"
            strategy_causes.append(
                {
                    "code": "high_alert_frequency",
                    "message": f"{alerts} alerts were generated in the last 24 hours.",
                }
            )
        else:
            strategy_causes.append(
                {
                    "code": "balanced_observation",
                    "message": (
                        "Observed completion, near-miss, and alert rates are within "
                        "current deterministic guardrails."
                    ),
                }
            )
        actions = [
            {"key": "open_candidates", "label": "Open candidates"},
            {"key": "inspect_top_blocker", "label": "Inspect top blocker"},
            {"key": "refine_chat", "label": "Refine in Chat"},
            {"key": "edit_canvas", "label": "Edit in Canvas"},
        ]
        summary = await self.session.scalar(
            select(MonitorHealthSummary).where(
                MonitorHealthSummary.strategy_version_id == version.id
            )
        )
        metrics = {
            "worker_heartbeat": cycle.heartbeat_at.isoformat()
            if cycle and cycle.heartbeat_at
            else None,
            "last_successful_evaluation": cycle.completed_at.isoformat()
            if cycle and cycle.completed_at
            else None,
            "next_expected_evaluation": cycle.next_expected_at.isoformat()
            if cycle and cycle.next_expected_at
            else None,
            "symbols_scanned": cycle.symbols_scanned if cycle else 0,
            "symbols_expected": cycle.symbols_expected if cycle else 0,
            "provider_errors": cycle.provider_errors if cycle else 0,
            "stale_candles": cycle.stale_candles if cycle else 0,
            "missing_candles": cycle.missing_candles if cycle else 0,
            "rate_limit_incidents": cycle.rate_limit_incidents if cycle else 0,
            "scanner_failures": cycle.scanner_failures if cycle else 0,
            "notification_channels": channels,
            "evaluations_14d": int(scan_count or 0),
            "confirmed_14d": int(confirmed_count or 0),
            "near_misses_14d": int(near_miss_count or 0),
            "alerts_24h": alerts,
            "final_blockers": final_blockers,
        }
        if summary is None:
            summary = MonitorHealthSummary(
                user_id=strategy.user_id,
                strategy_id=strategy.id,
                strategy_version_id=version.id,
                technical_status=technical,
                strategy_status=strategy_status,
                technical_causes=technical_causes,
                strategy_causes=strategy_causes,
                actions=actions,
                metrics=metrics,
                calculated_at=now,
            )
            self.session.add(summary)
        else:
            summary.technical_status = technical
            summary.strategy_status = strategy_status
            summary.technical_causes = technical_causes
            summary.strategy_causes = strategy_causes
            summary.actions = actions
            summary.metrics = metrics
            summary.calculated_at = now
        await self.session.flush()
        return summary

    async def health(self, user_id: UUID, monitor_id: UUID | None = None) -> list[dict[str, Any]]:
        filters = [MonitorHealthSummary.user_id == user_id]
        if monitor_id:
            filters.append(MonitorHealthSummary.strategy_id == monitor_id)
        rows = (
            await self.session.execute(
                select(MonitorHealthSummary, Strategy.name, StrategyVersion.version_number)
                .join(Strategy, Strategy.id == MonitorHealthSummary.strategy_id)
                .join(
                    StrategyVersion, StrategyVersion.id == MonitorHealthSummary.strategy_version_id
                )
                .where(*filters)
                .order_by(Strategy.name.asc())
            )
        ).all()
        return [
            {
                "monitor_id": str(item.strategy_id),
                "monitor_name": name,
                "strategy_version_id": str(item.strategy_version_id),
                "strategy_version": version,
                "technical_status": item.technical_status,
                "strategy_status": item.strategy_status,
                "technical_causes": item.technical_causes,
                "strategy_causes": item.strategy_causes,
                "actions": item.actions,
                "metrics": item.metrics,
                "calculated_at": item.calculated_at,
            }
            for item, name, version in rows
        ]

    async def bottlenecks(
        self,
        user_id: UUID,
        *,
        monitor_id: UUID | None = None,
        version_id: UUID | None = None,
        required: bool | None = None,
    ) -> list[dict[str, Any]]:
        filters = [ConditionObservabilityAggregate.user_id == user_id]
        if monitor_id:
            filters.append(ConditionObservabilityAggregate.strategy_id == monitor_id)
        if version_id:
            filters.append(ConditionObservabilityAggregate.strategy_version_id == version_id)
        if required is not None:
            filters.append(ConditionObservabilityAggregate.is_required.is_(required))
        latest = (
            select(
                ConditionObservabilityAggregate.strategy_version_id,
                func.max(ConditionObservabilityAggregate.calculated_at).label("latest"),
            )
            .where(*filters)
            .group_by(ConditionObservabilityAggregate.strategy_version_id)
            .subquery()
        )
        rows = (
            await self.session.execute(
                select(
                    ConditionObservabilityAggregate, Strategy.name, StrategyVersion.version_number
                )
                .join(
                    latest,
                    and_(
                        latest.c.strategy_version_id
                        == ConditionObservabilityAggregate.strategy_version_id,
                        latest.c.latest == ConditionObservabilityAggregate.calculated_at,
                    ),
                )
                .join(Strategy, Strategy.id == ConditionObservabilityAggregate.strategy_id)
                .join(
                    StrategyVersion,
                    StrategyVersion.id == ConditionObservabilityAggregate.strategy_version_id,
                )
                .where(*filters)
                .order_by(
                    ConditionObservabilityAggregate.final_blocker_count.desc(),
                    ConditionObservabilityAggregate.fail_count.desc(),
                )
            )
        ).all()
        totals: Counter[UUID] = Counter()
        for item, _, _ in rows:
            totals[item.strategy_version_id] += item.final_blocker_count
        return [
            self._bottleneck_payload(item, name, version, totals[item.strategy_version_id])
            for item, name, version in rows
        ]

    async def investigation(self, user_id: UUID, setup_id: UUID) -> dict[str, Any]:
        owned = await self.session.execute(
            select(SetupInstance, Strategy, StrategyVersion)
            .join(StrategyVersion, StrategyVersion.id == SetupInstance.strategy_version_id)
            .join(Strategy, Strategy.id == StrategyVersion.strategy_id)
            .where(SetupInstance.id == setup_id, SetupInstance.user_id == user_id)
        )
        row = owned.one_or_none()
        if row is None:
            raise LookupError("Lifecycle not found")
        setup, strategy, version = row
        events = list(
            (
                await self.session.scalars(
                    select(SetupLifecycleEvent)
                    .where(SetupLifecycleEvent.setup_instance_id == setup.id)
                    .order_by(SetupLifecycleEvent.occurred_at.asc())
                )
            ).all()
        )
        condition_rows = (
            await self.session.execute(
                select(SetupConditionResult, StrategyCondition)
                .join(
                    StrategyCondition,
                    StrategyCondition.id == SetupConditionResult.strategy_condition_id,
                )
                .where(SetupConditionResult.setup_instance_id == setup.id)
                .order_by(SetupConditionResult.evaluated_at.desc())
            )
        ).all()
        latest: dict[str, tuple[SetupConditionResult, StrategyCondition]] = {}
        for result, condition in condition_rows:
            latest.setdefault(result.condition_key, (result, condition))
        alerts = list(
            (
                await self.session.scalars(
                    select(Alert)
                    .where(Alert.setup_instance_id == setup.id)
                    .order_by(Alert.created_at.desc())
                )
            ).all()
        )
        alert_ids = [item.id for item in alerts]
        deliveries = (
            list(
                (
                    await self.session.scalars(
                        select(AlertDelivery)
                        .where(AlertDelivery.alert_id.in_(alert_ids))
                        .order_by(AlertDelivery.created_at.desc())
                    )
                ).all()
            )
            if alert_ids
            else []
        )
        latest_scan = (
            await self.session.get(ScanResult, setup.latest_scan_result_id)
            if setup.latest_scan_result_id
            else None
        )
        cycle = None
        if latest_scan:
            cycle = await self.session.scalar(
                select(MonitorEvaluationCycle).where(
                    MonitorEvaluationCycle.scan_job_id == latest_scan.scan_job_id
                )
            )
        successful = any(item.status in SUCCESSFUL_DELIVERY for item in deliveries)
        failed_delivery = next(
            (item for item in deliveries if item.status in FAILED_DELIVERY), None
        )
        failed_required = [
            (result, condition)
            for result, condition in latest.values()
            if condition.is_required and result.outcome != ConditionOutcome.PASSED
        ]
        unavailable = [
            (result, condition)
            for result, condition in failed_required
            if result.outcome in {ConditionOutcome.UNAVAILABLE, ConditionOutcome.ERROR}
        ]
        suppressed = next(
            (item for item in reversed(events) if item.to_state == SetupLifecycleState.SUPPRESSED),
            None,
        )
        if failed_delivery and not successful:
            category = "notification_delivery_failure"
            reason = f"The setup confirmed, but {failed_delivery.channel.value} delivery failed."
        elif suppressed:
            category = "cooldown_or_exclusion"
            reason = (
                "The setup confirmed, but notification rules suppressed it: "
                f"{suppressed.reason_code.replace('_', ' ')}."
            )
        elif unavailable:
            category = "data_provider_issue"
            reason = (
                "No alert was sent because required data was unavailable for "
                f"{unavailable[0][1].label}."
            )
        elif failed_required:
            category = "strategy_condition_failure"
            reason = (
                f"No alert was sent because {len(failed_required)} required condition"
                f"{'s' if len(failed_required) != 1 else ''} did not pass."
            )
        elif not alerts:
            category = "completed_without_alert"
            reason = (
                "No notification record exists for this lifecycle. The retained evidence "
                "does not show a completed required rule set."
            )
        elif successful:
            category = "alert_delivered"
            reason = "A notification was delivered successfully for this lifecycle."
        else:
            category = "notification_not_attempted"
            reason = (
                "The setup produced an alert record, but no notification destination was attempted."
            )
        conditions = [
            {
                "key": result.condition_key,
                "label": condition.label,
                "required": condition.is_required,
                "role": "required" if condition.is_required else "optional",
                "status": self._investigation_status(result.outcome),
                "actual": result.actual_value.get("value") if result.actual_value else None,
                "required_value": result.required_value.get("value")
                if result.required_value
                else None,
                "distance_to_pass": float(result.distance_to_pass)
                if result.distance_to_pass is not None
                else None,
                "timeframe": condition.timeframe,
                "evaluated_at": result.evaluated_at,
                "data_freshness_ms": result.data_freshness_ms,
                "explanation_code": result.explanation_code,
            }
            for result, condition in latest.values()
        ]
        evidence = (
            "exact" if latest else "closest_available" if events or latest_scan else "unavailable"
        )
        return {
            "setup_id": str(setup.id),
            "monitor_id": str(strategy.id),
            "monitor_name": strategy.name,
            "strategy_version_id": str(version.id),
            "strategy_version": version.version_number,
            "symbol": setup.symbol,
            "exchange": setup.exchange,
            "timeframe": setup.timeframe,
            "lifecycle_state": setup.state.value,
            "evaluated_window": {"from": setup.first_detected_at, "to": setup.last_evaluated_at},
            "evidence_availability": evidence,
            "primary_category": category,
            "primary_reason": reason,
            "notification_successful": successful,
            "conditions": conditions,
            "condition_summary": {
                "passed": sum(
                    item[0].outcome == ConditionOutcome.PASSED for item in latest.values()
                ),
                "failed_required": len(failed_required),
                "total": len(latest),
            },
            "events": [
                {
                    "from": item.from_state.value if item.from_state else None,
                    "to": item.to_state.value,
                    "reason": item.reason_code,
                    "evidence": item.evidence,
                    "occurred_at": item.occurred_at,
                }
                for item in events
            ],
            "provider_health": {
                "status": "degraded"
                if cycle and cycle.provider_errors
                else "healthy"
                if cycle
                else "unknown",
                "provider_errors": cycle.provider_errors if cycle else None,
                "latest_cycle": cycle.completed_at if cycle else None,
            },
            "notification_deliveries": [
                {
                    "id": str(item.id),
                    "channel": item.channel.value,
                    "status": item.status.value,
                    "attempt_count": item.attempt_count,
                    "last_error_code": item.last_error_code,
                    "last_error_detail": item.last_error_detail,
                    "delivered_at": item.delivered_at,
                    "retry_allowed": item.status in FAILED_DELIVERY,
                }
                for item in deliveries
            ],
            "actions": {
                "view_full_lifecycle": f"/dashboard/lifecycles?setup={setup.id}",
                "open_canvas": f"/dashboard/strategies/{strategy.id}/builder",
                "refine_chat": f"/dashboard/strategies/new?refine={strategy.id}",
                "view_monitor_health": (
                    f"/dashboard/lifecycles?monitor={strategy.id}#monitor-health"
                ),
                "retry_delivery_id": str(failed_delivery.id)
                if failed_delivery and not successful
                else None,
            },
        }

    async def cleanup(self) -> dict[str, int]:
        now = utcnow()
        detail_cutoff = now - timedelta(days=self.settings.observability_detail_retention_days)
        lifecycle_cutoff = now - timedelta(
            days=self.settings.observability_lifecycle_retention_days
        )
        deleted_explanations = await self.session.execute(
            delete(ObservabilityExplanation).where(
                ObservabilityExplanation.created_at < detail_cutoff
            )
        )
        deleted_cycles = await self.session.execute(
            delete(MonitorEvaluationCycle).where(MonitorEvaluationCycle.started_at < detail_cutoff)
        )
        deleted_aggregates = await self.session.execute(
            delete(ConditionObservabilityAggregate).where(
                ConditionObservabilityAggregate.window_ended_at < lifecycle_cutoff
            )
        )
        return {
            "explanations": self._rowcount(deleted_explanations),
            "cycles": self._rowcount(deleted_cycles),
            "aggregates": self._rowcount(deleted_aggregates),
        }

    @staticmethod
    def _rowcount(result: Any) -> int:
        """Return a portable affected-row count for SQLAlchemy DML results."""
        return int(getattr(result, "rowcount", 0) or 0)

    async def _scan_interval(self, version_id: UUID) -> int:
        from ai_market_monitor.db.models import StrategyUniverse

        value = await self.session.scalar(
            select(StrategyUniverse.scan_interval_seconds).where(
                StrategyUniverse.strategy_version_id == version_id
            )
        )
        return int(value or 60)

    async def _notification_channels(self, user_id: UUID) -> list[str]:
        telegram = await self.session.scalar(
            select(TelegramConnection.id).where(
                TelegramConnection.user_id == user_id,
                TelegramConnection.status == ConnectionStatus.ACTIVE,
                TelegramConnection.alerts_enabled.is_(True),
                TelegramConnection.chat_id.is_not(None),
            )
        )
        discord = await self.session.scalar(
            select(DiscordConnection.id).where(
                DiscordConnection.user_id == user_id,
                DiscordConnection.status == ConnectionStatus.ACTIVE,
            )
        )
        whatsapp = await self.session.scalar(
            select(WhatsAppConnection.id).where(
                WhatsAppConnection.user_id == user_id,
                WhatsAppConnection.status == ConnectionStatus.ACTIVE,
                WhatsAppConnection.alerts_enabled.is_(True),
                WhatsAppConnection.verified_at.is_not(None),
                WhatsAppConnection.opt_out_at.is_(None),
            )
        )
        return [
            name
            for name, present in (
                ("telegram", telegram),
                ("whatsapp", whatsapp),
                ("discord", discord),
            )
            if present
        ]

    async def _latest_aggregates(self, version_id: UUID) -> list[ConditionObservabilityAggregate]:
        latest = await self.session.scalar(
            select(func.max(ConditionObservabilityAggregate.calculated_at)).where(
                ConditionObservabilityAggregate.strategy_version_id == version_id
            )
        )
        if latest is None:
            return []
        return list(
            (
                await self.session.scalars(
                    select(ConditionObservabilityAggregate).where(
                        ConditionObservabilityAggregate.strategy_version_id == version_id,
                        ConditionObservabilityAggregate.calculated_at == latest,
                    )
                )
            ).all()
        )

    async def _previous_version_aggregates(
        self, strategy_id: UUID, version_number: int
    ) -> dict[str, ConditionObservabilityAggregate]:
        previous_version = await self.session.scalar(
            select(StrategyVersion)
            .where(
                StrategyVersion.strategy_id == strategy_id,
                StrategyVersion.version_number < version_number,
            )
            .order_by(StrategyVersion.version_number.desc())
            .limit(1)
        )
        if previous_version is None:
            return {}
        rows = await self._latest_aggregates(previous_version.id)
        return {item.condition_key: item for item in rows}

    @staticmethod
    def _candidate_payload(
        item: CandidateReadinessSnapshot, name: str, version: int
    ) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "setup_id": str(item.setup_instance_id) if item.setup_instance_id else None,
            "monitor_id": str(item.strategy_id),
            "monitor_name": name,
            "strategy_version_id": str(item.strategy_version_id),
            "strategy_version": version,
            "symbol": item.symbol,
            "exchange": item.exchange,
            "timeframe": item.timeframe,
            "direction": item.direction,
            "state": item.lifecycle_state,
            "stage_rank": item.stage_rank,
            "required": {"passed": item.required_passed, "total": item.required_total},
            "optional": {"passed": item.optional_passed, "total": item.optional_total},
            "blocker": {
                "key": item.blocker_key,
                "label": item.blocker_label,
                "outcome": item.blocker_outcome,
                "actual": item.blocker_actual.get("value") if item.blocker_actual else None,
                "required": item.blocker_required.get("value") if item.blocker_required else None,
                "distance": float(item.blocker_distance)
                if item.blocker_distance is not None
                else None,
                "unit": item.blocker_unit,
            },
            "most_recent_change": item.most_recent_change,
            "last_changed_at": item.last_changed_at,
            "last_evaluated_at": item.last_evaluated_at,
            "data_freshness_ms": item.data_freshness_ms,
            "data_health": item.data_health,
            "next_candle_close_at": item.next_candle_close_at,
            "expires_at": item.expires_at,
            "notification_status": item.notification_status,
            "condition_tree": item.condition_tree,
            "latest_values": item.latest_values,
        }

    @staticmethod
    def _bottleneck_payload(
        item: ConditionObservabilityAggregate, name: str, version: int, total_blockers: int
    ) -> dict[str, Any]:
        final_share = item.final_blocker_count / total_blockers * 100 if total_blockers else 0
        counterfactual = None
        if item.counterfactual_preview:
            preview = item.counterfactual_preview
            counterfactual = {
                "preview_only": True,
                "current_threshold": preview["current_threshold"],
                "proposed_threshold": preview["proposed_threshold"],
                "historical_completions_at_proposed_threshold": preview[
                    "additional_historical_completions"
                ],
                "message": (
                    f"Preview only: changing the threshold from "
                    f"{float(preview['current_threshold']):g} to "
                    f"{float(preview['proposed_threshold']):g} would have allowed "
                    f"{preview['additional_historical_completions']} additional historical "
                    "candidate completions in the selected window. "
                    "This is not a performance prediction."
                ),
            }
        return {
            "monitor_id": str(item.strategy_id),
            "monitor_name": name,
            "strategy_version_id": str(item.strategy_version_id),
            "strategy_version": version,
            "condition_key": item.condition_key,
            "condition_label": item.condition_label,
            "rule_role": item.rule_role,
            "required": item.is_required,
            "timeframe": item.timeframe,
            "evaluation_count": item.evaluation_count,
            "pass_count": item.pass_count,
            "pass_rate": round(item.pass_count / item.evaluation_count * 100, 2)
            if item.evaluation_count
            else 0,
            "fail_count": item.fail_count,
            "fail_rate": round(item.fail_count / item.evaluation_count * 100, 2)
            if item.evaluation_count
            else 0,
            "final_blocker_count": item.final_blocker_count,
            "final_blocker_share": round(final_share, 2),
            "near_miss_blocker_count": item.near_miss_blocker_count,
            "invalidation_count": item.invalidation_count,
            "average_actual": float(item.average_actual)
            if item.average_actual is not None
            else None,
            "median_actual_when_blocked": float(item.median_actual_when_blocked)
            if item.median_actual_when_blocked is not None
            else None,
            "average_required": float(item.average_required)
            if item.average_required is not None
            else None,
            "average_distance": float(item.average_distance)
            if item.average_distance is not None
            else None,
            "co_occurrence": item.co_occurrence,
            "previous_version_delta": item.previous_version_delta,
            "sample_status": item.sample_status,
            "counterfactual": counterfactual,
            "window": {"from": item.window_started_at, "to": item.window_ended_at},
        }

    @staticmethod
    def _investigation_status(outcome: ConditionOutcome) -> str:
        return {
            ConditionOutcome.PASSED: "passed",
            ConditionOutcome.FAILED: "failed",
            ConditionOutcome.PENDING: "not_evaluated",
            ConditionOutcome.UNAVAILABLE: "data_unavailable",
            ConditionOutcome.ERROR: "unsupported",
        }[outcome]


class GroundedObservabilityExplainer:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    async def explain(self, payload: dict[str, Any]) -> str:
        fallback = self._fallback(payload)
        if (
            not self.settings.openai_explanation_enabled
            or self.settings.openai_api_key is None
            or self.settings.ai_interpreter_provider != "openai"
        ):
            return fallback
        bounded = {
            "primary_reason": payload.get("primary_reason"),
            "primary_category": payload.get("primary_category"),
            "monitor_name": payload.get("monitor_name"),
            "symbol": payload.get("symbol"),
            "timeframe": payload.get("timeframe"),
            "evidence_availability": payload.get("evidence_availability"),
            "condition_summary": payload.get("condition_summary"),
            "conditions": (payload.get("conditions") or [])[:50],
            "provider_health": payload.get("provider_health"),
            "notification_deliveries": (payload.get("notification_deliveries") or [])[:20],
        }
        request = {
            "model": self.settings.openai_model,
            "store": False,
            "max_output_tokens": 260,
            "reasoning": {"effort": self.settings.openai_reasoning_effort},
            "instructions": (
                "Explain the supplied deterministic HilalMarkets lifecycle investigation in plain, "
                "beginner-friendly language. Use only supplied fields. Do not invent values, "
                "override the deterministic conclusion, suggest a trade, claim an alert should "
                "have fired, or modify rules. If evidence is incomplete, say so. Return a short "
                "paragraph, no markdown table."
            ),
            "input": json.dumps(bounded, sort_keys=True, default=str),
        }
        try:
            async with httpx.AsyncClient(
                base_url=str(self.settings.openai_base_url).rstrip("/"),
                timeout=self.settings.openai_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    "/responses",
                    headers={
                        "Authorization": (
                            "Bearer "
                            f"{self.settings.openai_api_key.get_secret_value()}"
                        ),
                        "Content-Type": "application/json",
                    },
                    json=request,
                )
            response.raise_for_status()
            text = self._output_text(response.json()).strip()
            return text[:1200] if text else fallback
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            return fallback

    @staticmethod
    def _fallback(payload: dict[str, Any]) -> str:
        primary_reason = payload.get(
            "primary_reason",
            "The retained evidence does not show why no alert was sent.",
        )
        return (
            f"{primary_reason} "
            f"The monitor evaluated {payload.get('symbol', 'this symbol')} on "
            f"{payload.get('timeframe', 'the configured timeframe')}. "
            "This explanation describes monitoring evidence only, not a trading recommendation."
        )

    @staticmethod
    def evidence_hash(payload: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    @staticmethod
    def _output_text(payload: dict[str, Any]) -> str:
        direct = payload.get("output_text")
        if isinstance(direct, str):
            return direct
        for item in payload.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    return str(content.get("text") or "")
        raise ValueError("OpenAI response did not contain text")
