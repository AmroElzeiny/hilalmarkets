import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic
from uuid import UUID

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.universe_membership import (
    membership_source_symbols,
)
from ai_market_monitor.db.models import (
    ApprovedWatchlist,
    ApprovedWatchlistAsset,
    AssetShariaAssessment,
    CanonicalAsset,
    ExchangeMarket,
    MonitorShariaAssetState,
    OperationalMetric,
    PublishedAssetAssessment,
    ShariaUniverseSnapshot,
    Strategy,
    StrategyUniverse,
    StrategyVersion,
)
from ai_market_monitor.db.models.enums import (
    ComplianceChangeBehavior,
    HealthStatus,
    MonitorShariaAssetStatus,
    ShariaAssetStatus,
    ShariaPolicyDecision,
    ShariaUniverseMode,
    StrategyStatus,
)
from ai_market_monitor.schemas.sharia import (
    ShariaUniverseExclusion,
    ShariaUniverseInclusion,
    ShariaUniverseResolutionResponse,
)
from ai_market_monitor.schemas.strategy import ShariaPolicyDefinition, StrategyDefinition
from ai_market_monitor.services.interfaces import MarketDataProvider
from ai_market_monitor.services.sharia_screening import (
    ShariaScreeningError,
    ShariaScreeningService,
    canonical_asset,
    canonical_symbol,
)
from ai_market_monitor.services.watchlist_snapshot import (
    WatchlistScope,
    scope_from_definition,
    watchlist_identity_changed,
)

logger = structlog.get_logger(__name__)


class ShariaUniverseError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ShariaUniverseResolver:
    """The single fail-closed boundary between exchange symbols and evaluation."""

    def __init__(
        self,
        session: AsyncSession,
        provider: MarketDataProvider,
        settings: Settings,
    ):
        self.session = session
        self.provider = provider
        self.settings = settings
        self.screening = ShariaScreeningService(session, settings)

    async def resolve(
        self,
        definition: StrategyDefinition,
        *,
        user_id: UUID | None = None,
        strategy_version_id: UUID | None = None,
        maximum_symbols: int | None = None,
        persist_snapshot: bool = True,
    ) -> ShariaUniverseResolutionResponse:
        started = monotonic()
        try:
            resolution = await self._resolve(
                definition,
                user_id=user_id,
                strategy_version_id=strategy_version_id,
                maximum_symbols=maximum_symbols,
                persist_snapshot=persist_snapshot,
            )
        except ShariaUniverseError as exc:
            elapsed = monotonic() - started
            self._queue_failure_metrics(reason=exc.code, elapsed=elapsed)
            logger.warning(
                "sharia_universe_resolution_failed_closed",
                reason=exc.code,
                duration_seconds=round(elapsed, 6),
            )
            raise
        except Exception as exc:
            elapsed = monotonic() - started
            reason = (
                "universe_dependency_timeout"
                if isinstance(exc, TimeoutError)
                else "universe_dependency_unavailable"
            )
            self._queue_failure_metrics(reason=reason, elapsed=elapsed)
            logger.error(
                "sharia_universe_dependency_failed_closed",
                reason=reason,
                error_type=type(exc).__name__,
                duration_seconds=round(elapsed, 6),
            )
            raise ShariaUniverseError(
                reason,
                "The Halal Market could not be verified, so no asset was made eligible.",
            ) from exc

        self._queue_resolution_metrics(resolution, elapsed=monotonic() - started)
        return resolution

    async def _resolve(
        self,
        definition: StrategyDefinition,
        *,
        user_id: UUID | None = None,
        strategy_version_id: UUID | None = None,
        maximum_symbols: int | None = None,
        persist_snapshot: bool = True,
    ) -> ShariaUniverseResolutionResponse:
        now = datetime.now(UTC)
        considered = await self._technical_symbols(definition)
        policy = definition.universe.sharia_policy
        if policy is None:
            return self._legacy_or_fail(definition, considered, maximum_symbols, now)
        if (
            policy.universe_mode == ShariaUniverseMode.EXPLICIT_ASSETS
            and not definition.universe.include_symbols
        ):
            raise ShariaUniverseError(
                "explicit_assets_required",
                "Choose at least one specific eligible asset for this Watch Plan.",
            )

        try:
            methodology = await self.screening.resolve_methodology(
                policy.methodology_id,
                as_of=now,
            )
        except ShariaScreeningError as exc:
            raise ShariaUniverseError(exc.code, str(exc)) from exc

        scoped_symbols, scope_exclusions = await self._apply_universe_mode(
            policy,
            considered,
            user_id=user_id,
            scope=scope_from_definition(definition),
        )
        assessments = await self.screening.effective_assessments(
            methodology.id,
            assets={canonical_asset(symbol) for symbol in scoped_symbols},
            as_of=now,
        )
        safety_holds = await self.screening.safety_hold_assets(
            assets={canonical_asset(symbol) for symbol in scoped_symbols}
        )
        publications = await self._active_publications(assessments)
        canonical_assets, exchange_markets = await self._market_mappings(
            publications,
            exchange=definition.universe.exchange,
            symbols=scoped_symbols,
        )
        allowed = set(policy.allowed_statuses)
        included: list[ShariaUniverseInclusion] = []
        excluded: list[ShariaUniverseExclusion] = list(scope_exclusions)
        for symbol in scoped_symbols:
            asset = canonical_asset(symbol)
            assessment = assessments.get(asset)
            if assessment is None:
                excluded.append(
                    ShariaUniverseExclusion(
                        symbol=symbol,
                        canonical_asset=asset,
                        reason_code=ShariaPolicyDecision.MISSING_ASSESSMENT.value,
                        reason=(
                            "No current evidence-backed assessment exists under the selected "
                            "methodology. The asset was excluded fail-closed."
                        ),
                        status=ShariaAssetStatus.INSUFFICIENT_INFORMATION,
                    )
                )
                continue
            publication = publications.get(assessment.id)
            canonical_record = (
                canonical_assets.get(publication.canonical_asset_id)
                if publication is not None
                else None
            )
            market = exchange_markets.get(canonical_symbol(symbol))
            if publication is None and self.settings.is_deployed:
                excluded.append(
                    ShariaUniverseExclusion(
                        symbol=symbol,
                        canonical_asset=asset,
                        reason_code="passport_version_unavailable",
                        reason=(
                            "No immutable published Passport version is bound to this "
                            "assessment, so the market was excluded fail-closed."
                        ),
                        status=assessment.status,
                        assessment_id=assessment.id,
                    )
                )
                continue
            if publication is not None and (
                canonical_record is None or canonical_record.mapping_state != "verified"
            ):
                excluded.append(
                    ShariaUniverseExclusion(
                        symbol=symbol,
                        canonical_asset=asset,
                        reason_code="canonical_identity_unverified",
                        reason=(
                            "The published assessment is not bound to a verified canonical "
                            "asset identity, so the market was excluded fail-closed."
                        ),
                        status=assessment.status,
                        assessment_id=assessment.id,
                    )
                )
                continue
            if publication is not None and (
                market is None
                or not market.is_active
                or market.market_type.casefold() != "spot"
                or market.canonical_asset_id != publication.canonical_asset_id
            ):
                excluded.append(
                    ShariaUniverseExclusion(
                        symbol=symbol,
                        canonical_asset=asset,
                        reason_code="eligible_market_unavailable",
                        reason=(
                            "The asset is screened under this methodology, but the selected "
                            "exchange spot market is unavailable or not verified for the "
                            "same canonical asset."
                        ),
                        status=assessment.status,
                        assessment_id=assessment.id,
                    )
                )
                continue
            effective_status = (
                ShariaAssetStatus.UNDER_REVIEW if asset in safety_holds else assessment.status
            )
            if effective_status not in allowed:
                excluded.append(
                    ShariaUniverseExclusion(
                        symbol=symbol,
                        canonical_asset=asset,
                        reason_code=ShariaPolicyDecision.EXCLUDED_STATUS.value,
                        reason=(
                            f"Current screening status {effective_status.value!r} is not "
                            "included by this Watch Plan's policy."
                        ),
                        status=effective_status,
                        assessment_id=assessment.id,
                    )
                )
                continue
            if (
                effective_status == ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS
                and policy.qualification_policy == "exclude"
            ):
                excluded.append(
                    ShariaUniverseExclusion(
                        symbol=symbol,
                        canonical_asset=asset,
                        reason_code=ShariaPolicyDecision.EXCLUDED_STATUS.value,
                        reason="This policy excludes assets that carry qualifications.",
                        status=assessment.status,
                        assessment_id=assessment.id,
                    )
                )
                continue
            if (
                effective_status == ShariaAssetStatus.DISPUTED
                and policy.disputed_asset_policy == "exclude"
            ):
                excluded.append(
                    ShariaUniverseExclusion(
                        symbol=symbol,
                        canonical_asset=asset,
                        reason_code=ShariaPolicyDecision.EXCLUDED_STATUS.value,
                        reason="This policy excludes disputed assets.",
                        status=assessment.status,
                        assessment_id=assessment.id,
                    )
                )
                continue
            included.append(
                ShariaUniverseInclusion(
                    symbol=symbol,
                    canonical_asset=asset,
                    canonical_asset_id=(
                        publication.canonical_asset_id if publication else None
                    ),
                    exchange_market_id=market.id if market else None,
                    status=effective_status,
                    assessment_id=assessment.id,
                    passport_version_id=publication.id if publication else None,
                    qualification=list(assessment.qualifications or []),
                    reviewed_at=assessment.reviewed_at,
                )
            )

        included.sort(key=lambda item: item.symbol)
        cap = self._effective_cap(definition, maximum_symbols)
        if cap is not None and len(included) > cap:
            for item in included[cap:]:
                excluded.append(
                    ShariaUniverseExclusion(
                        symbol=item.symbol,
                        canonical_asset=item.canonical_asset,
                        reason_code="symbol_limit",
                        reason="The current plan or Watch Plan symbol limit was reached.",
                        status=item.status,
                        assessment_id=item.assessment_id,
                    )
                )
            included = included[:cap]

        policy_hash = self._policy_hash(definition, methodology.id, policy)
        snapshot_hash = self._snapshot_hash(
            policy_hash=policy_hash,
            methodology_version=methodology.version,
            included=included,
            excluded=excluded,
        )
        snapshot: ShariaUniverseSnapshot | None = None
        if persist_snapshot:
            snapshot = await self._persist_snapshot(
                definition=definition,
                policy=policy,
                methodology=methodology,
                considered=considered,
                included=included,
                excluded=excluded,
                policy_hash=policy_hash,
                snapshot_hash=snapshot_hash,
                user_id=user_id,
                strategy_version_id=strategy_version_id,
                resolved_at=now,
            )
        monitor_paused_for_compliance = False
        if strategy_version_id is not None:
            monitor_paused_for_compliance = await self._record_monitor_asset_states(
                strategy_version_id=strategy_version_id,
                methodology_id=methodology.id,
                included=included,
                excluded=excluded,
                snapshot=snapshot,
                behavior=policy.compliance_change_behavior,
                changed_at=now,
            )
        excluded_by_policy = sum(
            item.reason_code
            in {
                ShariaPolicyDecision.EXCLUDED_STATUS.value,
                ShariaPolicyDecision.MISSING_ASSESSMENT.value,
            }
            for item in excluded
        )
        return ShariaUniverseResolutionResponse(
            included_symbols=[item.symbol for item in included],
            included=included,
            excluded=excluded,
            considered_count=len(considered),
            included_count=len(included),
            excluded_by_policy_count=excluded_by_policy,
            insufficient_information_count=sum(
                item.reason_code == ShariaPolicyDecision.MISSING_ASSESSMENT.value
                for item in excluded
            ),
            methodology_id=methodology.id,
            methodology_code=methodology.code,
            methodology_version=methodology.version,
            universe_mode=policy.universe_mode,
            policy_hash=policy_hash,
            snapshot_hash=snapshot_hash,
            snapshot_id=snapshot.id if snapshot else None,
            snapshot_version=snapshot.snapshot_version if snapshot else None,
            resolved_at=now,
            monitor_paused_for_compliance=monitor_paused_for_compliance,
        )

    def _queue_failure_metrics(self, *, reason: str, elapsed: float) -> None:
        measured_at = datetime.now(UTC)
        self.session.add_all(
            [
                OperationalMetric(
                    component="sharia_universe",
                    metric_name="sharia_fail_closed_total",
                    status=HealthStatus.DOWN,
                    value=Decimal("1"),
                    unit="resolution",
                    dimensions={"reason": reason},
                    measured_at=measured_at,
                ),
                OperationalMetric(
                    component="sharia_universe",
                    metric_name="sharia_universe_resolution_seconds",
                    status=HealthStatus.DOWN,
                    value=Decimal(str(elapsed)),
                    unit="seconds",
                    dimensions={"outcome": "failed_closed", "reason": reason},
                    measured_at=measured_at,
                ),
            ]
        )

    def _queue_resolution_metrics(
        self,
        resolution: ShariaUniverseResolutionResponse,
        *,
        elapsed: float,
    ) -> None:
        measured_at = datetime.now(UTC)
        excluded_count = len(resolution.excluded)
        considered_count = resolution.considered_count
        exclusion_rate = excluded_count / considered_count if considered_count else 0.0
        abnormal = (
            considered_count >= self.settings.sharia_abnormal_exclusion_minimum_assets
            and exclusion_rate >= self.settings.sharia_abnormal_exclusion_rate_threshold
        )
        status = HealthStatus.DEGRADED if abnormal else HealthStatus.HEALTHY
        dimensions = {
            "methodology_code": resolution.methodology_code or "legacy_local",
            "methodology_version": resolution.methodology_version or "none",
        }
        metrics = [
            OperationalMetric(
                component="sharia_universe",
                metric_name="sharia_universe_resolution_seconds",
                status=status,
                value=Decimal(str(elapsed)),
                unit="seconds",
                dimensions={**dimensions, "outcome": "resolved"},
                measured_at=measured_at,
            ),
            OperationalMetric(
                component="sharia_universe",
                metric_name="sharia_universe_included_count",
                status=status,
                value=Decimal(resolution.included_count),
                unit="assets",
                dimensions=dimensions,
                measured_at=measured_at,
            ),
            OperationalMetric(
                component="sharia_universe",
                metric_name="sharia_universe_excluded_count",
                status=status,
                value=Decimal(excluded_count),
                unit="assets",
                dimensions=dimensions,
                measured_at=measured_at,
            ),
            OperationalMetric(
                component="sharia_universe",
                metric_name="sharia_universe_exclusion_rate",
                status=status,
                value=Decimal(str(exclusion_rate)),
                unit="ratio",
                dimensions={**dimensions, "abnormal": abnormal},
                measured_at=measured_at,
            ),
        ]
        for reason, count in Counter(item.reason_code for item in resolution.excluded).items():
            metrics.append(
                OperationalMetric(
                    component="sharia_universe",
                    metric_name="sharia_fail_closed_total",
                    status=HealthStatus.DEGRADED,
                    value=Decimal(count),
                    unit="assets",
                    dimensions={**dimensions, "reason": reason},
                    measured_at=measured_at,
                )
            )
        self.session.add_all(metrics)
        if abnormal:
            logger.warning(
                "sharia_universe_abnormal_exclusion_rate",
                included_count=resolution.included_count,
                excluded_count=excluded_count,
                considered_count=considered_count,
                exclusion_rate=round(exclusion_rate, 6),
            )

    async def _active_publications(
        self,
        assessments: dict[str, AssetShariaAssessment],
    ) -> dict[UUID, PublishedAssetAssessment]:
        assessment_ids = [row.id for row in assessments.values()]
        if not assessment_ids:
            return {}
        rows = list(
            (
                await self.session.scalars(
                    select(PublishedAssetAssessment).where(
                        PublishedAssetAssessment.asset_assessment_id.in_(assessment_ids),
                        PublishedAssetAssessment.is_active.is_(True),
                        PublishedAssetAssessment.publication_state == "published",
                    )
                )
            ).all()
        )
        return {row.asset_assessment_id: row for row in rows}

    async def _market_mappings(
        self,
        publications: dict[UUID, PublishedAssetAssessment],
        *,
        exchange: str,
        symbols: list[str],
    ) -> tuple[dict[UUID, CanonicalAsset], dict[str, ExchangeMarket]]:
        asset_ids = {row.canonical_asset_id for row in publications.values()}
        if not asset_ids:
            return {}, {}
        assets = list(
            (
                await self.session.scalars(
                    select(CanonicalAsset).where(CanonicalAsset.id.in_(asset_ids))
                )
            ).all()
        )
        normalized_symbols = {canonical_symbol(symbol) for symbol in symbols}
        markets = list(
            (
                await self.session.scalars(
                    select(ExchangeMarket).where(
                        ExchangeMarket.canonical_asset_id.in_(asset_ids),
                        func.lower(ExchangeMarket.exchange) == exchange.casefold(),
                        ExchangeMarket.market_symbol.in_(normalized_symbols),
                    )
                )
            ).all()
        )
        return (
            {row.id: row for row in assets},
            {canonical_symbol(row.market_symbol): row for row in markets},
        )

    async def invalidate_methodology_snapshots(
        self,
        methodology_id: UUID,
        *,
        reason: str,
    ) -> int:
        now = datetime.now(UTC)
        rows = list(
            (
                await self.session.scalars(
                    select(ShariaUniverseSnapshot).where(
                        ShariaUniverseSnapshot.methodology_id == methodology_id,
                        ShariaUniverseSnapshot.invalidated_at.is_(None),
                    )
                )
            ).all()
        )
        for row in rows:
            row.invalidated_at = now
            row.invalidation_reason = reason[:160]
        await self.session.flush()
        return len(rows)

    async def _technical_symbols(self, definition: StrategyDefinition) -> list[str]:
        """The markets this policy could possibly cover, before screening.

        Only ``explicit_assets`` takes this list from ``include_symbols``. The other two
        modes take membership from somewhere else — the whole exchange, or a Favorites
        list — so reading ``include_symbols`` for them reads a value the user never
        authored for that purpose.

        That is what pinned dynamic monitors. A previous fix wrote each resolution's
        permitted symbols back into ``include_symbols``; this function then read them as
        the authored universe, and an "everything eligible" monitor stopped following the
        eligible market the moment it was approved. Asking the membership contract which
        field owns the answer removes the ambiguity instead of relying on the field
        happening to be empty.
        """
        policy = definition.universe.sharia_policy
        authored = membership_source_symbols(
            policy.universe_mode if policy is not None else None,
            authored_include_symbols=list(definition.universe.include_symbols),
        )
        if policy is None:
            # Legacy, unscreened, local-only. There is no membership contract to consult,
            # so the historical behaviour is kept exactly.
            authored = list(definition.universe.include_symbols)
        symbols = authored or await self.provider.list_symbols(
            definition.universe.exchange,
            definition.universe.quote_currencies,
        )
        excluded = {canonical_symbol(symbol) for symbol in definition.universe.exclude_symbols}
        quotes = {quote.upper() for quote in definition.universe.quote_currencies}
        return sorted(
            {
                canonical_symbol(symbol)
                for symbol in symbols
                if canonical_symbol(symbol) not in excluded
                and canonical_symbol(symbol).partition("/")[2].upper() in quotes
            }
        )

    async def _apply_universe_mode(
        self,
        policy: ShariaPolicyDefinition,
        considered: list[str],
        *,
        user_id: UUID | None,
        scope: WatchlistScope | None = None,
    ) -> tuple[list[str], list[ShariaUniverseExclusion]]:
        if policy.universe_mode == ShariaUniverseMode.ELIGIBLE_MARKET:
            return considered, []
        if policy.universe_mode == ShariaUniverseMode.EXPLICIT_ASSETS:
            if not considered:
                raise ShariaUniverseError(
                    "explicit_assets_required",
                    "Choose at least one specific eligible asset for this Watch Plan.",
                )
            return considered, []
        if user_id is None or policy.approved_watchlist_id is None:
            raise ShariaUniverseError(
                "watchlist_context_required",
                "An approved watchlist and authenticated owner are required.",
            )
        watchlist = await self.session.get(ApprovedWatchlist, policy.approved_watchlist_id)
        if watchlist is None or watchlist.user_id != user_id:
            raise ShariaUniverseError("watchlist_not_found", "Approved watchlist not found.")
        # A Favorites universe is *fixed to the list the user approved*. Reading the live
        # rows here is how a market added to Favorites after approval entered a running
        # monitor with no review and no record. The bound identity is checked every cycle,
        # and a changed list stops the monitor rather than quietly widening it.
        if (
            policy.approved_watchlist_version
            and scope is not None
            and await watchlist_identity_changed(
                self.session,
                watchlist.id,
                policy.approved_watchlist_version,
                scope=scope,
                require_resolved=self.settings.is_deployed,
            )
        ):
            raise ShariaUniverseError(
                "approved_watchlist_changed",
                "The markets in that Favorites list changed. Review and approve the "
                "Watch Plan again before it can run.",
            )
        assets = set(
            (
                await self.session.scalars(
                    select(ApprovedWatchlistAsset.canonical_asset).where(
                        ApprovedWatchlistAsset.watchlist_id == watchlist.id
                    )
                )
            ).all()
        )
        included = [symbol for symbol in considered if canonical_asset(symbol) in assets]
        excluded = [
            ShariaUniverseExclusion(
                symbol=symbol,
                canonical_asset=canonical_asset(symbol),
                reason_code=ShariaPolicyDecision.NOT_IN_WATCHLIST.value,
                reason="The asset is not in the selected approved watchlist.",
            )
            for symbol in considered
            if canonical_asset(symbol) not in assets
        ]
        return included, excluded

    def _legacy_or_fail(
        self,
        definition: StrategyDefinition,
        considered: list[str],
        maximum_symbols: int | None,
        now: datetime,
    ) -> ShariaUniverseResolutionResponse:
        if self.settings.sharia_screening_enforced:
            raise ShariaUniverseError(
                "sharia_policy_required",
                "Choose an approved methodology and Halal Market before scanning.",
            )
        if self.settings.is_deployed or not self.settings.sharia_allow_legacy_unscreened_local:
            raise ShariaUniverseError(
                "legacy_unscreened_forbidden",
                "Unscreened legacy universes are not allowed in this environment.",
            )
        cap = self._effective_cap(definition, maximum_symbols)
        symbols = considered[:cap] if cap is not None else considered
        return ShariaUniverseResolutionResponse(
            included_symbols=symbols,
            included=[],
            excluded=[],
            considered_count=len(considered),
            included_count=len(symbols),
            excluded_by_policy_count=0,
            insufficient_information_count=0,
            methodology_id=None,
            methodology_code=None,
            methodology_version=None,
            universe_mode=None,
            policy_hash=None,
            snapshot_hash=None,
            snapshot_id=None,
            snapshot_version=None,
            resolved_at=now,
            legacy_local_bypass=True,
        )

    @staticmethod
    def _effective_cap(
        definition: StrategyDefinition,
        maximum_symbols: int | None,
    ) -> int | None:
        caps = [
            int(value)
            for value in (maximum_symbols, definition.universe.max_symbols)
            if value is not None and int(value) > 0
        ]
        return min(caps) if caps else None

    @staticmethod
    def _policy_hash(
        definition: StrategyDefinition,
        methodology_id: UUID,
        policy: ShariaPolicyDefinition,
    ) -> str:
        payload = {
            "methodology_id": str(methodology_id),
            "policy": policy.model_dump(mode="json"),
            "exchange": definition.universe.exchange,
            "quote_currencies": sorted(definition.universe.quote_currencies),
            "include_symbols": sorted(map(canonical_symbol, definition.universe.include_symbols)),
            "exclude_symbols": sorted(map(canonical_symbol, definition.universe.exclude_symbols)),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _snapshot_hash(
        *,
        policy_hash: str,
        methodology_version: str,
        included: list[ShariaUniverseInclusion],
        excluded: list[ShariaUniverseExclusion],
    ) -> str:
        payload = {
            "policy_hash": policy_hash,
            "methodology_version": methodology_version,
            "included": [
                {
                    "symbol": item.symbol,
                    "assessment_id": str(item.assessment_id),
                    "passport_version_id": (
                        str(item.passport_version_id)
                        if item.passport_version_id
                        else None
                    ),
                    "canonical_asset_id": (
                        str(item.canonical_asset_id) if item.canonical_asset_id else None
                    ),
                    "exchange_market_id": (
                        str(item.exchange_market_id) if item.exchange_market_id else None
                    ),
                    "status": item.status.value,
                }
                for item in included
            ],
            "excluded": [
                {
                    "symbol": item.symbol,
                    "reason_code": item.reason_code,
                    "assessment_id": str(item.assessment_id) if item.assessment_id else None,
                    "status": item.status.value if item.status else None,
                }
                for item in sorted(excluded, key=lambda row: (row.symbol, row.reason_code))
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    async def _persist_snapshot(
        self,
        *,
        definition: StrategyDefinition,
        policy: ShariaPolicyDefinition,
        methodology,
        considered: list[str],
        included: list[ShariaUniverseInclusion],
        excluded: list[ShariaUniverseExclusion],
        policy_hash: str,
        snapshot_hash: str,
        user_id: UUID | None,
        strategy_version_id: UUID | None,
        resolved_at: datetime,
    ) -> ShariaUniverseSnapshot:
        existing = await self.session.scalar(
            select(ShariaUniverseSnapshot)
            .where(
                ShariaUniverseSnapshot.snapshot_hash == snapshot_hash,
                ShariaUniverseSnapshot.methodology_id == methodology.id,
                ShariaUniverseSnapshot.user_id == user_id,
                ShariaUniverseSnapshot.strategy_version_id == strategy_version_id,
                ShariaUniverseSnapshot.invalidated_at.is_(None),
                or_(
                    ShariaUniverseSnapshot.expires_at.is_(None),
                    ShariaUniverseSnapshot.expires_at > resolved_at,
                ),
            )
            .order_by(ShariaUniverseSnapshot.resolved_at.desc())
            .limit(1)
        )
        if existing is not None:
            await self._sync_strategy_universe(strategy_version_id, existing, policy)
            return existing
        maximum_version = await self.session.scalar(
            select(func.max(ShariaUniverseSnapshot.snapshot_version)).where(
                ShariaUniverseSnapshot.methodology_id == methodology.id,
                ShariaUniverseSnapshot.user_id == user_id,
                ShariaUniverseSnapshot.strategy_version_id == strategy_version_id,
            )
        )
        snapshot = ShariaUniverseSnapshot(
            user_id=user_id,
            strategy_version_id=strategy_version_id,
            methodology_id=methodology.id,
            methodology_code=methodology.code,
            methodology_version=methodology.version,
            universe_mode=policy.universe_mode,
            exchange=definition.universe.exchange,
            quote_currencies=definition.universe.quote_currencies,
            allowed_statuses=[status.value for status in policy.allowed_statuses],
            qualification_policy=policy.qualification_policy,
            disputed_asset_policy=policy.disputed_asset_policy,
            policy_hash=policy_hash,
            snapshot_hash=snapshot_hash,
            snapshot_version=int(maximum_version or 0) + 1,
            considered_symbols=considered,
            included_symbols=[item.symbol for item in included],
            excluded_assets=[item.model_dump(mode="json") for item in excluded],
            resolved_at=resolved_at,
            expires_at=resolved_at
            + timedelta(seconds=self.settings.sharia_universe_cache_ttl_seconds),
        )
        self.session.add(snapshot)
        await self.session.flush()
        await self._sync_strategy_universe(strategy_version_id, snapshot, policy)
        return snapshot

    async def _sync_strategy_universe(
        self,
        strategy_version_id: UUID | None,
        snapshot: ShariaUniverseSnapshot,
        policy: ShariaPolicyDefinition,
    ) -> None:
        if strategy_version_id is None:
            return
        universe = await self.session.scalar(
            select(StrategyUniverse).where(
                StrategyUniverse.strategy_version_id == strategy_version_id
            )
        )
        if universe is None:
            return
        universe.universe_mode = policy.universe_mode
        universe.methodology_id = snapshot.methodology_id
        universe.allowed_sharia_statuses = [item.value for item in policy.allowed_statuses]
        universe.qualification_policy = policy.qualification_policy
        universe.disputed_asset_policy = policy.disputed_asset_policy
        universe.compliance_change_behavior = policy.compliance_change_behavior
        universe.approved_watchlist_id = policy.approved_watchlist_id
        universe.universe_snapshot_version = snapshot.snapshot_version
        universe.universe_snapshot_hash = snapshot.snapshot_hash
        universe.universe_last_resolved_at = snapshot.resolved_at
        universe.sharia_policy_ready = True

    async def _record_monitor_asset_states(
        self,
        *,
        strategy_version_id: UUID,
        methodology_id: UUID,
        included: list[ShariaUniverseInclusion],
        excluded: list[ShariaUniverseExclusion],
        snapshot: ShariaUniverseSnapshot | None,
        behavior: ComplianceChangeBehavior,
        changed_at: datetime,
    ) -> bool:
        version = await self.session.get(StrategyVersion, strategy_version_id)
        if version is None:
            return False
        strategy = await self.session.get(Strategy, version.strategy_id)
        if strategy is None:
            return False
        rows = list(
            (
                await self.session.scalars(
                    select(MonitorShariaAssetState).where(
                        MonitorShariaAssetState.strategy_version_id == strategy_version_id
                    )
                )
            ).all()
        )
        by_symbol = {row.symbol: row for row in rows}
        pause_entire_monitor = False
        for included_item in included:
            row = by_symbol.get(included_item.symbol)
            if row is None:
                row = MonitorShariaAssetState(
                    user_id=strategy.user_id,
                    strategy_id=strategy.id,
                    strategy_version_id=version.id,
                    methodology_id=methodology_id,
                    canonical_asset=included_item.canonical_asset,
                    symbol=included_item.symbol,
                    changed_at=changed_at,
                )
                self.session.add(row)
            row.last_assessment_id = included_item.assessment_id
            row.sharia_status = included_item.status
            row.state = MonitorShariaAssetStatus.ACTIVE
            row.policy_decision = ShariaPolicyDecision.INCLUDED
            row.policy_reason = "Asset meets the approved screened-market policy."
            row.universe_snapshot_id = snapshot.id if snapshot else None
            row.changed_at = changed_at
        for excluded_item in excluded:
            if excluded_item.status is None:
                continue
            row = by_symbol.get(excluded_item.symbol)
            was_active = row is not None and row.state == MonitorShariaAssetStatus.ACTIVE
            if row is None:
                row = MonitorShariaAssetState(
                    user_id=strategy.user_id,
                    strategy_id=strategy.id,
                    strategy_version_id=version.id,
                    methodology_id=methodology_id,
                    canonical_asset=excluded_item.canonical_asset,
                    symbol=excluded_item.symbol,
                    changed_at=changed_at,
                )
                self.session.add(row)
            row.last_assessment_id = excluded_item.assessment_id
            row.sharia_status = excluded_item.status
            row.state = (
                MonitorShariaAssetStatus.REMOVED
                if behavior == ComplianceChangeBehavior.REMOVE_ASSET
                else MonitorShariaAssetStatus.PAUSED
            )
            row.policy_decision = (
                ShariaPolicyDecision.MISSING_ASSESSMENT
                if excluded_item.reason_code
                == ShariaPolicyDecision.MISSING_ASSESSMENT.value
                else ShariaPolicyDecision.EXCLUDED_STATUS
            )
            row.policy_reason = excluded_item.reason[:300]
            row.universe_snapshot_id = snapshot.id if snapshot else None
            row.changed_at = changed_at
            pause_entire_monitor = pause_entire_monitor or (
                was_active
                and behavior == ComplianceChangeBehavior.PAUSE_MONITOR_IF_ANY_ASSET_CHANGES
            )
        if pause_entire_monitor and strategy.status == StrategyStatus.ACTIVE:
            strategy.status = StrategyStatus.PAUSED
            strategy.paused_at = changed_at
        await self.session.flush()
        return pause_entire_monitor
