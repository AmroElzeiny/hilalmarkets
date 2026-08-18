from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.asset_logos import asset_logo
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    AlertInboxItem,
    AssetShariaAssessment,
    CanonicalAsset,
    SetupConditionResult,
    SetupInstance,
    SetupLifecycleEvent,
    Strategy,
    StrategyCondition,
    StrategyVersion,
)
from ai_market_monitor.db.models.enums import (
    ConditionOutcome,
    DeliveryStatus,
    SetupLifecycleState,
)
from ai_market_monitor.services.product_language import (
    lifecycle_presentation,
    readiness_copy,
)

LIFECYCLE_STAGES = (
    ("detected", "Detected"),
    ("forming", "Forming"),
    ("conditions_complete", "Ready for review"),
    ("alert_delivered", "Alert sent"),
    ("resolution", "Ended"),
)

STAGE_BY_STATE = {
    SetupLifecycleState.CANDIDATE_DETECTED: 0,
    SetupLifecycleState.DETECTED: 0,
    SetupLifecycleState.FORMING: 1,
    SetupLifecycleState.NEAR_CONFIRMATION: 1,
    SetupLifecycleState.ARMED: 1,
    SetupLifecycleState.BLOCKED: 1,
    SetupLifecycleState.DATA_UNAVAILABLE: 1,
    SetupLifecycleState.CONFIRMED: 2,
    SetupLifecycleState.ALERT_SENT: 3,
    SetupLifecycleState.SUPPRESSED: 3,
    SetupLifecycleState.ENTRY_ACTIVE: 3,
    SetupLifecycleState.ENTRY_ZONE_ACTIVE: 3,
    SetupLifecycleState.ENTRY_TOUCHED: 3,
    SetupLifecycleState.ENTRY_ZONE_MISSED: 4,
    SetupLifecycleState.ENTRY_MISSED: 4,
    SetupLifecycleState.INVALIDATED: 4,
    SetupLifecycleState.EXPIRED: 4,
    SetupLifecycleState.TARGET_1_REACHED: 4,
    SetupLifecycleState.TARGET_2_REACHED: 4,
    SetupLifecycleState.TARGET_REACHED: 4,
    SetupLifecycleState.STOP_REACHED: 4,
    SetupLifecycleState.STOP_LEVEL_REACHED: 4,
    SetupLifecycleState.MANUALLY_CLOSED: 4,
    SetupLifecycleState.COMPLETED: 4,
    SetupLifecycleState.CLOSED: 4,
}


async def lifecycle_cards(
    session: AsyncSession,
    user_id: UUID,
    *,
    limit: int = 100,
    monitor_id: UUID | None = None,
    muted_setup_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    muted_setup_ids = muted_setup_ids or set()
    query = (
        select(SetupInstance, Strategy.name, StrategyVersion.version_number)
        .join(StrategyVersion, StrategyVersion.id == SetupInstance.strategy_version_id)
        .join(Strategy, Strategy.id == StrategyVersion.strategy_id)
        .where(SetupInstance.user_id == user_id)
    )
    if monitor_id is not None:
        query = query.where(Strategy.id == monitor_id)
    raw_setup_rows = (
        await session.execute(
            query.order_by(SetupInstance.last_evaluated_at.desc()).limit(limit)
        )
    ).all()
    setup_rows: list[tuple[SetupInstance, str, int]] = [
        (setup, strategy_name, version_number)
        for setup, strategy_name, version_number in raw_setup_rows
        if str(setup.id) not in muted_setup_ids
    ]
    if not setup_rows:
        return []

    setup_ids = [setup.id for setup, _, _ in setup_rows]
    events = (
        await session.scalars(
            select(SetupLifecycleEvent)
            .where(SetupLifecycleEvent.setup_instance_id.in_(setup_ids))
            .order_by(SetupLifecycleEvent.occurred_at.asc())
        )
    ).all()
    condition_rows = (
        await session.execute(
            select(SetupConditionResult, StrategyCondition)
            .join(
                StrategyCondition,
                StrategyCondition.id == SetupConditionResult.strategy_condition_id,
            )
            .where(SetupConditionResult.setup_instance_id.in_(setup_ids))
            .order_by(SetupConditionResult.evaluated_at.desc())
        )
    ).all()
    alert_rows = (
        await session.scalars(
            select(Alert)
            .where(Alert.setup_instance_id.in_(setup_ids))
            .order_by(Alert.created_at.desc())
        )
    ).all()
    alert_ids = [alert.id for alert in alert_rows]
    delivery_rows = (
        await session.scalars(
            select(AlertDelivery).where(AlertDelivery.alert_id.in_(alert_ids))
        )
    ).all() if alert_ids else []
    inbox_rows = (
        await session.scalars(
            select(AlertInboxItem)
            .where(
                AlertInboxItem.setup_instance_id.in_(setup_ids),
                AlertInboxItem.archived_at.is_(None),
            )
            .order_by(AlertInboxItem.created_at.desc())
        )
    ).all()

    events_by_setup: dict[UUID, list[SetupLifecycleEvent]] = defaultdict(list)
    for event in events:
        events_by_setup[event.setup_instance_id].append(event)

    latest_conditions: dict[UUID, dict[str, tuple[SetupConditionResult, StrategyCondition]]] = (
        defaultdict(dict)
    )
    for result, definition in condition_rows:
        latest_conditions[result.setup_instance_id].setdefault(
            result.condition_key,
            (result, definition),
        )

    latest_alert_by_setup: dict[UUID, Alert] = {}
    for alert in alert_rows:
        if alert.setup_instance_id is not None:
            latest_alert_by_setup.setdefault(alert.setup_instance_id, alert)

    deliveries_by_alert: dict[UUID, list[AlertDelivery]] = defaultdict(list)
    for delivery in delivery_rows:
        deliveries_by_alert[delivery.alert_id].append(delivery)

    latest_inbox_by_setup: dict[UUID, AlertInboxItem] = {}
    for item in inbox_rows:
        if item.setup_instance_id is not None:
            latest_inbox_by_setup.setdefault(item.setup_instance_id, item)

    methodology_ids = {
        setup.sharia_methodology_id for setup, _, _ in setup_rows if setup.sharia_methodology_id
    }
    assets = {setup.symbol.partition("/")[0].upper() for setup, _, _ in setup_rows}
    current_status: dict[tuple[UUID, str], str] = {}
    if methodology_ids and assets:
        now = datetime.now(UTC)
        assessment_rows = list(
            (
                await session.scalars(
                    select(AssetShariaAssessment)
                    .where(
                        AssetShariaAssessment.methodology_id.in_(methodology_ids),
                        AssetShariaAssessment.canonical_asset.in_(assets),
                        AssetShariaAssessment.valid_from <= now,
                        (
                            AssetShariaAssessment.valid_until.is_(None)
                            | (AssetShariaAssessment.valid_until > now)
                        ),
                    )
                    .order_by(AssetShariaAssessment.valid_from.desc())
                )
            ).all()
        )
        for assessment in assessment_rows:
            current_status.setdefault(
                (assessment.methodology_id, assessment.canonical_asset),
                assessment.status.value,
            )

    asset_by_symbol: dict[str, CanonicalAsset] = {}
    if assets:
        canonical_assets = list(
            (
                await session.scalars(
                    select(CanonicalAsset)
                    .where(CanonicalAsset.symbol.in_(assets))
                    .order_by(CanonicalAsset.created_at.desc())
                )
            ).all()
        )
        for asset in canonical_assets:
            asset_by_symbol.setdefault(asset.symbol.upper(), asset)

    return [
        _lifecycle_card(
            setup,
            strategy_name,
            version_number,
            events_by_setup.get(setup.id, []),
            latest_conditions.get(setup.id, {}),
            latest_alert_by_setup.get(setup.id),
            deliveries_by_alert.get(latest_alert_by_setup[setup.id].id, [])
            if setup.id in latest_alert_by_setup
            else [],
            latest_inbox_by_setup.get(setup.id),
            current_status.get(
                (setup.sharia_methodology_id, setup.symbol.partition("/")[0].upper())
            )
            if setup.sharia_methodology_id
            else None,
            asset_by_symbol.get(setup.symbol.partition("/")[0].upper()),
        )
        for setup, strategy_name, version_number in setup_rows
    ]


def stage_index(state: SetupLifecycleState) -> int:
    return STAGE_BY_STATE.get(state, 0)


def state_label(state: SetupLifecycleState | str) -> str:
    return lifecycle_presentation(state).label


def _lifecycle_card(
    setup: SetupInstance,
    strategy_name: str,
    strategy_version_number: int,
    events: list[SetupLifecycleEvent],
    conditions: dict[str, tuple[SetupConditionResult, StrategyCondition]],
    latest_alert: Alert | None = None,
    deliveries: list[AlertDelivery] | None = None,
    latest_inbox_item: AlertInboxItem | None = None,
    current_sharia_status: str | None = None,
    asset: CanonicalAsset | None = None,
) -> dict[str, Any]:
    deliveries = deliveries or []
    current_index = stage_index(setup.state)
    stage_times: dict[int, datetime] = {0: setup.first_detected_at}
    for event in events:
        event_index = stage_index(event.to_state)
        stage_times.setdefault(event_index, event.occurred_at)
    if setup.confirmed_at is not None:
        stage_times.setdefault(2, setup.confirmed_at)
    if setup.closed_at is not None:
        stage_times.setdefault(4, setup.closed_at)

    stages = []
    for index, (key, label) in enumerate(LIFECYCLE_STAGES):
        if index < current_index:
            status = "done"
        elif index == current_index:
            status = "current"
        else:
            status = "pending"
        stages.append(
            {
                "key": key,
                "label": label,
                "status": status,
                "timestamp": stage_times.get(index),
                "detail": state_label(setup.state) if index == current_index else None,
            }
        )

    passed: list[dict[str, Any]] = []
    monitoring: list[dict[str, Any]] = []
    for result, definition in conditions.values():
        payload = {
            "key": result.condition_key,
            "name": definition.label,
            "timeframe": definition.timeframe or setup.timeframe,
            "state": result.outcome.value,
            "actual": (result.actual_value or {}).get("value"),
            "required": (result.required_value or {}).get("value"),
            "evaluated_at": result.evaluated_at,
        }
        if result.outcome == ConditionOutcome.PASSED:
            passed.append(payload)
        else:
            monitoring.append(payload)

    completed_events = [
        {
            "label": state_label(event.to_state),
            "timestamp": event.occurred_at,
            "reason": event.reason_code.replace("_", " "),
        }
        for event in events
    ]
    if not completed_events:
        completed_events.append(
            {
                "label": "Detected",
                "timestamp": setup.first_detected_at,
                "reason": "setup detected",
            }
        )
    asset_symbol = setup.symbol.partition("/")[0].upper()
    logo_symbol = asset.symbol.upper() if asset is not None else asset_symbol
    logo = asset_logo(logo_symbol, asset.provider_ids if asset is not None else None)
    return {
        "id": setup.id,
        "symbol": setup.symbol,
        "asset_symbol": logo_symbol,
        "logo_module_url": logo.module_url,
        "logo_url": logo.image_url,
        "exchange": setup.exchange,
        "timeframe": setup.timeframe,
        "direction": setup.direction,
        "strategy_name": strategy_name,
        "strategy_version_number": strategy_version_number,
        "state": setup.state.value,
        "state_label": state_label(setup.state),
        "stage_index": current_index,
        "completion_score": float(setup.completion_score),
        "readiness_label": readiness_copy(float(setup.completion_score), setup.state),
        "first_detected_at": setup.first_detected_at,
        "last_evaluated_at": setup.last_evaluated_at,
        "stages": stages,
        "completed_events": completed_events,
        "passed_conditions": passed,
        "monitoring_conditions": monitoring,
        "latest_alert_id": latest_alert.id if latest_alert else None,
        "latest_alert_title": latest_alert.title if latest_alert else None,
        "notification_delivered": any(
            item.status in {DeliveryStatus.SENT, DeliveryStatus.DELIVERED}
            for item in deliveries
        ),
        "show_why_no_alert": not any(
            item.status in {DeliveryStatus.SENT, DeliveryStatus.DELIVERED}
            for item in deliveries
        )
        and setup.state
        in {
            SetupLifecycleState.NEAR_CONFIRMATION,
            SetupLifecycleState.INVALIDATED,
            SetupLifecycleState.EXPIRED,
            SetupLifecycleState.BLOCKED,
            SetupLifecycleState.DATA_UNAVAILABLE,
            SetupLifecycleState.SUPPRESSED,
            SetupLifecycleState.COMPLETED,
        },
        "latest_inbox_item_id": latest_inbox_item.id if latest_inbox_item else None,
        "latest_inbox_state": latest_inbox_item.state if latest_inbox_item else None,
        "sharia_methodology_id": setup.sharia_methodology_id,
        "sharia_methodology_version": setup.sharia_methodology_version,
        "sharia_status_at_detection": setup.sharia_status_at_detection,
        "current_sharia_status": current_sharia_status,
    }
