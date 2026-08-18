from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.dashboard_paths import LIFECYCLES_PATH
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    ComplianceChange,
    ComplianceDriftNotification,
    ForensicInvestigation,
    SetupInstance,
    Strategy,
    StrategyVersion,
)
from ai_market_monitor.db.models.enums import (
    TERMINAL_SETUP_STATES,
    AlertType,
    DeliveryStatus,
    SetupLifecycleState,
    ShariaAssetStatus,
)
from ai_market_monitor.schemas.sharia import ActivityItem, ActivityResponse
from ai_market_monitor.services.product_language import (
    lifecycle_presentation,
    readiness_copy,
)
from ai_market_monitor.services.sharia_screening import (
    ShariaScreeningService,
    canonical_asset,
    sharia_evidence_from_proof,
)

ActivityTab = Literal[
    "all", "forming", "alerts", "ended", "compliance_changes", "investigations"
]


class ActivityReadService:
    """Bounded user-owned read model over immutable product evidence."""

    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.screening = ShariaScreeningService(session, settings)

    async def list_items(
        self,
        user_id: UUID,
        *,
        tab: ActivityTab = "all",
        monitor_id: UUID | None = None,
        symbol: str | None = None,
        sharia_status: ShariaAssetStatus | None = None,
        opportunity_state: str | None = None,
        methodology_id: UUID | None = None,
        delivery_status: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        page: int = 1,
        limit: int = 30,
    ) -> ActivityResponse:
        if monitor_id is not None:
            owned = await self.session.scalar(
                select(Strategy.id).where(
                    Strategy.id == monitor_id,
                    Strategy.user_id == user_id,
                )
            )
            if owned is None:
                raise ValueError("Watchlist not found.")
        fetch_limit = min(1000, max(limit, page * limit))
        items: list[ActivityItem] = []
        total = 0
        if tab in {"all", "forming", "ended"}:
            rows, count = await self._opportunities(
                user_id,
                tab=tab,
                monitor_id=monitor_id,
                symbol=symbol,
                state=opportunity_state,
                methodology_id=methodology_id,
                date_from=date_from,
                date_to=date_to,
                limit=fetch_limit,
            )
            items.extend(rows)
            total += count
        if tab in {"all", "alerts"}:
            rows, count = await self._alerts(
                user_id,
                monitor_id=monitor_id,
                symbol=symbol,
                methodology_id=methodology_id,
                delivery_status=delivery_status,
                date_from=date_from,
                date_to=date_to,
                limit=fetch_limit,
            )
            items.extend(rows)
            total += count
        if tab in {"all", "compliance_changes"}:
            rows, count = await self._compliance_changes(
                user_id,
                monitor_id=monitor_id,
                symbol=symbol,
                methodology_id=methodology_id,
                date_from=date_from,
                date_to=date_to,
                limit=fetch_limit,
            )
            items.extend(rows)
            total += count
        if tab in {"all", "investigations"}:
            rows, count = await self._investigations(
                user_id,
                monitor_id=monitor_id,
                symbol=symbol,
                date_from=date_from,
                date_to=date_to,
                limit=fetch_limit,
            )
            items.extend(rows)
            total += count

        await self._attach_current_statuses(items)
        if sharia_status is not None:
            items = [
                item
                for item in items
                if item.sharia_status_at_event == sharia_status
                or item.current_sharia_status == sharia_status
            ]
            # Status-at-event/current-status filtering spans heterogeneous evidence sources;
            # report the exact bounded result count rather than an ungrounded SQL estimate.
            total = len(items)
        items.sort(key=lambda item: _as_utc(item.occurred_at), reverse=True)
        start = (page - 1) * limit
        return ActivityResponse(
            items=items[start : start + limit],
            total=total,
            page=page,
            limit=limit,
        )

    async def _opportunities(
        self,
        user_id: UUID,
        *,
        tab: ActivityTab,
        monitor_id: UUID | None,
        symbol: str | None,
        state: str | None,
        methodology_id: UUID | None,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
    ) -> tuple[list[ActivityItem], int]:
        statement = (
            select(SetupInstance, Strategy.id, Strategy.name)
            .join(StrategyVersion, StrategyVersion.id == SetupInstance.strategy_version_id)
            .join(Strategy, Strategy.id == StrategyVersion.strategy_id)
            .where(SetupInstance.user_id == user_id)
        )
        count_statement = (
            select(func.count(SetupInstance.id))
            .join(StrategyVersion, StrategyVersion.id == SetupInstance.strategy_version_id)
            .join(Strategy, Strategy.id == StrategyVersion.strategy_id)
            .where(SetupInstance.user_id == user_id)
        )
        filters: list[ColumnElement[bool]] = []
        if tab == "forming":
            filters.append(SetupInstance.state.not_in(TERMINAL_SETUP_STATES))
        elif tab == "ended":
            filters.append(SetupInstance.state.in_(TERMINAL_SETUP_STATES))
        if monitor_id:
            filters.append(Strategy.id == monitor_id)
        if symbol:
            filters.append(SetupInstance.symbol == symbol.upper())
        if state:
            try:
                filters.append(SetupInstance.state == SetupLifecycleState(state))
            except ValueError:
                return [], 0
        if methodology_id:
            filters.append(SetupInstance.sharia_methodology_id == methodology_id)
        if date_from:
            filters.append(SetupInstance.last_evaluated_at >= date_from)
        if date_to:
            filters.append(SetupInstance.last_evaluated_at <= date_to)
        statement = statement.where(*filters).order_by(SetupInstance.last_evaluated_at.desc())
        count_statement = count_statement.where(*filters)
        rows = (await self.session.execute(statement.limit(limit))).all()
        count = int(await self.session.scalar(count_statement) or 0)
        items = []
        for setup, strategy_id, strategy_name in rows:
            presentation = lifecycle_presentation(setup.state)
            at_event = _status(setup.sharia_status_at_detection)
            ended = setup.state in TERMINAL_SETUP_STATES
            items.append(
                ActivityItem(
                    id=f"opportunity:{setup.id}",
                    item_type="ended" if ended else "opportunity",
                    occurred_at=setup.last_evaluated_at,
                    canonical_asset=canonical_asset(setup.symbol),
                    symbol=setup.symbol,
                    monitor_id=strategy_id,
                    monitor_name=strategy_name,
                    opportunity_state=presentation.label,
                    sharia_status_at_event=at_event,
                    current_sharia_status=None,
                    methodology_id=setup.sharia_methodology_id,
                    methodology_version=setup.sharia_methodology_version,
                    title=f"{setup.symbol} - {presentation.label}",
                    summary=(
                        f"{readiness_copy(float(setup.completion_score), setup.state)}. "
                        f"{presentation.explanation}"
                    ),
                    evidence_reference=f"{LIFECYCLES_PATH}?setup={setup.id}",
                    delivery_status=(
                        "sent" if setup.state == SetupLifecycleState.ALERT_SENT else None
                    ),
                    requires_attention=setup.state
                    in {
                        SetupLifecycleState.BLOCKED,
                        SetupLifecycleState.DATA_UNAVAILABLE,
                    },
                )
            )
        return items, count

    async def _alerts(
        self,
        user_id: UUID,
        *,
        monitor_id: UUID | None,
        symbol: str | None,
        methodology_id: UUID | None,
        delivery_status: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
    ) -> tuple[list[ActivityItem], int]:
        statement = (
            select(Alert, Strategy.id, Strategy.name, SetupInstance.symbol)
            .outerjoin(StrategyVersion, StrategyVersion.id == Alert.strategy_version_id)
            .outerjoin(Strategy, Strategy.id == StrategyVersion.strategy_id)
            .outerjoin(SetupInstance, SetupInstance.id == Alert.setup_instance_id)
            .where(Alert.user_id == user_id, Alert.alert_type != AlertType.COMPLIANCE)
        )
        count_statement = select(func.count(Alert.id)).where(
            Alert.user_id == user_id, Alert.alert_type != AlertType.COMPLIANCE
        )
        if monitor_id:
            statement = statement.where(Strategy.id == monitor_id)
            count_statement = count_statement.join(
                StrategyVersion, StrategyVersion.id == Alert.strategy_version_id
            ).where(StrategyVersion.strategy_id == monitor_id)
        if symbol:
            statement = statement.where(SetupInstance.symbol == symbol.upper())
            count_statement = count_statement.join(
                SetupInstance, SetupInstance.id == Alert.setup_instance_id
            ).where(SetupInstance.symbol == symbol.upper())
        if date_from:
            statement = statement.where(Alert.created_at >= date_from)
            count_statement = count_statement.where(Alert.created_at >= date_from)
        if date_to:
            statement = statement.where(Alert.created_at <= date_to)
            count_statement = count_statement.where(Alert.created_at <= date_to)
        rows = (
            await self.session.execute(statement.order_by(Alert.created_at.desc()).limit(limit))
        ).all()
        alert_ids = [row[0].id for row in rows]
        delivery_map: dict[UUID, list[DeliveryStatus]] = {}
        if alert_ids:
            for alert_id, status in (
                await self.session.execute(
                    select(AlertDelivery.alert_id, AlertDelivery.status).where(
                        AlertDelivery.alert_id.in_(alert_ids)
                    )
                )
            ).all():
                delivery_map.setdefault(alert_id, []).append(status)
        items = []
        for alert, strategy_id, strategy_name, alert_symbol in rows:
            proof = dict(alert.proof_receipt or {})
            screening = sharia_evidence_from_proof(proof)
            screening_asset = screening.get("asset")
            asset_evidence = screening_asset if isinstance(screening_asset, dict) else {}
            method_id = _uuid(screening.get("methodology_id"))
            if methodology_id and method_id != methodology_id:
                continue
            statuses = delivery_map.get(alert.id, [])
            delivery = _delivery_label(statuses)
            if delivery_status and delivery != delivery_status:
                continue
            status_at_event = _status(asset_evidence.get("status") or screening.get("status"))
            items.append(
                ActivityItem(
                    id=f"alert:{alert.id}",
                    item_type="alert",
                    occurred_at=alert.created_at,
                    canonical_asset=canonical_asset(alert_symbol) if alert_symbol else None,
                    symbol=alert_symbol,
                    monitor_id=strategy_id,
                    monitor_name=strategy_name,
                    opportunity_state=(
                        "Alert sent" if delivery == "delivered" else "Delivery pending"
                    ),
                    sharia_status_at_event=status_at_event,
                    current_sharia_status=None,
                    methodology_id=method_id,
                    methodology_version=_optional_text(screening.get("methodology_version")),
                    title=alert.title,
                    summary=alert.body,
                    evidence_reference=f"/dashboard/alerts/{alert.id}/proof",
                    delivery_status=delivery,
                    requires_attention=delivery in {"failed", "suppressed"},
                )
            )
        count = int(await self.session.scalar(count_statement) or 0)
        if methodology_id or delivery_status:
            count = len(items)
        return items, count

    async def _compliance_changes(
        self,
        user_id: UUID,
        *,
        monitor_id: UUID | None,
        symbol: str | None,
        methodology_id: UUID | None,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
    ) -> tuple[list[ActivityItem], int]:
        statement = (
            select(ComplianceDriftNotification, ComplianceChange, Strategy.name)
            .outerjoin(
                ComplianceChange,
                ComplianceChange.id == ComplianceDriftNotification.compliance_change_id,
            )
            .outerjoin(Strategy, Strategy.id == ComplianceDriftNotification.strategy_id)
            .where(ComplianceDriftNotification.user_id == user_id)
        )
        count_statement = select(func.count(ComplianceDriftNotification.id)).where(
            ComplianceDriftNotification.user_id == user_id
        )
        if monitor_id:
            statement = statement.where(ComplianceDriftNotification.strategy_id == monitor_id)
            count_statement = count_statement.where(
                ComplianceDriftNotification.strategy_id == monitor_id
            )
        if symbol:
            asset = canonical_asset(symbol)
            statement = statement.where(ComplianceDriftNotification.canonical_asset == asset)
            count_statement = count_statement.where(
                ComplianceDriftNotification.canonical_asset == asset
            )
        if date_from:
            statement = statement.where(ComplianceDriftNotification.created_at >= date_from)
            count_statement = count_statement.where(
                ComplianceDriftNotification.created_at >= date_from
            )
        if date_to:
            statement = statement.where(ComplianceDriftNotification.created_at <= date_to)
            count_statement = count_statement.where(
                ComplianceDriftNotification.created_at <= date_to
            )
        rows = (
            await self.session.execute(
                statement.order_by(ComplianceDriftNotification.created_at.desc()).limit(limit)
            )
        ).all()
        items = []
        for drift, change, strategy_name in rows:
            method_id = _uuid((drift.impact or {}).get("methodology_id"))
            if methodology_id and method_id != methodology_id:
                continue
            title = (
                change.title
                if change is not None
                else f"{drift.canonical_asset} screening status changed"
            )
            summary = (
                change.summary
                if change is not None
                else str((drift.impact or {}).get("policy_reason") or "Screening evidence changed.")
            )
            items.append(
                ActivityItem(
                    id=f"compliance:{drift.id}",
                    item_type="compliance_change",
                    occurred_at=drift.created_at,
                    canonical_asset=drift.canonical_asset,
                    symbol=None,
                    monitor_id=drift.strategy_id,
                    monitor_name=strategy_name,
                    opportunity_state="Paused for compliance"
                    if (drift.impact or {}).get("monitor_impact") in {"paused", "removed"}
                    else "Under review",
                    sharia_status_at_event=drift.previous_status,
                    current_sharia_status=drift.new_status,
                    methodology_id=method_id,
                    methodology_version=(drift.impact or {}).get("methodology_version"),
                    title=title,
                    summary=summary,
                    evidence_reference=f"/dashboard/market/{drift.canonical_asset}",
                    delivery_status="recorded",
                    requires_attention=drift.new_status
                    in {ShariaAssetStatus.UNDER_REVIEW, ShariaAssetStatus.EXCLUDED},
                )
            )
        count = int(await self.session.scalar(count_statement) or 0)
        if methodology_id:
            count = len(items)
        return items, count

    async def _investigations(
        self,
        user_id: UUID,
        *,
        monitor_id: UUID | None,
        symbol: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
    ) -> tuple[list[ActivityItem], int]:
        statement = (
            select(ForensicInvestigation, Strategy.name)
            .join(Strategy, Strategy.id == ForensicInvestigation.strategy_id)
            .where(ForensicInvestigation.user_id == user_id)
        )
        count_statement = select(func.count(ForensicInvestigation.id)).where(
            ForensicInvestigation.user_id == user_id
        )
        if monitor_id:
            statement = statement.where(ForensicInvestigation.strategy_id == monitor_id)
            count_statement = count_statement.where(
                ForensicInvestigation.strategy_id == monitor_id
            )
        if symbol:
            statement = statement.where(ForensicInvestigation.symbol == symbol.upper())
            count_statement = count_statement.where(ForensicInvestigation.symbol == symbol.upper())
        if date_from:
            statement = statement.where(ForensicInvestigation.created_at >= date_from)
            count_statement = count_statement.where(ForensicInvestigation.created_at >= date_from)
        if date_to:
            statement = statement.where(ForensicInvestigation.created_at <= date_to)
            count_statement = count_statement.where(ForensicInvestigation.created_at <= date_to)
        rows = (
            await self.session.execute(
                statement.order_by(ForensicInvestigation.created_at.desc()).limit(limit)
            )
        ).all()
        return (
            [
                ActivityItem(
                    id=f"investigation:{row.id}",
                    item_type="investigation",
                    occurred_at=row.completed_at or row.created_at,
                    canonical_asset=canonical_asset(row.symbol),
                    symbol=row.symbol,
                    monitor_id=row.strategy_id,
                    monitor_name=strategy_name,
                    opportunity_state="Investigation complete"
                    if row.status == "completed"
                    else "Needs attention",
                    sharia_status_at_event=_status(
                        (row.system_diagnostics or {}).get("sharia_status_at_event")
                    ),
                    current_sharia_status=None,
                    methodology_id=_uuid(
                        (row.system_diagnostics or {}).get("sharia_methodology_id")
                    ),
                    methodology_version=(row.system_diagnostics or {}).get(
                        "sharia_methodology_version"
                    ),
                    title=f"Why didn't this alert happen? - {row.symbol}",
                    summary=row.conclusion,
                    evidence_reference=f"{LIFECYCLES_PATH}?investigation={row.id}",
                    delivery_status=(row.delivery_diagnostics or {}).get("status"),
                    requires_attention=row.status != "completed",
                )
                for row, strategy_name in rows
            ],
            int(await self.session.scalar(count_statement) or 0),
        )

    async def _attach_current_statuses(self, items: list[ActivityItem]) -> None:
        by_method: dict[UUID, set[str]] = {}
        for item in items:
            if item.methodology_id and item.canonical_asset:
                by_method.setdefault(item.methodology_id, set()).add(item.canonical_asset)
        current: dict[tuple[UUID, str], ShariaAssetStatus] = {}
        all_assets = {asset for assets in by_method.values() for asset in assets}
        safety_holds = await self.screening.safety_hold_assets(assets=all_assets)
        for methodology_id, assets in by_method.items():
            assessments = await self.screening.effective_assessments(
                methodology_id,
                assets=assets,
            )
            current.update(
                {
                    (methodology_id, asset): (
                        ShariaAssetStatus.UNDER_REVIEW
                        if asset in safety_holds
                        else assessment.status
                    )
                    for asset, assessment in assessments.items()
                }
            )
        for index, item in enumerate(items):
            if item.current_sharia_status is not None:
                continue
            status = current.get((item.methodology_id, item.canonical_asset)) if (
                item.methodology_id and item.canonical_asset
            ) else None
            if status is not None:
                items[index] = item.model_copy(update={"current_sharia_status": status})


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _uuid(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value)) if value else None
    except (ValueError, TypeError):
        return None


def _status(value: object) -> ShariaAssetStatus | None:
    if isinstance(value, ShariaAssetStatus):
        return value
    try:
        return ShariaAssetStatus(str(value)) if value else None
    except ValueError:
        return None


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None


def _delivery_label(statuses: list[DeliveryStatus]) -> str:
    if not statuses:
        return "not_attempted"
    if DeliveryStatus.DELIVERED in statuses:
        return "delivered"
    if any(
        status
        in {
            DeliveryStatus.FAILED,
            DeliveryStatus.FAILED_RETRYABLE,
            DeliveryStatus.FAILED_PERMANENT,
        }
        for status in statuses
    ):
        return "failed"
    if DeliveryStatus.SUPPRESSED in statuses:
        return "suppressed"
    return "pending"
