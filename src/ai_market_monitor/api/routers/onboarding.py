from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import (
    OnboardingPrincipal,
    get_market_previewer,
    get_onboarding_principal,
)
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.db.models import Strategy, StrategyVersion
from ai_market_monitor.db.models.enums import OnboardingStatus, OnboardingStep
from ai_market_monitor.schemas.onboarding import (
    ActivationRequest,
    ActivationResponse,
    ApprovalRequest,
    ContinuationRequest,
    DisclaimerRequest,
    GuidedSetupRequest,
    InterpretationResponse,
    MarketPreviewResponse,
    OnboardingSessionResponse,
    StartOnboardingRequest,
    StrategyEditRequest,
)
from ai_market_monitor.schemas.strategy import InterpretationPreview
from ai_market_monitor.services.interfaces import RecentMarketPreviewer
from ai_market_monitor.services.onboarding import OnboardingError, OnboardingService
from ai_market_monitor.services.openai_interpreter import configured_strategy_interpreter
from ai_market_monitor.services.strategy import StrategyGateError, StrategyService

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


def api_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": getattr(exc, "code", "invalid_request"), "message": str(exc)},
    )


def require_matching_session(session_id: UUID, principal: OnboardingPrincipal) -> None:
    if session_id != principal.session_id:
        raise HTTPException(status_code=404, detail="Onboarding session not found")


@router.post("/start", response_model=OnboardingSessionResponse, status_code=201)
async def start_onboarding(
    request: StartOnboardingRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> OnboardingSessionResponse:
    try:
        return await OnboardingService(session, settings).start(request)
    except (OnboardingError, StrategyGateError) as exc:
        raise api_error(exc) from exc


@router.post("/resume", response_model=OnboardingSessionResponse)
async def resume_onboarding(
    request: ContinuationRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> OnboardingSessionResponse:
    try:
        return await OnboardingService(session, settings).resume(request.token)
    except (OnboardingError, StrategyGateError, ValueError) as exc:
        if not hasattr(exc, "code"):
            exc = OnboardingError("invalid_link", str(exc))
        raise api_error(exc) from exc


@router.get("/sessions/{session_id}", response_model=OnboardingSessionResponse)
async def get_onboarding_session(
    session_id: UUID,
    principal: OnboardingPrincipal = Depends(get_onboarding_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> OnboardingSessionResponse:
    require_matching_session(session_id, principal)
    service = OnboardingService(session, settings)
    onboarding = await service.get_session(session_id, principal.user_id)
    return service.response(onboarding, session_token=None)


@router.post("/sessions/{session_id}/disclaimer", response_model=OnboardingSessionResponse)
async def accept_disclaimer(
    session_id: UUID,
    request: DisclaimerRequest,
    principal: OnboardingPrincipal = Depends(get_onboarding_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> OnboardingSessionResponse:
    require_matching_session(session_id, principal)
    try:
        service = OnboardingService(session, settings)
        onboarding = await service.get_session(session_id, principal.user_id)
        await service.accept_disclaimer(onboarding, request)
        return service.response(onboarding)
    except OnboardingError as exc:
        raise api_error(exc) from exc


@router.post("/sessions/{session_id}/guided-setup", response_model=OnboardingSessionResponse)
async def save_guided_setup(
    session_id: UUID,
    request: GuidedSetupRequest,
    principal: OnboardingPrincipal = Depends(get_onboarding_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> OnboardingSessionResponse:
    require_matching_session(session_id, principal)
    try:
        service = OnboardingService(session, settings)
        onboarding = await service.get_session(session_id, principal.user_id)
        await service.save_guided_setup(onboarding, request)
        return service.response(onboarding)
    except OnboardingError as exc:
        raise api_error(exc) from exc


@router.post("/sessions/{session_id}/interpret", response_model=InterpretationResponse)
async def interpret_strategy(
    session_id: UUID,
    principal: OnboardingPrincipal = Depends(get_onboarding_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> InterpretationResponse:
    require_matching_session(session_id, principal)
    try:
        onboarding_service = OnboardingService(session, settings)
        onboarding = await onboarding_service.get_session(session_id, principal.user_id)
        raw_guided = onboarding.state_data.get("guided_setup")
        if not raw_guided:
            raise OnboardingError("guided_setup_missing", "Complete guided setup first")
        guided = GuidedSetupRequest.model_validate(raw_guided)
        preview = await configured_strategy_interpreter(settings).interpret(guided)
        strategy, version = await StrategyService(
            session, settings.disclaimer_version
        ).create_from_interpretation(principal.user_id, preview, source_text=guided.setup_text)
        await onboarding_service.mark_interpreted(
            onboarding, strategy.id, version.id, preview.activation_blocked
        )
        await session.commit()
        return InterpretationResponse(
            strategy_id=strategy.id, strategy_version_id=version.id, preview=preview
        )
    except (OnboardingError, StrategyGateError) as exc:
        await session.rollback()
        raise api_error(exc) from exc


@router.put("/sessions/{session_id}/strategy", response_model=InterpretationResponse)
async def edit_strategy(
    session_id: UUID,
    request: StrategyEditRequest,
    principal: OnboardingPrincipal = Depends(get_onboarding_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> InterpretationResponse:
    require_matching_session(session_id, principal)
    try:
        onboarding_service = OnboardingService(session, settings)
        onboarding = await onboarding_service.get_session(session_id, principal.user_id)
        strategy_id = UUID(onboarding.state_data["strategy_id"])
        strategy = await session.get(Strategy, strategy_id)
        if strategy is None:
            raise StrategyGateError("strategy_missing", "Strategy not found")
        version = await StrategyService(session, settings.disclaimer_version).revise(
            strategy, request.strategy, user_id=principal.user_id
        )
        state = dict(onboarding.state_data)
        state["strategy_version_id"] = str(version.id)
        onboarding.state_data = state
        onboarding.status = OnboardingStatus.IN_PROGRESS
        onboarding.current_step = OnboardingStep.APPROVAL
        onboarding.version += 1
        await session.commit()
        preview = InterpretationPreview(strategy=request.strategy, interpreter="user-edit")
        return InterpretationResponse(
            strategy_id=strategy.id, strategy_version_id=version.id, preview=preview
        )
    except (KeyError, ValueError, OnboardingError, StrategyGateError) as exc:
        await session.rollback()
        if not hasattr(exc, "code"):
            exc = OnboardingError("invalid_state", "Strategy onboarding state is incomplete")
        raise api_error(exc) from exc


@router.post("/sessions/{session_id}/approve", response_model=OnboardingSessionResponse)
async def approve_strategy(
    session_id: UUID,
    request: ApprovalRequest,
    principal: OnboardingPrincipal = Depends(get_onboarding_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> OnboardingSessionResponse:
    require_matching_session(session_id, principal)
    try:
        onboarding_service = OnboardingService(session, settings)
        onboarding = await onboarding_service.get_session(session_id, principal.user_id)
        version = await session.get(
            StrategyVersion, UUID(onboarding.state_data["strategy_version_id"])
        )
        if version is None:
            raise StrategyGateError("version_missing", "Strategy version not found")
        await StrategyService(session, settings.disclaimer_version).approve(
            version,
            user_id=principal.user_id,
            expected_schema_hash=request.expected_schema_hash,
        )
        await onboarding_service.mark_approved(onboarding)
        await session.commit()
        return onboarding_service.response(onboarding)
    except (KeyError, ValueError, OnboardingError, StrategyGateError) as exc:
        await session.rollback()
        if not hasattr(exc, "code"):
            exc = OnboardingError("invalid_state", "Strategy onboarding state is incomplete")
        raise api_error(exc) from exc


@router.post("/sessions/{session_id}/preview", response_model=MarketPreviewResponse)
async def preview_strategy(
    session_id: UUID,
    principal: OnboardingPrincipal = Depends(get_onboarding_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    previewer: RecentMarketPreviewer = Depends(get_market_previewer),
) -> MarketPreviewResponse:
    require_matching_session(session_id, principal)
    try:
        onboarding_service = OnboardingService(session, settings)
        onboarding = await onboarding_service.get_session(session_id, principal.user_id)
        version = await session.get(
            StrategyVersion, UUID(onboarding.state_data["strategy_version_id"])
        )
        if version is None:
            raise StrategyGateError("version_missing", "Strategy version not found")
        result = await StrategyService(session, settings.disclaimer_version).run_preview(
            version, user_id=principal.user_id, previewer=previewer
        )
        await onboarding_service.mark_previewed(onboarding, result.status == "succeeded")
        await session.commit()
        return result
    except (KeyError, ValueError, OnboardingError, StrategyGateError) as exc:
        await session.rollback()
        if not hasattr(exc, "code"):
            exc = OnboardingError("invalid_state", "Strategy onboarding state is incomplete")
        raise api_error(exc) from exc


@router.post("/sessions/{session_id}/activate", response_model=ActivationResponse)
async def activate_strategy(
    session_id: UUID,
    request: ActivationRequest,
    principal: OnboardingPrincipal = Depends(get_onboarding_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ActivationResponse:
    require_matching_session(session_id, principal)
    try:
        onboarding_service = OnboardingService(session, settings)
        onboarding = await onboarding_service.get_session(session_id, principal.user_id)
        version = await session.get(
            StrategyVersion, UUID(onboarding.state_data["strategy_version_id"])
        )
        if version is None:
            raise StrategyGateError("version_missing", "Strategy version not found")
        strategy = await StrategyService(session, settings.disclaimer_version).activate(
            version, user_id=principal.user_id, strategy_name=request.strategy_name
        )
        await onboarding_service.complete(onboarding)
        await session.commit()
        activated_at = strategy.activated_at or datetime.now(UTC)
        return ActivationResponse(
            strategy_id=strategy.id,
            strategy_version_id=version.id,
            status=strategy.status.value,
            activated_at=activated_at.isoformat(),
        )
    except (KeyError, ValueError, OnboardingError, StrategyGateError) as exc:
        await session.rollback()
        if not hasattr(exc, "code"):
            exc = OnboardingError("invalid_state", "Strategy onboarding state is incomplete")
        raise api_error(exc) from exc
