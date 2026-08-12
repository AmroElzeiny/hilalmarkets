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
   `AI_AGENT_CONTROL_ENABLED=true`, `AI_AGENT_SHADOW_MODE=false`,
   `AI_AGENT_ROLLOUT_PERCENT=100`, and `CAPABILITY_EXTENSION_ENABLED=true` from redacted runtime
   diagnostics.
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

## Objectives and the issue queue during the soak

From 2026-08-12 the soak also watches the service-level objectives directly. They are the same
definitions the alerts use, so a soak that stays green is a soak measured against the thresholds
that will page somebody in production. Read them at the same UTC hour as the daily audit:

```bash
curl -fsS -H "X-User-ID: <admin-uuid>" https://<staging-host>/api/v1/admin/health \
  > slo-day-N.json
```

Retain, for each of the seven days:

- every objective's `state`, which is `met`, `breached` or `no_data`;
- `firing_alerts`, which should normally be empty;
- `operational_issues.needs_attention`.

**An objective reading `no_data` for a whole day is a finding, not a pass.** It means nothing
exercised that path, so the objective was never tested. During a soak with live scan jobs and
delivered alerts, `scheduled_scan_completion`, `market_data_freshness`, `alert_delivery_success` and
`worker_liveness` must all report a real number every day. If one of them stays `no_data`, find out
what is not running before trusting anything else in the window.

The launch-blocking objectives are `api_availability`, `setup_chat_turn_success`,
`ai_provider_success`, `scheduled_scan_completion`, `market_data_freshness`,
`alert_delivery_success`, `worker_liveness` and `review_case_sla`. A breach of any one of them
during the window restarts the window after correction, on the same rule as an unexplained
duplicate.

Check the issue queue rather than only the alert list: the queue keeps `occurrence_count` and
`first_seen_at`, so a fault that fired once on day two and again on day six shows as one row with a
count of two. That pattern is invisible in a list of current alerts and is exactly the kind of
intermittent fault a seven-day window exists to catch.

### Two more controlled events

8. Take the AI provider offline for ten minutes. Verify that customers see the AI-unavailable
   banner, that approved Watchlists keep evaluating and keep delivering alerts, and that **no
   customer-visible message describes the outage as a Shariah, screening or compiler problem**.
   Verify `ai_provider_degraded` fires and one issue row is created.
9. Let the same alert fire on two separate days. Verify the queue holds **one** row with
   `occurrence_count` of two and an audit trail showing the reopen, not two rows.

## Acceptance

All seven daily audit outputs must report `status: pass` and every duplicate-group value must be
zero. No terminal journey may reopen, no unknown Sharia state may become eligible, no alert may lose
its Passport/strategy-version binding, and no disabled channel or billing action may become active.

Provider delivery, market correctness and process availability require their own retained
telemetry; a zero-duplicate database audit cannot prove those facts. Any unexplained gap or duplicate
restarts the seven-day window after correction.
