# Research Monitor Behavior

TraceEdge is a research-monitoring product. It does not place trades, recommend guaranteed entries, or require the user to provide trade execution details.

## Core Rule

Alert / match trigger = **100% of required monitored conditions pass**.

The deterministic evaluator now exposes this in proof receipts:

- `research_monitor: true`
- `monitor_mode: research` unless optional trade context was explicitly provided
- `match_rule: 100% of required monitored conditions must pass`
- `required_conditions_total`
- `required_conditions_passed`
- `required_completion_percent`
- `optional_conditions_total`
- `optional_conditions_passed`
- `optional_conditions_failed`
- `match_status`

## Entry And Risk Context

Entry, stop loss, take profit, reward-to-risk, and position sizing are optional trade context.

- They are preserved and validated when the user explicitly provides them.
- They are not required for Quick Scan.
- They are not required for publishing a research monitor.
- They should not be invented by the AI interpreter.
- They should not block a research match unless the user explicitly made them required.

## Optional Confirmations

Optional conditions can improve proof quality and Near-Miss scoring, but they do not block a confirmed research match when all required conditions pass.

## Provider-Required Conditions

Mandatory provider-required conditions block activation or scanning if the provider is not configured and tested. Optional provider-required conditions generate warnings and unavailable proof evidence rather than fake results.

## Preferred User-Facing Wording

Use:

- Research match confirmed
- Required conditions passed
- Conditions complete
- Optional confirmations
- No match
- Blocked by missing data
- Provider required
- Proof receipt

Avoid as the primary framing:

- Buy signal
- Sell signal
- Take trade
- Entry triggered
- TP/SL required
- RR required

## Lifecycle Defaults

Default research monitor lifecycle stages are:

1. Detected
2. Partial match
3. Conditions complete
4. Alert delivered
5. No longer matching

Legacy trade-context setup states still load, but default dashboard lifecycle wording no longer shows `Entry zone`.
