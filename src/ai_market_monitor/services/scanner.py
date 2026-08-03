import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    Alert,
    AuditEvent,
    ConditionRuntimeState,
    NearMissSnapshot,
    ScanJob,
    ScanResult,
    SetupConditionResult,
    SetupInstance,
    SetupLifecycleEvent,
    Strategy,
    StrategyCondition,
    StrategyExperiment,
    StrategyUniverse,
    StrategyVersion,
)
from ai_market_monitor.db.models.enums import (
    ALLOWED_SETUP_TRANSITIONS,
    TERMINAL_SETUP_STATES,
    AlertType,
    ConditionOutcome,
    ScanJobStatus,
    ScanOutcome,
    SetupLifecycleState,
    StrategyStatus,
    StrategyVersionStatus,
)
from ai_market_monitor.engine.dedup import AlertFatigueGuard, stable_event_hash
from ai_market_monitor.engine.dynamic_mechanics import required_history_candles
from ai_market_monitor.engine.evaluator import (
    StrategyRuleEngine,
    strategy_evaluation_directions,
)
from ai_market_monitor.engine.models import EvaluationResult, ensure_aware
from ai_market_monitor.engine.runtime_preflight import (
    RuntimeDataContract,
    skip_reason,
)
from ai_market_monitor.provider_context import ProviderContextService
from ai_market_monitor.schemas.strategy import (
    ConditionGroup,
    ConditionRule,
    StrategyDefinition,
)
from ai_market_monitor.services.entitlements import (
    EntitlementError,
    EntitlementService,
    UsageService,
)
from ai_market_monitor.services.interfaces import MarketDataProvider
from ai_market_monitor.services.lifecycle import transition_setup
from ai_market_monitor.services.market_preview import (
    assess_candle_data_quality,
    market_snapshot_from_candles,
    timeframe_duration,
)
from ai_market_monitor.services.notification_preferences import NotificationPreferenceService
from ai_market_monitor.services.notifications import NotificationDispatcher
from ai_market_monitor.services.reliability import ReliabilityService
from ai_market_monitor.services.sharia_universe import (
    ShariaUniverseError,
    ShariaUniverseResolver,
)
from ai_market_monitor.services.strategy import StrategyGateError, StrategyService
from ai_market_monitor.services.strategy_hashes import ensure_current_approved_schema_hash
from ai_market_monitor.services.trials import TrialLifecycleService


@dataclass(frozen=True, slots=True)
class ScanRunSummary:
    job_id: UUID
    status: ScanJobStatus
    symbols_planned: int
    symbols_scanned: int
    matches_found: int
    notifications_created: int
    failures: int


class ScanJobNotRunnable(RuntimeError):
    pass


class RetryableProviderFailure(RuntimeError):
    pass


class UniverseResolver:
    def __init__(self, provider: MarketDataProvider):
        self.provider = provider

    async def resolve(
        self,
        definition: StrategyDefinition,
        *,
        maximum_symbols: int | None = None,
    ) -> list[str]:
        symbols = definition.universe.include_symbols or await self.provider.list_symbols(
            definition.universe.exchange,
            definition.universe.quote_currencies,
        )
        excluded = {_canonical_symbol(symbol) for symbol in definition.universe.exclude_symbols}
        quotes = {quote.upper() for quote in definition.universe.quote_currencies}
        normalized = sorted(
            {
                _canonical_symbol(symbol)
                for symbol in symbols
                if _canonical_symbol(symbol) not in excluded
                and _canonical_symbol(symbol).partition("/")[2].upper() in quotes
            }
        )
        caps = [
            cap
            for cap in (maximum_symbols, definition.universe.max_symbols)
            if cap is not None and int(cap) > 0
        ]
        if not caps:
            return normalized
        return normalized[: min(int(cap) for cap in caps)]


def _canonical_symbol(symbol: str) -> str:
    return symbol.upper().replace("-", "/").strip().split(":", 1)[0]


def _sharia_proof_from_scan_context(
    scan_context: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Expose compact screening evidence at a stable proof-receipt key."""
    screening = (scan_context or {}).get("sharia_screening")
    if not isinstance(screening, dict):
        return {}
    return {"sharia_screening": dict(screening)}


class ScanScheduler:
    def __init__(self, session: AsyncSession, settings: Settings | None = None):
        self.session = session
        self.settings = settings

    async def schedule_due(self, *, scheduled_for: datetime | None = None) -> list[ScanJob]:
        if self.settings is not None and not self.settings.scanning_enabled:
            return []
        scheduled_for = ensure_aware(scheduled_for or datetime.now(UTC))
        rows = (
            await self.session.execute(
                select(Strategy, StrategyVersion, StrategyUniverse)
                .join(StrategyVersion, Strategy.active_version_id == StrategyVersion.id)
                .join(
                    StrategyUniverse,
                    StrategyUniverse.strategy_version_id == StrategyVersion.id,
                )
                .where(
                    Strategy.status == StrategyStatus.ACTIVE,
                    StrategyVersion.status == StrategyVersionStatus.ACTIVE,
                )
            )
        ).all()
        jobs: list[ScanJob] = []
        for _strategy, version, universe in rows:
            in_flight = await self.session.scalar(
                select(ScanJob.id)
                .where(
                    ScanJob.strategy_version_id == version.id,
                    ScanJob.job_type == "live",
                    ScanJob.status.in_([ScanJobStatus.QUEUED, ScanJobStatus.RUNNING]),
                )
                .limit(1)
            )
            if in_flight is not None:
                continue
            interval = max(1, universe.scan_interval_seconds)
            bucket_epoch = int(scheduled_for.timestamp()) // interval * interval
            bucket = datetime.fromtimestamp(bucket_epoch, tz=UTC)
            key = stable_event_hash(
                {
                    "strategy_version_id": str(version.id),
                    "scheduled_bucket": bucket.isoformat(),
                    "job_type": "live",
                }
            )
            existing = await self.session.scalar(
                select(ScanJob).where(ScanJob.idempotency_key == key)
            )
            if existing is not None:
                continue
            job = ScanJob(
                strategy_version_id=version.id,
                idempotency_key=key,
                job_type="live",
                status=ScanJobStatus.QUEUED,
                scheduled_for=bucket,
            )
            try:
                async with self.session.begin_nested():
                    self.session.add(job)
                    await self.session.flush()
                jobs.append(job)
            except IntegrityError:
                continue
        return jobs

    async def schedule_due_experiments(
        self,
        *,
        scheduled_for: datetime | None = None,
        experiment_ids: list[UUID] | None = None,
    ) -> list[ScanJob]:
        if self.settings is not None and not self.settings.scanning_enabled:
            return []
        scheduled_for = ensure_aware(scheduled_for or datetime.now(UTC))
        query = select(StrategyExperiment).where(StrategyExperiment.status == "running")
        if experiment_ids:
            query = query.where(StrategyExperiment.id.in_(experiment_ids))
        experiments = (await self.session.scalars(query)).all()
        jobs: list[ScanJob] = []
        for experiment in experiments:
            for raw_version_id in experiment.version_ids:
                try:
                    version_id = UUID(str(raw_version_id))
                except ValueError:
                    continue
                version = await self.session.get(StrategyVersion, version_id)
                if version is None or version.strategy_id != experiment.strategy_id:
                    continue
                universe = await self.session.scalar(
                    select(StrategyUniverse).where(
                        StrategyUniverse.strategy_version_id == version.id
                    )
                )
                interval = max(1, universe.scan_interval_seconds if universe else 60)
                bucket_epoch = int(scheduled_for.timestamp()) // interval * interval
                bucket = datetime.fromtimestamp(bucket_epoch, tz=UTC)
                job_type = (
                    "experiment_live" if experiment.mode == "live_monitor" else "experiment_dry_run"
                )
                key = stable_event_hash(
                    {
                        "experiment_id": str(experiment.id),
                        "strategy_version_id": str(version.id),
                        "scheduled_bucket": bucket.isoformat(),
                        "job_type": job_type,
                    }
                )
                if await self.session.scalar(
                    select(ScanJob.id).where(ScanJob.idempotency_key == key)
                ):
                    continue
                job = ScanJob(
                    strategy_version_id=version.id,
                    idempotency_key=key,
                    job_type=job_type,
                    status=ScanJobStatus.QUEUED,
                    scheduled_for=bucket,
                    metrics={
                        "experiment_id": str(experiment.id),
                        "experiment_mode": experiment.mode,
                    },
                )
                try:
                    async with self.session.begin_nested():
                        self.session.add(job)
                        await self.session.flush()
                    jobs.append(job)
                except IntegrityError:
                    continue
        return jobs

    async def recover_stale_or_retryable(self, *, now: datetime | None = None) -> list[ScanJob]:
        if self.settings is not None and not self.settings.scanning_enabled:
            return []
        current_time = ensure_aware(now or datetime.now(UTC))
        claim_timeout = (
            self.settings.scan_job_claim_timeout_seconds if self.settings is not None else 900
        )
        max_attempts = self.settings.scan_job_max_attempts if self.settings is not None else 3
        stale_before = current_time - timedelta(seconds=claim_timeout)
        rows = (
            await self.session.scalars(
                select(ScanJob)
                .where(
                    (
                        (ScanJob.status == ScanJobStatus.RUNNING)
                        & (ScanJob.heartbeat_at.is_not(None))
                        & (ScanJob.heartbeat_at <= stale_before)
                    )
                    | (
                        (ScanJob.status == ScanJobStatus.QUEUED)
                        & (ScanJob.next_retry_at.is_not(None))
                        & (ScanJob.next_retry_at <= current_time)
                    )
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        dispatchable: list[ScanJob] = []
        for job in rows:
            if job.status == ScanJobStatus.RUNNING:
                if job.attempt_count >= max_attempts:
                    job.status = ScanJobStatus.FAILED
                    job.completed_at = current_time
                    job.error_code = "stale_running_job_exhausted"
                    job.error_detail = "Running scan job heartbeat expired after max attempts."
                    continue
                metrics = dict(job.metrics or {})
                metrics.setdefault("recoveries", []).append(
                    {
                        "worker_id": job.worker_id,
                        "claimed_at": job.claimed_at.isoformat() if job.claimed_at else None,
                        "heartbeat_at": job.heartbeat_at.isoformat() if job.heartbeat_at else None,
                        "recovered_at": current_time.isoformat(),
                    }
                )
                job.metrics = metrics
                job.status = ScanJobStatus.QUEUED
                job.worker_id = None
                job.claimed_at = None
                job.heartbeat_at = None
                job.started_at = None
                job.next_retry_at = current_time
                job.error_code = "stale_running_job_recovered"
            if job.status == ScanJobStatus.QUEUED and (
                job.next_retry_at is None or job.next_retry_at <= current_time
            ):
                dispatchable.append(job)
        await self.session.flush()
        return dispatchable


class ScanPersistenceService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None):
        self.session = session
        self.settings = settings

    async def persist_evaluation(
        self,
        *,
        job: ScanJob,
        strategy: Strategy,
        version: StrategyVersion,
        definition: StrategyDefinition,
        result: EvaluationResult,
        is_candle_complete: bool,
        plan_alert_budget: int | None,
        evidence_only: bool = False,
        scan_context: dict[str, Any] | None = None,
    ) -> tuple[ScanResult, SetupInstance | None, Alert | None]:
        proof_summary = result.proof_receipt()
        if scan_context:
            proof_summary["scan_context"] = scan_context
        sharia_context = dict((scan_context or {}).get("sharia_screening") or {})
        sharia_asset = dict(sharia_context.get("asset") or {})
        scan_result = ScanResult(
            scan_job_id=job.id,
            strategy_version_id=version.id,
            exchange=result.exchange,
            symbol=result.symbol,
            timeframe=result.timeframe,
            direction=result.direction,
            outcome=result.outcome,
            completion_score=Decimal(str(result.near_miss.current_score)),
            candle_closed_at=result.market_data_timestamp or result.evaluation_time,
            evaluated_at=result.evaluation_time,
            data_freshness_ms=max(0, result.data_latency_ms or 0),
            is_candle_complete=is_candle_complete,
            exclusion_reason=(
                ",".join(result.market_filters.reasons) if result.market_filters.reasons else None
            ),
            error_code=(
                result.risk_validation.error_code
                if result.risk_validation
                and result.risk_validation.state.value in {"error", "unavailable"}
                else None
            ),
            proof_summary=proof_summary,
            sharia_methodology_id=(
                UUID(sharia_context["methodology_id"])
                if sharia_context.get("methodology_id")
                else None
            ),
            sharia_methodology_version=sharia_context.get("methodology_version"),
            sharia_status_at_scan=sharia_asset.get("status"),
            sharia_assessment_id=(
                UUID(sharia_asset["assessment_id"])
                if sharia_asset.get("assessment_id")
                else None
            ),
            sharia_passport_version_id=(
                UUID(sharia_asset["passport_version_id"])
                if sharia_asset.get("passport_version_id")
                else None
            ),
            sharia_universe_snapshot_id=(
                UUID(sharia_context["universe_snapshot_id"])
                if sharia_context.get("universe_snapshot_id")
                else None
            ),
            sharia_policy_decision=sharia_context.get("policy_decision"),
            sharia_policy_reason=sharia_context.get("policy_reason"),
        )
        self.session.add(scan_result)
        await self.session.flush()
        await self._persist_runtime_states(version, result)

        if evidence_only:
            return scan_result, None, None

        setup = await self._upsert_setup(
            strategy=strategy,
            version=version,
            definition=definition,
            scan_result=scan_result,
            result=result,
        )
        if setup is not None:
            await self._persist_condition_results(version, setup, scan_result, result)
        await self._persist_near_miss(version, setup, scan_result, result, definition)
        alert = await self._create_alert(
            strategy=strategy,
            version=version,
            setup=setup,
            result=result,
            plan_alert_budget=plan_alert_budget,
            definition=definition,
            scan_context=scan_context,
        )
        if self.settings is not None:
            from ai_market_monitor.services.setup_observability import (
                SetupObservabilityService,
            )

            readiness = await SetupObservabilityService(
                self.session, self.settings
            ).record_candidate(
                strategy=strategy,
                version=version,
                definition=definition,
                scan_result=scan_result,
                setup=setup,
                result=result,
            )
            if alert is not None:
                readiness.notification_status = (
                    "pending"
                    if int(getattr(alert, "_enqueued_delivery_count", 0)) > 0
                    else "not_attempted"
                )
        return scan_result, setup, alert

    async def _persist_runtime_states(
        self,
        version: StrategyVersion,
        result: EvaluationResult,
    ) -> None:
        for condition in result.conditions:
            outcome = ConditionOutcome(condition.state.value)
            state = await self.session.scalar(
                select(ConditionRuntimeState).where(
                    ConditionRuntimeState.strategy_version_id == version.id,
                    ConditionRuntimeState.exchange == result.exchange,
                    ConditionRuntimeState.symbol == result.symbol,
                    ConditionRuntimeState.timeframe == condition.timeframe,
                    ConditionRuntimeState.direction == result.direction,
                    ConditionRuntimeState.condition_key == condition.condition_id,
                )
            )
            passed = outcome == ConditionOutcome.PASSED
            if state is None:
                state = ConditionRuntimeState(
                    strategy_version_id=version.id,
                    exchange=result.exchange,
                    symbol=result.symbol,
                    timeframe=condition.timeframe,
                    direction=result.direction,
                    condition_key=condition.condition_id,
                    last_outcome=outcome,
                    first_true_at=result.evaluation_time if passed else None,
                    last_true_at=result.evaluation_time if passed else None,
                    last_evaluated_at=result.evaluation_time,
                    consecutive_true_count=1 if passed else 0,
                    actual_value={"value": condition.actual_value},
                )
                self.session.add(state)
                continue
            was_passed = state.last_outcome == ConditionOutcome.PASSED
            state.last_outcome = outcome
            state.last_evaluated_at = result.evaluation_time
            state.actual_value = {"value": condition.actual_value}
            if passed:
                state.first_true_at = state.first_true_at if was_passed else result.evaluation_time
                state.last_true_at = result.evaluation_time
                state.consecutive_true_count = state.consecutive_true_count + 1 if was_passed else 1
            else:
                state.first_true_at = None
                state.consecutive_true_count = 0
        await self.session.flush()

    async def _upsert_setup(
        self,
        *,
        strategy: Strategy,
        version: StrategyVersion,
        definition: StrategyDefinition,
        scan_result: ScanResult,
        result: EvaluationResult,
    ) -> SetupInstance | None:
        if result.setup_state is None:
            return None
        should_persist = result.outcome in {
            ScanOutcome.CONFIRMED,
            ScanOutcome.FORMING,
            ScanOutcome.INVALID,
            ScanOutcome.EXPIRED,
        } or (
            result.outcome in {ScanOutcome.NEAR_MISS, ScanOutcome.SKIPPED}
            and self._near_miss_storage_allowed(result, definition)
        )
        if not should_persist:
            return None
        setup = await self.session.scalar(
            select(SetupInstance)
            .where(
                SetupInstance.strategy_version_id == version.id,
                SetupInstance.exchange == result.exchange,
                SetupInstance.symbol == result.symbol,
                SetupInstance.timeframe == result.timeframe,
                SetupInstance.direction == result.direction,
                SetupInstance.state.not_in(TERMINAL_SETUP_STATES),
            )
            .order_by(SetupInstance.first_detected_at.desc())
        )
        risk = result.risk
        if setup is None:
            occurrence = result.market_data_timestamp or result.evaluation_time
            setup = SetupInstance(
                user_id=strategy.user_id,
                strategy_version_id=version.id,
                latest_scan_result_id=scan_result.id,
                exchange=result.exchange,
                symbol=result.symbol,
                timeframe=result.timeframe,
                direction=result.direction,
                setup_key=f"{result.direction}:{ensure_aware(occurrence).isoformat()}",
                state=result.setup_state,
                completion_score=Decimal(str(result.near_miss.current_score)),
                first_detected_at=result.evaluation_time,
                last_evaluated_at=result.evaluation_time,
                confirmed_at=(
                    result.evaluation_time
                    if result.setup_state == SetupLifecycleState.CONFIRMED
                    else None
                ),
                entry_zone_low=Decimal(str(risk.entry_zone_low)) if risk else None,
                entry_zone_high=Decimal(str(risk.entry_zone_high)) if risk else None,
                stop_price=Decimal(str(risk.stop_price)) if risk else None,
                target_price=(
                    Decimal(str(risk.targets[0]["price"])) if risk and risk.targets else None
                ),
                target_levels=risk.targets if risk else [],
                expires_at=result.evaluation_time
                + timeframe_duration(definition.base_timeframe)
                * definition.expiry.expire_after_candles,
                sharia_methodology_id=scan_result.sharia_methodology_id,
                sharia_methodology_version=scan_result.sharia_methodology_version,
                sharia_status_at_detection=scan_result.sharia_status_at_scan,
                sharia_assessment_id=scan_result.sharia_assessment_id,
                sharia_passport_version_id=scan_result.sharia_passport_version_id,
                sharia_universe_snapshot_id=scan_result.sharia_universe_snapshot_id,
                sharia_policy_decision=scan_result.sharia_policy_decision,
            )
            self.session.add(setup)
            await self.session.flush()
            self.session.add(
                SetupLifecycleEvent(
                    setup_instance_id=setup.id,
                    from_state=None,
                    to_state=result.setup_state,
                    reason_code=result.outcome.value,
                    evidence={
                        "scan_result_id": str(scan_result.id),
                        "score": result.near_miss.current_score,
                        "sharia_screening": {
                            "methodology_id": str(scan_result.sharia_methodology_id)
                            if scan_result.sharia_methodology_id
                            else None,
                            "methodology_version": scan_result.sharia_methodology_version,
                            "status": scan_result.sharia_status_at_scan,
                            "assessment_id": str(scan_result.sharia_assessment_id)
                            if scan_result.sharia_assessment_id
                            else None,
                            "passport_version_id": str(
                                scan_result.sharia_passport_version_id
                            )
                            if scan_result.sharia_passport_version_id
                            else None,
                            "universe_snapshot_id": str(
                                scan_result.sharia_universe_snapshot_id
                            )
                            if scan_result.sharia_universe_snapshot_id
                            else None,
                            "policy_decision": scan_result.sharia_policy_decision,
                        },
                    },
                    occurred_at=result.evaluation_time,
                )
            )
            return setup

        setup.latest_scan_result_id = scan_result.id
        setup.completion_score = Decimal(str(result.near_miss.current_score))
        setup.last_evaluated_at = result.evaluation_time
        if risk:
            setup.entry_zone_low = Decimal(str(risk.entry_zone_low))
            setup.entry_zone_high = Decimal(str(risk.entry_zone_high))
            setup.stop_price = Decimal(str(risk.stop_price))
            setup.target_levels = risk.targets
        if (
            result.setup_state != setup.state
            and result.setup_state in ALLOWED_SETUP_TRANSITIONS.get(setup.state, set())
        ):
            event = transition_setup(
                setup,
                result.setup_state,
                reason_code=result.outcome.value,
                evidence={
                    "scan_result_id": str(scan_result.id),
                    "score": result.near_miss.current_score,
                },
                occurred_at=result.evaluation_time,
            )
            self.session.add(event)
        await self.session.flush()
        return setup

    async def _persist_condition_results(
        self,
        version: StrategyVersion,
        setup: SetupInstance,
        scan_result: ScanResult,
        result: EvaluationResult,
    ) -> None:
        keys = [condition.condition_id for condition in result.conditions]
        rows = (
            await self.session.scalars(
                select(StrategyCondition).where(
                    StrategyCondition.strategy_version_id == version.id,
                    StrategyCondition.condition_key.in_(keys),
                )
            )
        ).all()
        by_key = {row.condition_key: row for row in rows}
        for condition in result.conditions:
            definition_row = by_key.get(condition.condition_id)
            if definition_row is None:
                continue
            self.session.add(
                SetupConditionResult(
                    setup_instance_id=setup.id,
                    scan_result_id=scan_result.id,
                    strategy_condition_id=definition_row.id,
                    condition_key=condition.condition_id,
                    outcome=ConditionOutcome(condition.state.value),
                    required_value={"value": condition.required_value},
                    actual_value={
                        "value": condition.actual_value,
                        "previous": condition.previous_actual_value,
                    },
                    distance_to_pass=Decimal(str(max(0, 100 - condition.proximity_score))),
                    contribution_score=Decimal(str(condition.proximity_score)),
                    candle_timestamp=condition.market_data_timestamp
                    or result.market_data_timestamp
                    or result.evaluation_time,
                    evaluated_at=result.evaluation_time,
                    data_freshness_ms=max(0, condition.data_latency_ms or 0),
                    explanation_code=condition.error_code,
                )
            )

    async def _persist_near_miss(
        self,
        version: StrategyVersion,
        setup: SetupInstance | None,
        scan_result: ScanResult,
        result: EvaluationResult,
        definition: StrategyDefinition,
    ) -> None:
        if not self._near_miss_storage_allowed(result, definition):
            return
        self.session.add(
            NearMissSnapshot(
                scan_result_id=scan_result.id,
                setup_instance_id=setup.id if setup else None,
                strategy_version_id=version.id,
                exchange=result.exchange,
                symbol=result.symbol,
                timeframe=result.timeframe,
                completion_score=Decimal(str(result.near_miss.current_score)),
                previous_score=(
                    Decimal(str(result.near_miss.previous_score))
                    if result.near_miss.previous_score is not None
                    else None
                ),
                trend=result.near_miss.trend.value,
                passed_condition_keys=[
                    condition.condition_id for condition in result.near_miss.passed_conditions
                ],
                missing_conditions=[
                    condition.to_proof_dict() for condition in result.near_miss.missing_conditions
                ],
                captured_at=result.evaluation_time,
            )
        )

    @staticmethod
    def _near_miss_storage_allowed(
        result: EvaluationResult,
        definition: StrategyDefinition,
    ) -> bool:
        if (
            not definition.near_miss.enabled
            or result.outcome == ScanOutcome.CONFIRMED
            or result.near_miss.current_score >= 100
            or result.near_miss.current_score < definition.near_miss.minimum_score_to_store
        ):
            return False
        return not any(
            condition.mandatory and condition.state.value in {"error", "unavailable"}
            for condition in result.near_miss.missing_conditions
        )

    async def _create_alert(
        self,
        *,
        strategy: Strategy,
        version: StrategyVersion,
        setup: SetupInstance | None,
        result: EvaluationResult,
        plan_alert_budget: int | None,
        definition: StrategyDefinition,
        scan_context: dict[str, Any] | None,
    ) -> Alert | None:
        alert_type: AlertType | None = None
        if result.outcome == ScanOutcome.CONFIRMED:
            alert_type = AlertType.CONFIRMED
        elif result.outcome == ScanOutcome.NEAR_MISS and result.near_miss.should_alert:
            alert_type = AlertType.NEAR_MISS
        elif result.outcome == ScanOutcome.FORMING and definition.alerts.forming_alerts:
            alert_type = AlertType.FORMING
        if alert_type is None or setup is None:
            return None
        sharia_proof = _sharia_proof_from_scan_context(scan_context)
        trial_service = (
            TrialLifecycleService(self.session, self.settings) if self.settings else None
        )
        if trial_service is not None:
            cap_reached, cycle, trial_cap = await trial_service.trial_alert_cap_reached(
                strategy.user_id
            )
            if cap_reached and cycle is not None:
                if setup.state == SetupLifecycleState.CONFIRMED:
                    self.session.add(
                        transition_setup(
                            setup,
                            SetupLifecycleState.SUPPRESSED,
                            reason_code="trial_alert_limit_reached",
                            evidence={"cycle_id": str(cycle.id)},
                            occurred_at=result.evaluation_time,
                        )
                    )
                suppressed_alert = Alert(
                    user_id=strategy.user_id,
                    strategy_version_id=version.id,
                    setup_instance_id=setup.id,
                    alert_type=alert_type,
                    deduplication_key=stable_event_hash(
                        {
                            "trial_cycle_id": str(cycle.id),
                            "setup_id": str(setup.id),
                            "alert_type": alert_type.value,
                            "reason": "trial_alert_limit_reached",
                        }
                    ),
                    title=(
                        f"{result.symbol} - {alert_type.value.replace('_', ' ').title()} Suppressed"
                    ),
                    body="Trial alert limit reached for this Monitor trial.",
                    proof_receipt={
                        **result.proof_receipt(),
                        **({"scan_context": scan_context} if scan_context else {}),
                        **sharia_proof,
                        "setup_instance_id": str(setup.id),
                        "universe_source": "approved_strategy_universe",
                        "cooldown_status": "not_evaluated_trial_limit",
                        "setup_replay_timeline": [
                            {
                                "timestamp": result.evaluation_time.isoformat(),
                                "state": result.setup_state.value if result.setup_state else None,
                                "event": "alert_suppressed",
                                "reason": "trial_alert_limit_reached",
                            }
                        ],
                    },
                    chart_snapshot_url=result.chart_reference,
                    candle_timestamp=result.market_data_timestamp,
                    suppressed_reason="trial_alert_limit_reached",
                    sharia_assessment_id=setup.sharia_assessment_id,
                    sharia_passport_version_id=setup.sharia_passport_version_id,
                    sharia_methodology_id=setup.sharia_methodology_id,
                    sharia_universe_snapshot_id=setup.sharia_universe_snapshot_id,
                    sharia_policy_decision=setup.sharia_policy_decision,
                )
                self.session.add(suppressed_alert)
                await self.session.flush()
                await trial_service.record_alert_generated(
                    suppressed_alert,
                    suppressed_reason="trial_alert_limit_reached",
                )
                await self._create_trial_limit_notice(
                    strategy=strategy,
                    version=version,
                    definition=definition,
                    cycle_id=cycle.id,
                    alert_cap=trial_cap,
                )
                return None
        configured_budget = definition.alerts.daily_alert_budget
        daily_budget = (
            min(budget for budget in (configured_budget, plan_alert_budget) if budget is not None)
            if configured_budget is not None or plan_alert_budget is not None
            else None
        )
        decision = await AlertFatigueGuard(self.session).check(
            result,
            user_id=strategy.user_id,
            strategy_version_id=version.id,
            alert_type=alert_type,
            cooldown_seconds=definition.alerts.cooldown_seconds,
            maximum_alerts_per_hour=definition.alerts.maximum_alerts_per_hour,
            daily_alert_budget=daily_budget,
        )
        if not decision.allowed:
            if setup.state == SetupLifecycleState.CONFIRMED:
                self.session.add(
                    transition_setup(
                        setup,
                        SetupLifecycleState.SUPPRESSED,
                        reason_code=decision.reason or "alert_suppressed",
                        evidence={"deduplication_key": decision.deduplication_key},
                        occurred_at=result.evaluation_time,
                    )
                )
            return None
        alert = Alert(
            user_id=strategy.user_id,
            strategy_version_id=version.id,
            setup_instance_id=setup.id,
            alert_type=alert_type,
            deduplication_key=decision.deduplication_key,
            title=f"{result.symbol} - {alert_type.value.replace('_', ' ').title()}",
            body=(
                f"{result.strategy_name}: {result.near_miss.current_score:.1f}% complete "
                f"on {result.exchange} {result.timeframe}."
            ),
            proof_receipt={
                **result.proof_receipt(),
                **({"scan_context": scan_context} if scan_context else {}),
                **sharia_proof,
                "setup_instance_id": str(setup.id),
                "universe_source": "approved_strategy_universe",
                "cooldown_status": "passed",
                "setup_replay_timeline": [
                    {
                        "timestamp": result.evaluation_time.isoformat(),
                        "state": result.setup_state.value if result.setup_state else None,
                        "event": "alert_created",
                        "reason": result.outcome.value,
                    }
                ],
            },
            chart_snapshot_url=result.chart_reference,
            candle_timestamp=result.market_data_timestamp,
            sharia_assessment_id=setup.sharia_assessment_id,
            sharia_passport_version_id=setup.sharia_passport_version_id,
            sharia_methodology_id=setup.sharia_methodology_id,
            sharia_universe_snapshot_id=setup.sharia_universe_snapshot_id,
            sharia_policy_decision=setup.sharia_policy_decision,
        )
        self.session.add(alert)
        await self.session.flush()
        if setup.state in {
            SetupLifecycleState.CONFIRMED,
            SetupLifecycleState.SUPPRESSED,
        }:
            self.session.add(
                transition_setup(
                    setup,
                    SetupLifecycleState.ALERT_SENT,
                    reason_code="alert_created",
                    evidence={"alert_id": str(alert.id)},
                    occurred_at=result.evaluation_time,
                )
            )
        if trial_service is not None:
            await trial_service.record_alert_generated(alert)
        deliveries = await NotificationDispatcher(self.session).enqueue(alert, definition)
        alert.__dict__["_enqueued_delivery_count"] = len(deliveries)
        return alert

    async def _create_trial_limit_notice(
        self,
        *,
        strategy: Strategy,
        version: StrategyVersion,
        definition: StrategyDefinition,
        cycle_id: UUID,
        alert_cap: int,
    ) -> None:
        dedupe = stable_event_hash(
            {
                "trial_cycle_id": str(cycle_id),
                "notice": "trial_alert_limit_reached",
            }
        )
        existing = await self.session.scalar(
            select(Alert.id).where(Alert.deduplication_key == dedupe)
        )
        if existing:
            return
        notice = Alert(
            user_id=strategy.user_id,
            strategy_version_id=version.id,
            setup_instance_id=None,
            alert_type=AlertType.TRIAL,
            deduplication_key=dedupe,
            title="Trial alert limit reached",
            body=(
                f"Your trial has reached its {alert_cap}-alert trial limit. "
                "Monitoring evidence is preserved, but additional setup alerts are suppressed "
                "until you upgrade."
            ),
            proof_receipt={
                "trial_cycle_id": str(cycle_id),
                "qualification_status": "non_qualifying_trial_notice",
            },
            chart_snapshot_url=None,
            candle_timestamp=None,
        )
        self.session.add(notice)
        await self.session.flush()
        await NotificationDispatcher(self.session).enqueue(notice, definition)


class ScanOrchestrator:
    def __init__(
        self,
        session: AsyncSession,
        provider: MarketDataProvider,
        *,
        engine: StrategyRuleEngine | None = None,
        settings: Settings | None = None,
    ):
        self.session = session
        self.provider = provider
        self.engine = engine or StrategyRuleEngine()
        self.persistence = ScanPersistenceService(session, settings=settings)
        self.settings = settings
        self.context = ProviderContextService(provider, settings)

    async def run_job(self, job_id: UUID, *, worker_id: str = "local") -> ScanRunSummary:
        if self.settings is not None and not self.settings.scanning_enabled:
            job = await self.session.get(ScanJob, job_id)
            if job is None:
                raise ValueError("Scan job not found")
            return self._summary(job, failures=0)
        job = await self._claim_job(job_id, worker_id=worker_id)
        if job is None:
            existing = await self.session.get(ScanJob, job_id)
            if existing is None:
                raise ValueError("Scan job not found")
            return self._summary(existing, failures=0)
        await self.session.commit()
        try:
            return await self._run_claimed_job(job)
        except Exception as exc:
            await self._fail_claimed_job(job, exc)
            return self._summary(job, failures=1)

    async def _claim_job(self, job_id: UUID, *, worker_id: str) -> ScanJob | None:
        job = await self.session.scalar(
            select(ScanJob).where(ScanJob.id == job_id).with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        now = datetime.now(UTC)
        if job.status != ScanJobStatus.QUEUED:
            return None
        if job.next_retry_at is not None and job.next_retry_at > now:
            return None
        job.status = ScanJobStatus.RUNNING
        job.worker_id = worker_id[:120]
        job.claimed_at = now
        job.heartbeat_at = now
        job.started_at = now
        job.completed_at = None
        job.attempt_count += 1
        await self.session.flush()
        return job

    async def _run_claimed_job(self, job: ScanJob) -> ScanRunSummary:
        if job.status != ScanJobStatus.RUNNING:
            return self._summary(job, failures=0)
        version = await self.session.get(StrategyVersion, job.strategy_version_id)
        if version is None:
            await self._cancel_job(job, "strategy_version_missing", "Strategy version not found.")
            return self._summary(job, failures=0)
        strategy = await self.session.get(Strategy, version.strategy_id)
        experiment = None
        experiment_mode = None
        if job.job_type.startswith("experiment_"):
            raw_experiment_id = (job.metrics or {}).get("experiment_id")
            try:
                experiment_id = UUID(str(raw_experiment_id))
            except ValueError:
                await self._cancel_job(
                    job,
                    "experiment_context_invalid",
                    "Experiment scan context is invalid.",
                )
                return self._summary(job, failures=0)
            experiment = await self.session.get(StrategyExperiment, experiment_id)
            if (
                strategy is None
                or experiment is None
                or experiment.status != "running"
                or experiment.strategy_id != strategy.id
                or str(version.id) not in experiment.version_ids
            ):
                await self._cancel_job(
                    job,
                    "experiment_not_running",
                    "The experiment is no longer runnable.",
                )
                return self._summary(job, failures=0)
            experiment_mode = experiment.mode
            if experiment_mode == "live_monitor" and (
                strategy.status != StrategyStatus.ACTIVE
                or version.status
                not in {
                    StrategyVersionStatus.APPROVED,
                    StrategyVersionStatus.READY,
                    StrategyVersionStatus.ACTIVE,
                }
                or version.approved_at is None
                or version.approved_schema_hash != version.schema_hash
            ):
                await self._cancel_job(
                    job,
                    "experiment_version_not_approved",
                    "Live experiment versions must be approved before scanning.",
                )
                return self._summary(job, failures=0)
        elif (
            strategy is None
            or strategy.status != StrategyStatus.ACTIVE
            or strategy.active_version_id != version.id
            or version.status != StrategyVersionStatus.ACTIVE
            or version.approved_at is None
            or version.approved_schema_hash != version.schema_hash
        ):
            if (
                strategy is not None
                and strategy.status == StrategyStatus.ACTIVE
                and strategy.active_version_id == version.id
            ):
                await self._degrade_strategy(
                    strategy,
                    job,
                    "strategy_not_runnable",
                    "The active monitor failed its immutable version or approval gate.",
                )
                return self._summary(job, failures=0)
            await self._cancel_job(job, "strategy_not_active", "Strategy is not active.")
            return self._summary(job, failures=0)

        definition = StrategyDefinition.model_validate(version.schema_json)
        if not ensure_current_approved_schema_hash(version, definition):
            await self._degrade_strategy(
                strategy,
                job,
                "strategy_hash_mismatch",
                "Strategy schema hash no longer matches the approved version.",
            )
            return self._summary(job, failures=0)
        settings = self.settings or Settings()
        try:
            await StrategyService(
                self.session,
                settings.disclaimer_version,
            ).assert_dynamic_capability_artifacts(
                definition,
                user_id=strategy.user_id,
            )
        except StrategyGateError as exc:
            await self._degrade_strategy(strategy, job, exc.code, str(exc))
            return self._summary(job, failures=0)
        preferences = await NotificationPreferenceService(self.session).current(strategy.user_id)
        if definition.universe.exchange.lower() not in (preferences.providers or set()):
            await self._degrade_strategy(
                strategy,
                job,
                "provider_disabled",
                "This strategy's market data provider is disabled in user settings.",
            )
            return self._summary(job, failures=0)
        try:
            entitlement = await EntitlementService(self.session).enforce_strategy_activation(
                strategy.user_id,
                definition,
                strategy_id=strategy.id,
            )
        except EntitlementError as exc:
            await self._degrade_strategy(strategy, job, exc.code, str(exc))
            return self._summary(job, failures=0)
        maximum_symbols = int(entitlement.limit("symbols_per_strategy") or 0)
        try:
            screening = await ShariaUniverseResolver(
                self.session,
                self.provider,
                settings,
            ).resolve(
                definition,
                user_id=strategy.user_id,
                strategy_version_id=version.id,
                maximum_symbols=maximum_symbols,
            )
        except ShariaUniverseError as exc:
            await self._degrade_strategy(strategy, job, exc.code, str(exc))
            return self._summary(job, failures=0)
        if screening.monitor_paused_for_compliance:
            await self._degrade_strategy(
                strategy,
                job,
                "monitor_paused_for_compliance",
                "The Watchlist was paused because a previously included asset left its "
                "screened-market policy.",
            )
            return self._summary(job, failures=0)
        symbols = screening.included_symbols
        symbols = await self.context.rank_symbols(
            definition,
            symbols,
            job.scheduled_for,
        )
        job.symbols_planned = len(symbols)
        job.metrics = {
            **(job.metrics or {}),
            "sharia_screening": {
                "methodology_id": str(screening.methodology_id)
                if screening.methodology_id
                else None,
                "methodology_code": screening.methodology_code,
                "methodology_version": screening.methodology_version,
                "universe_snapshot_id": str(screening.snapshot_id)
                if screening.snapshot_id
                else None,
                "universe_snapshot_hash": screening.snapshot_hash,
                "assets_considered": screening.considered_count,
                "assets_excluded_by_policy": screening.excluded_by_policy_count,
                "insufficient_information": screening.insufficient_information_count,
                "eligible_assets_planned": screening.included_count,
                "legacy_local_bypass": screening.legacy_local_bypass,
            },
        }
        screening_by_symbol = {item.symbol: item for item in screening.included}
        completed_symbols = set(
            (
                await self.session.scalars(
                    select(ScanResult.symbol).where(ScanResult.scan_job_id == job.id)
                )
            ).all()
        )
        job.symbols_scanned = len(completed_symbols)
        if self.settings is not None:
            from ai_market_monitor.services.setup_observability import (
                SetupObservabilityService,
            )

            await SetupObservabilityService(self.session, self.settings).sync_cycle(
                job=job,
                strategy=strategy,
                version=version,
                provider=definition.universe.exchange,
            )
        await self.session.flush()
        await self.session.commit()

        failures: list[dict[str, object]] = []
        matches = job.matches_found
        notifications_created = int((job.metrics or {}).get("notifications_created") or 0)
        for symbol in symbols:
            if symbol in completed_symbols:
                continue
            try:
                async with self.session.begin_nested():
                    result = await self._evaluate_symbol(
                        strategy, version, definition, symbol, job.scheduled_for
                    )
                    is_complete = self._base_candle_complete(
                        definition, result["candle_sets"], job.scheduled_for
                    )
                    confirmed_evaluations = 0
                    plan_budget_value = entitlement.limit("alerts_per_day")
                    plan_alert_budget = (
                        int(plan_budget_value) if plan_budget_value is not None else None
                    )
                    for evaluation in result["evaluations"]:
                        _, _, alert = await self.persistence.persist_evaluation(
                            job=job,
                            strategy=strategy,
                            version=version,
                            definition=definition,
                            result=evaluation,
                            is_candle_complete=is_complete,
                            plan_alert_budget=plan_alert_budget,
                            evidence_only=experiment_mode == "dry_run",
                            scan_context={
                                **(
                                    {
                                    "experiment_id": str(experiment.id),
                                    "mode": experiment_mode,
                                    "evidence_only": experiment_mode == "dry_run",
                                    }
                                    if experiment is not None
                                    else {}
                                ),
                                "sharia_screening": {
                                    "methodology_id": str(screening.methodology_id)
                                    if screening.methodology_id
                                    else None,
                                    "methodology_code": screening.methodology_code,
                                    "methodology_version": screening.methodology_version,
                                    "universe_snapshot_id": str(screening.snapshot_id)
                                    if screening.snapshot_id
                                    else None,
                                    "universe_snapshot_hash": screening.snapshot_hash,
                                    "asset": (
                                        screening_by_symbol[symbol].model_dump(mode="json")
                                        if symbol in screening_by_symbol
                                        else None
                                    ),
                                    "policy_decision": "included",
                                    "policy_reason": (
                                        "Asset met the approved screened-market policy at "
                                        "evaluation time."
                                    ),
                                    "legacy_local_bypass": screening.legacy_local_bypass,
                                },
                            },
                        )
                        if alert is not None:
                            notifications_created += int(
                                getattr(alert, "_enqueued_delivery_count", 0)
                            )
                        confirmed_evaluations += int(evaluation.outcome == ScanOutcome.CONFIRMED)
                    job.symbols_scanned += 1
                    matches += confirmed_evaluations
                    job.matches_found = matches
                    job.heartbeat_at = datetime.now(UTC)
                    await self.session.flush()
                    await self.session.commit()
            except Exception as exc:
                retryable = self._is_retryable_provider_error(exc)
                failures.append(
                    {
                        "symbol": symbol,
                        "error": type(exc).__name__,
                        "detail": str(exc)[:300],
                        "retryable": retryable,
                    }
                )
                job.heartbeat_at = datetime.now(UTC)
                await self.session.flush()
                await self.session.commit()

        job.matches_found = matches
        completed_at = datetime.now(UTC)
        job.completed_at = completed_at
        job.metrics = {
            **(job.metrics or {}),
            "failures": failures,
            "notifications_created": notifications_created,
        }
        if failures and job.symbols_scanned:
            job.status = ScanJobStatus.PARTIAL
            job.error_code = "partial_symbol_failures"
        elif failures:
            all_retryable = all(bool(failure["retryable"]) for failure in failures)
            max_attempts = self.settings.scan_job_max_attempts if self.settings else 3
            if all_retryable and job.attempt_count < max_attempts:
                job.status = ScanJobStatus.QUEUED
                job.error_code = "retryable_provider_failure"
                job.error_detail = "All symbols failed due to retryable provider errors."
                delay_seconds = min(3600, 60 * (2 ** (job.attempt_count - 1)))
                job.next_retry_at = completed_at + timedelta(seconds=delay_seconds)
                job.completed_at = None
                job.worker_id = None
                job.claimed_at = None
                job.heartbeat_at = None
            else:
                job.status = ScanJobStatus.FAILED
                job.error_code = "all_symbols_failed"
                job.error_detail = (
                    "All symbols failed; retry budget exhausted or failures are permanent."
                )
        else:
            job.status = ScanJobStatus.SUCCEEDED
            job.next_retry_at = None
            job.error_code = None
            job.error_detail = None
        if job.completed_at is not None:
            await UsageService(self.session).record(
                strategy.user_id,
                "symbols_scanned",
                quantity=job.symbols_scanned,
                period_start=job.scheduled_for,
                period_end=job.completed_at,
                idempotency_key=f"scan:{job.id}:symbols",
                subject_type="scan_job",
                subject_id=str(job.id),
                metadata={
                    "matches": matches,
                    "failures": len(failures),
                    "job_type": job.job_type,
                    "experiment_id": (job.metrics or {}).get("experiment_id"),
                },
            )
        if self.settings is not None:
            from ai_market_monitor.services.setup_observability import (
                SetupObservabilityService,
            )

            await SetupObservabilityService(self.session, self.settings).sync_cycle(
                job=job,
                strategy=strategy,
                version=version,
                provider=definition.universe.exchange,
            )
        await self.session.flush()
        return self._summary(job, failures=len(failures))

    async def _evaluate_symbol(
        self,
        strategy: Strategy,
        version: StrategyVersion,
        definition: StrategyDefinition,
        symbol: str,
        evaluation_time: datetime,
    ) -> dict:
        timeframes = {definition.base_timeframe, *definition.supporting_timeframes}
        candle_sets = {
            timeframe: await self.provider.fetch_ohlcv(
                definition.universe.exchange,
                symbol,
                timeframe,
                self._history_limit(definition, timeframe),
            )
            for timeframe in timeframes
        }
        reliability = ReliabilityService(self.session)
        for timeframe, candles in candle_sets.items():
            await reliability.record_market_data_health(
                provider="ccxt",
                exchange=definition.universe.exchange,
                symbol=symbol,
                timeframe=timeframe,
                latest_candle_at=candles[-1].timestamp if candles else None,
                retrieved_at=evaluation_time,
                candle_count=len(candles),
                expected_candle_count=definition.universe.min_historical_candles,
                duplicate_candle_count=len(candles) - len({candle.timestamp for candle in candles}),
                out_of_order_candle_count=sum(
                    left.timestamp >= right.timestamp
                    for left, right in zip(candles[:-1], candles[1:], strict=True)
                ),
                source_status="ok" if candles else "unavailable",
            )
        provider_metadata: dict[str, Any] = {}
        metadata_loader = getattr(self.provider, "fetch_universe_metadata", None)
        if callable(metadata_loader):
            try:
                provider_metadata = (
                    await metadata_loader(
                        definition.universe.exchange,
                        [symbol],
                        include_listing_dates=(
                            definition.universe.min_listing_age_days is not None
                        ),
                    )
                ).get(symbol, {})
            except Exception:
                provider_metadata = {
                    "data_quality_ok": False,
                    "exchange_available": False,
                    "metadata_source": "provider_metadata_unavailable",
                }
        market = market_snapshot_from_candles(
            definition,
            symbol,
            candle_sets,
            evaluation_time,
            provider_metadata,
        )
        quality = assess_candle_data_quality(
            definition,
            candle_sets,
            evaluation_time,
        )
        # The market-data promise this version was approved under, kept per symbol.
        #
        # A `policy_verified_runtime_fail_closed` approval means only a sample was
        # checked before approval, so every market has to be checked here. Nothing
        # enforced that: the worker evaluated whatever the universe re-resolved to,
        # including markets that entered it after approval and had never been checked.
        contract = RuntimeDataContract.from_approval_evidence(version.approval_evidence)
        refusal = skip_reason(
            contract,
            symbol=symbol,
            timeframes=sorted(timeframes),
            data_is_usable=quality.usable,
            data_reason=quality.code,
        )
        if refusal is not None:
            # `data_quality_ok=False` makes `MarketFilterEngine.evaluate` fail, which
            # returns `ScanOutcome.SKIPPED` with no conditions evaluated at all. The
            # reason travels with it, so a skipped market is visible rather than silent.
            market = replace(
                market,
                data_quality_ok=False,
                metadata={
                    **market.metadata,
                    "reliability_warnings": [refusal],
                    "preflight_contract": contract.contract,
                    "candle_snapshot_hash": quality.manifest_hash,
                },
            )
        previous_score = await self.session.scalar(
            select(NearMissSnapshot.completion_score)
            .where(
                NearMissSnapshot.strategy_version_id == version.id,
                NearMissSnapshot.exchange == definition.universe.exchange,
                NearMissSnapshot.symbol == symbol,
                NearMissSnapshot.timeframe == definition.base_timeframe,
            )
            .order_by(NearMissSnapshot.captured_at.desc())
            .limit(1)
        )
        last_strategy_triggered_at = await self.session.scalar(
            select(Alert.created_at)
            .where(Alert.strategy_version_id == version.id)
            .order_by(Alert.created_at.desc())
            .limit(1)
        )
        last_symbol_triggered_at = await self.session.scalar(
            select(Alert.created_at)
            .join(SetupInstance, SetupInstance.id == Alert.setup_instance_id)
            .where(
                Alert.strategy_version_id == version.id,
                SetupInstance.exchange == definition.universe.exchange,
                SetupInstance.symbol == symbol,
            )
            .order_by(Alert.created_at.desc())
            .limit(1)
        )
        current_setup = await self.session.scalar(
            select(SetupInstance)
            .where(
                SetupInstance.strategy_version_id == version.id,
                SetupInstance.exchange == definition.universe.exchange,
                SetupInstance.symbol == symbol,
                SetupInstance.timeframe == definition.base_timeframe,
                SetupInstance.state.not_in(TERMINAL_SETUP_STATES),
            )
            .order_by(SetupInstance.first_detected_at.desc())
            .limit(1)
        )
        setup_first_detected_at = (
            current_setup.first_detected_at if current_setup is not None else None
        )
        alerts_last_hour = await self.session.scalar(
            select(func.count(Alert.id)).where(
                Alert.strategy_version_id == version.id,
                Alert.created_at >= ensure_aware(evaluation_time) - timedelta(hours=1),
            )
        )
        alerts_last_day = await self.session.scalar(
            select(func.count(Alert.id)).where(
                Alert.strategy_version_id == version.id,
                Alert.created_at >= ensure_aware(evaluation_time) - timedelta(days=1),
            )
        )
        recent_outcomes = (
            await self.session.scalars(
                select(ScanResult.outcome)
                .where(
                    ScanResult.strategy_version_id == version.id,
                    ScanResult.exchange == definition.universe.exchange,
                    ScanResult.symbol == symbol,
                    ScanResult.timeframe == definition.base_timeframe,
                )
                .order_by(ScanResult.evaluated_at.desc())
                .limit(2)
            )
        ).all()
        condition_first_true_rows = (
            await self.session.execute(
                select(
                    ConditionRuntimeState.condition_key,
                    ConditionRuntimeState.first_true_at,
                ).where(
                    ConditionRuntimeState.strategy_version_id == version.id,
                    ConditionRuntimeState.exchange == definition.universe.exchange,
                    ConditionRuntimeState.symbol == symbol,
                    ConditionRuntimeState.first_true_at.is_not(None),
                )
            )
        ).all()
        base_candles = candle_sets.get(definition.base_timeframe, [])
        latest_close = float(base_candles[-1].close) if base_candles else None
        condition_context = {
            "last_strategy_triggered_at": last_strategy_triggered_at,
            "last_symbol_triggered_at": last_symbol_triggered_at,
            "last_triggered_at": last_symbol_triggered_at or last_strategy_triggered_at,
            "setup_first_detected_at": setup_first_detected_at,
            "setup_state": current_setup.state.value if current_setup else None,
            "setup_expires_at": current_setup.expires_at if current_setup else None,
            "setup_entry_zone_active": bool(
                current_setup
                and latest_close is not None
                and current_setup.entry_zone_low is not None
                and current_setup.entry_zone_high is not None
                and float(current_setup.entry_zone_low)
                <= latest_close
                <= float(current_setup.entry_zone_high)
            ),
            "setup_state_changed": (
                len(recent_outcomes) < 2 or recent_outcomes[0] != recent_outcomes[1]
            ),
            "alerts_last_hour": int(alerts_last_hour or 0),
            "alerts_last_day": int(alerts_last_day or 0),
            "condition_first_true_at_by_key": {
                key: value for key, value in condition_first_true_rows
            },
        }
        condition_context = await self.context.build(
            definition,
            symbol,
            candle_sets,
            evaluation_time,
            base_context=condition_context,
        )
        directions = strategy_evaluation_directions(definition)
        evaluations = [
            self.engine.evaluate(
                definition,
                market,
                candle_sets,
                evaluation_time=evaluation_time,
                strategy_version=str(version.version_number),
                strategy_id=str(strategy.id),
                strategy_version_id=str(version.id),
                strategy_version_number=version.version_number,
                market_data_provider="ccxt",
                evaluation_direction=direction,
                previous_score=float(previous_score) if previous_score is not None else None,
                condition_context=condition_context,
            )
            for direction in directions
        ]
        return {"evaluations": evaluations, "candle_sets": candle_sets}

    def _history_limit(self, definition: StrategyDefinition, timeframe: str) -> int:
        limit = max(300, definition.universe.min_historical_candles)
        maximum = (
            self.settings.capability_extension_max_history_candles
            if self.settings is not None
            else 25_000
        )
        for rule in _condition_rules(definition.conditions):
            if rule.timeframe != timeframe or rule.left.name != "certified_dynamic":
                continue
            try:
                expression = json.loads(str(rule.left.parameters.get("expression_json") or "{}"))
                limit = max(
                    limit,
                    required_history_candles(
                        expression,
                        timeframe,
                        minimum=limit,
                    ),
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return min(limit, maximum)

    @staticmethod
    def _base_candle_complete(
        definition: StrategyDefinition,
        candle_sets: dict,
        evaluation_time: datetime,
    ) -> bool:
        eligible = [
            candle
            for candle in candle_sets.get(definition.base_timeframe, [])
            if ensure_aware(candle.timestamp) <= ensure_aware(evaluation_time)
            and (definition.trigger_mode.value != "candle_close" or candle.is_closed)
        ]
        return bool(eligible and eligible[-1].is_closed)

    async def _cancel_job(self, job: ScanJob, code: str, detail: str) -> None:
        job.status = ScanJobStatus.CANCELED
        job.completed_at = datetime.now(UTC)
        job.error_code = code
        job.error_detail = detail[:1000]
        job.next_retry_at = None
        await self.session.flush()

    async def _degrade_strategy(
        self,
        strategy: Strategy,
        job: ScanJob,
        code: str,
        detail: str,
    ) -> None:
        """Pause a monitor when this gate would reject every future scheduled job."""

        now = datetime.now(UTC)
        strategy.status = StrategyStatus.PAUSED
        strategy.paused_at = now
        self.session.add(
            AuditEvent(
                actor_user_id=strategy.user_id,
                actor_type="system",
                action="strategy.runtime_degraded",
                target_type="strategy",
                target_id=str(strategy.id),
                metadata_redacted={
                    "code": code,
                    "strategy_version_id": str(job.strategy_version_id),
                },
                created_at=now,
            )
        )
        await self._cancel_job(job, code, detail)

    async def _fail_claimed_job(self, job: ScanJob, exc: Exception) -> None:
        job.status = ScanJobStatus.FAILED
        job.completed_at = datetime.now(UTC)
        job.error_code = type(exc).__name__
        job.error_detail = str(exc)[:1000]
        job.next_retry_at = None
        await self.session.flush()

    @staticmethod
    def _is_retryable_provider_error(exc: Exception) -> bool:
        return isinstance(exc, TimeoutError | ConnectionError | RetryableProviderFailure)

    @staticmethod
    def _summary(job: ScanJob, *, failures: int) -> ScanRunSummary:
        return ScanRunSummary(
            job_id=job.id,
            status=job.status,
            symbols_planned=job.symbols_planned,
            symbols_scanned=job.symbols_scanned,
            matches_found=job.matches_found,
            notifications_created=int((job.metrics or {}).get("notifications_created") or 0),
            failures=failures,
        )


def _condition_rules(node: ConditionRule | ConditionGroup) -> list[ConditionRule]:
    if isinstance(node, ConditionRule):
        return [node]
    rules: list[ConditionRule] = []
    for child in node.children:
        rules.extend(_condition_rules(child))
    return rules
