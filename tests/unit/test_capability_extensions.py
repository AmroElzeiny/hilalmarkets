from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AISetupChatMessage,
    AISetupChatSession,
    CapabilityExtension,
    CapabilityExtensionAttempt,
    CapabilityExtensionScan,
    Strategy,
    StrategyVersion,
    TelegramConnection,
    User,
)
from ai_market_monitor.db.models.enums import (
    ConnectionStatus,
    LogicalOperator,
    StrategyStatus,
    StrategyVersionStatus,
    TriggerMode,
)
from ai_market_monitor.engine.dynamic_mechanics import compile_dynamic_rule
from ai_market_monitor.schemas.capability_extensions import (
    MechanicDraft,
    MechanicRepair,
    MechanicReview,
)
from ai_market_monitor.schemas.strategy import (
    AlertPolicy,
    ConditionGroup,
    ConditionRule,
    StrategyDefinition,
    UniverseDefinition,
)
from ai_market_monitor.services.capability_extension_scope import CapabilityExtensionScopeError
from ai_market_monitor.services.capability_extensions import CapabilityExtensionService
from ai_market_monitor.services.interfaces import Candle
from ai_market_monitor.services.strategy import StrategyGateError, StrategyService
from ai_market_monitor.telegram.adapter import TelegramDeliveryResult, TelegramHttpAdapter


class BalancedProvider:
    async def list_symbols(self, exchange, quote_currencies):
        assert exchange == "bybit"
        return [f"COIN{index}/USDT" for index in range(10)]

    async def fetch_ohlcv(self, exchange, symbol, timeframe, limit):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        return [
            Candle(
                timestamp=start + timedelta(minutes=index),
                open=100,
                high=102 if index % 25 == 0 else 101,
                low=99.5 if index % 25 == 0 else 99,
                close=101.8 if index % 25 == 0 else 100.1,
                volume=1000,
            )
            for index in range(100)
        ]


class PassingExtensionAI:
    last_usage = {"input_tokens": 100, "output_tokens": 50}

    @staticmethod
    def mechanic() -> MechanicDraft:
        return MechanicDraft(
            label="Large candle body",
            deterministic_definition=(
                "The current candle body occupies more than 70 percent of its full range."
            ),
            timeframe="15m",
            parameters=[],
            resolved_parameters={},
            expression={
                "op": "gt",
                "left": {"op": "candle_metric", "name": "body_percent", "offset": 0},
                "right": {"op": "constant", "value": 70},
            },
            proof_template="Candle body percentage was {actual}; required above 70%.",
            assumptions=[],
            expected_frequency="occasional",
            logic_fidelity_statement="This directly implements the requested large-body condition.",
        )

    async def draft(self, **kwargs):
        return self.mechanic()

    async def review(self, **kwargs):
        return MechanicReview(
            verdict="pass",
            failure_source="none",
            preserves_user_logic=True,
            confidence=0.96,
            candidate_quality="balanced",
            issues=[],
            recommended_changes=[],
            explanation="The AST and market evidence match the user request.",
        )

    async def repair(self, **kwargs):
        return MechanicRepair(
            revised_draft=self.mechanic(),
            changed_implementation_only=True,
            user_logic_changed=False,
            applied_changes=[],
            deferred_changes=[],
        )


class EscalatingExtensionAI(PassingExtensionAI):
    @staticmethod
    def impossible() -> MechanicDraft:
        payload = PassingExtensionAI.mechanic().model_dump(mode="json")
        payload["expression"] = {
            "op": "gt",
            "left": {"op": "field", "field": "close"},
            "right": {"op": "constant", "value": 1_000_000_000},
        }
        return MechanicDraft.model_validate(payload)

    async def draft(self, **kwargs):
        return self.impossible()

    async def review(self, **kwargs):
        if kwargs["market_report"]["classification"] == "too_strict":
            return MechanicReview(
                verdict="repair",
                failure_source="implementation",
                preserves_user_logic=True,
                confidence=0.94,
                candidate_quality="too_strict",
                issues=["The generated comparison used the wrong constant."],
                recommended_changes=["Restore the requested candle-body comparison."],
                explanation="The implementation, not the user's threshold, caused no matches.",
            )
        return await super().review(**kwargs)


class DeliveryReviewAI(PassingExtensionAI):
    async def review(self, **kwargs):
        if "scan_count" not in kwargs["market_report"]:
            return await super().review(**kwargs)
        return MechanicReview(
            verdict="pass",
            failure_source="delivery",
            preserves_user_logic=True,
            confidence=0.95,
            candidate_quality="balanced",
            issues=["Candidates existed but no channel delivery was queued."],
            recommended_changes=["Check schedule, cooldown, and channel connection."],
            explanation="The deterministic mechanic is not the cause of missing delivery.",
        )


class LiveImplementationRepairAI(PassingExtensionAI):
    async def review(self, **kwargs):
        if "scan_count" not in kwargs["market_report"]:
            return await super().review(**kwargs)
        return MechanicReview(
            verdict="repair",
            failure_source="implementation",
            preserves_user_logic=True,
            confidence=0.96,
            candidate_quality="too_strict",
            issues=["The runtime implementation differs from its certified definition."],
            recommended_changes=["Restore the certified body comparison."],
            explanation="The code path, rather than user logic, requires correction.",
        )

    async def repair(self, **kwargs):
        payload = self.mechanic().model_dump(mode="json")
        payload["expression"] = {
            "op": "and",
            "args": [
                payload["expression"],
                {
                    "op": "eq",
                    "left": {"op": "constant", "value": 1},
                    "right": {"op": "constant", "value": 1},
                },
            ],
        }
        return MechanicRepair(
            revised_draft=MechanicDraft.model_validate(payload),
            changed_implementation_only=True,
            user_logic_changed=False,
            applied_changes=["Restored the runtime wrapper without changing rule meaning."],
            deferred_changes=[],
        )


def _settings(**changes) -> Settings:
    return Settings(
        app_env="test",
        app_secret_key=SecretStr("extension-test-secret-at-least-thirty-two-characters"),
        openai_api_key=SecretStr("test-key"),
        capability_extension_preflight_max_symbols=10,
        capability_extension_preflight_exchange="bybit",
        capability_extension_candle_limit=100,
        capability_extension_empty_scan_threshold=2,
        capability_extension_no_notification_threshold=2,
        **changes,
    )


async def test_provider_only_mechanic_is_rejected_before_queueing(test_context):
    user_id, chat_id = await _user_and_chat(test_context)
    service = CapabilityExtensionService(_settings(), ai=PassingExtensionAI())
    async with test_context["session_factory"]() as session:
        with pytest.raises(CapabilityExtensionScopeError) as exc_info:
            await service.request(
                session,
                user_id=user_id,
                chat_session_id=chat_id,
                source_prompt="Alert when liquidation heatmaps and open interest rise",
                conversation_history=[],
            )

        queued = int(await session.scalar(select(func.count(CapabilityExtension.id))) or 0)

    assert exc_info.value.code == "custom_capability_provider_required"
    assert exc_info.value.dependency.category == "derivatives"
    assert queued == 0


async def _user_and_chat(test_context):
    async with test_context["session_factory"]() as session:
        user = User(display_name="Extension Test")
        session.add(user)
        await session.flush()
        chat = AISetupChatSession(
            user_id=user.id,
            original_idea="Find a candle whose body is over 70% of its range",
            context_json={},
        )
        session.add(chat)
        await session.commit()
        return user.id, chat.id


async def test_extension_is_market_tested_certified_and_installed_in_chat(test_context):
    user_id, chat_id = await _user_and_chat(test_context)
    service = CapabilityExtensionService(_settings(), ai=PassingExtensionAI())
    async with test_context["session_factory"]() as session:
        extension = await service.request(
            session,
            user_id=user_id,
            chat_session_id=chat_id,
            source_prompt="Find a candle whose body is over 70% of its range on 15m",
            conversation_history=[{"role": "user", "content": "Body above 70%."}],
        )
        await service.process(session, extension, BalancedProvider())
        await session.commit()

        assert extension.status == "certified_user"
        assert extension.validation_score >= 85
        assert extension.artifact_hash
        attempts = list(
            (
                await session.scalars(
                    select(CapabilityExtensionAttempt).where(
                        CapabilityExtensionAttempt.extension_id == extension.id
                    )
                )
            ).all()
        )
        scans = list(
            (
                await session.scalars(
                    select(CapabilityExtensionScan).where(
                        CapabilityExtensionScan.extension_id == extension.id
                    )
                )
            ).all()
        )
        chat = await session.get(AISetupChatSession, chat_id)
        definition = StrategyDefinition.model_validate(chat.draft_schema_json)
        rule = definition.conditions.children[0]
        assert {attempt.operation for attempt in attempts} == {
            "initial_draft",
            "preflight_review_1",
        }
        assert scans[0].classification == "balanced"
        assert chat.status == "ready_for_approval"
        assert rule.capability_key == extension.capability_key
        assert rule.capability_version == "0.1.0"
        assert rule.capability_artifact_hash == extension.artifact_hash

        chat.status = "interviewing"
        reused = await service.request(
            session,
            user_id=user_id,
            chat_session_id=chat_id,
            source_prompt="Find a candle whose body is over 70% of its range on 15m",
            conversation_history=[{"role": "user", "content": "Build that mechanic again."}],
        )
        await session.refresh(chat)
        reused_definition = StrategyDefinition.model_validate(chat.draft_schema_json)
        matching_rules = [
            item
            for item in reused_definition.conditions.children
            if isinstance(item, ConditionRule)
            and item.capability_key == extension.capability_key
        ]
        assert reused.id == extension.id
        assert chat.status == "ready_for_approval"
        assert len(matching_rules) == 1

        strategy = Strategy(user_id=user_id, name="Certified monitor", status=StrategyStatus.DRAFT)
        session.add(strategy)
        await session.flush()
        version = StrategyVersion(
            strategy_id=strategy.id,
            version_number=1,
            status=StrategyVersionStatus.DRAFT,
            source_type="test",
            schema_json=definition.model_dump(mode="json"),
            schema_hash=definition.canonical_hash(),
        )
        session.add(version)
        await session.flush()
        approved = await StrategyService(session, "test").approve(
            version,
            user_id=user_id,
            expected_schema_hash=version.schema_hash,
        )
        assert approved.status == StrategyVersionStatus.APPROVED


async def test_quarantine_blocks_certified_artifact_until_owner_restores_it(test_context):
    user_id, chat_id = await _user_and_chat(test_context)
    service = CapabilityExtensionService(_settings(), ai=PassingExtensionAI())
    async with test_context["session_factory"]() as session:
        extension = await service.request(
            session,
            user_id=user_id,
            chat_session_id=chat_id,
            source_prompt="Find a candle whose body is over 70% of its range on 15m",
            conversation_history=[],
        )
        await service.process(session, extension, BalancedProvider())
        chat = await session.get(AISetupChatSession, chat_id)
        definition = StrategyDefinition.model_validate(chat.draft_schema_json)
        strategy = Strategy(user_id=user_id, name="Quarantine test", status=StrategyStatus.DRAFT)
        session.add(strategy)
        await session.flush()
        version = StrategyVersion(
            strategy_id=strategy.id,
            version_number=1,
            status=StrategyVersionStatus.DRAFT,
            source_type="test",
            schema_json=definition.model_dump(mode="json"),
            schema_hash=definition.canonical_hash(),
        )
        session.add(version)
        await session.flush()

        await service.quarantine(
            session,
            extension=extension,
            user_id=user_id,
            reason="Owner observed an unexpected runtime result.",
        )
        with pytest.raises(StrategyGateError) as exc_info:
            await StrategyService(session, "test").approve(
                version,
                user_id=user_id,
                expected_schema_hash=version.schema_hash,
            )
        assert exc_info.value.code == "dynamic_artifact_quarantined"

        await service.restore_from_quarantine(
            session,
            extension=extension,
            user_id=user_id,
        )
        approved = await StrategyService(session, "test").approve(
            version,
            user_id=user_id,
            expected_schema_hash=version.schema_hash,
        )

    assert extension.paused_at is None
    assert approved.status == StrategyVersionStatus.APPROVED


async def test_discard_pending_repair_rejects_revision_and_keeps_active_version(test_context):
    user_id, _chat_id = await _user_and_chat(test_context)
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        strategy = Strategy(user_id=user_id, name="Repair rollback", status=StrategyStatus.ACTIVE)
        session.add(strategy)
        await session.flush()
        active = StrategyVersion(
            strategy_id=strategy.id,
            version_number=1,
            status=StrategyVersionStatus.ACTIVE,
            source_type="test",
            schema_json={},
            schema_hash="a" * 64,
            approved_schema_hash="a" * 64,
            approved_at=now,
            approved_by_user_id=user_id,
        )
        pending = StrategyVersion(
            strategy_id=strategy.id,
            version_number=2,
            status=StrategyVersionStatus.DRAFT,
            source_type="system_repair",
            schema_json={},
            schema_hash="b" * 64,
        )
        session.add_all([active, pending])
        await session.flush()
        strategy.active_version_id = active.id
        extension = CapabilityExtension(
            user_id=user_id,
            strategy_version_id=active.id,
            pending_strategy_version_id=pending.id,
            request_fingerprint="c" * 64,
            capability_key="custom_repair_rollback",
            capability_version="0.1.0",
            registry_hash="d" * 64,
            artifact_hash="e" * 64,
            source_prompt="A custom closed-candle condition",
            conversation_history=[],
            status="repair_ready",
            stage="awaiting_user_approval",
            certified_at=now,
            validation_report={
                "passed": True,
                "pre_repair_status": "certified_user",
                "pending_revision": {
                    "artifact_hash": "f" * 64,
                    "requires_user_approval": True,
                },
            },
        )
        session.add(extension)
        await session.flush()

        await CapabilityExtensionService(_settings()).discard_pending_repair(
            session,
            extension=extension,
            user_id=user_id,
        )

    assert strategy.active_version_id == active.id
    assert active.status == StrategyVersionStatus.ACTIVE
    assert pending.status == StrategyVersionStatus.REJECTED
    assert extension.pending_strategy_version_id is None
    assert extension.status == "certified_user"
    assert extension.validation_report["discarded_revisions"]


async def test_five_scan_threshold_queues_independent_repair_review(test_context):
    user_id, chat_id = await _user_and_chat(test_context)
    service = CapabilityExtensionService(_settings(), ai=PassingExtensionAI())
    async with test_context["session_factory"]() as session:
        extension = await service.request(
            session,
            user_id=user_id,
            chat_session_id=chat_id,
            source_prompt="Find a candle whose body is over 70% of its range on 15m",
            conversation_history=[],
        )
        await service.process(session, extension, BalancedProvider())
        strategy = Strategy(user_id=user_id, name="Extension monitor", status=StrategyStatus.DRAFT)
        session.add(strategy)
        await session.flush()
        version = StrategyVersion(
            strategy_id=strategy.id,
            version_number=1,
            status=StrategyVersionStatus.DRAFT,
            source_type="test",
            schema_json={},
            schema_hash="a" * 64,
        )
        session.add(version)
        await session.flush()
        extension.strategy_version_id = version.id
        for _ in range(2):
            await service.record_live_scan(
                session,
                strategy_version_id=version.id,
                scan_job_id=None,
                symbols_scanned=100,
                candidates_found=0,
                notifications_created=0,
            )
        assert extension.status == "repair_queued"
        assert extension.validation_report["repair_reason"] == "no_candidates"
        assert extension.empty_scan_streak == 2
        await service.process(session, extension, BalancedProvider())
        attempts = list(
            (
                await session.scalars(
                    select(CapabilityExtensionAttempt)
                    .where(CapabilityExtensionAttempt.extension_id == extension.id)
                    .order_by(CapabilityExtensionAttempt.attempt_number)
                )
            ).all()
        )
        assert attempts[-1].operation == "live_no_candidates_review"
        assert attempts[-1].model == "gpt-5.4-mini"
        assert attempts[-1].reasoning_effort == "low"
        assert attempts[-1].service_tier == "flex"
        assert extension.status == "certified_user"


async def test_initial_empty_market_test_uses_ranked_escalation_ladder(test_context):
    user_id, chat_id = await _user_and_chat(test_context)
    service = CapabilityExtensionService(_settings(), ai=EscalatingExtensionAI())
    async with test_context["session_factory"]() as session:
        extension = await service.request(
            session,
            user_id=user_id,
            chat_session_id=chat_id,
            source_prompt="Find a candle whose body is over 70% of its range on 15m",
            conversation_history=[{"role": "user", "content": "Keep 70 percent."}],
        )
        await service.process(session, extension, BalancedProvider())
        attempts = list(
            (
                await session.scalars(
                    select(CapabilityExtensionAttempt)
                    .where(CapabilityExtensionAttempt.extension_id == extension.id)
                    .order_by(CapabilityExtensionAttempt.attempt_number)
                )
            ).all()
        )

        assert extension.status == "certified_user"
        assert [attempt.operation for attempt in attempts] == [
            "initial_draft",
            "preflight_review_1",
            "implementation_repair",
            "preflight_review_2",
        ]
        assert (attempts[0].model, attempts[0].reasoning_effort, attempts[0].service_tier) == (
            "gpt-5.4-nano",
            "low",
            "default",
        )
        assert (attempts[1].model, attempts[1].reasoning_effort, attempts[1].service_tier) == (
            "gpt-5.4-nano",
            "high",
            "flex",
        )
        assert (attempts[2].model, attempts[2].reasoning_effort, attempts[2].service_tier) == (
            "gpt-5.4-nano",
            "low",
            "flex",
        )
        assert (attempts[3].model, attempts[3].reasoning_effort, attempts[3].service_tier) == (
            "gpt-5.4-mini",
            "medium",
            "flex",
        )


async def test_no_delivery_escalates_to_mini_high_without_rewriting_mechanic(test_context):
    user_id, chat_id = await _user_and_chat(test_context)
    service = CapabilityExtensionService(_settings(), ai=DeliveryReviewAI())
    async with test_context["session_factory"]() as session:
        extension = await service.request(
            session,
            user_id=user_id,
            chat_session_id=chat_id,
            source_prompt="Find a candle whose body is over 70% of its range on 15m",
            conversation_history=[],
        )
        await service.process(session, extension, BalancedProvider())
        strategy = Strategy(user_id=user_id, name="Delivery monitor", status=StrategyStatus.DRAFT)
        session.add(strategy)
        await session.flush()
        version = StrategyVersion(
            strategy_id=strategy.id,
            version_number=1,
            status=StrategyVersionStatus.DRAFT,
            source_type="test",
            schema_json={},
            schema_hash="b" * 64,
        )
        session.add(version)
        await session.flush()
        extension.strategy_version_id = version.id
        for _ in range(2):
            await service.record_live_scan(
                session,
                strategy_version_id=version.id,
                scan_job_id=None,
                symbols_scanned=100,
                candidates_found=3,
                notifications_created=0,
            )
        assert extension.validation_report["repair_reason"] == "no_notifications"
        original_hash = extension.artifact_hash
        await service.process(session, extension, BalancedProvider())
        attempt = await session.scalar(
            select(CapabilityExtensionAttempt).where(
                CapabilityExtensionAttempt.extension_id == extension.id,
                CapabilityExtensionAttempt.operation == "live_no_notifications_review",
            )
        )
        assert attempt.model == "gpt-5.4-mini"
        assert attempt.reasoning_effort == "high"
        assert attempt.service_tier == "flex"
        assert extension.failure_classification == "delivery"
        assert extension.artifact_hash == original_hash
        assert extension.status == "certified_user"


async def test_live_implementation_repair_is_recertified_before_user_approval(test_context):
    user_id, chat_id = await _user_and_chat(test_context)
    service = CapabilityExtensionService(_settings(), ai=LiveImplementationRepairAI())
    async with test_context["session_factory"]() as session:
        extension = await service.request(
            session,
            user_id=user_id,
            chat_session_id=chat_id,
            source_prompt="Find a candle whose body is over 70% of its range on 15m",
            conversation_history=[],
        )
        await service.process(session, extension, BalancedProvider())
        chat = await session.get(AISetupChatSession, chat_id)
        definition = StrategyDefinition.model_validate(chat.draft_schema_json)
        strategy = Strategy(user_id=user_id, name="Repair monitor", status=StrategyStatus.ACTIVE)
        session.add(strategy)
        await session.flush()
        version = StrategyVersion(
            strategy_id=strategy.id,
            version_number=1,
            status=StrategyVersionStatus.ACTIVE,
            source_type="test",
            schema_json=definition.model_dump(mode="json"),
            schema_hash=definition.canonical_hash(),
            approved_schema_hash=definition.canonical_hash(),
            approved_by_user_id=user_id,
            approved_at=datetime.now(UTC),
        )
        session.add(version)
        await session.flush()
        strategy.active_version_id = version.id
        extension.strategy_version_id = version.id
        for _ in range(2):
            await service.record_live_scan(
                session,
                strategy_version_id=version.id,
                scan_job_id=None,
                symbols_scanned=100,
                candidates_found=0,
                notifications_created=0,
            )
        original_hash = extension.artifact_hash
        await service.process(session, extension, BalancedProvider())
        operations = list(
            (
                await session.scalars(
                    select(CapabilityExtensionAttempt.operation)
                    .where(CapabilityExtensionAttempt.extension_id == extension.id)
                    .order_by(CapabilityExtensionAttempt.attempt_number)
                )
            ).all()
        )

        assert operations[-3:] == [
            "live_no_candidates_review",
            "implementation_repair",
            "live_no_candidates_verification",
        ]
        assert extension.status == "repair_ready"
        assert extension.artifact_hash == original_hash
        pending = extension.validation_report["pending_revision"]
        assert pending["requires_user_approval"] is True
        assert pending["certification"]["passed"] is True
        assert pending["artifact_hash"] != original_hash
        verification_scan = await session.scalar(
            select(CapabilityExtensionScan).where(
                CapabilityExtensionScan.extension_id == extension.id,
                CapabilityExtensionScan.phase == "live_repair_verification",
            )
        )
        assert verification_scan.classification == "balanced"
        revised_strategy, revised_version = await service.materialize_pending_revision(
            session,
            extension=extension,
            user_id=user_id,
        )
        assert revised_strategy.id == strategy.id
        assert strategy.active_version_id == version.id
        assert revised_version.id != version.id
        assert revised_version.status == StrategyVersionStatus.DRAFT
        revised_definition = StrategyDefinition.model_validate(revised_version.schema_json)
        revised_rule = revised_definition.conditions.children[0]
        assert revised_rule.capability_version == "0.1.1"
        assert revised_rule.capability_artifact_hash == pending["artifact_hash"]
        approved = await StrategyService(session, "test").approve(
            revised_version,
            user_id=user_id,
            expected_schema_hash=revised_version.schema_hash,
        )
        assert approved.status == StrategyVersionStatus.APPROVED
        await StrategyService(session, "test")._promote_pending_dynamic_artifacts(
            revised_definition,
            user_id=user_id,
            strategy_version_id=revised_version.id,
            promoted_at=datetime.now(UTC),
        )
        assert extension.capability_version == "0.1.1"
        assert extension.artifact_hash == pending["artifact_hash"]
        assert extension.validation_report["artifact_history"][0]["artifact_hash"] == original_hash


async def test_unregistered_dynamic_artifact_cannot_be_approved(test_context):
    user_id, _ = await _user_and_chat(test_context)
    draft = PassingExtensionAI.mechanic()
    rule = compile_dynamic_rule(
        capability_key="custom_unregistered",
        capability_version="0.1.0",
        artifact_hash="f" * 64,
        label=draft.label,
        timeframe=draft.timeframe,
        expression=draft.expression,
        resolved_parameters=draft.resolved_parameters,
        proof_template=draft.proof_template,
        source_fragment="unregistered mechanic",
    )
    definition = StrategyDefinition(
        name="Unregistered mechanic",
        base_timeframe="15m",
        trigger_mode=TriggerMode.CANDLE_CLOSE,
        universe=UniverseDefinition(exchange="bybit", quote_currencies=["USDT"]),
        conditions=ConditionGroup(
            key="all_required_conditions",
            operator=LogicalOperator.AND,
            children=[rule],
        ),
        alerts=AlertPolicy(channels=["telegram"]),
    )
    async with test_context["session_factory"]() as session:
        strategy = Strategy(user_id=user_id, name=definition.name, status=StrategyStatus.DRAFT)
        session.add(strategy)
        await session.flush()
        version = StrategyVersion(
            strategy_id=strategy.id,
            version_number=1,
            status=StrategyVersionStatus.DRAFT,
            source_type="test",
            schema_json=definition.model_dump(mode="json"),
            schema_hash=definition.canonical_hash(),
        )
        session.add(version)
        await session.flush()
        with pytest.raises(StrategyGateError) as exc_info:
            await StrategyService(session, "test").approve(
                version,
                user_id=user_id,
                expected_schema_hash=version.schema_hash,
            )
        assert exc_info.value.code == "dynamic_artifact_unregistered"


async def test_website_mechanic_build_status_stays_in_chat_not_telegram(
    test_context,
    monkeypatch,
):
    user_id, chat_id = await _user_and_chat(test_context)
    delivered = []

    async def capture(_adapter, message):
        delivered.append(message)
        return TelegramDeliveryResult(message_ids=["mechanic-status-1"])

    monkeypatch.setattr(TelegramHttpAdapter, "deliver", capture)
    settings = _settings(
        telegram_enabled=True,
        telegram_adapter="http",
        telegram_bot_token=SecretStr("123456:test-token"),
    )
    async with test_context["session_factory"]() as session:
        session.add(
            TelegramConnection(
                user_id=user_id,
                telegram_user_id="extension-telegram-user",
                chat_id="extension-telegram-chat",
                status=ConnectionStatus.ACTIVE,
                alerts_enabled=True,
            )
        )
        await session.flush()
        await CapabilityExtensionService(settings, ai=PassingExtensionAI()).request(
            session,
            user_id=user_id,
            chat_session_id=chat_id,
            source_prompt="Find a candle whose body is over 70% of its range on 15m",
            conversation_history=[],
        )

        status = await session.scalar(
            select(AISetupChatMessage).where(
                AISetupChatMessage.session_id == chat_id,
                AISetupChatMessage.message_type == "mechanic_build_status",
            )
        )

    assert delivered == []
    assert status is not None
    assert "creating a safe, testable version" in status.content


async def test_telegram_origin_mechanic_build_status_uses_telegram(test_context, monkeypatch):
    user_id, _chat_id = await _user_and_chat(test_context)
    delivered = []

    async def capture(_adapter, message):
        delivered.append(message)
        return TelegramDeliveryResult(message_ids=["mechanic-status-1"])

    monkeypatch.setattr(TelegramHttpAdapter, "deliver", capture)
    settings = _settings(
        telegram_enabled=True,
        telegram_adapter="http",
        telegram_bot_token=SecretStr("123456:test-token"),
    )
    async with test_context["session_factory"]() as session:
        session.add(
            TelegramConnection(
                user_id=user_id,
                telegram_user_id="extension-telegram-user",
                chat_id="extension-telegram-chat",
                status=ConnectionStatus.ACTIVE,
                alerts_enabled=True,
            )
        )
        await session.flush()
        await CapabilityExtensionService(settings, ai=PassingExtensionAI()).request(
            session,
            user_id=user_id,
            chat_session_id=None,
            source_prompt="Find a candle whose body is over 70% of its range on 15m",
            conversation_history=[],
        )

    assert len(delivered) == 1
    assert delivered[0].chat_id == "extension-telegram-chat"
    assert "couple of minutes" in delivered[0].text
