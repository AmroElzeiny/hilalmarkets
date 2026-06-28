# Unique Value Plan

TraceEdge should not compete as another alert builder. The product should become a
strategy monitoring cockpit.

Core differentiation:

> Prompt -> coverage report -> visual strategy map -> market-wide monitor -> proofed alert -> diagnostics.

## Unique Bundle

### 1. Prompt Coverage Score

User sees whether TraceEdge understood the setup.

Implementation status:

- Implemented foundation in `engine/prompt_audit.py`.
- Shown in Strategy Builder preview and Coverage panel.

Next:

- Add admin prompt-test harness.
- Track common missed fragments.
- Turn repeated feedback into test cases.

### 2. Source-Linked Conditions

Every condition should show the exact prompt phrase that created it.

Implementation status:

- `ConditionRule.source_fragment` exists.
- Condition drawer and Coverage panel surface it.

Next:

- Highlight source phrase visually in prompt preview.
- Add "this source mapping is wrong" feedback per condition.

### 3. Strategy Health Preview

The system should say whether a monitor is too strict, too broad, noisy, silent, or incomplete.

Implementation status:

- Validation checklist and local warnings exist.
- Cockpit health direction exists.

Next:

- Add a health score before activation.
- Estimate likely match frequency from preview scans.
- Warn when all conditions are mandatory and too narrow.

### 4. Condition Bottleneck Map

Show which condition blocks the setup most often.

Implementation status:

- Cockpit and lifecycle data can support this.

Next:

- Persist per-condition pass/fail counts by strategy version.
- Show "top blocker" in Strategy Builder preview.
- Add "relax this condition" suggestions as visual diffs only.

### 5. Missed Move Analyzer

User asks: "Why did this move happen without an alert?"

Implementation status:

- Forensic investigation direction exists.
- Proof/lifecycle storage foundations exist.

Next:

- Make the flow first-class in dashboard and Telegram.
- Show data availability, strategy active status, cooldown, skipped symbols, and failed conditions.

### 6. Setup Lifecycle

Track setup state from detection to outcome.

Implementation status:

- Lifecycle models and dashboard direction exist.

Next:

- Make lifecycle cards the primary monitoring surface.
- Remove expired cards from active lifecycle view.
- Add condition milestones to each card.

### 7. Proof Receipt

Every alert should show required value, actual value, status, exchange, timeframe, timestamp,
data freshness, strategy version, and chart reference.

Implementation status:

- Proof receipt foundation exists.

Next:

- Standardize proof rendering across dashboard, Telegram, and Discord.
- Add proof download/export.

### 8. False Alert Trainer

User feedback should generate suggestions, not silently mutate strategy rules.

Implementation status:

- Alert feedback exists.
- Builder interpretation feedback is now audited.

Next:

- Convert repeated feedback into suggested diffs.
- Require explicit approval before changing rules.

### 9. Strategy Version Comparison

Compare old and new versions without live trading claims.

Implementation status:

- Versioning exists.

Next:

- Build visual diff for conditions, universe, risk, alerts, and expected match count.

### 10. Monitoring-First Safety

No exchange trading keys. No execution. No profit guarantees.

Implementation status:

- Product architecture is monitoring-first.

Next:

- Make this a visible trust message in onboarding, checkout, dashboard, and bot flows.

## Final Unique Claim

TraceEdge is strongest when it answers:

- What did my prompt become?
- What exactly is being monitored?
- What is forming now?
- Why did this alert fire?
- Why did this setup stay silent?
- Which condition blocks my strategy most often?

That is more defensible than "AI creates alerts."
