# Private-Beta Seven-Day Soak

This runbook verifies duplicate resistance and operational continuity before inviting external
users. It is a staging procedure, not a unit test and not evidence of live provider correctness by
itself.

## Scope

Run with the private-beta flags and allowlist: BTC, ETH and SOL on Binance spot; one approved
methodology; in-app and Telegram delivery; billing, Discord, WhatsApp, live Bounded Agent execution,
and capability extensions disabled.

Observe continuously for seven complete UTC days:

- scheduler-created live scan jobs;
- one market result per job, symbol, timeframe and direction;
- opportunity journey identities and terminal transitions;
- alert deduplication and delivery attempts;
- public-inquiry email event keys;
- payment email event keys, expected to remain zero while billing is disabled;
- API, database, Redis, worker and scheduler health;
- provider errors, stale data and abnormal Sharia exclusions.

## Start

1. Record deployment commit, Alembic head, environment name, UTC start, approved methodology and
   Passport IDs, exact exchange mappings, enabled monitor IDs and worker/scheduler versions.
2. Confirm `BILLING_ENABLED=false`, `WHATSAPP_ENABLED=false`,
   `AI_AGENT_CONTROL_ENABLED=true`, `AI_AGENT_SHADOW_MODE=true`, and
   `CAPABILITY_EXTENSION_ENABLED=false` from redacted runtime diagnostics.
3. Confirm only one scheduler instance is active.
4. Capture the baseline:

```bash
python scripts/audit_private_beta_soak.py --days 1 > soak-day-0.json
```

Store the output outside Git with deployment logs and monitoring screenshots.

## Daily Checks

At the same UTC hour each day, retain:

```bash
python scripts/audit_private_beta_soak.py --days 7 > soak-day-N.json
```

Also retain queue depth, worker heartbeat, scheduler heartbeat, provider failure counts, stale-data
counts, `sharia_fail_closed_total{reason}`, included/excluded universe counts, Telegram delivery
attempts and application error-rate/latency charts. Investigate any process restart, corrected
candle, safety hold, status change or provider incident and link it to the affected persisted IDs.

## Controlled Events

During the window, execute and record each once:

1. Restart a worker while a job is claimable; verify stale-claim recovery does not create a second
   job, result, journey or alert.
2. Restart the scheduler; verify no duplicate `(strategy_version_id, scheduled_for)` live slot.
3. Pause and resume a Watch Plan; verify scans and alerts stop and resume without reopening a
   terminal journey.
4. Apply and remove a Sharia safety hold through the audited governance flow.
5. Simulate one provider timeout and one stale-candle result; neither may become Eligible or
   Confirmed.
6. Retry one Telegram failure; verify one logical delivery destination and bounded attempts.
7. Submit one public inquiry and force one retry; verify exactly two logical recipient events and
   no duplicated email event keys.

## Acceptance

All seven daily audit outputs must report `status: pass` and every duplicate-group value must be
zero. No terminal journey may reopen, no unknown Sharia state may become eligible, no alert may lose
its Passport/strategy-version binding, and no disabled channel or billing action may become active.

Provider delivery, market correctness and process availability require their own retained
telemetry; a zero-duplicate database audit cannot prove those facts. Any unexplained gap or duplicate
restarts the seven-day window after correction.
