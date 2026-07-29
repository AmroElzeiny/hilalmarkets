# WhatsApp Cloud API Runbook

## Scope

HilalMarkets integrates directly with the official Meta WhatsApp Cloud API. It does not use
Twilio, another business-solution provider, WhatsApp Web automation, QR sessions, personal-account
automation, or polling. WhatsApp is an opt-in research-monitoring, compliance, account, and
navigation channel. It does not execute trades.

Code availability is not evidence that Meta has approved the business, phone number, templates,
message categories, legal terms, or production use. Those external steps must be completed and
recorded separately.

Official references:

- [WhatsApp Cloud API collection](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api)
- [Webhook payload reference](https://www.postman.com/meta/whatsapp-business-platform/folder/tduohwq/webhook-payload-reference)
- [Webhook subscriptions](https://www.postman.com/meta/whatsapp-business-platform/folder/ypn8q0n/webhook-subscriptions)

## Required Meta Assets

1. A Meta business portfolio and WhatsApp Business Account (WABA).
2. A Cloud API app associated with that WABA.
3. A registered HilalMarkets business phone number and its phone-number ID.
4. A production system-user access token stored only in the deployment secret store.
5. App permissions required by the selected Meta setup, including WhatsApp messaging and
   management permissions where applicable.
6. A public HTTPS callback reachable at:
   `https://<public-host>/api/v1/whatsapp/webhook`.
7. A private webhook verify token chosen by HilalMarkets.
8. Approved message templates for every event that must be sent outside an open customer-service
   window.

Do not request exchange API keys, wallet secrets, seed phrases, private keys, payment PINs, or Meta
credentials from a customer.

## Environment Contract

```dotenv
WHATSAPP_ENABLED=false
WHATSAPP_ADAPTER=none
WHATSAPP_GRAPH_API_VERSION=
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_APP_SECRET=
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_BUSINESS_ACCOUNT_ID=
WHATSAPP_BUSINESS_PHONE_E164=
WHATSAPP_DEFAULT_LANGUAGE=en_US
WHATSAPP_HTTP_TIMEOUT_SECONDS=15
WHATSAPP_MAX_DELIVERY_ATTEMPTS=5
WHATSAPP_OPPORTUNITY_ALERTS_ENABLED=false
WHATSAPP_TEMPLATE_NAMES={}
WHATSAPP_OPT_IN_VERSION=2026-07
WHATSAPP_MARK_INBOUND_READ=true
WHATSAPP_WEBHOOK_RECEIPT_RETENTION_DAYS=30
```

Secrets are `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET`, and `WHATSAPP_VERIFY_TOKEN`. Never put
them in client JavaScript, database rows, logs, screenshots, tickets, fixtures, or source control.
The Graph API version is intentionally configuration, not an endpoint constant.

`WHATSAPP_TEMPLATE_NAMES` accepts an event name or a locale map:

```json
{
  "connection_test": "hm_connection_test_v1",
  "compliance_change": {
    "en_US": "hm_compliance_change_en_v1",
    "default": "hm_compliance_change_en_v1"
  }
}
```

Unknown event keys and invalid Meta template names fail configuration validation.
In staging and production, enabling WhatsApp also requires approved names for every non-opportunity
event in the registry. `confirmed_research_event` is additionally required when opportunity alerts
are enabled. Placeholder names fail startup; this prevents an apparently active channel from
silently depending on unapproved or missing templates.

## Webhook Registration

1. Deploy the API over HTTPS with WhatsApp still disabled.
2. Add all required values except `WHATSAPP_ENABLED`; use the current Meta Graph API version
   approved for the deployment.
3. Set the callback URL to `https://<public-host>/api/v1/whatsapp/webhook`.
4. Enter the same secret value configured as `WHATSAPP_VERIFY_TOKEN` in Meta's verification form.
5. Subscribe the WABA to `messages` events. Delivery states arrive in the same webhook family.
6. Set `WHATSAPP_ENABLED=true` and `WHATSAPP_ADAPTER=http`, restart API, worker, and scheduler, then
   complete Meta's GET challenge.
7. Send a signed test event and confirm the webhook IntegrationHealth row updates.

GET verification uses constant-time verify-token comparison. POST reads the raw request body and
validates `X-Hub-Signature-256` with HMAC-SHA256 and the App Secret before JSON parsing. The parser
rejects a mismatched WABA or phone-number ID and processes every message and status in every batch.
Valid events are persisted with independent event keys, acknowledged, and sent to Celery.

Do not configure polling. The scheduler only retries already accepted webhook receipts.

## Dashboard Opt-In And Linking

1. The signed-in customer opens Dashboard > Integrations.
2. They enter their own E.164 number, select at least one category, and explicitly check the consent
   box. Nothing is preselected.
3. HilalMarkets stores only a digest of a cryptographically random, short-lived, single-use link
   token and returns a prefilled `wa.me` URL.
4. The customer sends `LINK <token>` from that WhatsApp account.
5. The signed inbound webhook proves the `wa_id`. The service requires its normalized number to
   equal the dashboard number and refuses identities already owned by another user.
6. Token consumption, connection verification, categories, consent version, source, and audit event
   are persisted atomically.
7. The newly opened service window permits a connection confirmation, and the dashboard detects the
   verified state by polling only its masked status endpoint.

Typed numbers are never treated as verified identity. Replayed, expired, canceled, mismatched, or
stolen tokens fail closed. The browser receives a masked number and status fields, never a `wa_id`
or server credential.

Supported customer controls are test, pause, resume, category update, locale update, clear error,
disconnect, and reconnect. `STOP`, `UNSUBSCRIBE`, `CANCEL`, `END`, and `QUIT` immediately opt out and
cancel safe-to-cancel queued WhatsApp rows. `START` does not silently reactivate delivery; it sends
the user to authenticated dashboard re-consent.

## Message Categories

| Category | Intended events |
|---|---|
| `account` | connection confirmation/test and important account notices |
| `subscription` | trial, entitlement, and payment-access updates |
| `compliance` | Sharia status and safety-hold changes |
| `evidence` | Passport and evidence-freshness updates |
| `watchlist_health` | Watch Plan paused/degraded/unavailable notices |
| `operational` | integration and provider failures |
| `lifecycle` | selected lifecycle-state updates |
| `opportunity` | confirmed research events, separately disabled by default |

Delivery requires an active verified connection, active consent, selected category, selected
generic channel, allowed schedule/frequency, no applicable mute or compliance block, and an open
service window or approved configured template. No template requirement is bypassed with arbitrary
text.

## Template Registry

| Event | Category | Body variables in order |
|---|---|---|
| `connection_confirmation` | account | `display_name`, `settings_url` |
| `connection_test` | account | `display_name`, `settings_url` |
| `account_notice` | account | `notice_title`, `dashboard_url` |
| `trial_update` | subscription | `trial_state`, `billing_url` |
| `subscription_update` | subscription | `subscription_state`, `billing_url` |
| `compliance_change` | compliance | `asset`, `status`, `methodology`, `passport_url` |
| `evidence_update` | evidence | `asset`, `evidence_state`, `passport_url` |
| `watchlist_paused` | watchlist_health | `watchlist_name`, `reason`, `dashboard_url` |
| `integration_failure` | operational | `channel`, `reason`, `settings_url` |
| `lifecycle_update` | lifecycle | `symbol`, `state`, `monitor_name`, `lifecycle_url` |
| `confirmed_research_event` | opportunity | `symbol`, `timeframe`, `monitor_name`, `proof_url` |

Templates must use neutral decision-support wording. Do not include buy/sell instructions, exchange
execution links, urgency pressure, or profit promises. Detailed proof and market evidence belong at
the authenticated HilalMarkets URL. A configured name means only that the operator claims it is
approved; staging must verify the real WABA status before enabling it.

`lifecycle_update` and `confirmed_research_event` are both treated as market-opportunity events by
the transport policy. They require `WHATSAPP_OPPORTUNITY_ALERTS_ENABLED=true` and their configured
approved template even when an inbound service window is open.

## Delivery And Status State

1. `NotificationDispatcher` creates at most one WhatsApp `AlertDelivery` for an alert and
   `wa:<verified_wa_id>` destination.
2. `WhatsAppDeliveryService` claims due pending/retryable rows with database locking.
3. It renders from the immutable alert/proof record and creates a short-lived authenticated
   dashboard link.
4. Inside a valid service window it may send session text. Outside it, it sends only the registered
   template for that event/locale.
5. A successful API response stores the returned `wamid`, `accepted_at`, and provider status
   `accepted`.
6. Signed status webhooks independently record `sent`, `delivered`, `read`, and `failed`.

Provider progression is monotonic: `accepted < sent < delivered < read`. A late `sent` or failed
event cannot downgrade a delivered/read row. Duplicate event keys are ignored, while a different
status for the same `wamid` remains processable. Trial usage and candidate notification completion
advance only on the corresponding real delivery transition.

Retryable network, rate-limit, explicit transient, and eligible 5xx failures use bounded
exponential backoff and respect `Retry-After`. Credential, permission, recipient, payload, policy,
and rejected/missing-template failures are permanent unless Meta explicitly classifies them as
transient. Attempts stop at `WHATSAPP_MAX_DELIVERY_ATTEMPTS`.

## Conversation Parity Boundary

WhatsApp supports an interactive main menu, Watch Plan listing and safe pause/resume controls,
opportunity/lifecycle navigation, one-time market-check navigation, pricing, settings, support,
about, and authenticated dashboard links. More than three choices use a list message.

Complex setup interpretation, template selection, rule editing, approval, activation, billing,
support tickets, and full evidence views use authenticated dashboard handoffs. This preserves the
same application services and authorization gates without pretending that Telegram callback/edit
semantics exist on WhatsApp. WhatsApp never receives an approval or activation bypass.

## Worker Operations

Beat schedules:

- `ai_market_monitor.process_pending_whatsapp_webhooks` every 10 seconds;
- `ai_market_monitor.retry_whatsapp_deliveries` every 60 seconds;
- `ai_market_monitor.cleanup_whatsapp_webhook_receipts` daily.

Direct accepted events also enqueue `ai_market_monitor.process_whatsapp_webhook_event`. Disabled or
incompletely configured workers return a bounded disabled reason and make no provider request.

Monitor:

- latest `IntegrationHealth` for `integration=whatsapp`, including `webhook` and per-user scopes;
- pending/retryable/permanent WhatsApp deliveries and attempts;
- pending/failed webhook receipts and receipt age;
- provider error code classes without raw payloads;
- token/permission, template, recipient/window, WABA, and phone-number mismatch errors;
- time since the last signed webhook and successful delivery.

## Staging Smoke Test

1. Apply Alembic head on a staging backup and confirm one head.
2. Start API, worker, scheduler, PostgreSQL, and Redis.
3. Verify the Meta callback GET challenge.
4. Connect a dedicated test customer from Dashboard > Integrations.
5. Confirm the dashboard never exposes the access token, App Secret, verify token, full number, or
   `wa_id`.
6. Send MENU, open a list item, pause, resume, and test delivery.
7. Confirm API acceptance stores a real `wamid`, then observe sent/delivered/read webhooks.
8. Test an outside-window account event with an actually approved template.
9. Send STOP and verify queued unsent rows are canceled and no future alert is enqueued.
10. Replay link and webhook events; verify no duplicate connection, message, or delivery.
11. Rotate the access token and App Secret in staging and repeat signed webhook plus delivery tests.
12. Leave opportunity alerts disabled until template and policy approval are independently recorded.

Never run CI against Meta. Automated tests use injected `httpx` transports and signed local
fixtures.

## Secret Rotation

1. Create the replacement token/secret in Meta without revoking the active one prematurely.
2. Update the deployment secret store, restart the relevant services, and run a controlled test.
3. For App Secret rotation, coordinate webhook signatures so no accepted event is lost during the
   cutover.
4. Revoke the old credential in Meta only after the new path is observed healthy.
5. Record actor, time, reason, smoke-test result, and rollback point outside source control.

The database stores no access token, App Secret, or verify token.

## Troubleshooting

| Symptom | Check |
|---|---|
| GET verification is 403 | Exact verify token and `hub.mode=subscribe`; tokens are case-sensitive. |
| POST is 401 | Raw-body `X-Hub-Signature-256`, App Secret, and proxy body preservation. |
| POST is 400 | WABA ID, phone-number ID, object type, and event shape. |
| Link does not connect | Token expiry/cancellation/replay and whether the sending number exactly matches E.164 input. |
| API returns 401/190 | Access token validity and deployment secret rotation. |
| API returns 403/10/200 | App/system-user permissions and WABA/phone assignment. |
| Template error | Approved name, locale, variable order/count, category, and WABA ownership. |
| Recipient/window error | Recipient eligibility, opt-in, service window, or required approved template. |
| Dashboard remains pending | Worker availability, pending receipt age, Redis, and signed inbound event authority. |
| Late status appears inconsistent | Inspect recorded observed statuses; generic delivery state intentionally cannot regress. |

When Meta data is unavailable, report unavailable. Do not estimate provider state or mark a test
successful without a successful API result and matching stored evidence.
