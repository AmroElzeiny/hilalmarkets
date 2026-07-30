# HilalMarkets Codex Handoff

Updated: 2026-07-30

## Purpose

This document is the working context for continuing HilalMarkets in a fresh
conversation. Treat the current local working tree as authoritative. Do not use
older TraceEdge assumptions, reports, names, URLs, or product positioning.

The current task is to make the authenticated HilalMarkets Setup Chat, one-time
Scanner, Strategy Canvas compiler, and continuous Monitor runtime reliable and
launch-ready. The public landing-page Support agent is explicitly outside this
work.

## Read First

Before editing, inspect these sources in this order:

1. `Notion/`
   - Company overview and product system
   - Customer journeys
   - Sharia and evidence boundaries
   - AI authority and safety boundaries
   - Watchlist, Scanner, Monitor, Passport, and notification terminology
   - Private-beta scope and roadmap
2. `brand guide.md`
3. The current HilalMarkets landing page and legal/contact/cookie pages
4. Existing production models, routes, services, workers, migrations, and tests
5. The current dirty Git diff

Use Notion for product truth and the brand guide for visual and voice decisions.
Do not infer product behavior from old implementation reports when current code
or Notion says otherwise.

## Non-Negotiable Product Boundaries

- Preserve authenticated ownership and tenant isolation.
- Preserve `ShariaUniverseResolver` as the execution boundary.
- Preserve deterministic strategy evaluation.
- Preserve immutable strategy versions, evidence, Passports, alerts, journeys,
  source snapshots, and audit history.
- Preserve explicit user approval followed by a separate activation action.
- Preserve fail-closed Sharia and market-data behavior.
- Never execute trades.
- Never let AI issue Sharia rulings or alter external methodology decisions.
- Never expose arbitrary code, SQL, shell, filesystem, or unrestricted network
  execution.
- Never silently substitute a similar trading capability for the requested one.
- Do not weaken validation to satisfy tests.

## Target Runtime Architecture

The authenticated setup path should remain:

```text
API message
-> deterministic intent gate
-> at most one bounded structured AI extraction call when needed
-> deterministic patch
-> StrategyDraftV2
-> deterministic compiler
-> deterministic semantic equivalence validation
-> inactive Canvas preview
-> explicit authenticated approval
-> StrategyService activation gates
-> existing Scanner or Monitor runtime
```

Legacy state is compatibility-only. Legacy orchestration must not mutate V2.

## Work Implemented in the Current Tree

### Semantic compilation

- Added independent `neutral` strategy direction.
- Neutral evaluation runs once rather than fanning out into duplicate long and
  short evaluations.
- Shared direction fan-out is used by Scanner and Monitor.
- Every non-base trigger timeframe is included in supporting candle fetches.
- Context and confirmation timeframes are currently fail-closed:
  - `context_timeframe_not_executable:<node_id>`
  - `confirmation_timeframe_not_executable:<node_id>`
- These roles are preserved in the draft but cannot become executable metadata
  until exact runtime semantics exist.
- Formula, operator, threshold, direction, timeframe roles, references, AST
  position, and provenance are checked after compilation by a deterministic
  equivalence validator.
- Exact deterministic primitives no longer become AI-interpreted capabilities.

### Activation and resume

- `StrategyService.activate()` is the sole customer activation implementation.
- Dashboard publish, experiment promotion, onboarding, and resume paths route
  through the shared activation gates.
- `/publish` no longer directly mutates a strategy to active and no longer
  performs implicit approval.
- Resume reruns approval hash, preview, disclaimer, entitlement, Sharia snapshot,
  notification channel, dynamic capability, and plan gates.
- Blocked monitors remain blocked instead of being displayed as active while
  workers would refuse them.
- Harmless assumed interpretation rows can be accepted only as part of the exact
  visible draft approval event. Each acceptance is audited.
- Ambiguous, contradictory, or unsupported interpretation remains blocking.

### Durable one-time Scanner

- Added immutable `OnDemandScanRun` and per-market result records.
- Added migration:
  `alembic/versions/b7f42a8d9c11_add_on_demand_scan_runs.py`
- The migration chain currently has one head: `b7f42a8d9c11`.
- Runs persist user, draft/version/hash, idempotency key, quota reservation,
  Sharia snapshot, provider, candle manifest/hash, typed results, errors, and
  timestamps.
- Retrying the same idempotency key returns the same run and result.
- An intentional rerun receives a new run ID and consumes quota.
- Quota is reserved atomically before provider work.
- Quota is released only when no market was evaluable due to policy,
  stale/incomplete data, provider failure, or runtime failure.
- Technical non-match is a completed scan and consumes quota.
- Scanner returns safe typed categories:
  - confirmed
  - forming
  - technical non-match
  - policy exclusion
  - market unavailable
  - stale/incomplete data
  - provider failure
- Raw provider exceptions are not returned to customers.
- Scanner and Monitor share candle freshness, completeness, ordering, and
  snapshot-hash validation.
- A frozen-candle parity regression compares outcome, direction, condition state,
  required values, and actual values across Scanner and Monitor.

### AI orchestration

- Successful mutation responses are composed deterministically from execution
  evidence.
- A free-text turn makes at most one structured model request.
- Model retries inside the same turn were removed.
- Redis stores shared circuit-breaker state.
- Redis failure after a successful provider response cannot discard the result or
  trigger another paid model call.
- Capability shortlists are filtered against configured runtime adapter
  requirements.
- Runtime preflight checks exchange, quote, symbol, and required timeframes and
  caches the result in Redis.
- Runtime uncertainty is represented explicitly instead of being called ready.
- Customer-visible Setup Chat copy was moved toward ASCII punctuation to prevent
  mojibake.

## Important Current Files

Core implementation:

- `src/ai_market_monitor/engine/strategy_draft_v2.py`
- `src/ai_market_monitor/engine/strategy_compiler_v2.py`
- `src/ai_market_monitor/engine/evaluator.py`
- `src/ai_market_monitor/schemas/strategy.py`
- `src/ai_market_monitor/schemas/strategy_draft_v2.py`
- `src/ai_market_monitor/services/setup_chat_agent.py`
- `src/ai_market_monitor/services/setup_chat_launch.py`
- `src/ai_market_monitor/services/ai_setup_chat.py`
- `src/ai_market_monitor/services/strategy.py`
- `src/ai_market_monitor/services/on_demand_scans.py`
- `src/ai_market_monitor/services/scanner.py`
- `src/ai_market_monitor/services/market_preview.py`
- `src/ai_market_monitor/services/monitor_operations.py`
- `src/ai_market_monitor/services/verified_strategy.py`
- `src/ai_market_monitor/api/routers/dashboard_api.py`
- `src/ai_market_monitor/api/routers/onboarding.py`
- `src/ai_market_monitor/cockpit_api.py`
- `src/ai_market_monitor/static/ai-setup-chat.js`

Models and migrations:

- `src/ai_market_monitor/db/models/monitoring.py`
- `src/ai_market_monitor/db/models/dashboard_extensions.py`
- `alembic/versions/a2e8f7c31d90_add_setup_chat_turns.py`
- `alembic/versions/b7f42a8d9c11_add_on_demand_scan_runs.py`

Primary regressions:

- `tests/unit/test_strategy_draft_v2.py`
- `tests/unit/test_strategy_engine.py`
- `tests/unit/test_on_demand_scans.py`
- `tests/unit/test_invariant_launch_v2_contracts.py`
- `tests/unit/test_invariant_setup_closure.py`
- `tests/integration/test_setup_chat_agent_turns.py`
- `tests/integration/test_setup_chat_launch_v2.py`
- `tests/integration/test_dashboard_api.py`
- `tests/integration/test_onboarding_flow.py`
- `tests/integration/test_scanner_pipeline.py`
- `tests/browser/test_dashboard_e2e.py`

Evaluator contracts:

- `src/hm_chatbot_eval/launch_core.py`
- `tests/evaluator/contracts/`
- `scripts/export_setup_chat_eval_contracts.py`

## Last Verified Results

The latest completed checks were:

- Ruff: passed across `src` and `tests` before the final small browser/copy edits.
- MyPy: passed across 254 production source files before the final small edits.
- Alembic: one head.
- Full Alembic upgrade and migration round-trip tests: passed.
- All non-browser tests: 4,199 passed.
- Focused Setup Chat, Scanner, Monitor, activation, onboarding, and migration
  regressions: passed after the latest fixes.

Do not report the full suite green yet. Ruff and MyPy should be rerun because a few
files changed after those checks.

## Exact Current Browser Blocker

The browser suite contains 36 tests.

One full browser pass reached 11 passing tests before the first Setup Chat failure.
The fixture provider initially omitted BTC, so runtime preflight correctly blocked
the canonical BTC scenario. BTC and ETH were then added to the test-only
`FixtureMarketDataProvider`.

The next isolated run returned HTTP 422 from:

```text
POST /api/v1/dashboard/setup-chat/sessions/{session_id}/messages
```

for:

```text
Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%
```

The failure body was not yet captured. The browser assertion was updated to include
the response body on failure. The immediate next action is to rerun:

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/browser/test_dashboard_e2e.py::test_strategy_prompt_to_coverage_preview_opens_board
```

Read the returned structured error before editing. Do not bypass runtime preflight
or semantic validation merely to make the browser test pass.

The fixture server log is:

```text
test-results/browser/browser-e2e-server.log
```

## Recommended Continuation Order

1. Read `Notion/`, `brand guide.md`, and the current dirty diff.
2. Reproduce the isolated browser HTTP 422 and inspect its structured body.
3. Fix the root cause without weakening provider, semantic, Sharia, or activation
   gates.
4. Run all 36 browser tests.
5. Run Ruff and MyPy again.
6. Run the complete test suite or, at minimum, preserve the already green 4,199
   non-browser result plus a fully green browser suite.
7. Run:

```powershell
.\.venv\Scripts\python.exe scripts/export_setup_chat_eval_contracts.py --check
.\.venv\Scripts\python.exe -m alembic heads
git diff --check
```

8. Review all remaining non-ASCII Setup Chat prompts and customer copy for actual
   encoding problems. Preserve real Arabic text and multilingual approval support.
9. Report production-only dependencies honestly.

## Production Dependencies Still Unverified

- Real Redis circuit-breaker behavior across multiple workers.
- Live Binance and Bybit symbol/timeframe preflight.
- Real provider outage and stale-candle behavior in staging.
- PostgreSQL migration on a production-like backup.
- Worker restart and duplicate-processing soak.
- Real latency p50/p95 under production network conditions.
- Live notification delivery.

Local fixture or mock results are not proof of these items.

## Working-Tree Safety

The repository is intentionally dirty and contains extensive HilalMarkets work from
the ongoing refactor. Do not reset, restore, or overwrite unrelated changes.
Generated `__pycache__` files are tracked in the repository and may appear modified
after tests; do not confuse them with product source changes.

Use existing models and services. Avoid creating a second Scanner, Monitor,
methodology, Passport, approval, notification, or strategy-state system.

## Definition of Done

Do not mark this work complete while any known path can:

- duplicate neutral direction into long and short results;
- discard or silently demote a timeframe role;
- substitute an unrelated capability;
- bypass explicit approval or activation gates;
- display a blocked Monitor as active;
- reuse quota for an intentional new Scanner run;
- evaluate stale or incomplete candles as current;
- lose the authoritative draft after provider or transport failure;
- make more than one model call for one free-text turn;
- expose a raw exception to a customer.

The goal is a small, deterministic, evidence-led HilalMarkets strategy builder that
is calm to use, exact in semantics, fail-closed in uncertainty, and consistent with
the Notion product model and HilalMarkets brand.
