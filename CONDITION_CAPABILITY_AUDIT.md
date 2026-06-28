# Condition Capability Audit

## Verification Result

- Registry capabilities: **473**
- Beta-visible deterministic concepts: **330**
- Hidden provider-required concepts: **142**
- Hidden unsupported concepts: **1**
- Schema-valid builder templates: verified through the compatibility/template alignment tests.
- Full pytest suite: **199 tests passed**
- Ruff: passed for source, tests, and migrations.
- Fresh Alembic migration: passed through revision `3c4d5e6f7a8b`.

Private-beta policy update on 2026-06-27:

- Normal builder payloads now expose only concepts with `availability == "available"`.
- Provider-required concepts are hidden from normal UI and prompt executable paths until a real
  adapter, rate-limit handling, proof support, and tests are configured.
- Old saved strategies containing mandatory provider-required conditions still load safely but
  block activation with unavailable proof.
- See `PROVIDER_REQUIRED_CONCEPTS_AUDIT.md`, `PROVIDER_SOURCES_RESEARCH.md`, and
  `PROVIDER_ENV_PLACEHOLDERS.md`.

## Provider Families Requiring Beta Gating

The sections below describe intended/provider-capable families from earlier implementation work.
For private beta, these families remain hidden unless the current registry payload marks the
specific concept `available`.

### Public Exchange Data

The following families require explicit adapter/proof verification before normal UI exposure:

- **cross_market:** BTC and ETH trend filters, relative performance, BTC correlation, beta,
  relative volatility, and relative move.
- **market_breadth:** EMA 50/200 breadth, positive 24-hour breadth, new-high breadth, volume
  breadth, breadth thrust, improving breadth, and deteriorating breadth.
- **order_book:** spread, bid/ask depth, depth imbalance, walls, wall changes, slippage,
  trade-count changes, average-trade-size changes, buy/sell imbalance, and short-window volume.
- **derivatives:** funding rate, open-interest direction, and price/open-interest combinations
  where the selected exchange exposes public contract data.
- **universe_ranking:** volume, relative volume, momentum, volatility, trend strength,
  EMA distance, high/low proximity, expansion, compression, breakout, pullback, and
  BTC-relative-strength ranking.

Universe ranking now affects live scanner ordering and the condition itself filters symbols
outside the requested percentile.

### Configurable External Context

The following families execute through a strict HTTP context-provider contract:

- **crypto_index:** TOTAL, TOTAL2, TOTAL3, BTC/USDT/stablecoin dominance, alt-market-cap,
  altseason, and crypto risk-on/risk-off context.
- **macro_market:** DXY, SPX, NASDAQ, gold, US 10Y yield, and VIX trend filters.
- **event_feed:** listings, delistings, token unlocks, launches, upgrades, governance, exploits,
  depegs, institutional or regulatory news, CPI, FOMC, Fed, NFP, GDP, calendar events, and
  forecast surprise conditions.
- **token_categories:** AI, DeFi, meme, layer 1, gaming, exchange-token, and relative category
  trend conditions.
- **derivatives enrichment:** liquidation spikes or provider-specific derivatives values not
  exposed by the exchange's standard public CCXT methods.

The endpoint receives:

```json
{
  "category": "event_feed",
  "requested_keys": ["cpi_event_window"],
  "exchange": "binance",
  "symbol": "SOL/USDT",
  "timeframe": "15m",
  "quote_assets": ["USDT"],
  "evaluated_at": "2026-06-25T12:00:00Z"
}
```

It must return condition-ready deterministic values:

```json
{
  "values": {
    "cpi_event_window": true
  },
  "as_of": "2026-06-25T12:00:00Z"
}
```

Only requested scalar values are accepted. Missing, invalid, late, or unreachable provider data
becomes an `unavailable` condition proof.

## Completed Internal Conditions

The previously recognized-only internal conditions are implemented:

- `btc_trend_filter`
- `correlation_filter`
- `eth_trend_filter`
- `fibonacci_extension_targets`
- `fibonacci_retracement_zone`
- `golden_pocket_zone`
- `market_cap_minimum`
- `meme_coin_exclusion`
- `previous_session_high_low`
- `rsi_divergence`

Deterministic definitions:

- Fibonacci zones use configured closed-candle swing lookbacks.
- Fibonacci target validation checks the configured first target against common extensions.
- RSI divergence pairs confirmed price pivots with RSI values at those pivots.
- Previous-session levels use configured session hours and timezone.
- Market cap and token categories use configured provider metadata and never infer missing tags.

## Completed Risk-Quality Conditions

Risk geometry is now calculated before the main condition tree, allowing the following conditions
to block the same evaluation:

- stop distance in ATR units, too tight, or too wide
- next support or resistance distance
- R multiple and clean path before an obstacle
- liquidity obstacle or target overlap
- price distance from trigger and candle overextension
- spread, volatility, setup age, and invalidation availability
- reward-to-risk after fees or slippage
- alert lateness and data latency
- minimum candle liquidity

The normal strategy risk validation still remains mandatory and is included separately in every
proof receipt.

## Completed Runtime Conditions

The live scanner supplies persisted runtime context for:

- same-symbol and same-strategy cooldowns
- hourly and daily alert budgets
- state-change-only alerts
- maximum alert lateness
- setup state, age, first-detected window, entry-zone activity, invalidation, and expiry
- time since the last alert, setup detection, or a specific condition first became true

`ConditionRuntimeState` now stores every condition's last state, first true timestamp, last true
timestamp, consecutive true count, actual value, and last evaluation time. This works even when
no setup instance or alert was created.

Migration:

- `alembic/versions/3c4d5e6f7a8b_add_condition_runtime_states.py`

## Provider Placeholders

Placeholders were added to both `.env` and `.env.example`:

```text
CRYPTO_INDEX_API_URL=
CRYPTO_INDEX_API_KEY=
MACRO_MARKET_API_URL=
MACRO_MARKET_API_KEY=
EVENT_FEED_API_URL=
EVENT_FEED_API_KEY=
TOKEN_CATEGORY_API_URL=
TOKEN_CATEGORY_API_KEY=
DERIVATIVES_CONTEXT_API_URL=
DERIVATIVES_CONTEXT_API_KEY=
CONTEXT_PROVIDER_TIMEOUT_SECONDS=15
CONTEXT_FETCH_CONCURRENCY=8
MARKET_BREADTH_MAX_SYMBOLS=100
```

Existing optional metadata placeholders remain responsible for market cap and token category
enrichment:

```text
MARKET_METADATA_API_URL=
MARKET_METADATA_API_KEY=
```

## Deterministic Approximation Notes

- `volume_profile_proxy` remains an OHLCV volume-bin approximation, not true exchange volume
  profile.
- `buy_sell_pressure_proxy` remains based on candle close location, not aggressor-side trades.
- Order-book buy/sell metrics use public trade-side labels where available.
- FVG, order-block, smart-money, swing-strength, trendline, Fibonacci, and structure conditions
  use documented deterministic rules and do not claim institutional intent.
- Breadth and ranking use at most `MARKET_BREADTH_MAX_SYMBOLS` symbols per cached evaluation
  bucket to control provider load.
- Custom sessions and calendar rules are timezone-aware; UTC remains the default.

## Partially Implemented

**None.**

External feeds may still require a URL, API key, subscription, or exchange support, but the
execution, validation, proof, failure handling, and configuration paths are complete.

## Deliberately Rejected

- Automated trade placement or exchange order execution.
- Wallet seed phrases, private keys, withdrawal permissions, or remote access.
- News-derived buy/sell recommendations or unverified sentiment predictions.
- Guaranteed-profit, future-price prediction, or AI-invented market values.
- Treating missing provider data as a passing condition.
- Futures-only values when neither exchange public data nor the configured derivatives provider
  can supply them.

## Main Files

- `src/ai_market_monitor/provider_context.py`
- `src/ai_market_monitor/engine/capabilities.py`
- `src/ai_market_monitor/engine/context_conditions.py`
- `src/ai_market_monitor/engine/evaluator.py`
- `src/ai_market_monitor/engine/price_action.py`
- `src/ai_market_monitor/engine/risk.py`
- `src/ai_market_monitor/services/market_preview.py`
- `src/ai_market_monitor/services/scanner.py`
- `src/ai_market_monitor/services/on_demand_scans.py`
- `src/ai_market_monitor/services/dashboard_jobs.py`
- `src/ai_market_monitor/services/interpreter.py`
- `src/ai_market_monitor/db/models/monitoring.py`
- `src/ai_market_monitor/core/config.py`
