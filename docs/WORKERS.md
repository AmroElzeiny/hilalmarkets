# Workers And Scheduling

Start a worker and beat scheduler:

```powershell
celery -A ai_market_monitor.worker.app worker --loglevel=INFO
celery -A ai_market_monitor.worker.app beat --loglevel=INFO
```

Beat schedules trial maintenance, scan-job creation, stale/retryable scan recovery, setup
expiration, Telegram delivery retries, public-inquiry email retries, and database health
metrics. Each due scan creates only new idempotent `ScanJob` rows. `run_scan_job` atomically claims
only queued jobs, records worker id/claim/heartbeat fields, resolves the plan-capped universe and
persists deterministic evidence per symbol.

When `SCANNING_ENABLED=false`, both scheduling and execution return without creating or running live
jobs. Retryable provider-wide failures are requeued with `next_retry_at`; permanent validation
failures are canceled or failed without Celery's blanket exception autoretry.

Operational checks:

```powershell
celery -A ai_market_monitor.worker.app inspect ping
celery -A ai_market_monitor.worker.app inspect registered
celery -A ai_market_monitor.worker.app inspect scheduled
```

Workers run the same fail-closed production configuration validator as the API. A deployed worker
will refuse unsafe mock providers, SQLite, default secrets, or enabled integrations without their
required credentials.

Since 2026-08-12 that validator also refuses an incoherent operational or launch configuration: a
service-level objective naming a metric nothing emits, an alert routed through the subsystem it
watches, or a launch stage promising something the deployment cannot deliver. A worker fails on
these for the same reason the API does — the worker is where scan and delivery metrics are recorded,
so a worker booted with an unmeasurable objective produces the silence those objectives exist to
detect.
