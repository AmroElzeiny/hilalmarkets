# Capability Coverage Console

## Purpose

The Capability Coverage Console is a protected administrator dashboard at
`/system-brain`. It turns real AI Setup Chat resolution evidence into an
operational view of where TraceEdge understands users well and where the
capability registry needs review.

The console reports:

- common unmatched fragments and unsupported keywords;
- supported prompts whose top registry match was below 80% confidence;
- capability clarifications selected by users;
- false top-candidate rankings where the user selected another capability;
- provider-blocked requests;
- capabilities with thin alias or negative-example coverage;
- proposed aliases awaiting administrator review;
- template/evaluator/availability regression status for every capability;
- non-excluded registered email users;
- chat, monitor, scan, alert and resolver totals;
- OpenAI token usage and estimated cost grouped by model and reasoning effort.

## Access Security

Access requires two independent steps:

1. The configured administrator username and password.
2. A short-lived six-digit code delivered to that administrator email through
   the configured SMTP adapter.

The password is never stored in plaintext. Store only the output of
`hash_password` in `SYSTEM_BRAIN_ADMIN_PASSWORD_HASH`. OTP challenges and
sessions are database-backed, expiring, rate-limited and audited. Session
cookies are HTTP-only, SameSite Strict, and Secure in staging/production.

Required variables:

```env
SYSTEM_BRAIN_ADMIN_USERNAME=contact@trace-edge.com
SYSTEM_BRAIN_ADMIN_PASSWORD_HASH=<pbkdf2 hash>
SYSTEM_BRAIN_OTP_TTL_MINUTES=10
SYSTEM_BRAIN_OTP_MAX_ATTEMPTS=5
SYSTEM_BRAIN_SESSION_HOURS=8
SYSTEM_BRAIN_LOGIN_ATTEMPTS_PER_15_MINUTES=5
EMAIL_ADAPTER=smtp
SMTP_HOST=smtp.resend.com
SMTP_PORT=2587
SMTP_USERNAME=resend
SMTP_PASSWORD=<provider credential>
SMTP_FROM_EMAIL=no-reply@trace-edge.com
SMTP_FROM_NAME=TraceEdge
```

Generate a new password hash locally:

```powershell
.venv\Scripts\python.exe -c "from ai_market_monitor.core.security import hash_password; print(hash_password('replace-this-password'))"
```

Copy only the printed hash to the protected VPS environment file.

## Telemetry Semantics

`CapabilityResolutionEvent` records a resolver decision per chat message and
fragment. Candidate keys, confidence, unknown terms and provider availability
are retained so ranking quality can be audited. A user selection updates the
event with the immutable selected `capability_key`.

When the selected capability differs from the first-ranked candidate, the
system creates or increments a pending `CapabilityAliasProposal`. Approval in
the console does not mutate executable registry code at runtime. It records an
audited review decision for inclusion in a tested registry release. This keeps
the deterministic engine reproducible.

`AIUsageEvent` stores token counts returned by OpenAI. Costs use the centrally
configured `OPENAI_MODEL_PRICING_USD_PER_MILLION` map. Cached input is priced
separately. Reasoning tokens are displayed for diagnosis but are already part
of output tokens and are not charged twice.

## Deployment

Run the migration before opening the console:

```bash
docker compose exec api alembic upgrade head
```

Then restart the API container after setting the environment variables. The
public route is `https://app.hilalmarkets.com/system-brain` when the application
is served at that host. If the desired public host is exactly
`https://hilalmarkets.com/system-brain`, route that path to the FastAPI service in
the reverse proxy.

## Operational Notes

- Empty panels mean no evidence has been recorded since this migration.
- Registered-user exclusions are normalized case-insensitively.
- The console never exposes OpenAI, SMTP, Telegram or provider credentials.
- All responses use `Cache-Control: no-store` and deny iframe embedding.
- Alias reviews and login/session events are written to `audit_events`.

## Verification

Implemented on 2026-07-12 and verified with:

```powershell
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m pytest tests/unit/test_system_brain.py tests/integration/test_system_brain_web.py tests/unit/test_ai_setup_chat.py tests/unit/test_capability_resolver.py tests/integration/test_ai_setup_chat_api.py -q
```

Results:

- Alembic upgraded to `9c0d1e2f3a4b`.
- Full suite: 1,716 passed.
- Final focused console/chat/resolver run: 54 passed.
- Ruff and `git diff --check`: passed; Git reported only existing line-ending notices.
