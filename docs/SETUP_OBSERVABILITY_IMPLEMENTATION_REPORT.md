# Setup Observability Implementation Report

> Historical record notice (2026-07-17): Discord references below describe retired behavior and
> are not part of the private-beta product. See `docs/RETIRED_DISCORD_COMPATIBILITY.md`.

## 1. Architecture and mechanism

Setup observability is one pipeline shared by the readiness radar, monitor/strategy
health, bottleneck intelligence, and lifecycle investigation. The deterministic
scanner persists a candidate snapshot after evaluation. Notification adapters then
update that same candidate when delivery succeeds or fails. Worker jobs maintain
cycle telemetry, health summaries, condition aggregates, and retention.

The browser reads bounded, tenant-scoped API payloads. It never recalculates candle
history and never asks AI to decide whether a rule passed. AI receives only a bounded
structured health or investigation payload and can explain it in plain language.

## 2. Files changed

Core implementation:

- `src/ai_market_monitor/db/models/observability.py`
- `src/ai_market_monitor/db/models/__init__.py`
- `src/ai_market_monitor/services/setup_observability.py`
- `src/ai_market_monitor/services/scanner.py`
- `src/ai_market_monitor/services/notifications.py`
- `src/ai_market_monitor/discord/service.py`
- `src/ai_market_monitor/worker.py`
- `src/ai_market_monitor/api/routers/dashboard.py`
- `src/ai_market_monitor/api/routers/dashboard_api.py`
- `src/ai_market_monitor/services/lifecycle_dashboard.py`
- `src/ai_market_monitor/templates/dashboard.html`
- `src/ai_market_monitor/static/dashboard.js`
- `src/ai_market_monitor/static/dashboard.css`
- `src/ai_market_monitor/core/config.py`
- `.env.example`

Migrations and tests:

- `alembic/versions/d0e1f2a3b4c5_add_setup_observability.py`
- `alembic/versions/e1f2a3b4c5d6_add_observability_counterfactual.py`
- `tests/unit/test_setup_observability.py`
- `tests/integration/test_setup_observability_api.py`
- `tests/browser/conftest.py`
- `tests/browser/test_dashboard_e2e.py`

## 3. Database models and migrations

`MonitorEvaluationCycle` stores one bounded operational summary per scan cycle.
`CandidateReadinessSnapshot` stores the latest state for a unique monitor, version,
symbol, and timeframe. `MonitorHealthSummary` stores precomputed technical and
strategy health. `ConditionObservabilityAggregate` stores version/window condition
statistics and preview-only counterfactual evidence. `ObservabilityExplanation`
stores bounded grounded explanations for audit and short-retention cleanup.

Both migrations have been applied locally. Current and head are
`e1f2a3b4c5d6`.

## 4. Readiness calculation

Readiness is deterministic. It uses separate counts for primary triggers, required
filters, required confirmations, and optional rules. Confirmed requires every
required executable rule to pass and no mandatory unavailable rule. Optional rules
are reported separately and do not silently become required.

The UI reports stage, passed/total required conditions, blocker severity, and the
next mathematically valid distance. Numeric distance is shown only for compatible
comparators and units. Unrelated units are never combined into a universal AI score.

## 5. Lifecycle state rules

The radar supports Not Started, Forming, Confirmation Pending, Near Miss,
Confirmed, Invalidated, Expired, and Provider/Data Error. Scanner outcomes and
retained setup state map to those labels. Delivery status is tracked separately so
a deterministic confirmation with failed delivery is not presented as a delivered
alert.

## 6. Technical and strategy health

Technical health uses worker heartbeat recency, cycle status, scanned/expected
coverage, delayed evaluations, missing candles, provider failures, unsupported
conditions, and active notification channels. Labels are Healthy, Degraded,
Offline, or Misconfigured, always with cause records.

Strategy health uses candidate volume, confirmed frequency, near-miss frequency,
invalidation frequency, unavailable evidence, alert frequency, and dominant
bottlenecks. Labels include Healthy, Too Strict, Too Broad, Potentially Noisy,
Contradictory, Insufficient History, and Provider-Limited. Low sample sizes are
explicit and do not produce confident recommendations.

## 7. Bottleneck aggregation

Background aggregation calculates evaluation/pass/fail counts, final-blocker and
near-miss-blocker counts, invalidation counts, average actual/required/distance,
co-occurrence, and previous-version pass-rate delta. Ranking prioritizes final
blocker share and then observed failures.

Counterfactual cards are generated only when retained numeric evidence and the
minimum sample are available. They count historical condition completions at a
proposed observed threshold. They are labelled preview-only and are not profit or
performance predictions. No rule is edited automatically.

## 8. AI boundaries and prompts

`GroundedObservabilityExplainer` receives only the serialized health or lifecycle
investigation payload. The prompt forbids invented values, status changes, advice,
or rule mutation. The deterministic primary reason remains visible beside the AI
explanation. Missing OpenAI configuration returns a safe deterministic explanation.

## 9. Retention and performance

Configuration in `.env.example` controls detailed explanation/cycle retention,
lifecycle aggregate retention, aggregation window, minimum sample size, candidate
staleness, polling, and per-user candidate bounds. Candidate and summary tables use
tenant/version/time/state indexes. Radar queries are paginated. Aggregation and
cleanup run in worker beat tasks; dashboard requests read precomputed records.

## 10. UI structure and animations

The Lifecycles page contains a responsive readiness table/card hybrid, health cards,
bottleneck ranking, lifecycle records, and a right-side investigation drawer.
Animations cover state entry, progress changes, skeletons, drawers, filters, and
condition expansion. `prefers-reduced-motion` disables nonessential transitions.

## 11. Mobile behavior

At mobile width the sidebar remains a compact rail, candidates become cards, filters
remain touch-sized, and investigation becomes a full-screen sheet. The same API,
filter, and evidence data are used on desktop and mobile.

## 12. API endpoints

All paths are under `/api/v1/dashboard`:

- `GET /observability/monitors`
- `GET /observability/radar`
- `GET /observability/health`
- `GET /observability/bottlenecks`
- `POST /observability/health/{monitor_id}/explain`
- `GET /lifecycles/{setup_id}/investigation`
- `POST /lifecycles/{setup_id}/investigation/explain`
- `POST /notification-deliveries/{delivery_id}/retry`

## 13. Tests and exact results

- Focused observability/chat/lifecycle matrix: 79 passed in 98.3 seconds.
- Full pytest suite: 1,758 passed in 898.9 seconds.
- Standalone browser suite: 14 passed in 103.8 seconds.
- Focused observability browser scenario: passed in 34.7 seconds.
- Ruff on touched Python paths: passed with the repository's long-line exception for
  existing message-heavy files.
- MyPy on observability models/service: passed.
- `node --check src/ai_market_monitor/static/dashboard.js`: passed.
- `python -m compileall -q src tests`: passed.
- Alembic current/head: both `e1f2a3b4c5d6`.
- `git diff --check`: no whitespace errors; only Windows line-ending notices.

## 14. Visual QA screenshots

- `reports/playwright/visual-qa/setup-observability-desktop.png`
- `reports/playwright/visual-qa/setup-observability-mobile-390.png`
- `reports/playwright/visual-qa/forming-candidate.png`
- `reports/playwright/visual-qa/near-miss-candidate.png`
- `reports/playwright/visual-qa/degraded-too-strict-monitor.png`
- `reports/playwright/visual-qa/bottleneck-intelligence.png`
- `reports/playwright/visual-qa/candidate-detail-timeline.png`
- `reports/playwright/visual-qa/empty-radar-state.png`
- `reports/playwright/visual-qa/provider-error-state.png`

## 15. Known limitations

Historical explanations can only be as exact as retained condition snapshots. When
an older lifecycle predates snapshot persistence, the API returns
`closest_available` or `unavailable` rather than reconstructing or guessing values.
Counterfactuals measure historical condition completion, not returns or future
performance. Live updates use bounded visibility-aware polling because the current
runtime has no shared browser WebSocket event bus.

## 16. Manual QA checklist

- Start API, worker, scheduler, Redis, and the configured database.
- Publish a validated monitor with at least one connected notification channel.
- Confirm a scan cycle creates/reorders radar candidates without a page refresh.
- Confirm required and optional rules remain separated.
- Pause the worker and verify health becomes degraded/offline after its threshold.
- Force a provider failure and verify Provider/Data Error and investigation evidence.
- Force delivery failure and verify Retry Notification appears only for that case.
- Filter by monitor, refresh, and use browser back/forward.
- Open investigation using keyboard and close with Escape.
- Verify mobile at 390 px and reduced-motion browser preference.
