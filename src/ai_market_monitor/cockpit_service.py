from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import (
    Alert,
    AlertFrequencyForecast,
    AlertInboxItem,
    ConditionBottleneckAggregate,
    DashboardNotification,
    EdgeHealthSnapshot,
    MissedMoveAnalysis,
    OutcomeReview,
    ScanJob,
    ScanResult,
    SetupConditionResult,
    SetupInstance,
    SetupLifecycleEvent,
    SetupReplayJob,
    SetupReplayResult,
    Strategy,
    StrategyCondition,
    StrategyDecayEvent,
    StrategyExperiment,
    StrategySuggestion,
    StrategyValidationRecord,
    StrategyVersion,
    UniverseOptimizationSnapshot,
    UserFeedback,
    UserStrategyPreference,
)
from ai_market_monitor.db.models.enums import (
    AlertType,
    ConditionOutcome,
    ScanOutcome,
    SetupLifecycleState,
    StrategyStatus,
    StrategyVersionStatus,
)
from ai_market_monitor.engine.data_freshness import measure_freshness
from ai_market_monitor.engine.market_filters import is_leveraged_token, is_stablecoin_base
from ai_market_monitor.market_context import MarketRegimeAnalyzer
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.interfaces import MarketDataProvider
from ai_market_monitor.services.product_language import (
    monitor_issue_words,
    monitor_working_words,
)
from ai_market_monitor.strategy_cockpit import (
    forecast_from_structure,
    health_status,
    schema_diff,
    suggest_schema_adjustment,
    validate_strategy_conflicts,
)

POSITIVE_FEEDBACK = {"good_alert", "correct_setup", "entered", "good_idea_weak_proof"}
NEGATIVE_FEEDBACK = {
    "too_early",
    "too_late",
    "false_alert",
    "incorrect_match",
    "too_many_alerts",
    "not_relevant",
    "bad_market_context",
}
TERMINAL_STATES = {
    SetupLifecycleState.INVALIDATED,
    SetupLifecycleState.EXPIRED,
    SetupLifecycleState.ENTRY_MISSED,
    SetupLifecycleState.ENTRY_ZONE_MISSED,
    SetupLifecycleState.TARGET_REACHED,
    SetupLifecycleState.STOP_REACHED,
    SetupLifecycleState.STOP_LEVEL_REACHED,
    SetupLifecycleState.MANUALLY_CLOSED,
    SetupLifecycleState.COMPLETED,
    SetupLifecycleState.CLOSED,
}


class StrategyCockpitService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def latest_version(self, strategy: Strategy) -> StrategyVersion | None:
        if strategy.active_version_id:
            active = await self.session.get(StrategyVersion, strategy.active_version_id)
            if active is not None:
                return active
        return await self.session.scalar(
            select(StrategyVersion)
            .where(StrategyVersion.strategy_id == strategy.id)
            .order_by(StrategyVersion.version_number.desc())
            .limit(1)
        )

    async def condition_bottlenecks(
        self,
        strategy: Strategy,
        *,
        limit: int = 500,
        persist: bool = True,
    ) -> dict[str, Any]:
        versions = (
            await self.session.scalars(
                select(StrategyVersion.id).where(StrategyVersion.strategy_id == strategy.id)
            )
        ).all()
        if not versions:
            return {
                "strategy_id": str(strategy.id),
                "sample_size": 0,
                "conditions": [],
                "main_bottleneck": None,
                "message": "No strategy version or condition evidence exists yet.",
            }
        rows = (
            await self.session.execute(
                select(SetupConditionResult, ScanResult.completion_score)
                .join(ScanResult, ScanResult.id == SetupConditionResult.scan_result_id)
                .where(SetupConditionResult.strategy_condition_id.is_not(None))
                .where(ScanResult.strategy_version_id.in_(versions))
                .order_by(SetupConditionResult.evaluated_at.desc())
                .limit(limit)
            )
        ).all()
        labels = {
            row.condition_key: row.label
            for row in (await self.session.scalars(select_strategy_conditions(strategy.id))).all()
        }
        grouped: dict[str, list[tuple[SetupConditionResult, Decimal]]] = defaultdict(list)
        for result, completion_score in rows:
            grouped[result.condition_key].append((result, completion_score))
        calculated_at = datetime.now(UTC)
        conditions: list[dict[str, Any]] = []
        for key, values in grouped.items():
            outcomes = Counter(result.outcome.value for result, _ in values)
            sample_count = len(values)
            blocking_count = sum(
                1
                for result, score in values
                if result.outcome
                in {
                    ConditionOutcome.FAILED,
                    ConditionOutcome.PENDING,
                    ConditionOutcome.UNAVAILABLE,
                    ConditionOutcome.ERROR,
                }
                and float(score or 0) >= 60
            )
            pass_rate = outcomes["passed"] / sample_count * 100 if sample_count else 0
            blocking_rate = blocking_count / sample_count * 100 if sample_count else 0
            average_proximity = (
                sum(float(result.contribution_score or 0) for result, _ in values) / sample_count
            )
            payload = {
                "condition_key": key,
                "condition_label": labels.get(key, key.replace("_", " ").title()),
                "sample_count": sample_count,
                "passed_count": outcomes["passed"],
                "failed_count": outcomes["failed"],
                "pending_count": outcomes["pending"],
                "unavailable_count": outcomes["unavailable"],
                "error_count": outcomes["error"],
                "pass_rate": round(pass_rate, 2),
                "blocking_rate": round(blocking_rate, 2),
                "average_proximity": round(average_proximity, 2),
                "suggested_fix": _bottleneck_suggestion(key, pass_rate, outcomes),
            }
            conditions.append(payload)
            if persist:
                latest_version_id = values[0][0].strategy_condition_id
                version_id = await self._condition_version_id(latest_version_id)
                if version_id is not None:
                    self.session.add(
                        ConditionBottleneckAggregate(
                            strategy_id=strategy.id,
                            strategy_version_id=version_id,
                            condition_key=key,
                            condition_label=payload["condition_label"],
                            sample_count=sample_count,
                            passed_count=outcomes["passed"],
                            failed_count=outcomes["failed"],
                            pending_count=outcomes["pending"],
                            unavailable_count=outcomes["unavailable"],
                            error_count=outcomes["error"],
                            blocking_count=blocking_count,
                            pass_rate=Decimal(str(pass_rate)),
                            blocking_rate=Decimal(str(blocking_rate)),
                            details={"average_proximity": average_proximity},
                            window_started_at=min(result.evaluated_at for result, _ in values),
                            window_ended_at=max(result.evaluated_at for result, _ in values),
                            calculated_at=calculated_at,
                        )
                    )
        conditions.sort(key=lambda item: (-item["blocking_rate"], item["pass_rate"]))
        main = conditions[0] if conditions else None
        return {
            "strategy_id": str(strategy.id),
            "sample_size": len(rows),
            "conditions": conditions,
            "main_bottleneck": main,
            "message": (
                f"Main blocker: {main['condition_label']}."
                if main
                else "More scan history is needed to identify a bottleneck."
            ),
            "calculated_at": calculated_at,
        }

    async def edge_health(
        self,
        strategy: Strategy,
        *,
        persist: bool = True,
        provider: MarketDataProvider | None = None,
    ) -> dict[str, Any]:
        version = await self.latest_version(strategy)
        version_ids = (
            await self.session.scalars(
                select(StrategyVersion.id).where(StrategyVersion.strategy_id == strategy.id)
            )
        ).all()
        if not version_ids:
            components = [
                _component(
                    "Scan evidence",
                    0,
                    20,
                    "missing",
                    "Activate an approved version to begin collecting evidence.",
                )
            ]
            return await self._health_payload(strategy, version, components, 0, persist)
        now = datetime.now(UTC)
        since = now - timedelta(days=30)
        scans = (
            await self.session.scalars(
                select(ScanResult).where(
                    ScanResult.strategy_version_id.in_(version_ids),
                    ScanResult.evaluated_at >= since,
                )
            )
        ).all()
        alerts = (
            await self.session.scalars(
                select(Alert).where(
                    Alert.strategy_version_id.in_(version_ids),
                    Alert.created_at >= since,
                )
            )
        ).all()
        setups = (
            await self.session.scalars(
                select(SetupInstance).where(
                    SetupInstance.strategy_version_id.in_(version_ids),
                    SetupInstance.last_evaluated_at >= since,
                )
            )
        ).all()
        feedback = (
            await self.session.scalars(
                select(UserFeedback)
                .join(Alert, Alert.id == UserFeedback.alert_id)
                .where(
                    Alert.strategy_version_id.in_(version_ids),
                    UserFeedback.created_at >= since,
                )
            )
        ).all()
        bottlenecks = await self.condition_bottlenecks(strategy, persist=persist)
        regime = await self._market_regime_context(
            strategy,
            version,
            provider=provider,
        )
        components = self._health_components(
            scans,
            alerts,
            setups,
            feedback,
            bottlenecks,
            regime,
            now,
        )
        sample_size = len(scans)
        return await self._health_payload(strategy, version, components, sample_size, persist)

    async def alert_frequency_forecast(self, strategy: Strategy) -> dict[str, Any]:
        version = await self.latest_version(strategy)
        if version is None:
            raise ValueError("Strategy version not found")
        definition = StrategyDefinition.model_validate(version.schema_json)
        first_evaluation, last_evaluation, matches, symbol_count = (
            await self.session.execute(
                select(
                    func.min(ScanResult.evaluated_at),
                    func.max(ScanResult.evaluated_at),
                    func.sum(
                        case(
                            (ScanResult.outcome == ScanOutcome.CONFIRMED, 1),
                            else_=0,
                        )
                    ),
                    func.count(func.distinct(ScanResult.symbol)),
                ).where(ScanResult.strategy_version_id == version.id)
            )
        ).one()
        observation_days = 0.0
        if first_evaluation and last_evaluation:
            observation_days = max(
                1 / 24,
                (last_evaluation - first_evaluation).total_seconds() / 86_400,
            )
        result = forecast_from_structure(
            definition,
            historical_matches=int(matches or 0),
            observation_days=observation_days,
            symbols_observed=int(symbol_count or 0),
        )
        forecast = AlertFrequencyForecast(
            strategy_id=strategy.id,
            strategy_version_id=version.id,
            estimated_min_per_week=Decimal(str(result["estimated_min_per_week"])),
            estimated_max_per_week=Decimal(str(result["estimated_max_per_week"])),
            classification=result["classification"],
            confidence=result["confidence"],
            inputs=result["inputs"],
            warnings=result["warnings"],
            suggestions=result["suggestions"],
            calculated_at=datetime.now(UTC),
        )
        self.session.add(forecast)
        await self.session.flush()
        return {"id": str(forecast.id), **result, "calculated_at": forecast.calculated_at}

    async def validate_definition(
        self,
        *,
        user_id: UUID,
        definition: StrategyDefinition,
        strategy_id: UUID | None = None,
        strategy_version_id: UUID | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        findings = [finding.to_dict() for finding in validate_strategy_conflicts(definition)]
        counts = Counter(item["severity"] for item in findings)
        blocking = counts["critical"] > 0
        if persist:
            record = StrategyValidationRecord(
                user_id=user_id,
                strategy_id=strategy_id,
                strategy_version_id=strategy_version_id,
                schema_hash=definition.canonical_hash(),
                blocking=blocking,
                critical_count=counts["critical"],
                warning_count=counts["warning"],
                info_count=counts["info"],
                findings=findings,
                created_at=datetime.now(UTC),
            )
            self.session.add(record)
            await self.session.flush()
        return {
            "schema_hash": definition.canonical_hash(),
            "blocking": blocking,
            "critical_count": counts["critical"],
            "warning_count": counts["warning"],
            "info_count": counts["info"],
            "findings": findings,
        }

    async def preview_universe(
        self,
        *,
        user_id: UUID,
        strategy: Strategy,
        provider: MarketDataProvider,
        manual_include: list[str] | None = None,
        manual_exclude: list[str] | None = None,
        include_categories: list[str] | None = None,
        exclude_categories: list[str] | None = None,
        rank_by: str = "quote_volume",
        result_limit: int | None = None,
    ) -> dict[str, Any]:
        version = await self.latest_version(strategy)
        if version is None:
            raise ValueError("Strategy version not found")
        definition = StrategyDefinition.model_validate(version.schema_json)
        universe = definition.universe
        symbols = await provider.list_symbols(universe.exchange, universe.quote_currencies)
        include_override = {_canonical_symbol(item) for item in manual_include or []}
        exclude_override = {
            _canonical_symbol(item) for item in [*universe.exclude_symbols, *(manual_exclude or [])]
        }
        allowlist = {
            _canonical_symbol(item) for item in (manual_include or universe.include_symbols)
        }
        included: list[str] = []
        excluded: list[dict[str, str]] = []
        for raw_symbol in symbols:
            symbol = _canonical_symbol(raw_symbol)
            base = symbol.split("/", 1)[0]
            reason = None
            if allowlist and symbol not in allowlist:
                reason = "not_in_allowlist"
            elif symbol in exclude_override:
                reason = "manual_blocklist"
            # These two questions are answered by `engine/market_filters`, the same
            # module the scanner asks. This screen used to carry its own shorter
            # answers: a stablecoin list missing BUSD and USDP, and a leveraged-token
            # test missing "5L" and "5S". So a five-times leveraged token was listed
            # here as a coin the monitor would watch, and then dropped by every scan.
            elif universe.exclude_stablecoins and is_stablecoin_base(base):
                reason = "stablecoin_base"
            elif universe.exclude_leveraged_tokens and is_leveraged_token(base):
                reason = "leveraged_token"
            if symbol in include_override:
                reason = None
            if reason:
                excluded.append({"symbol": symbol, "reason": reason})
            elif symbol not in included:
                included.append(symbol)

        metadata: dict[str, dict[str, Any]] = {}
        metadata_error: str | None = None
        metadata_loader = getattr(provider, "fetch_universe_metadata", None)
        if callable(metadata_loader) and included:
            try:
                metadata = await metadata_loader(
                    universe.exchange,
                    included,
                    include_listing_dates=universe.min_listing_age_days is not None,
                )
            except Exception as exc:
                metadata_error = type(exc).__name__
        include_category_set = {
            item.strip().lower() for item in include_categories or [] if item.strip()
        }
        exclude_category_set = {
            item.strip().lower() for item in exclude_categories or [] if item.strip()
        }
        filtered: list[str] = []
        for symbol in included:
            values = metadata.get(symbol, {})
            reason = None
            quote_volume = _number_or_none(values.get("quote_volume_24h"))
            spread_bps = _number_or_none(values.get("spread_bps"))
            market_cap = _number_or_none(values.get("market_cap"))
            listed_at = _datetime_or_none(values.get("listed_at"))
            category = str(values.get("category") or "").strip().lower()
            if universe.min_quote_volume_24h is not None:
                if quote_volume is None:
                    reason = "quote_volume_unavailable"
                elif quote_volume < universe.min_quote_volume_24h:
                    reason = "quote_volume_below_minimum"
            if reason is None and universe.max_spread_bps is not None:
                if spread_bps is None:
                    reason = "spread_unavailable"
                elif spread_bps > universe.max_spread_bps:
                    reason = "spread_above_maximum"
            if reason is None and universe.min_listing_age_days is not None:
                if listed_at is None:
                    reason = "listing_age_unavailable"
                elif (
                    datetime.now(UTC) - listed_at
                ).total_seconds() / 86_400 < universe.min_listing_age_days:
                    reason = "listing_too_new"
            if reason is None and universe.min_market_cap is not None:
                if market_cap is None:
                    reason = "market_cap_unavailable"
                elif market_cap < universe.min_market_cap:
                    reason = "market_cap_below_minimum"
            if reason is None and values.get("data_quality_ok") is False:
                reason = "data_quality_failed"
            if reason is None and include_category_set:
                reason = (
                    "category_unavailable"
                    if not category
                    else "category_not_selected"
                    if category not in include_category_set
                    else None
                )
            if reason is None and category and category in exclude_category_set:
                reason = "category_excluded"
            if reason:
                excluded.append({"symbol": symbol, "reason": reason})
            else:
                filtered.append(symbol)

        def ranking_value(symbol: str) -> tuple[float, str]:
            values = metadata.get(symbol, {})
            if rank_by == "relative_strength":
                return (
                    -(_number_or_none(values.get("relative_strength_btc")) or float("-inf")),
                    symbol,
                )
            if rank_by == "lowest_spread":
                return (
                    _number_or_none(values.get("spread_bps")) or float("inf"),
                    symbol,
                )
            return (
                -(_number_or_none(values.get("quote_volume_24h")) or float("-inf")),
                symbol,
            )

        included = sorted(filtered, key=ranking_value)
        effective_limit = result_limit or universe.max_symbols
        if effective_limit:
            overflow = included[effective_limit:]
            included = included[:effective_limit]
            excluded.extend(
                {"symbol": symbol, "reason": "plan_or_rule_limit"} for symbol in overflow
            )
        reason_counts = Counter(item["reason"] for item in excluded)
        unavailable_reasons = {
            reason: count
            for reason, count in reason_counts.items()
            if reason.endswith("_unavailable")
        }
        summary = {
            "provider_symbols": len({_canonical_symbol(item) for item in symbols}),
            "included_count": len(included),
            "excluded_count": len(excluded),
            "exclusion_reasons": dict(reason_counts),
            "rank_by": rank_by,
            "metadata_symbols": len(metadata),
            "metadata_error": metadata_error,
            "unavailable_metadata": unavailable_reasons,
            "deferred_filters": [],
            "note": (
                "Static and provider-backed liquidity, spread, listing-age, category, "
                "market-cap, data-quality, and ranking rules were applied. Missing required "
                "metadata is reported as an exclusion rather than guessed."
            ),
        }
        snapshot = UniverseOptimizationSnapshot(
            user_id=user_id,
            strategy_id=strategy.id,
            strategy_version_id=version.id,
            rules=universe.model_dump(mode="json"),
            included_symbols=included,
            excluded_symbols=excluded,
            summary=summary,
            source="market_provider",
            created_at=datetime.now(UTC),
        )
        self.session.add(snapshot)
        await self.session.flush()
        return {
            "id": str(snapshot.id),
            "rules": snapshot.rules,
            "included_symbols": included,
            "included_metadata": {symbol: metadata.get(symbol, {}) for symbol in included},
            "excluded_symbols": excluded,
            "summary": summary,
        }

    async def submit_feedback(
        self,
        *,
        user_id: UUID,
        alert: Alert,
        feedback_type: str,
        source: str,
        comment: str | None = None,
    ) -> UserFeedback:
        feedback = UserFeedback(
            user_id=user_id,
            alert_id=alert.id,
            setup_instance_id=alert.setup_instance_id,
            feedback_type=feedback_type,
            comment=comment,
            source=source,
            metadata_json={
                "strategy_version_id": (
                    str(alert.strategy_version_id) if alert.strategy_version_id else None
                ),
                "monitoring_feedback": True,
            },
        )
        self.session.add(feedback)
        await self.session.flush()
        await self._maybe_create_feedback_suggestion(alert, feedback_type)
        return feedback

    async def compare_versions(
        self,
        left: StrategyVersion,
        right: StrategyVersion,
        *,
        experiment_id: UUID | None = None,
    ) -> dict[str, Any]:
        left_metrics = await self._version_metrics(left.id, experiment_id=experiment_id)
        right_metrics = await self._version_metrics(right.id, experiment_id=experiment_id)
        return {
            "left": left_metrics,
            "right": right_metrics,
            "schema_diff": schema_diff(
                StrategyDefinition.model_validate(left.schema_json),
                StrategyDefinition.model_validate(right.schema_json),
            ),
            "comparison_notes": _comparison_notes(left_metrics, right_metrics),
            "non_advisory_notice": (
                "This compares monitor behavior, not profitability or trade outcomes."
            ),
            "evidence_scope": (
                "scheduled_experiment" if experiment_id is not None else "stored_version_history"
            ),
        }

    async def create_experiment(
        self,
        *,
        user_id: UUID,
        strategy: Strategy,
        version_ids: list[UUID],
        name: str,
        mode: str,
    ) -> StrategyExperiment:
        versions = (
            await self.session.scalars(
                select(StrategyVersion).where(
                    StrategyVersion.strategy_id == strategy.id,
                    StrategyVersion.id.in_(version_ids),
                )
            )
        ).all()
        if len(versions) != 2 or len(set(version_ids)) != 2:
            raise ValueError("Exactly two distinct owned strategy versions are required")
        for version in versions:
            definition = StrategyDefinition.model_validate(version.schema_json)
            if definition.canonical_hash() != version.schema_hash:
                raise ValueError("A selected strategy version has an invalid schema hash")
        if mode == "live_monitor":
            if strategy.status != StrategyStatus.ACTIVE:
                raise ValueError("Live experiments require an active monitor")
            for version in versions:
                if (
                    version.status
                    not in {
                        StrategyVersionStatus.APPROVED,
                        StrategyVersionStatus.READY,
                        StrategyVersionStatus.ACTIVE,
                    }
                    or version.approved_at is None
                    or version.approved_schema_hash != version.schema_hash
                ):
                    raise ValueError("Every live experiment version must be explicitly approved")
        experiment = StrategyExperiment(
            user_id=user_id,
            strategy_id=strategy.id,
            name=name,
            status="running",
            mode=mode,
            version_ids=[str(version.id) for version in versions],
            comparison={},
            started_at=datetime.now(UTC),
        )
        self.session.add(experiment)
        await self.session.flush()
        await self.refresh_experiment(experiment)
        return experiment

    async def refresh_experiment(
        self,
        experiment: StrategyExperiment,
    ) -> StrategyExperiment:
        versions = (
            await self.session.scalars(
                select(StrategyVersion)
                .where(
                    StrategyVersion.strategy_id == experiment.strategy_id,
                    StrategyVersion.id.in_([UUID(value) for value in experiment.version_ids]),
                )
                .order_by(StrategyVersion.version_number.asc())
            )
        ).all()
        if len(versions) < 2:
            experiment.status = "failed"
            experiment.ended_at = datetime.now(UTC)
            experiment.comparison = {
                "error": "experiment_versions_unavailable",
                "non_advisory_notice": (
                    "The experiment stopped because fewer than two versions remain available."
                ),
            }
            await self.session.flush()
            return experiment
        comparison = await self.compare_versions(
            versions[0],
            versions[1],
            experiment_id=experiment.id,
        )
        scheduled_jobs = (
            await self.session.scalars(
                select(ScanJob).where(
                    ScanJob.strategy_version_id.in_([version.id for version in versions]),
                    ScanJob.job_type.in_(["experiment_dry_run", "experiment_live"]),
                )
            )
        ).all()
        experiment_jobs = [
            job
            for job in scheduled_jobs
            if str((job.metrics or {}).get("experiment_id")) == str(experiment.id)
        ]
        comparison["scheduled_jobs"] = len(experiment_jobs)
        comparison["completed_jobs"] = sum(
            job.status.value in {"succeeded", "partial"} for job in experiment_jobs
        )
        comparison["last_refreshed_at"] = datetime.now(UTC).isoformat()
        experiment.comparison = comparison
        await self.session.flush()
        return experiment

    async def promote_experiment_version(
        self,
        *,
        experiment: StrategyExperiment,
        version: StrategyVersion,
    ) -> None:
        if str(version.id) not in experiment.version_ids:
            raise ValueError("Version is not part of this experiment")
        strategy = await self.session.get(Strategy, experiment.strategy_id)
        if strategy is None:
            raise ValueError("Strategy not found")
        experiment.promoted_version_id = version.id
        experiment.status = "completed"
        experiment.ended_at = datetime.now(UTC)
        await self.session.flush()

    async def generate_suggestion(
        self,
        *,
        user_id: UUID,
        strategy: Strategy,
        action: str,
        narrator: Any | None = None,
    ) -> StrategySuggestion:
        version = await self.latest_version(strategy)
        if version is None:
            raise ValueError("Strategy version not found")
        bottlenecks = await self.condition_bottlenecks(strategy, persist=False)
        main = bottlenecks.get("main_bottleneck") or {}
        before = StrategyDefinition.model_validate(version.schema_json)
        proposed, reason = suggest_schema_adjustment(
            before,
            action,
            bottleneck_key=main.get("condition_key"),
        )
        diff = schema_diff(before, proposed)
        outcome_rows = list(
            (
                await self.session.scalars(
                    select(OutcomeReview).where(
                        OutcomeReview.user_id == user_id,
                        OutcomeReview.strategy_id == strategy.id,
                        OutcomeReview.status.in_(["reviewed", "reviewed_without_market_path"]),
                    )
                )
            ).all()
        )
        classifications = Counter(
            item.classification for item in outcome_rows if item.classification
        )
        sample_count = len(outcome_rows)
        outcome_evidence = {
            "sample_count": sample_count,
            "classifications": dict(classifications),
            "source": "user_defined_outcome_reviews",
            "automatic_profit_labels": False,
        }
        confidence = "medium" if sample_count >= 20 else "low"
        limitations = [
            "A proposed rule has not been approved or applied to live monitoring.",
            "Outcome labels are user-defined and do not establish future performance.",
        ]
        if sample_count < 20:
            limitations.append("Fewer than 20 user-reviewed outcomes are available.")
        source = "deterministic"
        if narrator is not None:
            narrated = await narrator.narrate(
                action=action,
                deterministic_reason=reason,
                diff=diff,
                bottleneck=main,
            )
            if narrated:
                reason = narrated
                source = "openai_wording_over_deterministic_diff"
        suggestion = StrategySuggestion(
            user_id=user_id,
            strategy_id=strategy.id,
            strategy_version_id=version.id,
            action=action,
            status="draft",
            reason=reason,
            source=source,
            before_schema=before.model_dump(mode="json"),
            proposed_schema=proposed.model_dump(mode="json"),
            diff=diff,
            outcome_evidence=outcome_evidence,
            historical_effect={
                "status": "requires_deterministic_preview",
                "alerts_removed": None,
                "alerts_retained": None,
                "strong_outcomes_lost": None,
                "weak_outcomes_removed": None,
            },
            confidence=confidence,
            limitations=limitations,
        )
        self.session.add(suggestion)
        await self.session.flush()
        return suggestion

    async def apply_suggestion(
        self,
        *,
        suggestion: StrategySuggestion,
        user_id: UUID,
    ) -> StrategyVersion:
        if suggestion.user_id != user_id or suggestion.status != "draft":
            raise ValueError("Suggestion is unavailable")
        strategy = await self.session.get(Strategy, suggestion.strategy_id)
        if strategy is None or strategy.user_id != user_id:
            raise ValueError("Strategy not found")
        definition = StrategyDefinition.model_validate(suggestion.proposed_schema)
        from ai_market_monitor.services.strategy import StrategyService

        version = await StrategyService(self.session, "suggestion-draft").revise(
            strategy,
            definition,
            user_id=user_id,
            source_text=suggestion.reason,
            assumptions=["User confirmation is required before activation."],
            ambiguities=[],
            unsupported=[],
            interpreter="strategy_cockpit",
        )
        version.source_type = "cockpit_suggestion"
        suggestion.status = "applied_as_draft"
        suggestion.applied_version_id = version.id
        suggestion.applied_at = datetime.now(UTC)
        await self.session.flush()
        return version

    async def strategy_preferences(self, user_id: UUID) -> UserStrategyPreference:
        preference = await self.session.scalar(
            select(UserStrategyPreference).where(UserStrategyPreference.user_id == user_id)
        )
        if preference is not None:
            return preference
        preference = UserStrategyPreference(
            user_id=user_id,
            preferences={},
            evidence={},
        )
        self.session.add(preference)
        await self.session.flush()
        await self.derive_strategy_preferences(preference)
        return preference

    async def derive_strategy_preferences(
        self,
        preference: UserStrategyPreference,
    ) -> UserStrategyPreference:
        versions = (
            await self.session.scalars(
                select(StrategyVersion)
                .join(Strategy, Strategy.id == StrategyVersion.strategy_id)
                .where(Strategy.user_id == preference.user_id)
                .order_by(StrategyVersion.created_at.desc())
                .limit(100)
            )
        ).all()
        timeframes: Counter[str] = Counter()
        trigger_modes: Counter[str] = Counter()
        channels: Counter[str] = Counter()
        exchanges: Counter[str] = Counter()
        max_alerts: list[int] = []
        for version in versions:
            try:
                definition = StrategyDefinition.model_validate(version.schema_json)
            except ValueError:
                continue
            timeframes[definition.base_timeframe] += 1
            trigger_modes[definition.trigger_mode.value] += 1
            exchanges[definition.universe.exchange] += 1
            channels.update(definition.alerts.channels)
            max_alerts.append(definition.alerts.maximum_alerts_per_hour)
        derived = {
            "preferred_entry_timeframe": _most_common(timeframes),
            "preferred_trigger_mode": _most_common(trigger_modes),
            "preferred_exchange": _most_common(exchanges),
            "preferred_alert_channels": [name for name, _ in channels.most_common()],
            "typical_max_alerts_per_hour": (
                round(sum(max_alerts) / len(max_alerts)) if max_alerts else None
            ),
        }
        explicit = dict(preference.preferences or {})
        preference.preferences = {key: value for key, value in derived.items() if value is not None}
        preference.preferences.update(explicit)
        preference.evidence = {
            "strategy_versions_reviewed": len(versions),
            "derived_only_from_product_activity": True,
        }
        preference.last_derived_at = datetime.now(UTC)
        await self.session.flush()
        return preference

    async def sync_inbox(self, user_id: UUID) -> int:
        created = 0
        alerts = (
            await self.session.scalars(
                select(Alert)
                .where(Alert.user_id == user_id)
                .order_by(Alert.created_at.desc())
                .limit(250)
            )
        ).all()
        version_ids = {
            alert.strategy_version_id for alert in alerts if alert.strategy_version_id is not None
        }
        version_to_strategy = (
            {
                version_id: strategy_id
                for version_id, strategy_id in (
                    await self.session.execute(
                        select(StrategyVersion.id, StrategyVersion.strategy_id).where(
                            StrategyVersion.id.in_(version_ids)
                        )
                    )
                ).all()
            }
            if version_ids
            else {}
        )
        for alert in alerts:
            proof = alert.proof_receipt or {}
            item_type = _alert_inbox_type(alert)
            if await self._inbox_exists(user_id, item_type, "alert", str(alert.id)):
                continue
            self.session.add(
                AlertInboxItem(
                    user_id=user_id,
                    item_type=item_type,
                    source_type="alert",
                    source_id=str(alert.id),
                    strategy_id=version_to_strategy.get(alert.strategy_version_id),
                    strategy_version_id=alert.strategy_version_id,
                    setup_instance_id=alert.setup_instance_id,
                    alert_id=alert.id,
                    symbol=proof.get("symbol"),
                    timeframe=proof.get("timeframe"),
                    state=str(proof.get("setup_state") or alert.alert_type.value),
                    health_score=_decimal_or_none(
                        proof.get("alert_trust_score", {}).get("score")
                        if isinstance(proof.get("alert_trust_score"), dict)
                        else None
                    ),
                    title=alert.title,
                    summary=alert.body,
                    reason=alert.suppressed_reason,
                    proof_reference={
                        "alert_id": str(alert.id),
                        "setup_instance_id": (
                            str(alert.setup_instance_id) if alert.setup_instance_id else None
                        ),
                    },
                    actions=_inbox_actions(alert),
                    labels=[],
                    created_at=alert.created_at,
                )
            )
            created += 1
        created += await self._sync_suggestions(user_id)
        created += await self._sync_feedback_reviews(user_id)
        created += await self._sync_decay_events(user_id)
        await self.session.flush()
        return created

    async def setup_timeline(self, setup: SetupInstance) -> dict[str, Any]:
        events = (
            await self.session.scalars(
                select(SetupLifecycleEvent)
                .where(SetupLifecycleEvent.setup_instance_id == setup.id)
                .order_by(SetupLifecycleEvent.occurred_at.asc())
            )
        ).all()
        conditions = (
            await self.session.scalars(
                select(SetupConditionResult)
                .where(SetupConditionResult.setup_instance_id == setup.id)
                .order_by(SetupConditionResult.evaluated_at.asc())
            )
        ).all()
        timeline: list[dict[str, Any]] = [
            {
                "event_type": "lifecycle",
                "timestamp": event.occurred_at,
                "title": event.to_state.value.replace("_", " ").title(),
                "state": event.to_state.value,
                "reason": event.reason_code,
                "evidence": event.evidence,
            }
            for event in events
        ]
        last_state: dict[str, ConditionOutcome] = {}
        for result in conditions:
            previous = last_state.get(result.condition_key)
            if previous == result.outcome:
                continue
            last_state[result.condition_key] = result.outcome
            timeline.append(
                {
                    "event_type": "condition",
                    "timestamp": result.evaluated_at,
                    "title": result.condition_key.replace("_", " ").title(),
                    "state": result.outcome.value,
                    "reason": (
                        f"Condition became {result.outcome.value}; "
                        f"actual {result.actual_value.get('value')}, "
                        f"required {result.required_value.get('value')}."
                    ),
                    "evidence": {
                        "scan_result_id": str(result.scan_result_id),
                        "actual": result.actual_value,
                        "required": result.required_value,
                    },
                }
            )
        timeline.sort(key=lambda item: item["timestamp"])
        return {
            "setup_id": str(setup.id),
            "symbol": setup.symbol,
            "state": setup.state.value,
            "timeline": timeline,
            "exportable": True,
        }

    async def finalize_missed_move_job(
        self,
        replay_job_id: UUID,
    ) -> MissedMoveAnalysis | None:
        analysis = await self.session.scalar(
            select(MissedMoveAnalysis).where(MissedMoveAnalysis.replay_job_id == replay_job_id)
        )
        if analysis is None:
            return None
        replay_job = await self.session.get(SetupReplayJob, replay_job_id)
        if replay_job is None:
            analysis.status = "failed"
            analysis.error_code = "replay_job_missing"
            analysis.completed_at = datetime.now(UTC)
            analysis.result = {
                "summary": "The queued replay record is unavailable.",
                "primary_reason": "replay_job_missing",
                "automatic_strategy_change": False,
            }
            await self.session.flush()
            return analysis
        if replay_job.status in {"queued", "running"}:
            analysis.status = replay_job.status
            await self.session.flush()
            return analysis
        replay_result = await self.session.scalar(
            select(SetupReplayResult).where(SetupReplayResult.replay_job_id == replay_job_id)
        )
        if replay_job.status != "succeeded" or replay_result is None:
            analysis.status = "failed"
            analysis.error_code = replay_job.error_code or "replay_failed"
            analysis.completed_at = replay_job.completed_at or datetime.now(UTC)
            analysis.result = {
                "summary": "The deterministic replay could not complete.",
                "primary_reason": "market_data_or_replay_unavailable",
                "error_code": analysis.error_code,
                "automatic_strategy_change": False,
            }
            await self.sync_inbox(analysis.user_id)
            await self.session.flush()
            return analysis
        version = (
            await self.session.get(StrategyVersion, analysis.strategy_version_id)
            if analysis.strategy_version_id
            else None
        )
        strategy = await self.session.get(Strategy, analysis.strategy_id)
        if version is None or strategy is None:
            analysis.status = "failed"
            analysis.error_code = "strategy_version_missing"
            analysis.completed_at = datetime.now(UTC)
            analysis.result = {
                "summary": "The strategy version used for replay is unavailable.",
                "primary_reason": "strategy_version_missing",
                "automatic_strategy_change": False,
            }
        else:
            analysis.status = "succeeded"
            analysis.error_code = None
            analysis.completed_at = replay_job.completed_at or datetime.now(UTC)
            analysis.result = await self._missed_move_result(
                strategy,
                version,
                analysis,
                replay_result,
            )
        await self.sync_inbox(analysis.user_id)
        await self.session.flush()
        return analysis

    async def _missed_move_result(
        self,
        strategy: Strategy,
        version: StrategyVersion,
        analysis: MissedMoveAnalysis,
        replay_result: SetupReplayResult,
    ) -> dict[str, Any]:
        summary = replay_result.summary or {}
        best = summary.get("best_result") or {}
        conditions = best.get("conditions") or []
        passed = [item for item in conditions if item.get("state") == "passed"]
        failed = [item for item in conditions if item.get("state") == "failed"]
        pending = [item for item in conditions if item.get("state") == "pending"]
        unavailable = [item for item in conditions if item.get("state") in {"unavailable", "error"}]
        setup = await self.session.scalar(
            select(SetupInstance)
            .where(
                SetupInstance.strategy_version_id == version.id,
                SetupInstance.exchange == analysis.exchange,
                SetupInstance.symbol == analysis.symbol,
                SetupInstance.last_evaluated_at <= analysis.approximate_time,
            )
            .order_by(SetupInstance.last_evaluated_at.desc())
            .limit(1)
        )
        definition = StrategyDefinition.model_validate(version.schema_json)
        included = {_canonical_symbol(item) for item in definition.universe.include_symbols}
        excluded = {_canonical_symbol(item) for item in definition.universe.exclude_symbols}
        symbol_selected = (
            not included or analysis.symbol in included
        ) and analysis.symbol not in excluded
        primary_reason = "conditions_not_complete"
        if not symbol_selected:
            primary_reason = "symbol_excluded_from_universe"
        elif unavailable:
            primary_reason = "required_data_unavailable"
        elif failed:
            primary_reason = "mandatory_conditions_failed"
        elif pending:
            primary_reason = "confirmation_arrived_late_or_remained_pending"
        elif best.get("setup_completion_score") == 100:
            primary_reason = "setup_completed_but_alert_or_delivery_evidence_missing"
        return {
            "summary": (
                f"{analysis.symbol} reached "
                f"{best.get('setup_completion_score', 0):.0f}% completion in the replay."
                if best
                else "No qualifying deterministic setup was reconstructed in the replay window."
            ),
            "primary_reason": primary_reason,
            "strategy_id": str(strategy.id),
            "strategy_version_id": str(version.id),
            "strategy_version_number": version.version_number,
            "symbol_was_in_universe": symbol_selected,
            "setup_was_persisted": setup is not None,
            "setup_state": setup.state.value if setup else None,
            "passed_conditions": passed,
            "failed_conditions": failed,
            "pending_conditions": pending,
            "unavailable_conditions": unavailable,
            "timeline": replay_result.timeline_points,
            "chart": {
                "candles": summary.get("candles", []),
                "markers": summary.get("markers", []),
                "overlays": summary.get("overlays", []),
            },
            "suggested_adjustments": replay_result.suggested_adjustments,
            "automatic_strategy_change": False,
            "non_predictive_notice": (
                "This reconstruction explains historical monitor behavior; it does not claim "
                "the move could have been predicted or captured."
            ),
        }

    async def detect_decay(self, strategy: Strategy, *, persist: bool = True) -> list[dict]:
        version_ids = (
            await self.session.scalars(
                select(StrategyVersion.id).where(StrategyVersion.strategy_id == strategy.id)
            )
        ).all()
        if not version_ids:
            return []
        now = datetime.now(UTC)
        current_start = now - timedelta(days=7)
        baseline_start = now - timedelta(days=35)
        current = await self._period_metrics(version_ids, current_start, now)
        baseline = await self._period_metrics(version_ids, baseline_start, current_start)
        events = _decay_findings(current, baseline, now)
        result: list[dict[str, Any]] = []
        version = await self.latest_version(strategy)
        for event in events:
            result.append(event)
            if not persist:
                continue
            existing = await self.session.scalar(
                select(StrategyDecayEvent).where(
                    StrategyDecayEvent.strategy_id == strategy.id,
                    StrategyDecayEvent.event_type == event["event_type"],
                    StrategyDecayEvent.status == "open",
                )
            )
            if existing is None:
                self.session.add(
                    StrategyDecayEvent(
                        user_id=strategy.user_id,
                        strategy_id=strategy.id,
                        strategy_version_id=version.id if version else None,
                        event_type=event["event_type"],
                        severity=event["severity"],
                        status="open",
                        baseline=baseline,
                        current=current,
                        explanation=event["explanation"],
                        suggested_actions=event["suggested_actions"],
                        detected_at=now,
                    )
                )
        await self.session.flush()
        return result

    async def create_weekly_health_summary(
        self,
        strategy: Strategy,
        health: dict[str, Any],
    ) -> DashboardNotification | None:
        action_url = f"/dashboard/strategies/{strategy.id}"
        recent = await self.session.scalar(
            select(DashboardNotification.id).where(
                DashboardNotification.user_id == strategy.user_id,
                DashboardNotification.action_url == action_url,
                DashboardNotification.title == f"{strategy.name} weekly health",
                DashboardNotification.created_at >= datetime.now(UTC) - timedelta(days=7),
            )
        )
        if recent is not None:
            return None
        # The same words the Monitors card uses, from the same owner. This note is read
        # by the person who owns the monitor, and it used to say "Edge Health: 40/100.
        # Main issue: Average recorded latency is 653784 ms." — a term from inside the
        # machine, a number this product decided not to show a beginner, and a sentence
        # written for an engineer. The card and the note now agree, because one function
        # decides both.
        said = monitor_working_words(float(health["score"]))
        notification = DashboardNotification(
            user_id=strategy.user_id,
            level="warning" if health["score"] < 70 else "info",
            title=f"{strategy.name} weekly health",
            body=(
                f"{said.label}. "
                + monitor_issue_words(
                    health["main_issue_component"],
                    blocker_known=bool(health.get("main_issue_blocker_known")),
                )
            ),
            action_label="Open monitor review",
            action_url=action_url,
            created_at=datetime.now(UTC),
        )
        self.session.add(notification)
        await self.session.flush()
        return notification

    async def _health_payload(
        self,
        strategy: Strategy,
        version: StrategyVersion | None,
        components: list[dict[str, Any]],
        sample_size: int,
        persist: bool,
    ) -> dict[str, Any]:
        score = round(sum(item["score"] for item in components), 2)
        status, grade = health_status(score)
        weak = min(components, key=lambda item: item["score"] / item["maximum"])
        explanation = (
            f"Edge Health is {score:.0f}/100. "
            f"The weakest component is {weak['name'].lower()}: {weak['explanation']}"
        )
        suggested_action = _component_action(weak["name"])
        trend = (
            await self.session.scalars(
                select(EdgeHealthSnapshot)
                .where(EdgeHealthSnapshot.strategy_id == strategy.id)
                .order_by(EdgeHealthSnapshot.calculated_at.desc())
                .limit(20)
            )
        ).all()
        payload = {
            "strategy_id": str(strategy.id),
            "strategy_version_id": str(version.id) if version else None,
            "score": score,
            "grade": grade,
            "status": status,
            "components": components,
            "explanation": explanation,
            "main_issue": weak["explanation"],
            # The weakest component's *name*, beside its sentence. The name is the
            # stable part, and it is what a beginner-facing screen needs in order to say
            # the same thing in its own words instead of repeating an engineer's. Every
            # reader used to re-derive "which one is weakest" or, more often, print the
            # engineer's sentence.
            "main_issue_component": weak["name"],
            # Whether the weakest component already knows what is in the way. Two very
            # different sentences hang off this, and both surfaces read it from here
            # rather than each deciding for itself.
            "main_issue_blocker_known": bool(
                (weak.get("details") or {}).get("blocker_known")
            ),
            "suggested_action": suggested_action,
            "sample_size": sample_size,
            "trend": [
                {
                    "score": float(snapshot.score),
                    "status": snapshot.status,
                    "calculated_at": snapshot.calculated_at,
                }
                for snapshot in reversed(trend)
            ],
            "non_advisory_notice": (
                "Edge Health describes monitor behavior and evidence quality, not profitability."
            ),
        }
        if persist:
            snapshot = EdgeHealthSnapshot(
                user_id=strategy.user_id,
                strategy_id=strategy.id,
                strategy_version_id=version.id if version else None,
                score=Decimal(str(score)),
                grade=grade,
                status=status,
                components=components,
                explanation=explanation,
                main_issue=weak["explanation"],
                suggested_action=suggested_action,
                sample_size=sample_size,
                calculated_at=datetime.now(UTC),
            )
            self.session.add(snapshot)
            await self.session.flush()
            payload["snapshot_id"] = str(snapshot.id)
        return payload

    def _health_components(
        self,
        scans: Sequence[ScanResult],
        alerts: Sequence[Alert],
        setups: Sequence[SetupInstance],
        feedback: Sequence[UserFeedback],
        bottlenecks: dict[str, Any],
        regime: dict[str, Any],
        now: datetime,
    ) -> list[dict[str, Any]]:
        scan_count = len(scans)
        successful_scans = sum(1 for scan in scans if scan.outcome != ScanOutcome.ERROR)
        coverage_ratio = successful_scans / scan_count if scan_count else 0
        coverage = _component(
            "Data coverage",
            coverage_ratio * 12,
            12,
            _ratio_status(coverage_ratio),
            (
                f"{successful_scans} of {scan_count} recent evaluations had usable data."
                if scan_count
                else "No recent scan evidence exists."
            ),
        )
        # How often a check read a candle the market had already moved past, counted in
        # candles rather than in milliseconds. Milliseconds cannot answer this on their
        # own: whether a delay is late depends entirely on the candle period, and the
        # thresholds here used to be a flat 5 s and 60 s. A five-minute monitor reading
        # the newest candle the moment it closed still scored 0.3 out of 1, so every
        # monitor slower than a minute carried a permanent, untrue "the prices it read
        # were not the newest ones" on its card.
        measured = [
            measure_freshness(lateness_ms=scan.data_freshness_ms, timeframe=scan.timeframe)
            for scan in scans
        ]
        known = [item for item in measured if item.is_known]
        current_scans = sum(1 for item in known if item.is_current)
        freshness_ratio = (
            sum(item.ratio for item in known) / len(known) if known else _FRESHNESS_UNKNOWN_RATIO
        )
        freshness = _component(
            "Data freshness",
            freshness_ratio * 8,
            8,
            _ratio_status(freshness_ratio),
            (
                f"{current_scans} of {len(known)} recent evaluations read the newest "
                "closed candle."
                if known
                else "Data lateness evidence is unavailable."
            ),
        )
        confirmed = sum(1 for scan in scans if scan.outcome == ScanOutcome.CONFIRMED)
        forming = sum(
            1 for scan in scans if scan.outcome in {ScanOutcome.FORMING, ScanOutcome.NEAR_MISS}
        )
        frequency_ratio = _frequency_health_ratio(confirmed, scan_count)
        frequency = _component(
            "Frequency health",
            frequency_ratio * 12,
            12,
            _ratio_status(frequency_ratio),
            f"{confirmed} confirmed and {forming} forming evaluations in the last 30 days.",
        )
        top = bottlenecks.get("main_bottleneck")
        condition_pass = (
            sum(item["pass_rate"] for item in bottlenecks["conditions"])
            / len(bottlenecks["conditions"])
            / 100
            if bottlenecks.get("conditions")
            else 0
        )
        conditions = _component(
            "Condition pass health",
            condition_pass * 14,
            14,
            _ratio_status(condition_pass),
            (
                f"{top['condition_label']} is the strongest blocker."
                if top
                else "No condition history exists yet."
            ),
        )
        # Whether a blocking rule is actually known, kept as a fact rather than left to
        # be guessed from the sentence beside it. "Not enough history yet" and "one rule
        # is almost never true" are opposite pieces of news, and the beginner-facing card
        # was printing the first for monitors that had months of history and a rule that
        # could never be true.
        conditions["details"] = {"blocker_known": top is not None}
        terminal = [setup for setup in setups if setup.state in TERMINAL_STATES]
        progressed = [
            setup
            for setup in terminal
            if setup.state
            in {
                SetupLifecycleState.TARGET_REACHED,
                SetupLifecycleState.COMPLETED,
                SetupLifecycleState.CLOSED,
                SetupLifecycleState.MANUALLY_CLOSED,
            }
        ]
        lifecycle_ratio = len(progressed) / len(terminal) if terminal else (0.6 if setups else 0)
        lifecycle = _component(
            "Lifecycle completion",
            lifecycle_ratio * 10,
            10,
            _ratio_status(lifecycle_ratio),
            (
                f"{len(progressed)} of {len(terminal)} terminal setups reached a recorded closure."
                if terminal
                else "Not enough terminal setup history exists."
            ),
        )
        positive = sum(1 for item in feedback if item.feedback_type in POSITIVE_FEEDBACK)
        negative = sum(1 for item in feedback if item.feedback_type in NEGATIVE_FEEDBACK)
        feedback_ratio = positive / (positive + negative) if positive + negative else 0.65
        feedback_component = _component(
            "Alert quality feedback",
            feedback_ratio * 12,
            12,
            _ratio_status(feedback_ratio),
            (
                f"{positive} positive and {negative} corrective feedback item(s)."
                if feedback
                else "No alert-quality feedback exists; a neutral score is used."
            ),
        )
        proof_complete = sum(
            1
            for alert in alerts
            if alert.proof_receipt
            and alert.proof_receipt.get("conditions") is not None
            and alert.proof_receipt.get("strategy_version") is not None
        )
        proof_ratio = proof_complete / len(alerts) if alerts else 0
        proof = _component(
            "Proof completeness",
            proof_ratio * 10,
            10,
            _ratio_status(proof_ratio),
            (
                f"{proof_complete} of {len(alerts)} alerts have reconstructable proof."
                if alerts
                else "No alert proof receipts exist yet."
            ),
        )
        regime_ratio = float(regime.get("fit_score", 50)) / 100
        regime_component = _component(
            "Market-regime fit",
            regime_ratio * 12,
            12,
            str(regime.get("status") or _ratio_status(regime_ratio)),
            str(regime.get("explanation") or "Cross-market regime evidence is not available yet."),
        )
        regime_component["details"] = regime
        latest_alert_at = max((alert.created_at for alert in alerts), default=None)
        # `None` when no alert exists, and the score treats that as the longest silence.
        # The *sentence* may not: "Last alert evidence is 30.0 day(s) old." was printed
        # for a monitor switched on an hour ago, describing evidence that has never
        # existed. A missing measurement is reported as missing, never as a number.
        silent_days = (
            (now - _aware(latest_alert_at)).total_seconds() / 86_400
            if latest_alert_at
            else None
        )
        silence_ratio = (
            1
            if silent_days is not None and silent_days <= 14
            else 0.6
            if silent_days is not None and silent_days <= 21
            else 0.2
        )
        silent = _component(
            "Silent-monitor risk",
            silence_ratio * 5,
            5,
            _ratio_status(silence_ratio),
            (
                f"Last alert evidence is {silent_days:.1f} day(s) old."
                if silent_days is not None
                else "No alert has been sent yet, so there is no alert evidence to age."
            ),
        )
        alerts_per_day = len(alerts) / 30
        spam_ratio = 1 if alerts_per_day <= 10 else 0.6 if alerts_per_day <= 30 else 0.2
        spam = _component(
            "Alert spam risk",
            spam_ratio * 5,
            5,
            _ratio_status(spam_ratio),
            f"Recent average is {alerts_per_day:.2f} alerts per day.",
        )
        return [
            coverage,
            freshness,
            frequency,
            conditions,
            lifecycle,
            feedback_component,
            proof,
            regime_component,
            silent,
            spam,
        ]

    async def _market_regime_context(
        self,
        strategy: Strategy,
        version: StrategyVersion | None,
        *,
        provider: MarketDataProvider | None,
    ) -> dict[str, Any]:
        if version is not None and provider is not None:
            try:
                definition = StrategyDefinition.model_validate(version.schema_json)
                return await MarketRegimeAnalyzer(provider).evaluate(definition)
            except Exception as exc:
                return {
                    "classification": "unavailable",
                    "fit_score": 50,
                    "status": "partial",
                    "error": type(exc).__name__,
                    "explanation": (
                        "Benchmark market context was unavailable, so a neutral fit score "
                        "was used without inventing regime data."
                    ),
                }
        latest = await self.session.scalar(
            select(EdgeHealthSnapshot)
            .where(EdgeHealthSnapshot.strategy_id == strategy.id)
            .order_by(EdgeHealthSnapshot.calculated_at.desc())
            .limit(1)
        )
        if latest is not None:
            for component in latest.components or []:
                if component.get("name") == "Market-regime fit":
                    details = component.get("details")
                    if isinstance(details, dict):
                        return details
        return {
            "classification": "not_evaluated",
            "fit_score": 50,
            "status": "partial",
            "explanation": ("Market-regime fit will be calculated by the scheduled health worker."),
        }

    async def _condition_version_id(self, condition_id: UUID) -> UUID | None:
        return await self.session.scalar(
            select(StrategyCondition.strategy_version_id).where(
                StrategyCondition.id == condition_id
            )
        )

    async def _maybe_create_feedback_suggestion(
        self,
        alert: Alert,
        feedback_type: str,
    ) -> StrategySuggestion | None:
        action = {
            "too_early": "make_less_noisy",
            "too_late": "make_trigger_earlier",
            "false_alert": "reduce_false_alerts",
            "incorrect_match": "reduce_false_alerts",
            "too_many_alerts": "make_less_noisy",
            "too_strict": "increase_alert_frequency",
            "missed_move": "review_lifecycle_evidence",
            "not_relevant": "add_market_context_filter",
            "bad_market_context": "add_market_context_filter",
            "good_idea_weak_proof": "add_volume_confirmation",
        }.get(feedback_type)
        if action is None or alert.strategy_version_id is None:
            return None
        version = await self.session.get(StrategyVersion, alert.strategy_version_id)
        strategy = await self.session.get(Strategy, version.strategy_id) if version else None
        if strategy is None:
            return None
        repeated = await self.session.scalar(
            select(func.count(UserFeedback.id))
            .join(Alert, Alert.id == UserFeedback.alert_id)
            .where(
                Alert.strategy_version_id.in_(
                    select(StrategyVersion.id).where(StrategyVersion.strategy_id == strategy.id)
                ),
                UserFeedback.feedback_type == feedback_type,
            )
        )
        if (repeated or 0) < 3:
            return None
        existing = await self.session.scalar(
            select(StrategySuggestion).where(
                StrategySuggestion.strategy_id == strategy.id,
                StrategySuggestion.action == action,
                StrategySuggestion.status == "draft",
                StrategySuggestion.source == "feedback_pattern",
            )
        )
        if existing is not None:
            return existing
        suggestion = await self.generate_suggestion(
            user_id=strategy.user_id,
            strategy=strategy,
            action=action,
        )
        suggestion.source = "feedback_pattern"
        suggestion.reason = (
            f"You used '{feedback_type.replace('_', ' ')}' on {repeated} alerts. "
            f"Suggested monitoring adjustment: {suggestion.reason}"
        )
        return suggestion

    async def _version_metrics(
        self,
        version_id: UUID,
        *,
        experiment_id: UUID | None = None,
    ) -> dict[str, Any]:
        scans = (
            await self.session.scalars(
                select(ScanResult).where(ScanResult.strategy_version_id == version_id)
            )
        ).all()
        if experiment_id is not None:
            scans = [
                scan
                for scan in scans
                if str((scan.proof_summary or {}).get("scan_context", {}).get("experiment_id"))
                == str(experiment_id)
            ]
        alerts = (
            await self.session.scalars(select(Alert).where(Alert.strategy_version_id == version_id))
        ).all()
        if experiment_id is not None:
            alerts = [
                alert
                for alert in alerts
                if str((alert.proof_receipt or {}).get("scan_context", {}).get("experiment_id"))
                == str(experiment_id)
            ]
        feedback = (
            await self.session.scalars(
                select(UserFeedback)
                .join(Alert, Alert.id == UserFeedback.alert_id)
                .where(Alert.strategy_version_id == version_id)
            )
        ).all()
        if experiment_id is not None:
            experiment_alert_ids = {alert.id for alert in alerts}
            feedback = [item for item in feedback if item.alert_id in experiment_alert_ids]
        confirmed = sum(1 for scan in scans if scan.outcome == ScanOutcome.CONFIRMED)
        forming = sum(
            1 for scan in scans if scan.outcome in {ScanOutcome.FORMING, ScanOutcome.NEAR_MISS}
        )
        false_feedback = sum(
            1
            for item in feedback
            if item.feedback_type in {"false_alert", "incorrect_match", "not_relevant"}
        )
        evaluated_times = sorted(scan.evaluated_at for scan in scans)
        average_lead_minutes = None
        if len(evaluated_times) >= 2:
            intervals = [
                (current - previous).total_seconds() / 60
                for previous, current in zip(evaluated_times, evaluated_times[1:], strict=False)
            ]
            average_lead_minutes = sum(intervals) / len(intervals)
        return {
            "version_id": str(version_id),
            "evaluations": len(scans),
            "confirmed_matches": confirmed,
            "forming_setups": forming,
            "alerts": len(alerts),
            "false_alert_feedback": false_feedback,
            "feedback_count": len(feedback),
            "average_evaluation_interval_minutes": (
                round(average_lead_minutes, 2) if average_lead_minutes is not None else None
            ),
            "strictness": (
                "high"
                if scans and confirmed / len(scans) < 0.01
                else "low"
                if scans and confirmed / len(scans) > 0.2
                else "moderate"
            ),
        }

    async def _period_metrics(
        self,
        version_ids: Sequence[UUID],
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        scans = (
            await self.session.scalars(
                select(ScanResult).where(
                    ScanResult.strategy_version_id.in_(version_ids),
                    ScanResult.evaluated_at >= start,
                    ScanResult.evaluated_at < end,
                )
            )
        ).all()
        alerts = (
            await self.session.scalars(
                select(Alert).where(
                    Alert.strategy_version_id.in_(version_ids),
                    Alert.created_at >= start,
                    Alert.created_at < end,
                )
            )
        ).all()
        setups = (
            await self.session.scalars(
                select(SetupInstance).where(
                    SetupInstance.strategy_version_id.in_(version_ids),
                    SetupInstance.last_evaluated_at >= start,
                    SetupInstance.last_evaluated_at < end,
                )
            )
        ).all()
        last_alert_at = max((_aware(alert.created_at) for alert in alerts), default=None)
        return {
            "scan_count": len(scans),
            "confirmed_count": sum(1 for scan in scans if scan.outcome == ScanOutcome.CONFIRMED),
            "error_count": sum(1 for scan in scans if scan.outcome == ScanOutcome.ERROR),
            "alert_count": len(alerts),
            "invalidation_count": sum(
                1 for setup in setups if setup.state == SetupLifecycleState.INVALIDATED
            ),
            "universe_size": len({scan.symbol for scan in scans}),
            "last_alert_at": last_alert_at.isoformat() if last_alert_at is not None else None,
        }

    async def _inbox_exists(
        self,
        user_id: UUID,
        item_type: str,
        source_type: str,
        source_id: str,
    ) -> bool:
        return (
            await self.session.scalar(
                select(AlertInboxItem.id).where(
                    AlertInboxItem.user_id == user_id,
                    AlertInboxItem.item_type == item_type,
                    AlertInboxItem.source_type == source_type,
                    AlertInboxItem.source_id == source_id,
                )
            )
            is not None
        )

    async def _sync_suggestions(self, user_id: UUID) -> int:
        suggestions = (
            await self.session.scalars(
                select(StrategySuggestion)
                .where(StrategySuggestion.user_id == user_id)
                .order_by(StrategySuggestion.created_at.desc())
                .limit(100)
            )
        ).all()
        created = 0
        for suggestion in suggestions:
            if await self._inbox_exists(
                user_id,
                "strategy_suggestion",
                "strategy_suggestion",
                str(suggestion.id),
            ):
                continue
            self.session.add(
                AlertInboxItem(
                    user_id=user_id,
                    item_type="strategy_suggestion",
                    source_type="strategy_suggestion",
                    source_id=str(suggestion.id),
                    strategy_id=suggestion.strategy_id,
                    strategy_version_id=suggestion.strategy_version_id,
                    state=suggestion.status,
                    title=suggestion.action.replace("_", " ").title(),
                    summary=suggestion.reason,
                    proof_reference={"suggestion_id": str(suggestion.id)},
                    actions=[
                        {"label": "Review diff", "action": "review_suggestion"},
                        {"label": "Apply as draft", "action": "apply_suggestion"},
                    ],
                    labels=["monitoring adjustment"],
                    created_at=suggestion.created_at,
                )
            )
            created += 1
        return created

    async def _sync_feedback_reviews(self, user_id: UUID) -> int:
        rows = (
            await self.session.execute(
                select(UserFeedback, Alert)
                .join(Alert, Alert.id == UserFeedback.alert_id)
                .where(
                    UserFeedback.user_id == user_id,
                    UserFeedback.feedback_type.in_(NEGATIVE_FEEDBACK),
                )
                .order_by(UserFeedback.created_at.desc())
                .limit(100)
            )
        ).all()
        created = 0
        for feedback, alert in rows:
            if await self._inbox_exists(
                user_id,
                "false_alert_review",
                "user_feedback",
                str(feedback.id),
            ):
                continue
            proof = alert.proof_receipt or {}
            self.session.add(
                AlertInboxItem(
                    user_id=user_id,
                    item_type="false_alert_review",
                    source_type="user_feedback",
                    source_id=str(feedback.id),
                    strategy_version_id=alert.strategy_version_id,
                    setup_instance_id=alert.setup_instance_id,
                    alert_id=alert.id,
                    symbol=proof.get("symbol"),
                    timeframe=proof.get("timeframe"),
                    state="needs_review",
                    title=feedback.feedback_type.replace("_", " ").title(),
                    summary=(
                        f"Corrective feedback was recorded for {alert.title}. "
                        "Review proof before proposing any condition adjustment."
                    ),
                    reason=feedback.comment,
                    proof_reference={
                        "feedback_id": str(feedback.id),
                        "alert_id": str(alert.id),
                    },
                    actions=[
                        {"label": "View proof", "action": "view_proof"},
                        {"label": "Improve monitor", "action": "improve_monitor"},
                    ],
                    labels=["feedback"],
                    created_at=feedback.created_at,
                )
            )
            created += 1
        return created

    async def _sync_missed_moves(self, user_id: UUID) -> int:
        analyses = (
            await self.session.scalars(
                select(MissedMoveAnalysis)
                .where(MissedMoveAnalysis.user_id == user_id)
                .order_by(MissedMoveAnalysis.created_at.desc())
                .limit(100)
            )
        ).all()
        created = 0
        for analysis in analyses:
            if await self._inbox_exists(
                user_id,
                "missed_move_review",
                "missed_move_analysis",
                str(analysis.id),
            ):
                continue
            self.session.add(
                AlertInboxItem(
                    user_id=user_id,
                    item_type="missed_move_review",
                    source_type="missed_move_analysis",
                    source_id=str(analysis.id),
                    strategy_id=analysis.strategy_id,
                    strategy_version_id=analysis.strategy_version_id,
                    symbol=analysis.symbol,
                    timeframe=analysis.timeframe,
                    state=analysis.status,
                    title=f"{analysis.symbol} missed-move review",
                    summary=str(
                        analysis.result.get("summary")
                        or "Deterministic reconstruction is available."
                    ),
                    reason=analysis.result.get("primary_reason"),
                    proof_reference={"analysis_id": str(analysis.id)},
                    actions=[{"label": "Open review", "action": "open_missed_move"}],
                    labels=["missed move"],
                    created_at=analysis.created_at,
                )
            )
            created += 1
        return created

    async def _sync_decay_events(self, user_id: UUID) -> int:
        events = (
            await self.session.scalars(
                select(StrategyDecayEvent)
                .where(StrategyDecayEvent.user_id == user_id)
                .order_by(StrategyDecayEvent.detected_at.desc())
                .limit(100)
            )
        ).all()
        created = 0
        for event in events:
            if await self._inbox_exists(
                user_id,
                "system_notice",
                "strategy_decay",
                str(event.id),
            ):
                continue
            self.session.add(
                AlertInboxItem(
                    user_id=user_id,
                    item_type="system_notice",
                    source_type="strategy_decay",
                    source_id=str(event.id),
                    strategy_id=event.strategy_id,
                    strategy_version_id=event.strategy_version_id,
                    state=event.status,
                    title=event.event_type.replace("_", " ").title(),
                    summary=event.explanation,
                    proof_reference={"decay_event_id": str(event.id)},
                    actions=[
                        {"label": "Inspect bottlenecks", "action": "open_bottlenecks"},
                        {"label": "Run validation", "action": "validate_strategy"},
                    ],
                    labels=["monitor health", event.severity],
                    created_at=event.detected_at,
                )
            )
            created += 1
        return created


def select_strategy_conditions(strategy_id: UUID):
    return (
        select(StrategyCondition)
        .join(
            StrategyVersion,
            StrategyVersion.id == StrategyCondition.strategy_version_id,
        )
        .where(StrategyVersion.strategy_id == strategy_id)
    )


def _component(
    name: str,
    score: float,
    maximum: float,
    status: str,
    explanation: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "score": round(max(0, min(maximum, score)), 3),
        "maximum": maximum,
        "status": status,
        "explanation": explanation,
    }


#: Scored when no scan carried a period we can size. Not zero: never having measured
#: lateness is not the same as having measured it and found the data late.
_FRESHNESS_UNKNOWN_RATIO = 0.6


def _ratio_status(ratio: float) -> str:
    if ratio >= 0.8:
        return "healthy"
    if ratio >= 0.5:
        return "partial"
    return "needs_attention"


def _frequency_health_ratio(confirmed: int, scan_count: int) -> float:
    if scan_count == 0:
        return 0
    ratio = confirmed / scan_count
    if 0.005 <= ratio <= 0.15:
        return 1
    if ratio < 0.005:
        return 0.45
    if ratio <= 0.3:
        return 0.65
    return 0.3


def _component_action(name: str) -> str:
    return {
        "Data coverage": "Check provider coverage and the universe preview.",
        "Data freshness": "Inspect provider health and scan latency.",
        "Frequency health": "Run the alert frequency forecast and review strictness.",
        "Condition pass health": "Open the bottleneck map before changing any rule.",
        "Lifecycle completion": "Review invalidated and expired setup timelines.",
        "Alert quality feedback": "Review repeated feedback and create a safe draft suggestion.",
        "Proof completeness": "Inspect recent alerts with incomplete proof context.",
        "Market-regime fit": "Review benchmark context before changing strategy rules.",
        "Silent-monitor risk": "Review lifecycle evidence or run quick validation.",
        "Alert spam risk": "Increase cooldown or add a context filter.",
    }.get(name, "Review the monitor evidence before changing its rules.")


def _bottleneck_suggestion(
    key: str,
    pass_rate: float,
    outcomes: Counter,
) -> str:
    if outcomes["unavailable"] or outcomes["error"]:
        return "Check required data and provider availability before changing the threshold."
    if pass_rate < 10:
        return (
            f"Review whether {key.replace('_', ' ')} should remain mandatory or use a "
            "less restrictive threshold."
        )
    if pass_rate < 35:
        return "Inspect close failures and consider a small threshold adjustment as a draft."
    return "Keep the condition and monitor more evidence before changing it."


def _comparison_notes(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if left["confirmed_matches"] != right["confirmed_matches"]:
        notes.append(
            f"Confirmed matches: {left['confirmed_matches']} versus {right['confirmed_matches']}."
        )
    if left["false_alert_feedback"] != right["false_alert_feedback"]:
        notes.append(
            "Corrective feedback: "
            f"{left['false_alert_feedback']} versus {right['false_alert_feedback']}."
        )
    if left["strictness"] != right["strictness"]:
        notes.append(f"Strictness changed from {left['strictness']} to {right['strictness']}.")
    return notes or ["The stored behavioral metrics are currently equivalent."]


def _canonical_symbol(symbol: str) -> str:
    return symbol.upper().replace("-", "/").split(":", 1)[0].strip()


def _most_common(counter: Counter[str]) -> str | None:
    return counter.most_common(1)[0][0] if counter else None


def _decimal_or_none(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except (ValueError, TypeError):
        return None


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def _datetime_or_none(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _aware(value)
    if value is None:
        return None
    try:
        return _aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _alert_inbox_type(alert: Alert) -> str:
    if alert.suppressed_reason:
        return "suppressed_alert"
    state = str((alert.proof_receipt or {}).get("setup_state") or "")
    if state == "invalidated":
        return "invalidated_setup"
    if state == "suppressed":
        return "suppressed_alert"
    if state in {"blocked", "data_unavailable"}:
        return "data_issue"
    return {
        AlertType.CONFIRMED: "confirmed_alert",
        AlertType.FORMING: "forming_setup",
        AlertType.NEAR_MISS: "forming_setup",
        AlertType.LIFECYCLE: "lifecycle_update",
        AlertType.FAILURE: "data_issue",
        AlertType.TRIAL: "system_notice",
    }[alert.alert_type]


def _inbox_actions(alert: Alert) -> list[dict[str, str]]:
    actions = [{"label": "View proof", "action": "view_proof"}]
    if alert.setup_instance_id:
        actions.append({"label": "Open timeline", "action": "open_timeline"})
    if alert.alert_type in {AlertType.CONFIRMED, AlertType.FORMING, AlertType.NEAR_MISS}:
        actions.append({"label": "Give feedback", "action": "give_feedback"})
    return actions


def _decay_findings(
    current: dict[str, Any],
    baseline: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    last_alert = current.get("last_alert_at") or baseline.get("last_alert_at")
    if last_alert:
        parsed = datetime.fromisoformat(last_alert)
        silence_days = (now - _aware(parsed)).total_seconds() / 86_400
        if silence_days >= 21:
            events.append(
                {
                    "event_type": "long_silence",
                    "severity": "warning",
                    "explanation": (
                        f"This monitor has no recorded alert in {silence_days:.0f} days."
                    ),
                    "suggested_actions": [
                        "Inspect condition bottlenecks",
                        "Review lifecycle evidence",
                        "Run quick validation",
                    ],
                }
            )
    baseline_weekly_alerts = baseline["alert_count"] / 4
    if current["alert_count"] >= 5 and current["alert_count"] > max(
        3,
        baseline_weekly_alerts * 3,
    ):
        events.append(
            {
                "event_type": "alert_spike",
                "severity": "warning",
                "explanation": (
                    f"Alerts increased to {current['alert_count']} this week versus a "
                    f"{baseline_weekly_alerts:.1f} weekly baseline."
                ),
                "suggested_actions": ["Review cooldown", "Inspect market-regime filters"],
            }
        )
    if current["scan_count"] and current["error_count"] / current["scan_count"] >= 0.2:
        events.append(
            {
                "event_type": "data_quality_deterioration",
                "severity": "high",
                "explanation": "At least 20% of recent evaluations ended with data errors.",
                "suggested_actions": ["Inspect provider health", "Review universe coverage"],
            }
        )
    if baseline["universe_size"] and current["universe_size"] < baseline["universe_size"] * 0.6:
        events.append(
            {
                "event_type": "universe_shrinkage",
                "severity": "warning",
                "explanation": (
                    f"Recent coverage fell to {current['universe_size']} symbols from a "
                    f"{baseline['universe_size']} symbol baseline."
                ),
                "suggested_actions": ["Preview optimized universe", "Inspect plan limits"],
            }
        )
    return events


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
