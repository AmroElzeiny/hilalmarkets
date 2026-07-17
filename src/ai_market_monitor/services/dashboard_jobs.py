import csv
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    Alert,
    AlertInboxItem,
    BacktestJob,
    BacktestResult,
    SetupInstance,
    SetupLifecycleEvent,
    SetupReplayJob,
    SetupReplayResult,
    Strategy,
    StrategyVersion,
    UserExportJob,
)
from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.engine.models import EvaluationResult, ensure_aware
from ai_market_monitor.provider_context import ProviderContextService
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.interfaces import Candle, MarketDataProvider
from ai_market_monitor.services.market_preview import (
    market_snapshot_from_candles,
    timeframe_duration,
)


class DashboardJobError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class DashboardJobService:
    def __init__(
        self,
        session: AsyncSession,
        provider: MarketDataProvider,
        settings: Settings,
        *,
        engine: StrategyRuleEngine | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings
        self.engine = engine or StrategyRuleEngine()
        self.context = ProviderContextService(provider, settings)

    async def process_replay_jobs(self, *, limit: int = 5) -> list[SetupReplayJob]:
        jobs = list(
            (
                await self.session.scalars(
                    select(SetupReplayJob)
                    .where(SetupReplayJob.status == "queued")
                    .order_by(SetupReplayJob.created_at.asc())
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for job in jobs:
            await self.run_replay_job(job.id)
        return jobs

    async def evaluate_historical_moment(
        self,
        *,
        strategy_version_id: UUID,
        symbol: str,
        timeframe: str,
        evaluation_time: datetime,
    ) -> EvaluationResult:
        """Evaluate one retained/provider historical moment through the production rule engine."""
        definition, version, strategy = await self._load_definition(strategy_version_id)
        candle_sets = await self._fetch_candle_sets(
            definition,
            symbol,
            max(300, definition.universe.min_historical_candles),
            extra_timeframes={timeframe},
            center=ensure_aware(evaluation_time),
            before_minutes=max(14_400, definition.universe.min_historical_candles * 60),
            after_minutes=0,
        )
        base = candle_sets.get(timeframe) or candle_sets.get(definition.base_timeframe) or []
        eligible = [
            candle
            for candle in base
            if ensure_aware(candle.timestamp) <= ensure_aware(evaluation_time)
        ]
        if not eligible:
            raise DashboardJobError(
                "historical_candle_unavailable",
                "No candle was available at or before the selected historical time.",
            )
        selected = eligible[-1]
        return await self._evaluate(
            definition,
            strategy,
            version,
            symbol,
            candle_sets,
            ensure_aware(selected.timestamp),
            previous_score=None,
            chart_reference=f"verification:{version.id}:{symbol}",
        )

    async def run_replay_job(self, job_id: UUID) -> SetupReplayResult:
        job = await self.session.get(SetupReplayJob, job_id)
        if job is None:
            raise DashboardJobError("replay_missing", "Replay job not found.")
        result = await self._get_or_create_replay_result(job)
        job.status = "running"
        job.started_at = job.started_at or _now()
        await self.session.flush()
        try:
            definition, version, strategy = await self._load_definition(job.strategy_version_id)
            chart_before_minutes = max(14_400, job.window_before_minutes)
            candle_sets = await self._fetch_candle_sets(
                definition,
                job.symbol,
                max(1000, definition.universe.min_historical_candles),
                extra_timeframes={job.timeframe},
                center=job.approximate_time,
                before_minutes=chart_before_minutes,
                after_minutes=job.window_after_minutes,
            )
            base = candle_sets.get(job.timeframe) or candle_sets[definition.base_timeframe]
            candidates = _window_candles(
                base,
                center=job.approximate_time,
                before_minutes=chart_before_minutes,
                after_minutes=job.window_after_minutes,
            )
            if not candidates:
                candidates = _nearest_candles(base, job.approximate_time, count=120)
            if not candidates:
                raise DashboardJobError("no_candles", "No candles were available for replay.")

            timeline: list[dict[str, Any]] = []
            candle_proofs: list[dict[str, Any]] = []
            previous_score: float | None = None
            best: EvaluationResult | None = None
            for candle in candidates[-2000:]:
                evaluation = await self._evaluate(
                    definition,
                    strategy,
                    version,
                    job.symbol,
                    candle_sets,
                    ensure_aware(candle.timestamp),
                    previous_score=previous_score,
                    chart_reference=f"replay:{job.id}",
                )
                previous_score = evaluation.near_miss.current_score
                if (
                    best is None
                    or evaluation.near_miss.current_score > best.near_miss.current_score
                ):
                    best = evaluation
                timeline.append(_timeline_point(evaluation))
                candle_proofs.append(
                    {
                        "timestamp": candle.timestamp.isoformat(),
                        "open": candle.open,
                        "high": candle.high,
                        "low": candle.low,
                        "close": candle.close,
                        "volume": candle.volume,
                        "proof": evaluation.proof_receipt(),
                    }
                )

            if best is None:
                raise DashboardJobError("no_evaluation", "Replay did not evaluate any candles.")
            result.summary = {
                "status": "succeeded",
                "message": "Replay completed from deterministic candle evaluations.",
                "strategy_name": definition.name,
                "strategy_version_id": str(version.id),
                "strategy_version_number": version.version_number,
                "exchange": job.exchange,
                "symbol": job.symbol,
                "timeframe": job.timeframe,
                "requested_time": ensure_aware(job.approximate_time).isoformat(),
                "evaluations": len(timeline),
                "best_result": best.proof_receipt(),
                "candles": [_candle_payload(candle) for candle in candidates],
                "overlays": _overlays_from_result(best),
                "markers": _markers_from_timeline(timeline, selected=best),
                "report": _replay_report(best, len(timeline)),
            }
            result.timeline_points = timeline
            result.candle_proofs = candle_proofs[-200:]
            result.suggested_adjustments = _suggestions_from_result(best)
            result.created_at = _now()
            job.status = "succeeded"
            job.completed_at = _now()
            job.error_code = None
            job.error_detail = None
            await self.session.flush()
            return result
        except Exception as exc:
            job.status = "failed"
            job.completed_at = _now()
            job.error_code = getattr(exc, "code", type(exc).__name__)
            job.error_detail = str(exc)[:1000]
            result.summary = {
                "status": "failed",
                "message": str(exc),
                "error_code": job.error_code,
            }
            result.timeline_points = []
            result.candle_proofs = []
            result.suggested_adjustments = []
            result.created_at = _now()
            await self.session.flush()
            return result

    async def process_backtest_jobs(self, *, limit: int = 3) -> list[BacktestJob]:
        jobs = list(
            (
                await self.session.scalars(
                    select(BacktestJob)
                    .where(BacktestJob.status == "queued")
                    .order_by(BacktestJob.created_at.asc())
                    .limit(limit)
                )
            ).all()
        )
        for job in jobs:
            await self.run_backtest_job(job.id)
        return jobs

    async def run_backtest_job(self, job_id: UUID) -> BacktestResult:
        job = await self.session.get(BacktestJob, job_id)
        if job is None:
            raise DashboardJobError("backtest_missing", "Backtest job not found.")
        result = await self._get_or_create_backtest_result(job)
        job.status = "running"
        await self.session.flush()
        try:
            definition, version, strategy = await self._load_definition(job.strategy_version_id)
            setup_results: list[dict[str, Any]] = []
            curve: list[dict[str, Any]] = []
            evaluated = 0
            confirmed = 0
            near_miss = 0
            outcome_counts: Counter[str] = Counter()
            blocker_counts: Counter[str] = Counter()
            condition_evaluations: Counter[str] = Counter()
            condition_passes: Counter[str] = Counter()
            condition_failures: Counter[str] = Counter()
            category_examples: dict[str, list[dict[str, Any]]] = {
                "matches": [],
                "near_matches": [],
                "invalidated": [],
                "non_matches": [],
            }
            cumulative_quality = 0.0
            unavailable_symbols: list[dict[str, str]] = []
            chart_payload: dict[str, Any] = {
                "symbol": job.symbols[0] if job.symbols else None,
                "timeframe": job.timeframe,
                "candles": [],
                "markers": [],
                "condition_events": [],
            }
            for symbol in job.symbols:
                range_minutes = max(
                    1,
                    int(
                        (
                            ensure_aware(job.ended_at_range)
                            - ensure_aware(job.started_at_range)
                        ).total_seconds()
                        / 60
                    ),
                )
                candle_sets = await self._fetch_candle_sets(
                    definition,
                    symbol,
                    1000,
                    extra_timeframes={job.timeframe},
                    center=ensure_aware(job.ended_at_range),
                    before_minutes=range_minutes,
                )
                base = candle_sets.get(job.timeframe) or candle_sets[definition.base_timeframe]
                candidates = [
                    candle
                    for candle in base
                    if ensure_aware(job.started_at_range)
                    <= ensure_aware(candle.timestamp)
                    <= ensure_aware(job.ended_at_range)
                ]
                if not candidates:
                    unavailable_symbols.append(
                        {
                            "symbol": symbol,
                            "reason": "no_candles_in_requested_window",
                        }
                    )
                    continue
                if symbol == chart_payload["symbol"]:
                    chart_payload["candles"] = [
                        _candle_payload(candle) for candle in candidates[-500:]
                    ]
                previous_score: float | None = None
                for candle in candidates:
                    evaluation = await self._evaluate(
                        definition,
                        strategy,
                        version,
                        symbol,
                        candle_sets,
                        ensure_aware(candle.timestamp),
                        previous_score=previous_score,
                        chart_reference=f"backtest:{job.id}:{symbol}",
                    )
                    previous_score = evaluation.near_miss.current_score
                    evaluated += 1
                    outcome = evaluation.outcome.value
                    outcome_counts[outcome] += 1
                    cumulative_quality += evaluation.near_miss.current_score / 100
                    if outcome == "confirmed":
                        confirmed += 1
                    if outcome == "near_miss":
                        near_miss += 1
                    failed_required = [
                        condition
                        for condition in evaluation.conditions
                        if condition.mandatory and condition.state.value != "passed"
                    ]
                    if failed_required:
                        blocker_counts[failed_required[0].name] += 1
                    for condition in evaluation.conditions:
                        condition_evaluations[condition.name] += 1
                        if condition.state.value == "passed":
                            condition_passes[condition.name] += 1
                        else:
                            condition_failures[condition.name] += 1
                    category = (
                        "matches"
                        if outcome == "confirmed"
                        else "near_matches"
                        if outcome in {"near_miss", "forming"}
                        else "invalidated"
                        if outcome in {"invalid", "expired"}
                        else "non_matches"
                    )
                    if len(category_examples[category]) < 50:
                        category_examples[category].append(
                            {
                                "symbol": symbol,
                                "timestamp": candle.timestamp.isoformat(),
                                "outcome": outcome,
                                "score": round(evaluation.near_miss.current_score, 3),
                                "primary_blocker": (
                                    failed_required[0].name if failed_required else None
                                ),
                                "conditions": [
                                    {
                                        "name": condition.name,
                                        "state": condition.state.value,
                                        "actual_value": condition.actual_value,
                                        "required_value": condition.required_value,
                                    }
                                    for condition in evaluation.conditions
                                ],
                            }
                        )
                    if outcome in {"confirmed", "near_miss", "forming"}:
                        proof = evaluation.proof_receipt()
                        setup_results.append(
                            {
                                "symbol": symbol,
                                "timestamp": candle.timestamp.isoformat(),
                                "outcome": evaluation.outcome.value,
                                "score": round(evaluation.near_miss.current_score, 3),
                                "entry_zone": proof.get("entry_zone"),
                                "risk_calculation": proof.get("risk_calculation"),
                            }
                        )
                        if symbol == chart_payload["symbol"]:
                            chart_payload["markers"].append(
                                {
                                    "time": candle.timestamp.isoformat(),
                                    "outcome": evaluation.outcome.value,
                                    "score": round(evaluation.near_miss.current_score, 3),
                                    "text": (f"{evaluation.near_miss.current_score:.0f}%"),
                                }
                            )
                            chart_payload["condition_events"].append(
                                {
                                    "timestamp": candle.timestamp.isoformat(),
                                    "outcome": evaluation.outcome.value,
                                    "score": round(evaluation.near_miss.current_score, 3),
                                    "conditions": [
                                        {
                                            "condition_id": condition.condition_id,
                                            "name": condition.name,
                                            "state": condition.state.value,
                                            "actual_value": condition.actual_value,
                                            "required_value": condition.required_value,
                                        }
                                        for condition in evaluation.conditions
                                    ],
                                }
                            )
                    curve.append(
                        {
                            "timestamp": candle.timestamp.isoformat(),
                            "symbol": symbol,
                            "quality_index": round(cumulative_quality, 6),
                            "score": round(evaluation.near_miss.current_score, 3),
                        }
                    )
            result.metrics = {
                "status": "succeeded" if evaluated else "insufficient_data",
                "message": (
                    "Historical analysis completed. These are hypothetical evaluations."
                    if evaluated
                    else "No candles were available in the requested historical window."
                ),
                "executed_trades": False,
                "evaluations": evaluated,
                "confirmed_setups": confirmed,
                "near_miss_setups": near_miss,
                "invalidated_setups": (
                    outcome_counts["invalid"] + outcome_counts["expired"]
                ),
                "non_match_setups": sum(
                    outcome_counts[value]
                    for value in ("invalid", "skipped", "expired", "error")
                ),
                "outcome_counts": dict(outcome_counts),
                "most_common_failed_condition": (
                    blocker_counts.most_common(1)[0][0] if blocker_counts else None
                ),
                "failed_condition_counts": dict(blocker_counts),
                "condition_statistics": {
                    name: {
                        "evaluations": count,
                        "passes": condition_passes[name],
                        "failures": condition_failures[name],
                        "pass_rate": round(condition_passes[name] / count, 6)
                        if count
                        else None,
                    }
                    for name, count in condition_evaluations.items()
                },
                "examples_by_outcome": category_examples,
                "symbols": job.symbols,
                "unavailable_symbols": unavailable_symbols,
                "report": _backtest_report(
                    symbols=job.symbols,
                    evaluated=evaluated,
                    confirmed=confirmed,
                    near_miss=near_miss,
                    setup_results=setup_results,
                ),
                "chart": {
                    **chart_payload,
                    "markers": chart_payload["markers"][-300:],
                    "condition_events": chart_payload["condition_events"][-300:],
                },
            }
            result.equity_curve = curve[-2000:]
            result.setup_results = setup_results[-1000:]
            result.created_at = _now()
            job.status = "succeeded"
            job.error_code = None
            job.error_detail = None
            await self.session.flush()
            return result
        except Exception as exc:
            job.status = "failed"
            job.error_code = getattr(exc, "code", type(exc).__name__)
            job.error_detail = str(exc)[:1000]
            result.metrics = {
                "status": "failed",
                "message": str(exc),
                "error_code": job.error_code,
                "executed_trades": False,
            }
            result.equity_curve = []
            result.setup_results = []
            result.created_at = _now()
            await self.session.flush()
            return result

    async def process_export_jobs(self, *, limit: int = 3) -> list[UserExportJob]:
        jobs = list(
            (
                await self.session.scalars(
                    select(UserExportJob)
                    .where(UserExportJob.status == "queued")
                    .order_by(UserExportJob.created_at.asc())
                    .limit(limit)
                )
            ).all()
        )
        for job in jobs:
            await self.run_export_job(job.id)
        return jobs

    async def run_export_job(self, job_id: UUID) -> UserExportJob:
        job = await self.session.get(UserExportJob, job_id)
        if job is None:
            raise DashboardJobError("export_missing", "Export job not found.")
        job.status = "running"
        await self.session.flush()
        try:
            payload = await self._export_payload(job)
            path = export_file_path(self.settings, job)
            path.parent.mkdir(parents=True, exist_ok=True)
            if job.format == "csv":
                self._write_csv_export(path, payload, job.export_type)
            else:
                path.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
            job.file_url = f"/api/v1/dashboard/exports/{job.id}/download"
            job.completed_at = _now()
            job.status = "succeeded"
            job.error_code = None
            job.error_detail = None
            await self.session.flush()
            return job
        except Exception as exc:
            job.status = "failed"
            job.completed_at = _now()
            job.error_code = getattr(exc, "code", type(exc).__name__)
            job.error_detail = str(exc)[:1000]
            await self.session.flush()
            return job

    async def _load_definition(
        self,
        version_id: UUID | None,
    ) -> tuple[StrategyDefinition, StrategyVersion, Strategy]:
        if version_id is None:
            raise DashboardJobError("strategy_version_required", "Choose a strategy version.")
        version = await self.session.get(StrategyVersion, version_id)
        if version is None:
            raise DashboardJobError("strategy_version_missing", "Strategy version not found.")
        strategy = await self.session.get(Strategy, version.strategy_id)
        if strategy is None:
            raise DashboardJobError("strategy_missing", "Strategy not found.")
        return StrategyDefinition.model_validate(version.schema_json), version, strategy

    async def _fetch_candle_sets(
        self,
        definition: StrategyDefinition,
        symbol: str,
        limit: int,
        *,
        extra_timeframes: set[str] | None = None,
        center: datetime | None = None,
        before_minutes: int | None = None,
        after_minutes: int = 0,
    ) -> dict[str, list[Candle]]:
        timeframes = {
            definition.base_timeframe,
            *definition.supporting_timeframes,
            *(extra_timeframes or set()),
        }
        candle_sets: dict[str, list[Candle]] = {}
        range_fetcher = getattr(self.provider, "fetch_ohlcv_range", None)
        for timeframe in timeframes:
            timeframe_limit = limit
            if center is not None and before_minutes is not None:
                duration_minutes = max(1, int(timeframe_duration(timeframe).total_seconds() / 60))
                chart_points = (before_minutes + after_minutes) // duration_minutes + 2
                timeframe_limit = min(50_000, max(limit, chart_points + 300))
            if center is not None and before_minutes is not None and callable(range_fetcher):
                warmup = timeframe_duration(timeframe) * min(300, timeframe_limit)
                start = ensure_aware(center) - timedelta(minutes=before_minutes) - warmup
                end = ensure_aware(center) + timedelta(minutes=after_minutes)
                candle_sets[timeframe] = await range_fetcher(
                    definition.universe.exchange,
                    symbol,
                    timeframe,
                    start,
                    end,
                    timeframe_limit,
                )
            else:
                candle_sets[timeframe] = await self.provider.fetch_ohlcv(
                    definition.universe.exchange,
                    symbol,
                    timeframe,
                    timeframe_limit,
                )
        return candle_sets

    async def _evaluate(
        self,
        definition: StrategyDefinition,
        strategy: Strategy,
        version: StrategyVersion,
        symbol: str,
        candle_sets: dict[str, list[Candle]],
        evaluation_time: datetime,
        *,
        previous_score: float | None,
        chart_reference: str,
    ) -> EvaluationResult:
        metadata_loader = getattr(self.provider, "fetch_universe_metadata", None)
        metadata = {}
        if callable(metadata_loader):
            try:
                metadata = (await metadata_loader(definition.universe.exchange, [symbol])).get(
                    symbol, {}
                )
            except Exception:
                metadata = {}
        market = market_snapshot_from_candles(
            definition,
            symbol,
            candle_sets,
            evaluation_time,
            metadata,
        )
        condition_context = await self.context.build(
            definition,
            symbol,
            candle_sets,
            evaluation_time,
        )
        return self.engine.evaluate(
            definition,
            market,
            candle_sets,
            evaluation_time=evaluation_time,
            strategy_version=str(version.version_number),
            strategy_id=str(strategy.id),
            strategy_version_id=str(version.id),
            strategy_version_number=version.version_number,
            market_data_provider=type(self.provider).__name__,
            previous_score=previous_score,
            chart_reference=chart_reference,
            condition_context=condition_context,
        )

    async def _get_or_create_replay_result(self, job: SetupReplayJob) -> SetupReplayResult:
        result = await self.session.scalar(
            select(SetupReplayResult).where(SetupReplayResult.replay_job_id == job.id)
        )
        if result is not None:
            return result
        result = SetupReplayResult(
            replay_job_id=job.id,
            summary={},
            timeline_points=[],
            candle_proofs=[],
            suggested_adjustments=[],
            created_at=_now(),
        )
        self.session.add(result)
        await self.session.flush()
        return result

    async def _get_or_create_backtest_result(self, job: BacktestJob) -> BacktestResult:
        result = await self.session.scalar(
            select(BacktestResult).where(BacktestResult.backtest_job_id == job.id)
        )
        if result is not None:
            return result
        result = BacktestResult(
            backtest_job_id=job.id,
            metrics={},
            equity_curve=[],
            setup_results=[],
            created_at=_now(),
        )
        self.session.add(result)
        await self.session.flush()
        return result

    async def _export_payload(self, job: UserExportJob) -> dict[str, Any]:
        strategies = (
            await self.session.scalars(
                select(Strategy)
                .where(Strategy.user_id == job.user_id)
                .order_by(Strategy.created_at)
            )
        ).all()
        versions = (
            await self.session.scalars(
                select(StrategyVersion)
                .join(Strategy, StrategyVersion.strategy_id == Strategy.id)
                .where(Strategy.user_id == job.user_id)
                .order_by(StrategyVersion.created_at)
            )
        ).all()
        setups = (
            await self.session.scalars(
                select(SetupInstance)
                .where(SetupInstance.user_id == job.user_id)
                .order_by(SetupInstance.created_at.desc())
                .limit(1000)
            )
        ).all()
        alerts = (
            await self.session.scalars(
                select(Alert)
                .where(Alert.user_id == job.user_id)
                .order_by(Alert.created_at.desc())
                .limit(1000)
            )
        ).all()
        setup_ids = [setup.id for setup in setups]
        lifecycle_events = (
            (
                await self.session.scalars(
                    select(SetupLifecycleEvent)
                    .where(SetupLifecycleEvent.setup_instance_id.in_(setup_ids))
                    .order_by(SetupLifecycleEvent.occurred_at.asc())
                )
            ).all()
            if setup_ids
            else []
        )
        inbox_items = (
            await self.session.scalars(
                select(AlertInboxItem)
                .where(AlertInboxItem.user_id == job.user_id)
                .order_by(AlertInboxItem.created_at.desc())
                .limit(1000)
            )
        ).all()
        return {
            "export_type": job.export_type,
            "generated_at": _now().isoformat(),
            "user_id": str(job.user_id),
            "filters": job.filters,
            "strategies": [
                {
                    "id": str(strategy.id),
                    "name": strategy.name,
                    "description": strategy.description,
                    "status": strategy.status.value,
                    "active_version_id": str(strategy.active_version_id)
                    if strategy.active_version_id
                    else None,
                }
                for strategy in strategies
            ],
            "strategy_versions": [
                {
                    "id": str(version.id),
                    "strategy_id": str(version.strategy_id),
                    "version_number": version.version_number,
                    "status": version.status.value,
                    "schema_hash": version.schema_hash,
                    "schema_json": version.schema_json,
                    "preview_summary": version.preview_summary,
                }
                for version in versions
            ],
            "setups": [
                {
                    "id": str(setup.id),
                    "symbol": setup.symbol,
                    "exchange": setup.exchange,
                    "timeframe": setup.timeframe,
                    "state": setup.state.value,
                    "completion_score": float(setup.completion_score),
                    "last_evaluated_at": setup.last_evaluated_at.isoformat(),
                }
                for setup in setups
            ],
            "alerts": [
                {
                    "id": str(alert.id),
                    "title": alert.title,
                    "alert_type": alert.alert_type.value,
                    "created_at": alert.created_at.isoformat(),
                    "proof_receipt": alert.proof_receipt,
                }
                for alert in alerts
            ],
            "setup_timeline": [
                {
                    "id": str(event.id),
                    "setup_instance_id": str(event.setup_instance_id),
                    "from_state": event.from_state.value if event.from_state else None,
                    "to_state": event.to_state.value,
                    "reason_code": event.reason_code,
                    "evidence": event.evidence,
                    "occurred_at": event.occurred_at.isoformat(),
                }
                for event in lifecycle_events
            ],
            "alert_inbox": [
                {
                    "id": str(item.id),
                    "item_type": item.item_type,
                    "title": item.title,
                    "status": item.state,
                    "created_at": item.created_at.isoformat(),
                    "proof_reference": item.proof_reference,
                }
                for item in inbox_items
            ],
        }

    @staticmethod
    def _write_csv_export(path: Path, payload: dict[str, Any], export_type: str) -> None:
        section_names = {
            "strategies": ["strategies", "strategy_versions"],
            "alerts": ["alerts"],
            "setups": ["setups", "setup_timeline"],
            "dashboard": [
                "strategies",
                "strategy_versions",
                "setups",
                "setup_timeline",
                "alerts",
                "alert_inbox",
            ],
        }.get(
            export_type,
            [
                "strategies",
                "strategy_versions",
                "setups",
                "setup_timeline",
                "alerts",
                "alert_inbox",
            ],
        )
        fieldnames = ["record_type", "id", "name", "status", "timestamp", "data"]
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for section in section_names:
                for row in payload.get(section, []):
                    writer.writerow(
                        {
                            "record_type": section,
                            "id": row.get("id", ""),
                            "name": row.get("name", row.get("title", row.get("symbol", ""))),
                            "status": row.get(
                                "status",
                                row.get("alert_type", row.get("state", "")),
                            ),
                            "timestamp": row.get(
                                "created_at",
                                row.get("last_evaluated_at", ""),
                            ),
                            "data": json.dumps(row, default=str, separators=(",", ":")),
                        }
                    )


def export_file_path(settings: Settings, job: UserExportJob) -> Path:
    root = Path(settings.dashboard_export_directory).resolve()
    return root / str(job.user_id) / f"{job.id}.{job.format}"


def _now() -> datetime:
    return datetime.now(UTC)


def _candle_payload(candle: Candle) -> dict[str, Any]:
    return {
        "timestamp": ensure_aware(candle.timestamp).isoformat(),
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
        "quote_volume": candle.quote_volume,
        "is_closed": candle.is_closed,
    }


def _window_candles(
    candles: list[Candle],
    *,
    center: datetime,
    before_minutes: int,
    after_minutes: int,
) -> list[Candle]:
    center = ensure_aware(center)
    return [
        candle
        for candle in candles
        if -before_minutes * 60
        <= (ensure_aware(candle.timestamp) - center).total_seconds()
        <= after_minutes * 60
    ]


def _nearest_candles(candles: list[Candle], center: datetime, *, count: int) -> list[Candle]:
    center = ensure_aware(center)
    ordered = sorted(candles, key=lambda candle: abs(ensure_aware(candle.timestamp) - center))
    nearest = sorted(ordered[:count], key=lambda candle: ensure_aware(candle.timestamp))
    return nearest


def _timeline_point(evaluation: EvaluationResult) -> dict[str, Any]:
    proof = evaluation.proof_receipt()
    return {
        "timestamp": evaluation.evaluation_time.isoformat(),
        "outcome": evaluation.outcome.value,
        "setup_state": proof.get("setup_state"),
        "completion_score": proof.get("setup_completion_score"),
        "passed_conditions": [
            condition["condition_id"]
            for condition in proof.get("conditions", [])
            if condition.get("state") == "passed"
        ],
        "failed_conditions": [
            condition["condition_id"]
            for condition in proof.get("conditions", [])
            if condition.get("state") == "failed"
        ],
        "missing_conditions": evaluation.near_miss.to_dict().get("missing_conditions", []),
        "entry_zone": proof.get("entry_zone"),
        "risk_calculation": proof.get("risk_calculation"),
    }


def _overlays_from_result(evaluation: EvaluationResult) -> dict[str, Any]:
    proof = evaluation.proof_receipt()
    risk = proof.get("risk_calculation") or {}
    entry_zone = proof.get("entry_zone") or {}
    return {
        "entry_zone": entry_zone,
        "invalidation_level": proof.get("invalidation_level"),
        "stop_price": risk.get("stop_price"),
        "targets": risk.get("targets", []),
        "conditions": [
            {
                "condition_id": condition["condition_id"],
                "state": condition["state"],
                "actual_value": condition["actual_value"],
                "required_value": condition["required_value"],
            }
            for condition in proof.get("conditions", [])
        ],
    }


def _markers_from_timeline(
    timeline: list[dict[str, Any]],
    *,
    selected: EvaluationResult | None = None,
) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for point in timeline:
        if point["outcome"] in {"confirmed", "near_miss", "forming"}:
            markers.append(
                {
                    "time": point["timestamp"],
                    "label": point["outcome"],
                    "score": point["completion_score"],
                    "text": f"{float(point['completion_score'] or 0):.0f}%",
                }
            )
    if selected is not None:
        markers.append(
            {
                "time": selected.evaluation_time.isoformat(),
                "label": "selected",
                "score": selected.near_miss.current_score,
                "text": f"Selected {selected.near_miss.current_score:.0f}%",
            }
        )
    return markers[-100:]


def _suggestions_from_result(evaluation: EvaluationResult) -> list[dict[str, Any]]:
    missing = evaluation.near_miss.missing_conditions
    suggestions: list[dict[str, Any]] = []
    for condition in missing[:5]:
        suggestions.append(
            {
                "condition_id": condition.condition_id,
                "message": condition.explanation,
                "actual_value": condition.actual_value,
                "required_value": condition.required_value,
                "state": condition.state.value,
            }
        )
    return suggestions


def _replay_report(evaluation: EvaluationResult, evaluations: int) -> dict[str, Any]:
    proof = evaluation.proof_receipt()
    missing = evaluation.near_miss.missing_conditions
    passed = [
        condition["name"]
        for condition in proof.get("conditions", [])
        if condition.get("state") == "passed"
    ]
    blockers = [condition.name for condition in missing if getattr(condition, "mandatory", True)]
    headline = (
        "The setup would have confirmed in the replay window."
        if evaluation.outcome.value == "confirmed"
        else "The setup did not fully confirm in the replay window."
    )
    return {
        "title": "Setup Replay Report",
        "headline": headline,
        "summary": (
            f"Evaluated {evaluations} candle snapshot(s). The best deterministic score was "
            f"{evaluation.near_miss.current_score:.0f}% on {evaluation.symbol}."
        ),
        "outcome": evaluation.outcome.value,
        "best_score": round(evaluation.near_miss.current_score, 3),
        "passed_conditions": passed[:5],
        "blocking_conditions": blockers[:5],
        "next_step": ("Review the proof rows and chart markers before changing any strategy rule."),
    }


def _backtest_report(
    *,
    symbols: list[str],
    evaluated: int,
    confirmed: int,
    near_miss: int,
    setup_results: list[dict[str, Any]],
) -> dict[str, Any]:
    best = max(setup_results, key=lambda item: float(item.get("score") or 0), default=None)
    headline = (
        "Historical analysis found confirmed setup candidates."
        if confirmed
        else "Historical analysis found no confirmed setup candidates."
    )
    return {
        "title": "Historical Analysis Report",
        "headline": headline,
        "summary": (
            f"Scanned {len(symbols)} symbol(s) across {evaluated} candle evaluation(s). "
            f"Confirmed: {confirmed}. Near-miss: {near_miss}."
        ),
        "best_symbol": best.get("symbol") if best else None,
        "best_score": best.get("score") if best else None,
        "best_outcome": best.get("outcome") if best else None,
        "executed_trades": False,
        "next_step": (
            "Use this report as monitoring evidence only; no trade was executed or implied."
        ),
    }
