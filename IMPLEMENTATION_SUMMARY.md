# Implementation Summary

## Research Monitor Behavior Follow-Up - June 27, 2026

Completed:

- Made internal `StrategyDefinition` research monitors validate without explicit `entry`,
  `targets`, or `risk` payloads.
- Changed default `targets` to an empty list instead of creating a fake 2R target.
- Added neutral research defaults: `entry` defaults to signal-close metadata and `risk`
  defaults to disabled.
- Updated Telegram and shared alert presentation copy to say `Research match confirmed`.
- Updated research-only alert text to explicitly say no user-defined entry, stop, target, or
  R:R context was provided.
- Updated Discord confirmed-alert embeds to use `Research Match Confirmed`, `Required
  completion`, and `User-defined trade context` only when trade context exists.
- Updated landing-page lifecycle copy from entry availability to condition completion and alert
  delivery.
- Updated Telegram monitor help text to describe risk/trade-quality fields as optional.
- Updated docs to describe entry, targets, stops, and reward-to-risk as optional user-provided
  trade context.

Exact files changed in this follow-up:

- `src/ai_market_monitor/schemas/strategy.py`
- `src/ai_market_monitor/services/alert_presentation.py`
- `src/ai_market_monitor/discord/service.py`
- `src/ai_market_monitor/telegram/rendering.py`
- `src/ai_market_monitor/telegram/service.py`
- `src/ai_market_monitor/templates/index.html`
- `src/ai_market_monitor/engine/condition_registry.py`
- `docs/alert-proof.md`
- `docs/ARCHITECTURE.md`
- `docs/strategy-language.md`
- `tests/unit/test_strategy_defaults.py`
- `tests/unit/test_quality_scores.py`
- `tests/integration/test_discord_service.py`
- `tests/integration/test_landing_page.py`
- `IMPLEMENTATION_SUMMARY.md`
- `PLAYWRIGHT_E2E_REPORT.md`
- `reports/playwright/playwright-results.xml`
- `reports/playwright/playwright-summary.json`

Tests added/updated:

- Added direct schema coverage that an internal research monitor can omit entry/risk/targets.
- Added alert-copy coverage proving research-only Telegram text does not show entry/R:R lines.
- Updated Discord alert embed expectations to research-monitor wording.
- Updated landing-page tests to reject the default `Entry zone` lifecycle node and require
  condition-completion lifecycle labels.

Commands run:

- `.venv\Scripts\python.exe -m ruff check src/ai_market_monitor/services/alert_presentation.py src/ai_market_monitor/discord/service.py src/ai_market_monitor/telegram/rendering.py src/ai_market_monitor/telegram/service.py src/ai_market_monitor/schemas/strategy.py src/ai_market_monitor/engine/condition_registry.py tests/unit/test_strategy_defaults.py tests/unit/test_quality_scores.py tests/integration/test_discord_service.py tests/integration/test_landing_page.py`
  - Result: passed.
- `.venv\Scripts\python.exe -m pytest tests/unit/test_strategy_defaults.py tests/unit/test_quality_scores.py::test_research_only_alert_copy_does_not_show_entry_or_rr_context tests/integration/test_discord_service.py::test_discord_alert_embed_delivery_reuses_setup_thread_and_suppresses_duplicates tests/integration/test_landing_page.py::test_landing_page_contains_product_flow_without_performance_claims tests/unit/test_lifecycle.py::test_default_lifecycle_stages_are_research_monitoring_first tests/unit/test_on_demand_scans.py::test_on_demand_scan_returns_proof_without_live_alert_persistence -q`
  - Result: passed, 9 tests.
- `.venv\Scripts\python.exe -m pytest`
  - Result: passed, 426 tests in 256.37 seconds.
- `.venv\Scripts\python.exe -m pytest tests\browser --junitxml=reports\playwright\playwright-results.xml`
  - Result: passed, 9 tests in 82.10 seconds.

Remaining risks/manual checks:

- Real Telegram and Discord delivery with live credentials still needs staging verification.
- Legacy strategies with trade context still show that context, but now as user-defined context
  rather than advice.
- Local Git metadata still returns `NO_GIT_REPOSITORY`; verify in a clean clone before commit.

## Private Beta Readiness Pass - June 27, 2026

Baseline:

- Branch/status: local `.git` metadata is present but `git status --short` returned `NO_GIT_REPOSITORY`; verify a clean clone before committing.
- App entrypoint: `ai_market_monitor.main:app`.
- Local API command: `.venv\Scripts\python.exe -m uvicorn ai_market_monitor.main:app --reload`.
- Worker entrypoint: `ai_market_monitor.worker.app`.
- Worker command: `celery -A ai_market_monitor.worker.app worker --loglevel=INFO`.
- Scheduler command: `celery -A ai_market_monitor.worker.app beat --loglevel=INFO`.
- Market-data architecture: CCXT public spot provider plus deterministic local fixture provider for tests/smoke only.
- Capability status from live registry: 473 total, 330 visible available, 142 hidden provider-required, 1 hidden unsupported.
- Browser test command: `.venv\Scripts\python.exe -m pytest tests\browser --junitxml=reports\playwright\playwright-results.xml`.
- Full test command: `.venv\Scripts\python.exe -m pytest`.

Completed:

- Expanded `.gitignore` for local/runtime/generated files, caches, browser artifacts, databases, logs, and secret/key patterns.
- Added provider placeholders and runtime safety flags to `.env.example`.
- Added provider config settings for fixture mode, Binance public endpoints, CoinGecko, Alternative.me, and FRED.
- Added deployed runtime validation that rejects fixture mode and unwired provider flags.
- Hid provider-required and unsupported concepts from normal capability payloads.
- Preserved audit/admin access through `condition_registry_payload(include_provider_required=True)`.
- Added research-monitor proof fields: `research_monitor`, `monitor_mode`, required/optional condition counts, required completion percent, `match_status`, and `match_rule`.
- Changed default lifecycle dashboard stages from entry-first wording to condition-completion-first wording.
- Added deterministic `FixtureMarketDataProvider` for local/CI scans and smoke scripts.
- Added `scripts/smoke_worker.py`, which generated `WORKER_SMOKE_REPORT.md` with PASS.
- Updated stale capability-audit wording so provider-required concepts are gated for private beta.

Exact files changed:

- `.gitignore`
- `.env.example`
- `CONDITION_CAPABILITY_AUDIT.md`
- `IMPLEMENTATION_SUMMARY.md`
- `PRIVATE_BETA_READINESS_CHECKLIST.md`
- `PROVIDER_ENV_PLACEHOLDERS.md`
- `PROVIDER_REQUIRED_CONCEPTS_AUDIT.md`
- `PROVIDER_SOURCES_RESEARCH.md`
- `REPO_HYGIENE_AUDIT.md`
- `RESEARCH_MONITOR_BEHAVIOR.md`
- `STAGING_ENV_CHECKLIST.md`
- `TEST_SYSTEMS_SETUP.md`
- `TRADING_CONCEPT_LOGIC_AUDIT.md`
- `WORKER_SMOKE_REPORT.md`
- `PLAYWRIGHT_E2E_REPORT.md`
- `docs/PRODUCTION_DEPLOYMENT.md`
- `reports/playwright/playwright-results.xml`
- `reports/playwright/playwright-summary.json`
- `scripts/smoke_worker.py`
- `src/ai_market_monitor/engine/concept_e2e.py`
- `src/ai_market_monitor/api/dependencies.py`
- `src/ai_market_monitor/core/config.py`
- `src/ai_market_monitor/core/startup.py`
- `src/ai_market_monitor/engine/condition_registry.py`
- `src/ai_market_monitor/engine/models.py`
- `src/ai_market_monitor/services/fixture_market_data.py`
- `src/ai_market_monitor/services/lifecycle_dashboard.py`
- `tests/services/test_provider_required_blocking.py`
- `tests/unit/test_condition_expansion_phase2.py`
- `tests/unit/test_condition_registry.py`
- `tests/unit/test_fixture_market_data.py`
- `tests/unit/test_lifecycle.py`
- `tests/unit/test_on_demand_scans.py`
- `tests/unit/test_reliability_security.py`
- `tests/browser/conftest.py`
- `tests/browser/test_dashboard_e2e.py`

Tests and commands run:

- `.venv\Scripts\python.exe -m pytest tests/unit/test_condition_registry.py tests/unit/test_condition_expansion_phase2.py::test_registry_categories_provider_badges_and_builder_markup tests/services/test_provider_required_blocking.py tests/unit/test_on_demand_scans.py::test_on_demand_scan_returns_proof_without_live_alert_persistence tests/unit/test_lifecycle.py tests/unit/test_reliability_security.py::test_deployed_runtime_rejects_fixture_market_data_and_unwired_provider_flags tests/unit/test_fixture_market_data.py -q`
  - Result: passed, 17 tests.
- `.venv\Scripts\python.exe scripts\smoke_worker.py`
  - Result: PASS, report written to `WORKER_SMOKE_REPORT.md`.

Additional tests run:

- Full backend suite: `.venv\Scripts\python.exe -m pytest`
  - Result: passed, 424 tests in 222.56 seconds.
- Browser suite: `.venv\Scripts\python.exe -m pytest tests\browser --junitxml=reports\playwright\playwright-results.xml`
  - Result: passed, 9 tests in 63.78 seconds.
- Focused concept/interpreter suite: `.venv\Scripts\python.exe -m pytest tests\engine tests\services tests\unit\test_interpreter.py tests\unit\test_interpreter_part2_expansion.py tests\interpreter -q`
  - Result: passed.
- Quick Scan browser fixture path: `.venv\Scripts\python.exe -m pytest tests\browser\test_dashboard_e2e.py::test_quick_scan_finder_prompt_flow -q`
  - Result: passed.
- Docker/Celery/Postgres smoke: not run; local smoke script does not require Redis/Postgres.

Provider concepts hidden:

- 142 provider-required concepts are hidden from normal UI and normal prompt executable paths. See `PROVIDER_REQUIRED_CONCEPTS_AUDIT.md` for names.

Provider concepts enabled:

- No newly provider-required concepts were enabled.
- Existing OHLCV/CCXT-backed available concepts remain visible.

Provider sources researched:

- CoinGecko, Binance Spot public market data, Binance USD-M Futures public market data, Alternative.me Fear and Greed, and FRED.
- All non-current provider-backed concepts remain hidden until adapter/proof/rate-limit tests exist.

Private beta readiness estimate:

- Strong local readiness. Core beta safety changes are in place and local backend/browser/smoke tests pass. Staging readiness still requires clean Git metadata, secret scan, real staging env validation, and Telegram/Discord/payment test-system verification.

Next recommended action:

- Run full pytest and browser tests from a clean clone, then configure staging with `ALLOW_MOCK_PROVIDERS=false`, `TRACEDGE_MARKET_DATA_MODE=ccxt`, PostgreSQL, Redis, and only the integrations that have real test credentials.

## Trading Concept E2E Audit And Fix Pass - June 27, 2026

Completed:

- Generated `TRADING_CONCEPT_E2E_MATRIX.md` from the live condition registry,
  compatibility checks, condition template validation, and StrategyDefinition
  schema validation.
- Current concept status:
  - 473 total concepts.
  - 330 GREEN concepts.
  - 142 PROVIDER_REQUIRED concepts.
  - 1 PLANNED concept.
  - 0 YELLOW concepts.
  - 0 RED concepts.
- Added `engine/concept_e2e.py` to produce repeatable matrix rows, status
  counts, and markdown output.
- Added compatibility helpers so the dashboard, prompt interpreter, and audit
  code agree on which concepts are executable, provider-required, or planned.
- Fixed prompt-to-evaluator mappings for liquidity sweeps, break/retest,
  support/resistance, range breakout/breakdown, BOS/CHOCH, equal highs/lows,
  moving-average retests, range expansion, consolidation, impulse candles,
  session/time filters, and swing-state conditions.
- Fixed prompt source preservation for catalogue-matched conditions such as OBV
  and CMF so prompt coverage can prove the user phrase was not ignored.
- Fixed price-threshold parsing so market-cap/funding/open-interest context is
  not falsely parsed as a simple price condition.
- Hardened OpenAI interpretation so coverage failure returns a deterministic
  fallback with a blocking review issue instead of silently marking the draft
  ready.
- Added provider-required activation blocking in the strategy validation path.
- Restored plan-level symbol activation caps for Demo, Trader, and trial plans
  while keeping Quick Scan/Finder light-prompt symbol limits high.
- Added risk-policy assumptions to prompt coverage so parsed stop-distance and
  reward-to-risk requirements are represented instead of falsely marked as
  ignored.
- Updated the dashboard condition drawer to preserve and edit
  `approximation_note` alongside `source_fragment`, `confidence`,
  `provider_required`, and `availability`.
- Added Telegram/logging safety by redacting bot tokens and bearer tokens from
  standard log records.
- Repaired legacy approved strategy hash handling so saved approved monitors can
  be normalized before scanner/on-demand use.
- Raised the trial alert cap configuration to 500 by default and made trial
  enforcement prefer current plan definitions over stale persisted plan JSON.

Exact code files changed:

- `src/ai_market_monitor/core/config.py`
- `src/ai_market_monitor/core/logging.py`
- `src/ai_market_monitor/core/plans.py`
- `src/ai_market_monitor/engine/capabilities.py`
- `src/ai_market_monitor/engine/capability_compatibility.py`
- `src/ai_market_monitor/engine/concept_e2e.py`
- `src/ai_market_monitor/engine/condition_registry.py`
- `src/ai_market_monitor/engine/price_action.py`
- `src/ai_market_monitor/engine/prompt_audit.py`
- `src/ai_market_monitor/services/interpreter.py`
- `src/ai_market_monitor/services/openai_interpreter.py`
- `src/ai_market_monitor/services/on_demand_scans.py`
- `src/ai_market_monitor/services/scanner.py`
- `src/ai_market_monitor/services/strategy_hashes.py`
- `src/ai_market_monitor/services/trials.py`
- `src/ai_market_monitor/static/dashboard.js`
- `src/ai_market_monitor/strategy_cockpit.py`
- `src/ai_market_monitor/telegram/__init__.py`
- `.env`
- `.env.example`

Exact tests added or updated:

- `tests/engine/test_trading_concept_e2e_matrix.py`
- `tests/engine/test_capability_template_schema_evaluator_alignment.py`
- `tests/services/test_prompt_to_strategy_end_to_end.py`
- `tests/dashboard/test_builder_schema_preservation.py`
- `tests/services/test_provider_required_blocking.py`
- `tests/unit/test_condition_expansion_phase2.py`
- `tests/unit/test_interpreter_part2_expansion.py`
- `tests/unit/test_strategy_hashes.py`
- `tests/unit/test_telegram_imports.py`

Documentation created:

- `TRADING_CONCEPT_E2E_MATRIX.md`
- `CAPABILITY_E2E_FIXES.md`
- `API_ENDPOINT_AUDIT.md`
- `PROMPT_INTERPRETER_E2E_AUDIT.md`
- `BUILDER_SCHEMA_PRESERVATION_AUDIT.md`
- `SCANNER_PROOF_CONSISTENCY_AUDIT.md`

Tests run:

- `.venv\Scripts\python.exe -m pytest tests\engine\test_trading_concept_e2e_matrix.py tests\engine\test_capability_template_schema_evaluator_alignment.py tests\services\test_prompt_to_strategy_end_to_end.py tests\dashboard\test_builder_schema_preservation.py tests\services\test_provider_required_blocking.py tests\unit\test_telegram_imports.py tests\unit\test_strategy_hashes.py tests\unit\test_billing_entitlements.py::test_trial_alert_cap_uses_current_definition_when_plan_json_is_stale -q`
  - Result: passed.
- `.venv\Scripts\python.exe -m pytest tests\unit\test_condition_registry.py tests\unit\test_condition_expansion_phase2.py tests\unit\test_interpreter.py tests\unit\test_interpreter_part2_expansion.py tests\interpreter\test_prompt_interpreter_reliability.py tests\unit\test_openai_interpreter.py -q`
  - Result: passed.
- `.venv\Scripts\python.exe -m pytest`
  - Result: passed, 408 tests in 181.85 seconds.

Features actually fixed:

- End-to-end capability status classification.
- Capability template to StrategyDefinition validation.
- GREEN capability evaluator/proof smoke coverage.
- Prompt coverage for catalogue-matched conditions.
- OpenAI coverage-guard fallback behavior.
- Provider-required mandatory activation blocking.
- Risk requirements represented in prompt coverage.
- Strategy activation symbol limits for capped plans.
- Dashboard schema metadata preservation.
- Trial alert-cap enforcement against stale database plan JSON.
- Token redaction in logging.
- Approved strategy hash repair before scanning.

Features only documented:

- Full browser automation for the board/canvas.
- Provider adapters for market cap, token categories, cross-market context,
  macro context, event feeds, order book, derivatives, universe ranking, and
  runtime alert/lifecycle context.
- Deeper live-worker smoke verification after deployment.

Concepts downgraded/hidden:

- `pivot_high_low` remains PLANNED and hidden from normal executable add paths.
- 142 provider-required concepts remain draft/provider-required and block
  mandatory activation until their providers are configured.

Remaining risks:

- Provider-required concepts need real external data providers before they can
  become executable.
- The 60-prompt suite is representative; the requested 150-prompt scale-out is
  still future test expansion.
- Browser-side workflow board behavior still needs Playwright-style automation
  to prove every drag/drop/edit path.
- Existing live Docker queues may contain stale jobs from earlier builds; run a
  worker smoke test after rebuilding.

Next recommended action:

- Add provider adapter placeholders and contract tests for market cap, token
  categories, cross-market BTC/ETH context, order book, derivatives, and event
  feeds, then move each provider-required concept to GREEN only after proof
  receipts include real provider evidence.

## Next-Level Strategy Builder Pass - June 26, 2026

Completed:

- Expanded the Strategy Builder prompt path into structured sections:
  goal, must-have rules, optional confirmations, market/universe, timeframe, alerts/risk,
  things to avoid, pasted notes, and extra instructions.
- Added prompt example chips, a local prompt-improvement helper, and a "Check what this means"
  action.
- Added a dedicated Coverage tab to the dashboard trust panel and maximized Strategy Board.
- Preserved prompt coverage metadata after the user opens the Strategy Board.
- Added condition source-trace rendering in the Coverage panel.
- Changed condition cards to show AI/source badges per condition instead of a global path badge.
- Added provider-required and confidence badges to condition cards.
- Added source fragment, confidence, AI interpreted, provider-required, and availability fields
  to the condition drawer's advanced section.
- Added guidebook category metadata to the condition registry payload.
- Updated the condition library to use guidebook categories, a real Popular bucket, full-catalogue
  search, plan/provider badges, availability status, and preview sentences.
- Added interpretation feedback buttons and a dashboard endpoint that records feedback as audit
  events.
- Added builder documentation:
  `NEXT_LEVEL_BUILDER_SPEC.md`, `STRATEGY_GUIDEBOOK_SPEC.md`,
  `STRATEGY_BOARD_SPEC.md`, `PROMPT_TO_BOARD_FLOW.md`,
  `CONDITION_LIBRARY_SPEC.md`, and `UX_COPY_GUIDE.md`.

Known limitations:

- The admin prompt-test harness UI is still deferred.
- Provider-required market-context conditions remain blocked until a provider-backed evaluator is
  enabled.
- Board connections are still controlled workflow links; arbitrary user-created edge logic remains
  a future advanced-board feature.

## Prompt Interpreter Reliability Pass - June 26, 2026

Completed:

- Added `engine/prompt_audit.py` with `PromptCoverageReport`.
- Added condition-level `source_fragment`, `confidence`, `ai_interpreted`,
  `provider_required`, and `availability` fields.
- Rule-based fallback now attaches provenance to every generated condition.
- Meaningful prompt fragments that are not represented become blocking review issues.
- Fixed decimal-safe prompt fragment splitting.
- Fixed clause-level optional/mandatory detection.
- Added parser support for previous bullish/bearish candle phrasing.
- Added parser support for optional volume confirmation.
- Added provider-required blocking for BTC/ETH cross-symbol context.
- Added ambiguity blocking for vague discretionary phrases such as `ready to pump`,
  `good setups`, `strong coins`, and `high probability`.
- OpenAI interpretation now preserves validation errors, safe output excerpts, and prompt
  coverage metadata when fallback is used.
- OpenAI draft schema is stricter and requires condition source fragments and confidence.
- Added dedicated dashboard endpoint `POST /api/v1/dashboard/strategies/interpret`.
- Dashboard Strategy Builder prompt flow no longer uses Quick Scan interpretation.
- Dashboard preview now shows coverage score, confidence score, source fragments, and
  coverage mapping.
- Added capability compatibility checker and dashboard registry compatibility statuses.

Compatibility status after this pass:

- 473 registered capabilities.
- 301 currently classified as available.
- 140 classified as provider-required.
- 32 classified as unsupported.

Tests added:

- `tests/interpreter/test_prompt_interpreter_reliability.py`
- `tests/engine/test_capability_registry_compatibility.py`

Documentation added:

- `INTERPRETER_AUDIT.md`
- `PROMPT_COVERAGE_SYSTEM.md`
- `CAPABILITY_COMPATIBILITY_REPORT.md`
- `PROMPT_TEST_CASES.md`

Known limitations:

- Cross-symbol context is recognized but blocked until a dedicated provider-backed
  evaluator path is enabled.
- Several capability templates still need evaluator-name alignment before they can be
  marked available.
- The rule-based interpreter is deterministic and safer, but intentionally blocks
  unclear prompts instead of guessing.

## Strategy Canvas Rebuild - June 25, 2026

The dashboard Strategy Builder was rebuilt as a progressive-disclosure Strategy Canvas.

Completed:

- Clean three-path creation screen with prompt recommended.
- Prompt Understanding Preview before opening the visual map.
- Template preview and explicit template application.
- Sticky builder header and validation-gated monitoring actions.
- Desktop outline, visual canvas, and tabbed trust panel.
- Readable condition and nested group cards.
- Searchable condition-library modal.
- Condition, group, monitor, universe, alert, and risk edit drawers.
- Summary, Validation, Preview, and AI Help tabs.
- Visual-diff-only AI suggestions with no automatic schema mutation.
- Advanced-only raw schema, copy schema, and save-template actions.
- Tablet outline tabs, collapsible review panel, and mobile five-step navigation.
- Existing schema hydration, serialization, prompt, template, validation, preview, save, version,
  and publication APIs preserved.

Tests added:

- Static component and progressive-disclosure contract checks.
- Schema/API compatibility hook checks.
- Updated dashboard-render expectations for the new builder copy and structure.

Deferred:

- A builder replay/backtest button remains omitted because standalone Historical Replay is
  intentionally hidden by the current product configuration.

See `STRATEGY_BUILDER_UX_AUDIT.md`, `STRATEGY_CANVAS_REBUILD.md`, and
`BUILDER_COPY_GUIDE.md` for design and copy details.

## Verification Result

- Ruff: passed for `src` and `tests`.
- Python AST syntax validation: passed for `src` and `tests`.
- Pytest: **201 tests passed**.
- Alembic: a fresh SQLite database upgraded successfully through revision
  `3c4d5e6f7a8b`.
- OpenAPI: **23 Strategy Cockpit routes** are registered under
  `/api/v1/dashboard/cockpit`.
- Celery Beat includes recurring scan scheduling, replay processing every 30 seconds,
  stale scan recovery, and hourly strategy-health evaluation.

The repository's original `.venv` is stale because it points to a removed Python 3.12
installation. Verification used a disposable environment and did not change the project's
Python 3.12 requirement.

## Completed In This Pass

### Condition Capability Completion

- All 473 registered conditions are executable and schema-valid.
- Public CCXT context covers cross-market, breadth, ranking, order-book, funding, and
  open-interest families.
- Configurable HTTP contracts cover crypto-index, macro, event, token-category, and derivatives
  enrichment.
- Risk-quality conditions execute inside the same rule tree.
- Persisted alert, lifecycle, and first-true condition state is available through
  `ConditionRuntimeState`.

### 1. Recurring A/B Monitor Experiments

- Running experiments now schedule both versions on every due scan interval.
- Dry-run experiments persist deterministic `ScanResult` proof evidence without creating
  setup instances, alerts, or deliveries.
- Live-monitor experiments require an active monitor and explicitly approved versions.
- Scan jobs store experiment ID, mode, evidence scope, failures, and idempotency data.
- Experiment comparisons refresh from experiment-specific evidence after worker execution.
- Users can retrieve, stop, or explicitly promote an experiment version.
- No version is promoted automatically.

### 2. Provider-Backed Universe Optimization

- CCXT now loads exchange-wide spot ticker metadata for Binance and Bybit.
- Implemented quote-volume, spread, listing-age, market-cap, category, and data-quality
  filtering.
- Implemented quote-volume, lowest-spread, and BTC-relative-strength ranking.
- Listing age uses exchange metadata when available and an earliest daily-candle probe when
  necessary.
- Live scanning merges provider metadata into deterministic market-filter evaluation.
- Required missing metadata produces a specific exclusion reason rather than a guessed value.
- Optional external category and market-cap enrichment is supported through the documented
  `MARKET_METADATA_API_URL` contract.

### 3. AI Wording Over Deterministic Suggestions

- OpenAI can rewrite the wording of a schema-valid deterministic strategy suggestion.
- The model receives only the action, deterministic reason, validated schema diff, and
  bottleneck evidence.
- AI output cannot alter the proposed schema, thresholds, conditions, or activation state.
- Unsupported, unsafe, invalid, or unavailable AI output falls back to deterministic wording.
- The configured model and `OPENAI_REASONING_EFFORT` are reused; minimal reasoning remains the
  default.

### 4. Explicit Market-Regime Fit

- Edge Health now includes a dedicated 12-point `Market-regime fit` component.
- BTC and ETH benchmark candles are evaluated deterministically on the selected exchange.
- The analyzer records trend direction, 24-hour return, EMA 50/200, realized volatility,
  classification, fit score, errors, and evaluation time.
- Scheduled health workers calculate and persist the provider-backed component.
- Dashboard reads can reuse the latest persisted regime evidence without blocking on exchange
  requests.
- Missing benchmark data uses an explicit neutral unavailable result; no regime is invented.

### 5. Worker-Queued Missed Move Analysis

- The API now creates a queued replay and returns immediately.
- Celery workers claim replay jobs with row locking, run the deterministic replay, and finalize
  the linked `MissedMoveAnalysis`.
- Success and failure results are persisted with exact reasons and replay evidence.
- The dashboard polls the analysis endpoint while the replay is queued or running.
- Large replay workloads no longer execute inside the HTTP request.

## Previously Completed Product Capabilities

1. Edge Health snapshots, explanations, trend data, monitor UI, and weekly summaries.
2. Condition bottleneck aggregation with pass, fail, pending, unavailable, and error rates.
3. Alert feedback across dashboard, Telegram, and Discord.
4. Expanded setup lifecycle states, valid transitions, timeline APIs, and lifecycle UI.
5. Explainable proof receipts with setup, universe, cooldown, and timeline references.
6. Deterministic alert-frequency forecasts.
7. Conflict validation and critical publication gates.
8. Schema-valid improvement drafts requiring confirmation.
9. Personal strategy preferences with derive, update, reset, and prompt-use behavior.
10. Alert Quality Inbox with filters, proof, timeline, feedback, and bulk actions.
11. Strategy decay detection, persisted events, worker evaluation, warnings, and summaries.
12. Telegram, Discord, billing, onboarding, subscriptions, exports, and reliability controls.

## Database And Migration Status

Condition history now uses the `ConditionRuntimeState` table so first-true and consecutive-true
state exists independently of setup creation.

Core cockpit migration:

- `alembic/versions/2b3c4d5e6f7a_add_strategy_monitoring_cockpit.py`
- `alembic/versions/3c4d5e6f7a8b_add_condition_runtime_states.py`

## Operational Configuration

The implementation is complete; these settings control external execution:

- `SCANNING_ENABLED=true` enables live and experiment scan scheduling.
- Run both a Celery worker and Celery Beat for recurring experiments, replay jobs, and health
  evaluation.
- `MARKET_DATA_PROVIDER=ccxt` enables Binance or Bybit public market data.
- `OPENAI_EXPLANATION_ENABLED=true` enables the optional wording layer.
- `OPENAI_API_KEY` is required for OpenAI wording; deterministic fallback remains available.
- `MARKET_METADATA_API_URL` and `MARKET_METADATA_API_KEY` are optional and enrich categories
  and market cap. Their request/response contract is documented in `.env.example`.
- Crypto-index, macro, event, token-category, and derivatives-enrichment URL/key placeholders
  are documented in `.env.example` and `docs/PRODUCTION_DEPLOYMENT.md`.

## Main Files Changed In This Pass

- `src/ai_market_monitor/ai_explanations.py`
- `src/ai_market_monitor/market_context.py`
- `src/ai_market_monitor/provider_context.py`
- `src/ai_market_monitor/engine/capabilities.py`
- `src/ai_market_monitor/engine/context_conditions.py`
- `src/ai_market_monitor/engine/evaluator.py`
- `src/ai_market_monitor/engine/price_action.py`
- `src/ai_market_monitor/engine/risk.py`
- `src/ai_market_monitor/cockpit_service.py`
- `src/ai_market_monitor/cockpit_api.py`
- `src/ai_market_monitor/services/scanner.py`
- `src/ai_market_monitor/services/market_preview.py`
- `src/ai_market_monitor/services/dashboard_jobs.py`
- `src/ai_market_monitor/services/interfaces.py`
- `src/ai_market_monitor/worker.py`
- `src/ai_market_monitor/core/config.py`
- `src/ai_market_monitor/templates/dashboard.html`
- `.env.example`

## Partially Implemented

**None of the five items previously listed in this section remains partially implemented.**

External services can still be unavailable or unconfigured, but every corresponding code path
now executes, records an explicit unavailable reason, and fails safely without fabricated data.

## Deliberately Not Implemented

- Automated trade execution, exchange trading keys, wallet permissions, and copy trading.
- Guaranteed profitability, win probability, future prediction, or similar performance claims.
- Automatic strategy mutation or automatic experiment promotion.
- Fabricated category, market-cap, benchmark, exchange, or AI values when a provider is
  unavailable.
- Re-enabling the hidden standalone Historical Replay or Near-Miss Radar pages. Their useful
  evidence remains integrated into Lifecycles and Strategy Cockpit.

## Tests Added Or Expanded

- recurring two-version experiment scheduling
- dry-run evidence isolation from setup and alert delivery
- experiment-specific comparison refresh
- provider metadata filtering and category/relative-strength preview
- deterministic benchmark regime classification
- constrained OpenAI wording through a mocked Responses API
- queued Missed Move creation, replay execution, and worker-style finalization
- existing scanner, dashboard, Telegram, Discord, billing, lifecycle, and onboarding regressions

## Playwright Browser E2E Pass

Implemented real browser-driven dashboard E2E coverage using Python Playwright, matching the
FastAPI/Jinja/static-JavaScript stack.

Files added:

- `playwright.config.json`
- `tests/browser/conftest.py`
- `tests/browser/test_dashboard_e2e.py`
- `docs/PLAYWRIGHT_E2E.md`
- generated `PLAYWRIGHT_E2E_REPORT.md`
- generated `reports/playwright/playwright-results.xml`
- generated `reports/playwright/playwright-summary.json`
- generated `playwright-report/index.html`

Files updated:

- `pyproject.toml`
- `src/ai_market_monitor/templates/auth.html`
- `src/ai_market_monitor/templates/dashboard.html`
- `src/ai_market_monitor/static/dashboard.js`
- `src/ai_market_monitor/schemas/strategy.py`
- `IMPLEMENTATION_SUMMARY.md`

Browser tests added:

- dashboard loads after signup/login
- strategy prompt to coverage preview
- provider-required prompt blocks activation
- Strategy Board metadata survives edit/save/reload
- executable monitor validates and publishes
- Quick Scan/Finder browser flow
- seeded deterministic proof receipt visibility
- Strategy Cockpit smoke
- Telegram/Discord integration handoff smoke

Commands run:

- `.venv\Scripts\python.exe -m pip install --timeout 180 --retries 5 playwright==1.60.0`
- `.venv\Scripts\python.exe -m playwright install chromium`
- `.venv\Scripts\python.exe -m pytest tests\browser --collect-only`
- `.venv\Scripts\python.exe -m pytest tests\browser --junitxml=reports\playwright\playwright-results.xml`
- `.venv\Scripts\python.exe -m pytest`

Results:

- Playwright browser suite: 9 passed.
- Full Python suite: 418 passed.
- Browser auto-start works with `uvicorn ai_market_monitor.main:app` and a disposable SQLite DB.
- Chromium browser binaries are installed locally.
- Failure screenshots, traces, and videos are retained under `test-results/browser`.

Still manual/live-provider:

- Live Telegram and Discord message delivery require real test tokens and are not sent by the
  browser suite.
- Quick Scan uses browser-layer mocked interpretation and light-scan responses to avoid live
  exchange dependency in E2E.
- Long-running worker scans, production billing webhooks, and live market-data reliability remain
  covered by lower-level tests and manual/staging checks.

## Prompt Understanding Vocabulary Expansion

Date: 2026-06-28

TraceEdge prompt interpretation was expanded with a maintainable vocabulary and semantic layer
instead of one-off regex patches.

Files added:

- `src/ai_market_monitor/engine/prompt_vocabulary.json`
- `src/ai_market_monitor/engine/prompt_semantics.py`
- `scripts/generate_prompt_understanding_corpus.py`
- `tests/fixtures/prompt_understanding_corpus.jsonl`
- `tests/interpreter/test_prompt_semantics_vocabulary.py`
- `tests/interpreter/test_prompt_understanding_corpus.py`
- `PROMPT_UNDERSTANDING_EXPANSION_REPORT.md`
- `PROMPT_LEARNING_QUEUE.md`

Files updated:

- `src/ai_market_monitor/services/interpreter.py`
- `src/ai_market_monitor/static/dashboard.js`
- `src/ai_market_monitor/templates/dashboard.html`
- `src/ai_market_monitor/telegram/service.py`
- `tests/unit/test_interpreter.py`
- `tests/integration/test_telegram_service.py`

Behavior changed:

- The rule-based interpreter now runs a vocabulary-backed semantic pass after the existing
  deterministic parsers.
- Trader wording such as green candle, positive candle, red candle, candle grew, coin up,
  volume not dead, avoid doji, no bearish engulfing, reclaimed VWAP, holding EMA, and RSI
  recovering maps to canonical monitor conditions when context gates pass.
- Broad words such as bullish, positive, green, strong, and ready to pump remain blocked or
  clarification-required when they are not attached to measurable market context.
- Provider-required phrases such as open interest or positive news do not become executable
  conditions.
- Source fragments and confidence values are preserved for generated semantic conditions.
- Entry-first user-facing wording was cleaned up in active interpreter, dashboard, Telegram,
  and test expectations.

Corpus:

- 1,200 generated prompt cases.
- 10 prompt families.
- Minimum requested family counts are satisfied.

How to add phrases later:

1. Add phrases or phrase groups to `src/ai_market_monitor/engine/prompt_vocabulary.json`.
2. Add or adjust generator templates in `scripts/generate_prompt_understanding_corpus.py`.
3. Regenerate `tests/fixtures/prompt_understanding_corpus.jsonl`.
4. Add targeted tests if the phrase introduces a new ambiguity rule.
5. Run the focused interpreter suite and full suite.

## AI Semantic Fallback for Unknown Prompt Language

Date: 2026-06-28

An optional AI semantic fallback was added for unresolved prompt fragments. It is disabled by
default and can only classify unknown language into known registry capabilities after strict
validation.

Files added:

- `src/ai_market_monitor/services/ai_semantic_fallback.py`
- `AI_SEMANTIC_FALLBACK_REPORT.md`

Files updated:

- `src/ai_market_monitor/core/config.py`
- `src/ai_market_monitor/services/openai_interpreter.py`
- `.env.example`
- `tests/interpreter/test_prompt_semantics_vocabulary.py`

Config flags:

- `AI_SEMANTIC_FALLBACK_ENABLED`
- `AI_SEMANTIC_FALLBACK_MODEL`
- `AI_SEMANTIC_FALLBACK_MIN_CONFIDENCE`
- `AI_SEMANTIC_FALLBACK_REVIEW_CONFIDENCE`
- `AI_SEMANTIC_FALLBACK_CACHE_TTL_SECONDS`
- `AI_SEMANTIC_FALLBACK_MAX_CALLS_PER_PROMPT`
- `AI_SEMANTIC_FALLBACK_MAX_FRAGMENT_CHARS`

Safety behavior:

- Deterministic parsing always runs first.
- AI is called only for unresolved fragments.
- AI output must be strict JSON.
- Returned capability keys must exist in the registry.
- Provider-required, hidden, unsupported, vague, malformed, or low-confidence results are blocked
  or marked for review.
- Safe results preserve `source_fragment` and are included in prompt coverage metadata.
- Repeated fragments are cached.

Tests and commands run:

- `.venv\Scripts\python.exe -m pytest tests\interpreter\test_prompt_semantics_vocabulary.py -q`
- `.venv\Scripts\python.exe -m pytest tests\unit\test_interpreter.py tests\unit\test_interpreter_part2_expansion.py tests\unit\test_finder_conditions.py -q`
- `.venv\Scripts\python.exe -m pytest tests\interpreter\test_prompt_understanding_corpus.py -q`
- `.venv\Scripts\python.exe -m pytest tests\interpreter tests\unit\test_interpreter.py tests\unit\test_interpreter_part2_expansion.py tests\unit\test_finder_conditions.py -q`
- `.venv\Scripts\python.exe -m pytest tests\services\test_prompt_to_strategy_end_to_end.py tests\unit\test_interpreter_prompt_mechanics.py tests\unit\test_interpreter_part2_expansion.py tests\unit\test_openai_interpreter.py -q`
- `.venv\Scripts\python.exe -m pytest tests\browser --junitxml=reports\playwright\playwright-results.xml -q`
- `.venv\Scripts\python.exe -m pytest`

Results:

- Focused interpreter and AI fallback tests passed.
- Browser suite passed: 9 tests.
- Full backend suite passed: 1,638 tests.

Remaining risks:

- Real OpenAI fallback calls were not made in tests; mocked responses prove validation behavior.
- The dashboard already renders assumptions and coverage metadata, but a dedicated AI-assisted
  badge remains a UI polish improvement.
- Git metadata in this local checkout is broken, so tracked generated artifacts could not be
  removed with `git rm --cached`; see `REPO_HYGIENE_AUDIT.md`.

## Live VPS Deployment Preparation

Date: 2026-07-06

Deployment target:

- Landing page: `https://trace-edge.com`
- Dashboard/app: `https://app.trace-edge.com`
- `https://www.trace-edge.com` redirects to `https://trace-edge.com`

Files added:

- `docker-compose.prod.yml`
- `deploy/Caddyfile`
- `.env.production.example`
- `deploy/deploy.sh`
- `scripts/deployment_smoke.py`
- `DEPLOY_TRACE_EDGE_LIVE.md`
- `TRACE_EDGE_LIVE_DEPLOYMENT_REPORT.md`
- `tests/integration/test_public_health.py`

Files updated:

- `.gitignore`
- `src/ai_market_monitor/api/routers/public.py`
- `src/ai_market_monitor/core/config.py`
- `src/ai_market_monitor/core/startup.py`

Behavior changed:

- Added shallow `/health` metadata for deployment probes.
- Added `/health/deep` database and Redis checks without leaking exception details.
- Added `APP_BASE_URL` as a first-class setting for `https://app.trace-edge.com`.
- Production/staging validation now requires `APP_BASE_URL` to be HTTPS when set.
- Production/staging validation now rejects placeholder credentials such as `REPLACE_*` in
  critical fields.
- Added a production Compose stack with Caddy, API, worker, scheduler, Postgres, Redis, and
  persistent volumes.
- Added a Caddy configuration for `trace-edge.com`, `app.trace-edge.com`, and the `www` redirect.
- Added a deployment smoke script for health, landing, dashboard, and static assets.

Commands to run after this change:

- `.venv\Scripts\python.exe -m pytest tests\integration\test_public_health.py -q`
- `$env:TRACEDGE_ENV_FILE='.env.production.example'; docker compose --env-file .env.production.example -f docker-compose.prod.yml config`
- `.venv\Scripts\python.exe scripts\deployment_smoke.py --base-url http://127.0.0.1:8000`

Validation run:

- `.venv\Scripts\python.exe -m pytest tests\integration\test_public_health.py tests\unit\test_reliability_security.py -q` passed: `10 passed`.
- `$env:TRACEDGE_ENV_FILE='.env.production.example'; docker compose --env-file .env.production.example -f docker-compose.prod.yml config` passed.
- Production runtime validation passed with `.env.production.example` plus safe dummy replacements
  for placeholder credentials.
- `.venv\Scripts\python.exe scripts\deployment_smoke.py --help` passed.

Manual deployment remains:

- Copy `.env.production.example` to `.env.production` on the VPS.
- Fill real secrets and provider credentials.
- Configure Cloudflare proxied A records for `@`, `www`, and `app`.
- Run `bash deploy/deploy.sh` from the VPS repository root.
- Verify Telegram, Discord, NOWPayments, SMTP, and real market-data behavior before inviting beta
  users.

## 2026-07-13 Certified Capability Extensions

Implemented a production backend for user-approved, non-existing OHLCV mechanics. The feature uses
AI for constrained drafting, contextual diagnosis and implementation review while deterministic
code owns AST validation, parameter bounds, market evaluation, proof, certification, immutable
hashing and strategy activation.

Key behavior:

- `gpt-5.4-nano` with low reasoning is the default draft/implementation model.
- Bybit spot preflight runs before a custom mechanic is offered for strategy approval.
- Imbalanced initial tests escalate through nano-high review, nano-low repair and mini-medium
  review; repair/review calls use Flex.
- Five empty live scans trigger mini-low review. Five scans with candidates but no queued
  notifications trigger mini-high review.
- Repairs cannot alter user logic merely to produce matches and cannot replace an active revision
  without user approval.
- Telegram and chat show creation, market-test, review, repair and strict-strategy states.
- `/system-brain` exposes build logs, certification, live results, AI usage/cost and stage-separated
  quality metrics.
- Registry retrieval is initialized once and cached by deterministic hash; embeddings are secondary
  retrieval only; approved aliases are versioned; clarifications remain review evidence.

Primary implementation files:

- `src/ai_market_monitor/engine/dynamic_mechanics.py`
- `src/ai_market_monitor/engine/capability_index.py`
- `src/ai_market_monitor/services/capability_extension_ai.py`
- `src/ai_market_monitor/services/capability_extensions.py`
- `src/ai_market_monitor/services/capability_registry.py`
- `src/ai_market_monitor/services/hybrid_capability_resolution.py`
- `src/ai_market_monitor/db/models/capability_extensions.py`
- `src/ai_market_monitor/schemas/capability_extensions.py`
- `alembic/versions/ad1e2f3a4b5c_add_capability_extension_pipeline.py`
- `alembic/versions/be2f3a4b5c6d_add_capability_stage_metrics.py`
- `alembic/versions/cf3a4b5c6d7e_add_pending_mechanic_revision.py`

Validation:

- Feature-focused suite: 55 passed.
- Full repository suite: 1,748 passed in 408.9 seconds.
- Browser suite: 13 passed in 58.4 seconds.
- Supported prompt shortlist audit: 2,643/2,643 (100%).
- Fresh migration from an empty SQLite database to `cf3a4b5c6d7e`: passed.
- Feature-local Ruff checks: passed.

Operational documentation and the honest reliability boundary are in
`docs/CAPABILITY_EXTENSION_PIPELINE.md` and the “Certified Non-Existing Mechanics” section of
`docs/AI_SETUP_CHAT_IMPLEMENTATION_REPORT.md`. A real staging OpenAI Flex plus Bybit public-data
run remains required before enabling this beta feature for users.

## 2026-07-13 Verified Strategy Monitoring

Implemented the complete user-controlled verification workflow from phrase-to-rule audit through
saved strategy examples, Historical Preview, immutable version approval, monitor activation,
condition lifecycle evidence, sealed alert proof, missed-alert reconstruction, health review,
user-defined outcome review, controlled draft improvements, and portable strategy contracts.

Key safeguards:

- Critical ambiguity, unsupported mechanics, contradictions, and regressed saved examples block
  approval or activation.
- Approved versions and alert proof are immutable; proof receipts carry a deterministic integrity
  hash and exact strategy-version reference.
- Restores and improvement suggestions create new drafts, rerun saved tests/history where evidence
  exists, invalidate previous approval, and never mutate a live version.
- Missing historical evidence is reported as unavailable or closest retained evidence, never
  invented.
- Outcome classifications remain user-defined and historical completion differences are not
  presented as performance predictions.

Validation:

- Full backend suite: `1765 passed in 452.53s`.
- Full browser suite: `14 passed in 70.47s`.
- Fresh migration through `a3b4c5d6e7f8`: passed; Alembic reports no schema drift.
- All static JavaScript files pass `node --check`.
- Verified-workflow source files pass Ruff and focused mypy.
- Repository-wide Ruff (93 findings) and mypy (122 findings in 26 files) still expose older static
  debt; these are documented rather than hidden.

Architecture, endpoints, files, visual QA artifacts, limitations, and manual checks are documented
in `docs/VERIFIED_STRATEGY_MONITORING_IMPLEMENTATION_REPORT.md`.
