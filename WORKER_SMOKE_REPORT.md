# Worker Smoke Report

- Date/time: 2026-06-27T10:01:32.649763+00:00
- Command run: `.venv\Scripts\python.exe scripts\smoke_worker.py`
- Environment: local/test
- Database mode: not used by local smoke script
- Market data mode: fixture
- Worker command: `celery -A ai_market_monitor.worker.app worker --loglevel=INFO`
- Strategy created: Worker Smoke Research Monitor
- Scan job id/reference: inline-worker-smoke
- Symbols scanned: 1
- Proof created: yes
- Alert sink recorded: yes
- Result: PASS

## Failures

- None.

## Remaining Risks

- This local smoke path does not require Redis, Postgres, Telegram, or Discord.
- Run the documented Docker/Celery flow before staging rollout.
- Fixture data is blocked in staging/production by runtime validation.
