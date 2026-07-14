# MT5 PO3 Feature Import Audit

Date: 2026-07-10

Source folders inspected:

- `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Experts\MT5_PO3_Codex`
- `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex`

## Imported As Research Features

The following deterministic market-reading concepts were imported or tightened in TraceEdge:

- PO3 dealing-range sweep:
  - `po3_dealing_range_sweep_bullish`
  - `po3_dealing_range_sweep_bearish`
- PO3 sweep followed by displacement:
  - `po3_sweep_displacement_bullish`
  - `po3_sweep_displacement_bearish`
- PO3 sweep, displacement, and structure break:
  - `po3_sweep_displacement_structure_bullish`
  - `po3_sweep_displacement_structure_bearish`
- FVG lifecycle states:
  - `fvg_still_open_bullish`
  - `fvg_still_open_bearish`
  - `fvg_virgin`
  - `fvg_touched`
  - `fvg_mid_mitigated`
  - `fvg_fully_mitigated`
  - `fvg_structure_invalidated`
- Indicator adapters:
  - `trend_strength`
  - `expansion_ratio`
  - `anchored_vwap`

These are connected through:

- Deterministic evaluator: `src/ai_market_monitor/engine/price_action.py`
- Indicator registry: `src/ai_market_monitor/engine/indicators.py`
- Capability catalogue: `src/ai_market_monitor/engine/capabilities.py`
- Prompt vocabulary: `src/ai_market_monitor/engine/prompt_vocabulary.json`
- Generic vocabulary conversion: `src/ai_market_monitor/engine/prompt_semantics.py`

## Existing TraceEdge Features Kept

These already existed and were not duplicated:

- Sell-side and buy-side liquidity sweeps.
- Equal highs and equal lows liquidity pools.
- Session high and session low sweeps.
- Daily and weekly high/low sweeps.
- Break of structure / CHoCH-style market structure shifts.
- Displacement candles.
- FVG detection, price entering FVG, price filling FVG, FVG rejection, midpoint touch.
- Support retest and resistance rejection.
- ATR, VWAP, volume ratio, ADX, Bollinger squeeze, and many other indicator families.

## Intentional Non-Imports

The MT5 folders contain a large amount of execution-EA logic. These were intentionally not imported into TraceEdge as default user-facing monitor features:

- Order placement and pending-order management.
- Position sizing and broker execution safety.
- AI trade approval gates.
- Trade bucket risk policy.
- Stop-loss and take-profit automation.
- Rollover order freeze handling.
- "Only breaker retest virgin strong origin" execution filters.

Reason: TraceEdge is a research-monitoring product. These concepts are either execution-only, account-specific, or would imply automated trading behavior.

## Conceptual Corrections

- "Bullish sweep" maps to sell-side liquidity being taken: price breaches prior lows and closes back inside.
- "Bearish sweep" maps to buy-side liquidity being taken: price breaches prior highs and closes back inside.
- PO3 confirmation is modeled as condition completion: sweep, displacement, and optional structure break. It does not imply an entry order.
- FVG state is monitored as market evidence. It is not treated as an automatic entry zone.

## Verification

Added focused tests in:

- `tests/unit/test_mt5_po3_imported_features.py`

Covered:

- PO3 dealing-range sweep.
- PO3 sweep plus displacement.
- PO3 sweep plus displacement plus structure break.
- FVG virgin, touched, filled, and still-open states.
- Indicator registry support for trend strength, expansion ratio, and anchored VWAP.
- Prompt conversion for imported MT5 terminology.
