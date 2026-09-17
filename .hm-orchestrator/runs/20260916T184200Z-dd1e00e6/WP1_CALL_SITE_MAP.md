# WP1: AI Provider Call Site Classification (Verified)

**Date:** 2026-09-16
**Run:** 20260916T184200Z-dd1e00e6
**Scope:** Every AI call site and "is AI configured" check in `src/ai_market_monitor`.
**Method:** Prior draft (run 20260916T182445Z-e1b1762c) re-checked row by row against code by read-only explorer. Corrections in section 7.

---

## 1. Mounted Routers (evidence from `main.py:164-185`)

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

All routers mounted unconditionally except `whatsapp_router` gated on `settings.whatsapp_enabled` (`main.py:181`).

---

## 2. Celery Beat Entries (from `worker.py`)

| Beat entry name | Task function | AI call? |
|---|---|---|
| `schedule-due-scans-every-minute` | `schedule_due_scans` | No (deterministic scanner) |
| `research-unscreened-tradeable-coins` | `research_unscreened_coins` | No (deterministic scraping — see R7) |
| `screen-researched-coins` | `screen_researched_coins` | No (deterministic screen) |
| `monitor-published-sharia-sources` | `monitor_published_sharia_sources` | **Yes** — `ShariaSourceMonitoringService.run_due()` → `self.ai.analyze()` |
| `process-sharia-authority-imports` | `process_sharia_authority_imports` | **Yes** — `ShariaResearchPipeline.research_initial_asset()` → `ShariaAIResearchClient._post()` |
| `process-capability-extensions-every-thirty-seconds` | `process_capability_extensions` | **Yes** — `CapabilityExtensionAI` draft/review/repair |
| `recover-stalled-setup-chat-turns` | `recover_setup_chat_turns` | Yes — `setup_chat_recovery.py` (stale family) |
| `refresh-system-brain-repository-index-every-five-minutes` | `refresh_system_brain_repository_index` | No (file system index) |
| `resolve-official-sources-daily` | `resolve_official_sources` | **Yes** — `AISourceDiscovery.suggest()` (gated on `SHARIA_SOURCE_AI_DISCOVERY_ENABLED`) |
| `aggregate-setup-observability-every-five-minutes` | `aggregate_setup_observability` | No (deterministic aggregation) |

---

## 3. Classification Table: Active AI Call Sites

| module | function/symbol | feature | classification | evidence |
|---|---|---|---|---|
| `services/public_chat.py` | `PublicChatService.answer()` (line 788) | Public landing chat AI | **ACTIVE** | Mounted via `public_chat_router` at `/api/v1`; gated on `PUBLIC_CHAT_AI_ENABLED` |
| `services/public_support_ai.py` | `PublicSupportAIService.respond()` (line 75) | Public support AI | **ACTIVE** | Called by `public_chat.py`; gated on `PUBLIC_CHAT_AI_ENABLED` |
| `services/hilal_chat_agent.py` | `HilalChatAgent.answer()` (line 306) | Hilal dashboard chat | **ACTIVE** | Mounted via `hilal_chat_router` at `/api/v1`; uses `OpenAIAgentResponsesClient` |
| `services/agent_control.py` | `OpenAIAgentResponsesClient.create()` (line 77) | Shared AI client (Hilal, public support, System Brain) | **ACTIVE** | Used by `hilal_chat_agent.py`, `public_support_ai.py`, `system_brain_agent.py`, `system_brain_assistant.py` |
| `services/system_brain_agent.py` | `SystemBrainAgentService.run_turn()` (line 397) | System Brain operational agent | **ACTIVE** | Mounted via `system_brain_router`; gated on `SYSTEM_BRAIN_AI_ENABLED` |
| `services/system_brain_assistant.py` | `SystemBrainAssistantService.answer()` (line 125) | System Brain assistant | **ACTIVE** | Mounted via `system_brain_router`; gated on `SYSTEM_BRAIN_AI_ENABLED` |
| `services/openai_structured_call.py` | `structured_call()` (line 135) | Shared bounded structured AI call | **ACTIVE-shared** | Used by ACTIVE `agent_control.py` and stale family; editable only with byte-identical stale requests |
| `services/sharia_research.py` | `ShariaAIResearchClient._post()` (line 427), `analyze()` (line 393) | Sharia factual dossier AI | **ACTIVE** | Called by beats `process-sharia-authority-imports`, `monitor-published-sharia-sources` |
| `services/sharia_research.py` | `ShariaResearchPipeline.research_initial_asset()` (line 548) | Sharia research pipeline | **ACTIVE** | Called by `_process_sharia_authority_imports` beat task |
| `services/sharia_source_monitoring.py` | `ShariaSourceMonitoringService.run_due()` (line 65) | Published Sharia source re-check | **ACTIVE** | Beat `monitor-published-sharia-sources` (daily); calls `self.ai.analyze()` at line 275 on material change |
| `services/sharia_source_ai_discovery.py` | `AISourceDiscovery.suggest()` (line 125) | Coin-website source bot | **ACTIVE** (gated OFF) | Called by `sharia_source_resolution.py` as 6th fallback; flag `SHARIA_SOURCE_AI_DISCOVERY_ENABLED=false` unchanged |
| `services/sharia_source_resolution.py` | `SourceResolutionService.resolve_pending()` (line 438) | Official source resolution | **ACTIVE** | Beat `resolve-official-sources-daily`; calls `AISourceDiscovery` as 6th fallback |
| `services/agent_policy.py` | `AgentPolicyService` (line 188) | Capability extension gate | **ACTIVE-check** | Guards capability extension eligibility |

---

## 4. Classification Table: "is AI configured" Checks

| module | line | check | evidence |
|---|---|---|---|
| `core/startup.py` | 267 | `settings.openai_api_key is None` | Sharia research validation |
| `core/startup.py` | 301 | `settings.openai_api_key is None` | AI interpreter provider validation |
| `core/startup.py` | 320 | `settings.openai_api_key is None` | Agent control validation |
| `core/startup.py` | 334 | `settings.openai_api_key is None` | Public chat validation |
| `api/routers/public.py` | 931 | `settings.openai_api_key is not None` | Health check `sharia_ai_research` status |
| `services/agent_policy.py` | 188 | `self.settings.openai_api_key is not None` | Capability extension gate |
| `services/system_brain_agent.py` | 405 | `self.settings.openai_api_key is None` | Agent unavailable guard |
| `services/system_brain_assistant.py` | 136 | `self.settings.openai_api_key is None` | Assistant unavailable guard |
| `services/sharia_research.py` | 394 | `self.settings.openai_api_key is None` | Research client guard |
| `services/sharia_source_ai_discovery.py` | 110 | `self.settings.openai_api_key is not None` | Discovery configured check |
| `api/routers/dashboard_api.py` | 3230 | `settings.openai_api_key` (truthiness) | Model name for observability explanation |
| `api/routers/dashboard_api.py` | 3281 | `settings.openai_api_key` (truthiness) | Model name for lifecycle investigation |
| `services/feature_control.py` | 323 | `getattr(settings, "openai_model", "")` | Reads `openai_model`, NOT a key check |

---

## 5. Classification Table: Stale Family (Strategy-from-Prompt — never edited, never switched)

| module | function/symbol | evidence |
|---|---|---|
| `services/ai_setup_chat.py` | `OpenAISetupChatInterviewer` | Only stale callers (`onboarding.py`, stale dashboard routes) |
| `services/openai_interpreter.py` | `configured_strategy_interpreter()` | Called by `telegram/service.py` (stale) |
| `services/strategy_patch_extractor.py` | `StrategyPatchExtractor` | Strategy-from-prompt family |
| `services/hybrid_capability_resolution.py` | `HybridCapabilityResolutionService` | Strategy-from-prompt family |
| `services/ai_semantic_fallback.py` | `AISemanticFallbackService` | Strategy-from-prompt family |
| `services/setup_chat_launch.py` | `SetupChatLaunchService` | Only reachable from `onboarding.py` (stale router) |
| `services/setup_chat_agent.py` | `SetupChatAgent` (line 584; `structured_call` at 3254/3388/3637) | Only reachable from stale callers |
| `api/routers/onboarding.py` | onboarding routes | Serves stale Setup Chat flow |
| `telegram/service.py` | strategy interpretation path | Uses `openai_interpreter.py` (stale) |

---

## 6. AMBIGUOUS — marked, NOT switched (mission rule)

| module | function/symbol | why ambiguous |
|---|---|---|
| `ai_explanations.py` | `OpenAISuggestionNarrator.narrate()` (line 34) | Called by ACTIVE dashboard routes but narrates deterministic evidence; mission names it as the ambiguous example |
| `services/capability_registry.py` | `OpenAIEmbeddingClient.embed()` (line 28) | ACTIVE app startup (`main.py` lifespan) but embeddings serve the stale strategy family |
| `services/capability_extension_ai.py` | `CapabilityExtensionAI.draft()/.review()/.repair()` | ACTIVE beat `process-capability-extensions` but AI calls belong to stale family |
| `services/openai_structured_call.py` | `structured_call()` (line 135) | Shared by ACTIVE and stale callers (shared-client rule applies) |
| `services/setup_observability.py` | `GroundedObservabilityExplainer.explain()` (line 1509) | Called from ACTIVE dashboard routes (`dashboard_api.py:3221,3272`) but module listed stale in mission |

---

## 7. Corrections from Prior Draft

| Draft claim | Correction |
|---|---|
| `hilal_chat_agent.py`: `HilalChatService.chat()` | Class is `HilalChatAgent` (line 286), method `answer()` (line 306) |
| `agent_control.py`: `OpenAIAgentResponsesClient.post()` | Method is `create()` (line 77) |
| `setup_observability.py`: STALE | `GroundedObservabilityExplainer.explain()` called from ACTIVE dashboard routes — moved to AMBIGUOUS |
| `setup_chat_agent.py`: missing | Added as STALE (only stale callers) |
| `capability_extension_ai.py` / `capability_registry.py`: STALE rows | Kept also as AMBIGUOUS (active beat/startup); NOT switched either way |
| Beat `process-capability-extensions` listed twice | Deduplicated |
| Beat `aggregate-setup-observability` missing | Added (deterministic) |
| `feature_control.py:323` reads `openai_api_key` | Reads `openai_model`, not a key check |

---

## 8. R7 Answer: "The bot that scrapes for coins in the Hilal strategy"

The coin scraper does **NOT** call a model. Evidence chain:

1. Beat `research-unscreened-tradeable-coins` (`worker.py:228-231`) → `_research_unscreened_coins()` (`worker.py:1527+`) → `UnscreenedCoinResearchService.research()` (`services/unscreened_coin_research.py:237`) → CoinMarketCap data provider (deterministic, no LLM).
2. `CoinEvidenceCrawler.gather()` (`services/coin_evidence_crawler.py:262`) — deterministic web scraping (website, whitepaper, repository).
3. Beat `screen-researched-coins` (`worker.py:239-242`) → `AutomatedScreenPipeline` (`services/automated_screen_pipeline.py:144`) — deterministic screen, no LLM.
4. The LLM call happens only later in the separate beat `process-sharia-authority-imports` via `ShariaResearchPipeline.research_initial_asset()` → `ShariaAIResearchClient._post()` (covered by R4).

So R7 needs no model routing; the row stays as evidence that the scraper is deterministic.
