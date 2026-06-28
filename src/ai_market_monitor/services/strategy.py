from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.db.models import (
    AuditEvent,
    DisclaimerAcceptance,
    Strategy,
    StrategyCondition,
    StrategyUniverse,
    StrategyVersion,
)
from ai_market_monitor.db.models.enums import (
    StrategyStatus,
    StrategyVersionStatus,
)
from ai_market_monitor.schemas.onboarding import MarketPreviewResponse
from ai_market_monitor.schemas.strategy import (
    ConditionGroup,
    ConditionRule,
    InterpretationPreview,
    OperandKind,
    StrategyDefinition,
)
from ai_market_monitor.services.entitlements import EntitlementError, EntitlementService
from ai_market_monitor.services.interfaces import RecentMarketPreviewer
from ai_market_monitor.services.trials import TrialLifecycleService


class StrategyGateError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class StrategyService:
    def __init__(self, session: AsyncSession, disclaimer_version: str):
        self.session = session
        self.disclaimer_version = disclaimer_version

    async def create_from_interpretation(
        self,
        user_id: UUID,
        preview: InterpretationPreview,
        *,
        source_text: str | None,
    ) -> tuple[Strategy, StrategyVersion]:
        strategy = Strategy(
            user_id=user_id,
            name=preview.strategy.name,
            description=preview.strategy.description,
            status=StrategyStatus.DRAFT,
        )
        self.session.add(strategy)
        await self.session.flush()
        version = await self._create_version(
            strategy,
            preview.strategy,
            source_text=source_text,
            assumptions=preview.assumptions,
            ambiguities=[issue.model_dump(mode="json") for issue in preview.ambiguities],
            unsupported=[issue.model_dump(mode="json") for issue in preview.unsupported_conditions],
            interpreter=preview.interpreter,
        )
        await self._audit(
            user_id,
            "strategy.interpreted",
            "strategy_version",
            version.id,
            {"interpreter": preview.interpreter, "schema_hash": version.schema_hash},
        )
        return strategy, version

    async def revise(
        self,
        strategy: Strategy,
        definition: StrategyDefinition,
        *,
        user_id: UUID,
        source_text: str | None = None,
        assumptions: list[str] | None = None,
        ambiguities: list[dict] | None = None,
        unsupported: list[dict] | None = None,
        interpreter: str = "user-edit",
    ) -> StrategyVersion:
        self._assert_owner(strategy, user_id)
        current_max = await self.session.scalar(
            select(func.max(StrategyVersion.version_number)).where(
                StrategyVersion.strategy_id == strategy.id
            )
        )
        prior_versions = (
            await self.session.scalars(
                select(StrategyVersion).where(
                    StrategyVersion.strategy_id == strategy.id,
                    StrategyVersion.status.in_(
                        [
                            StrategyVersionStatus.DRAFT,
                            StrategyVersionStatus.NEEDS_CLARIFICATION,
                            StrategyVersionStatus.APPROVED,
                            StrategyVersionStatus.READY,
                        ]
                    ),
                )
            )
        ).all()
        for prior in prior_versions:
            prior.status = StrategyVersionStatus.SUPERSEDED
        version = await self._create_version(
            strategy,
            definition,
            source_text=source_text,
            assumptions=assumptions or [],
            ambiguities=ambiguities or [],
            unsupported=unsupported or [],
            interpreter=interpreter,
            version_number=(current_max or 0) + 1,
        )
        await self._audit(
            user_id,
            "strategy.revised",
            "strategy_version",
            version.id,
            {"schema_hash": version.schema_hash},
        )
        return version

    async def approve(
        self, version: StrategyVersion, *, user_id: UUID, expected_schema_hash: str
    ) -> StrategyVersion:
        strategy = await self.session.get(Strategy, version.strategy_id)
        if strategy is None:
            raise StrategyGateError("strategy_missing", "Strategy no longer exists")
        self._assert_owner(strategy, user_id)
        if version.ambiguities:
            raise StrategyGateError(
                "ambiguities_unresolved", "Resolve all ambiguous terms before approval"
            )
        blocking_unsupported = [
            item for item in version.unsupported_conditions if item.get("blocking", True)
        ]
        if blocking_unsupported:
            raise StrategyGateError(
                "unsupported_conditions", "Remove or clarify unsupported conditions before approval"
            )
        if expected_schema_hash != version.schema_hash:
            raise StrategyGateError(
                "strategy_changed", "The strategy changed since it was displayed; review it again"
            )
        version.approved_by_user_id = user_id
        version.approved_schema_hash = version.schema_hash
        version.approved_at = datetime.now(UTC)
        version.status = StrategyVersionStatus.APPROVED
        await self._audit(
            user_id,
            "strategy.approved",
            "strategy_version",
            version.id,
            {"schema_hash": version.schema_hash},
        )
        return version

    async def run_preview(
        self,
        version: StrategyVersion,
        *,
        user_id: UUID,
        previewer: RecentMarketPreviewer,
    ) -> MarketPreviewResponse:
        strategy = await self.session.get(Strategy, version.strategy_id)
        if strategy is None:
            raise StrategyGateError("strategy_missing", "Strategy no longer exists")
        self._assert_owner(strategy, user_id)
        self._assert_approval_intact(version)
        version.status = StrategyVersionStatus.PREVIEWING
        definition = StrategyDefinition.model_validate(version.schema_json)
        result = await previewer.run(definition)
        version.preview_status = result.status
        version.previewed_at = datetime.now(UTC)
        version.preview_summary = result.model_dump(mode="json")
        version.status = (
            StrategyVersionStatus.READY
            if result.status == "succeeded"
            else StrategyVersionStatus.APPROVED
        )
        await self._audit(
            user_id,
            "strategy.previewed",
            "strategy_version",
            version.id,
            {"status": result.status, "symbols_checked": result.symbols_checked},
        )
        return result

    async def activate(
        self, version: StrategyVersion, *, user_id: UUID, strategy_name: str
    ) -> Strategy:
        strategy = await self.session.get(Strategy, version.strategy_id)
        if strategy is None:
            raise StrategyGateError("strategy_missing", "Strategy no longer exists")
        self._assert_owner(strategy, user_id)
        self._assert_approval_intact(version)
        if version.preview_status != "succeeded":
            raise StrategyGateError(
                "preview_required",
                "A successful recent-market preview is required before activation",
            )
        disclaimer = await self.session.scalar(
            select(DisclaimerAcceptance.id).where(
                DisclaimerAcceptance.user_id == user_id,
                DisclaimerAcceptance.disclaimer_version == self.disclaimer_version,
            )
        )
        if disclaimer is None:
            raise StrategyGateError(
                "disclaimer_required", "Accept the current risk disclaimer before activation"
            )
        definition = StrategyDefinition.model_validate(version.schema_json)
        try:
            await EntitlementService(self.session).enforce_strategy_activation(
                user_id,
                definition,
                strategy_id=strategy.id,
            )
        except EntitlementError as exc:
            raise StrategyGateError(
                exc.code,
                str(exc),
            ) from exc
        universe_id = await self.session.scalar(
            select(StrategyUniverse.id).where(StrategyUniverse.strategy_version_id == version.id)
        )
        if universe_id is None:
            raise StrategyGateError("universe_required", "Strategy universe is missing")

        now = datetime.now(UTC)
        active_version = (
            await self.session.get(StrategyVersion, strategy.active_version_id)
            if strategy.active_version_id
            else None
        )
        if active_version and active_version.id != version.id:
            active_version.status = StrategyVersionStatus.SUPERSEDED
        strategy.name = strategy_name
        strategy.status = StrategyStatus.ACTIVE
        strategy.active_version_id = version.id
        strategy.activated_at = now
        version.status = StrategyVersionStatus.ACTIVE
        version.activated_at = now
        await self._audit(
            user_id,
            "strategy.activated",
            "strategy",
            strategy.id,
            {"version_id": str(version.id)},
        )
        await TrialLifecycleService(
            self.session,
            get_settings(),
        ).start_monitoring_cycle(user_id, activated_at=now)
        return strategy

    async def _create_version(
        self,
        strategy: Strategy,
        definition: StrategyDefinition,
        *,
        source_text: str | None,
        assumptions: list[str],
        ambiguities: list[dict],
        unsupported: list[dict],
        interpreter: str,
        version_number: int = 1,
    ) -> StrategyVersion:
        schema_json = definition.model_dump(mode="json")
        version = StrategyVersion(
            strategy_id=strategy.id,
            version_number=version_number,
            status=(
                StrategyVersionStatus.NEEDS_CLARIFICATION
                if ambiguities or unsupported
                else StrategyVersionStatus.DRAFT
            ),
            source_type="natural_language" if source_text else "structured",
            source_text=source_text,
            schema_json=schema_json,
            schema_hash=definition.canonical_hash(),
            interpretation_provider=interpreter,
            assumptions=assumptions,
            ambiguities=ambiguities,
            unsupported_conditions=unsupported,
        )
        self.session.add(version)
        await self.session.flush()
        universe = definition.universe
        self.session.add(
            StrategyUniverse(
                strategy_version_id=version.id,
                exchange=universe.exchange,
                market_type=universe.market_type,
                quote_currencies=universe.quote_currencies,
                include_symbols=universe.include_symbols,
                exclude_symbols=universe.exclude_symbols,
                timeframes=[definition.base_timeframe, *definition.supporting_timeframes],
                trigger_mode=definition.trigger_mode,
                min_quote_volume_24h=universe.min_quote_volume_24h,
                min_listing_age_days=universe.min_listing_age_days,
                max_spread_bps=universe.max_spread_bps,
                min_order_book_depth=universe.min_order_book_depth,
                max_symbols=universe.max_symbols,
            )
        )
        await self._persist_node(version.id, definition.conditions, parent_id=None, sequence=0)
        await self.session.flush()
        return version

    async def _persist_node(
        self,
        version_id: UUID,
        node: ConditionRule | ConditionGroup,
        *,
        parent_id: UUID | None,
        sequence: int,
    ) -> None:
        if isinstance(node, ConditionGroup):
            row = StrategyCondition(
                strategy_version_id=version_id,
                parent_condition_id=parent_id,
                condition_key=node.key,
                label=node.key.replace("_", " ").title(),
                node_type="group",
                condition_type=None,
                logical_operator=node.operator,
                sequence=sequence,
                config={},
            )
            self.session.add(row)
            await self.session.flush()
            for child_sequence, child in enumerate(node.children):
                await self._persist_node(
                    version_id, child, parent_id=row.id, sequence=child_sequence
                )
            return
        required_value = None
        if node.right and node.right.kind == OperandKind.CONSTANT:
            value = node.right.value
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                required_value = Decimal(str(value))
        self.session.add(
            StrategyCondition(
                strategy_version_id=version_id,
                parent_condition_id=parent_id,
                condition_key=node.key,
                label=node.label,
                node_type="condition",
                condition_type=node.condition_type,
                timeframe=node.timeframe,
                comparator=node.comparator.value,
                left_operand=node.left.model_dump(mode="json"),
                right_operand=node.right.model_dump(mode="json") if node.right else {},
                required_value=required_value,
                weight=Decimal(str(node.weight)),
                sequence=sequence,
                is_required=node.required,
                config={
                    "forming_tolerance_percent": node.forming_tolerance_percent,
                    "notes": node.notes,
                },
            )
        )

    @staticmethod
    def _assert_owner(strategy: Strategy, user_id: UUID) -> None:
        if strategy.user_id != user_id:
            raise StrategyGateError("not_found", "Strategy not found")

    @staticmethod
    def _assert_approval_intact(version: StrategyVersion) -> None:
        if not version.approved_at or version.approved_schema_hash != version.schema_hash:
            raise StrategyGateError(
                "approval_required", "Approve this exact strategy version before continuing"
            )

    async def _audit(
        self, user_id: UUID, action: str, target_type: str, target_id: UUID, metadata: dict
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
