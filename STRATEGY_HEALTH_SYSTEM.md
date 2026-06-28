# Strategy Health System

## Product Boundary

TraceEdge monitors user-approved crypto spot strategies. It does not place trades, predict
profitability, or describe Edge Health as a win probability.

## Shared Evidence Flow

1. An approved `StrategyVersion` is evaluated by the deterministic scanner.
2. `ScanResult`, `SetupConditionResult`, `SetupInstance`, and `SetupLifecycleEvent` preserve
   the evidence used by the scanner.
3. Confirmed, forming, lifecycle, suppressed, and reliability events produce proof-backed
   `Alert` records.
4. The strategy cockpit derives health, bottlenecks, forecasts, decay, feedback patterns,
   replay investigations, and inbox items from those records.
5. Suggestions are stored as visual schema diffs. Applying one creates a draft version only.

## Feature Connections

### Edge Health

`StrategyCockpitService.edge_health` produces a 0-100 score from:

- data coverage and freshness
- alert frequency health
- condition pass health and the bottleneck map
- lifecycle completion evidence
- user alert-quality feedback
- proof completeness
- silent-monitor and spam risk

Snapshots are stored in `edge_health_snapshots`. The score includes component explanations,
the main issue, a safe review action, and historical trend points.

### Condition Bottlenecks

Condition outcomes are aggregated from `setup_condition_results` and their historical
`scan_results`. The result includes pass, fail, pending, unavailable, and error counts,
plus blocking impact for otherwise-close evaluations.

### Feedback Trainer

Dashboard, Telegram, and Discord feedback writes `user_feedback` linked to the alert and setup.
Three repeated corrective feedback events can create a draft `StrategySuggestion`. No rule is
changed automatically.

### Setup Lifecycle And Replay

Persistent setup instances support detected, forming, armed, confirmed, alert sent,
suppressed, blocked, data unavailable, entry, invalidation, expiration, target, stop, and
closure states. Lifecycle events and condition state changes form the setup timeline.

### Missed Move Analysis

A missed-move request creates `MissedMoveAnalysis`, an internal replay job, timeline evidence,
condition classifications, universe checks, and a non-predictive explanation.

### Forecast, Validation, And Universe

- Alert frequency uses stored historical matches when available and a cautious structural
  estimate otherwise.
- Conflict validation detects duplicates, contradictory thresholds, provider requirements,
  excessive cooldowns, broad or overly strict maps, and missing delivery/risk requirements.
- Universe preview applies static spot-universe rules immediately and identifies provider
  metadata filters that remain enforced during live scanning.

### Version Experiments

Experiments compare schema differences and stored behavior across strategy versions. Promotion
requires explicit confirmation and preserves the audit trail.

### Strategy Decay

The decay detector compares the recent seven-day window with a prior 28-day baseline. It
detects long silence, alert spikes, data deterioration, and universe shrinkage. Hourly worker
evaluation stores events and can create one dashboard health summary per monitor per week.

### Alert Quality Inbox

The inbox materializes alerts, feedback reviews, missed-move analyses, suggestions, decay
events, suppressed alerts, and data issues into one review center. Items support filters,
review/archive actions, labels through the bulk API, proof links, and setup timelines.

## Determinism And Safety

- Indicator and condition values always come from the rule engine or stored scan evidence.
- LLM output is never accepted as market evidence.
- AI-style improvement actions produce schema-valid drafts and reasons.
- Critical strategy conflicts block publication.
- Existing strategy versions and setup instances remain immutable historical references.
