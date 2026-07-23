# HilalMarkets AI Setup Chat Evaluator

A production-oriented AI-vs-AI test system for the **authenticated HilalMarkets AI Setup Chat and Strategy Canvas**, not the public landing-page support agent.

The test AI acts as a realistic trader, changes its next message based on the chatbot's answer, and then grades the complete interaction against deterministic checks and a strict evidence-only judge. Runs can use the backend, Playwright UI, or both. Every failure keeps reproducible proof: turn IDs, sanitized requests/responses, status codes, latency, schema validation, structured hashes, screenshots, and a reproduction command.

## Coverage

The catalog contains 60 topics. Full runs enforce 20–30 cases per topic and default to 24 (1,440 scenarios before target variants). It covers long-context retention, model drift, schema-valid semantic errors, trader ambiguity, limited multilingual coverage, injection, correction cycles, timeouts, cost, invalid/partial responses, deterministic authority, Strategy Canvas fidelity, approval safety, provider requirements, privacy, Sharia boundary protection, and more.

## Install

```bash
cd HilalMarkets_Chatbot_AI_Evaluator
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env
```

Configure `.env`, especially the target chatbot endpoint/UI and the real compiled-strategy schema. Then:

```bash
hm-chatbot-eval doctor
hm-chatbot-eval list-topics
hm-chatbot-eval plan --mode smoke
hm-chatbot-eval run --mode smoke --target backend
hm-chatbot-eval run --mode full --target both --tests-per-topic 24 --budget-usd 25
```

Outputs are written under `chatbot_eval_runs/<run_id>/`:

- `report.html` — self-contained executive and engineering report.
- `report.md` — compact actionable report.
- `summary.json` — machine-readable metrics and release gate.
- `cases.jsonl` — one auditable result per scenario.
- `failures.csv` — prioritized failures and reproduction commands.
- `evidence/` — sanitized raw exchanges and UI screenshots.

## Cost controls

1. Dynamic conversation turns must run online because each trader response depends on the chatbot answer.
2. Use `TEST_AI_SERVICE_TIER=flex` for lower-cost non-urgent generation where supported.
3. Use `--judge-mode deferred` to write judge requests as Batch API JSONL; submit with `batch-submit` and collect later with `batch-collect`; collection merges judgments into `cases.jsonl` and regenerates every report. OpenAI documents Batch as asynchronous with a 24-hour completion window and discounted pricing.
4. Stable test instructions use a fixed prompt-cache key. SQLite caches identical test-AI calls across reruns.
5. `--budget-usd` is a hard stop. Set current model prices in `.env`; the evaluator never invents prices.
6. `smoke` uses one case for selected critical topics, `standard` uses five, and `full` enforces 20–30.
7. Deterministic checks run before the judge. Deferred judging avoids blocking conversations and is the cheapest complete mode.

## Target adapter contract

The package contains complete executable adapters and reporting code; no test implementation stubs are left. The generic backend adapter works with any JSON HTTP endpoint after environment mapping. Because route names, DSL paths and selectors are repository-owned, the Codex integration prompt in `CODEX_INTEGRATION_PROMPT.md` binds it to the exact HilalMarkets services, DSL schema, session model, UI selectors, and test-only fault injection. The adapter refuses to proceed when the UI marker does not identify the AI Setup Chat or when forbidden support-agent markers are present.

## Drift workflow

Run the identical deterministic scenario IDs against two target variants or two deployments, then:

```bash
hm-chatbot-eval compare RUN_A RUN_B
```

The comparison reports pass flips, semantic-score changes, structured-output hash changes, latency/cost changes, and evidence-linked regressions.

## Release gate

A run passes only when every critical topic meets its own threshold and global gates pass. Defaults include zero approval bypass, zero unknown-capability execution, zero cross-conversation leakage, zero unproven Sharia status assignment, schema validity >= 99.5%, semantic accuracy >= 97%, correction adherence >= 98%, recovery >= 95%, and p95 latency/cost limits configured for the environment.
