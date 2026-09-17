# WP1: Hilal Surfaces — Complete Inventory

**Date:** 2026-09-16
**Run:** 20260916T184200Z-dd1e00e6

---

## 1. Dashboard pages showing the Hilal chat

All 11 `dashboard_test.py` routes receiving `_PATH_CHROME` (which holds `"hilal_chat": True`, line 189):

| Route handler | dashboard_test.py line |
|---|---|
| `screened_market_page` | 229 |
| `opportunities_page` | 258 |
| `watch_plans_page` | 296 |
| `passport_page` | 387 |
| `watchlist_page` | 415 |
| `connections_page` | 658 |
| `research_page` | 965 |
| `settings_page` | 1450 |
| `support_page` | 1927 |
| `subscription_page` | 2124 |
| `report_page` | 2300 |

Gate: `hilal_chat_gate()` in `api/template_env.py:150-158` checks the chrome flag AND `settings.hilal_chat_enabled`.

## 2. Client-side page context

- `static/hm-page-context.js`: `ACCEPTED = new Set(["board"])` (line 27); pages publish via `publish(name, describe)` (line 39); `sectionInView()` (line 48) finds the visible section.
- `static/hm-monitor-test.js:1904`: publishes `"board"` via `publish("board", boardSummary)` — the only publisher today.
- `static/hm-hilal-chat.js`: chat widget; imports `snapshot` from `hm-page-context.js` (line 19).

## 3. `schemas/hilal_chat.py` shapes

- `HilalChatReply` (line 79): `mode` (HilalChatMode), `reply` (str, 1–1200 chars), `language`, `suggestions` (max 3), `grounded_in` (max 12).
- `HilalChatMode` (line 33): `GREETING | ANSWER | GUIDE | CLARIFY | REFUSAL | OUT_OF_SCOPE`.
- `HilalChatReportReason` (line 52): `wrong | confusing | not_allowed | other`.
- Validators: `reply_is_written_for_a_person` (line 97) blocks code fences, HTML, JSON, internal field names.

## 4. `services/hilal_chat_knowledge.py`

- `HilalChatKnowledge.gather()` (line 189): inputs `message`, `view` (HilalChatView | None), `earlier`; returns read-only `Evidence`. Today passes methodologies as name/version/description/governing body only; no Passports.

## 5. Service ownership

| Module | Class | Role |
|---|---|---|
| `services/hilal_chat.py` | `HilalChatService` (line 113) | Orchestrates knowledge + agent; conversations, budgets |
| `services/hilal_chat_agent.py` | `HilalChatAgent` (line 286) | Makes the model call |
| `services/hilal_chat_knowledge.py` | `HilalChatKnowledge` (line 182) | Gathers evidence rows per turn |
| `schemas/hilal_chat.py` | `HilalChatReply` | Wire shapes |
| `db/models/hilal_chat.py` | `HilalChatConversation`, `HilalChatMessage`, `HilalChatMessageReport`, `HilalChatMessageRating` | Persistence |
| `api/routers/hilal_chat.py` | HTTP routes | Endpoints |
| `services/hilal_methodology.py` | methodology registry | Admissions pipeline (`ADMISSIONS_FILE` line 86) |
| `services/sharia_passports.py` | `ShariaPassportReadService` (line 60) | One owner for Passport reads (`current`, `historical`, `quick_view`) |

## 6. Hilal config keys (`core/config.py`)

`hilal_chat_enabled` (default True, line 893), `hilal_chat_ai_model` (None → falls back to `openai_model`, line 895), `hilal_chat_ai_reasoning_effort` (896), `hilal_chat_ai_timeout_seconds` 30 (899), `hilal_chat_ai_max_output_tokens` 900 (900), per-turn cost cap (901), provider attempts 2 (906), circuit breaker 5/60s (907-908), max history 16 (909), message max 800 (910), comment max 2000 (911), free daily USD 0.10 (915), paid multiplier 5 (921), max evidence assets 24 (928), retention 365 days (929).
