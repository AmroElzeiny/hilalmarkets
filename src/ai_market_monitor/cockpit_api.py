from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.ai_explanations import OpenAISuggestionNarrator
from ai_market_monitor.api.dependencies import UserPrincipal, get_market_data_provider
from ai_market_monitor.api.routers.dashboard_api import get_dashboard_principal
from ai_market_monitor.cockpit_service import StrategyCockpitService
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.db.models import (
    Alert,
    AlertInboxItem,
    AuditEvent,
    SetupInstance,
    Strategy,
    StrategyExperiment,
    StrategySuggestion,
    StrategyVersion,
)
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.interfaces import MarketDataProvider

router = APIRouter(prefix="/cockpit", tags=["strategy-cockpit"])


class StrategyValidationRequest(BaseModel):
    definition: StrategyDefinition
    strategy_id: UUID | None = None
    strategy_version_id: UUID | None = None


class FeedbackRequest(BaseModel):
    feedback_type: Literal[
        "good_alert",
        "too_early",
        "too_late",
        "false_alert",
        "too_many_alerts",
        "too_strict",
        "not_relevant",
        "bad_market_context",
        "good_idea_weak_proof",
    ]
    comment: str | None = Field(default=None, max_length=2000)
    source: Literal["dashboard", "telegram", "discord"] = "dashboard"


class UniversePreviewRequest(BaseModel):
    include_symbols: list[str] = Field(default_factory=list, max_length=5000)
    exclude_symbols: list[str] = Field(default_factory=list, max_length=5000)
    include_categories: list[str] = Field(default_factory=list, max_length=50)
    exclude_categories: list[str] = Field(default_factory=list, max_length=50)
    rank_by: Literal["quote_volume", "relative_strength", "lowest_spread"] = "quote_volume"
    result_limit: int | None = Field(default=None, ge=1, le=5000)


class SuggestionRequest(BaseModel):
    action: Literal[
        "make_stricter",
        "make_less_noisy",
        "make_trigger_earlier",
        "make_safer",
        "make_simpler",
        "explain_bottleneck",
        "add_market_context_filter",
        "add_volume_confirmation",
        "reduce_false_alerts",
        "increase_alert_frequency",
        "beginner_friendly",
        "advanced_version",
    ]


class PreferencesRequest(BaseModel):
    preferences: dict[str, Any] = Field(default_factory=dict)


class ExperimentRequest(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    version_ids: list[UUID] = Field(min_length=2, max_length=2)
    mode: Literal["dry_run", "live_monitor"] = "dry_run"


class PromoteExperimentRequest(BaseModel):
    version_id: UUID


class InboxActionRequest(BaseModel):
    action: Literal["review", "archive", "restore"]


class InboxBulkActionRequest(BaseModel):
    item_ids: list[UUID] = Field(min_length=1, max_length=200)
    action: Literal["review", "archive", "restore"]
    label: str | None = Field(default=None, max_length=40)


async def _owned_strategy(
    session: AsyncSession,
    user_id: UUID,
    strategy_id: UUID,
) -> Strategy:
    strategy = await session.get(Strategy, strategy_id)
    if strategy is None or strategy.user_id != user_id:
        raise HTTPException(status_code=404, detail="Monitor not found")
    return strategy


async def _owned_version(
    session: AsyncSession,
    user_id: UUID,
    version_id: UUID,
) -> StrategyVersion:
    version = await session.get(StrategyVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Strategy version not found")
    await _owned_strategy(session, user_id, version.strategy_id)
    return version


@router.get("/strategies/{strategy_id}/health")
async def monitor_health(
    strategy_id: UUID,
    refresh_regime: bool = Query(default=False),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    provider: MarketDataProvider = Depends(get_market_data_provider),
) -> dict[str, Any]:
    strategy = await _owned_strategy(session, principal.user_id, strategy_id)
    result = await StrategyCockpitService(session).edge_health(
        strategy,
        provider=provider if refresh_regime else None,
    )
    await session.commit()
    return result


@router.get("/strategies/{strategy_id}/bottlenecks")
async def monitor_bottlenecks(
    strategy_id: UUID,
    limit: int = Query(default=500, ge=20, le=5000),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    strategy = await _owned_strategy(session, principal.user_id, strategy_id)
    result = await StrategyCockpitService(session).condition_bottlenecks(
        strategy,
        limit=limit,
    )
    await session.commit()
    return result


@router.post("/strategies/validate")
async def validate_strategy(
    payload: StrategyValidationRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    if payload.strategy_id:
        await _owned_strategy(session, principal.user_id, payload.strategy_id)
    if payload.strategy_version_id:
        await _owned_version(session, principal.user_id, payload.strategy_version_id)
    result = await StrategyCockpitService(session).validate_definition(
        user_id=principal.user_id,
        definition=payload.definition,
        strategy_id=payload.strategy_id,
        strategy_version_id=payload.strategy_version_id,
    )
    await session.commit()
    return result


@router.post("/strategies/{strategy_id}/frequency-forecast")
async def frequency_forecast(
    strategy_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    strategy = await _owned_strategy(session, principal.user_id, strategy_id)
    result = await StrategyCockpitService(session).alert_frequency_forecast(strategy)
    await session.commit()
    return result


@router.post("/strategies/{strategy_id}/universe-preview")
async def universe_preview(
    strategy_id: UUID,
    payload: UniversePreviewRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    provider: MarketDataProvider = Depends(get_market_data_provider),
) -> dict[str, Any]:
    strategy = await _owned_strategy(session, principal.user_id, strategy_id)
    result = await StrategyCockpitService(session).preview_universe(
        user_id=principal.user_id,
        strategy=strategy,
        provider=provider,
        manual_include=payload.include_symbols,
        manual_exclude=payload.exclude_symbols,
        include_categories=payload.include_categories,
        exclude_categories=payload.exclude_categories,
        rank_by=payload.rank_by,
        result_limit=payload.result_limit,
    )
    await session.commit()
    return result


@router.post("/alerts/{alert_id}/feedback", status_code=status.HTTP_201_CREATED)
async def submit_alert_feedback(
    alert_id: UUID,
    payload: FeedbackRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    alert = await session.get(Alert, alert_id)
    if alert is None or alert.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Alert not found")
    feedback = await StrategyCockpitService(session).submit_feedback(
        user_id=principal.user_id,
        alert=alert,
        feedback_type=payload.feedback_type,
        source=payload.source,
        comment=payload.comment,
    )
    session.add(
        AuditEvent(
            actor_user_id=principal.user_id,
            actor_type=payload.source,
            action="alert.feedback_submitted",
            target_type="alert",
            target_id=str(alert.id),
            metadata_redacted={"feedback_type": payload.feedback_type},
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return {
        "id": str(feedback.id),
        "feedback_type": feedback.feedback_type,
        "message": "Feedback recorded. No strategy rule was changed.",
    }


@router.get("/alerts/{alert_id}/proof")
async def alert_proof(
    alert_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    alert = await session.get(Alert, alert_id)
    if alert is None or alert.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {
        "alert_id": str(alert.id),
        "title": alert.title,
        "proof_receipt": alert.proof_receipt,
        "chart_snapshot_url": alert.chart_snapshot_url,
        "suppressed_reason": alert.suppressed_reason,
        "exportable": True,
    }


@router.get("/setups/{setup_id}/timeline")
async def setup_timeline(
    setup_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    setup = await session.get(SetupInstance, setup_id)
    if setup is None or setup.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Setup not found")
    return await StrategyCockpitService(session).setup_timeline(setup)


@router.post("/strategies/{strategy_id}/suggestions", status_code=status.HTTP_201_CREATED)
async def create_suggestion(
    strategy_id: UUID,
    payload: SuggestionRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    strategy = await _owned_strategy(session, principal.user_id, strategy_id)
    suggestion = await StrategyCockpitService(session).generate_suggestion(
        user_id=principal.user_id,
        strategy=strategy,
        action=payload.action,
        narrator=OpenAISuggestionNarrator(settings),
    )
    await session.commit()
    return _suggestion_payload(suggestion)


@router.post("/suggestions/{suggestion_id}/apply")
async def apply_suggestion(
    suggestion_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    suggestion = await session.get(StrategySuggestion, suggestion_id)
    if suggestion is None or suggestion.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    try:
        version = await StrategyCockpitService(session).apply_suggestion(
            suggestion=suggestion,
            user_id=principal.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.add(
        AuditEvent(
            actor_user_id=principal.user_id,
            actor_type="user",
            action="strategy_suggestion.applied_as_draft",
            target_type="strategy_version",
            target_id=str(version.id),
            metadata_redacted={"suggestion_id": str(suggestion.id)},
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return {
        "suggestion": _suggestion_payload(suggestion),
        "draft_version": {
            "id": str(version.id),
            "version_number": version.version_number,
            "status": version.status.value,
            "schema_hash": version.schema_hash,
        },
        "message": "The confirmed suggestion was saved as a draft version, not activated.",
    }


@router.get("/preferences")
async def get_strategy_preferences(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    preference = await StrategyCockpitService(session).strategy_preferences(principal.user_id)
    await session.commit()
    return {
        "preferences": preference.preferences,
        "evidence": preference.evidence,
        "last_derived_at": preference.last_derived_at,
        "forget_available": True,
    }


@router.put("/preferences")
async def update_strategy_preferences(
    payload: PreferencesRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    preference = await StrategyCockpitService(session).strategy_preferences(principal.user_id)
    preference.preferences = payload.preferences
    preference.reset_at = None
    await session.commit()
    return {"preferences": preference.preferences, "updated": True}


@router.delete("/preferences")
async def forget_strategy_preferences(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    preference = await StrategyCockpitService(session).strategy_preferences(principal.user_id)
    preference.preferences = {}
    preference.evidence = {"forgotten_by_user": True}
    preference.reset_at = datetime.now(UTC)
    await session.commit()
    return {"forgotten": True, "preferences": {}}


@router.post("/strategies/{strategy_id}/experiments", status_code=status.HTTP_201_CREATED)
async def create_experiment(
    strategy_id: UUID,
    payload: ExperimentRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    strategy = await _owned_strategy(session, principal.user_id, strategy_id)
    try:
        experiment = await StrategyCockpitService(session).create_experiment(
            user_id=principal.user_id,
            strategy=strategy,
            version_ids=payload.version_ids,
            name=payload.name,
            mode=payload.mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return _experiment_payload(experiment)


@router.post("/experiments/{experiment_id}/promote")
async def promote_experiment_version(
    experiment_id: UUID,
    payload: PromoteExperimentRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    experiment = await session.get(StrategyExperiment, experiment_id)
    if experiment is None or experiment.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Experiment not found")
    version = await _owned_version(session, principal.user_id, payload.version_id)
    try:
        await StrategyCockpitService(session).promote_experiment_version(
            experiment=experiment,
            version=version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.add(
        AuditEvent(
            actor_user_id=principal.user_id,
            actor_type="user",
            action="strategy_experiment.version_promoted",
            target_type="strategy_version",
            target_id=str(version.id),
            metadata_redacted={"experiment_id": str(experiment.id)},
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return _experiment_payload(experiment)


@router.get("/experiments/{experiment_id}")
async def get_experiment(
    experiment_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    experiment = await session.get(StrategyExperiment, experiment_id)
    if experiment is None or experiment.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Experiment not found")
    await StrategyCockpitService(session).refresh_experiment(experiment)
    await session.commit()
    return _experiment_payload(experiment)


@router.post("/experiments/{experiment_id}/stop")
async def stop_experiment(
    experiment_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    experiment = await session.get(StrategyExperiment, experiment_id)
    if experiment is None or experiment.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Experiment not found")
    if experiment.status == "running":
        await StrategyCockpitService(session).refresh_experiment(experiment)
        experiment.status = "completed"
        experiment.ended_at = datetime.now(UTC)
    await session.commit()
    return _experiment_payload(experiment)


@router.get("/inbox")
async def alert_quality_inbox(
    item_type: str | None = None,
    strategy_id: UUID | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    state: str | None = None,
    feedback_status: Literal["reviewed", "unreviewed"] | None = None,
    minimum_health: float | None = Query(default=None, ge=0, le=100),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    archived: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    service = StrategyCockpitService(session)
    created = await service.sync_inbox(principal.user_id)
    query = select(AlertInboxItem).where(AlertInboxItem.user_id == principal.user_id)
    query = query.where(
        AlertInboxItem.archived_at.is_not(None)
        if archived
        else AlertInboxItem.archived_at.is_(None)
    )
    if item_type:
        query = query.where(AlertInboxItem.item_type == item_type)
    if strategy_id:
        query = query.where(AlertInboxItem.strategy_id == strategy_id)
    if symbol:
        query = query.where(AlertInboxItem.symbol == symbol.upper().replace("-", "/"))
    if timeframe:
        query = query.where(AlertInboxItem.timeframe == timeframe)
    if state:
        query = query.where(AlertInboxItem.state == state)
    if feedback_status == "reviewed":
        query = query.where(AlertInboxItem.reviewed_at.is_not(None))
    elif feedback_status == "unreviewed":
        query = query.where(AlertInboxItem.reviewed_at.is_(None))
    if minimum_health is not None:
        query = query.where(AlertInboxItem.health_score >= minimum_health)
    if date_from is not None:
        query = query.where(AlertInboxItem.created_at >= date_from)
    if date_to is not None:
        query = query.where(AlertInboxItem.created_at <= date_to)
    items = (
        await session.scalars(query.order_by(AlertInboxItem.created_at.desc()).limit(limit))
    ).all()
    await session.commit()
    return {
        "items": [_inbox_payload(item) for item in items],
        "materialized_count": created,
        "filters": {
            "item_type": item_type,
            "strategy_id": str(strategy_id) if strategy_id else None,
            "symbol": symbol,
            "timeframe": timeframe,
            "state": state,
            "feedback_status": feedback_status,
            "minimum_health": minimum_health,
            "date_from": date_from,
            "date_to": date_to,
            "archived": archived,
        },
    }


@router.post("/inbox/{item_id}")
async def update_inbox_item(
    item_id: UUID,
    payload: InboxActionRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    item = await session.get(AlertInboxItem, item_id)
    if item is None or item.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Inbox item not found")
    now = datetime.now(UTC)
    if payload.action == "review":
        item.reviewed_at = now
    elif payload.action == "archive":
        item.archived_at = now
    else:
        item.archived_at = None
    await session.commit()
    return _inbox_payload(item)


@router.post("/inbox-actions/bulk")
async def bulk_update_inbox_items(
    payload: InboxBulkActionRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    items = (
        await session.scalars(
            select(AlertInboxItem).where(
                AlertInboxItem.user_id == principal.user_id,
                AlertInboxItem.id.in_(payload.item_ids),
            )
        )
    ).all()
    now = datetime.now(UTC)
    for item in items:
        if payload.action == "review":
            item.reviewed_at = now
        elif payload.action == "archive":
            item.archived_at = now
        else:
            item.archived_at = None
        if payload.label:
            labels = set(item.labels or [])
            labels.add(payload.label)
            item.labels = sorted(labels)
    await session.commit()
    return {"updated": len(items), "action": payload.action, "label": payload.label}


@router.get("/strategies/{strategy_id}/decay")
async def strategy_decay(
    strategy_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    strategy = await _owned_strategy(session, principal.user_id, strategy_id)
    events = await StrategyCockpitService(session).detect_decay(strategy)
    await session.commit()
    return {"strategy_id": str(strategy.id), "events": events}


def _suggestion_payload(suggestion: StrategySuggestion) -> dict[str, Any]:
    return {
        "id": str(suggestion.id),
        "strategy_id": str(suggestion.strategy_id),
        "strategy_version_id": (
            str(suggestion.strategy_version_id)
            if suggestion.strategy_version_id
            else None
        ),
        "action": suggestion.action,
        "status": suggestion.status,
        "reason": suggestion.reason,
        "source": suggestion.source,
        "diff": suggestion.diff,
        "proposed_schema": suggestion.proposed_schema,
        "applied_version_id": (
            str(suggestion.applied_version_id) if suggestion.applied_version_id else None
        ),
    }


def _experiment_payload(experiment: StrategyExperiment) -> dict[str, Any]:
    return {
        "id": str(experiment.id),
        "strategy_id": str(experiment.strategy_id),
        "name": experiment.name,
        "status": experiment.status,
        "mode": experiment.mode,
        "version_ids": experiment.version_ids,
        "comparison": experiment.comparison,
        "promoted_version_id": (
            str(experiment.promoted_version_id) if experiment.promoted_version_id else None
        ),
        "started_at": experiment.started_at,
        "ended_at": experiment.ended_at,
    }


def _inbox_payload(item: AlertInboxItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "item_type": item.item_type,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "strategy_id": str(item.strategy_id) if item.strategy_id else None,
        "strategy_version_id": (
            str(item.strategy_version_id) if item.strategy_version_id else None
        ),
        "setup_instance_id": (
            str(item.setup_instance_id) if item.setup_instance_id else None
        ),
        "alert_id": str(item.alert_id) if item.alert_id else None,
        "symbol": item.symbol,
        "timeframe": item.timeframe,
        "state": item.state,
        "health_score": float(item.health_score) if item.health_score is not None else None,
        "title": item.title,
        "summary": item.summary,
        "reason": item.reason,
        "proof_reference": item.proof_reference,
        "actions": item.actions,
        "labels": item.labels,
        "reviewed_at": item.reviewed_at,
        "archived_at": item.archived_at,
        "created_at": item.created_at,
    }
