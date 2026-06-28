from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from ai_market_monitor.cockpit_service import StrategyCockpitService
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    ConditionRuntimeState,
    NearMissSnapshot,
    ScanJob,
    ScanResult,
    SetupConditionResult,
    SetupInstance,
    Strategy,
    StrategyCondition,
    StrategyExperiment,
    StrategyUniverse,
    StrategyVersion,
    TelegramConnection,
    User,
)
from ai_market_monitor.db.models.enums import (
    AlertType,
    ConnectionStatus,
    DeliveryChannel,
    DeliveryStatus,
    ScanJobStatus,
    StrategyStatus,
    StrategyVersionStatus,
)
from ai_market_monitor.services.notifications import TelegramDeliveryService
from ai_market_monitor.services.scanner import ScanOrchestrator, ScanScheduler
from ai_market_monitor.telegram.adapter import TelegramDeliveryResult
from tests.factories import candle_sets, load_strategy


class PartialMarketProvider:
    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        return ["FAIL/USDT", "SOL/USDT"]

    async def fetch_ohlcv(self, exchange: str, symbol: str, timeframe: str, limit: int):
        if symbol == "FAIL/USDT":
            raise TimeoutError("fixture exchange timeout")
        return candle_sets(volume_multiplier=1.6)[timeframe][-limit:]


class SingleMarketProvider:
    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        return ["SOL/USDT"]

    async def fetch_ohlcv(self, exchange: str, symbol: str, timeframe: str, limit: int):
        return candle_sets(volume_multiplier=1.6)[timeframe][-limit:]


class CloseButBelowAlertProvider:
    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        return ["SOL/USDT"]

    async def fetch_ohlcv(self, exchange: str, symbol: str, timeframe: str, limit: int):
        sets = candle_sets(volume_multiplier=1.42)
        if timeframe == "15m":
            latest = sets[timeframe][-1]
            sets[timeframe][-1] = latest.__class__(
                timestamp=latest.timestamp,
                open=100,
                high=102,
                low=99.5,
                close=100.5,
                volume=latest.volume,
                is_closed=True,
            )
        return sets[timeframe][-limit:]


class SuccessfulTelegramAdapter:
    async def deliver(self, message):
        return TelegramDeliveryResult(message_ids=["telegram-message-1"])


class MissingMessageIdTelegramAdapter:
    async def deliver(self, message):
        return TelegramDeliveryResult(message_ids=[])


async def _active_strategy(session):
    definition = load_strategy()
    definition.universe.include_symbols = []
    user = User(display_name="Scanner User")
    session.add(user)
    await session.flush()
    strategy = Strategy(
        user_id=user.id,
        name=definition.name,
        status=StrategyStatus.ACTIVE,
        activated_at=datetime.now(UTC),
    )
    session.add(strategy)
    await session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id,
        version_number=1,
        status=StrategyVersionStatus.ACTIVE,
        source_type="structured",
        schema_json=definition.model_dump(mode="json"),
        schema_hash=definition.canonical_hash(),
        approved_by_user_id=user.id,
        approved_schema_hash=definition.canonical_hash(),
        approved_at=datetime.now(UTC),
        preview_status="succeeded",
        activated_at=datetime.now(UTC),
    )
    session.add(version)
    await session.flush()
    strategy.active_version_id = version.id
    session.add(
        StrategyUniverse(
            strategy_version_id=version.id,
            exchange=definition.universe.exchange,
            market_type=definition.universe.market_type,
            quote_currencies=definition.universe.quote_currencies,
            include_symbols=[],
            exclude_symbols=[],
            timeframes=[definition.base_timeframe, *definition.supporting_timeframes],
            trigger_mode=definition.trigger_mode,
            scan_interval_seconds=60,
        )
    )
    for sequence, condition in enumerate(definition.conditions.children):
        session.add(
            StrategyCondition(
                strategy_version_id=version.id,
                condition_key=condition.key,
                label=condition.label,
                node_type="condition",
                condition_type=condition.condition_type,
                timeframe=condition.timeframe,
                comparator=condition.comparator.value,
                left_operand=condition.left.model_dump(mode="json"),
                right_operand=condition.right.model_dump(mode="json") if condition.right else {},
                weight=Decimal(str(condition.weight)),
                sequence=sequence,
                is_required=condition.required,
            )
        )
    await session.flush()
    return strategy, version


async def test_scan_schedule_is_idempotent_and_partial_job_persists_success(test_context):
    async with test_context["session_factory"]() as session:
        strategy, version = await _active_strategy(session)
        session.add(
            TelegramConnection(
                user_id=strategy.user_id,
                telegram_user_id="scanner-telegram",
                chat_id="scanner-chat",
                status=ConnectionStatus.ACTIVE,
                alerts_enabled=True,
            )
        )
        await session.flush()
        scheduled_for = candle_sets()["15m"][-1].timestamp
        first = await ScanScheduler(session).schedule_due(scheduled_for=scheduled_for)
        second = await ScanScheduler(session).schedule_due(scheduled_for=scheduled_for)
        next_bucket = await ScanScheduler(session).schedule_due(
            scheduled_for=scheduled_for + timedelta(minutes=1)
        )
        assert len(first) == 1
        assert second == []
        assert next_bucket == []
        assert await session.scalar(select(func.count(ScanJob.id))) == 1

        summary = await ScanOrchestrator(session, PartialMarketProvider()).run_job(
            first[0].id,
            worker_id="test-worker",
        )
        await session.commit()
        assert summary.status == ScanJobStatus.PARTIAL
        assert summary.symbols_planned == 2
        assert summary.symbols_scanned == 1
        assert summary.failures == 1
        assert await session.scalar(select(func.count(ScanResult.id))) == 1
        persisted_result = await session.scalar(select(ScanResult))
        assert await session.scalar(select(func.count(SetupInstance.id))) == 1, (
            persisted_result.outcome,
            persisted_result.exclusion_reason,
            persisted_result.proof_summary,
        )
        assert await session.scalar(select(func.count(SetupConditionResult.id))) == 3
        assert await session.scalar(select(func.count(ConditionRuntimeState.id))) == 3
        assert await session.scalar(select(func.count(NearMissSnapshot.id))) == 0
        assert await session.scalar(select(func.count(Alert.id))) == 1
        assert await session.scalar(select(func.count(AlertDelivery.id))) == 1

        deliveries = await TelegramDeliveryService(
            session,
            test_context["settings"],
            SuccessfulTelegramAdapter(),
        ).process_due()
        assert len(deliveries) == 1
        assert deliveries[0].status == DeliveryStatus.SENT
        assert deliveries[0].provider_message_id == "telegram-message-1"

        setup = await session.scalar(select(SetupInstance))
        assert setup is not None
        assert setup.strategy_version_id == version.id
        assert setup.direction == "long"
        assert len(setup.target_levels) == 2
        job = await session.get(ScanJob, first[0].id)
        assert job.worker_id == "test-worker"
        assert job.claimed_at is not None
        assert job.heartbeat_at is not None

        rerun = await ScanOrchestrator(session, PartialMarketProvider()).run_job(first[0].id)
        assert rerun.status == ScanJobStatus.PARTIAL
        assert await session.scalar(select(func.count(ScanResult.id))) == 1


async def test_telegram_delivery_is_not_sent_without_provider_message_id(test_context):
    async with test_context["session_factory"]() as session:
        strategy, _version = await _active_strategy(session)
        session.add(
            TelegramConnection(
                user_id=strategy.user_id,
                telegram_user_id="missing-message-id",
                chat_id="missing-message-id-chat",
                status=ConnectionStatus.ACTIVE,
                alerts_enabled=True,
            )
        )
        alert = Alert(
            user_id=strategy.user_id,
            alert_type=AlertType.CONFIRMED,
            deduplication_key=f"missing-message-id-{strategy.id}",
            title="SOL/USDT confirmed",
            body="Deterministic proof attached.",
            proof_receipt={
                "strategy_name": strategy.name,
                "symbol": "SOL/USDT",
                "completion_score": 100,
                "conditions": [{"name": "Volume", "state": "passed"}],
            },
            candle_timestamp=datetime.now(UTC),
        )
        session.add(alert)
        await session.flush()
        delivery = AlertDelivery(
            alert_id=alert.id,
            channel=DeliveryChannel.TELEGRAM,
            destination_key="chat:missing-message-id-chat",
            status=DeliveryStatus.PENDING,
        )
        session.add(delivery)
        old_false_success = Alert(
            user_id=strategy.user_id,
            alert_type=AlertType.CONFIRMED,
            deduplication_key=f"old-missing-message-id-{strategy.id}",
            title="ETH/USDT confirmed",
            body="Deterministic proof attached.",
            proof_receipt={
                "strategy_name": strategy.name,
                "symbol": "ETH/USDT",
                "completion_score": 100,
                "conditions": [{"name": "Volume", "state": "passed"}],
            },
            candle_timestamp=datetime.now(UTC),
        )
        session.add(old_false_success)
        await session.flush()
        session.add(
            AlertDelivery(
                alert_id=old_false_success.id,
                channel=DeliveryChannel.TELEGRAM,
                destination_key="chat:missing-message-id-chat",
                status=DeliveryStatus.SENT,
                delivered_at=datetime.now(UTC),
            )
        )
        await session.flush()

        deliveries = await TelegramDeliveryService(
            session,
            test_context["settings"],
            MissingMessageIdTelegramAdapter(),
        ).process_due()

        assert len(deliveries) == 2
        assert {delivery.status for delivery in deliveries} == {DeliveryStatus.FAILED_RETRYABLE}
        assert all(delivery.provider_message_id is None for delivery in deliveries)
        assert {
            delivery.last_error_code for delivery in deliveries
        } == {"telegram_message_id_missing"}
        connection = await session.scalar(
            select(TelegramConnection).where(TelegramConnection.chat_id == "missing-message-id-chat")
        )
        assert connection.last_error_code == "telegram_message_id_missing"


async def test_close_skipped_result_persists_lifecycle_and_near_miss_without_alert(test_context):
    async with test_context["session_factory"]() as session:
        strategy, version = await _active_strategy(session)
        scheduled_for = candle_sets()["15m"][-1].timestamp
        jobs = await ScanScheduler(session).schedule_due(scheduled_for=scheduled_for)

        summary = await ScanOrchestrator(session, CloseButBelowAlertProvider()).run_job(
            jobs[0].id,
            worker_id="near-miss-storage-test",
        )
        await session.commit()

        assert summary.status == ScanJobStatus.SUCCEEDED
        scan = await session.scalar(select(ScanResult))
        assert scan is not None
        assert scan.outcome.value == "skipped"
        assert 40 <= float(scan.completion_score) < 70
        setup = await session.scalar(select(SetupInstance))
        assert setup is not None
        assert setup.strategy_version_id == version.id
        assert float(setup.completion_score) == float(scan.completion_score)
        snapshot = await session.scalar(select(NearMissSnapshot))
        assert snapshot is not None
        assert snapshot.setup_instance_id == setup.id
        assert await session.scalar(select(func.count(Alert.id))) == 0


async def test_dry_run_experiment_schedules_variants_and_persists_evidence_only(
    test_context,
):
    async with test_context["session_factory"]() as session:
        strategy, active_version = await _active_strategy(session)
        definition = load_strategy()
        variant = StrategyVersion(
            strategy_id=strategy.id,
            version_number=2,
            status=StrategyVersionStatus.DRAFT,
            source_type="structured",
            schema_json=definition.model_dump(mode="json"),
            schema_hash=definition.canonical_hash(),
        )
        session.add(variant)
        await session.flush()
        session.add(
            StrategyUniverse(
                strategy_version_id=variant.id,
                exchange=definition.universe.exchange,
                market_type=definition.universe.market_type,
                quote_currencies=definition.universe.quote_currencies,
                include_symbols=["SOL/USDT"],
                exclude_symbols=[],
                timeframes=[definition.base_timeframe, *definition.supporting_timeframes],
                trigger_mode=definition.trigger_mode,
                scan_interval_seconds=60,
            )
        )
        experiment = StrategyExperiment(
            user_id=strategy.user_id,
            strategy_id=strategy.id,
            name="Dry evidence comparison",
            status="running",
            mode="dry_run",
            version_ids=[str(active_version.id), str(variant.id)],
            comparison={},
            started_at=datetime.now(UTC),
        )
        session.add(experiment)
        await session.flush()

        scheduled_for = candle_sets()["15m"][-1].timestamp
        jobs = await ScanScheduler(session).schedule_due_experiments(
            scheduled_for=scheduled_for,
            experiment_ids=[experiment.id],
        )
        assert len(jobs) == 2
        assert {job.job_type for job in jobs} == {"experiment_dry_run"}

        variant_job = next(job for job in jobs if job.strategy_version_id == variant.id)
        summary = await ScanOrchestrator(session, SingleMarketProvider()).run_job(
            variant_job.id,
            worker_id="experiment-test",
        )
        await StrategyCockpitService(session).refresh_experiment(experiment)
        await session.commit()

        assert summary.status == ScanJobStatus.SUCCEEDED
        scan = await session.scalar(
            select(ScanResult).where(ScanResult.scan_job_id == variant_job.id)
        )
        assert scan.proof_summary["scan_context"]["experiment_id"] == str(experiment.id)
        assert scan.proof_summary["scan_context"]["evidence_only"] is True
        assert (
            await session.scalar(
                select(func.count(SetupInstance.id)).where(
                    SetupInstance.strategy_version_id == variant.id
                )
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count(Alert.id)).where(
                    Alert.strategy_version_id == variant.id
                )
            )
            == 0
        )
        assert experiment.comparison["scheduled_jobs"] == 2
        assert experiment.comparison["right"]["evaluations"] >= 1
