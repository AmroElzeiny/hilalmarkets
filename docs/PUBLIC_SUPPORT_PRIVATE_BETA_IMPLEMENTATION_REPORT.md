# Public Support AI Private-Beta Implementation Report

Date: 2026-07-18

Base revision inspected: `c7e1b7bc` on `main`

## 1. Root cause

The former support flow gave the model a UI-authoritative `show_inquiry_form`
field. Low confidence, grounding failures, unavailable product sources, and model
validation failures could all become a knowledge-gap response with that field set.
The browser then called `showInquiry()` directly from the model-backed response.
Consequently, ordinary conversation such as a greeting could be misclassified and
immediately replace chat with the Support form.

The corrected invariant is:

> Model output may say that human support is available, but only an explicit user
> action may open the Support form.

The only opening paths are the visitor clicking **No. Submit a support form** or
the visitor explicitly asking to contact, email, or speak with the team.

## 2. Conversation modes

| Mode | Authority and behavior |
| --- | --- |
| `PRODUCT_FACT` | Current HilalMarkets facts require an approved server source or a successful read-only tool result. |
| `PRODUCT_CONVERSATION` | Greetings, thanks, confusion, and normal product conversation use natural AI wording and do not require citations. |
| `GENERAL_TRADING_EDUCATION` | Neutral concept explanations are allowed, while personalized instructions, live values, predictions, guarantees, and Sharia rulings are rejected. |
| `ACCOUNT_SUPPORT` | Authenticated read-only account tools supply the facts. The model cannot mutate account state. |
| `OUT_OF_SCOPE` | Unrelated requests receive a friendly HilalMarkets-focused redirect. |
| `SAFETY_REFUSAL` | Buy/sell advice, leverage, guarantees, personal fatwas, secret extraction, and cross-account access are refused. |

Natural-language classification and response wording remain AI-controlled. Code is
used for evidence, ownership, schema, tool, and safety enforcement rather than as a
keyword response tree.

## 3. AI response contract

The strict response schema now contains:

- `stage`
- `mode`
- `intent`
- `answer`
- `clarification_question`
- `source_ids`
- `related_route_ids`
- `requested_tools`
- `confidence`
- `answer_complete`
- `safety_boundary`
- `suggested_follow_ups`
- `support_handoff_available`
- `support_handoff_reason`

`support_handoff_available` is advisory. It has no direct UI authority. The API
adds a separate server-derived `support_handoff_explicitly_requested` value after
detecting an explicit visitor request. Unknown source IDs, routes, tools, account
claims without successful tools, and ungrounded product facts fail closed.

## 4. Feedback bar and state flow

After each persisted assistant answer event, the chat displays:

`Did AI answer your question?`

Actions:

- **Yes** records one helpful feedback record and displays
  `Great! Ready when you are.` The composer remains active.
- **No. Submit a support form** records one unhelpful/support-requested result,
  then opens the editable form with the visitor name, email, and latest question.
- **Not now** closes the form and returns to the intact conversation.

The bar is hidden while loading and while profile, inquiry, or success views are
active. It resets for the next message. Duplicate submissions for one answer are
idempotent; conflicting second submissions are rejected.

## 5. Support-form authority

The browser no longer reads `show_inquiry_form`, and that field is absent from the
response schema. Model recommendation, low confidence, source gaps, timeout, and
invalid output cannot open the form. Inquiry submission also fails server-side
unless the same session first persisted negative feedback with
`support_form_requested=true` for that answer event.

Internal metadata is assembled from the persisted answer event, not editable form
fields. It includes stage, mode, intent, model, confidence, source IDs, validation
state, and the knowledge-gap reason.

## 6. Expected behavior examples

| Input | Result |
| --- | --- |
| `Hi` | Friendly `PRODUCT_CONVERSATION`; no citation and no handoff. |
| `How are you?` | Natural follow-up; no handoff. |
| `What is an Evidence Passport?` | Grounded `PRODUCT_FACT` answer. |
| `What is RSI?` | Neutral `GENERAL_TRADING_EDUCATION` explanation. |
| `Give me a cupcake recipe` | Friendly `OUT_OF_SCOPE` redirect. |
| `Should I buy SOL now?` | `SAFETY_REFUSAL`; no recommendation or form. |
| `Email the team about a partnership` | Answer plus explicit user-controlled handoff path. |

## 7. Conversation memory

Bounded persisted state now carries the current topic, last entities, last question,
last answer event, previous source IDs, resolved references, pending clarification,
previous feedback result, and support-form state. Profile names may personalize a
greeting, but locally stored email data never authenticates account access.

## 8. Feedback persistence and System Brain

Migration `3cedf4051627` adds:

- conversation mode, knowledge-gap reason, greeting marker, and advisory handoff
  fields to answer events;
- server-owned support metadata on inquiries;
- `public_chat_answer_feedback`, uniquely keyed by answer event and optionally
  linked one-to-one with an inquiry.

System Brain now reports answered, clarified, refused, out-of-scope, AI-unavailable,
Yes rate, No/support-form rate, form completion, greeting misclassification,
dissatisfaction by intent, model usage, latency, cost, validation failures, and
common unanswered product topics.

## 9. Project Notion resources

The public Support AI has bounded read-only retrieval from `./Notion` for `.md`,
`.txt`, `.csv`, and `.json` resources. Retrieval enforces a fixed resolved root,
rejects symlinks and path escapes, bounds file/document/character counts, redacts
secret-like assignments, and supplies content hashes. Retrieved Notion text is
marked `context_only`: it can improve vocabulary and explanations but cannot prove
current prices, product state, availability, account facts, or screening decisions.
Binary design assets are not sent to the model.

Production startup fails clearly when Notion retrieval is enabled but the configured
directory is missing. The application image includes the project Notion export.

## 10. Placeholder changes

- Public Support Chat: `Ask about HilalMarkets...` (rendered with the requested
  single ellipsis glyph in the UI).
- Dashboard Trading Chat: `Describe your setup...` (rendered with the requested
  single ellipsis glyph in the UI).

Templates, accessibility labels, JavaScript behavior, and browser assertions use
the same wording.

## 11. Dashboard Trading Agent boundaries

The local and production environment profiles were aligned to the requested beta
state:

- bounded agent control enabled;
- shadow mode disabled;
- rollout at 100 percent;
- capability extension enabled;
- public Support AI enabled;
- billing and WhatsApp disabled.

The implementation does not alter scheduled deterministic evaluation, Watch Plan
approval/activation, ownership checks, capability certification, quarantine, or
the emergency agent kill switch. No approval, activation, arbitrary code, SQL,
shell, filesystem, or unrestricted network tool was added.

## 12. Files changed

### Backend and data

- `src/ai_market_monitor/schemas/public_chat.py`
- `src/ai_market_monitor/db/models/public_chat.py`
- `src/ai_market_monitor/db/models/__init__.py`
- `src/ai_market_monitor/services/public_chat.py`
- `src/ai_market_monitor/services/public_support_ai.py`
- `src/ai_market_monitor/services/notion_knowledge.py`
- `src/ai_market_monitor/services/system_brain.py`
- `src/ai_market_monitor/api/routers/public_chat.py`
- `src/ai_market_monitor/core/config.py`
- `src/ai_market_monitor/core/startup.py`
- `alembic/versions/3cedf4051627_add_public_chat_answer_feedback.py`

### Browser and presentation

- `src/ai_market_monitor/templates/hilal/partials/public_chat.html`
- `src/ai_market_monitor/static/hilalmarkets-public-chat.js`
- `src/ai_market_monitor/static/hilalmarkets-public-chat.css`
- `src/ai_market_monitor/templates/hilal/dashboard/builder.html`
- `src/ai_market_monitor/templates/system_brain.html`

### Configuration, packaging, tests, and documentation

- `.env.example`
- `.env.production.example`
- `Dockerfile`
- `Notion/`
- `tests/integration/test_public_chat_api.py`
- `tests/unit/test_public_chat.py`
- `tests/unit/test_notion_knowledge.py`
- `tests/unit/test_system_brain.py`
- `tests/unit/test_request_guards.py`
- `tests/unit/test_agent_control.py`
- `tests/browser/test_dashboard_e2e.py`
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/OPERATIONS.md`
- `docs/PRIVATE_BETA_READINESS_REPORT.md`
- `docs/PRIVATE_BETA_SOAK_RUNBOOK.md`
- this report

## 13. Verification results

Verified in this working tree:

- Focused support, Notion, System Brain, route-guard, and agent regression suite:
  **43 passed**.
- Ruff repository check: **passed**.
- MyPy for the changed production modules: **passed**.
- Jinja load validation: **62 templates passed**.
- JavaScript syntax validation: **17 files passed**.
- API route-security audit: **passed**.
- Release-invariant audit: **passed**.
- Alembic fresh upgrade and single-head validation: **passed**, head
  `3cedf4051627`.

The complete backend and browser runs are recorded after they finish below; no
external provider or staging result is inferred from local tests.

## 14. Visual QA

The browser suite writes responsive evidence to:

- `reports/playwright/visual-qa/public-chat/public-chat-desktop-1440.png`
- `reports/playwright/visual-qa/public-chat/public-chat-desktop-1024.png`
- `reports/playwright/visual-qa/public-chat/public-chat-desktop-768.png`
- `reports/playwright/visual-qa/public-chat/public-chat-mobile-390.png`
- `reports/playwright/visual-qa/public-chat/public-chat-mobile-feedback-390.png`
- `reports/playwright/visual-qa/public-chat/public-chat-mobile-support-form-390.png`

These paths become authoritative only after the final browser run succeeds.

## 15. Readiness matrix

| Feature or gate | State | Evidence or remaining work |
| --- | --- | --- |
| Six-mode Support AI contract | Implemented, locally verified | Focused integration suite. |
| Greeting and normal conversation | Implemented, locally verified | `hi`, `hello`, `how are you`, and `thanks` regressions. |
| Product grounding | Implemented, locally verified | Unknown citations and ungrounded facts fail closed. |
| General trading education safety | Implemented, locally verified | Neutral education and buy/sell refusal tests. |
| User-controlled Support handoff | Implemented, locally verified | API and browser flows; server enforces prior negative feedback. |
| Durable feedback and System Brain metrics | Implemented, locally verified | Migration, idempotency, metrics tests. |
| Bounded Notion resources | Implemented, locally verified | Root, size, format, redaction, and authority-boundary tests. |
| Exact chat placeholders | Implemented, focused verified | Final browser verification pending in this run. |
| Dashboard Agent live rollout configuration | Configured locally | Production deployment must load the reviewed environment file. |
| Full backend regression | Running | Final count will be recorded after completion. |
| Desktop/mobile Playwright and screenshots | Pending in this run | Requires isolated Chromium container. |
| GitHub Release Gate on final commit | External pending | Final work is uncommitted; no workflow result can exist yet. |
| Branch-protection required checks | External pending | Configure in GitHub repository settings. |
| Real OpenAI/Binance capability certification | External pending | Must use controlled staging credentials and redacted evidence. |
| Staging PostgreSQL upgrade and restore drill | External pending | Requires a real staging backup and recovery target. |
| Live Telegram and Support email delivery | External pending | Requires controlled recipients and provider-log evidence. |
| SPF, DKIM, DMARC | External pending | Verify against the production sending domain. |
| Seven-day duplicate soak and ten-user usability study | External pending | Time- and participant-dependent release gates. |
| Cloudflare/origin hardening | External pending | Requires production DNS, Access, and firewall control. |

## 16. Manual and staging QA checklist

1. Apply Alembic migration `3cedf4051627` to a staging PostgreSQL backup.
2. Verify `Hi`, one product fact, one educational question, one refusal, and one
   out-of-scope request with the configured Support model.
3. Confirm no response opens the form until the visitor clicks No or explicitly
   requests the team.
4. Confirm Yes remains idempotent and leaves the composer usable.
5. Confirm No prefills editable identity/question fields, and Not now preserves the
   conversation.
6. Verify an authenticated account-status question uses only the current user's
   read-only tool result.
7. Inspect System Brain feedback and cost metrics after the above turns.
8. Exercise the Dashboard Agent kill switch and restore the requested rollout.
9. Run controlled successful and failed custom-capability certification with real
   OpenAI and Binance data; retain only redacted evidence.
10. Run GitHub Release Gate on the exact final commit and require every job before
    private-beta deployment.
