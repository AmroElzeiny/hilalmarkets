# MISSION — Active AI features move to OpenCode Go Muse (high), and Hilal sees the page and the Passports

Owner: Claude Code (architect, final judge). Written 2026-09-16.
Execution owner: OpenCode Go supervisor. Tier: **Deep** (secrets, privacy of user data sent
to a model, Shariah-authority boundary, a shared provider client used by many features).
Branch `main`, HEAD `9a19d488`.

The working tree carries **unrelated uncommitted work** (money/billing/affiliate files,
`.hm-orchestrator/*`, `.opencode/agents/*`, `CLAUDE.md`, `tools/hm-orchestrator/*`, two new
`tests/unit/test_invariant_*` files). Never modify, revert, reformat or stage any of it.

## 0. What the owner asked (their words, cleaned)

1. "Switch any place in the system that is using OpenAI to OpenCode `muse-spark-1.3-contributor`
   on high reasoning effort. Only change **active** features. Do not change anything in the
   stale prompt features that build strategies from prompts — that is not on the website.
   Active examples: chat support on the landing pages, the Hilal agent in the dashboard, the
   bot that checks halal status every couple of days, the bot in the System Brain, the bot
   that scrapes coins' websites, the bot that scrapes for coins in the Hilal strategy."
2. "Give the Hilal agent on the dashboard access to page data — which cards the user added,
   which inputs are not filled, etc. — so it can help deeply."
3. "Give the Hilal agent access to Passports and the reasons why a coin is halal and why it is
   listed, the definitions of the methodologies and where they come from."
4. "Test only on major milestones, not on every minor step."

## 1. Facts Claude already established (do not re-derive)

**Provider behaviour — measured by Claude on 2026-09-16 with 4 real calls:**

- Base URL `https://opencode.ai/zen/go/v1`, endpoint `POST /responses`, header
  `Authorization: Bearer <key>` **and** `x-opencode-session: <id>` (without it: HTTP 400
  `MissingSessionID`). Key for local use: `%USERPROFILE%\.local\share\opencode\auth.json`,
  entry `opencode-go` (field `key`). Never print it.
- Model id sent in the body is the bare `muse-spark-1.3-contributor` (no `opencode-go/` prefix).
- Works, HTTP 200: `store:false`, `reasoning:{effort:"high"}`,
  `text.format` `json_schema` `strict:true`, function `tools` + `tool_choice:"auto"`, and a
  follow-up turn that sends back the prior `output` items plus `function_call_output`.
- `service_tier:"flex"` is accepted and **ignored** (response `service_tier` is `null`).
- Response has **no `output_text`** field; text is in `output[type=message].content[].text`.
  Output items include a `reasoning` item. A tool turn returned **both** a `message` and a
  `function_call` item in the same response.
- `usage` has the OpenAI shape (`input_tokens`, `output_tokens`,
  `input_tokens_details.cached_tokens`, `output_tokens_details.reasoning_tokens`). The body
  also has a top-level `cost` field.
- **High effort spends most output tokens on reasoning**: "what is 2+2" used 368 reasoning
  tokens of 383. Every `max_output_tokens` budget sized for `low` effort will now truncate.
- Only Muse speaks `/responses` on OpenCode Go (GLM/MiMo/Qwen refuse it). So the runtime
  target is Muse only; no fallback to another OpenCode model.

**Where the app calls AI today:** every call site builds its own URL from
`OPENAI_BASE_URL` and its own `Authorization` header from `OPENAI_API_KEY`. Found by grep:
`ai_explanations.py`, `services/agent_control.py` (`OpenAIAgentResponsesClient` — shared by
public chat, Hilal, System Brain assistant/agent), `services/openai_structured_call.py`,
`services/sharia_research.py` (two paths: `ShariaAIResearchClient._post` and the
`sharia_factual_dossier` call near line 1004), `services/sharia_source_monitoring.py`,
`services/sharia_source_ai_discovery.py`, `services/system_brain_agent.py`,
`services/system_brain_assistant.py`, `services/public_support_ai.py`/`public_chat.py`,
`services/hilal_chat_agent.py`, plus the stale family: `ai_setup_chat.py`,
`openai_interpreter.py`, `strategy_patch_extractor.py`, `capability_extension_ai.py`,
`capability_registry.py` (embeddings), `hybrid_capability_resolution.py`,
`ai_semantic_fallback.py`, `setup_observability.py`, `setup_chat_launch.py`,
`telegram/service.py` (strategy interpretation), `api/routers/onboarding.py`.
"Is AI configured" checks read `openai_api_key` directly in: `core/startup.py`,
`api/routers/public.py:931`, `services/agent_policy.py:188`, `system_brain_agent.py:405`,
`system_brain_assistant.py:136`, `sharia_research.py:394`, `sharia_source_ai_discovery.py`,
`api/routers/dashboard_api.py:3230/3281`, `services/feature_control.py:323`.

**Settings today** (all four env files agree): `PUBLIC_CHAT_AI_MODEL`, `HILAL_CHAT_AI_MODEL`,
`SYSTEM_BRAIN_AI_MODEL`, `SHARIA_AI_MODEL`, `SHARIA_SOURCE_AI_MODEL` = `gpt-5.6-luna`; their
efforts `low` (`SHARIA_SOURCE_AI_REASONING_EFFORT=none`). `OPENAI_MODEL=gpt-5.6-luna`.
`SHARIA_SOURCE_AI_DISCOVERY_ENABLED=false` in all four files. Pricing lives in
`OPENAI_MODEL_PRICING_USD_PER_MILLION` (JSON value) and is used by `estimate_usage_cost`,
`structured_call` (refuses a model with no price when a cost limit is set) and per-turn caps.
`hilal_chat_agent.model_for_turn` and `public_support_ai` fall back to `openai_model` when
their own model setting is empty.

**Hilal today:** `static/hm-page-context.js` is the one owner of "what is on screen". Pages
`publish(name, describe)`; `ACCEPTED` is a closed set holding only `"board"`, published by
`static/hm-monitor-test.js:1904`. Server shape: `schemas/hilal_chat.py` (`HilalChatView`,
`HilalChatBoard`, `HilalChatBoardCard` with `needs`). Evidence is gathered read-only in
`services/hilal_chat_knowledge.py` (`HilalChatKnowledge.gather`, `Evidence.to_payload`,
`Evidence.ids`); rules and prompt in `services/hilal_chat_agent.py`. Methodologies are
passed as name/version/description/governing body only. Passports are **not** passed.
Passport reads have one owner: `services/sharia_passports.py` (`ShariaPassportReadService`:
`current`, `historical`, `quick_view`). Our own standard: `services/hilal_methodology.py`
(+ `hilal_methodology_admissions.json`), routes `regulator_floor` / `automated_screen`, and
it must stay excluded from the aggregate view and from `default_methodology()`.

**Privacy fact to carry into the report (not a blocker — the owner chose this model):**
`.hm-orchestrator/models/OCG_CATALOG_2026-09-09.md` lists Muse Spark 1.3 Contributor as
"training enabled; not ZDR; region limited".

## 2. Runtime / source-of-truth authority

- `CLAUDE.md`, `AGENTS.md`, `.agents/commands.json`, `brand guide.md` (for any wording).
- `src/ai_market_monitor/core/config.py`, `core/startup.py`, the four env files.
- The call sites and owners listed in section 1.
- `services/provider_runtime.py` (`provider_request`, `provider_call`) — keep using it.
- Memory notes worth reading: `a-setting-lives-in-four-files`, `one-owner-for-coin-logos`
  style "one owner" rule, `hilal-methodology-is-published`, `verified-meant-somebody-typed-it`.

## 3. Requirements coverage

| ID | Requirement | Acceptance evidence |
|---|---|---|
| R1 | Every **active** AI call path is classified with evidence; every stale strategy-from-prompt path is classified with evidence. | `WP1_CALL_SITE_MAP.md`: one row per call site: module, function, feature, ACTIVE/STALE, evidence (route mounted + linked from a live page, beat schedule entry, or enabled flag in `.env.production`). |
| R2 | Landing-page support chat uses `muse-spark-1.3-contributor`, effort `high`, via OpenCode Go. | Invariant test + one real call through the production code path. |
| R3 | Dashboard Hilal agent uses Muse/high via OpenCode Go. | Invariant test + one real turn. |
| R4 | Shariah research ("checks halal status" — every AI path in `sharia_research.py` and `sharia_source_monitoring.py`) uses Muse/high via OpenCode Go. | Invariant test + one real dossier call on a fixture evidence package. |
| R5 | System Brain assistant **and** System Brain tool-using agent use Muse/high via OpenCode Go, including the multi-step tool loop. | Invariant test + one real tool-loop turn (at least one tool call and a final answer). |
| R6 | Coin-website source bot (`sharia_source_ai_discovery.py`) uses Muse/high via OpenCode Go. Its on/off flag is **not** changed. | Invariant test; real call only if cheap (one). |
| R7 | "Bot that scrapes for coins in the Hilal strategy": find it (Hilal methodology automated screen / admissions pipeline / coin evidence crawler). If it calls a model, route it to Muse/high. If it calls no model, state that with evidence. | Row in WP1 map + test or evidence note. |
| R8 | Stale strategy-from-prompt features are **unchanged in behaviour**: same model (`OPENAI_MODEL`), same OpenAI endpoint and key. Their own files are not edited. | Invariant test asserting each stale path still resolves to OpenAI with its old model; `git diff` shows no edits in stale-only files. |
| R9 | One owner decides provider endpoint, key, headers for a model; no active call site builds its own URL or `Authorization` header. | Source-scan invariant test over the active modules. |
| R10 | New settings exist in all four env files (examples + both real files), real key copied safely, no existing value changed. | Key-count proof before/after for `.env` and `.env.production`; `test_invariant_phase6_launch_audit.py` green. |
| R11 | Startup validation and every "is AI configured" check use the owner: an active feature on Muse needs the OpenCode Go key, not the OpenAI key. Placeholder keys refused. | Parametrised tests: each active feature × key present/missing/placeholder. |
| R12 | Output budgets, timeouts and per-turn cost caps are resized for high reasoning from **measured** real calls; a truncated answer (`status:"incomplete"`, reason `max_output_tokens`) is reported as its own clear error, not as "invalid JSON". | Measurement table in report (tokens, reasoning tokens, latency per feature); unit test for the incomplete case. |
| R13 | Muse has a pricing entry wherever pricing is read, from a documented source. | Source cited; cost estimate test. |
| R14 | Secrets: the new key is a credential field, redacted in logs/labels/security review/reliability metrics, never printed. | Tests that the new key is redacted everywhere the OpenAI key is. |
| R15 | Hilal sees page data on **every live dashboard page** that shows the chat, published by each page in its own words through `hm-page-context.js`. | Per-page list in report; browser test file that opens each page and asserts `snapshot()` carries that page's description and the server accepts it. |
| R16 | On the monitor canvas Hilal sees every card added and, per card, every input: its label, filled or empty, and the value as the person typed it. | Schema + JS + test: a card with one filled and one empty input reaches the model payload with both. |
| R17 | Hilal sees the signed-in person's own saved records it needs to help: their monitors (name, running/paused/draft, what each still needs, last check), their plan, their connected alert channels. Only their own rows. | Test that user A's turn never contains user B's rows. |
| R18 | Hilal sees the Passport of every coin in the question or on screen, per methodology: recorded status words, why (summary, qualifications, exclusion reasons), when reviewed, next check, evidence source names and dates, and for our own standard its admission route (regulator floor vs automated reading) with its notice. Read through `ShariaPassportReadService`, not a second query. | Test per status × per methodology kind; payload contains passport fields. |
| R19 | Hilal sees methodology definitions: name, version, governing body, what it is, where it comes from (the authority and its published source), what it screens for, in force from; our own standard's published contract. | Test that each active methodology appears with its origin fields. |
| R20 | Shariah authority boundary kept: the model only repeats recorded statuses per named methodology; our automated standard is never merged into an aggregate "winner" and never presented as the default; prompt rules updated for the new records. | Adversarial tests + reviewer sign-off. |
| R21 | Payload stays bounded: caps on page data and records truncate, they never raise (a diagnostic never becomes the failure); JS caps and schema caps agree. | Test over-cap input is truncated and the turn succeeds; test JS caps == schema caps. |
| R22 | Tests run only at milestones (end of WP2, WP3, WP4+WP5 together, and the final run). | Report lists exactly when each suite ran. |
| R23 | Anything else broken that is found on the way is fixed and listed. | Report section "found and fixed". |

## 4. Non-negotiable invariants

- Never change a stale strategy-from-prompt feature's model, endpoint, key or files (R8).
  Editing the **shared** clients (`OpenAIAgentResponsesClient`, `structured_call`) is allowed
  only if stale callers keep byte-identical requests to OpenAI; prove it with a test.
- No silent fallback: an active feature on Muse with no OpenCode Go key is "not configured",
  never quietly sent to OpenAI. Remove the `or settings.openai_model` fallback for active
  features so they can never inherit the stale model.
- Unknown model id → refused as a configuration error (fail closed), not guessed.
- AI never assigns, infers or changes a Shariah status. No buy/sell advice. Hilal never
  recommends a value for an input; it may repeat back what the person typed as *theirs*.
- Never print a value from `.env` / `.env.production` / `auth.json`. Key names and counts only.
- Real env files: back up first (`.bak-opencode-go`), edit in **Python** only, copy only the
  changed/new keys, never reorder, prove key count rose by exactly the number added and no
  other value changed. For a JSON-valued key (pricing), add the Muse entry and prove every
  other entry is identical.
- Never weaken, skip or delete a test. Never touch the unrelated uncommitted work.
- No new dependency.
- Plain-language rule for anything a user reads (prompt wording shown to users, errors).

## 5. Work packages

### WP1 — Map every AI call and every Hilal surface (read-only)
Objective: produce `WP1_CALL_SITE_MAP.md` in the run dir (R1, R7) and `WP1_HILAL_SURFACES.md`.
Scope: every module in section 1; worker beat schedule (`worker.py`); routers mounted in the
live app; `templates/hilal/*` and `api/routers/dashboard_test.py` (pages with
`hilal_chat: True`); `sharia_passports.py`, `hilal_methodology.py`, methodology model fields
(`db/models`), import pack source fields.
Classification rule: ACTIVE = reachable from a live page/route, a scheduled task, or enabled in
`.env.production`. STALE = the strategy-from-prompt family (setup chat, interpreter, patch
extractor, capability extension/registry/embeddings, hybrid resolution, semantic fallback,
setup observability, Telegram strategy interpretation, onboarding interpreter). If a path is
genuinely ambiguous (e.g. `ai_explanations.py` cockpit narrator), mark AMBIGUOUS with evidence,
do **not** switch it, and list it under uncertainties.
Do not change: any file.
Acceptance: every grep hit in section 1 appears in the map; R7 answered.
Evidence: the two map files. Model: `mimo-v2.5` explorer.
Escalate if: an owner-listed active feature (section 0 item 1) looks stale or disabled in
production.

### WP2 — One owner for "which provider serves this model" + settings + secrets
Objective: R9, R10, R11, R13, R14.
Design (Claude's decision):
- New module `services/ai_provider.py` (one owner). A closed table model id → provider
  (`openai` for every model the stale features use today: `gpt-5.4-nano`, `gpt-5.4-mini`,
  `gpt-5.6-luna`, `text-embedding-3-small`, and any other id found in config/env;
  `opencode_go` for `muse-spark-1.3-contributor`). It returns the endpoint URL, the auth +
  content-type headers, provider-specific headers (`x-opencode-session` — a random id per
  conversation/run where one exists, else per call; never a user id or email), whether the
  provider honours `service_tier` (OpenCode Go: no — do not send it), the `provider` label
  for `provider_request` metrics, and `is_configured(settings, model)`.
- Also one shared reader for response text that does not rely on `output_text` (several
  modules have their own `_output_text`/`response_output_text` copies — active ones must use
  the one reader; do not edit stale-only files).
- Settings: `OPENCODE_GO_API_KEY: SecretStr | None`, `OPENCODE_GO_BASE_URL` default
  `https://opencode.ai/zen/go/v1`. Add the key to `CREDENTIAL_FIELDS`, redaction patterns,
  `security_review.py`, reliability-metrics secret matching.
- Defaults and all four env files: `PUBLIC_CHAT_AI_MODEL`, `HILAL_CHAT_AI_MODEL`,
  `SYSTEM_BRAIN_AI_MODEL`, `SHARIA_AI_MODEL`, `SHARIA_SOURCE_AI_MODEL` =
  `muse-spark-1.3-contributor`; their `*_REASONING_EFFORT` = `high`. `OPENAI_*` unchanged.
  Pricing entry for Muse added. Claude found the source: `LIVE_MODELS_VERBOSE.txt` metadata
  for `muse-spark-1.3-contributor` gives USD per million tokens `input 0.1`, `output 0.2`,
  cache read `0.002`; context 1,048,576; max output 131,072. Use these, in the same shape the
  existing pricing entries use.
- Real key: copy `opencode-go` key from `auth.json` into `.env` and `.env.production` in Python.
- Startup (`core/startup.py`) and every configured-check in section 1 go through
  `is_configured`.
Do not change: stale-only files; behaviour of any gpt model request.
Acceptance: R9–R11, R13, R14 tests green; phase6 launch audit green; env proofs recorded.
Milestone test (once, at end of WP2): new invariant files + neighbours of changed modules +
ruff on changed files.
Risk: secrets, startup refusing to boot in production. Escalate if: the price cannot be found
from any documented source.

### WP3 — Move the active features onto the owner and prove them with real calls
Objective: R2–R8, R12.
Scope: `agent_control.OpenAIAgentResponsesClient`, `openai_structured_call.structured_call`
(shared), `public_support_ai.py`, `public_chat.py`, `hilal_chat_agent.py`,
`system_brain_assistant.py`, `system_brain_agent.py` (tool loop must handle a response that
has both a `message` and `function_call` items, and ignore `reasoning` items correctly when
sending output back), `sharia_research.py` (both paths; flex→default fallback must not
re-send to a provider that ignores tiers — make it a no-op for OpenCode Go),
`sharia_source_monitoring.py`, `sharia_source_ai_discovery.py`, and R7's bot if it calls AI.
Budgets: make **one** real call per feature through the production code path (5–6 calls
total, plus at most one repeat for a feature whose budget had to change). Record input,
output, reasoning tokens and latency; set `*_MAX_OUTPUT_TOKENS`, `*_TIMEOUT_SECONDS` and
per-turn cost caps (defaults + four env files) with headroom from that measurement. Handle
`status:"incomplete"` explicitly.
Do not change: stale-only files; the `SHARIA_SOURCE_AI_DISCOVERY_ENABLED` flag; any Shariah
decision logic.
Acceptance: parametrised invariant test over every ACTIVE feature (model, effort, endpoint,
headers, no `service_tier`) and every STALE feature (unchanged OpenAI request); real-call table.
Milestone test (once, at end of WP3): the invariant files + test files of every touched module.
Escalate if: a real call fails in a way that is not a budget/parse issue (auth, quota 429,
refusal of a request field).

### WP4 — Hilal sees the page it is on and the person's own records
Objective: R15, R16, R17, R21.
Design:
- Keep the direction: pages **publish** their own words; `hm-page-context.js` never reads a
  page. Widen `ACCEPTED` to a closed list, one name per live dashboard page that shows Hilal
  (from WP1_HILAL_SURFACES.md): e.g. home overview, monitors list, alerts/history, coins /
  Passport page, settings & notifications, billing/plan — use the page's existing data and
  wording, no new visible UI.
- Canvas: extend `HilalChatBoardCard` with `inputs: [{label, filled, value}]` (value as typed,
  capped); keep `needs`.
- Server: matching typed optional fields on `HilalChatView`; `_on_screen` passes them with a
  note that values are the person's own. Caps: client truncates to the same caps the schema
  holds (one source — e.g. the server renders caps into the page, or a test asserts equality);
  an over-long description is truncated, never a refused message.
- Account records: new read-only section in `HilalChatKnowledge.gather` for the signed-in user
  only (monitors and what each still needs, last check, plan, connected alert channels),
  reusing existing read owners (dashboard context builders / services) rather than new queries
  where an owner exists. Pass `user_id` from the router; nothing for anonymous.
- Update `_instructions()` so the model knows these records exist and the rules (repeat the
  person's own values as theirs; never recommend a value).
Do not change: page visuals, the chat widget UI, refusal rules.
Acceptance: R15–R17, R21 tests; browser test file for page snapshots (run once at milestone).
Risk: privacy (cross-user leak). Escalate if: a page has no existing data source for its
description and would need a new API.

### WP5 — Hilal knows the Passports and the methodologies
Objective: R18, R19, R20.
Design:
- For each coin in `asked_about` (and the subject on screen): add its Passport through
  `ShariaPassportReadService` (the one owner) — status words per methodology, why,
  qualifications, exclusion reasons, reviewed at, next check, evidence source names and
  dates (no URLs; the model may not output them), admission route and automated notice for
  our own standard. Ids like `passport:<SYMBOL>:<METHODOLOGY_CODE>` added to `Evidence.ids`.
- Methodologies: extend `_methodologies()` with origin fields (governing body, source
  authority / published document name, what it screens for, in force from) from the stored
  rows / import pack / `hilal_methodology.py` published contract — whichever owner already
  holds each field.
- Prompt: say where these come from and that status is repeated per named standard only; our
  automated standard is always named as an automated reading and never as "the" answer.
- Bound size: measure payload characters for a 3-coin question; keep under a cap setting
  (four env files if new).
Do not change: any assessment, status, publication or default-methodology logic.
Acceptance: R18–R20 tests (parametrised over every `ShariaAssetStatus` and both methodology
kinds); one real Hilal turn asking "why is BTC listed and under which standard" whose answer
cites only records present in the payload.
Milestone test (once, WP4+WP5 together): hilal chat test files + new invariant files +
named browser file.

### WP6 — Final verification and handover
Objective: R10 proofs, R22, R23, reports.
- Full verification once: `ruff check src tests scripts`, `mypy src`,
  `pytest tests/unit tests/engine tests/interpreter tests/services -q -p no:randomly
  --junitxml`, plus `tests/integration` files for chat, system brain and sharia research.
- Before calling any failure a regression, compare failing IDs against a clean worktree at
  HEAD (`C:\wt-head`) per CLAUDE.md.
- Report: the exact server step (add `OPENCODE_GO_API_KEY` and the changed model/effort/budget
  keys to the server's `.env.production`, then redeploy) as a command; the privacy fact from
  section 1; the quota risk (Shariah research batches draw on the same OpenCode Go quota).

## 6. Verification floor

Reproducer tests for R9/R11/R12/R16/R21 fail on the unfixed code and pass after. Focused +
adjacent tests at each milestone only (R22). Final diff review against R8 (no stale-only file
in the diff). Requirement audit.

## 7. Reviewer policy

Risk: **high**. Two reviewers from different families: logic `glm-5.3-flash`, adversarial
`qwen3.7-plus` (focus: secrets never logged, cross-user leak in Hilal records, Shariah
authority wording, stale features untouched, silent OpenAI fallback).

## 8. Completion condition

Do not report complete until every R# has PASS evidence or a named blocker. Real-call
evidence is required for R2–R5 and the WP5 Hilal turn; spend minimally.
