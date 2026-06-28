# Workers And Scheduling

Start a worker and beat scheduler:

```powershell
celery -A ai_market_monitor.worker.app worker --loglevel=INFO
celery -A ai_market_monitor.worker.app beat --loglevel=INFO
```

Beat schedules trial maintenance, scan-job creation, stale/retryable scan recovery, setup
expiration, Telegram and Discord delivery retries, Discord role synchronization, and database health
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
