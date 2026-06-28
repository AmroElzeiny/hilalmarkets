# Trading Concept Logic Audit

Date: 2026-06-27

## Registry Scope

- Total concepts: 473
- Beta-visible executable concepts: 330
- Hidden provider-required concepts: 142
- Hidden unsupported concepts: 1

The executable set is now constrained to concepts where the compatibility checker reports `availability == "available"`. Provider-required concepts are hidden from normal UI and prompt executable candidate sets.

## Audit Verdict

- RSI, moving-average, MACD, volume, VWAP, volatility, candle-pattern, price-action, market-structure, liquidity, time/session, logic-operator, and OHLCV-based finder conditions remain beta-visible.
- Entry, stop, target, and reward-to-risk are optional context and must not be assumed when the prompt does not request them.
- Provider-required concepts are not beta-visible by default.

## Family Checks

### RSI

- Status: `LOGIC_OK`
- Threshold and crossing concepts use explicit comparators.
- Exits oversold/overbought must use crossing direction, not static level only.
- Divergence stays deterministic through confirmed pivots only.

### MACD

- Status: `LOGIC_OK`
- Line/signal crosses, histogram flips, and histogram slope are deterministic indicator outputs.
- Proof must show actual MACD line/signal/histogram values.

### Moving Averages

- Status: `LOGIC_OK`
- Price above/below EMA/SMA uses close against selected MA.
- Crossovers compare current and previous closed values.
- Retests and distance-percent rules must show timeframe and warmup.

### Volume

- Status: `LOGIC_OK`
- Volume ratio/spike/dry-up compare against explicit historical windows.
- 24h quote-volume filters use market metadata or candle-derived fallback when available.

### VWAP

- Status: `LOGIC_OK`
- Reclaim and deviation checks use deterministic VWAP from OHLCV.
- Proof must include required vs actual value.

### Volatility

- Status: `LOGIC_OK`
- ATR, ATR percent, Bollinger squeeze/touch/re-entry, range expansion, and contraction use closed-candle windows.
- Incomplete warmup becomes pending/unavailable.

### Candle Patterns

- Status: `LOGIC_OK`
- Bullish/bearish engulfing, hammer, shooting star, doji, inside/outside bar, pin bar, strong-close, consecutive candle color, previous candle state, and negation are deterministic.
- `NOT` conditions must invert proof without hiding the inner condition evidence.

### Price Action

- Status: `LOGIC_OK`
- Breakouts and breakdowns use closed-candle lookback windows; historical windows must respect prompt period.
- Pullback depth, HH/HL/LH/LL, impulse, consolidation, and break/retest require explicit lookback parameters.

### Market Structure / Liquidity

- Status: `NEEDS_DETERMINISTIC_DEFINITION` for any newly added smart-money aliases.
- Current visible sweep/FVG/order-block style concepts must keep deterministic definitions in proof.
- Subjective aliases should be hidden or clarified before activation.

### Time / Session

- Status: `LOGIC_OK`
- NY session, midnight UTC, day/week/month logic, previous candle, and weekday filters require timezone-aware timestamps.
- User timezone settings must be applied before rendering alert/history times.

### Logic Operators

- Status: `LOGIC_OK`
- AND/OR/NOT/sequence/within-last/persisted-for/count/cooldown style operators are supported by schema.
- Optional conditions do not block required-condition completion.

### Risk / Trade Quality

- Status: `BETA_VISIBLE_ONLY_WHEN_EXPLICIT`
- Risk fields are optional and preserved only if requested.
- No fake RR is created when entry/stop are absent.

### Provider-Required Concepts

- Status: `HIDE_FROM_BETA`
- 142 concepts are hidden until real providers, proof receipts, and tests exist.

## Tests Covering This Audit

- `tests/engine/test_capability_registry_compatibility.py`
- `tests/engine/test_capability_template_schema_evaluator_alignment.py`
- `tests/services/test_provider_required_blocking.py`
- `tests/unit/test_condition_registry.py`
- `tests/unit/test_on_demand_scans.py`
- `tests/unit/test_lifecycle.py`
- `tests/unit/test_fixture_market_data.py`

## Remaining Manual Work

- Run the full prompt matrix after any alias/catalogue expansion.
- Review all subjective smart-money wording before beta copy is finalized.
- Reclassify provider-required concepts one-by-one only after adapter/proof tests pass.
