from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import UserPrincipal, get_market_data_provider
from ai_market_monitor.api.routers.dashboard_api import get_dashboard_principal
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.csrf import csrf_token_matches
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.db.models import (
    ApprovedWatchlist,
    ApprovedWatchlistAsset,
    AssetShariaStatusHistory,
    DashboardPreference,
    SetupInstance,
    ShariaMethodology,
    Strategy,
    StrategyUniverse,
    StrategyVersion,
)
from ai_market_monitor.db.models.enums import (
    SetupLifecycleState,
    ShariaAssetStatus,
    StrategyStatus,
    UserRole,
)
from ai_market_monitor.schemas.sharia import (
    AssessmentCreateRequest,
    AssetAssessmentSummary,
    AssetPassportResponse,
    ComplianceChangeIngestRequest,
    ComplianceReviewRequest,
    LiveSpotMarketResponse,
    MethodologyComparisonResponse,
    MethodologyCreateRequest,
    MethodologyDetail,
    MethodologySummary,
    PassportProblemReportRequest,
    PassportProblemReportResponse,
    PassportQuickViewResponse,
    ScreenedAssetListResponse,
    ShariaPreferenceUpdateRequest,
    StatusHistoryResponse,
    WatchlistAffectedPlan,
    WatchlistAssetRemovalImpact,
    WatchlistCreateRequest,
    WatchlistResponse,
)
from ai_market_monitor.services.compliance_watch import (
    ComplianceWatchError,
    ComplianceWatchService,
)
from ai_market_monitor.services.interfaces import MarketDataProvider
from ai_market_monitor.services.sharia_governance import (
    ShariaGovernanceError,
    ShariaGovernanceService,
)
from ai_market_monitor.services.sharia_passports import ShariaPassportReadService
from ai_market_monitor.services.sharia_screening import (
    DEFAULT_ALLOWED_STATUSES,
    ShariaScreeningError,
    ShariaScreeningService,
    canonical_asset,
    canonical_symbol,
    methodology_is_development_only,
)
from ai_market_monitor.services.test_market_quotes import (
    TEST_METHODOLOGY_CODE,
    TestMarketQuoteService,
)

router = APIRouter(prefix="/sharia", tags=["sharia-screening"])

_OPPORTUNITY_STATES: dict[str, set[SetupLifecycleState]] = {
    "forming": {
        SetupLifecycleState.CANDIDATE_DETECTED,
        SetupLifecycleState.DETECTED,
        SetupLifecycleState.FORMING,
    },
    "getting_closer": {
        SetupLifecycleState.NEAR_CONFIRMATION,
        SetupLifecycleState.ARMED,
    },
    "ready_for_review": {
        SetupLifecycleState.CONFIRMED,
        SetupLifecycleState.ALERT_SENT,
    },
    "ended": {
        SetupLifecycleState.SUPPRESSED,
        SetupLifecycleState.INVALIDATED,
        SetupLifecycleState.EXPIRED,
        SetupLifecycleState.COMPLETED,
        SetupLifecycleState.CLOSED,
    },
}


def _admin(principal: UserPrincipal) -> None:
    if principal.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Administrator role required.")


def _screening_error(exc: ShariaScreeningError | ComplianceWatchError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": exc.code, "message": str(exc)},
    )


def _governance_error(exc: ShariaGovernanceError) -> HTTPException:
    status_code = 403 if exc.code.endswith("required") else status.HTTP_409_CONFLICT
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


async def _require_governance_mutation(
    *,
    principal: UserPrincipal,
    session: AsyncSession,
    settings: Settings,
    permission: str,
    csrf_value: str | None,
) -> None:
    _admin(principal)
    if not csrf_token_matches(settings, principal.user_id, csrf_value):
        raise HTTPException(status_code=403, detail="Invalid form token.")
    try:
        await ShariaGovernanceService(session, settings).require_permission(
            principal.user_id,
            permission,
        )
    except ShariaGovernanceError as exc:
        raise _governance_error(exc) from exc


async def _watchlist_asset_removal_impact(
    session: AsyncSession,
    *,
    user_id: UUID,
    watchlist_id: UUID,
    asset_id: str,
) -> WatchlistAssetRemovalImpact:
    watchlist = await session.get(ApprovedWatchlist, watchlist_id)
    if watchlist is None or watchlist.user_id != user_id:
        raise HTTPException(status_code=404, detail="Saved asset collection not found.")
    asset = canonical_asset(asset_id)
    saved_asset_id = await session.scalar(
        select(ApprovedWatchlistAsset.id).where(
            ApprovedWatchlistAsset.watchlist_id == watchlist.id,
            ApprovedWatchlistAsset.canonical_asset == asset,
        )
    )
    if saved_asset_id is None:
        raise HTTPException(status_code=404, detail="Saved asset not found in this collection.")
    rows = list(
        (
            await session.execute(
                select(
                    Strategy.id,
                    Strategy.name,
                    Strategy.status,
                    StrategyVersion.id,
                    StrategyVersion.version_number,
                )
                .join(StrategyVersion, Strategy.active_version_id == StrategyVersion.id)
                .join(
                    StrategyUniverse,
                    StrategyUniverse.strategy_version_id == StrategyVersion.id,
                )
                .where(
                    Strategy.user_id == user_id,
                    Strategy.status != StrategyStatus.ARCHIVED,
                    StrategyUniverse.approved_watchlist_id == watchlist.id,
                )
                .order_by(Strategy.name.asc(), StrategyVersion.version_number.desc())
            )
        ).all()
    )
    affected = [
        WatchlistAffectedPlan(
            strategy_id=strategy_id,
            name=name,
            status=strategy_status.value,
            strategy_version_id=version_id,
            strategy_version_number=version_number,
        )
        for strategy_id, name, strategy_status, version_id, version_number in rows
    ]
    return WatchlistAssetRemovalImpact(
        watchlist_id=watchlist.id,
        watchlist_name=watchlist.name,
        canonical_asset=asset,
        affected_watch_plans=affected,
        requires_confirmation=bool(affected),
    )


@router.get("/assets", response_model=ScreenedAssetListResponse)
async def screened_assets(
    methodology: UUID | None = None,
    statuses: list[ShariaAssetStatus] | None = Query(default=None, alias="status"),
    exchange: str | None = Query(default=None, min_length=2, max_length=40),
    quote_asset: str = Query(default="USDT", min_length=2, max_length=12),
    liquidity: float | None = Query(default=None, ge=0),
    search: str | None = Query(default=None, max_length=120),
    opportunity_type: str | None = Query(default=None, max_length=80),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=30, ge=1, le=100),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    provider: MarketDataProvider = Depends(get_market_data_provider),
    settings: Settings = Depends(get_settings),
) -> ScreenedAssetListResponse:
    asset_scope: set[str] | None = None
    if liquidity is not None and exchange is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "exchange_required_for_liquidity_filter",
                "message": "Choose an exchange before applying a verified liquidity filter.",
            },
        )
    if exchange:
        try:
            symbols = await provider.list_symbols(exchange, [quote_asset.upper()])
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "exchange_universe_unavailable",
                    "message": "The exchange universe is unavailable; no assets were guessed.",
                },
            ) from exc
        if liquidity is not None:
            metadata_loader = getattr(provider, "fetch_universe_metadata", None)
            if not callable(metadata_loader):
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "liquidity_filter_unavailable",
                        "message": "The configured provider cannot verify 24h liquidity.",
                    },
                )
            metadata = await metadata_loader(exchange, symbols)
            symbols = [
                symbol
                for symbol in symbols
                if (metadata.get(canonical_symbol(symbol), {}).get("quote_volume_24h") or 0)
                >= liquidity
            ]
        asset_scope = {canonical_asset(symbol) for symbol in symbols}
    if opportunity_type:
        selected_states = _OPPORTUNITY_STATES.get(opportunity_type)
        if selected_states is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "unsupported_opportunity_type",
                    "message": ("Use forming, getting_closer, ready_for_review, or ended."),
                },
            )
        opportunity_assets = {
            canonical_asset(symbol)
            for symbol in (
                await session.scalars(
                    select(SetupInstance.symbol)
                    .where(
                        SetupInstance.user_id == principal.user_id,
                        SetupInstance.state.in_(selected_states),
                    )
                    .distinct()
                )
            ).all()
        }
        asset_scope = (
            opportunity_assets if asset_scope is None else asset_scope & opportunity_assets
        )
    service = ShariaScreeningService(session, settings)
    result = await service.list_screened_assets(
        methodology_id=methodology,
        statuses=set(statuses) if statuses else DEFAULT_ALLOWED_STATUSES,
        search=search,
        asset_scope=asset_scope,
        page=page,
        limit=limit,
    )
    return result


@router.get("/test-market", response_model=LiveSpotMarketResponse)
async def test_market_quotes(
    exchange: str = Query(default="binance", pattern="^(binance|bybit)$"),
    quote_asset: str = Query(default="USDT", min_length=2, max_length=12),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    provider: MarketDataProvider = Depends(get_market_data_provider),
    settings: Settings = Depends(get_settings),
) -> LiveSpotMarketResponse:
    if settings.app_env not in {"development", "test"} or not settings.sharia_test_market_enabled:
        raise HTTPException(status_code=404, detail="The Test market is not enabled.")
    methodology_id = await session.scalar(
        select(ShariaMethodology.id)
        .where(ShariaMethodology.code == TEST_METHODOLOGY_CODE)
        .order_by(ShariaMethodology.created_at.desc())
        .limit(1)
    )
    try:
        return await TestMarketQuoteService(provider, settings).snapshot(
            exchange=exchange,
            quote_asset=quote_asset,
            methodology_id=methodology_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "live_test_market_unavailable",
                "message": "Live spot quotes are unavailable; no prices were invented.",
            },
        ) from exc


@router.get("/market-quotes", response_model=LiveSpotMarketResponse)
async def screened_market_quotes(
    methodology_id: UUID,
    exchange: str = Query(default="binance", pattern="^(binance|bybit)$"),
    quote_asset: str = Query(default="USDT", min_length=2, max_length=12),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    provider: MarketDataProvider = Depends(get_market_data_provider),
    settings: Settings = Depends(get_settings),
) -> LiveSpotMarketResponse:
    screening = ShariaScreeningService(session, settings)
    try:
        methodology = await screening.methodology(methodology_id)
    except ShariaScreeningError as exc:
        raise _screening_error(exc) from exc

    quote_service = TestMarketQuoteService(provider, settings)
    if methodology_is_development_only(methodology):
        if (
            settings.app_env not in {"development", "test"}
            or not settings.sharia_test_market_enabled
        ):
            raise HTTPException(status_code=404, detail="The Test market is not enabled.")
        try:
            return await quote_service.snapshot(
                exchange=exchange,
                quote_asset=quote_asset,
                methodology_id=methodology.id,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "live_market_unavailable",
                    "message": "Live spot quotes are unavailable; no prices were invented.",
                },
            ) from exc

    try:
        screened = await screening.list_screened_assets(
            methodology_id=methodology.id,
            statuses=DEFAULT_ALLOWED_STATUSES,
            limit=10_000,
        )
        return await quote_service.screened_snapshot(
            exchange=exchange,
            quote_asset=quote_asset,
            methodology=screening.methodology_summary(methodology),
            assessments=screened.items,
            warning=screened.warning,
        )
    except ShariaScreeningError as exc:
        raise _screening_error(exc) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "live_market_unavailable",
                "message": "Live spot quotes are unavailable; no prices were invented.",
            },
        ) from exc


@router.get("/assets/{asset_id}", response_model=AssetAssessmentSummary)
async def screened_asset(
    asset_id: str,
    methodology: UUID | None = None,
    _principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AssetAssessmentSummary:
    service = ShariaScreeningService(session, settings)
    try:
        selected = await service.resolve_methodology(methodology)
        assessment = await service.effective_assessment(selected.id, asset_id)
        if assessment is None:
            raise ShariaScreeningError(
                "assessment_not_found", "No current assessment exists for this asset."
            )
        return service.assessment_summary(assessment, selected)
    except ShariaScreeningError as exc:
        raise _screening_error(exc) from exc


@router.get("/assets/{asset_id}/passport", response_model=AssetPassportResponse)
async def asset_passport(
    asset_id: str,
    methodology: UUID | None = None,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AssetPassportResponse:
    try:
        return await ShariaPassportReadService(session, settings).current(
            asset_id,
            methodology_id=methodology,
            user_id=principal.user_id,
        )
    except ShariaScreeningError as exc:
        raise _screening_error(exc) from exc


@router.get(
    "/assets/{asset_id}/passport/quick-view",
    response_model=PassportQuickViewResponse,
)
async def asset_passport_quick_view(
    asset_id: str,
    methodology: UUID | None = None,
    passport_version_id: UUID | None = None,
    canonical_asset_id: UUID | None = None,
    event_time: datetime | None = None,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PassportQuickViewResponse:
    try:
        return await ShariaPassportReadService(session, settings).quick_view(
            asset_id,
            methodology_id=methodology,
            passport_version_id=passport_version_id,
            canonical_asset_id=canonical_asset_id,
            event_time=event_time,
            user_id=principal.user_id,
        )
    except ShariaScreeningError as exc:
        raise _screening_error(exc) from exc


@router.get(
    "/passports/{canonical_asset_id}/versions/{passport_version_id}",
    response_model=AssetPassportResponse,
)
async def historical_asset_passport(
    canonical_asset_id: UUID,
    passport_version_id: UUID,
    event_time: datetime | None = None,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AssetPassportResponse:
    try:
        return await ShariaPassportReadService(session, settings).historical(
            canonical_asset_id=canonical_asset_id,
            passport_version_id=passport_version_id,
            event_time=event_time,
            user_id=principal.user_id,
        )
    except ShariaScreeningError as exc:
        raise _screening_error(exc) from exc


@router.post(
    "/passports/{canonical_asset_id}/problem-reports",
    response_model=PassportProblemReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def report_passport_problem(
    canonical_asset_id: UUID,
    payload: PassportProblemReportRequest,
    x_csrf_token: str | None = Header(default=None),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PassportProblemReportResponse:
    if not csrf_token_matches(settings, principal.user_id, x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid form token.")
    try:
        result = await ShariaPassportReadService(session, settings).report_problem(
            user_id=principal.user_id,
            canonical_asset_id=canonical_asset_id,
            payload=payload,
        )
        await session.commit()
        return result
    except ShariaScreeningError as exc:
        await session.rollback()
        raise _screening_error(exc) from exc


@router.get("/assets/{asset_id}/history", response_model=list[StatusHistoryResponse])
async def asset_status_history(
    asset_id: str,
    methodology: UUID | None = None,
    _principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> list[StatusHistoryResponse]:
    service = ShariaScreeningService(session, settings)
    try:
        selected = await service.resolve_methodology(methodology)
    except ShariaScreeningError as exc:
        raise _screening_error(exc) from exc
    rows = list(
        (
            await session.scalars(
                select(AssetShariaStatusHistory)
                .where(
                    AssetShariaStatusHistory.canonical_asset == canonical_asset(asset_id),
                    AssetShariaStatusHistory.methodology_id == selected.id,
                )
                .order_by(AssetShariaStatusHistory.changed_at.desc())
            )
        ).all()
    )
    return [service.history_response(row) for row in rows]


@router.get(
    "/assets/{asset_id}/methodology-comparison",
    response_model=MethodologyComparisonResponse,
)
async def asset_methodology_comparison(
    asset_id: str,
    _principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MethodologyComparisonResponse:
    return await ShariaScreeningService(session, settings).methodology_comparison(asset_id)


@router.get("/methodologies", response_model=list[MethodologySummary])
async def methodologies(
    include_non_active: bool = False,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> list[MethodologySummary]:
    if include_non_active:
        _admin(principal)
    service = ShariaScreeningService(session, settings)
    rows = (
        await service.methodologies(include_non_active=True)
        if include_non_active
        else await service.executable_methodologies()
    )
    return [service.methodology_summary(row) for row in rows]


@router.get("/methodologies/{methodology_id}", response_model=MethodologyDetail)
async def methodology_detail(
    methodology_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MethodologyDetail:
    service = ShariaScreeningService(session, settings)
    try:
        row = await service.methodology(
            methodology_id,
            require_active=principal.role != UserRole.ADMIN,
        )
    except ShariaScreeningError as exc:
        raise _screening_error(exc) from exc
    return service.methodology_detail(row)


@router.get("/watchlists", response_model=list[WatchlistResponse])
async def watchlists(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> list[WatchlistResponse]:
    rows = list(
        (
            await session.scalars(
                select(ApprovedWatchlist)
                .where(ApprovedWatchlist.user_id == principal.user_id)
                .order_by(ApprovedWatchlist.is_default.desc(), ApprovedWatchlist.name.asc())
            )
        ).all()
    )
    assets = list(
        (
            await session.execute(
                select(ApprovedWatchlistAsset.watchlist_id, ApprovedWatchlistAsset.canonical_asset)
                .join(
                    ApprovedWatchlist,
                    ApprovedWatchlist.id == ApprovedWatchlistAsset.watchlist_id,
                )
                .where(ApprovedWatchlist.user_id == principal.user_id)
            )
        ).all()
    )
    by_watchlist: dict[UUID, list[str]] = {}
    for watchlist_id, asset in assets:
        by_watchlist.setdefault(watchlist_id, []).append(asset)
    return [
        WatchlistResponse(
            id=row.id,
            name=row.name,
            is_default=row.is_default,
            assets=sorted(by_watchlist.get(row.id, [])),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


@router.post("/watchlists", response_model=WatchlistResponse, status_code=201)
async def create_watchlist(
    payload: WatchlistCreateRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WatchlistResponse:
    if await session.scalar(
        select(ApprovedWatchlist.id).where(
            ApprovedWatchlist.user_id == principal.user_id,
            ApprovedWatchlist.name == payload.name,
        )
    ):
        raise HTTPException(status_code=409, detail="A watchlist with this name already exists.")
    service = ShariaScreeningService(session, settings)
    try:
        methodology = await service.resolve_methodology(None)
    except ShariaScreeningError as exc:
        raise HTTPException(
            status_code=409,
            detail="An approved active methodology is required before creating a watchlist.",
        ) from exc
    assessments = await service.effective_assessments(
        methodology.id,
        assets=set(payload.assets),
    )
    rejected = [
        asset
        for asset in payload.assets
        if assessments.get(canonical_asset(asset)) is None
        or assessments[canonical_asset(asset)].status not in DEFAULT_ALLOWED_STATUSES
    ]
    if rejected:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "watchlist_assets_not_eligible",
                "message": "Every new watchlist asset must meet the default screening policy.",
                "assets": rejected,
            },
        )
    if payload.is_default:
        existing_defaults = list(
            (
                await session.scalars(
                    select(ApprovedWatchlist).where(
                        ApprovedWatchlist.user_id == principal.user_id,
                        ApprovedWatchlist.is_default.is_(True),
                    )
                )
            ).all()
        )
        for row in existing_defaults:
            row.is_default = False
    row = ApprovedWatchlist(
        user_id=principal.user_id,
        name=payload.name,
        is_default=payload.is_default,
    )
    session.add(row)
    await session.flush()
    now = datetime.now(UTC)
    for asset in payload.assets:
        session.add(
            ApprovedWatchlistAsset(
                watchlist_id=row.id,
                canonical_asset=canonical_asset(asset),
                added_at=now,
            )
        )
    await session.commit()
    return WatchlistResponse(
        id=row.id,
        name=row.name,
        is_default=row.is_default,
        assets=payload.assets,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get(
    "/watchlists/{watchlist_id}/assets/{asset_id}/removal-impact",
    response_model=WatchlistAssetRemovalImpact,
)
async def watchlist_asset_removal_impact(
    watchlist_id: UUID,
    asset_id: str,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> WatchlistAssetRemovalImpact:
    return await _watchlist_asset_removal_impact(
        session,
        user_id=principal.user_id,
        watchlist_id=watchlist_id,
        asset_id=asset_id,
    )


@router.delete("/watchlists/{watchlist_id}/assets/{asset_id}", status_code=204)
async def remove_watchlist_asset(
    watchlist_id: UUID,
    asset_id: str,
    confirmed: bool = Query(default=False),
    x_csrf_token: str | None = Header(default=None),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> None:
    if not csrf_token_matches(settings, principal.user_id, x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid form token.")
    impact = await _watchlist_asset_removal_impact(
        session,
        user_id=principal.user_id,
        watchlist_id=watchlist_id,
        asset_id=asset_id,
    )
    if impact.requires_confirmation and not confirmed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "watchlist_asset_in_use",
                "message": (
                    "Review the affected Watch Plans before removing this saved asset."
                ),
                "impact": impact.model_dump(mode="json"),
            },
        )
    await session.execute(
        delete(ApprovedWatchlistAsset).where(
            ApprovedWatchlistAsset.watchlist_id == impact.watchlist_id,
            ApprovedWatchlistAsset.canonical_asset == impact.canonical_asset,
        )
    )
    await session.commit()


@router.put("/preferences")
async def update_sharia_preferences(
    payload: ShariaPreferenceUpdateRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    if payload.default_methodology_id is not None:
        try:
            await ShariaScreeningService(session, settings).methodology(
                payload.default_methodology_id,
                require_active=True,
            )
        except ShariaScreeningError as exc:
            raise _screening_error(exc) from exc
    preference = await session.scalar(
        select(DashboardPreference).where(DashboardPreference.user_id == principal.user_id)
    )
    if preference is None:
        preference = DashboardPreference(user_id=principal.user_id)
        session.add(preference)
    values = dict(preference.notification_preferences or {})
    values["sharia"] = {
        "default_methodology_id": str(payload.default_methodology_id)
        if payload.default_methodology_id
        else None,
        "allowed_statuses": [item.value for item in payload.allowed_statuses],
        "compliance_change_behavior": payload.compliance_change_behavior.value,
        "advanced_override_acknowledged": payload.advanced_override_acknowledged,
    }
    values.update(
        {
            "default_sharia_methodology_id": (
                str(payload.default_methodology_id) if payload.default_methodology_id else None
            ),
            "allowed_sharia_statuses": [item.value for item in payload.allowed_statuses],
            "compliance_change_behavior": payload.compliance_change_behavior.value,
            "advanced_sharia_override_acknowledged": (payload.advanced_override_acknowledged),
            "compliance_alerts_enabled": payload.compliance_alerts_enabled,
            "compliance_alert_channels": payload.compliance_alert_channels,
            "compliance_alert_digest": payload.compliance_alert_digest,
            "qualification_change_alerts": payload.qualification_change_alerts,
            "under_review_alerts": payload.under_review_alerts,
            "exclusion_alerts": payload.exclusion_alerts,
        }
    )
    preference.notification_preferences = values
    await session.commit()
    return {"status": "saved", "preferences": values["sharia"]}


@router.post("/admin/methodologies", response_model=MethodologyDetail, status_code=201)
async def create_methodology(
    payload: MethodologyCreateRequest,
    x_csrf_token: str | None = Header(default=None),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MethodologyDetail:
    await _require_governance_mutation(
        principal=principal,
        session=session,
        settings=settings,
        permission="SYSTEM_ADMIN",
        csrf_value=x_csrf_token,
    )
    service = ShariaScreeningService(session, settings)
    try:
        row = await service.create_methodology(
            payload,
            actor_user_id=principal.user_id,
            actor_identity="dashboard-admin",
        )
        await session.commit()
    except ShariaScreeningError as exc:
        await session.rollback()
        raise _screening_error(exc) from exc
    return service.methodology_detail(row)


@router.post("/admin/assessments", response_model=AssetAssessmentSummary, status_code=201)
async def create_assessment(
    payload: AssessmentCreateRequest,
    x_csrf_token: str | None = Header(default=None),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AssetAssessmentSummary:
    await _require_governance_mutation(
        principal=principal,
        session=session,
        settings=settings,
        permission="PUBLISHER",
        csrf_value=x_csrf_token,
    )
    if settings.is_deployed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "governance_publication_required",
                "message": (
                    "Initial production assessments must use System Brain research, review, "
                    "approval, and publication."
                ),
            },
        )
    service = ShariaScreeningService(session, settings)
    try:
        current = await service.effective_assessment(
            payload.methodology_id,
            payload.canonical_asset,
            as_of=payload.valid_from,
        )
        if current is not None:
            raise ShariaScreeningError(
                "compliance_review_required",
                "An existing assessment may only be superseded through Compliance Watch "
                "so monitor impact, history, cache invalidation, and drift alerts are audited.",
            )
        row = await service.create_assessment(payload, actor_user_id=principal.user_id)
        methodology = await service.methodology(row.methodology_id)
        await session.commit()
    except ShariaScreeningError as exc:
        await session.rollback()
        raise _screening_error(exc) from exc
    return service.assessment_summary(row, methodology)


@router.post("/admin/compliance-changes", status_code=201)
async def ingest_compliance_change(
    payload: ComplianceChangeIngestRequest,
    x_csrf_token: str | None = Header(default=None),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    await _require_governance_mutation(
        principal=principal,
        session=session,
        settings=settings,
        permission="RESEARCHER",
        csrf_value=x_csrf_token,
    )
    row, created = await ComplianceWatchService(session, settings).ingest_change(
        payload,
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return {"id": str(row.id), "status": row.status.value, "created": created}


@router.post("/admin/compliance-changes/{change_id}/review")
async def review_compliance_change(
    change_id: UUID,
    payload: ComplianceReviewRequest,
    x_csrf_token: str | None = Header(default=None),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    await _require_governance_mutation(
        principal=principal,
        session=session,
        settings=settings,
        permission="REVIEWER",
        csrf_value=x_csrf_token,
    )
    try:
        review, assessment_id, affected = await ComplianceWatchService(
            session, settings
        ).review_change(
            change_id,
            payload,
            reviewer_user_id=principal.user_id,
        )
        await session.commit()
    except ComplianceWatchError as exc:
        await session.rollback()
        raise _screening_error(exc) from exc
    return {
        "review_id": str(review.id),
        "decision": review.decision.value,
        "assessment_id": str(assessment_id) if assessment_id else None,
        "affected_watch_plans": affected,
    }
