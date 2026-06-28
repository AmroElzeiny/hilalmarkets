# Platform Responsibilities

TraceEdge uses one shared backend and database. Strategies, subscriptions, trials,
alerts, deliveries, setup lifecycle records and support requests are not duplicated per channel.

## Dashboard / Website

The Dashboard is the control center and billing source of truth.

- Sign up, sign in and account management
- Billing, payment provider checkout, billing return pages and subscription management
- Trial status, usage and limits
- Full strategy builder, drafts, approvals and monitor settings
- Full Scan Market Now, Near-Miss Radar, alert history and proof viewer
- Why No Alert investigations
- Performance, analytics, settings, support and admin controls

## Telegram

Telegram is the personal fast-action assistant.

- Onboarding, trial claim and simple monitor creation
- Fast alerts, near-miss alerts and lifecycle updates
- Short proof summaries and quick Why No Alert prompts
- Quick scan entry points where quota allows
- Pause/resume, mute, support and settings shortcuts
- Secure Dashboard links for billing, full proof, analytics and complex editing

Telegram never collects payment details and does not own separate strategy or subscription state.

## Discord

Discord is optional and is not required for normal users.

- Personal alert delivery
- Community/creator delivery where plan entitlements allow it
- Per-setup threads, role sync and support channels
- Slash commands for quick status and secure Dashboard links

Discord never collects payment details and does not own separate strategy or subscription state.

## Capability Matrix

The code-level source of truth is `src/ai_market_monitor/core/platforms.py`.
