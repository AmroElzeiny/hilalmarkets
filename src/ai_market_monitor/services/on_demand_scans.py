import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import PlanDefinition, timeframe_to_minutes
from ai_market_monitor.db.models import (
    AuditEvent,
    NearMissSnapshot,
    Strategy,
    StrategyVersion,
    UsageRecord,
)
from ai_market_monitor.db.models.enums import ScanOutcome, StrategyVersionStatus
from ai_market_monitor.engine.dedup import stable_event_hash
from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.engine.models import ConditionEvaluation, ensure_aware
from ai_market_monitor.provider_context import ProviderContextService
from ai_market_monitor.schemas.on_demand import (
    OnDemandConditionSummary,
    OnDemandScanMarketResult,
    OnDemandScanRequest,
    OnDemandScanResponse,
)
from ai_market_monitor.schemas.strategy import StrategyDefinition, StrategyDirection
from ai_market_monitor.services.entitlements import (
    EntitlementContext,
    EntitlementService,
    UsageService,
)
from ai_market_monitor.services.interfaces import MarketDataProvider
from ai_market_monitor.services.market_preview import market_snapshot_from_candles
from ai_market_monitor.services.sharia_universe import (
    ShariaUniverseError,
    ShariaUniverseResolver,
)
from ai_market_monitor.services.strategy_hashes import ensure_current_approved_schema_hash


class OnDemandScanError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class OnDemandScanService:
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
        self.settings = settings or Settings()
        self.context = ProviderContextService(provider, self.settings)

    async def run(self, user_id: UUID, request: OnDemandScanRequest) -> OnDemandScanResponse:
        definition, strategy, version = await self._load_definition(user_id, request)
        context = await EntitlementService(self.session).current(user_id)
        metric = "light_prompt_scans" if request.light_scan else "on_demand_scans"
        quota_limit, quota_used, period_start, period_end = await self._quota(
            context, user_id, metric=metric
        )
        if quota_limit <= 0:
            code = (
                "light_prompt_scan_not_available"
                if request.light_scan
                else "on_demand_not_available"
            )
            message = (
                "Your current plan does not include Scanner."
                if request.light_scan
                else "Your current plan does not include one-time scanning."
            )
            raise OnDemandScanError(code, message)
        if request.light_scan and not context.feature_enabled("light_prompt_scan"):
            raise OnDemandScanError(
                "light_prompt_scan_not_available",
                "Your current plan does not include Scanner.",
            )
        if quota_used >= quota_limit:
            label = "Scanner" if request.light_scan else "one-time scan"
            quota_code = (
                "light_prompt_scans_quota_exceeded"
                if request.light_scan
                else "on_demand_quota_exceeded"
            )
            raise OnDemandScanError(
                quota_code,
                f"Your plan allows {quota_limit} {label} request(s) for this period.",
            )
        definition = self._apply_symbol_override(definition, request)
        self._enforce_scan_limits(context.plan, definition, request)

        symbol_limit_key = "light_prompt_symbols" if request.light_scan else "symbols_per_strategy"
        maximum_symbols = min(
            int(context.limit(symbol_limit_key) or 0),
            int(request.max_symbols),
        )
        # Release database locks before exchange and provider network work begins.
        await self.session.commit()
        try:
            screening = await ShariaUniverseResolver(
                self.session,
                self.provider,
                self.settings,
            ).resolve(
                definition,
                user_id=user_id,
                strategy_version_id=version.id if version else None,
                maximum_symbols=maximum_symbols,
            )
        except ShariaUniverseError as exc:
            raise OnDemandScanError(exc.code, str(exc)) from exc
        if screening.monitor_paused_for_compliance:
            raise OnDemandScanError(
                "monitor_paused_for_compliance",
                "The Watch Plan was paused because a previously included asset left its "
                "screened-market policy.",
            )
        symbols = screening.included_symbols
        if not symbols:
            raise OnDemandScanError(
                "empty_screened_universe",
                "No assets currently meet this scan's screened-market policy.",
            )

        evaluated_at = datetime.now(UTC)
        symbols = await self.context.rank_symbols(definition, symbols, evaluated_at)
        results: list[OnDemandScanMarketResult] = []
        scanned_symbols: set[str] = set()
        warnings: list[str] = []
        if screening.excluded_by_policy_count:
            warnings.append(
                f"{screening.excluded_by_policy_count} asset(s) were excluded by the "
                "selected Sharia policy before technical evaluation."
            )
        screening_by_symbol = {item.symbol: item for item in screening.included}

        async def evaluate(symbol: str) -> tuple[str, list[OnDemandScanMarketResult], str | None]:
            try:
                evaluated_results = await self._evaluate_symbol(
                    definition,
                    symbol,
                    evaluated_at,
                    strategy=strategy,
                    version=version,
                    light_scan=request.light_scan,
                    include_non_confirmed=request.include_non_confirmed,
                    account_balance=request.account_balance,
                    screening_evidence=(
                        screening_by_symbol[symbol].model_dump(mode="json")
                        if symbol in screening_by_symbol
                        else None
                    ),
                    screening_context={
                        "methodology_id": str(screening.methodology_id)
                        if screening.methodology_id
                        else None,
                        "methodology_code": screening.methodology_code,
                        "methodology_version": screening.methodology_version,
                        "universe_snapshot_id": str(screening.snapshot_id)
                        if screening.snapshot_id
                        else None,
                        "universe_snapshot_hash": screening.snapshot_hash,
                        "legacy_local_bypass": screening.legacy_local_bypass,
                    },
                )
                return symbol, evaluated_results, None
            except Exception as exc:
                return symbol, [], f"{symbol}: {type(exc).__name__}: {exc}"

        if request.light_scan and version is None:
            semaphore = asyncio.Semaphore(self.settings.on_demand_scan_concurrency)

            async def bounded_evaluate(
                symbol: str,
            ) -> tuple[str, list[OnDemandScanMarketResult], str | None]:
                async with semaphore:
                    return await evaluate(symbol)

            evaluated = await asyncio.gather(*(bounded_evaluate(symbol) for symbol in symbols))
            for symbol, evaluated_results, warning in evaluated:
                if warning:
                    warnings.append(warning)
                    continue
                scanned_symbols.add(_canonical_symbol(symbol))
                results.extend(evaluated_results)
        else:
            for symbol in symbols:
                _, evaluated_results, warning = await evaluate(symbol)
                if warning:
                    warnings.append(warning)
                    continue
                scanned_symbols.add(_canonical_symbol(symbol))
                results.extend(evaluated_results)

        status: Literal["succeeded", "partial", "failed"] = "succeeded"
        if warnings and results:
            status = "partial"
        elif warnings and not results:
            status = "failed"

        usage = await UsageService(self.session).record(
            user_id,
            metric,
            period_start=period_start,
            period_end=period_end,
            idempotency_key=self._usage_key(user_id, request),
            subject_type="strategy_version" if version else "inline_strategy",
            subject_id=str(version.id) if version else definition.canonical_hash(),
            metadata={
                "symbols_requested": len(symbols),
                "symbols_scanned": len(scanned_symbols),
                "status": status,
                "scan_mode": "light_prompt" if request.light_scan else "on_demand",
                "sharia_universe_snapshot_id": str(screening.snapshot_id)
                if screening.snapshot_id
                else None,
            },
        )
        self.session.add(
            AuditEvent(
                actor_user_id=user_id,
                actor_type="user",
                action="on_demand_scan.executed",
                target_type="strategy_version" if version else "inline_strategy",
                target_id=str(version.id) if version else definition.canonical_hash(),
                metadata_redacted={
                    "status": status,
                    "symbols_requested": len(symbols),
                    "symbols_scanned": len(scanned_symbols),
                    "quota_limit": quota_limit,
                    "quota_used_before": quota_used,
                    "scan_mode": "light_prompt" if request.light_scan else "on_demand",
                    "screened_assets_considered": screening.considered_count,
                    "assets_excluded_by_sharia_policy": screening.excluded_by_policy_count,
                    "eligible_assets_scanned": len(scanned_symbols),
                },
                created_at=evaluated_at,
            )
        )
        await self.session.flush()
        sorted_results = sorted(results, key=lambda result: result.completion_score, reverse=True)
        return OnDemandScanResponse(
            status=status,
            plan_code=context.plan.code,
            quota_limit=quota_limit,
            quota_used=quota_used + 1,
            quota_remaining=max(0, quota_limit - quota_used - 1),
            symbols_requested=len(symbols),
            symbols_scanned=len(scanned_symbols),
            results=sorted_results,
            warnings=warnings,
            evaluated_at=evaluated_at,
            usage_record_id=usage.id,
            screened_assets_considered=screening.considered_count,
            assets_excluded_by_sharia_policy=screening.excluded_by_policy_count,
            assets_with_insufficient_screening_data=screening.insufficient_information_count,
            eligible_assets_scanned=len(scanned_symbols),
            sharia_methodology_id=screening.methodology_id,
            sharia_methodology_code=screening.methodology_code,
            sharia_methodology_version=screening.methodology_version,
            sharia_universe_snapshot_id=screening.snapshot_id,
            sharia_universe_snapshot_hash=screening.snapshot_hash,
        )

    async def _load_definition(
        self,
        user_id: UUID,
        request: OnDemandScanRequest,
    ) -> tuple[StrategyDefinition, Strategy | None, StrategyVersion | None]:
        if request.strategy is not None:
            return request.strategy, None, None
        if request.strategy_version_id is None:
            raise OnDemandScanError("strategy_required", "Choose a strategy to scan.")
        version = await self.session.get(StrategyVersion, request.strategy_version_id)
        if version is None:
            raise OnDemandScanError("strategy_not_found", "Strategy version not found.")
        strategy = await self.session.get(Strategy, version.strategy_id)
        if strategy is None or strategy.user_id != user_id:
            raise OnDemandScanError("strategy_not_found", "Strategy version not found.")
        if version.status == StrategyVersionStatus.SUPERSEDED:
            raise OnDemandScanError("strategy_superseded", "This strategy version is superseded.")
        if not version.approved_at or version.approved_schema_hash != version.schema_hash:
            raise OnDemandScanError(
                "approval_required",
                "Approve this exact strategy interpretation before running a scan.",
            )
        definition = StrategyDefinition.model_validate(version.schema_json)
        if not ensure_current_approved_schema_hash(version, definition):
            raise OnDemandScanError(
                "strategy_hash_mismatch",
                "The stored strategy schema no longer matches its approval hash.",
            )
        return definition, strategy, version

    @staticmethod
    def _apply_symbol_override(
        definition: StrategyDefinition, request: OnDemandScanRequest
    ) -> StrategyDefinition:
        if not request.symbols:
            return definition
        symbols = _unique_symbols(request.symbols)
        universe = definition.universe.model_copy(
            update={
                "include_symbols": symbols,
                "max_symbols": min(len(symbols), request.max_symbols),
            }
        )
        return definition.model_copy(update={"universe": universe})

    async def _quota(
        self,
        context: EntitlementContext,
        user_id: UUID,
        *,
        metric: str,
    ) -> tuple[int, int, datetime, datetime]:
        now = datetime.now(UTC)
        if metric == "light_prompt_scans":
            if not context.feature_enabled("light_prompt_scan"):
                return 0, 0, now, now
            limit = int(context.limit("light_prompt_scans_per_day") or 0)
            period_start = datetime(now.year, now.month, now.day, tzinfo=UTC)
            period_end = period_start + timedelta(days=1)
            used = await self.session.scalar(
                select(func.coalesce(func.sum(UsageRecord.quantity), 0)).where(
                    UsageRecord.user_id == user_id,
                    UsageRecord.metric == metric,
                    UsageRecord.period_start >= period_start,
                    UsageRecord.period_start < period_end,
                )
            )
            return limit, int(used or 0), period_start, period_end

        if context.source == "trial":
            limit = int(context.limit("on_demand_scans_total") or 0)
            period_start = datetime(1970, 1, 1, tzinfo=UTC)
            period_end = datetime(9999, 12, 31, tzinfo=UTC)
            used = await self.session.scalar(
                select(func.coalesce(func.sum(UsageRecord.quantity), 0)).where(
                    UsageRecord.user_id == user_id,
                    UsageRecord.metric == metric,
                )
            )
            return limit, int(used or 0), period_start, period_end

        limit = int(context.limit("on_demand_scans_per_month") or 0)
        period_start = datetime(now.year, now.month, 1, tzinfo=UTC)
        if now.month == 12:
            period_end = datetime(now.year + 1, 1, 1, tzinfo=UTC)
        else:
            period_end = datetime(now.year, now.month + 1, 1, tzinfo=UTC)
        used = await self.session.scalar(
            select(func.coalesce(func.sum(UsageRecord.quantity), 0)).where(
                UsageRecord.user_id == user_id,
                UsageRecord.metric == metric,
                UsageRecord.period_start >= period_start,
                UsageRecord.period_start < period_end,
            )
        )
        return limit, int(used or 0), period_start, period_end

    @staticmethod
    def _enforce_scan_limits(
        plan: PlanDefinition,
        definition: StrategyDefinition,
        request: OnDemandScanRequest,
    ) -> None:
        symbol_limit = int(plan.limits.get("symbols_per_strategy") or 0)
        if request.light_scan:
            symbol_limit = int(plan.limits.get("light_prompt_symbols") or symbol_limit)
        requested_symbols = len(_unique_symbols(request.symbols)) or min(
            definition.universe.max_symbols or symbol_limit,
            request.max_symbols,
        )
        if requested_symbols > symbol_limit:
            raise OnDemandScanError(
                "light_symbol_limit" if request.light_scan else "symbol_limit",
                f"Your plan allows up to {symbol_limit} symbols per scan.",
            )
        minimum_minutes = int(plan.limits.get("minimum_timeframe_minutes") or 1)
        all_timeframes = [definition.base_timeframe, *definition.supporting_timeframes]
        if any(timeframe_to_minutes(timeframe) < minimum_minutes for timeframe in all_timeframes):
            raise OnDemandScanError(
                "timeframe_not_allowed",
                f"Your plan supports {minimum_minutes}-minute timeframe or higher.",
            )

    async def _evaluate_symbol(
        self,
        definition: StrategyDefinition,
        symbol: str,
        evaluated_at: datetime,
        *,
        strategy: Strategy | None,
        version: StrategyVersion | None,
        light_scan: bool,
        include_non_confirmed: bool,
        account_balance: float | None,
        screening_evidence: dict | None,
        screening_context: dict,
    ) -> list[OnDemandScanMarketResult]:
        candle_sets = await self._fetch_candle_sets(definition, symbol)
        metadata_loader = getattr(self.provider, "fetch_universe_metadata", None)
        metadata = {}
        if callable(metadata_loader):
            try:
                metadata = (await metadata_loader(definition.universe.exchange, [symbol])).get(
                    symbol,
                    {},
                )
            except Exception:
                metadata = {}
        market = market_snapshot_from_candles(
            definition,
            symbol,
            candle_sets,
            evaluated_at,
            metadata,
        )
        condition_context = await self.context.build(
            definition,
            symbol,
            candle_sets,
            evaluated_at,
        )
        previous_score = None
        if version is not None:
            previous = await self.session.scalar(
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
            previous_score = float(previous) if isinstance(previous, Decimal) else previous
        directions: list[StrategyDirection | None] = (
            [StrategyDirection.LONG, StrategyDirection.SHORT]
            if definition.direction == StrategyDirection.BOTH
            else [None]
        )
        results: list[OnDemandScanMarketResult] = []
        for direction in directions:
            evaluation = self.engine.evaluate(
                definition,
                market,
                candle_sets,
                evaluation_time=ensure_aware(evaluated_at),
                strategy_version=(
                    str(version.version_number)
                    if version is not None
                    else f"inline:{definition.canonical_hash()[:12]}"
                ),
                strategy_id=str(strategy.id) if strategy is not None else None,
                strategy_version_id=str(version.id) if version is not None else None,
                strategy_version_number=version.version_number if version is not None else None,
                market_data_provider=type(self.provider).__name__,
                evaluation_direction=direction,
                previous_score=previous_score,
                account_balance=account_balance,
                condition_context=condition_context,
            )
            if evaluation.outcome != ScanOutcome.CONFIRMED and not include_non_confirmed:
                continue
            results.append(
                OnDemandScanMarketResult(
                    exchange=evaluation.exchange,
                    symbol=evaluation.symbol,
                    timeframe=evaluation.timeframe,
                    direction=evaluation.direction,
                    outcome=evaluation.outcome.value,
                    completion_score=round(evaluation.near_miss.current_score, 3),
                    match_percentage=(
                        100
                        if evaluation.outcome == ScanOutcome.CONFIRMED
                        else round(evaluation.near_miss.current_score, 3)
                    ),
                    trend=evaluation.near_miss.trend.value,
                    passed_conditions=[
                        self._condition_summary(condition)
                        for condition in evaluation.near_miss.passed_conditions
                    ],
                    missing_conditions=[
                        self._condition_summary(condition)
                        for condition in evaluation.near_miss.missing_conditions
                    ],
                    closest_missing_condition=(
                        self._condition_summary(evaluation.near_miss.closest_missing_condition)
                        if evaluation.near_miss.closest_missing_condition
                        else None
                    ),
                    proof_receipt=evaluation.proof_receipt()
                    | {
                        "on_demand_scan": not light_scan,
                        "light_scan": light_scan,
                        "scan_mode": "light_prompt" if light_scan else "on_demand",
                        "live_alert_created": False,
                        "sharia_screening": {
                            **screening_context,
                            "asset": screening_evidence,
                        },
                    },
                )
            )
        return results

    async def _fetch_candle_sets(
        self,
        definition: StrategyDefinition,
        symbol: str,
    ) -> dict[str, list]:
        requirements = _history_requirements(definition)
        candle_sets: dict[str, list] = {}
        for timeframe in {definition.base_timeframe, *definition.supporting_timeframes}:
            requirement = requirements.get(timeframe, {})
            limit = max(
                300,
                definition.universe.min_historical_candles,
                int(requirement.get("limit") or 0),
            )
            start = requirement.get("start")
            end = requirement.get("end")
            if start and end:
                range_candles = _range_candle_count(
                    timeframe,
                    datetime.fromisoformat(str(start)),
                    datetime.fromisoformat(str(end)),
                )
                if range_candles > 50_000:
                    raise OnDemandScanError(
                        "historical_window_too_large",
                        (
                            f"The requested {timeframe} historical window needs "
                            f"{range_candles} candles. Narrow the period or use Setup Replay."
                        ),
                    )
                limit = max(limit, range_candles + 2)
            range_fetcher = getattr(self.provider, "fetch_ohlcv_range", None)
            if start and end and callable(range_fetcher):
                candle_sets[timeframe] = await range_fetcher(
                    definition.universe.exchange,
                    symbol,
                    timeframe,
                    datetime.fromisoformat(str(start)),
                    datetime.fromisoformat(str(end)),
                    limit,
                )
            else:
                candle_sets[timeframe] = await self.provider.fetch_ohlcv(
                    definition.universe.exchange,
                    symbol,
                    timeframe,
                    min(50_000, limit),
                )
        return candle_sets

    @staticmethod
    def _condition_summary(condition: ConditionEvaluation) -> OnDemandConditionSummary:
        return OnDemandConditionSummary(
            condition_id=condition.condition_id,
            name=condition.name,
            state=condition.state.value,
            required_value=condition.required_value,
            actual_value=condition.actual_value,
            proximity_score=round(condition.proximity_score, 4),
        )

    @staticmethod
    def _usage_key(user_id: UUID, request: OnDemandScanRequest) -> str:
        if request.idempotency_key:
            return stable_event_hash(
                {
                    "user_id": str(user_id),
                    "idempotency_key": request.idempotency_key,
                    "metric": "light_prompt_scans" if request.light_scan else "on_demand_scans",
                }
            )
        prefix = "light-scan" if request.light_scan else "on-demand"
        return f"{prefix}:{uuid4()}"


def _unique_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for symbol in symbols:
        normalized = symbol.upper().replace("-", "/").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _canonical_symbol(symbol: str) -> str:
    return symbol.upper().replace("-", "/").strip().split(":", 1)[0]


def _history_requirements(definition: StrategyDefinition) -> dict[str, dict[str, int | str]]:
    requirements: dict[str, dict[str, int | str]] = {}

    def visit(node) -> None:
        if getattr(node, "node_type", None) == "condition":
            parameters = dict(getattr(node.left, "parameters", {}) or {})
            lookback = int(parameters.get("lookback") or parameters.get("period") or 1)
            search_lookback = int(parameters.get("search_lookback") or 0)
            offset = int(parameters.get("offset") or 0)
            required = max(2, lookback + search_lookback + offset + 2)
            current = requirements.setdefault(node.timeframe, {"limit": 0})
            current["limit"] = max(int(current.get("limit") or 0), required)
            if parameters.get("search_start"):
                current["start"] = str(parameters["search_start"])
            if parameters.get("search_end"):
                current["end"] = str(parameters["search_end"])
            return
        for child in getattr(node, "children", []):
            visit(child)

    visit(definition.conditions)
    return requirements


def _range_candle_count(timeframe: str, start: datetime, end: datetime) -> int:
    value = int(timeframe[:-1])
    unit = timeframe[-1]
    minutes = value if unit == "m" else value * 60 if unit == "h" else value * 1440
    return max(1, int((ensure_aware(end) - ensure_aware(start)).total_seconds() / 60 / minutes))
