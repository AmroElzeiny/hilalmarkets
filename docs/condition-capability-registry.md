# Condition Capability Registry

`ai_market_monitor.engine.condition_registry` is the source of truth for condition discovery in
the Visual Strategy Builder and notification-channel entry points.

The dashboard endpoint is:

`GET /api/v1/dashboard/capabilities`

Registry version `2.0` exposes:

- Unique capability keys and searchable prompt aliases.
- Categories, descriptions, supported markets and timeframes.
- Required market data and deterministic warm-up requirements.
- Parameter definitions, defaults, outputs and supported comparators.
- Plain-English examples and visual-card sentences.
- Implementation status and evaluator function names.
- A validated `condition_template` ready to insert into a strategy tree.
- Parameterized logic-operator definitions.

## Logic Operators

The deterministic tree supports:

- `and`
- `or`
- `not`
- `sequence`
- `within_last`
- `persisted_for`
- `count_of`
- `cooldown_condition`
- `first_time_true`
- `changed_state`
- `cross_with_confirmation`
- `conditional_branch`

Temporal operators evaluate only candles visible at the requested evaluation time. They reuse the
same closed-candle filtering as normal conditions, preventing look-ahead bias. The live scanner
supplies persisted last-alert timestamps for cooldown evaluation.

## Canonical Indicator Rule

Every indicator is calculated in `engine/indicators.py`. Multi-output studies use a `component`
parameter so the rule engine still receives one deterministic scalar value per operand.

Examples:

- `stochastic_rsi` with component `k` or `d`.
- `ichimoku_cloud` with component `tenkan`, `cloud_top`, or `price_above_cloud`.
- `directional_movement` with component `plus_di`, `minus_di`, `dx`, `adx`, or `adxr`.
- `squeeze_detection` with component `squeeze_on`, `squeeze_fired`, or directional fire state.

## Deduplication

The extension deliberately reuses existing implementations for SMA, EMA, ATR, volume ratio, RSI,
MACD, Bollinger Bands, Bollinger bandwidth, Stochastic, VWAP, ADX, NOT, and SEQUENCE.

The registry payload reports these under `deduplication.already_present`. New keys are validated for
uniqueness during application import.

## Compatibility

Condition templates produce the existing `ConditionRule` schema, so they work with:

- Deterministic live scans.
- Quick Scan and recent-market preview.
- Proof receipts and forensic reconstruction.
- Forward testing.
- Visual builder edits and AI prompt interpretation.

Historical replay remains hidden at the product level, but the same evaluator contract can be used
by future replay workers without a separate indicator implementation.
