import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.db.models import (
    AuditEvent,
    CapabilityExtension,
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
from ai_market_monitor.engine.dynamic_mechanics import (
    expression_hash,
    validate_expression,
    validate_expression_parameters,
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
from ai_market_monitor.services.sharia_screening import (
    ShariaScreeningError,
    ShariaScreeningService,
)
from ai_market_monitor.services.sharia_universe import (
    ShariaUniverseError,
    ShariaUniverseResolver,
)
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
            created_by_user_id=user_id,
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
        parent = await self.session.scalar(
            select(StrategyVersion)
            .where(StrategyVersion.strategy_id == strategy.id)
            .order_by(StrategyVersion.version_number.desc())
            .limit(1)
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
            created_by_user_id=user_id,
            parent_version_id=parent.id if parent else None,
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

    async def create_system_revision(
        self,
        strategy: Strategy,
        definition: StrategyDefinition,
        *,
        user_id: UUID,
        source_text: str,
        reason: str,
    ) -> StrategyVersion:
        self._assert_owner(strategy, user_id)
        current_max = await self.session.scalar(
            select(func.max(StrategyVersion.version_number)).where(
                StrategyVersion.strategy_id == strategy.id
            )
        )
        parent = await self.session.scalar(
            select(StrategyVersion)
            .where(StrategyVersion.strategy_id == strategy.id)
            .order_by(StrategyVersion.version_number.desc())
            .limit(1)
        )
        version = await self._create_version(
            strategy,
            definition,
            created_by_user_id=user_id,
            parent_version_id=parent.id if parent else None,
            source_text=source_text,
            assumptions=[reason],
            ambiguities=[],
            unsupported=[],
            interpreter="certified-capability-repair",
            version_number=(current_max or 0) + 1,
        )
        await self._audit(
            user_id,
            "strategy.repair_revision_created",
            "strategy_version",
            version.id,
            {
                "schema_hash": version.schema_hash,
                "active_version_unchanged": str(strategy.active_version_id or ""),
            },
        )
        return version

    async def approve(
        self, version: StrategyVersion, *, user_id: UUID, expected_schema_hash: str
    ) -> StrategyVersion:
        await self.validate_approval(
            version,
            user_id=user_id,
            expected_schema_hash=expected_schema_hash,
        )
        if version.approved_at is not None:
            if version.approved_schema_hash != version.schema_hash:
                raise StrategyGateError(
                    "approved_version_changed",
                    "The approved strategy no longer matches its immutable schema hash.",
                )
            return version
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

    async def validate_approval(
        self, version: StrategyVersion, *, user_id: UUID, expected_schema_hash: str
    ) -> Strategy:
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
        definition = StrategyDefinition.model_validate(version.schema_json)
        await self._assert_dynamic_capability_artifacts(definition, user_id=user_id)
        return strategy

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
        await self._assert_dynamic_capability_artifacts(definition, user_id=user_id)
        preview_definition = definition
        settings = get_settings()
        if settings.sharia_screening_enforced:
            provider = getattr(previewer, "provider", None)
            if provider is None:
                raise StrategyGateError(
                    "screened_preview_unavailable",
                    "The preview service cannot verify the selected screened market.",
                )
            try:
                resolution = await ShariaUniverseResolver(
                    self.session,
                    provider,
                    settings,
                ).resolve(
                    definition,
                    user_id=user_id,
                    strategy_version_id=version.id,
                )
            except ShariaUniverseError as exc:
                raise StrategyGateError(exc.code, str(exc)) from exc
            if not resolution.included_symbols:
                raise StrategyGateError(
                    "screened_universe_empty",
                    "No assets currently meet this Watch Plan's screening policy.",
                )
            preview_definition = definition.model_copy(
                update={
                    "universe": definition.universe.model_copy(
                        update={"include_symbols": resolution.included_symbols}
                    )
                }
            )
        result = await previewer.run(preview_definition)
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
        await self._assert_dynamic_capability_artifacts(definition, user_id=user_id)
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
        universe = await self.session.scalar(
            select(StrategyUniverse).where(StrategyUniverse.strategy_version_id == version.id)
        )
        if universe is None:
            raise StrategyGateError("universe_required", "Strategy universe is missing")
        settings = get_settings()
        if settings.sharia_screening_enforced:
            policy = definition.universe.sharia_policy
            if policy is None or policy.methodology_id is None:
                raise StrategyGateError(
                    "sharia_policy_required",
                    "Choose an approved methodology and screened market before activation.",
                )
            try:
                await ShariaScreeningService(self.session, settings).methodology(
                    policy.methodology_id,
                    require_active=True,
                )
            except ShariaScreeningError as exc:
                raise StrategyGateError(exc.code, str(exc)) from exc
            if (
                not universe.sharia_policy_ready
                or not universe.universe_snapshot_hash
                or universe.methodology_id != policy.methodology_id
            ):
                raise StrategyGateError(
                    "screened_preview_required",
                    "Run a successful preview against the current screened market before "
                    "activation.",
                )

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
        await self._promote_pending_dynamic_artifacts(
            definition,
            user_id=user_id,
            strategy_version_id=version.id,
            promoted_at=now,
        )
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

    async def _promote_pending_dynamic_artifacts(
        self,
        definition: StrategyDefinition,
        *,
        user_id: UUID,
        strategy_version_id: UUID,
        promoted_at: datetime,
    ) -> None:
        rules = [
            rule
            for rule in _walk_rules(definition.conditions)
            if rule.left.name == "certified_dynamic" and rule.capability_artifact_hash
        ]
        if not rules:
            return
        extensions = list(
            (
                await self.session.scalars(
                    select(CapabilityExtension).where(CapabilityExtension.user_id == user_id)
                )
            ).all()
        )
        for rule in rules:
            extension = next(
                (
                    item
                    for item in extensions
                    if (item.validation_report or {})
                    .get("pending_revision", {})
                    .get("artifact_hash")
                    == rule.capability_artifact_hash
                ),
                None,
            )
            if extension is None:
                continue
            report = dict(extension.validation_report or {})
            pending = dict(report.get("pending_revision") or {})
            certification = dict(pending.get("certification") or {})
            if not certification.get("passed"):
                continue
            history = list(report.get("artifact_history") or [])
            history.append(
                {
                    "artifact_hash": extension.artifact_hash,
                    "capability_key": extension.capability_key,
                    "capability_version": extension.capability_version,
                    "manifest": extension.manifest,
                    "expression": extension.expression,
                    "certified": bool(extension.certified_at),
                    "superseded_at": promoted_at.isoformat(),
                }
            )
            draft = dict(pending.get("draft") or {})
            expression = dict(draft.get("expression") or {})
            manifest_without_expression = dict(draft)
            manifest_without_expression.pop("expression", None)
            extension.capability_version = str(pending["capability_version"])
            extension.artifact_hash = str(pending["artifact_hash"])
            extension.manifest = draft
            extension.expression = expression
            extension.generated_code = json.dumps(
                {
                    "capability_key": extension.capability_key,
                    "capability_version": extension.capability_version,
                    "artifact_hash": extension.artifact_hash,
                    "manifest": manifest_without_expression,
                    "expression": expression,
                },
                indent=2,
                sort_keys=True,
            )
            extension.validation_report = {
                **certification,
                "artifact_history": history[-20:],
                "promoted_strategy_version_id": str(strategy_version_id),
            }
            extension.ai_review = dict(pending.get("verification") or {})
            extension.status = "certified_user"
            extension.stage = "monitoring"
            extension.strategy_version_id = strategy_version_id
            extension.pending_strategy_version_id = None
            extension.certified_at = promoted_at
            extension.approved_at = promoted_at
            extension.build_log = [
                *(extension.build_log or []),
                {
                    "timestamp": promoted_at.isoformat(),
                    "stage": "repair_promoted",
                    "message": (
                        "User-approved strategy revision activated; prior artifact retained."
                    ),
                },
            ][-500:]

    async def _assert_dynamic_capability_artifacts(
        self,
        definition: StrategyDefinition,
        *,
        user_id: UUID,
    ) -> None:
        rules = [
            rule
            for rule in _walk_rules(definition.conditions)
            if rule.left.kind == OperandKind.PRICE_ACTION
            and rule.left.name == "certified_dynamic"
        ]
        if not rules:
            return
        artifact_hashes = {rule.capability_artifact_hash for rule in rules}
        if None in artifact_hashes:
            raise StrategyGateError(
                "dynamic_artifact_missing",
                "A generated mechanic is missing its immutable certification hash",
            )
        extensions = list(
            (
                await self.session.scalars(
                    select(CapabilityExtension).where(
                        or_(
                            CapabilityExtension.user_id == user_id,
                            CapabilityExtension.status == "approved_global",
                        )
                    )
                )
            ).all()
        )
        for rule in rules:
            match = next(
                (
                    (extension, snapshot)
                    for extension in extensions
                    if (
                        snapshot := _artifact_snapshot(
                            extension,
                            str(rule.capability_artifact_hash),
                        )
                    )
                    is not None
                ),
                None,
            )
            if match is None:
                raise StrategyGateError(
                    "dynamic_artifact_unregistered",
                    f"Generated mechanic {rule.label} has not passed TraceEdge certification",
                )
            extension, snapshot = match
            if extension.status != "approved_global" and extension.user_id != user_id:
                raise StrategyGateError(
                    "dynamic_artifact_owner_mismatch",
                    "This generated mechanic belongs to a different account",
                )
            if not snapshot["certified"]:
                raise StrategyGateError(
                    "dynamic_artifact_not_certified",
                    f"Generated mechanic {rule.label} is not certified for monitoring",
                )
            if (
                rule.capability_key != snapshot["capability_key"]
                or rule.capability_version != snapshot["capability_version"]
            ):
                raise StrategyGateError(
                    "dynamic_artifact_version_mismatch",
                    "Generated mechanic identity or version changed after certification",
                )
            try:
                expression = json.loads(str(rule.left.parameters["expression_json"]))
                parameters = json.loads(str(rule.left.parameters.get("parameters_json") or "{}"))
                validate_expression(expression)
                validate_expression_parameters(expression, parameters)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise StrategyGateError(
                    "dynamic_artifact_invalid",
                    f"Generated mechanic {rule.label} has an invalid deterministic payload",
                ) from exc
            manifest = dict(snapshot["manifest"] or {})
            manifest_expression = manifest.pop("expression", snapshot["expression"])
            expected_hash = expression_hash(manifest_expression, manifest)
            if (
                expression != snapshot["expression"]
                or parameters != dict(snapshot["manifest"].get("resolved_parameters") or {})
                or parameters != rule.resolved_parameters
                or expected_hash != snapshot["artifact_hash"]
                or rule.left.parameters.get("artifact_hash") != snapshot["artifact_hash"]
            ):
                raise StrategyGateError(
                    "dynamic_artifact_tampered",
                    f"Generated mechanic {rule.label} no longer matches its certified artifact",
                )

    async def _create_version(
        self,
        strategy: Strategy,
        definition: StrategyDefinition,
        *,
        created_by_user_id: UUID | None = None,
        parent_version_id: UUID | None = None,
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
            parent_version_id=parent_version_id,
            created_by_user_id=created_by_user_id,
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
        sharia_policy = universe.sharia_policy
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
                universe_mode=sharia_policy.universe_mode if sharia_policy else None,
                methodology_id=sharia_policy.methodology_id if sharia_policy else None,
                allowed_sharia_statuses=(
                    [status.value for status in sharia_policy.allowed_statuses]
                    if sharia_policy
                    else []
                ),
                qualification_policy=(
                    sharia_policy.qualification_policy if sharia_policy else None
                ),
                disputed_asset_policy=(
                    sharia_policy.disputed_asset_policy if sharia_policy else None
                ),
                compliance_change_behavior=(
                    sharia_policy.compliance_change_behavior if sharia_policy else None
                ),
                approved_watchlist_id=(
                    sharia_policy.approved_watchlist_id if sharia_policy else None
                ),
                universe_snapshot_version=(
                    sharia_policy.universe_snapshot_version if sharia_policy else None
                ),
                universe_last_resolved_at=(
                    sharia_policy.universe_last_resolved_at if sharia_policy else None
                ),
                sharia_policy_ready=False,
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
                    "capability_key": node.capability_key,
                    "capability_version": node.capability_version,
                    "capability_artifact_hash": node.capability_artifact_hash,
                    "resolved_parameters": node.resolved_parameters,
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


def _walk_rules(node: ConditionRule | ConditionGroup) -> list[ConditionRule]:
    if isinstance(node, ConditionRule):
        return [node]
    rules: list[ConditionRule] = []
    for child in node.children:
        rules.extend(_walk_rules(child))
    return rules


def _artifact_snapshot(
    extension: CapabilityExtension,
    artifact_hash: str,
) -> dict | None:
    if extension.artifact_hash == artifact_hash:
        return {
            "artifact_hash": artifact_hash,
            "capability_key": extension.capability_key,
            "capability_version": extension.capability_version,
            "manifest": dict(extension.manifest or {}),
            "expression": dict(extension.expression or {}),
            "certified": bool(
                extension.certified_at is not None
                and (extension.validation_report or {}).get("passed")
            ),
        }
    report = dict(extension.validation_report or {})
    pending = dict(report.get("pending_revision") or {})
    if pending.get("artifact_hash") == artifact_hash:
        draft = dict(pending.get("draft") or {})
        return {
            "artifact_hash": artifact_hash,
            "capability_key": extension.capability_key,
            "capability_version": pending.get("capability_version"),
            "manifest": draft,
            "expression": dict(draft.get("expression") or {}),
            "certified": bool((pending.get("certification") or {}).get("passed")),
        }
    for history in report.get("artifact_history") or []:
        if history.get("artifact_hash") != artifact_hash:
            continue
        return {
            "artifact_hash": artifact_hash,
            "capability_key": history.get("capability_key"),
            "capability_version": history.get("capability_version"),
            "manifest": dict(history.get("manifest") or {}),
            "expression": dict(history.get("expression") or {}),
            "certified": bool(history.get("certified")),
        }
    return None
