# Capability E2E Fixes

Date: 2026-06-27

This audit is generated from the live capability registry, condition templates,
StrategyDefinition validation, evaluator compatibility checks, and prompt
reachability tests. The complete row-by-row matrix is in
`TRADING_CONCEPT_E2E_MATRIX.md`.

## Current Status

- Total concepts: 473
- GREEN: 330
- PROVIDER_REQUIRED: 142
- PLANNED: 1
- YELLOW: 0
- RED: 0

## Concepts Fixed

- Liquidity sweep wording now maps to evaluator-supported price action names:
  bullish sweep uses `sell_side_liquidity_sweep`; bearish sweep uses
  `buy_side_liquidity_sweep`.
- Market-structure aliases now map to evaluator-supported operands:
  `market_structure_shift_bullish`, `market_structure_shift_bearish`,
  `equal_highs_liquidity_pool`, and `equal_lows_liquidity_pool`.
- Range, retest, support, and resistance prompts now map to canonical evaluator
  names such as `breakout_from_consolidation`,
  `breakdown_from_consolidation`, `break_and_retest_confirmed`,
  `price_bounces_from_support`, and `price_rejects_resistance`.
- Moving-average retest now maps to `pullback_to_ema`.
- Volatility contraction, range expansion, impulse candles, and consolidation
  concepts now use supported price-action operands instead of orphan names.
- Session/time-window prompts now use market-filter operands instead of fake
  price-action operands.
- Swing-state conditions now support `higher_high`, `higher_low`,
  `lower_high`, and `lower_low` in the price-action evaluator.
- OBV, CMF, and other catalogue-matched rules now preserve the user's matched
  source clause instead of a generic catalogue description.
- Price-threshold parsing now avoids stealing numeric values from unrelated
  market-cap, funding, open-interest, or volume-provider phrases.

## Concepts Made Provider-Required

The following categories require external or runtime context that is not a
plain OHLCV candle stream. They may be visible as draft/provider-required but
must block mandatory live activation until the provider is configured.

- Market capitalization and token-category filters.
- Cross-market BTC/ETH context.
- Crypto index context such as TOTAL, TOTAL2, TOTAL3, BTC dominance, and USDT
  dominance.
- Macro market context such as DXY, SPX, Nasdaq, gold, US 10Y, and VIX.
- Market breadth.
- Token-sector or narrative categories.
- News, unlock, listing, delisting, economic-calendar, and event-feed rules.
- Order-book, spread, depth, wall, trade-count, and aggressive-flow rules.
- Derivatives data such as liquidations, open interest, and funding.
- Universe ranking concepts that require global ranking providers.
- Risk-context rules that require post-proof obstacle, slippage, latency, clean
  path, or persisted lifecycle context.
- Alert-behavior and setup-lifecycle runtime-context conditions.

Provider placeholders remain intentionally explicit. The system must not fake
these values or silently turn them into executable candle-only rules.

## Concepts Hidden Or Planned

- `pivot_high_low` is the only PLANNED concept. It is retained in admin/audit
  context but hidden from normal executable add paths.

## Concepts Downgraded

No currently available concept had to remain downgraded to YELLOW or RED after
this pass. Concepts that could not be proven through evaluator support were
either remapped to supported operands or classified as provider-required/planned.

## Future Provider Work

To turn provider-required concepts GREEN, add provider adapters and proof
evidence for:

- Market cap and token categories.
- Cross-symbol/cross-index market data.
- Macro/index feeds.
- Event/news/economic calendar feeds.
- Order-book and trade tape streams.
- Derivatives feeds.
- Universe ranking snapshots.
- Persisted runtime alert/lifecycle context checks.

