import logging

from celery import Celery

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.core.logging import configure_logging
from ai_market_monitor.core.startup import validate_runtime_configuration

settings = get_settings()
validate_runtime_configuration(settings)
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)
app = Celery("ai_market_monitor", broker=settings.redis_url, backend=settings.redis_url)
app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "evaluate-due-trial-cycles-every-hour": {
            "task": "ai_market_monitor.evaluate_due_trial_cycles",
            "schedule": 60 * 60,
        },
        "send-trial-cycle-reminders-every-hour": {
            "task": "ai_market_monitor.send_trial_cycle_reminders",
            "schedule": 60 * 60,
        },
        "reconcile-trial-alert-deliveries-every-five-minutes": {
            "task": "ai_market_monitor.reconcile_trial_alert_deliveries",
            "schedule": 5 * 60,
        },
        "repair-trial-cycle-counters-every-six-hours": {
            "task": "ai_market_monitor.repair_trial_cycle_counters",
            "schedule": 6 * 60 * 60,
        },
        "retry-discord-deliveries-every-five-minutes": {
            "task": "ai_market_monitor.retry_discord_deliveries",
            "schedule": 5 * 60,
        },
        "retry-telegram-deliveries-every-minute": {
            "task": "ai_market_monitor.retry_telegram_deliveries",
            "schedule": 60,
        },
        "process-pending-whatsapp-webhooks": {
            "task": "ai_market_monitor.process_pending_whatsapp_webhooks",
            "schedule": 10,
        },
        "retry-whatsapp-deliveries-every-minute": {
            "task": "ai_market_monitor.retry_whatsapp_deliveries",
            "schedule": 60,
        },
        "cleanup-whatsapp-webhook-receipts-nightly": {
            "task": "ai_market_monitor.cleanup_whatsapp_webhook_receipts",
            "schedule": 24 * 60 * 60,
        },
        "poll-telegram-updates": {
            "task": "ai_market_monitor.poll_telegram_updates",
            "schedule": settings.telegram_polling_interval_seconds,
        },
        "sync-discord-roles-every-five-minutes": {
            "task": "ai_market_monitor.process_discord_role_sync",
            "schedule": 5 * 60,
        },
        "record-database-health-every-minute": {
            "task": "ai_market_monitor.record_database_health",
            "schedule": 60,
        },
        "schedule-due-scans-every-minute": {
            "task": "ai_market_monitor.schedule_due_scans",
            "schedule": 60,
        },
        "recover-stale-scan-jobs-every-five-minutes": {
            "task": "ai_market_monitor.recover_stale_scan_jobs",
            "schedule": 5 * 60,
        },
        "expire-setup-instances-every-minute": {
            "task": "ai_market_monitor.expire_setup_instances",
            "schedule": 60,
        },
        "process-dashboard-replay-jobs-every-thirty-seconds": {
            "task": "ai_market_monitor.process_dashboard_replay_jobs",
            "schedule": 30,
        },
        "process-dashboard-export-jobs-every-minute": {
            "task": "ai_market_monitor.process_dashboard_export_jobs",
            "schedule": 60,
        },
        "evaluate-strategy-health-every-hour": {
            "task": "ai_market_monitor.evaluate_strategy_health",
            "schedule": 60 * 60,
        },
        "aggregate-setup-observability-every-five-minutes": {
            "task": "ai_market_monitor.aggregate_setup_observability",
            "schedule": 5 * 60,
        },
        "cleanup-setup-observability-nightly": {
            "task": "ai_market_monitor.cleanup_setup_observability",
            "schedule": 24 * 60 * 60,
        },
        "process-capability-extensions-every-thirty-seconds": {
            "task": "ai_market_monitor.process_capability_extensions",
            "schedule": 30,
        },
        "send-compliance-digests-every-hour": {
            "task": "ai_market_monitor.send_compliance_digests",
            "schedule": 60 * 60,
        },
        "process-sc-malaysia-imports-daily": {
            "task": "ai_market_monitor.process_sc_malaysia_imports",
            "schedule": 24 * 60 * 60,
        },
        "send-sharia-review-reminders-hourly": {
            "task": "ai_market_monitor.send_sharia_review_reminders",
            "schedule": 60 * 60,
        },
        "retry-sharia-admin-telegram-every-minute": {
            "task": "ai_market_monitor.retry_sharia_admin_telegram",
            "schedule": 60,
        },
        "retry-payment-emails-every-minute": {
            "task": "ai_market_monitor.retry_payment_emails",
            "schedule": 60,
        },
        "expire-ended-paid-access-every-five-minutes": {
            "task": "ai_market_monitor.expire_ended_paid_access",
            "schedule": 5 * 60,
        },
        "monitor-published-sharia-sources": {
            "task": "ai_market_monitor.monitor_published_sharia_sources",
            "schedule": settings.sharia_source_scan_interval_hours * 60 * 60,
        },
    },
)


def _run_async_task(coro) -> dict:
    import asyncio

    return asyncio.run(_run_with_worker_cleanup(coro))


async def _run_with_worker_cleanup(coro) -> dict:
    try:
        return await coro
    finally:
        # Celery prefork workers call these async tasks through short-lived
        # event loops. asyncpg connections are bound to the loop that created
        # them, so pooled connections must not survive into the next task loop.
        import sys

        database_module = sys.modules.get("ai_market_monitor.core.database")
        if database_module is not None:
            engine = getattr(database_module, "engine", None)
            if engine is not None:
                await engine.dispose()


@app.task(name="ai_market_monitor.evaluate_due_trial_cycles")
def evaluate_due_trial_cycles() -> dict:
    return _run_async_task(_evaluate_due_trial_cycles())


@app.task(name="ai_market_monitor.send_trial_cycle_reminders")
def send_trial_cycle_reminders() -> dict:
    return _run_async_task(_send_trial_cycle_reminders())


@app.task(name="ai_market_monitor.reconcile_trial_alert_deliveries")
def reconcile_trial_alert_deliveries() -> dict:
    return _run_async_task(_reconcile_trial_alert_deliveries())


@app.task(name="ai_market_monitor.repair_trial_cycle_counters")
def repair_trial_cycle_counters() -> dict:
    return _run_async_task(_repair_trial_cycle_counters())


@app.task(name="ai_market_monitor.expire_trials")
def expire_trials() -> dict:
    return _run_async_task(_evaluate_due_trial_cycles())


@app.task(name="ai_market_monitor.trial_reminders_due")
def trial_reminders_due() -> dict:
    return _run_async_task(_send_trial_cycle_reminders())


@app.task(name="ai_market_monitor.retry_discord_deliveries")
def retry_discord_deliveries() -> dict:
    return _run_async_task(_retry_discord_deliveries())


@app.task(name="ai_market_monitor.retry_telegram_deliveries")
def retry_telegram_deliveries() -> dict:
    return _run_async_task(_retry_telegram_deliveries())


@app.task(name="ai_market_monitor.poll_telegram_updates")
def poll_telegram_updates() -> dict:
    return _run_async_task(_poll_telegram_updates())


@app.task(name="ai_market_monitor.process_whatsapp_webhook_event")
def process_whatsapp_webhook_event(receipt_id: str) -> dict:
    return _run_async_task(_process_whatsapp_webhook_event(receipt_id))


@app.task(name="ai_market_monitor.process_pending_whatsapp_webhooks")
def process_pending_whatsapp_webhooks() -> dict:
    return _run_async_task(_process_pending_whatsapp_webhooks())


@app.task(name="ai_market_monitor.retry_whatsapp_deliveries")
def retry_whatsapp_deliveries() -> dict:
    return _run_async_task(_retry_whatsapp_deliveries())


@app.task(name="ai_market_monitor.cleanup_whatsapp_webhook_receipts")
def cleanup_whatsapp_webhook_receipts() -> dict:
    return _run_async_task(_cleanup_whatsapp_webhook_receipts())


@app.task(name="ai_market_monitor.process_discord_role_sync")
def process_discord_role_sync() -> dict:
    return _run_async_task(_process_discord_role_sync())


@app.task(name="ai_market_monitor.record_database_health")
def record_database_health() -> dict:
    return _run_async_task(_record_database_health())


@app.task(name="ai_market_monitor.schedule_due_scans")
def schedule_due_scans() -> dict:
    return _run_async_task(_schedule_due_scans())


@app.task(name="ai_market_monitor.recover_stale_scan_jobs")
def recover_stale_scan_jobs() -> dict:
    return _run_async_task(_recover_stale_scan_jobs())


@app.task(bind=True, name="ai_market_monitor.run_scan_job")
def run_scan_job(self, job_id: str) -> dict:
    worker_id = getattr(self.request, "hostname", None) or getattr(self.request, "id", "unknown")
    return _run_async_task(_run_scan_job(job_id, worker_id=str(worker_id)))


@app.task(name="ai_market_monitor.expire_setup_instances")
def expire_setup_instances() -> dict:
    return _run_async_task(_expire_setup_instances())


@app.task(name="ai_market_monitor.process_dashboard_replay_jobs")
def process_dashboard_replay_jobs() -> dict:
    return _run_async_task(_process_dashboard_replay_jobs())


@app.task(name="ai_market_monitor.process_dashboard_export_jobs")
def process_dashboard_export_jobs() -> dict:
    return _run_async_task(_process_dashboard_export_jobs())


@app.task(name="ai_market_monitor.evaluate_strategy_health")
def evaluate_strategy_health() -> dict:
    return _run_async_task(_evaluate_strategy_health())


@app.task(name="ai_market_monitor.aggregate_setup_observability")
def aggregate_setup_observability() -> dict:
    return _run_async_task(_aggregate_setup_observability())


@app.task(name="ai_market_monitor.cleanup_setup_observability")
def cleanup_setup_observability() -> dict:
    return _run_async_task(_cleanup_setup_observability())


@app.task(name="ai_market_monitor.process_capability_extensions")
def process_capability_extensions() -> dict:
    return _run_async_task(_process_capability_extensions())


@app.task(name="ai_market_monitor.send_compliance_digests")
def send_compliance_digests() -> dict:
    return _run_async_task(_send_compliance_digests())


@app.task(name="ai_market_monitor.process_sc_malaysia_imports")
def process_sc_malaysia_imports() -> dict:
    return _run_async_task(_process_sc_malaysia_imports())


@app.task(name="ai_market_monitor.send_sharia_review_reminders")
def send_sharia_review_reminders() -> dict:
    return _run_async_task(_send_sharia_review_reminders())


@app.task(name="ai_market_monitor.retry_payment_emails")
def retry_payment_emails() -> dict:
    return _run_async_task(_retry_payment_emails())


@app.task(name="ai_market_monitor.expire_ended_paid_access")
def expire_ended_paid_access() -> dict:
    return _run_async_task(_expire_ended_paid_access())


@app.task(name="ai_market_monitor.retry_sharia_admin_telegram")
def retry_sharia_admin_telegram() -> dict:
    return _run_async_task(_retry_sharia_admin_telegram())


@app.task(name="ai_market_monitor.monitor_published_sharia_sources")
def monitor_published_sharia_sources() -> dict:
    return _run_async_task(_monitor_published_sharia_sources())


async def _evaluate_due_trial_cycles() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.trials import TrialLifecycleService

    async with SessionFactory() as session:
        affected = await TrialLifecycleService(session, settings).evaluate_due_cycles()
        await session.commit()
        return {"cycles_evaluated": len(affected)}


async def _send_trial_cycle_reminders() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.trials import TrialLifecycleService

    async with SessionFactory() as session:
        alerts = await TrialLifecycleService(session, settings).create_due_reminder_messages()
        await session.commit()
        return {"reminder_alerts_enqueued": len(alerts)}


async def _reconcile_trial_alert_deliveries() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.trials import TrialLifecycleService

    async with SessionFactory() as session:
        result = await TrialLifecycleService(session, settings).reconcile_alert_deliveries()
        await session.commit()
        return result


async def _repair_trial_cycle_counters() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.trials import TrialLifecycleService

    async with SessionFactory() as session:
        result = await TrialLifecycleService(session, settings).repair_cycle_counters()
        await session.commit()
        return result


async def _retry_discord_deliveries() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.discord.service import DiscordAlertService

    async with SessionFactory() as session:
        if not settings.discord_enabled:
            return {"retried": 0, "disabled": True}
        retried = await DiscordAlertService(session, settings=settings).retry_due_deliveries()
        await session.commit()
        return {"retried": len(retried)}


async def _retry_telegram_deliveries() -> dict:
    if not settings.telegram_enabled:
        return {"processed": 0, "disabled": True}
    if settings.telegram_adapter != "http" or settings.telegram_bot_token is None:
        return {"processed": 0, "disabled": True, "reason": "telegram_http_not_configured"}
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.notifications import TelegramDeliveryService
    from ai_market_monitor.telegram.adapter import TelegramHttpAdapter

    async with SessionFactory() as session:
        processed = await TelegramDeliveryService(
            session,
            settings,
            TelegramHttpAdapter(settings),
        ).process_due()
        await session.commit()
        return {"processed": len(processed)}


async def _poll_telegram_updates() -> dict:
    if not settings.telegram_enabled or not settings.telegram_polling_enabled:
        return {"processed": 0, "disabled": True}
    if settings.telegram_adapter != "http" or settings.telegram_bot_token is None:
        return {"processed": 0, "disabled": True, "reason": "telegram_http_not_configured"}

    from sqlalchemy import Integer, cast, func, select

    from ai_market_monitor.api.routers.telegram import process_telegram_update
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.db.models import TelegramUpdateReceipt
    from ai_market_monitor.services.market_preview import (
        CcxtMarketDataProvider,
        MarketPreviewService,
    )
    from ai_market_monitor.telegram.adapter import TelegramDeliveryError, TelegramHttpAdapter

    adapter = TelegramHttpAdapter(settings)
    try:
        if settings.telegram_polling_clear_webhook:
            await adapter.delete_webhook(drop_pending_updates=False)
        else:
            webhook = await adapter.get_webhook_info()
            if webhook.get("url"):
                return {
                    "processed": 0,
                    "webhook_active": True,
                    "hint": "Set TELEGRAM_POLLING_CLEAR_WEBHOOK=true for local polling.",
                }
    except TelegramDeliveryError as exc:
        return {"processed": 0, "failed": 1, "error_code": exc.code}

    provider = CcxtMarketDataProvider(settings)
    previewer = MarketPreviewService(
        provider,
        candle_limit=settings.preview_candle_limit,
        settings=settings,
    )
    processed = 0
    failed = 0
    try:
        async with SessionFactory() as session:
            latest = await session.scalar(
                select(func.max(cast(TelegramUpdateReceipt.update_id, Integer)))
            )
            updates = await adapter.get_updates(
                offset=(int(latest) + 1 if latest is not None else None),
                limit=settings.telegram_polling_limit,
                timeout=0,
            )
            for update in updates:
                try:
                    result = await process_telegram_update(
                        update,
                        session=session,
                        settings=settings,
                        previewer=previewer,
                        adapter=adapter,
                    )
                    if result.get("ok"):
                        processed += 1
                except Exception:
                    failed += 1
                    await session.rollback()
        return {"processed": processed, "failed": failed}
    finally:
        await provider.close()


async def _process_whatsapp_webhook_event(receipt_id: str) -> dict:
    if not settings.whatsapp_enabled:
        return {"receipt_id": receipt_id, "status": "disabled"}
    if settings.whatsapp_adapter != "http" or settings.whatsapp_access_token is None:
        return {
            "receipt_id": receipt_id,
            "status": "disabled",
            "reason": "whatsapp_http_not_configured",
        }
    from uuid import UUID

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.whatsapp.adapter import WhatsAppCloudAdapter
    from ai_market_monitor.whatsapp.service import WhatsAppWebhookProcessor

    async with SessionFactory() as session:
        result = await WhatsAppWebhookProcessor(
            session, settings, WhatsAppCloudAdapter(settings)
        ).process(UUID(receipt_id))
        await session.commit()
        return {"receipt_id": receipt_id, "status": result}


async def _process_pending_whatsapp_webhooks() -> dict:
    if not settings.whatsapp_enabled:
        return {"processed": 0, "disabled": True}
    from datetime import UTC, datetime

    from sqlalchemy import or_, select

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.db.models import WhatsAppWebhookReceipt

    now = datetime.now(UTC)
    async with SessionFactory() as session:
        receipt_ids = list(
            (
                await session.scalars(
                    select(WhatsAppWebhookReceipt.id)
                    .where(
                        WhatsAppWebhookReceipt.processing_status.in_(
                            {"pending", "ready", "failed_retryable"}
                        ),
                        or_(
                            WhatsAppWebhookReceipt.next_retry_at.is_(None),
                            WhatsAppWebhookReceipt.next_retry_at <= now,
                        ),
                    )
                    .order_by(WhatsAppWebhookReceipt.received_at.asc())
                    .limit(50)
                )
            ).all()
        )
    results: dict[str, int] = {}
    for receipt_id in receipt_ids:
        outcome = await _process_whatsapp_webhook_event(str(receipt_id))
        state = str(outcome.get("status") or "unknown")
        results[state] = results.get(state, 0) + 1
    return {"processed": len(receipt_ids), "outcomes": results}


async def _retry_whatsapp_deliveries() -> dict:
    if not settings.whatsapp_enabled:
        return {"processed": 0, "disabled": True}
    if settings.whatsapp_adapter != "http" or settings.whatsapp_access_token is None:
        return {
            "processed": 0,
            "disabled": True,
            "reason": "whatsapp_http_not_configured",
        }
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.whatsapp.adapter import WhatsAppCloudAdapter
    from ai_market_monitor.whatsapp.service import WhatsAppDeliveryService

    async with SessionFactory() as session:
        processed = await WhatsAppDeliveryService(
            session, settings, WhatsAppCloudAdapter(settings)
        ).process_due()
        await session.commit()
        return {"processed": len(processed)}


async def _cleanup_whatsapp_webhook_receipts() -> dict:
    from datetime import UTC, datetime

    from sqlalchemy import delete

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.db.models import WhatsAppWebhookReceipt

    async with SessionFactory() as session:
        result = await session.execute(
            delete(WhatsAppWebhookReceipt).where(
                WhatsAppWebhookReceipt.retain_until <= datetime.now(UTC)
            )
        )
        await session.commit()
        return {"deleted": int(result.rowcount or 0)}


async def _process_discord_role_sync() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.discord.service import DiscordRoleSyncService

    async with SessionFactory() as session:
        if not settings.discord_enabled:
            return {"processed": 0, "disabled": True}
        processed = await DiscordRoleSyncService(session, settings=settings).process_due()
        await session.commit()
        return {"processed": len(processed)}


async def _record_database_health() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.db.models.enums import HealthStatus
    from ai_market_monitor.services.reliability import ReliabilityService

    async with SessionFactory() as session:
        service = ReliabilityService(session)
        status = await service.check_database()
        await service.record_metric(
            component="database",
            metric_name="connectivity",
            status=status,
            value=1 if status == HealthStatus.HEALTHY else 0,
            unit="boolean",
        )
        await session.commit()
        return {"database": status.value}


async def _schedule_due_scans() -> dict:
    if not settings.scanning_enabled:
        return {"scheduled": 0, "disabled": True}
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.scanner import ScanScheduler

    async with SessionFactory() as session:
        scheduler = ScanScheduler(session, settings)
        live_jobs = await scheduler.schedule_due()
        experiment_jobs = await scheduler.schedule_due_experiments()
        jobs = [*live_jobs, *experiment_jobs]
        await session.commit()
    for job in jobs:
        run_scan_job.delay(str(job.id))
    return {
        "scheduled": len(jobs),
        "live_jobs": len(live_jobs),
        "experiment_jobs": len(experiment_jobs),
    }


async def _recover_stale_scan_jobs() -> dict:
    if not settings.scanning_enabled:
        return {"recovered": 0, "disabled": True}
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.scanner import ScanScheduler

    async with SessionFactory() as session:
        jobs = await ScanScheduler(session, settings).recover_stale_or_retryable()
        await session.commit()
    for job in jobs:
        run_scan_job.delay(str(job.id))
    return {"recovered": len(jobs)}


async def _run_scan_job(job_id: str, *, worker_id: str) -> dict:
    if not settings.scanning_enabled:
        return {"job_id": job_id, "status": "disabled", "disabled": True}
    from uuid import UUID

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.db.models import ScanJob, StrategyExperiment
    from ai_market_monitor.services.market_preview import CcxtMarketDataProvider
    from ai_market_monitor.services.scanner import ScanOrchestrator

    provider = CcxtMarketDataProvider(settings)
    try:
        async with SessionFactory() as session:
            summary = await ScanOrchestrator(session, provider, settings=settings).run_job(
                UUID(job_id),
                worker_id=worker_id,
            )
            job = await session.get(ScanJob, UUID(job_id))
            experiment_id = (job.metrics or {}).get("experiment_id") if job else None
            if experiment_id:
                from ai_market_monitor.cockpit_service import StrategyCockpitService

                experiment = await session.get(StrategyExperiment, UUID(str(experiment_id)))
                if experiment is not None:
                    await StrategyCockpitService(session).refresh_experiment(experiment)
            if job is not None and job.strategy_version_id is not None:
                from ai_market_monitor.services.capability_extensions import (
                    CapabilityExtensionService,
                )

                await CapabilityExtensionService(settings).record_live_scan(
                    session,
                    strategy_version_id=job.strategy_version_id,
                    scan_job_id=job.id,
                    symbols_scanned=summary.symbols_scanned,
                    candidates_found=summary.matches_found,
                    notifications_created=summary.notifications_created,
                )
            await session.commit()
            return {
                "job_id": str(summary.job_id),
                "status": summary.status.value,
                "symbols_planned": summary.symbols_planned,
                "symbols_scanned": summary.symbols_scanned,
                "matches_found": summary.matches_found,
                "notifications_created": summary.notifications_created,
                "failures": summary.failures,
            }
    finally:
        await provider.close()


async def _process_capability_extensions() -> dict:
    if not settings.capability_extension_enabled:
        return {"processed": 0, "disabled": True}
    from sqlalchemy import select

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.db.models import CapabilityExtension
    from ai_market_monitor.services.capability_extensions import CapabilityExtensionService
    from ai_market_monitor.services.capability_registry import CapabilityRegistryService
    from ai_market_monitor.services.market_preview import CcxtMarketDataProvider

    provider = CcxtMarketDataProvider(settings)
    processed = 0
    failed = 0
    try:
        async with SessionFactory() as session:
            await CapabilityRegistryService(settings).initialize(session)
            await session.commit()
        for _ in range(5):
            async with SessionFactory() as session:
                extension = await session.scalar(
                    select(CapabilityExtension)
                    .where(CapabilityExtension.status.in_({"queued", "repair_queued"}))
                    .order_by(CapabilityExtension.created_at.asc())
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                if extension is None:
                    break
                extension_id = extension.id
                try:
                    await CapabilityExtensionService(settings).process(
                        session,
                        extension,
                        provider,
                    )
                    await session.commit()
                    processed += 1
                except Exception:
                    await session.rollback()
                    logger.exception(
                        "Unexpected capability extension failure for %s",
                        extension_id,
                    )
                    failed += 1
        return {"processed": processed, "failed": failed}
    finally:
        await provider.close()


async def _send_compliance_digests() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.compliance_watch import ComplianceDigestService

    async with SessionFactory() as session:
        result = await ComplianceDigestService(session, settings).process_due()
        await session.commit()
        return result


async def _process_sc_malaysia_imports() -> dict:
    from uuid import UUID

    from sqlalchemy import select

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.db.models import ExternalAssessment, ReviewCase
    from ai_market_monitor.services.market_preview import CcxtMarketDataProvider
    from ai_market_monitor.services.sc_malaysia_import import SCMalaysiaImporter
    from ai_market_monitor.services.sharia_governance import ShariaAdminTelegramService
    from ai_market_monitor.services.sharia_identity import (
        AssetIdentityError,
        CanonicalAssetMappingService,
    )
    from ai_market_monitor.services.sharia_research import ShariaResearchPipeline

    provider = CcxtMarketDataProvider(settings)
    try:
        verified_symbols = {
            symbol.upper()
            for symbol in await provider.list_symbols("binance", ["USDT"])
        }
        async with SessionFactory() as session:
            imported = await SCMalaysiaImporter(session, settings).import_latest()
            await session.commit()
        allowed = settings.sharia_pilot_symbol_set
        if settings.sharia_process_remaining_imports:
            allowed = set()
        async with SessionFactory() as session:
            query = select(ExternalAssessment).where(
                ExternalAssessment.mapping_state == "unresolved"
            )
            if allowed:
                query = query.where(ExternalAssessment.asset_symbol.in_(allowed))
            drafts = list((await session.scalars(query)).all())
            mapped = 0
            cases = 0
            failed = 0
            for external in drafts:
                try:
                    mapping = CanonicalAssetMappingService(session)
                    if settings.sharia_process_remaining_imports:
                        await mapping.map_registered(
                            external,
                            verified_exchange_symbols=verified_symbols,
                        )
                    else:
                        await mapping.map_pilot(
                            external,
                            verified_exchange_symbols=verified_symbols,
                        )
                    await session.flush()
                    mapped += 1
                    result = await ShariaResearchPipeline(
                        session, settings
                    ).research_initial_asset(external.id)
                    if result.case_id:
                        case = await session.get(ReviewCase, UUID(result.case_id))
                        if case is not None:
                            await ShariaAdminTelegramService(
                                session, settings
                            ).enqueue(
                                case,
                                notification_type="new_review_required",
                                idempotency_key=f"new-review:{case.id}",
                            )
                            cases += 1
                except AssetIdentityError:
                    failed += 1
                except Exception:
                    logger.exception(
                        "SC Malaysia pilot processing failed for %s", external.asset_symbol
                    )
                    failed += 1
            await session.commit()
        async with SessionFactory() as session:
            delivered = await ShariaAdminTelegramService(
                session, settings
            ).process_due()
            await session.commit()
        return {
            "import_run_id": imported.run_id,
            "explicit_rows": imported.explicit_rows,
            "notice_only_rows_excluded": imported.excluded_notice_rows,
            "pilot_assets_mapped": mapped,
            "review_cases_ready": cases,
            "failed_or_waiting_identity": failed,
            "telegram_attempts_processed": delivered,
            "remaining_imports_enabled": settings.sharia_process_remaining_imports,
        }
    finally:
        await provider.close()


async def _send_sharia_review_reminders() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.sharia_governance import ShariaAdminTelegramService

    async with SessionFactory() as session:
        service = ShariaAdminTelegramService(session, settings)
        enqueued = await service.enqueue_due_reminders()
        processed = await service.process_due()
        await session.commit()
        return {"enqueued": enqueued, "processed": processed}


async def _retry_sharia_admin_telegram() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.sharia_governance import ShariaAdminTelegramService

    async with SessionFactory() as session:
        processed = await ShariaAdminTelegramService(session, settings).process_due()
        await session.commit()
        return {"processed": processed}


async def _retry_payment_emails() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.payment_emails import PaymentEmailOutboxService

    async with SessionFactory() as session:
        return await PaymentEmailOutboxService(session, settings).process_due()


async def _expire_ended_paid_access() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.billing import BillingService

    async with SessionFactory() as session:
        expired = await BillingService(session, settings).expire_ended_access()
        await session.commit()
        return {"expired": expired}


async def _monitor_published_sharia_sources() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.sharia_source_monitoring import (
        ShariaSourceMonitoringService,
    )

    async with SessionFactory() as session:
        result = await ShariaSourceMonitoringService(session, settings).run_due()
        await session.commit()
        return result


async def _expire_setup_instances() -> dict:
    from datetime import UTC, datetime

    from sqlalchemy import select

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.db.models import CandidateReadinessSnapshot, SetupInstance
    from ai_market_monitor.db.models.enums import (
        TERMINAL_SETUP_STATES,
        SetupLifecycleState,
    )
    from ai_market_monitor.services.lifecycle import transition_setup

    async with SessionFactory() as session:
        now = datetime.now(UTC)
        setups = (
            await session.scalars(
                select(SetupInstance).where(
                    SetupInstance.expires_at.is_not(None),
                    SetupInstance.expires_at <= now,
                    SetupInstance.state.not_in(TERMINAL_SETUP_STATES),
                )
            )
        ).all()
        expired = 0
        for setup in setups:
            try:
                session.add(
                    transition_setup(
                        setup,
                        SetupLifecycleState.EXPIRED,
                        reason_code="expiry_time_reached",
                        occurred_at=now,
                    )
                )
                readiness = await session.scalar(
                    select(CandidateReadinessSnapshot).where(
                        CandidateReadinessSnapshot.setup_instance_id == setup.id
                    )
                )
                if readiness is not None:
                    readiness.lifecycle_state = "expired"
                    readiness.stage_rank = -2
                    readiness.most_recent_change = "Lifecycle expired at its configured limit."
                    readiness.last_changed_at = now
                expired += 1
            except ValueError:
                continue
        await session.commit()
        return {"expired": expired}


async def _process_dashboard_replay_jobs() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.dashboard_jobs import DashboardJobService
    from ai_market_monitor.services.market_preview import CcxtMarketDataProvider

    provider = CcxtMarketDataProvider(settings)
    try:
        async with SessionFactory() as session:
            jobs = await DashboardJobService(session, provider, settings).process_replay_jobs()
            await session.commit()
            return {"processed": len(jobs)}
    finally:
        await provider.close()


async def _process_dashboard_export_jobs() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.dashboard_jobs import DashboardJobService
    from ai_market_monitor.services.market_preview import CcxtMarketDataProvider

    provider = CcxtMarketDataProvider(settings)
    try:
        async with SessionFactory() as session:
            jobs = await DashboardJobService(session, provider, settings).process_export_jobs()
            await session.commit()
            return {"processed": len(jobs)}
    finally:
        await provider.close()


async def _evaluate_strategy_health() -> dict:
    from sqlalchemy import select

    from ai_market_monitor.cockpit_service import StrategyCockpitService
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.db.models import Strategy
    from ai_market_monitor.db.models.enums import StrategyStatus
    from ai_market_monitor.services.market_preview import CcxtMarketDataProvider

    provider = CcxtMarketDataProvider(settings)
    try:
        async with SessionFactory() as session:
            strategies = (
                await session.scalars(
                    select(Strategy).where(
                        Strategy.status.in_(
                            [
                                StrategyStatus.ACTIVE,
                                StrategyStatus.FORWARD_TEST,
                                StrategyStatus.PAUSED,
                            ]
                        ),
                        Strategy.archived_at.is_(None),
                    )
                )
            ).all()
            service = StrategyCockpitService(session)
            for strategy in strategies:
                health = await service.edge_health(strategy, provider=provider)
                await service.detect_decay(strategy)
                await service.sync_inbox(strategy.user_id)
                await service.create_weekly_health_summary(strategy, health)
            await session.commit()
            return {"evaluated": len(strategies)}
    finally:
        await provider.close()


async def _aggregate_setup_observability() -> dict:
    from sqlalchemy import select

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.db.models import Strategy, StrategyVersion
    from ai_market_monitor.db.models.enums import StrategyStatus
    from ai_market_monitor.services.setup_observability import SetupObservabilityService

    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(Strategy, StrategyVersion)
                .join(StrategyVersion, StrategyVersion.id == Strategy.active_version_id)
                .where(Strategy.status == StrategyStatus.ACTIVE)
            )
        ).all()
        service = SetupObservabilityService(session, settings)
        for strategy, version in rows:
            await service.aggregate_version(strategy, version)
        await session.commit()
        return {"aggregated_versions": len(rows)}


async def _cleanup_setup_observability() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.setup_observability import SetupObservabilityService

    async with SessionFactory() as session:
        result = await SetupObservabilityService(session, settings).cleanup()
        await session.commit()
        return result
