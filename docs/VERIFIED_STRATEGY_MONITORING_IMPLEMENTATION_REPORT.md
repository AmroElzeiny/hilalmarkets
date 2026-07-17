# Verified Strategy Monitoring Implementation Report

> Historical record notice (2026-07-17): Discord references below describe retired behavior and
> are not part of the private-beta product. See `docs/RETIRED_DISCORD_COMPATIBILITY.md`.

Date: 2026-07-13

## Status

The verified strategy workflow is implemented end to end across interpretation review, saved
examples, historical preview, immutable strategy versions, activation gates, lifecycle evidence,
alert proof, forensic reconstruction, monitor health, outcome review, controlled draft
suggestions, and portable strategy contracts.

The runtime and behavioral gates pass. Repository-wide Ruff and mypy checks still expose older
static-analysis debt outside this workflow; exact counts and affected boundaries are recorded in
the Test Results section.

## Audit Matrix

| Capability | State found during audit | Delivery | Final status |
| --- | --- | --- | --- |
| AI interpretation and assumption audit | Partial prompt coverage and chat assumptions existed | Added persisted phrase-to-rule statements, mechanics, statuses, resolution actions, approval gate, and audit events | Complete |
| User-defined strategy unit tests | Missing | Added permanent positive, negative, and near-match cases with deterministic historical evaluation and version-specific runs | Complete |
| Historical validation pack | Partial `BacktestJob` infrastructure existed | Reused it as a bounded Historical Preview with exact requested ranges, retained examples, blockers, frequency, breadth, and chart evidence | Complete as Historical Preview |
| Semantic strategy versioning | Partial immutable hashes and numbered versions existed | Added parent/restore lineage, creator, semantic diffs, behavior effects, audited draft save, compare, approval, activation, and restore-as-new-draft | Complete |
| Version attached to alerts | Mostly complete in storage | Corrected presentation and added version badges/copy to web proof, Telegram, and Discord | Complete |
| Condition-level lifecycle | Strong existing lifecycle/observability foundation | Reused setup instances, condition snapshots, lifecycle events, health summaries, and detail timelines | Complete |
| Immutable alert proof | Partial JSON receipt existed | Added proof schema version, seal time, integrity hash, mutation detection, owned receipt page, copy/export, and version links | Complete |
| Missed-alert reconstruction | Partial lifecycle investigation existed | Added guided, persisted point-in-time reconstruction across market, monitor, provider, suppression, delivery, and version evidence | Complete |
| User monitor health and coverage | Existing observability read models and UI | Connected real heartbeat, scan coverage, provider, freshness, notification, and error evidence to verification workspace and lifecycle views | Complete |
| Outcome review | Missing | Added user-defined horizons, classifications, notes, tags, retained price paths, proof/version/lifecycle links | Complete |
| Controlled improvement suggestions | Partial cockpit suggestions existed | Reused suggestions, added evidence/limitations/effect fields, create-as-draft only, automatic saved-test and historical-preview reruns, and approval invalidation | Complete |
| Strategy quality report | Partial validation and health labels existed | Added separate clarity, test coverage, historical coverage, monitoring readiness, and data reliability dimensions with causes | Complete |
| Portable strategy contract | Missing | Added owned human-readable and machine-readable contract with assumptions, tests, history, settings, version, and integrity hash | Complete |

## Consolidation Decisions

- `StrategyVersion` remains the immutable source of approved mechanics. No second strategy schema
  or execution path was introduced.
- Existing `BacktestJob` and deterministic rule evaluation are reused for Historical Preview and
  saved examples. AI text is never executed.
- Existing setup lifecycle, condition snapshot, monitor-cycle, health, notification, and
  bottleneck models remain the operational evidence source.
- Existing `StrategySuggestion` remains the improvement mechanism. Applying a suggestion creates a
  new draft and never mutates or replaces an active version.
- The existing lifecycle-card investigation answers a known lifecycle quickly; the new
  `ForensicInvestigation` stores a guided monitor/symbol/time reconstruction. Both consume the same
  retained evidence and do not invent missing values.

## Architecture and Workflow

1. A prompt or canvas creates a draft `StrategyVersion` with original source text and deterministic
   structured rules.
2. `VerifiedStrategyService.prepare_version` splits source text into statements, maps each phrase
   to immutable rule keys, stores mechanics and assumptions, and creates a verification aggregate.
3. Critical ambiguous, unsupported, or contradictory statements block interpretation approval.
4. The user accepts assumptions, answers clarifications, or edits/removes unsupported mechanics.
5. Saved example tests evaluate the exact version hash at the selected UTC market moment.
6. Historical Preview evaluates only provider candles in the requested range and records explicit
   unavailable/insufficient-data outcomes instead of falling back to current data.
7. The quality report and activation gate combine interpretation, saved-test, history, provider,
   and monitor compatibility evidence without producing a universal quality score.
8. Approval stamps the immutable schema hash only after the verification gate passes. Repeated
   approval is idempotent and does not restamp the original approval time.
9. Activation points the monitor at the approved version. Suggested edits, restores, and normal
   revisions always create a new draft.
10. Scanner/lifecycle processing saves condition evidence, lifecycle transitions, proof receipts,
    delivery attempts, and health telemetry against the exact version.
11. Alert proof is sealed with a deterministic SHA-256 integrity hash. Historical proof does not
    recalculate when markets or strategy versions change.
12. Outcome reviews and possible improvements stay separate from active rules and require explicit
    user approval through the normal verification path.

## Data Model and Migrations

Migration `f2a3b4c5d6e7_add_verified_strategy_workflow.py` adds:

- `strategy_interpretation_statements`
- `strategy_test_cases`
- `strategy_test_runs`
- `strategy_version_verifications`
- `forensic_investigations`
- `outcome_reviews`
- strategy-version parent, restore, creator, semantic-diff, and change-summary fields
- alert proof hash, schema version, and sealed timestamp
- suggestion outcome evidence, historical effects, confidence, and limitations
- tenant, version, status, and time indexes plus foreign-key constraints

Migration `a3b4c5d6e7f8_expand_strategy_logical_operator.py` safely expands persisted logical operators
for current `AND`, `OR`, `NOT`, sequence, temporal, count, cooldown, state-change, confirmation, and
conditional-branch mechanics. It uses Alembic batch mode for SQLite compatibility and remains
compatible with PostgreSQL deployments.

All timestamps are UTC-aware. User-owned records include user/strategy/version keys, and API
lookups enforce ownership before returning or mutating private data.

## Feature Mechanics

### Interpretation Audit

Each persisted statement includes the original phrase, structured interpretation, rule keys,
timeframe, operator, threshold, data source, candle-close requirement, assumptions, status, and
resolution. Supported statuses are confirmed, assumed, ambiguous, unsupported, and contradictory.
Activation cannot bypass unresolved critical statements.

### Saved Strategy Examples

Users can save `should_trigger`, `should_not_trigger`, and `near_match` expectations with symbol,
exchange, timeframe, and UTC timestamp. Runs retain expected/actual result, each condition value,
threshold, evidence, provider, candle timestamp, and mismatch explanation. Every new version can
rerun the permanent suite; a previously passing example that regresses blocks approval.

### Historical Preview

Historical Preview reports matches, near matches, invalidated candidates, clear non-matches,
frequency estimates, breadth/narrowness, common blockers, condition pass/fail counts, and bounded
chart/example evidence. The requested time range is honored exactly. Missing provider history is
shown as unavailable and is never replaced with recent candles. This is monitoring-behavior
validation, not a profit backtest or future-performance claim.

### Versioning and Alert Linkage

Every changed rule set becomes a new version. Semantic diffs use immutable rule-instance identity,
so repeated capabilities are compared independently. Compare output includes rule changes,
confirmed-match history, saved-test status, changed test results, and historical-preview counts.
Rollback restores an older schema as a new draft. Alerts retain strategy, monitor, strategy-version,
and proof references; presentation warns when an alert's version differs from the current version.

### Lifecycle, Proof, and Health

Condition events retain pass/fail state, actual/required values, candle time, freshness, and provider
context. Lifecycle states and score changes describe rule completion, never profit probability.
Proof receipts show summary first, rule evidence second, and delivery evidence last. Monitor health
uses actual scans, heartbeat, expected coverage, provider/freshness incidents, channel state, and
delivery attempts rather than database existence.

### Forensic Reconstruction

The guided investigation takes monitor/strategy, symbol, exchange, timeframe, and UTC time. It
separates market-rule failures, incomplete/intrabar candles, monitor state, cooldown or duplicate
suppression, universe exclusions, provider/data incidents, scan errors, delivery failures, and
version mismatch. Evidence availability is `exact`, `closest`, `system_only`, or `unavailable`; the
conclusion says when exact historical evidence was not retained.

### Outcomes and Controlled Improvements

Outcome horizons support 1h, 4h, 24h, 7d, and validated custom minutes. Classification is supplied
by the user as positive, negative, neutral, or invalid/irrelevant, with optional definitions, notes,
and tags. TraceEdge stores the observed path but does not infer profitability.

Suggestions cite deterministic evidence and limitations. `Apply` creates and tests a new draft,
reruns saved examples, and reruns the same Historical Preview range when available. Historical
completion differences are labelled as preview evidence, not performance. Strong/weak outcome
effects remain explicitly unavailable until enough outcomes are reliably linked.

### Quality Report and Contract

The quality report presents separate dimensions for clarity, test coverage, historical coverage,
monitoring readiness, and data reliability. Every status has an explanation, remaining risks, data
dependencies, frequency context, and available condition influence evidence.

The portable contract contains the human summary, structured schema, original prompt, assumptions,
interpretation audit, unit tests, version lineage, historical summary, activation/delivery settings,
quality report, and deterministic integrity hash. It remains user-owned and does not expose secrets.

## API Surface

- `GET /api/v1/dashboard/strategies/{strategy_id}/verification`
- `POST /api/v1/dashboard/strategies/{strategy_id}/interpretation/{statement_id}/resolve`
- `POST /api/v1/dashboard/strategies/{strategy_id}/versions/{version_id}/interpretation/approve`
- `POST /api/v1/dashboard/strategies/{strategy_id}/versions/{version_id}/save-draft`
- `POST /api/v1/dashboard/strategies/{strategy_id}/tests`
- `POST /api/v1/dashboard/strategies/{strategy_id}/tests/{test_case_id}/run`
- `POST /api/v1/dashboard/strategies/{strategy_id}/versions/{version_id}/tests/run`
- `POST /api/v1/dashboard/strategies/{strategy_id}/versions/{version_id}/historical-validation`
- `POST /api/v1/dashboard/strategies/{strategy_id}/versions/{version_id}/restore`
- `GET /api/v1/dashboard/strategies/{strategy_id}/versions/{version_id}/contract`
- `POST /api/v1/dashboard/strategies/compare`
- `POST /api/v1/dashboard/forensic-investigations`
- `POST /api/v1/dashboard/alerts/{alert_id}/outcomes`
- `GET /api/v1/dashboard/strategies/{strategy_id}/outcomes`
- `GET /api/v1/dashboard/cockpit/alerts/{alert_id}/proof`
- `POST /api/v1/dashboard/cockpit/strategies/{strategy_id}/suggestions`
- `POST /api/v1/dashboard/cockpit/suggestions/{suggestion_id}/apply`

The HTML routes add the strategy verification workspace and owned immutable alert-proof receipt.

## Primary Files Changed

Data and migrations:

- `src/ai_market_monitor/db/models/verified_strategy.py`
- `src/ai_market_monitor/db/models/strategy.py`
- `src/ai_market_monitor/db/models/cockpit.py`
- `src/ai_market_monitor/db/models/accounts.py`
- `src/ai_market_monitor/db/models/system_brain.py`
- `src/ai_market_monitor/db/models/__init__.py`
- `alembic/versions/f2a3b4c5d6e7_add_verified_strategy_workflow.py`
- `alembic/versions/a3b4c5d6e7f8_expand_strategy_logical_operator.py`

Backend behavior:

- `src/ai_market_monitor/services/verified_strategy.py`
- `src/ai_market_monitor/services/dashboard_jobs.py`
- `src/ai_market_monitor/services/strategy.py`
- `src/ai_market_monitor/services/alert_presentation.py`
- `src/ai_market_monitor/services/ai_setup_chat.py`
- `src/ai_market_monitor/services/setup_observability.py`
- `src/ai_market_monitor/cockpit_api.py`
- `src/ai_market_monitor/cockpit_service.py`
- `src/ai_market_monitor/api/routers/dashboard.py`
- `src/ai_market_monitor/api/routers/dashboard_api.py`
- `src/ai_market_monitor/api/routers/onboarding.py`
- `src/ai_market_monitor/telegram/service.py`
- `src/ai_market_monitor/discord/service.py`

Frontend:

- `src/ai_market_monitor/templates/dashboard.html`
- `src/ai_market_monitor/static/dashboard.js`
- `src/ai_market_monitor/static/dashboard.css`

Tests:

- `tests/unit/test_verified_strategy.py`
- `tests/unit/test_setup_observability.py`
- `tests/integration/test_dashboard_api.py`
- `tests/integration/test_dashboard_web.py`
- `tests/integration/test_setup_observability_api.py`
- `tests/integration/test_ai_setup_chat_api.py`
- `tests/integration/test_onboarding_flow.py`
- `tests/integration/test_telegram_service.py`
- `tests/integration/test_discord_service.py`
- `tests/browser/test_dashboard_e2e.py`
- `tests/browser/conftest.py`

## UI, Responsive Design, and Accessibility

- The verification workspace uses summary-first cards with expandable mechanics and QA evidence.
- Desktop uses the existing TraceEdge dashboard grid; the workspace collapses to one column on
  tablet/mobile without squeezing tables into the viewport.
- Buttons use real loading/disabled states and clear success, warning, degraded, unavailable, and
  error messages.
- Focus-visible styles, semantic buttons/forms/details, labelled SVG charts, keyboard-compatible
  actions, text status labels, and non-color-only feedback are preserved.
- Motion uses existing 150-250ms brand transitions and is disabled/reduced under
  `prefers-reduced-motion`.
- No new generic AI gradient system or icon style was introduced.

Visual QA artifacts:

- `reports/playwright/visual-qa/verified-strategy-workflow-desktop.png`
- `reports/playwright/visual-qa/verified-strategy-workflow-mobile-390.png`
- `reports/playwright/visual-qa/immutable-alert-proof-desktop.png`
- `reports/playwright/visual-qa/setup-observability-desktop.png`
- `reports/playwright/visual-qa/setup-observability-mobile-390.png`
- `reports/playwright/visual-qa/forming-candidate.png`
- `reports/playwright/visual-qa/near-miss-candidate.png`
- `reports/playwright/visual-qa/degraded-too-strict-monitor.png`
- `reports/playwright/visual-qa/bottleneck-intelligence.png`
- `reports/playwright/visual-qa/candidate-detail-timeline.png`
- `reports/playwright/visual-qa/provider-error-state.png`
- `reports/playwright/visual-qa/empty-radar-state.png`

## Security, Integrity, and AI Boundaries

- All private endpoints resolve the authenticated principal and verify ownership server-side.
- Approved versions are immutable; draft saves reject approved, active, and superseded versions.
- Approval is idempotent and requires the same immutable schema hash.
- Proof hashes are recomputed on access; a mismatch raises an integrity error instead of displaying
  altered evidence as valid.
- AI may explain deterministic data or propose a draft. It cannot approve, activate, mutate live
  rules, determine proof outcomes, fabricate missing values, or execute raw text.
- API keys and provider credentials are never returned in contracts, evidence, or frontend state.

## Test Results

Behavioral tests:

- Full backend: `.venv\Scripts\python.exe -m pytest`
  - Result: `1765 passed in 452.53s`.
- Full browser: `.venv\Scripts\python.exe -m pytest tests\browser --junitxml=reports\playwright\playwright-results.xml`
  - Result: `14 passed in 70.47s`.
- Connected verification/observability/chat/channel suite:
  - Result: `132 passed in 87.8s`.
- Post-type-cleanup targeted verification/suggestion suite:
  - Result: `7 passed in 8.7s`.
- Focused migration/model tests:
  - Result: `5 passed in 6.1s`.

Migration checks:

- Fresh SQLite database upgraded from base through `a3b4c5d6e7f8`.
- `alembic current` reported `a3b4c5d6e7f8 (head)`.
- `alembic check` reported `No new upgrade operations detected.`

Static checks:

- JavaScript `node --check`: passed for all 7 static JavaScript files.
- Ruff on verified-workflow source files: passed.
- Mypy on `verified_strategy.py` and `cockpit_api.py`: passed with no issues.
- Repository-wide Ruff currently reports 93 older findings, mainly formatting/import debt in
  scripts, observability text, and legacy tests. The unscoped command also requires a configuration
  fix because the repository's custom `exclude` replaces Ruff's default `.venv` exclusion.
- Repository-wide mypy currently reports 122 older findings across 26 files, concentrated in the
  evaluator, provider context, prompt semantics, market preview, and other pre-existing modules.
  The full runtime suite passes, but the repository-wide static gate is not yet clean.

## Known Limitations

1. Historical Preview validates monitoring behavior against available OHLCV history. It is not a
   trade-execution, slippage, fee, or profitability backtest.
2. Old lifecycle records may predate condition snapshots. Forensics returns closest/system-only or
   unavailable evidence instead of reconstructing values that were never retained.
3. Outcome-based strong/weak effect counts require enough user-classified, reliably linked
   outcomes. Until then, the UI explicitly reports insufficient evidence.
4. Browser tests use deterministic fixture market data and test notification sinks. Real provider,
   Telegram, Discord, and worker delivery should still be smoke-tested in staging with dedicated
   test credentials.
5. The repository-wide Ruff and mypy debt above remains a separate cleanup task; no failures were
   hidden or converted to blanket ignores.

## Manual QA Checklist

- Create a monitor from AI chat and confirm it opens the verification workspace.
- Confirm every source phrase maps to visible mechanics and unresolved critical items block
  approval.
- Add one positive, one negative, and one near-match example at known historical moments.
- Run Historical Preview against a provider-supported range and an intentionally unavailable range.
- Compare two versions with repeated capability types and verify each instance has its own diff.
- Approve and activate only after all gates pass; confirm repeated approval does not change time.
- Trigger a fixture alert and verify web, Telegram, Discord, proof, and version labels agree.
- Modify a draft after an alert and confirm the old receipt stays unchanged and identifies its old
  version.
- Run forensic reconstruction for a failed rule, paused monitor, suppression, provider failure,
  delivery failure, and old record with missing evidence.
- Review outcomes at 1h and a custom horizon; verify classification remains user-defined.
- Apply a possible improvement and confirm it creates a tested draft without changing the active
  monitor.
- Verify desktop keyboard flow, 390px mobile layout, reduced-motion behavior, and error retry states.
