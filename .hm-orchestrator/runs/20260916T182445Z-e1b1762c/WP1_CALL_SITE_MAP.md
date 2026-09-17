# WP1: AI Provider Call Site Classification

**Date:** 2026-09-16  
**Scope:** Every AI (OpenAI) call site and "is AI configured" check in `src/ai_market_monitor`

---

## 1. Mounted Routers (evidence from `main.py`)

| Router | Mount prefix | Used by modules |
|---|---|---|
| `public_router` | `/` | public pages, health check |
| `public_chat_router` | `/api/v1` | `public_chat.py`, `public_support_ai.py` |
| `dashboard_api_router` | `/api/v1` | `hilal_chat_agent.py`, dashboard AI explanation |
| `hilal_chat_router` | `/api/v1` | `hilal_chat_agent.py` |
| `system_brain_router` | `/` (no prefix) | `system_brain_agent.py`, `system_brain_assistant.py` |
| `sharia_router` | `/api/v1` | Sharia research triggers |
| `onboarding_router` | `/api/v1` | Setup Chat (stale family) |
| `telegram_router` | `/api/v1` | Telegram service (strategy interpretation — stale) |
| `monitor_canvas_router` | `/api/v1` | strategy cockpit |
| `admin_router` | `/api/v1` | admin dashboard |

All routers are mounted unconditionally in `create_app()` (lines 164–185), except `whatsapp_router` gated on `settings.whatsapp_enabled`.

---

## 2. Celery Beat Entries (from `worker.py`)

| Beat entry name | Task function | AI call? |
|---|---|---|
| `schedule-due-scans-every-minute` | `schedule_due_scans` | No (deterministic scanner) |
| `research-unscreened-tradeable-coins` | `research_unscreened_coins` | **Yes** — calls `sharia_research.py` AI path |
| `screen-researched-coins` | `screen_researched_coins` | **Yes** — calls `automated_screen_pipeline.py` (deterministic + optional AI) |
| `monitor-published-sharia-sources` | `monitor_published_sharia_sources` | **Yes** — calls `sharia_source_monitoring.py` → `ShariaAIResearchClient._post` |
| `process-sharia-authority-imports` | `process_sharia_authority_imports` | **Yes** — calls `sharia_research.py` → `research_initial_asset` |
| `process-capability-extensions-every-thirty-seconds` | `process_capability_extensions` | **Yes** — calls `capability_extension_ai.py` |
| `recover-stalled-setup-chat-turns` | `recover_setup_chat_turns` | Yes — calls `setup_chat_recovery.py` (stale family) |
| `process-capability-extensions-every-thirty-seconds` | `process_capability_extensions` | Yes — calls `capability_extension_ai.py` |
| `refresh-system-brain-repository-index-every-five-minutes` | `refresh_system_brain_repository_index` | No (file system index) |
| `resolve-official-sources-daily` | `resolve_official_sources` | **Yes** — calls `sharia_source_resolution.py` → `sharia_source_ai_discovery.py` (gated on `SHARIA_SOURCE_AI_DISCOVERY_ENABLED`) |

---

## 3. Classification Table: Active AI Call Sites

| module | function/symbol | feature | classification | evidence |
|---|---|---|---|---|
| `services/public_chat.py` | `PublicChatService.answer()` | Public landing chat AI | **ACTIVE** | Mounted via `public_chat_router` at `/api/v1`; beat `cleanup_public_chat_data`; enabled via `PUBLIC_CHAT_AI_ENABLED=true` in production.example |
| `services/public_support_ai.py` | `PublicSupportAIService.respond()` | Public support AI | **ACTIVE** | Called by `public_chat.py` → `PublicSupportAIService`; gated on `PUBLIC_CHAT_AI_ENABLED`; production.example has it enabled |
| `services/hilal_chat_agent.py` | `HilalChatService.chat()` | Hilal dashboard chat | **ACTIVE** | Mounted via `hilal_chat_router` at `/api/v1`; uses `OpenAIAgentResponsesClient` |
| `services/agent_control.py` | `OpenAIAgentResponsesClient.post()` | Shared AI client (Setup Chat, Hilal, System Brain) | **ACTIVE** | Used by: `hilal_chat_agent.py`, `public_support_ai.py`, `system_brain_agent.py`, `system_brain_assistant.py`; all reachable from mounted routers |
| `services/system_brain_agent.py` | `SystemBrainAgentService.run_turn()` | System Brain operational agent | **ACTIVE** | Mounted via `system_brain_router`; gated on `SYSTEM_BRAIN_AI_ENABLED=true` in production.example |
| `services/system_brain_assistant.py` | `SystemBrainAssistantService.answer()` | System Brain assistant | **ACTIVE** | Mounted via `system_brain_router`; gated on `SYSTEM_BRAIN_AI_ENABLED=true` |
| `services/openai_structured_call.py` | `structured_call()` | Shared bounded structured AI call | **ACTIVE** | Used by `agent_control.py` (ACTIVE path) and by stale family; provider of last resort for structured output |
| `services/sharia_research.py` | `ShariaAIResearchClient._post()` | Sharia factual dossier AI | **ACTIVE** | Called by beat `process-sharia-authority-imports`, `monitor-published-sharia-sources`, `research-unscreened-tradeable-coins`; startup validates `OPENAI_API_KEY` required when `sharia_screening_enforced` |
| `services/sharia_research.py` | `ShariaResearchPipeline.research_initial_asset()` | Sharia research pipeline | **ACTIVE** | Called by `_process_sharia_authority_imports` beat task (line 1294); the "bot that checks halal status every couple of days" |
| `services/sharia_source_monitoring.py` | `ShariaSourceMonitoringService.run_due()` | Published Sharia source re-check | **ACTIVE** | Beat entry `monitor-published-sharia-sources` (daily tick); uses `ShariaAIResearchClient` when material changes detected |
| `services/sharia_source_ai_discovery.py` | `AISourceDiscovery.suggest()` | AI-powered source address discovery | **ACTIVE** (gated) | Called by `sharia_source_resolution.py`; gated on `SHARIA_SOURCE_AI_DISCOVERY_ENABLED=false` (default OFF); enabled flag `sharia_source_ai_discovery_enabled` exists |
| `services/sharia_source_resolution.py` | `SourceResolutionService.resolve_pending()` | Official source resolution | **ACTIVE** | Beat entry `resolve-official-sources-daily`; calls `AISourceDiscovery` as 6th fallback |
| `services/capability_registry.py` | `OpenAIEmbeddingClient.embed()` | Capability embeddings | **ACTIVE** | Called at app startup via `initialize_capability_registry()` in `main.py` lifespan; gated on `openai_api_key is not None` |
| `services/capability_extension_ai.py` | AI draft/review calls | Capability extension AI | **ACTIVE** | Beat entry `process-capability-extensions-every-thirty-seconds`; enabled via `CAPABILITY_EXTENSION_ENABLED=true` in production.example |
| `ai_explanations.py` | `OpenAISuggestionNarrator.narrate()` | Strategy suggestion narration | **ACTIVE** | Called by dashboard routes; gated on `OPENAI_EXPLANATION_ENABLED=true` in production.example |
| `services/agent_policy.py` | `AgentPolicyService` (AI configured check) | Capability extension gate | **ACTIVE** | Line 188: `self.settings.openai_api_key is not None`; guards capability extension eligibility |

---

## 4. Classification Table: "is AI configured" Checks

| module | line | symbol/scope | verified exists? | evidence |
|---|---|---|---|---|
| `core/startup.py` | 267, 301, 320, 335 | `settings.openai_api_key is None` | **Yes** | Lines 267 (sharia), 301 (interpreter), 320 (agent control), 335 (public chat) |
| `api/routers/public.py` | 931 | `settings.openai_api_key is not None` | **Yes** | Health check: `sharia_ai_research` status |
| `services/agent_policy.py` | 188 | `self.settings.openai_api_key is not None` | **Yes** | Capability extension gate |
| `services/system_brain_agent.py` | 405 | `self.settings.openai_api_key is None` | **Yes** | System Brain agent unavailable guard |
| `services/system_brain_assistant.py` | 136 | `self.settings.openai_api_key is None` | **Yes** | System Brain assistant unavailable guard |
| `services/sharia_research.py` | 394 | `self.settings.openai_api_key is None` | **Yes** | Sharia AI research client guard |
| `services/sharia_source_ai_discovery.py` | 110 | `self.settings.openai_api_key is not None` | **Yes** | AI discovery configured check |
| `api/routers/dashboard_api.py` | 3230 | `settings.openai_api_key` (truthiness) | **Yes** | Records model name for observability explanation |
| `api/routers/dashboard_api.py` | 3281 | `settings.openai_api_key` (truthiness) | **Yes** | Records model name for lifecycle investigation explanation |
| `services/feature_control.py` | 323 | `getattr(settings, "openai_model", "")` | **Yes** | Model route feature rule variant (not a key check, but reads openai_model) |

---

## 5. Classification Table: Stale Family (Strategy-from-Prompt)

| module | function/symbol | feature | classification | evidence |
|---|---|---|---|---|
| `services/ai_setup_chat.py` | `OpenAISetupChatClient` | Setup Chat AI | **STALE** | Strategy-from-prompt family; only referenced by stale modules (`onboarding.py`, `setup_chat_launch.py`) |
| `services/openai_interpreter.py` | `configured_strategy_interpreter()` | Legacy strategy interpreter | **STALE** | Strategy-from-prompt family; called by `telegram/service.py` (stale) |
| `services/strategy_patch_extractor.py` | `StrategyPatchExtractor` | Strategy patch extraction | **STALE** | Strategy-from-prompt family |
| `services/capability_extension_ai.py` | AI draft/review calls | Capability extension | **STALE** | Strategy-from-prompt family (listed as stale in mission); however also called by ACTIVE beat `process-capability-extensions` — see AMBIGUOUS note below |
| `services/capability_registry.py` | `OpenAIEmbeddingClient.embed()` | Capability embeddings | **STALE** | Strategy-from-prompt family (embeddings for strategy capabilities); however also called at ACTIVE app startup — see AMBIGUOUS note below |
| `services/hybrid_capability_resolution.py` | `HybridCapabilityResolver` | Capability resolution AI | **STALE** | Strategy-from-prompt family |
| `services/ai_semantic_fallback.py` | `SemanticFallback` | Semantic fallback AI | **STALE** | Strategy-from-prompt family |
| `services/setup_observability.py` | `SetupObservabilityService` | Setup observability narration | **STALE** | Strategy-from-prompt family; beat `aggregate-setup-observability` calls it but beat itself is stale infrastructure |
| `services/setup_chat_launch.py` | `SetupChatLaunchService` | Setup Chat launch | **STALE** | Strategy-from-prompt family; only reachable from `onboarding.py` (stale router) |
| `api/routers/onboarding.py` | onboarding routes | Setup Chat entry | **STALE** | Strategy-from-prompt family; mounted but serves stale Setup Chat flow |
| `telegram/service.py` | strategy interpretation path | Telegram strategy interpretation | **STALE** | Uses `openai_interpreter.py` (stale); mounted via `telegram_router` but the AI interpretation path is stale |

---

## 6. AMBIGUOUS Items

| module | function/symbol | feature | classification | evidence |
|---|---|---|---|---|
| `ai_explanations.py` | `OpenAISuggestionNarrator.narrate()` | "Cockpit narrator" — rewords deterministic evidence | **AMBIGUOUS** | Called by ACTIVE dashboard routes (`dashboard_api.py` lines 3221-3222, 3272-3273); gated on `OPENAI_EXPLANATION_ENABLED=true` in production.example; narrates deterministic evidence (cockpit observations) — not strategy-from-prompt but also not core user-facing chat. Question: should this move to new provider or stay? |
| `services/capability_registry.py` | `OpenAIEmbeddingClient.embed()` | Capability embeddings (semantic search for capabilities) | **AMBIGUOUS** | Called at ACTIVE app startup (`main.py` lifespan); listed as stale in mission (embeddings for strategy capabilities); but the registry is initialized for the builder and capability extension (ACTIVE beat). Dual use: both stale strategy family AND active capability extension infrastructure. |
| `services/capability_extension_ai.py` | AI draft/review | Capability extension AI | **AMBIGUOUS** | Listed as stale in mission; but called by ACTIVE beat `process-capability-extensions-every-thirty-seconds` which is enabled in production.example (`CAPABILITY_EXTENSION_ENABLED=true`). The beat is active; the AI call within it is part of the stale strategy-from-prompt family. |
| `services/openai_structured_call.py` | `structured_call()` | Shared structured AI call | **AMBIGUOUS** | Used by BOTH active (`agent_control.py` → Hilal/System Brain/public support) and stale (`ai_setup_chat.py`, `strategy_patch_extractor.py`) callers. The function itself is a utility; its classification depends on which caller invokes it. |

---

## 7. Extra Required Answers

### R7: "The bot that scrapes for coins in the Hilal strategy"

**Evidence:**  
- `worker.py` beat entry `research-unscreened-tradeable-coins` (line 228-231) → task `research_unscreened_coins` → `_research_unscreened_coins()` (line 1527)  
- `_research_unscreened_coins()` calls `UnscreenedCoinResearchService` (imported from `services/unscreened_coin_research.py`)  
- `UnscreenedCoinResearchService.research()` gathers website, whitepaper, repository, logo from CoinMarketCap — **deterministic scraping, no LLM call**  
- The subsequent beat `screen-researched-coins` (line 239-242) → `_screen_researched_coins()` calls `AutomatedScreenPipeline` which reads scraped pages and records automated screen results — also **deterministic, no LLM call**  
- The AI call happens later in `process-sharia-authority-imports` (line 168-171) via `ShariaResearchPipeline.research_initial_asset()` which calls `ShariaAIResearchClient._post()`  

**Answer:** The coin scraper/researcher (`research_unscreened_coins`) does NOT call an LLM. It is purely deterministic web scraping. The LLM call happens in the separate `process-sharia-authority-imports` beat task via `ShariaResearchPipeline` → `ShariaAIResearchClient._post()`.

---

### sharia_research.py: Which path is used by which feature?

**Path 1: `ShariaAIResearchClient._post()`** (line 393-428)  
- The low-level AI call: posts evidence to OpenAI Responses API  
- Used by: `ShariaResearchPipeline.research_initial_asset()` AND `ShariaSourceMonitoringService._research_material_change()`  
- The "bot that checks halal status every couple of days" uses this path via `ShariaResearchPipeline.research_initial_asset()`, called from:  
  - `process-sharia-authority-imports` beat (daily) — initial research for newly imported coins  
  - `monitor-published-sharia-sources` beat (daily) — re-check when published sources change  

**Path 2: `ShariaResearchPipeline.research_initial_asset()`** (line 540+)  
- Orchestrates: source fetching, evidence packaging, then calls `ShariaAIResearchClient.analyze()`  
- Called by: `process_sharia_authority_imports` worker task (the daily authority import sweep)  
- This is the primary "check halal status" path  

**Answer:** Both paths serve the same Sharia research feature. `_post()` is the HTTP layer; `research_initial_asset()` is the orchestrator. The daily "check halal status" bot uses `research_initial_asset()` which internally calls `_post()`.

---

### sharia_source_monitoring.py and sharia_source_ai_discovery.py: Beat entries and enable flags

**sharia_source_monitoring.py:**  
- Beat entry: `monitor-published-sharia-sources` (worker.py line 220-223)  
- Task: `ai_market_monitor.monitor_published_sharia_sources`  
- Schedule: 24 hours (daily)  
- No separate enable flag — runs unconditionally (calls `ShariaSourceMonitoringService.run_due()`)  

**sharia_source_ai_discovery.py:**  
- Beat entry: `resolve-official-sources-daily` (worker.py line 173-175)  
- Task: `ai_market_monitor.resolve_official_sources`  
- Schedule: 24 hours (daily)  
- Enable flag: `SHARIA_SOURCE_AI_DISCOVERY_ENABLED` (default `false` in both .env.example and .env.production.example)  
- The AI discovery is the 6th fallback in `SourceResolutionService`, only called when all 5 free methods fail AND the flag is on  

---

## 8. Summary: What Moves to New Provider

**Definitely moves (ACTIVE, user-facing or scheduled):**
1. `public_chat.py` / `public_support_ai.py` → public landing chat
2. `hilal_chat_agent.py` → Hilal dashboard chat
3. `system_brain_agent.py` / `system_brain_assistant.py` → System Brain
4. `agent_control.py` → shared client (used by all ACTIVE chat features)
5. `sharia_research.py` → Sharia factual dossier AI (daily scheduled)
6. `sharia_source_monitoring.py` → Sharia source re-check AI (daily scheduled)
7. `capability_registry.py` embeddings → app startup (ACTIVE infrastructure)
8. `capability_extension_ai.py` → capability extension beat (ACTIVE beat)
9. `ai_explanations.py` → cockpit narration (ACTIVE route)

**Definitely stays stale (strategy-from-prompt):**
1. `ai_setup_chat.py`
2. `openai_interpreter.py`
3. `strategy_patch_extractor.py`
4. `hybrid_capability_resolution.py`
5. `ai_semantic_fallback.py`
6. `setup_observability.py`
7. `setup_chat_launch.py`
8. `onboarding.py` router
9. `telegram/service.py` strategy interpretation path

**Needs decision (AMBIGUOUS):**
1. `openai_structured_call.py` — shared utility, used by both active and stale
2. `capability_registry.py` embeddings — active infrastructure but listed as stale
3. `capability_extension_ai.py` — active beat but listed as stale family
4. `ai_explanations.py` — active route but "cockpit narrator" role unclear
