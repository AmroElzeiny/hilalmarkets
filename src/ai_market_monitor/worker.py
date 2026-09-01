import logging
import time
from typing import TYPE_CHECKING

from celery import Celery
from celery.signals import task_postrun

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.core.logging import configure_logging
from ai_market_monitor.core.startup import validate_runtime_configuration

if TYPE_CHECKING:
    from ai_market_monitor.services.interfaces import MarketDataProvider

#: When this process last wrote its measurements down. Throttles the per-task flush.
_LAST_METRIC_FLUSH: float = 0.0

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
    # The three settings that stop a worker eating the server. See the long note beside
    # `celery_worker_concurrency` in core/config.py for what happened on 22 August 2026:
    # a child grew to 1.4 GB, nothing replaced it, and the kernel killed systemd.
    #
    # They live here rather than on the command line so that every way of starting a
    # worker is bounded — the compose file, a hand-typed `celery` command, and a local
    # run all read the same numbers.
    worker_concurrency=settings.celery_worker_concurrency,
    worker_max_tasks_per_child=settings.celery_worker_max_tasks_per_child,
    worker_max_memory_per_child=settings.celery_worker_max_memory_per_child_kb,
    # structlog writes to stdout, and Celery captures stdout into its own logger. That
    # capture defaults to WARNING, so every *successful* provider call was arriving in the
    # log stamped as a warning while its own payload said `"level": "info"`. Two costs: a
    # real warning became impossible to pick out, and `--loglevel=WARNING` — the obvious
    # way to quieten a busy scanner — would have hidden nothing at all. INFO here lets the
    # severity the application chose survive the trip.
    worker_redirect_stdouts_level="INFO",
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
        # More often than the reservation lifetime, so a crashed worker's promise is
        # returned within one window rather than held until somebody restarts everything.
        "sweep-expired-ai-reservations-every-five-minutes": {
            "task": "ai_market_monitor.sweep_expired_ai_reservations",
            "schedule": 5 * 60,
        },
        "retry-telegram-deliveries-every-minute": {
            "task": "ai_market_monitor.retry_telegram_deliveries",
            "schedule": 60,
        },
        # The same minute as Telegram. Email is a delivery channel of equal standing, so
        # it is not allowed to run slower — a person who chose email would otherwise
        # hear about a setup later than one who chose Telegram, for no stated reason.
        "retry-email-deliveries-every-minute": {
            "task": "ai_market_monitor.retry_email_deliveries",
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
        # Runs whether or not scanning is enabled. History left behind by a monitor that
        # has since been paused still occupies the disk a working monitor needs, and a
        # deployment with scanning switched off is exactly where it would pile up unseen.
        "cleanup-scan-history-nightly": {
            "task": "ai_market_monitor.cleanup_scan_history",
            "schedule": 24 * 60 * 60,
        },
        "expire-setup-instances-every-minute": {
            "task": "ai_market_monitor.expire_setup_instances",
            "schedule": 60,
        },
        "recover-stalled-setup-chat-turns": {
            "task": "ai_market_monitor.recover_setup_chat_turns",
            "schedule": settings.setup_chat_recovery_interval_seconds,
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
        # Ticks daily; what is actually *due* is decided inside, against
        # `sharia_source_scan_interval_hours` (a week). The tick and the cadence are
        # deliberately different numbers.
        #
        # They used to be the same one, and that made the cadence mean two things at
        # once. Raising the re-check cadence to a week would then also have made this
        # task run weekly — and this task is not only the authority import: it is where a
        # newly listed coin gets its identity discovered and its first research run. A
        # coin added on Tuesday would have waited until the next weekly tick before the
        # product looked at it at all. The importers already refuse to re-fetch inside
        # the cadence (see `fasset_import.import_latest`), so a daily tick costs nothing
        # and keeps new coins moving.
        "process-sharia-authority-imports": {
            "task": "ai_market_monitor.process_sharia_authority_imports",
            "schedule": 24 * 60 * 60,
        },
        "resolve-official-sources-daily": {
            "task": "ai_market_monitor.resolve_official_sources",
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
        "retry-account-emails-every-minute": {
            "task": "ai_market_monitor.retry_account_emails",
            "schedule": 60,
        },
        "retry-public-inquiry-emails-every-minute": {
            "task": "ai_market_monitor.retry_public_inquiry_emails",
            "schedule": 60,
        },
        "retry-public-form-deliveries-every-minute": {
            "task": "ai_market_monitor.retry_public_form_deliveries",
            "schedule": 60,
        },
        "cleanup-public-chat-data-nightly": {
            "task": "ai_market_monitor.cleanup_public_chat_data",
            "schedule": 24 * 60 * 60,
        },
        "refresh-system-brain-repository-index-every-five-minutes": {
            "task": "ai_market_monitor.refresh_system_brain_repository_index",
            "schedule": 5 * 60,
        },
        "expire-ended-paid-access-every-five-minutes": {
            "task": "ai_market_monitor.expire_ended_paid_access",
            "schedule": 5 * 60,
        },
        # The re-review of every coin that already carries a published Shariah status:
        # its official pages are fetched again and a reviewer is asked to look only when
        # one of them changed in a way the research marks as possibly material.
        #
        # Ticks daily, and each coin is due once a week — `sharia_source_scan_interval_hours`.
        # A daily tick over coins that are not due is a handful of cheap queries, and it
        # is what stops a coin published just after a weekly tick waiting nearly two weeks
        # for its first look.
        "monitor-published-sharia-sources": {
            "task": "ai_market_monitor.monitor_published_sharia_sources",
            "schedule": 24 * 60 * 60,
        },
        # Gathers the website, whitepaper and repository for coins that are tradeable
        # but that no authority has ruled on. Writes no Shariah status of any kind.
        # It is on this beat rather than run by hand so that a deployment picks it up
        # by itself: the researcher starts on its own after the VPS restarts.
        "research-unscreened-tradeable-coins": {
            "task": "ai_market_monitor.research_unscreened_coins",
            "schedule": settings.unscreened_research_interval_hours * 60 * 60,
        },
        # Reads those coins' own pages and records what the automated screen makes of
        # them. Offset by an hour from the researcher above so it works on links that
        # have already been gathered rather than racing it for an empty queue.
        #
        # It publishes nothing. Every row it writes is a proposal marked as reviewed by
        # nobody, and only the application's own approval route can turn one into a
        # published status.
        "screen-researched-coins": {
            "task": "ai_market_monitor.screen_researched_coins",
            "schedule": settings.automated_screen_interval_hours * 60 * 60,
        },
        # Size, rank and how each screened coin moved over weeks — what an exchange
        # ticker cannot say. Two provider calls for the whole list, once a day.
        "refresh-market-numbers": {
            "task": "ai_market_monitor.refresh_market_numbers",
            "schedule": settings.market_numbers_interval_hours * 60 * 60,
        },
        # The scheduler writes its own measurements down on this beat. Every other
        # process writes its own after each task it runs, or on the API's own timer —
        # a scheduled task only ever runs in one process, so it can never flush the
        # rest of them.
        "flush-operational-metrics": {
            "task": "ai_market_monitor.flush_operational_metrics",
            "schedule": settings.observability_flush_interval_seconds,
        },
        # The only thing standing between the measurement table and unbounded growth.
        "compact-operational-metrics-hourly": {
            "task": "ai_market_monitor.compact_operational_metrics",
            "schedule": 60 * 60,
        },
        "deliver-operational-alerts-every-minute": {
            "task": "ai_market_monitor.deliver_operational_alerts",
            "schedule": 60,
        },
        "retry-operational-alert-deliveries-every-minute": {
            "task": "ai_market_monitor.retry_operational_alert_deliveries",
            "schedule": 60,
        },
    },
)


def _flush_metrics_now() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.observability.durable_metrics import flush_metrics_once

    async def _run() -> dict:
        async with SessionFactory() as session:
            written = await flush_metrics_once(
                session, policy=settings.metric_retention_policy
            )
            return {"series_written": written}

    return _run_async_task(_run())


@task_postrun.connect
def _write_measurements_down_after_each_task(**_kwargs: object) -> None:
    """Every worker process writes its own measurements down, on its own schedule.

    A beat entry runs in exactly one worker process. Relying on it alone would have
    stored the measurements of one process and thrown away every other worker's,
    which is the same blindness this whole layer exists to remove.

    Throttled, so a burst of short tasks does not turn into a write per task, and
    wrapped, because a failure to record how the work went must never fail the work.
    """

    global _LAST_METRIC_FLUSH
    now = time.monotonic()
    if now - _LAST_METRIC_FLUSH < settings.observability_flush_interval_seconds:
        return
    _LAST_METRIC_FLUSH = now
    try:
        _flush_metrics_now()
    except Exception:  # pragma: no cover - defensive, never fails the task
        logger.warning("Could not write operational measurements down", exc_info=True)


def _run_async_task(coro) -> dict:
    import asyncio

    return asyncio.run(_run_with_worker_cleanup(coro))


async def _run_with_worker_cleanup(coro) -> dict:
    try:
        return await coro
    finally:
        # Celery prefork workers call these async tasks through short-lived event loops.
        # Anything bound to the loop that created it must not survive into the next task.
        #
        # This block used to release the database engine alone. The outbound HTTP side has
        # exactly the same loop affinity — the shared client pool, the circuit breaker's
        # lock, and the module lock guarding both — and was left behind, so a worker
        # process served one task and then failed every later one with "Event loop is
        # closed". Telegram polling surfaced it first only because it runs every five
        # seconds; scheduled scans reach the market-data and OpenAI providers through the
        # same pool and were failing identically.
        #
        # Both are released here, by module rather than by import, so a task that never
        # touched one does not pay to load it.
        import sys

        provider_module = sys.modules.get("ai_market_monitor.services.provider_runtime")
        if provider_module is not None:
            await provider_module.release_provider_runtime_for_loop()

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


@app.task(name="ai_market_monitor.refresh_system_brain_repository_index")
def refresh_system_brain_repository_index() -> dict:
    return _run_async_task(_refresh_system_brain_repository_index())


async def _refresh_system_brain_repository_index() -> dict:
    from datetime import UTC, datetime

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.db.models import AuditEvent
    from ai_market_monitor.services.system_brain_repository_index import (
        RepositoryEvidenceIndexService,
    )

    async with SessionFactory() as session:
        result = await RepositoryEvidenceIndexService().refresh(session)
        session.add(
            AuditEvent(
                actor_user_id=None,
                actor_type="system_maintenance",
                action="system_brain.repository_index.refreshed",
                target_type="repository_evidence_index",
                target_id=None,
                request_id=None,
                ip_hash=None,
                metadata_redacted=result,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
        return result


@app.task(name="ai_market_monitor.reconcile_trial_alert_deliveries")
def reconcile_trial_alert_deliveries() -> dict:
    return _run_async_task(_reconcile_trial_alert_deliveries())


@app.task(name="ai_market_monitor.repair_trial_cycle_counters")
def repair_trial_cycle_counters() -> dict:
    return _run_async_task(_repair_trial_cycle_counters())


@app.task(name="ai_market_monitor.sweep_expired_ai_reservations")
def sweep_expired_ai_reservations() -> dict:
    return _run_async_task(_sweep_expired_ai_reservations())


@app.task(name="ai_market_monitor.expire_trials")
def expire_trials() -> dict:
    return _run_async_task(_evaluate_due_trial_cycles())


@app.task(name="ai_market_monitor.trial_reminders_due")
def trial_reminders_due() -> dict:
    return _run_async_task(_send_trial_cycle_reminders())


@app.task(name="ai_market_monitor.retry_telegram_deliveries")
def retry_telegram_deliveries() -> dict:
    return _run_async_task(_retry_telegram_deliveries())


@app.task(name="ai_market_monitor.retry_email_deliveries")
def retry_email_deliveries() -> dict:
    return _run_async_task(_retry_email_deliveries())


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


@app.task(name="ai_market_monitor.record_database_health")
def record_database_health() -> dict:
    return _run_async_task(_record_database_health())


@app.task(name="ai_market_monitor.flush_operational_metrics")
def flush_operational_metrics() -> dict:
    return _flush_metrics_now()


@app.task(name="ai_market_monitor.compact_operational_metrics")
def compact_operational_metrics() -> dict:
    return _run_async_task(_compact_operational_metrics())


@app.task(name="ai_market_monitor.deliver_operational_alerts")
def deliver_operational_alerts() -> dict:
    return _run_async_task(_deliver_operational_alerts())


@app.task(name="ai_market_monitor.retry_operational_alert_deliveries")
def retry_operational_alert_deliveries() -> dict:
    return _run_async_task(_retry_operational_alert_deliveries())


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


@app.task(name="ai_market_monitor.recover_setup_chat_turns")
def recover_setup_chat_turns() -> dict:
    return _run_async_task(_recover_setup_chat_turns())


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


@app.task(name="ai_market_monitor.cleanup_scan_history")
def cleanup_scan_history() -> dict:
    return _run_async_task(_cleanup_scan_history())


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
    return _run_async_task(_process_sharia_authority_imports())


@app.task(name="ai_market_monitor.process_sharia_authority_imports")
def process_sharia_authority_imports() -> dict:
    return _run_async_task(_process_sharia_authority_imports())


@app.task(name="ai_market_monitor.resolve_official_sources")
def resolve_official_sources() -> dict:
    return _run_async_task(_resolve_official_sources())


@app.task(name="ai_market_monitor.send_sharia_review_reminders")
def send_sharia_review_reminders() -> dict:
    return _run_async_task(_send_sharia_review_reminders())


@app.task(name="ai_market_monitor.retry_payment_emails")
def retry_payment_emails() -> dict:
    return _run_async_task(_retry_payment_emails())


@app.task(name="ai_market_monitor.retry_account_emails")
def retry_account_emails() -> dict:
    return _run_async_task(_retry_account_emails())


@app.task(name="ai_market_monitor.retry_public_inquiry_emails")
def retry_public_inquiry_emails() -> dict:
    return _run_async_task(_retry_public_inquiry_emails())


@app.task(name="ai_market_monitor.retry_public_form_deliveries")
def retry_public_form_deliveries() -> dict:
    return _run_async_task(_retry_public_form_deliveries())


@app.task(name="ai_market_monitor.cleanup_public_chat_data")
def cleanup_public_chat_data() -> dict:
    return _run_async_task(_cleanup_public_chat_data())


@app.task(name="ai_market_monitor.expire_ended_paid_access")
def expire_ended_paid_access() -> dict:
    return _run_async_task(_expire_ended_paid_access())


@app.task(name="ai_market_monitor.retry_sharia_admin_telegram")
def retry_sharia_admin_telegram() -> dict:
    return _run_async_task(_retry_sharia_admin_telegram())


@app.task(name="ai_market_monitor.monitor_published_sharia_sources")
def monitor_published_sharia_sources() -> dict:
    return _run_async_task(_monitor_published_sharia_sources())


@app.task(name="ai_market_monitor.research_unscreened_coins")
def research_unscreened_coins() -> dict:
    return _run_async_task(_research_unscreened_coins())


@app.task(name="ai_market_monitor.screen_researched_coins")
def screen_researched_coins() -> dict:
    return _run_async_task(_screen_researched_coins())


@app.task(name="ai_market_monitor.refresh_market_numbers")
def refresh_market_numbers() -> dict:
    return _run_async_task(_refresh_market_numbers())


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


async def _sweep_expired_ai_reservations() -> dict:
    """Return budget promised by workers that never came back.

    Without this a crashed worker's reservation holds capacity for ever: the allowance
    shrinks a little with every crash, nobody notices until people start being refused,
    and the only cure is restarting everything.
    """

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.ai_budget import AIBudgetService, limits_from_settings

    async with SessionFactory() as session:
        swept = await AIBudgetService(session, limits_from_settings(settings)).sweep_expired()
        await session.commit()
        return {"swept": swept}


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


async def _retry_email_deliveries() -> dict:
    """Send the alert emails that are waiting, and retry the ones that failed.

    The same shape as the Telegram retry above, on purpose: email is a real delivery
    channel with the same retry rules and the same failure codes, not a side path that
    quietly behaves differently when something goes wrong.
    """

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.alert_emails import EmailAlertDeliveryService
    from ai_market_monitor.services.email_delivery import email_delivery_available

    if not email_delivery_available(settings):
        return {"processed": 0, "disabled": True, "reason": "email_sender_not_configured"}

    async with SessionFactory() as session:
        processed = await EmailAlertDeliveryService(session, settings).process_due()
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
    from ai_market_monitor.services.market_preview import MarketPreviewService
    from ai_market_monitor.services.market_provider import market_data_provider
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

    provider = market_data_provider(settings)
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
        return {"deleted": int(getattr(result, "rowcount", 0) or 0)}


async def _compact_operational_metrics() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.observability.durable_metrics import DurableMetricsStore

    async with SessionFactory() as session:
        return await DurableMetricsStore(
            session, policy=settings.metric_retention_policy
        ).compact()


async def _deliver_operational_alerts() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.observability.alert_delivery import OperationalAlertDispatcher

    async with SessionFactory() as session:
        return await OperationalAlertDispatcher(session, settings).dispatch_due()


async def _retry_operational_alert_deliveries() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.observability.alert_delivery import OperationalAlertDispatcher

    async with SessionFactory() as session:
        return await OperationalAlertDispatcher(session, settings).process_due()


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
    from ai_market_monitor.services.market_provider import market_data_provider
    from ai_market_monitor.services.scanner import ScanOrchestrator

    provider = market_data_provider(settings)
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
    from ai_market_monitor.services.market_provider import market_data_provider

    provider = market_data_provider(settings)
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


async def _fetch_exchange_symbols(
    provider: "MarketDataProvider",
    exchange_names: tuple[str, ...],
) -> dict[str, set[str]]:
    """Fetch each exchange's USDT symbol list independently.

    A failed fetch for one exchange is not evidence that exchange has no
    listings — it means this run has no fresh data for it. Leaving the key out
    (rather than storing an empty set) lets the other exchange's fetch and the
    rest of the caller's work continue, and keeps map_candidate() from reading
    "couldn't check" as "confirmed delisted".
    """
    exchange_symbols: dict[str, set[str]] = {}
    for exchange_name in exchange_names:
        try:
            exchange_symbols[exchange_name] = {
                symbol.upper()
                for symbol in await provider.list_symbols(exchange_name, ["USDT"])
            }
        except Exception:
            logger.exception(
                "Could not fetch the %s symbol list; leaving its stored "
                "market-availability flags unchanged this run",
                exchange_name,
            )
    return exchange_symbols


async def _process_sharia_authority_imports() -> dict:
    import asyncio
    from dataclasses import asdict
    from uuid import UUID

    from sqlalchemy import select

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.db.models import (
        AssetResearchDossier,
        CanonicalAsset,
        ExternalAssessment,
        ReviewCase,
    )
    from ai_market_monitor.services.fasset_import import FassetImporter
    from ai_market_monitor.services.market_provider import market_data_provider
    from ai_market_monitor.services.sc_malaysia_import import SCMalaysiaImporter
    from ai_market_monitor.services.sharia_governance import ShariaAdminTelegramService
    from ai_market_monitor.services.sharia_identity import (
        CanonicalAssetMappingService,
        can_reuse_verified_mapping,
    )
    from ai_market_monitor.services.sharia_identity_discovery import (
        CoinGeckoIdentityDiscovery,
        IdentityDiscoveryError,
    )
    from ai_market_monitor.services.sharia_import_pack import (
        ShariaMethodologyImportPackService,
    )
    from ai_market_monitor.services.sharia_research import ShariaResearchPipeline

    pack_import: dict = {"status": "not_run"}
    async with SessionFactory() as session:
        try:
            pack_result = await ShariaMethodologyImportPackService(
                session,
                settings,
            ).import_bundle()
            await session.commit()
            pack_import = {"status": "completed", **pack_result.as_dict()}
        except Exception as exc:
            await session.rollback()
            logger.exception("Sharia methodology import pack failed")
            pack_import = {
                "status": "failed",
                "error_type": type(exc).__name__,
            }

    package_enrichment = await _process_package_enrichment_queue()
    provider = market_data_provider(settings)
    try:
        exchange_symbols = await _fetch_exchange_symbols(provider, ("binance", "bybit"))
        imports: dict[str, dict] = {}
        for source_name, importer_type in (
            ("sc_malaysia", SCMalaysiaImporter),
            ("fasset", FassetImporter),
        ):
            async with SessionFactory() as session:
                try:
                    imported = await importer_type(session, settings).import_latest()
                    await session.commit()
                    imports[source_name] = {
                        "status": "completed",
                        **asdict(imported),
                    }
                except Exception as exc:
                    await session.rollback()
                    logger.exception("%s authority import failed", source_name)
                    imports[source_name] = {
                        "status": "failed",
                        "error_type": type(exc).__name__,
                    }
        allowed = settings.sharia_pilot_symbol_set
        if settings.sharia_process_remaining_imports:
            allowed = set()
        async with SessionFactory() as session:
            query = (
                select(ExternalAssessment.id)
                .where(
                    ExternalAssessment.normalized_status == "ELIGIBLE_EXTERNAL_REFERENCE",
                    ExternalAssessment.source_row_id.is_not(None),
                )
                .order_by(ExternalAssessment.created_at.asc())
                .limit(settings.sharia_identity_discovery_batch_size)
            )
            if allowed:
                query = query.where(ExternalAssessment.asset_symbol.in_(allowed))
            draft_ids = list((await session.scalars(query)).all())

        discovery = CoinGeckoIdentityDiscovery(settings)
        mapped = 0
        cases = 0
        failed = 0
        research_queue: list[tuple[UUID, str]] = []
        for external_id in draft_ids:
            external_symbol = str(external_id)
            async with SessionFactory() as session:
                external = await session.get(ExternalAssessment, external_id)
                if external is None:
                    failed += 1
                    continue
                external_symbol = external.asset_symbol
                reusable_asset = (
                    await session.get(
                        CanonicalAsset,
                        external.canonical_asset_id,
                    )
                    if external.canonical_asset_id is not None
                    else None
                )
                try:
                    if reusable_asset is not None and can_reuse_verified_mapping(
                        external,
                        reusable_asset,
                    ):
                        external.mapping_state = "mapped"
                    else:
                        candidate = await discovery.candidate_for(
                            external,
                            exchange_symbols=exchange_symbols,
                        )
                        await CanonicalAssetMappingService(session).map_candidate(
                            external,
                            candidate,
                            fetched_exchanges=set(exchange_symbols.keys()),
                        )
                    await session.commit()
                    mapped += 1
                except IdentityDiscoveryError as exc:
                    external.mapping_state = "conflict"
                    external.mapping_notes = sorted(
                        {
                            *list(external.mapping_notes or []),
                            f"{exc.code}: {exc}",
                        }
                    )
                    await session.commit()
                    failed += 1
                    continue
                except Exception:
                    await session.rollback()
                    logger.exception(
                        "Authority assessment mapping failed for %s",
                        external_symbol,
                    )
                    failed += 1
                    continue

            # Build a bounded research queue after canonical identities are
            # committed. Completed immutable dossiers remain authoritative.
            async with SessionFactory() as session:
                from ai_market_monitor.services import (
                    sharia_dossier_state as dossier_state,
                )

                completed_dossier_id = await session.scalar(
                    select(AssetResearchDossier.id)
                    .where(
                        AssetResearchDossier.external_assessment_id == external_id,
                        dossier_state.complete_state_clause(AssetResearchDossier.state),
                    )
                    .limit(1)
                )
                existing_case_id = await session.scalar(
                    select(ReviewCase.id)
                    .where(ReviewCase.external_assessment_id == external_id)
                    .limit(1)
                )
                if completed_dossier_id is not None and existing_case_id is not None:
                    external = await session.get(
                        ExternalAssessment,
                        external_id,
                    )
                    if external is not None:
                        external.enrichment_state = "completed"
                    await session.commit()
                    continue
                research_queue.append((external_id, external_symbol))

        # Separate sessions make concurrent provider waits safe. Four assets is a
        # conservative bound for official hosts and the configured model budget.
        research_semaphore = asyncio.Semaphore(4)

        async def research_one(
            external_id: UUID,
            external_symbol: str,
        ) -> tuple[int, int]:
            async with research_semaphore, SessionFactory() as session:
                try:
                    result = await ShariaResearchPipeline(session, settings).research_initial_asset(
                        external_id
                    )
                    external = await session.get(
                        ExternalAssessment,
                        external_id,
                    )
                    if external is not None:
                        external.enrichment_state = (
                            "completed" if result.ai_status == "completed" else "failed"
                        )
                    if result.case_id:
                        case = await session.get(ReviewCase, UUID(result.case_id))
                        if case is not None:
                            await ShariaAdminTelegramService(session, settings).enqueue(
                                case,
                                notification_type="new_review_required",
                                idempotency_key=f"new-review:{case.id}",
                            )
                    await session.commit()
                    return (1 if result.case_id else 0, 0)
                except Exception:
                    await session.rollback()
                    logger.exception(
                        "Authority assessment research failed for %s",
                        external_symbol,
                    )
                    return (0, 1)

        # Prove each verified asset's official links before research reads them.
        # ``research_initial_asset`` selects *only* sources marked verified, so an asset
        # whose own pages have never been proved is researched from its
        # authority snapshot and nothing else. Running the resolver first is what gives
        # that research something to read.
        source_resolution = await _resolve_official_sources()

        research_results = await asyncio.gather(
            *(
                research_one(external_id, external_symbol)
                for external_id, external_symbol in research_queue
            )
        )
        cases += sum(value[0] for value in research_results)
        failed += sum(value[1] for value in research_results)
        async with SessionFactory() as session:
            delivered = await ShariaAdminTelegramService(session, settings).process_due()
            await session.commit()
        auto_publication = await _auto_publish_ready_imports()
        return {
            "methodology_pack": pack_import,
            "imports": imports,
            "assets_mapped": mapped,
            "review_cases_ready": cases,
            "failed_or_waiting_identity": failed,
            "package_enrichment_completed": package_enrichment["completed"],
            "package_enrichment_failed": package_enrichment["failed"],
            "package_enrichment_considered": package_enrichment["considered"],
            "telegram_attempts_processed": delivered,
            "auto_publication": auto_publication,
            "official_sources": source_resolution,
            "remaining_imports_enabled": settings.sharia_process_remaining_imports,
        }
    finally:
        await provider.close()


async def _resolve_official_sources() -> dict:
    """Find and prove every asset's official news page, and any community page it finds.

    Runs on its own daily, and again inside the authority import sweep so that newly
    approved identities get their links proved before anything researches them.

    A failure here is reported and never raised. Losing the links for one sweep leaves
    the previous, already-proved ones in place; letting the exception escape would take
    down the import run that carries the actual Sharia evidence.
    """

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.sharia_source_resolution import (
        SourceResolutionService,
    )

    if not settings.sharia_source_resolution_enabled:
        return {"status": "disabled"}
    async with SessionFactory() as session:
        try:
            sweep = await SourceResolutionService(session, settings).resolve_pending()
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Official source resolution failed")
            return {"status": "failed"}
    return {
        "status": "completed",
        "assets_checked": len(sweep.assets),
        "links_proved": sweep.proved,
        # Links that answer but have stopped saying anything worth reading. Not a
        # failure and never a withdrawal: it is how many pages the product is now
        # looking for company for.
        "links_gone_quiet": sweep.quiet,
        "sent_to_a_person": sweep.escalated,
    }


async def _research_unscreened_coins() -> dict:
    """Gather provider facts for tradeable coins that carry no Shariah result.

    Runs on its own schedule, so a deployment starts it without anybody asking: after
    the VPS restarts, Celery beat picks this up on its next tick and the researcher
    works through the queue on its own.

    It writes **no Shariah status**. What it stores is the project's own website,
    whitepaper, repository and logo — the factual half of a Passport, gathered in
    advance. Deciding anything about those coins still needs an authority, an
    assessment and a person.

    A failure is reported, never raised. The provider being down leaves the queue where
    it was for the next run.
    """

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.market_provider import market_data_provider
    from ai_market_monitor.services.unscreened_coin_research import (
        UnscreenedCoinResearchService,
    )

    if not settings.unscreened_research_enabled:
        return {"status": "disabled"}
    if not settings.coinmarketcap_enabled:
        return {"status": "provider_disabled"}

    provider = market_data_provider(settings)
    try:
        async with SessionFactory() as session:
            try:
                service = UnscreenedCoinResearchService(
                    session,
                    settings,
                    market_data_provider=provider,
                )
                result = await service.research()
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("Unscreened coin research failed")
                return {"status": "failed"}
        return result.as_dict()
    finally:
        close = getattr(provider, "close", None)
        if close is not None:
            await close()


async def _screen_researched_coins() -> dict:
    """Read the pages of researched coins and record what the automated screen makes of them.

    Runs after the researcher, on its own beat, so a deployment starts it without
    anybody asking. It reads a project's own website, documentation and whitepaper, and
    writes an :class:`AutomatedScreenRun` saying what it found, with the sentence behind
    every reason.

    **It publishes nothing.** No ``AssetShariaAssessment`` is created, no authority's
    data is touched, and ``published`` stays false on every row it writes. What it
    produces is a proposal shown only where the product says a machine made it.

    A failure is reported, never raised, so one unreachable site leaves the rest of the
    queue for the next run.
    """

    from sqlalchemy import select

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.db.models import AutomatedScreenRun, ProviderCoinProfile
    from ai_market_monitor.services.automated_screen_pipeline import (
        AutomatedScreenPipeline,
    )

    if not settings.unscreened_research_enabled:
        return {"status": "disabled"}
    if not settings.coinmarketcap_enabled:
        return {"status": "provider_disabled"}

    async with SessionFactory() as session:
        try:
            # Coins the researcher has gathered links for, that this has not read yet.
            # Worked in market order, so the effort lands where users actually are.
            already = set(
                (await session.scalars(select(AutomatedScreenRun.symbol))).all()
            )
            candidates = (
                await session.scalars(
                    select(ProviderCoinProfile.symbol)
                    .where(
                        ProviderCoinProfile.provider == "coinmarketcap",
                        ProviderCoinProfile.research_state == "researched",
                    )
                    .order_by(ProviderCoinProfile.market_cap_usd.desc().nullslast())
                )
            ).all()
            wanted = [symbol for symbol in candidates if symbol not in already]
            if not wanted:
                return {"status": "nothing_to_screen"}

            pipeline = AutomatedScreenPipeline(session, settings)
            result = await pipeline.run(
                wanted, limit=settings.automated_screen_batch_limit
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Automated screen sweep failed")
            return {"status": "failed"}
    return result.as_dict()


async def _refresh_market_numbers() -> dict:
    """Read size, rank and long-range movement for every screened coin.

    Once a day, two provider calls for the whole list. These are the numbers an exchange
    ticker cannot answer, and none of them changes fast enough to be worth fetching
    while somebody is loading a page.
    """

    from sqlalchemy import select

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.db.models import AssetShariaAssessment
    from ai_market_monitor.services.market_numbers import MarketNumbersService

    if not settings.coinmarketcap_enabled:
        return {"status": "provider_disabled"}

    async with SessionFactory() as session:
        try:
            symbols = (
                await session.scalars(
                    select(AssetShariaAssessment.canonical_asset).distinct()
                )
            ).all()
            result = await MarketNumbersService(session, settings).refresh(symbols)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Market numbers refresh failed")
            return {"status": "failed"}
    return result.as_dict()


async def _process_package_enrichment_queue() -> dict[str, int]:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select, update

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.db.models import (
        AssetResearchDossier,
        ExternalAssessment,
    )
    from ai_market_monitor.services.sharia_research import (
        ShariaResearchPipeline,
    )

    async with SessionFactory() as session:
        from ai_market_monitor.services import sharia_dossier_state as dossier_state

        completed_dossier_exists = (
            select(AssetResearchDossier.id)
            .where(
                AssetResearchDossier.external_assessment_id == ExternalAssessment.id,
                dossier_state.complete_state_clause(AssetResearchDossier.state),
            )
            .exists()
        )
        await session.execute(
            update(ExternalAssessment)
            .where(
                ExternalAssessment.enrichment_state.in_({"queued", "failed", "running"}),
                completed_dossier_exists,
            )
            .values(enrichment_state="completed")
        )
        await session.execute(
            update(ExternalAssessment)
            .where(
                ExternalAssessment.enrichment_state == "running",
                ExternalAssessment.updated_at < datetime.now(UTC) - timedelta(minutes=30),
            )
            .values(enrichment_state="queued")
        )
        await session.commit()
        external_ids = list(
            (
                await session.scalars(
                    select(ExternalAssessment.id).where(
                        ExternalAssessment.mapping_state == "mapped",
                        ExternalAssessment.enrichment_state.in_({"queued", "failed"}),
                        ExternalAssessment.enrichment_task_id.is_not(None),
                    )
                )
            ).all()
        )
    completed = 0
    failed = 0
    for external_id in external_ids:
        async with SessionFactory() as session:
            external = await session.get(
                ExternalAssessment,
                external_id,
            )
            if external is None:
                continue
            external.enrichment_state = "running"
            await session.commit()
        async with SessionFactory() as session:
            external = await session.get(
                ExternalAssessment,
                external_id,
            )
            if external is None:
                continue
            try:
                research = await ShariaResearchPipeline(
                    session,
                    settings,
                ).research_initial_asset(external_id)
                external.enrichment_state = (
                    "completed" if research.ai_status == "completed" else "failed"
                )
                if research.ai_status == "completed":
                    completed += 1
                else:
                    failed += 1
                await session.commit()
            except Exception:
                await session.rollback()
                external = await session.get(
                    ExternalAssessment,
                    external_id,
                )
                if external is None:
                    failed += 1
                    continue
                external.enrichment_state = "failed"
                await session.commit()
                logger.exception(
                    "Package enrichment failed for %s",
                    external.source_row_id,
                )
                failed += 1
    return {
        "considered": len(external_ids),
        "completed": completed,
        "failed": failed,
    }


async def _auto_publish_ready_imports() -> dict[str, int | str]:
    from sqlalchemy import select

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.db.models import (
        ExternalAssessment,
        PublishedAssetAssessment,
        ReviewCase,
        ReviewDecision,
        User,
        UserIdentity,
    )
    from ai_market_monitor.db.models.enums import IdentityProvider, UserRole
    from ai_market_monitor.services.sharia_governance import (
        ShariaGovernanceError,
        ShariaGovernanceService,
    )

    if not settings.sharia_import_auto_publish:
        return {"status": "disabled", "published": 0, "failed": 0}
    actor_email = settings.system_brain_username
    if not actor_email:
        return {
            "status": "actor_unavailable",
            "published": 0,
            "failed": 0,
        }
    async with SessionFactory() as session:
        actor_id = await session.scalar(
            select(User.id)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(
                UserIdentity.provider == IdentityProvider.EMAIL,
                UserIdentity.normalized_identifier == actor_email.casefold(),
                User.role == UserRole.ADMIN,
            )
            .limit(1)
        )
        if actor_id is None:
            return {
                "status": "actor_unavailable",
                "published": 0,
                "failed": 0,
            }
        case_ids = list(
            (
                await session.scalars(
                    select(ReviewCase.id)
                    .join(
                        ExternalAssessment,
                        ExternalAssessment.id == ReviewCase.external_assessment_id,
                    )
                    .where(
                        ReviewCase.state == "ready_for_review",
                        ReviewCase.done_at.is_(None),
                        ExternalAssessment.source_row_id.is_not(None),
                        ExternalAssessment.normalized_status == "ELIGIBLE_EXTERNAL_REFERENCE",
                        ~select(PublishedAssetAssessment.id)
                        .join(
                            ReviewDecision,
                            ReviewDecision.id == PublishedAssetAssessment.review_decision_id,
                        )
                        .where(
                            ReviewDecision.review_case_id == ReviewCase.id,
                            PublishedAssetAssessment.is_active.is_(True),
                        )
                        .exists(),
                    )
                    .order_by(ReviewCase.created_at.asc())
                )
            ).all()
        )
    published = 0
    failed = 0
    for case_id in case_ids:
        async with SessionFactory() as session:
            try:
                await ShariaGovernanceService(
                    session,
                    settings,
                ).auto_publish_external_reference(
                    case_id,
                    admin_user_id=actor_id,
                )
                await session.commit()
                published += 1
            except ShariaGovernanceError:
                await session.rollback()
                logger.exception(
                    "External reference auto-publication was blocked for %s",
                    case_id,
                )
                failed += 1
            except Exception:
                await session.rollback()
                logger.exception(
                    "External reference auto-publication failed for %s",
                    case_id,
                )
                failed += 1
    return {
        "status": "completed",
        "published": published,
        "failed": failed,
    }


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


async def _retry_account_emails() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.account_emails import AccountEmailOutboxService

    async with SessionFactory() as session:
        return await AccountEmailOutboxService(session, settings).process_due()


async def _retry_public_inquiry_emails() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.public_chat import PublicChatService

    async with SessionFactory() as session:
        return await PublicChatService(session, settings).process_due()


async def _retry_public_form_deliveries() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.public_forms import PublicFormsService

    async with SessionFactory() as session:
        service = PublicFormsService(session, settings)
        contact = await service.process_contact_due()
        waitlist = await service.process_waitlist_due()
        return {"contact": contact, "waitlist": waitlist}


async def _cleanup_public_chat_data() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.public_chat import PublicChatService

    async with SessionFactory() as session:
        result = await PublicChatService(session, settings).cleanup_expired()
        await session.commit()
        return result


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


async def _recover_setup_chat_turns() -> dict:
    """Settle Setup Chat turns a crash left half-finished.

    Safe to run on several workers at once and safe to restart mid-pass: each turn is
    claimed by a conditional update that only one worker can win, and each is settled in
    its own transaction.
    """

    from ai_market_monitor.core.config import get_settings
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.setup_chat_recovery import SetupChatRecoveryService

    settings = get_settings()
    async with SessionFactory() as session:
        outcome = await SetupChatRecoveryService(settings).run_once(session)
    return outcome.to_dict()


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
    from ai_market_monitor.services.market_provider import market_data_provider

    provider = market_data_provider(settings)
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
    from ai_market_monitor.services.market_provider import market_data_provider

    provider = market_data_provider(settings)
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
    from ai_market_monitor.services.market_provider import market_data_provider

    provider = market_data_provider(settings)
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


async def _cleanup_scan_history() -> dict:
    """Delete scan history the product no longer needs.

    Deliberately not gated on ``scanning_enabled``. Every other scan task returns early
    when scanning is off, because there is no work to schedule or run. Cleanup is the
    opposite case: with scanning off, nothing is trimming a table that is already full,
    and the operator most likely to run out of disk is the one who paused everything and
    stopped looking.
    """

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.scan_retention import ScanRetentionService

    async with SessionFactory() as session:
        result = await ScanRetentionService(session, settings).run()
        await session.commit()
    return result


async def _cleanup_setup_observability() -> dict:
    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.services.setup_observability import SetupObservabilityService

    async with SessionFactory() as session:
        result = await SetupObservabilityService(session, settings).cleanup()
        await session.commit()
        return result
