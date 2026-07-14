from __future__ import annotations

import hashlib
import json
import re
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    AuditEvent,
    BacktestJob,
    BacktestResult,
    DiscordConnection,
    ForensicInvestigation,
    MonitorHealthSummary,
    OutcomeReview,
    ScanJob,
    ScanResult,
    SetupConditionResult,
    SetupInstance,
    SetupLifecycleEvent,
    Strategy,
    StrategyInterpretationStatement,
    StrategyTestCase,
    StrategyTestRun,
    StrategyVersion,
    StrategyVersionVerification,
    TelegramConnection,
)
from ai_market_monitor.db.models.enums import (
    ConnectionStatus,
    DeliveryStatus,
    ScanJobStatus,
    ScanOutcome,
)
from ai_market_monitor.engine.models import EvaluationResult, EvaluationState, ensure_aware
from ai_market_monitor.schemas.strategy import ConditionRule, StrategyDefinition
from ai_market_monitor.services.dashboard_jobs import DashboardJobService
from ai_market_monitor.services.interfaces import MarketDataProvider
from ai_market_monitor.services.market_preview import timeframe_duration
from ai_market_monitor.strategy_cockpit import (
    condition_rules,
    forecast_from_structure,
    validate_strategy_conflicts,
)

CRITICAL_INTERPRETATION_STATES = {"ambiguous", "unsupported", "contradictory"}
SUCCESSFUL_DELIVERY_STATES = {DeliveryStatus.SENT, DeliveryStatus.DELIVERED}
FINAL_TEST_FAILURES = {"failed", "error", "needs_review"}


class VerifiedStrategyError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def seal_alert_proof(alert: Alert, *, now: datetime | None = None) -> str:
    """Seal a proof once; later callers can verify but cannot silently replace it."""
    digest = canonical_hash(alert.proof_receipt or {})
    if alert.proof_hash and alert.proof_hash != digest:
        raise VerifiedStrategyError(
            "proof_integrity_violation",
            "The stored alert proof no longer matches its immutable integrity hash.",
        )
    alert.proof_hash = alert.proof_hash or digest
    alert.proof_schema_version = alert.proof_schema_version or "1.0"
    alert.proof_sealed_at = alert.proof_sealed_at or now or datetime.now(UTC)
    return digest


class VerifiedStrategyService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def owned_strategy(self, user_id: UUID, strategy_id: UUID) -> Strategy:
        strategy = await self.session.scalar(
            select(Strategy).where(Strategy.id == strategy_id, Strategy.user_id == user_id)
        )
        if strategy is None:
            raise VerifiedStrategyError("strategy_not_found", "Strategy not found.")
        return strategy

    async def owned_version(self, user_id: UUID, version_id: UUID) -> StrategyVersion:
        version = await self.session.scalar(
            select(StrategyVersion)
            .join(Strategy, Strategy.id == StrategyVersion.strategy_id)
            .where(StrategyVersion.id == version_id, Strategy.user_id == user_id)
        )
        if version is None:
            raise VerifiedStrategyError("version_not_found", "Strategy version not found.")
        return version

    async def prepare_version(
        self,
        *,
        user_id: UUID,
        strategy: Strategy,
        version: StrategyVersion,
        parent: StrategyVersion | None = None,
    ) -> StrategyVersionVerification:
        version.created_by_user_id = version.created_by_user_id or user_id
        if parent and parent.id != version.id:
            version.parent_version_id = version.parent_version_id or parent.id
            diff = semantic_strategy_diff(
                StrategyDefinition.model_validate(parent.schema_json),
                StrategyDefinition.model_validate(version.schema_json),
            )
            version.semantic_diff = diff
            version.change_summary = semantic_diff_summary(diff)
        await self.sync_interpretation(user_id=user_id, strategy=strategy, version=version)
        verification = await self._verification(user_id, strategy, version)
        verification.semantic_diff = list(version.semantic_diff or [])
        active_tests = await self.session.scalar(
            select(func.count(StrategyTestCase.id)).where(
                StrategyTestCase.user_id == user_id,
                StrategyTestCase.strategy_id == strategy.id,
                StrategyTestCase.active.is_(True),
            )
        )
        if active_tests and verification.tests_status == "not_run":
            verification.tests_status = "queued"
        await self.refresh_quality(user_id=user_id, strategy=strategy, version=version)
        return verification

    async def sync_interpretation(
        self,
        *,
        user_id: UUID,
        strategy: Strategy,
        version: StrategyVersion,
    ) -> list[StrategyInterpretationStatement]:
        existing = list(
            (
                await self.session.scalars(
                    select(StrategyInterpretationStatement)
                    .where(
                        StrategyInterpretationStatement.user_id == user_id,
                        StrategyInterpretationStatement.strategy_version_id == version.id,
                    )
                    .order_by(StrategyInterpretationStatement.position)
                )
            ).all()
        )
        if existing:
            return existing
        definition = StrategyDefinition.model_validate(version.schema_json)
        rules = condition_rules(definition)
        phrases = _prompt_phrases(version.source_text or "")
        unmatched = list(rules)
        groups: list[tuple[str, list[ConditionRule]]] = []
        for phrase in phrases:
            selected = [rule for rule in unmatched if _rule_matches_phrase(rule, phrase)]
            if selected:
                groups.append((phrase, selected))
                unmatched = [rule for rule in unmatched if rule not in selected]
        for rule in unmatched:
            groups.append((rule.source_fragment or rule.label, [rule]))
        if not groups and rules:
            groups = [(version.source_text or "Structured strategy", rules)]
        rows: list[StrategyInterpretationStatement] = []
        for position, (phrase, mapped) in enumerate(groups):
            status = _interpretation_status(version, phrase, mapped)
            mechanics = [_rule_mechanics(rule, definition) for rule in mapped]
            row = StrategyInterpretationStatement(
                user_id=user_id,
                strategy_id=strategy.id,
                strategy_version_id=version.id,
                position=position,
                original_phrase=phrase,
                structured_interpretation="; ".join(rule.label for rule in mapped),
                rule_keys=[rule.key for rule in mapped],
                mechanics={"rules": mechanics},
                assumptions=_matching_assumptions(version.assumptions, phrase),
                status=status,
                resolution_status="confirmed" if status == "confirmed" else "unresolved",
            )
            self.session.add(row)
            rows.append(row)
        await self.session.flush()
        return rows

    async def resolve_statement(
        self,
        *,
        user_id: UUID,
        statement_id: UUID,
        action: str,
        resolution_text: str | None,
    ) -> StrategyInterpretationStatement:
        row = await self.session.scalar(
            select(StrategyInterpretationStatement).where(
                StrategyInterpretationStatement.id == statement_id,
                StrategyInterpretationStatement.user_id == user_id,
            )
        )
        if row is None:
            raise VerifiedStrategyError(
                "statement_not_found", "Interpretation statement not found."
            )
        version = await self.owned_version(user_id, row.strategy_version_id)
        if version.approved_at is not None:
            raise VerifiedStrategyError(
                "approved_version_immutable",
                "Approved interpretation is immutable. Create a draft version to edit it.",
            )
        if action not in {"accept", "answer"}:
            raise VerifiedStrategyError(
                "revision_required",
                "Edit or remove this rule in Chat or Canvas to create a new draft version.",
            )
        if action == "answer" and not (resolution_text or "").strip():
            raise VerifiedStrategyError("answer_required", "Enter the clarification answer.")
        row.status = "confirmed"
        row.resolution_status = "accepted" if action == "accept" else "answered"
        row.resolution_text = (resolution_text or "").strip() or None
        row.resolved_at = datetime.now(UTC)
        self._audit(
            user_id=user_id,
            action="strategy.interpretation_resolved",
            target_type="strategy_interpretation_statement",
            target_id=row.id,
            metadata={"resolution": action, "version_id": str(row.strategy_version_id)},
        )
        await self.session.flush()
        return row

    async def approve_interpretation(
        self, *, user_id: UUID, version: StrategyVersion
    ) -> StrategyVersionVerification:
        strategy = await self.owned_strategy(user_id, version.strategy_id)
        statements = await self.sync_interpretation(
            user_id=user_id, strategy=strategy, version=version
        )
        unresolved = [
            item
            for item in statements
            if item.status in CRITICAL_INTERPRETATION_STATES
            or (item.status == "assumed" and item.resolution_status == "unresolved")
        ]
        if unresolved:
            raise VerifiedStrategyError(
                "interpretation_unresolved",
                f"Resolve {len(unresolved)} interpretation item(s) before approval.",
            )
        verification = await self._verification(user_id, strategy, version)
        verification.interpretation_status = "approved"
        verification.updated_at = datetime.now(UTC)
        self._audit(
            user_id=user_id,
            action="strategy.interpretation_approved",
            target_type="strategy_version",
            target_id=version.id,
            metadata={"schema_hash": version.schema_hash},
        )
        await self.session.flush()
        return verification

    async def create_test_case(
        self,
        *,
        user_id: UUID,
        strategy: Strategy,
        title: str,
        case_type: str,
        expected_result: str,
        exchange: str,
        symbol: str,
        timeframe: str,
        evaluation_time: datetime,
        notes: str | None,
    ) -> StrategyTestCase:
        if case_type not in {"positive", "negative", "near_match"}:
            raise VerifiedStrategyError("invalid_case_type", "Choose a supported example type.")
        if expected_result not in {"should_trigger", "should_not_trigger", "near_match"}:
            raise VerifiedStrategyError("invalid_expected_result", "Choose an expected result.")
        case = StrategyTestCase(
            user_id=user_id,
            strategy_id=strategy.id,
            title=title.strip(),
            case_type=case_type,
            expected_result=expected_result,
            exchange=exchange.casefold(),
            symbol=_symbol(symbol),
            timeframe=timeframe,
            evaluation_time=ensure_aware(evaluation_time),
            notes=(notes or "").strip() or None,
            active=True,
        )
        self.session.add(case)
        await self.session.flush()
        return case

    async def run_test_case(
        self,
        *,
        user_id: UUID,
        case: StrategyTestCase,
        version: StrategyVersion,
        provider: MarketDataProvider,
    ) -> StrategyTestRun:
        if case.user_id != user_id or case.strategy_id != version.strategy_id:
            raise VerifiedStrategyError("test_case_not_found", "Test case not found.")
        existing = await self.session.scalar(
            select(StrategyTestRun).where(
                StrategyTestRun.test_case_id == case.id,
                StrategyTestRun.strategy_version_id == version.id,
                StrategyTestRun.schema_hash == version.schema_hash,
            )
        )
        evidence: dict[str, Any]
        try:
            evaluation = await DashboardJobService(
                self.session, provider, self.settings
            ).evaluate_historical_moment(
                strategy_version_id=version.id,
                symbol=case.symbol,
                timeframe=case.timeframe,
                evaluation_time=case.evaluation_time,
            )
            actual = _actual_test_result(evaluation)
            status = "passed" if _test_matches(case.expected_result, actual) else "failed"
            mismatch = None if status == "passed" else _test_mismatch(case, evaluation, actual)
            values = [condition.to_proof_dict() for condition in evaluation.conditions]
            evidence = {
                "proof": evaluation.proof_receipt(),
                "chart_marker": {
                    "timestamp": evaluation.evaluation_time.isoformat(),
                    "symbol": evaluation.symbol,
                    "outcome": evaluation.outcome.value,
                },
            }
            provider_name = evaluation.market_data_provider
            candle_timestamp = evaluation.market_data_timestamp or evaluation.evaluation_time
        except Exception as exc:
            actual = "unavailable"
            status = "needs_review"
            mismatch = str(exc)
            values = []
            evidence = {"error_code": getattr(exc, "code", type(exc).__name__)}
            provider_name = type(provider).__name__
            candle_timestamp = None
        run = existing or StrategyTestRun(
            user_id=user_id,
            test_case_id=case.id,
            strategy_version_id=version.id,
            schema_hash=version.schema_hash,
            status=status,
            expected_result=case.expected_result,
            actual_result=actual,
            condition_results=values,
            mismatch_reason=mismatch,
            evidence=evidence,
            data_source=provider_name,
            candle_timestamp=candle_timestamp,
            run_at=datetime.now(UTC),
        )
        if existing:
            run.status = status
            run.actual_result = actual
            run.condition_results = values
            run.mismatch_reason = mismatch
            run.evidence = evidence
            run.data_source = provider_name
            run.candle_timestamp = candle_timestamp
            run.run_at = datetime.now(UTC)
        else:
            self.session.add(run)
        await self.session.flush()
        await self._refresh_test_status(user_id, version)
        self._audit(
            user_id=user_id,
            action="strategy.test_executed",
            target_type="strategy_test_case",
            target_id=case.id,
            metadata={
                "strategy_version_id": str(version.id),
                "status": status,
                "actual_result": actual,
            },
        )
        return run

    async def run_saved_tests(
        self,
        *,
        user_id: UUID,
        version: StrategyVersion,
        provider: MarketDataProvider,
    ) -> list[StrategyTestRun]:
        cases = list(
            (
                await self.session.scalars(
                    select(StrategyTestCase)
                    .where(
                        StrategyTestCase.user_id == user_id,
                        StrategyTestCase.strategy_id == version.strategy_id,
                        StrategyTestCase.active.is_(True),
                    )
                    .order_by(StrategyTestCase.created_at)
                )
            ).all()
        )
        return [
            await self.run_test_case(
                user_id=user_id, case=case, version=version, provider=provider
            )
            for case in cases
        ]

    async def queue_historical_validation(
        self,
        *,
        user_id: UUID,
        strategy: Strategy,
        version: StrategyVersion,
        exchange: str,
        symbols: list[str],
        timeframe: str,
        started_at: datetime,
        ended_at: datetime,
    ) -> BacktestJob:
        if ensure_aware(started_at) >= ensure_aware(ended_at):
            raise VerifiedStrategyError("invalid_date_range", "Start must be before end.")
        job = BacktestJob(
            user_id=user_id,
            strategy_id=strategy.id,
            strategy_version_id=version.id,
            exchange=exchange,
            symbols=[_symbol(value) for value in symbols][:500],
            timeframe=timeframe,
            started_at_range=ensure_aware(started_at),
            ended_at_range=ensure_aware(ended_at),
            status="queued",
            parameters={
                "workflow": "verified_strategy",
                "non_advisory": True,
                "include": ["matches", "near_matches", "invalidated", "non_matches"],
            },
        )
        self.session.add(job)
        await self.session.flush()
        verification = await self._verification(user_id, strategy, version)
        verification.historical_status = "queued"
        verification.historical_job_id = job.id
        verification.updated_at = datetime.now(UTC)
        self._audit(
            user_id=user_id,
            action="strategy.historical_validation_queued",
            target_type="strategy_version",
            target_id=version.id,
            metadata={"job_id": str(job.id), "symbol_count": len(job.symbols)},
        )
        return job

    async def sync_historical_result(
        self, *, user_id: UUID, job: BacktestJob, result: BacktestResult
    ) -> StrategyVersionVerification:
        if not job.strategy_id or not job.strategy_version_id:
            raise VerifiedStrategyError("strategy_required", "Historical job has no strategy.")
        strategy = await self.owned_strategy(user_id, job.strategy_id)
        version = await self.owned_version(user_id, job.strategy_version_id)
        verification = await self._verification(user_id, strategy, version)
        metrics = dict(result.metrics or {})
        verification.historical_status = str(metrics.get("status") or job.status)
        verification.historical_summary = _historical_summary(metrics, result.setup_results)
        verification.updated_at = datetime.now(UTC)
        await self.refresh_quality(user_id=user_id, strategy=strategy, version=version)
        return verification

    async def approval_gate(self, *, user_id: UUID, version: StrategyVersion) -> None:
        strategy = await self.owned_strategy(user_id, version.strategy_id)
        verification = await self._verification(user_id, strategy, version)
        statements = await self.sync_interpretation(
            user_id=user_id, strategy=strategy, version=version
        )
        unresolved = [
            item
            for item in statements
            if item.status in CRITICAL_INTERPRETATION_STATES
            or (item.status == "assumed" and item.resolution_status == "unresolved")
        ]
        if unresolved or verification.interpretation_status != "approved":
            raise VerifiedStrategyError(
                "interpretation_approval_required",
                "Approve the reviewed interpretation before approving this version.",
            )
        tests = list(
            (
                await self.session.scalars(
                    select(StrategyTestRun).where(
                        StrategyTestRun.user_id == user_id,
                        StrategyTestRun.strategy_version_id == version.id,
                    )
                )
            ).all()
        )
        if any(item.status in FINAL_TEST_FAILURES for item in tests):
            raise VerifiedStrategyError(
                "strategy_examples_regressed",
                "One or more saved examples fail on this version. Review them before approval.",
            )

    async def restore_version(
        self, *, user_id: UUID, strategy: Strategy, source: StrategyVersion
    ) -> StrategyVersion:
        from ai_market_monitor.services.strategy import StrategyService

        latest = await self.session.scalar(
            select(StrategyVersion)
            .where(StrategyVersion.strategy_id == strategy.id)
            .order_by(StrategyVersion.version_number.desc())
            .limit(1)
        )
        restored = await StrategyService(
            self.session, self.settings.disclaimer_version
        ).revise(
            strategy,
            StrategyDefinition.model_validate(source.schema_json),
            user_id=user_id,
            source_text=source.source_text,
            assumptions=list(source.assumptions or []),
            ambiguities=list(source.ambiguities or []),
            unsupported=list(source.unsupported_conditions or []),
            interpreter="user-restored-version",
        )
        restored.restored_from_version_id = source.id
        restored.parent_version_id = latest.id if latest else None
        restored.change_summary = f"Restored Version {source.version_number} as a new draft."
        await self.prepare_version(
            user_id=user_id, strategy=strategy, version=restored, parent=latest
        )
        self._audit(
            user_id=user_id,
            action="strategy.version_restored",
            target_type="strategy_version",
            target_id=restored.id,
            metadata={"source_version_id": str(source.id)},
        )
        return restored

    async def investigate(
        self,
        *,
        user_id: UUID,
        strategy: Strategy,
        symbol: str,
        exchange: str,
        timeframe: str,
        requested_time: datetime,
    ) -> ForensicInvestigation:
        requested = ensure_aware(requested_time)
        version_ids = select(StrategyVersion.id).where(
            StrategyVersion.strategy_id == strategy.id
        )
        expected_version = await self.session.scalar(
            select(StrategyVersion)
            .where(
                StrategyVersion.strategy_id == strategy.id,
                StrategyVersion.activated_at.is_not(None),
                StrategyVersion.activated_at <= requested,
            )
            .order_by(StrategyVersion.activated_at.desc())
            .limit(1)
        )
        monitor_event = await self.session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.target_type == "strategy",
                AuditEvent.target_id == str(strategy.id),
                AuditEvent.action.in_(
                    [
                        "strategy.activated",
                        "strategy.paused",
                        "strategy.resumed",
                        "strategy.deleted",
                    ]
                ),
                AuditEvent.created_at <= requested,
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(1)
        )
        event_states = {
            "strategy.activated": "active",
            "strategy.paused": "paused",
            "strategy.resumed": "active",
            "strategy.deleted": "archived",
        }
        monitor_state = (
            event_states.get(monitor_event.action, "unknown")
            if monitor_event
            else "unknown"
        )
        jobs = list(
            (
                await self.session.scalars(
                    select(ScanJob)
                    .where(
                        ScanJob.strategy_version_id.in_(version_ids),
                        ScanJob.scheduled_for.between(
                            requested - timedelta(hours=12), requested + timedelta(hours=12)
                        ),
                    )
                    .order_by(ScanJob.scheduled_for.desc())
                    .limit(100)
                )
            ).all()
        )
        nearest_job = min(
            jobs,
            key=lambda item: abs(
                (ensure_aware(item.scheduled_for) - requested).total_seconds()
            ),
            default=None,
        )
        # Keep SQL portable, then select the nearest UTC timestamp in Python.
        candidates = list(
            (
                await self.session.scalars(
                    select(ScanResult)
                    .where(
                        ScanResult.strategy_version_id.in_(version_ids),
                        ScanResult.exchange == exchange,
                        ScanResult.symbol == _symbol(symbol),
                        ScanResult.timeframe == timeframe,
                        ScanResult.evaluated_at.between(
                            requested - timedelta(hours=12), requested + timedelta(hours=12)
                        ),
                    )
                    .order_by(ScanResult.evaluated_at.desc())
                    .limit(200)
                )
            ).all()
        )
        scan = min(
            candidates,
            key=lambda item: abs((ensure_aware(item.evaluated_at) - requested).total_seconds()),
            default=None,
        )
        setup = None
        if scan:
            setup = await self.session.scalar(
                select(SetupInstance).where(
                    SetupInstance.strategy_version_id == scan.strategy_version_id,
                    SetupInstance.exchange == scan.exchange,
                    SetupInstance.symbol == scan.symbol,
                    SetupInstance.timeframe == scan.timeframe,
                )
            )
        rule_results: list[dict[str, Any]] = []
        timeline: list[dict[str, Any]] = []
        delivery: dict[str, Any] = {"attempted": False, "deliveries": []}
        category = "evidence_unavailable"
        conclusion = "No retained evaluation was available near the selected time."
        availability = "unavailable"
        if scan:
            offset_seconds = abs(
                (ensure_aware(scan.evaluated_at) - requested).total_seconds()
            )
            availability = "exact" if offset_seconds <= 60 else "closest_available"
            category = _scan_category(scan)
            conclusion = _scan_conclusion(scan)
            if setup:
                results = list(
                    (
                        await self.session.scalars(
                            select(SetupConditionResult)
                            .where(
                                SetupConditionResult.setup_instance_id == setup.id,
                                SetupConditionResult.scan_result_id == scan.id,
                            )
                            .order_by(SetupConditionResult.condition_key)
                        )
                    ).all()
                )
                rule_results = [_condition_result_payload(item) for item in results]
                events = list(
                    (
                        await self.session.scalars(
                            select(SetupLifecycleEvent)
                            .where(SetupLifecycleEvent.setup_instance_id == setup.id)
                            .order_by(SetupLifecycleEvent.occurred_at)
                        )
                    ).all()
                )
                timeline = [
                    {
                        "state": item.to_state.value,
                        "reason": item.reason_code,
                        "occurred_at": ensure_aware(item.occurred_at).isoformat(),
                        "evidence": item.evidence,
                    }
                    for item in events
                ]
                alert = await self.session.scalar(
                    select(Alert)
                    .where(
                        Alert.setup_instance_id == setup.id,
                        Alert.strategy_version_id == scan.strategy_version_id,
                    )
                    .order_by(Alert.created_at.desc())
                    .limit(1)
                )
                if alert:
                    deliveries = list(
                        (
                            await self.session.scalars(
                                select(AlertDelivery).where(AlertDelivery.alert_id == alert.id)
                            )
                        ).all()
                    )
                    delivery = {
                        "attempted": bool(deliveries),
                        "alert_id": str(alert.id),
                        "suppressed_reason": alert.suppressed_reason,
                        "deliveries": [
                            {
                                "channel": item.channel.value,
                                "status": item.status.value,
                                "error": item.last_error_detail,
                            }
                            for item in deliveries
                        ],
                    }
                    if alert.suppressed_reason:
                        category = _suppression_category(alert.suppressed_reason)
                        conclusion = _suppression_conclusion(alert.suppressed_reason)
                    elif deliveries and not any(
                        item.status in SUCCESSFUL_DELIVERY_STATES for item in deliveries
                    ):
                        category = "notification_delivery"
                        conclusion = "The setup created an alert, but notification delivery failed."
            if not scan.is_candle_complete and category == "market_conditions":
                category = "incomplete_candle"
                conclusion = (
                    "No alert was sent because the evaluated candle was still forming. "
                    "Intrabar values are not treated as a closed-candle confirmation."
                )
            if expected_version and scan.strategy_version_id != expected_version.id:
                category = "version_mismatch"
                scanned_version = await self._version_number(scan.strategy_version_id)
                conclusion = (
                    f"The retained scan used Version {scanned_version}, "
                    f"while Version {expected_version.version_number} was expected at that time."
                )
        elif monitor_state in {"paused", "archived"}:
            availability = "system_evidence_only"
            category = "monitor_configuration"
            conclusion = f"The monitor was {monitor_state} at the selected time."
        elif nearest_job and nearest_job.status in {
            ScanJobStatus.FAILED,
            ScanJobStatus.CANCELED,
        }:
            availability = "system_evidence_only"
            category = "scan_failure"
            conclusion = (
                "The scheduled scan did not complete"
                f" ({nearest_job.error_code or nearest_job.status.value})."
            )
        elif expected_version is None:
            availability = "system_evidence_only"
            category = "monitor_configuration"
            conclusion = "No approved strategy version was active at the selected time."
        version_id = scan.strategy_version_id if scan else strategy.active_version_id
        investigation = ForensicInvestigation(
            user_id=user_id,
            strategy_id=strategy.id,
            strategy_version_id=version_id,
            setup_instance_id=setup.id if setup else None,
            exchange=exchange,
            symbol=_symbol(symbol),
            timeframe=timeframe,
            requested_time=requested,
            status="completed",
            evidence_availability=availability,
            primary_category=category,
            conclusion=conclusion,
            rule_results=rule_results,
            timeline=timeline,
            system_diagnostics={
                "scan_found": scan is not None,
                "scan_status": scan.outcome.value if scan else None,
                "exclusion_reason": scan.exclusion_reason if scan else None,
                "error_code": scan.error_code if scan else None,
                "evaluated_at": (
                    ensure_aware(scan.evaluated_at).isoformat() if scan else None
                ),
                "candle_complete": scan.is_candle_complete if scan else None,
                "monitor_state": monitor_state,
                "monitor_state_evidence_at": (
                    ensure_aware(monitor_event.created_at).isoformat()
                    if monitor_event
                    else None
                ),
                "nearest_scan_job": (
                    {
                        "id": str(nearest_job.id),
                        "status": nearest_job.status.value,
                        "scheduled_for": ensure_aware(nearest_job.scheduled_for).isoformat(),
                        "symbols_planned": nearest_job.symbols_planned,
                        "symbols_scanned": nearest_job.symbols_scanned,
                        "error_code": nearest_job.error_code,
                        "error_detail": nearest_job.error_detail,
                    }
                    if nearest_job
                    else None
                ),
                "expected_strategy_version_id": (
                    str(expected_version.id) if expected_version else None
                ),
            },
            delivery_diagnostics=delivery,
            completed_at=datetime.now(UTC),
        )
        self.session.add(investigation)
        self._audit(
            user_id=user_id,
            action="strategy.forensic_investigation_completed",
            target_type="forensic_investigation",
            target_id=investigation.id,
            metadata={
                "strategy_id": str(strategy.id),
                "evidence_availability": availability,
                "primary_category": category,
            },
        )
        await self.session.flush()
        return investigation

    async def _version_number(self, version_id: UUID) -> int | str:
        version = await self.session.get(StrategyVersion, version_id)
        return version.version_number if version else "unknown"

    async def review_outcome(
        self,
        *,
        user_id: UUID,
        alert: Alert,
        provider: MarketDataProvider,
        horizon_minutes: int,
        classification: str,
        classification_rules: dict[str, Any],
        notes: str | None,
        tags: list[str],
    ) -> OutcomeReview:
        if alert.user_id != user_id or not alert.strategy_version_id:
            raise VerifiedStrategyError("alert_not_found", "Alert not found.")
        version = await self.owned_version(user_id, alert.strategy_version_id)
        if classification not in {"positive", "negative", "neutral", "invalid"}:
            raise VerifiedStrategyError("invalid_outcome", "Choose an outcome classification.")
        review = await self.session.scalar(
            select(OutcomeReview).where(
                OutcomeReview.alert_id == alert.id,
                OutcomeReview.horizon_minutes == horizon_minutes,
            )
        )
        due = (alert.candle_timestamp or alert.created_at) + timedelta(minutes=horizon_minutes)
        proof = dict(alert.proof_receipt or {})
        exchange = str(proof.get("exchange") or "binance")
        symbol = str(proof.get("symbol") or "")
        timeframe = str(proof.get("timeframe") or "15m")
        confirmed_at = ensure_aware(alert.candle_timestamp or alert.created_at)
        candles = []
        evidence_error = None
        if symbol:
            try:
                range_fetcher = getattr(provider, "fetch_ohlcv_range", None)
                interval = max(60, int(timeframe_duration(timeframe).total_seconds()))
                limit = min(2000, max(20, int(horizon_minutes * 60 / interval) + 10))
                if callable(range_fetcher):
                    candles = await range_fetcher(
                        exchange,
                        symbol,
                        timeframe,
                        confirmed_at,
                        ensure_aware(due),
                        limit,
                    )
                else:
                    candles = await provider.fetch_ohlcv(
                        exchange,
                        symbol,
                        timeframe,
                        limit,
                    )
                candles = [
                    candle
                    for candle in candles
                    if confirmed_at <= ensure_aware(candle.timestamp) <= ensure_aware(due)
                ]
            except Exception as exc:
                evidence_error = getattr(exc, "code", type(exc).__name__)
        price_path = [
            {
                "timestamp": ensure_aware(candle.timestamp).isoformat(),
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
            }
            for candle in candles[-500:]
        ]
        if candles:
            start_price = float(candles[0].close)
            end_price = float(candles[-1].close)
            metrics = {
                "evidence_available": True,
                "start_price": start_price,
                "end_price": end_price,
                "highest_price": max(float(candle.high) for candle in candles),
                "lowest_price": min(float(candle.low) for candle in candles),
                "change_percent": (
                    round((end_price - start_price) / start_price * 100, 6)
                    if start_price
                    else None
                ),
                "candle_count": len(candles),
                "provider": type(provider).__name__,
                "classification_source": "user",
            }
        else:
            metrics = {
                "evidence_available": False,
                "reason": evidence_error or "historical_candles_unavailable",
                "classification_source": "user",
            }
        if review is None:
            review = OutcomeReview(
                user_id=user_id,
                strategy_id=version.strategy_id,
                strategy_version_id=version.id,
                setup_instance_id=alert.setup_instance_id,
                alert_id=alert.id,
                horizon_minutes=horizon_minutes,
                evaluation_due_at=due,
                status="reviewed" if candles else "reviewed_without_market_path",
                classification=classification,
                classification_rules=classification_rules,
                outcome_metrics=metrics,
                price_path=price_path,
                notes=(notes or "").strip() or None,
                tags=tags[:20],
                reviewed_at=datetime.now(UTC),
            )
            self.session.add(review)
        else:
            review.status = "reviewed" if candles else "reviewed_without_market_path"
            review.classification = classification
            review.classification_rules = classification_rules
            review.outcome_metrics = metrics
            review.price_path = price_path
            review.notes = (notes or "").strip() or None
            review.tags = tags[:20]
            review.reviewed_at = datetime.now(UTC)
        self._audit(
            user_id=user_id,
            action="alert.outcome_reviewed",
            target_type="outcome_review",
            target_id=review.id,
            metadata={
                "alert_id": str(alert.id),
                "classification": classification,
                "horizon_minutes": horizon_minutes,
            },
        )
        await self.session.flush()
        return review

    async def refresh_quality(
        self, *, user_id: UUID, strategy: Strategy, version: StrategyVersion
    ) -> dict[str, Any]:
        verification = await self._verification(user_id, strategy, version)
        definition = StrategyDefinition.model_validate(version.schema_json)
        statements = await self.sync_interpretation(
            user_id=user_id, strategy=strategy, version=version
        )
        runs = list(
            (
                await self.session.scalars(
                    select(StrategyTestRun).where(
                        StrategyTestRun.user_id == user_id,
                        StrategyTestRun.strategy_version_id == version.id,
                    )
                )
            ).all()
        )
        findings = [item.to_dict() for item in validate_strategy_conflicts(definition)]
        rules = condition_rules(definition)
        provider_unavailable = any(
            rule.provider_required and rule.availability != "available" for rule in rules
        )
        history = dict(verification.historical_summary or {})
        observed = int(history.get("evaluations") or 0)
        confirmed = int(history.get("matches") or 0)
        frequency = forecast_from_structure(
            definition,
            historical_matches=confirmed,
            observation_days=max(1.0, float(history.get("observation_days") or 1)),
            symbols_observed=max(1, len(history.get("symbols") or [])),
        )
        ambiguity_count = sum(
            item.status == "ambiguous"
            or (item.status == "assumed" and item.resolution_status == "unresolved")
            for item in statements
        )
        unsupported_count = sum(item.status == "unsupported" for item in statements)
        contradiction_count = sum(
            item["severity"] == "critical" or "contradict" in item["code"]
            for item in findings
        )
        condition_influence = _condition_influence(
            dict(history.get("condition_statistics") or {})
        )
        dimensions = {
            "clarity": _dimension(
                not ambiguity_count and not unsupported_count and not contradiction_count,
                "All interpretation items are resolved."
                if not ambiguity_count and not unsupported_count and not contradiction_count
                else "Interpretation issues still need review.",
            ),
            "test_coverage": _dimension(
                bool(runs) and all(item.status == "passed" for item in runs),
                f"{sum(item.status == 'passed' for item in runs)}/{len(runs)} saved examples pass."
                if runs
                else "No saved examples have been run.",
                "not_run" if not runs else None,
            ),
            "historical_coverage": _dimension(
                observed > 0,
                f"{observed} historical moments evaluated."
                if observed
                else "Historical validation has not completed.",
                "not_run" if not observed else None,
            ),
            "monitoring_readiness": _dimension(
                not any(item["severity"] == "critical" for item in findings),
                "No critical deterministic validation findings."
                if not any(item["severity"] == "critical" for item in findings)
                else "Critical deterministic findings block activation.",
            ),
            "data_reliability": _dimension(
                not provider_unavailable,
                "Required data sources are available."
                if not provider_unavailable
                else "At least one required provider-backed rule is unavailable.",
            ),
        }
        report = {
            "dimensions": dimensions,
            "ambiguity_count": ambiguity_count,
            "unsupported_instructions": unsupported_count,
            "contradictions": contradiction_count,
            "unit_tests": {
                "passed": sum(item.status == "passed" for item in runs),
                "failed": sum(item.status == "failed" for item in runs),
                "needs_review": sum(item.status == "needs_review" for item in runs),
            },
            "historical_match_frequency": history.get("estimated_frequency"),
            "breadth_warning": frequency.get("classification"),
            "condition_influence": condition_influence,
            "required_data_dependencies": sorted(
                {value for rule in rules for value in rule.required_data}
            ),
            "expected_alert_frequency": frequency,
            "monitor_compatible": all(item["status"] != "blocked" for item in dimensions.values()),
            "remaining_risks": [item["message"] for item in findings],
            "non_advisory_notice": (
                "Quality dimensions measure monitoring reliability, not returns."
            ),
        }
        verification.quality_report = report
        verification.updated_at = datetime.now(UTC)
        await self.session.flush()
        return report

    async def contract(
        self, *, user_id: UUID, strategy: Strategy, version: StrategyVersion
    ) -> dict[str, Any]:
        verification = await self._verification(user_id, strategy, version)
        statements = await self.sync_interpretation(
            user_id=user_id, strategy=strategy, version=version
        )
        tests = list(
            (
                await self.session.execute(
                    select(StrategyTestCase, StrategyTestRun)
                    .outerjoin(
                        StrategyTestRun,
                        (StrategyTestRun.test_case_id == StrategyTestCase.id)
                        & (StrategyTestRun.strategy_version_id == version.id),
                    )
                    .where(
                        StrategyTestCase.user_id == user_id,
                        StrategyTestCase.strategy_id == strategy.id,
                    )
                )
            ).all()
        )
        telegram = await self.session.scalar(
            select(TelegramConnection).where(
                TelegramConnection.user_id == user_id,
                TelegramConnection.status == ConnectionStatus.ACTIVE,
            )
        )
        discord = await self.session.scalar(
            select(DiscordConnection).where(
                DiscordConnection.user_id == user_id,
                DiscordConnection.status == ConnectionStatus.ACTIVE,
            )
        )
        payload = {
            "contract_version": "1.0",
            "generated_at": datetime.now(UTC).isoformat(),
            "strategy": {
                "id": str(strategy.id),
                "name": strategy.name,
                "summary": strategy.description,
                "status": strategy.status.value,
            },
            "version": {
                "id": str(version.id),
                "number": version.version_number,
                "schema_hash": version.schema_hash,
                "created_at": version.created_at.isoformat(),
                "approved_at": version.approved_at.isoformat() if version.approved_at else None,
                "change_summary": version.change_summary,
            },
            "original_prompt": version.source_text,
            "structured_rules": version.schema_json,
            "interpretation": [_statement_payload(item) for item in statements],
            "assumptions": version.assumptions,
            "unit_tests": [
                {
                    "id": str(case.id),
                    "title": case.title,
                    "expected": case.expected_result,
                    "result": run.status if run else "not_run",
                    "actual": run.actual_result if run else None,
                }
                for case, run in tests
            ],
            "historical_validation": verification.historical_summary,
            "quality_report": verification.quality_report,
            "activation": {
                "active_version": strategy.active_version_id == version.id,
                "activated_at": version.activated_at.isoformat() if version.activated_at else None,
            },
            "delivery": {
                "telegram_connected": telegram is not None,
                "discord_connected": discord is not None,
                "channels": StrategyDefinition.model_validate(version.schema_json).alerts.channels,
            },
            "permissions": {"owner_user_id": str(user_id), "shareable": False},
            "notice": "Monitoring contract only. No trading execution or outcome guarantee.",
        }
        digest = canonical_hash(payload)
        payload["integrity_hash"] = digest
        verification.contract_hash = digest
        verification.updated_at = datetime.now(UTC)
        await self.session.flush()
        return payload

    async def workspace(
        self, *, user_id: UUID, strategy: Strategy, version: StrategyVersion
    ) -> dict[str, Any]:
        verification = await self.prepare_version(
            user_id=user_id, strategy=strategy, version=version
        )
        statements = list(
            (
                await self.session.scalars(
                    select(StrategyInterpretationStatement)
                    .where(StrategyInterpretationStatement.strategy_version_id == version.id)
                    .order_by(StrategyInterpretationStatement.position)
                )
            ).all()
        )
        cases = list(
            (
                await self.session.scalars(
                    select(StrategyTestCase)
                    .where(
                        StrategyTestCase.user_id == user_id,
                        StrategyTestCase.strategy_id == strategy.id,
                    )
                    .order_by(StrategyTestCase.created_at.desc())
                )
            ).all()
        )
        runs = list(
            (
                await self.session.scalars(
                    select(StrategyTestRun).where(
                        StrategyTestRun.user_id == user_id,
                        StrategyTestRun.strategy_version_id == version.id,
                    )
                )
            ).all()
        )
        run_by_case = {item.test_case_id: item for item in runs}
        versions = list(
            (
                await self.session.scalars(
                    select(StrategyVersion)
                    .where(StrategyVersion.strategy_id == strategy.id)
                    .order_by(StrategyVersion.version_number.desc())
                )
            ).all()
        )
        health = await self.session.scalar(
            select(MonitorHealthSummary).where(
                MonitorHealthSummary.strategy_version_id == version.id
            )
        )
        return {
            "strategy": {
                "id": str(strategy.id),
                "name": strategy.name,
                "status": strategy.status.value,
            },
            "version": _version_payload(version, strategy),
            "versions": [_version_payload(item, strategy) for item in versions],
            "interpretation": [_statement_payload(item) for item in statements],
            "test_cases": [
                _test_case_payload(item, run_by_case.get(item.id)) for item in cases
            ],
            "verification": _verification_payload(verification),
            "health": {
                "technical": health.technical_status if health else "unknown",
                "strategy": health.strategy_status if health else "insufficient_history",
                "causes": [
                    *(health.technical_causes if health else []),
                    *(health.strategy_causes if health else []),
                ],
            },
            "activation_blockers": _activation_blockers(statements, runs, verification),
        }

    async def _verification(
        self, user_id: UUID, strategy: Strategy, version: StrategyVersion
    ) -> StrategyVersionVerification:
        row = await self.session.scalar(
            select(StrategyVersionVerification).where(
                StrategyVersionVerification.strategy_version_id == version.id
            )
        )
        if row is None:
            row = StrategyVersionVerification(
                user_id=user_id,
                strategy_id=strategy.id,
                strategy_version_id=version.id,
                interpretation_status="needs_review",
                tests_status="not_run",
                historical_status="not_run",
                historical_summary={},
                semantic_diff=list(version.semantic_diff or []),
                test_effects={},
                historical_effects={},
                quality_report={},
                updated_at=datetime.now(UTC),
            )
            self.session.add(row)
            await self.session.flush()
        return row

    async def _refresh_test_status(self, user_id: UUID, version: StrategyVersion) -> None:
        strategy = await self.owned_strategy(user_id, version.strategy_id)
        verification = await self._verification(user_id, strategy, version)
        runs = list(
            (
                await self.session.scalars(
                    select(StrategyTestRun).where(
                        StrategyTestRun.user_id == user_id,
                        StrategyTestRun.strategy_version_id == version.id,
                    )
                )
            ).all()
        )
        verification.tests_status = (
            "not_run"
            if not runs
            else "failed"
            if any(item.status in FINAL_TEST_FAILURES for item in runs)
            else "passed"
        )
        previous = await self.session.scalar(
            select(StrategyVersion)
            .where(
                StrategyVersion.strategy_id == version.strategy_id,
                StrategyVersion.version_number < version.version_number,
            )
            .order_by(StrategyVersion.version_number.desc())
            .limit(1)
        )
        changed: list[dict[str, Any]] = []
        if previous:
            prior_runs = list(
                (
                    await self.session.scalars(
                        select(StrategyTestRun).where(
                            StrategyTestRun.user_id == user_id,
                            StrategyTestRun.strategy_version_id == previous.id,
                        )
                    )
                ).all()
            )
            prior_by_case = {item.test_case_id: item for item in prior_runs}
            for run in runs:
                prior = prior_by_case.get(run.test_case_id)
                if prior and prior.actual_result != run.actual_result:
                    changed.append(
                        {
                            "test_case_id": str(run.test_case_id),
                            "before": prior.actual_result,
                            "after": run.actual_result,
                        }
                    )
        verification.test_effects = {"changed_results": changed}
        verification.updated_at = datetime.now(UTC)
        await self.refresh_quality(user_id=user_id, strategy=strategy, version=version)

    def _audit(
        self,
        *,
        user_id: UUID,
        action: str,
        target_type: str,
        target_id: UUID,
        metadata: dict[str, Any],
    ) -> None:
        self.session.add(
            AuditEvent(
                actor_user_id=user_id,
                actor_type="user",
                action=action,
                target_type=target_type,
                target_id=str(target_id),
                metadata_redacted=metadata,
                created_at=datetime.now(UTC),
            )
        )


def semantic_strategy_diff(
    before: StrategyDefinition,
    after: StrategyDefinition,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    # A capability may be used more than once (for example on two timeframes).
    # Rule keys identify those individual strategy nodes; capability keys do not.
    before_rules = {rule.key: rule for rule in condition_rules(before)}
    after_rules = {rule.key: rule for rule in condition_rules(after)}
    for key in sorted(before_rules.keys() | after_rules.keys()):
        left = before_rules.get(key)
        right = after_rules.get(key)
        if left is None and right is not None:
            changes.append(
                {
                    "path": right.label,
                    "operation": "added",
                    "before": None,
                    "after": _rule_summary(right),
                }
            )
            continue
        if right is None and left is not None:
            changes.append(
                {
                    "path": left.label,
                    "operation": "removed",
                    "before": _rule_summary(left),
                    "after": None,
                }
            )
            continue
        if left is None or right is None:
            continue
        fields = {
            "timeframe": (left.timeframe, right.timeframe),
            "operator": (left.comparator.value, right.comparator.value),
            "threshold": (_rule_threshold(left), _rule_threshold(right)),
            "required": (left.required, right.required),
            "parameters": (left.resolved_parameters, right.resolved_parameters),
        }
        for field, (old, new) in fields.items():
            if old != new:
                changes.append(
                    {
                        "path": f"{right.label}.{field}",
                        "operation": "modified",
                        "before": old,
                        "after": new,
                    }
                )
    for field in ("base_timeframe", "supporting_timeframes", "trigger_mode"):
        old = getattr(before, field)
        new = getattr(after, field)
        old_value = old.value if hasattr(old, "value") else old
        new_value = new.value if hasattr(new, "value") else new
        if old_value != new_value:
            changes.append(
                {
                    "path": field,
                    "operation": "modified",
                    "before": old_value,
                    "after": new_value,
                }
            )
    return changes


def semantic_diff_summary(diff: list[dict[str, Any]]) -> str:
    if not diff:
        return "No rule changes."
    summaries: list[str] = []
    for item in diff[:8]:
        path = str(item.get("path") or item.get("field") or item.get("section") or "Strategy")
        label = path.replace("_", " ").replace(".", " > ").strip().title()
        before = item.get("before", item.get("left"))
        after = item.get("after", item.get("right"))
        operation = item.get("operation") or item.get("change")
        if operation == "added" or before is None:
            summaries.append(f"{label} added: {_brief(after)}.")
        elif operation == "removed" or after is None:
            summaries.append(f"{label} removed.")
        else:
            summaries.append(f"{label} changed from {_brief(before)} to {_brief(after)}.")
    return " ".join(summaries)


def _rule_threshold(rule: ConditionRule) -> Any:
    if rule.right is None:
        return None
    payload = rule.right.model_dump(mode="json")
    return payload.get("value", payload)


def _rule_summary(rule: ConditionRule) -> str:
    threshold = _rule_threshold(rule)
    suffix = f" {threshold}" if threshold is not None else ""
    requirement = "required" if rule.required else "optional"
    return (
        f"{rule.label} on {rule.timeframe} "
        f"{rule.comparator.value}{suffix} ({requirement})"
    )


def _prompt_phrases(text: str) -> list[str]:
    return [
        item.strip(" -\t")
        for item in re.split(r"(?:[\n;]+|(?<=[.!?])\s+|\s+\b(?:and then|then)\b\s+)", text)
        if len(item.strip(" -\t")) >= 2
    ][:100]


def _rule_matches_phrase(rule: ConditionRule, phrase: str) -> bool:
    source = (rule.source_fragment or "").casefold().strip()
    candidate = phrase.casefold()
    if source and (source in candidate or candidate in source):
        return True
    rule_tokens = set(re.findall(r"[a-z0-9]+", " ".join((rule.label, source)).casefold()))
    phrase_tokens = set(re.findall(r"[a-z0-9]+", candidate))
    return len(rule_tokens & phrase_tokens) >= min(2, max(1, len(rule_tokens) // 2))


def _interpretation_status(
    version: StrategyVersion, phrase: str, rules: list[ConditionRule]
) -> str:
    issue_text = " ".join(
        str(item.get("message") or item.get("source_fragment") or "")
        for item in version.ambiguities
    ).casefold()
    unsupported_text = " ".join(
        str(item.get("message") or item.get("source_fragment") or "")
        for item in version.unsupported_conditions
    ).casefold()
    lowered = phrase.casefold()
    if lowered and lowered in unsupported_text:
        return "unsupported"
    if lowered and lowered in issue_text:
        return "ambiguous"
    if any((rule.availability or "available") != "available" for rule in rules):
        return "unsupported"
    if any(rule.confidence is not None and rule.confidence < 0.6 for rule in rules):
        return "ambiguous"
    if any(rule.ai_interpreted or (rule.confidence or 1) < 0.85 for rule in rules):
        return "assumed"
    return "confirmed"


def _rule_mechanics(rule: ConditionRule, definition: StrategyDefinition) -> dict[str, Any]:
    right = rule.right.model_dump(mode="json") if rule.right else None
    threshold = right.get("value") if isinstance(right, dict) else None
    return {
        "rule_key": rule.key,
        "capability_key": rule.capability_key,
        "interpretation": rule.label,
        "timeframe": rule.timeframe,
        "condition": rule.left.name,
        "operator": rule.comparator.value,
        "threshold": threshold,
        "data_source": (
            ", ".join(rule.required_data)
            if rule.required_data
            else f"{definition.universe.exchange} spot OHLCV"
        ),
        "candle_close_required": definition.trigger_mode.value == "candle_close",
        "required": rule.required,
        "confidence": rule.confidence,
    }


def _matching_assumptions(assumptions: list[str], phrase: str) -> list[str]:
    tokens = set(re.findall(r"[a-z0-9]+", phrase.casefold()))
    matched = [
        item
        for item in assumptions
        if tokens & set(re.findall(r"[a-z0-9]+", item.casefold()))
    ]
    return matched or list(assumptions if len(assumptions) == 1 else [])


def _actual_test_result(evaluation: EvaluationResult) -> str:
    if evaluation.outcome == ScanOutcome.CONFIRMED:
        return "should_trigger"
    if evaluation.outcome in {ScanOutcome.NEAR_MISS, ScanOutcome.FORMING}:
        return "near_match"
    return "should_not_trigger"


def _test_matches(expected: str, actual: str) -> bool:
    if expected == "should_not_trigger":
        return actual != "should_trigger"
    return expected == actual


def _test_mismatch(case: StrategyTestCase, evaluation: EvaluationResult, actual: str) -> str:
    failed = [
        item.name
        for item in evaluation.conditions
        if item.mandatory and item.state != EvaluationState.PASSED
    ]
    if case.expected_result == "should_trigger" and failed:
        return f"Expected a trigger, but required checks did not pass: {', '.join(failed)}."
    return f"Expected {case.expected_result.replace('_', ' ')}, got {actual.replace('_', ' ')}."


def _historical_summary(metrics: dict[str, Any], setups: list[dict[str, Any]]) -> dict[str, Any]:
    evaluations = int(metrics.get("evaluations") or 0)
    matches = int(metrics.get("confirmed_setups") or 0)
    near = int(metrics.get("near_miss_setups") or 0)
    match_rate = matches / evaluations if evaluations else 0.0
    timestamps = [
        str(timestamp)
        for item in setups
        if (timestamp := item.get("timestamp")) is not None
    ]
    days = 1.0
    if len(timestamps) >= 2:
        with suppress(ValueError):
            days = max(
                1.0,
                (
                    datetime.fromisoformat(max(timestamps))
                    - datetime.fromisoformat(min(timestamps))
                ).total_seconds()
                / 86_400,
            )
    return {
        "status": metrics.get("status"),
        "evaluations": evaluations,
        "matches": matches,
        "near_matches": near,
        "invalidated": int(metrics.get("invalidated_setups") or 0),
        "non_matches": int(
            metrics.get("non_match_setups")
            or max(0, evaluations - matches - near)
        ),
        "match_rate": round(match_rate, 6),
        "breadth": (
            "unusually_broad"
            if match_rate >= 0.25
            else "unusually_narrow"
            if evaluations >= 100 and match_rate <= 0.001
            else "balanced_or_insufficient_sample"
        ),
        "most_common_failed_condition": metrics.get("most_common_failed_condition"),
        "failed_condition_counts": metrics.get("failed_condition_counts") or {},
        "condition_statistics": metrics.get("condition_statistics") or {},
        "symbols": metrics.get("symbols") or [],
        "unavailable_symbols": metrics.get("unavailable_symbols") or [],
        "observation_days": round(days, 3),
        "estimated_frequency": {
            "per_day": round(matches / days, 3),
            "per_week": round(matches / days * 7, 3),
            "per_month": round(matches / days * 30, 3),
        },
        "examples": setups[:20],
        "examples_by_outcome": metrics.get("examples_by_outcome") or {},
        "chart": metrics.get("chart") or {},
        "notice": "Historical monitoring preview; not a future-performance estimate.",
    }


def _scan_category(scan: ScanResult) -> str:
    if scan.exclusion_reason:
        return "universe_or_exclusion"
    if scan.error_code or scan.outcome == ScanOutcome.ERROR:
        return "data_or_infrastructure"
    if scan.outcome == ScanOutcome.CONFIRMED:
        return "notification_or_suppression"
    return "market_conditions"


def _scan_conclusion(scan: ScanResult) -> str:
    if scan.exclusion_reason:
        return f"The market was excluded: {scan.exclusion_reason}."
    if scan.error_code:
        return f"The evaluation could not complete: {scan.error_code}."
    if scan.outcome == ScanOutcome.CONFIRMED:
        return "The rules confirmed; notification and suppression evidence must be checked."
    if scan.outcome == ScanOutcome.NEAR_MISS:
        return "The setup was close, but at least one required condition did not pass."
    return "The required market conditions were not complete at the selected time."


def _suppression_category(reason: str) -> str:
    normalized = reason.casefold()
    if "cooldown" in normalized:
        return "cooldown"
    if "duplicate" in normalized or "dedup" in normalized:
        return "duplicate_suppression"
    if any(token in normalized for token in ("excluded", "universe", "muted", "schedule")):
        return "exclusion_or_schedule"
    return "notification_suppression"


def _suppression_conclusion(reason: str) -> str:
    readable = reason.replace("_", " ").strip()
    return f"The setup confirmed, but notification rules suppressed delivery: {readable}."


def _condition_result_payload(item: SetupConditionResult) -> dict[str, Any]:
    return {
        "condition_key": item.condition_key,
        "status": item.outcome.value,
        "actual": item.actual_value,
        "required": item.required_value,
        "distance": float(item.distance_to_pass) if item.distance_to_pass is not None else None,
        "candle_timestamp": (
            ensure_aware(item.candle_timestamp).isoformat()
            if item.candle_timestamp
            else None
        ),
        "data_freshness_ms": item.data_freshness_ms,
        "explanation_code": item.explanation_code,
    }


def _dimension(ok: bool, explanation: str, override: str | None = None) -> dict[str, str]:
    return {"status": override or ("ready" if ok else "blocked"), "explanation": explanation}


def _condition_influence(statistics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    observed = [
        (name, values)
        for name, values in statistics.items()
        if int(values.get("evaluations") or 0) > 0
    ]
    if not observed:
        return {
            "evidence_available": False,
            "most": None,
            "least": None,
            "method": "Historical condition evidence has not been collected.",
        }
    ranked = sorted(
        observed,
        key=lambda item: (
            int(item[1].get("failures") or 0),
            -float(item[1].get("pass_rate") or 0),
        ),
        reverse=True,
    )

    def payload(item: tuple[str, dict[str, Any]]) -> dict[str, Any]:
        name, values = item
        return {
            "condition": name,
            "evaluations": int(values.get("evaluations") or 0),
            "failures": int(values.get("failures") or 0),
            "pass_rate": values.get("pass_rate"),
        }

    return {
        "evidence_available": True,
        "most": payload(ranked[0]),
        "least": payload(ranked[-1]),
        "method": (
            "Influence is ranked by observed condition failures in this historical preview; "
            "it is not an outcome-quality or profit measure."
        ),
    }


def _statement_payload(item: StrategyInterpretationStatement) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "original_phrase": item.original_phrase,
        "structured_interpretation": item.structured_interpretation,
        "rule_keys": item.rule_keys,
        "mechanics": item.mechanics,
        "assumptions": item.assumptions,
        "status": item.status,
        "resolution_status": item.resolution_status,
        "resolution_text": item.resolution_text,
    }


def _test_case_payload(case: StrategyTestCase, run: StrategyTestRun | None) -> dict[str, Any]:
    return {
        "id": str(case.id),
        "title": case.title,
        "case_type": case.case_type,
        "expected_result": case.expected_result,
        "exchange": case.exchange,
        "symbol": case.symbol,
        "timeframe": case.timeframe,
        "evaluation_time": case.evaluation_time,
        "notes": case.notes,
        "active": case.active,
        "latest_run": (
            {
                "status": run.status,
                "actual_result": run.actual_result,
                "condition_results": run.condition_results,
                "mismatch_reason": run.mismatch_reason,
                "evidence": run.evidence,
                "run_at": run.run_at,
            }
            if run
            else None
        ),
    }


def _verification_payload(item: StrategyVersionVerification) -> dict[str, Any]:
    return {
        "interpretation_status": item.interpretation_status,
        "tests_status": item.tests_status,
        "historical_status": item.historical_status,
        "historical_job_id": str(item.historical_job_id) if item.historical_job_id else None,
        "historical_summary": item.historical_summary,
        "semantic_diff": item.semantic_diff,
        "test_effects": item.test_effects,
        "historical_effects": item.historical_effects,
        "quality_report": item.quality_report,
        "contract_hash": item.contract_hash,
    }


def _version_payload(version: StrategyVersion, strategy: Strategy) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "number": version.version_number,
        "status": version.status.value,
        "schema_hash": version.schema_hash,
        "created_at": version.created_at,
        "approved_at": version.approved_at,
        "active": strategy.active_version_id == version.id,
        "change_summary": version.change_summary,
        "semantic_diff": version.semantic_diff,
        "parent_version_id": str(version.parent_version_id) if version.parent_version_id else None,
        "restored_from_version_id": (
            str(version.restored_from_version_id) if version.restored_from_version_id else None
        ),
    }


def _activation_blockers(
    statements: list[StrategyInterpretationStatement],
    runs: list[StrategyTestRun],
    verification: StrategyVersionVerification,
) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    unresolved = [
        item
        for item in statements
        if item.status in CRITICAL_INTERPRETATION_STATES
        or (item.status == "assumed" and item.resolution_status == "unresolved")
    ]
    if unresolved or verification.interpretation_status != "approved":
        blockers.append(
            {"code": "interpretation_review", "message": "Approve the interpretation audit."}
        )
    if any(item.status in FINAL_TEST_FAILURES for item in runs):
        blockers.append(
            {"code": "example_regression", "message": "Resolve failing saved examples."}
        )
    return blockers


def _symbol(value: str) -> str:
    return value.strip().upper().replace("-", "/").split(":", 1)[0]


def _brief(value: Any) -> str:
    if isinstance(value, (dict, list)):
        encoded = json.dumps(value, sort_keys=True, default=str)
        return encoded[:100] + ("…" if len(encoded) > 100 else "")
    return str(value)
