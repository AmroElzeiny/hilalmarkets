# Authenticated AI Setup Chat Evaluator

## Scope

`hm_chatbot_eval` evaluates the authenticated Watchlist Builder at
`/dashboard/strategies/new`, its durable Setup Chat sessions, validated strategy
compilation, approval readiness, and Strategy Canvas rendering. It does not evaluate or
share tools with the public landing-page Support assistant.

The integrated package lives at `src/hm_chatbot_eval`. The supplied
`HilalMarkets_Chatbot_AI_Evaluator/` directory remains the source bundle; the application
package, project dependency set, CI, and stable contracts are owned by this repository.

## Audit Matrix

| Area | Before integration | Integrated behavior |
| --- | --- | --- |
| Authenticated chat sessions | Complete | Reused without a parallel session model |
| Message persistence/idempotency | Complete | Reused by the HilalMarkets backend and UI adapters |
| Strategy compiler and Pydantic validation | Complete | Reused as the only source of evaluator strategy data |
| Approval and immutable versions | Complete | Observed by the evaluator; never exposed as evaluator actions |
| Canvas | Partial for black-box comparison | Nested groups, nodes, and edges now carry stable contract IDs |
| Generic HTTP/Playwright adapters | Complete in source bundle | Retained as explicit black-box fallbacks |
| Repository-aware backend adapter | Missing | Uses the real create-session and message routes |
| Stable production schema exports | Missing | Generated and checked from production Pydantic models |
| Test-only LLM faults | Missing | Added at the actual Responses/interviewer/interpreter boundary |
| Deterministic target variants | Missing | Added as test-only, server-allowlisted model/prompt labels |
| Deterministic CI evaluator tests | Missing | Added to the release gate |

## Files Changed

Project configuration and CI:

- `.env.example`
- `.env.production.example`
- `.github/workflows/release-gate.yml`
- `.gitignore`
- `pyproject.toml`

Production Setup Chat integration:

- `src/ai_market_monitor/api/routers/dashboard_api.py`
- `src/ai_market_monitor/core/config.py`
- `src/ai_market_monitor/core/startup.py`
- `src/ai_market_monitor/schemas/ai_setup_chat.py`
- `src/ai_market_monitor/schemas/setup_chat_evaluation.py`
- `src/ai_market_monitor/services/agent_control.py`
- `src/ai_market_monitor/services/ai_model_routing.py`
- `src/ai_market_monitor/services/ai_setup_chat.py`
- `src/ai_market_monitor/services/ai_setup_evaluator_control.py`
- `src/ai_market_monitor/services/openai_interpreter.py`
- `src/ai_market_monitor/services/setup_chat_evaluation.py`

Authenticated UI and target separation:

- `src/ai_market_monitor/static/ai-setup-chat.js`
- `src/ai_market_monitor/static/dashboard.js`
- `src/ai_market_monitor/templates/hilal/dashboard/builder.html`
- `src/ai_market_monitor/templates/hilal/partials/public_chat.html`

Evaluator package, contracts, and tests:

- `src/hm_chatbot_eval/__init__.py`
- `src/hm_chatbot_eval/__main__.py`
- `src/hm_chatbot_eval/batch.py`
- `src/hm_chatbot_eval/cache.py`
- `src/hm_chatbot_eval/cli.py`
- `src/hm_chatbot_eval/compare.py`
- `src/hm_chatbot_eval/config.py`
- `src/hm_chatbot_eval/doctor.py`
- `src/hm_chatbot_eval/evaluate.py`
- `src/hm_chatbot_eval/models.py`
- `src/hm_chatbot_eval/openai_client.py`
- `src/hm_chatbot_eval/report.py`
- `src/hm_chatbot_eval/runner.py`
- `src/hm_chatbot_eval/scenarios.py`
- `src/hm_chatbot_eval/test_ai.py`
- `src/hm_chatbot_eval/topics.py`
- `src/hm_chatbot_eval/util.py`
- `src/hm_chatbot_eval/targets/__init__.py`
- `src/hm_chatbot_eval/targets/auth.py`
- `src/hm_chatbot_eval/targets/backend.py`
- `src/hm_chatbot_eval/targets/base.py`
- `src/hm_chatbot_eval/targets/ui.py`
- `scripts/export_setup_chat_eval_contracts.py`
- `tests/evaluator/contracts/field_map.json`
- `tests/evaluator/contracts/setup_chat_evaluation_contract.schema.json`
- `tests/evaluator/contracts/strategy_definition.schema.json`
- `tests/evaluator/test_auth.py`
- `tests/evaluator/test_contract_export.py`
- `tests/evaluator/test_control.py`
- `tests/evaluator/test_core.py`
- `tests/evaluator/test_integration.py`
- `tests/evaluator/test_ui_boundary.py`

Documentation:

- `README.md`
- `docs/AI_SETUP_CHAT_EVALUATOR.md`
- `docs/AI_SETUP_CHAT_IMPLEMENTATION_REPORT.md`
- `docs/ARCHITECTURE.md`

The full mypy/Ruff gate also required two behavior-neutral cleanups:
`PublicChatService.email_delivery_state()` now declares its existing literal return
states, and an extra blank line was removed from
`tests/unit/test_waitlist_google_apps_script.py`.

## Production Binding

The default `HilalMarketsBackendTarget` performs this real flow:

1. Authenticate a dedicated test user through the existing sign-in flow, unless an
   already-authenticated application test client is injected.
2. Create a session with `POST /api/v1/dashboard/setup-chat/sessions`.
3. Send idempotent messages to
   `POST /api/v1/dashboard/setup-chat/sessions/{chat_id}/messages`.
4. Capture the persisted assistant message and the server-generated
   `evaluation_contract`.
5. Validate the contract against the exported schema and canonical field map.

`evaluation_contract` is projected only after `StrategyDefinition.model_validate()`
succeeds. It contains the same canonical strategy hash used by approval. For an approved
chat it also includes the persisted immutable strategy-version ID, version number, and
schema hash.

The Playwright adapter requires exactly one
`data-evaluator-target="authenticated-ai-setup-chat"` marker and rejects any page carrying
the public Support marker. It captures the underlying message response, compares the
rendered preview hash, and requires the Canvas node/group IDs to equal the backend
contract.

## Stable Contracts

The checked-in artifacts are:

- `tests/evaluator/contracts/strategy_definition.schema.json`: exact JSON Schema exported
  from the executable `StrategyDefinition`.
- `tests/evaluator/contracts/setup_chat_evaluation_contract.schema.json`: the validated
  response, approval, and Canvas comparison contract.
- `tests/evaluator/contracts/field_map.json`: canonical paths and matching semantics for
  universe, symbols, exclusions, direction, timeframes, operators, thresholds, nested
  groups, filters, alerts, assumptions, confidence, unsupported/provider-required
  capabilities, approval state, version/hash, and Canvas nodes/groups/edges.

Regenerate after an intentional schema change:

```powershell
python scripts/export_setup_chat_eval_contracts.py
```

CI checks drift without rewriting files:

```powershell
python scripts/export_setup_chat_eval_contracts.py --check
```

No evaluator schema, field map, or adapter can make invalid strategy prose executable.
Production schema validation, capability authority, linting, ownership, entitlement,
approval, activation, and immutable-version checks remain unchanged.

## Fault Boundary

The following one-shot faults are supported:

- `timeout_once`
- `429_once`
- `empty_once`
- `invalid_json_once`
- `partial_json_once`
- `stream_disconnect_once`

They are selected through evaluator configuration and transported as the
`X-HM-Eval-Fault` test header. The header is not authority: the server accepts it only
when `APP_ENV=test`, `AI_SETUP_EVALUATOR_ENABLED=true`, and
`AI_SETUP_EVALUATOR_FAULTS_ENABLED=true`. Each fault is consumed once at the real LLM
client boundary. Staging and production startup reject either evaluator flag or any
target-version map, and the request guard rejects controls outside the test environment.

The evaluator does not intercept provider routes, fabricate successful compilation, or
replace the deterministic engine.

## Target Versions

Identical golden scenarios can compare server-owned model and prompt variants. The
application allowlist is configured only in a test process:

```env
AI_SETUP_EVALUATOR_TARGET_VERSIONS={"golden-v2":{"model":"configured-model","reasoning_effort":"high","prompt_version":"context_guard_v1"}}
TARGET_VARIANTS_JSON=[{"name":"current"},{"name":"golden-v2","target_version":"golden-v2"}]
```

The browser or evaluator sends only the allowlisted label. Unknown labels fail closed.
Customers cannot submit model names, reasoning settings, or prompt text.

## Commands

Install the root project and Chromium:

```powershell
pip install -e ".[dev]"
python -m playwright install chromium
```

Inspect configuration:

```powershell
python -m hm_chatbot_eval doctor
```

Preview the cost-controlled plan without calling OpenAI or the application:

```powershell
python -m hm_chatbot_eval plan --mode budget --target both
```

Run the recommended routine quality gate:

```powershell
python -m hm_chatbot_eval run --mode budget --target both --judge-mode online
```

The budget profile is the normal pre-release live check:

- one backend scenario for each of the 69 security, authority, mapping, capability,
  conversation, language, resilience, Canvas, integration, quality and performance topics;
- 12 selected UI/Canvas scenarios repeated through Playwright;
- one extra repeat for deterministic reproducibility;
- four turns for ordinary topics, six for state/correction topics and eight for genuine
  long-context topics;
- every completed case receives the semantic AI judge in `online` mode;
- additional target variants run only for `model_version_drift`;
- execution is serialized and both evaluator and target-chatbot usage count toward
  `EVAL_BUDGET_PROFILE_MAX_USD`; the target amount is the request-correlated sum from the
  application's AI usage ledger, including coordinator and compiler calls reached through tools;
- current defaults cap the measured run at `$2.50`, leaving headroom below `$3` for the one
  in-flight provider response that can complete as the limit is crossed.

The current one-variant plan contains 82 target conversations and at most 366 adaptive turns.
The usual spend is expected to remain below the cap; actual cost depends on conversation length,
tool-loop steps, cache hits and model routing. A limit stop is a failed/incomplete evaluation, not
a passing quality result.

Run live smoke coverage against both the authenticated API and UI:

```powershell
python -m hm_chatbot_eval run --mode smoke --target both
```

Run the full deterministic corpus with deferred judging:

```powershell
python -m hm_chatbot_eval run --mode full --target both --tests-per-topic 24 --judge-mode deferred
```

Live runs require a reachable application configured as a test environment, a dedicated
authenticated test user, Chromium, an OpenAI key for the target and evaluator, current
model pricing for cost gates, and at least two target variants for drift comparison. The
evaluator never guesses prices.

Interactive runs can sign in once with `TARGET_BACKEND_EMAIL` and
`TARGET_BACKEND_PASSWORD`; that one process-local cookie is reused by API and Playwright
cases so evaluator volume does not weaken or exhaust the application rate limiter.
Scheduled runs may instead provision a short-lived application session and supply it as
`TARGET_SESSION_COOKIE`. It is treated as a secret and is never included in evidence.

## Reports

Each run writes to `chatbot_eval_runs/<run-id>/`:

- `report.html`
- `report.md`
- `summary.json`
- `cases.jsonl`
- `failures.csv`
- `evidence/`

These paths are ignored by Git. Reports redact configured credential-like keys, but
operators must still use dedicated test accounts and review evidence before sharing it.

## CI and Manual Runs

The release gate runs contract drift checks and `tests/evaluator` with deterministic fake
LLM transports and real application services/test factories. CI does not call OpenAI,
Binance, or a deployed website.

Real API/UI corpus runs remain manual or scheduled because they require credentials,
incur model cost, depend on live services, and produce screenshots. A local deterministic
pass is not evidence that an external model, browser deployment, or provider was tested.

## Verification Record

Local verification on 2026-07-23:

- `ruff check .`: passed.
- `mypy src/ai_market_monitor src/hm_chatbot_eval`: 210 source files passed.
- `pytest --ignore=tests/browser`: 2,124 passed; two existing lxml deprecation
  warnings.
- `pytest tests/evaluator`: 27 passed.
- Focused authenticated builder Playwright slice: 5 passed.
- JavaScript syntax: 18 files passed.
- Jinja loading: 63 templates passed.
- Dependency integrity: `pip check` passed and 34 direct dependencies are exact-pinned.
- Contract drift: `scripts/export_setup_chat_eval_contracts.py --check` passed.

`python -m hm_chatbot_eval doctor` passed the target identity, case count, exact
strategy schema, evaluator response schema, canonical field map, local backend health,
current evaluator/target prices, and the `$2.50` budget-profile cap checks. It correctly
failed OpenAI authentication with HTTP 401 and reported that only one target variant is
configured, so a live model-drift comparison is not yet possible.

The exact requested smoke command was executed against both the authenticated backend and
Playwright target. Its final report is:

`chatbot_eval_runs/20260723T054409Z/report.html`

The run failed closed with 24 errored cases and zero pending judges. Every case recorded the
same OpenAI HTTP 401 before the adaptive trader's first message; there were no remaining
login-rate-limit or wrong-page-marker failures after session reuse was added. This is not a
model-quality result. Replace the evaluator OpenAI credential and configure at least two
allowlisted target variants before treating a real corpus result as release evidence.

The later budget-profile verification added official configured prices, request-correlated
target usage, mixed-model cost aggregation and a fail-closed `$2.50` run limit. A no-spend
plan contains 69 backend topics, 12 UI boundary repetitions, one reproducibility repeat,
82 target conversations and at most 366 adaptive turns. A live budget run was attempted
and stopped before its first quality case because authenticated target access returned
HTTP 401. It spent `$0.00`, marked the run `INCOMPLETE` rather than assigning a chatbot
quality score, and wrote `chatbot_eval_runs/20260723T065318Z/report.html`. Doctor separately
reports HTTP 401 for the evaluator OpenAI credential. Both credentials must be corrected
before the paid quality run is repeated.

The full 24-tests-per-topic run was not started because the same invalid credential would
produce 1,440 or more non-evaluations and unnecessary provider traffic.

## Adding Coverage Safely

1. Add a scenario/topic or deterministic assertion inside `src/hm_chatbot_eval`.
2. Map new structured facts to an existing production field, or extend the production
   response model first and regenerate both schemas.
3. Add a server-owned target-version prompt appendix only when an intentional comparison
   needs it.
4. Add tests proving malformed arguments, unsupported capabilities, unapproved drafts,
   and public Support pages still fail closed.
5. Never add evaluator tools that approve, activate, execute arbitrary code, bypass
   ownership, or replace deterministic strategy evaluation.
