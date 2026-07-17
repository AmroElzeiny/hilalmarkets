# Setup Replay

Setup Replay is the user-facing replacement for "Why No Alert?"

Inputs:

- Strategy.
- Symbol.
- Approximate timestamp.
- Exchange and timeframe when needed.
- Optional user note.

Output:

- Whether the market was evaluated.
- Strategy version in effect.
- Data availability.
- Conditions passed and failed.
- Best near-miss moment.
- Whether filters, subscription limits, cooldowns, or delivery failures blocked an alert.
- Chart or replay reference.
- Suggested adjustments that require user approval before becoming rules.

Rules:

- Run deterministic reconstruction first.
- Do not let AI guess the reason.
- Show chart overlays and condition rows in the dashboard.
- Telegram may open secure dashboard links for full replay evidence.
