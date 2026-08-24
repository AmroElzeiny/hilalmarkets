# Condition Capability Audit

## Summary

- Registry capabilities: **502**
- Deterministically executable: **502**
- Deferred/provider/runtime dependent: **0**
- No trade execution, exchange trading keys, or AI-only signal outcomes were added.

## Already Existing Capabilities Skipped

- SMA, EMA, ATR, ATR percent, volume ratio, RSI, MACD, Bollinger Bands, Bollinger width/delta, Stochastic, VWAP, ADX, NOT, and SEQUENCE.
- higher/lower highs and lows, break of structure, change of character, liquidity sweeps, equal highs/lows, range breakout/breakdown, breakout retest, support/resistance retest, inside/outside bars, engulfing candles, hammer, shooting star, doji, pin bar, range expansion, volume spike/dry-up, time windows, spread/listing filters, and existing risk calculations.
- Existing keys were retained rather than duplicated; registry import validates key uniqueness.

## Newly Added Capabilities

The following keys are executable from OHLCV or timezone-safe runtime context:

`abandoned_baby_bearish`, `abandoned_baby_bullish`, `above_range`, `accumulation_distribution`, `all_time_high_breakout`, `ascending_triangle_breakout`, `asia_session`, `auto_channel_breakdown`, `auto_channel_breakout`, `auto_channel_lower_touch`, `auto_channel_upper_touch`, `avoid_daily_reset`, `avoid_low_liquidity_hours`, `bearish_candle`, `bearish_fair_value_gap`, `bearish_harami`, `bearish_order_block_candidate`, `below_range`, `belt_hold_bearish`, `belt_hold_bullish`, `bollinger_reentry`, `break_and_retest_confirmed`, `breakdown_from_consolidation`, `breakout_from_consolidation`, `breakout_with_volume_confirmation`, `breakout_without_volume_confirmation`, `breaks_n_candle_high`, `breaks_n_candle_low`, `bullish_candle`, `bullish_fair_value_gap`, `bullish_harami`, `bullish_order_block_candidate`, `buy_sell_pressure_proxy`, `buy_side_liquidity_sweep`, `candle_anatomy`, `chaikin_money_flow`, `choppiness_index`, `close_above_previous_day_high`, `close_above_previous_week_high`, `close_below_previous_day_low`, `close_below_previous_week_low`, `closes_above_n_candle_high`, `closes_below_n_candle_low`, `compression_before_breakout`, `condition_after_timestamp`, `condition_before_timestamp`, `condition_valid_until`, `consecutive_inside_bars`, `correction_leg_detected`, `daily_high_swept`, `daily_low_swept`, `daily_open`, `dark_cloud_cover`, `day_of_week`, `deep_pullback`, `descending_triangle_breakdown`, `displacement_candle_bearish`, `displacement_candle_bullish`, `distance_to_reference`, `dollar_volume`, `double_bottom_neckline_break`, `double_top_neckline_break`, `downside_tasuki_gap`, `dragonfly_doji`, `dynamic_trendline`, `ease_of_movement`, `equal_highs_liquidity_pool`, `equal_lows_liquidity_pool`, `evening_doji_star`, `evening_star`, `external_structure_break`, `failed_breakdown`, `failed_breakout`, `falling_three_methods`, `first_n_minutes_of_session`, `force_index`, `fvg_fully_mitigated`, `fvg_mid_mitigated`, `fvg_midpoint_touched`, `fvg_still_open`, `fvg_still_open_bearish`, `fvg_still_open_bullish`, `fvg_structure_invalidated`, `fvg_touched`, `fvg_virgin`, `gravestone_doji`, `hanging_man`, `harami_cross_bearish`, `harami_cross_bullish`, `head_and_shoulders_formed`, `head_and_shoulders_neckline_break`, `higher_high`, `higher_low`, `historical_volatility`, `impulse_leg_detected`, `in_neck_bearish`, `inside_range`, `internal_structure_break`, `inverse_head_and_shoulders_formed`, `inverse_head_and_shoulders_neckline_break`, `inverted_hammer`, `kicking_bearish`, `kicking_bullish`, `large_body_relative_to_atr`, `last_down_before_bullish_displacement`, `last_n_minutes_of_session`, `last_up_before_bearish_displacement`, `level_distance_percent`, `level_strength_score`, `linear_regression_channel_breakout`, `linear_regression_channel_touch`, `liquidity_grab_close_inside`, `london_session`, `long_legged_doji`, `long_lower_shadow`, `long_upper_shadow`, `lower_high`, `lower_low`, `market_structure_shift_bearish`, `market_structure_shift_bullish`, `marubozu_bearish`, `marubozu_bullish`, `matching_low`, `monthly_open`, `morning_doji_star`, `morning_star`, `multiple_touches_of_level`, `n_day_high_breakout`, `n_day_low_breakdown`, `narrow_range_candle`, `new_day_breakout`, `new_week_breakout`, `new_york_session`, `normalized_atr`, `nr4_candle`, `nr7_candle`, `on_balance_volume`, `on_neck_bearish`, `order_block_invalidated`, `order_block_mitigated`, `order_block_rejection`, `piercing_pattern`, `pivot_points`, `po3_dealing_range_sweep_bearish`, `po3_dealing_range_sweep_bullish`, `po3_sweep_displacement_bearish`, `po3_sweep_displacement_bullish`, `po3_sweep_displacement_structure_bearish`, `po3_sweep_displacement_structure_bullish`, `previous_high_swept`, `previous_low_swept`, `previous_session_high_low`, `price_bounces_from_support`, `price_bounces_from_trendline`, `price_breaks_trendline`, `price_closes_above_level`, `price_closes_below_level`, `price_enters_fvg`, `price_fills_fvg`, `price_near_horizontal_level`, `price_rejects_fvg`, `price_rejects_level`, `price_rejects_resistance`, `price_retests_broken_trendline`, `price_returns_to_order_block`, `price_touches_level`, `price_touches_trendline`, `protected_high`, `protected_low`, `pullback_ending_reversal_candle`, `pullback_to_breakout_level`, `pullback_to_ema`, `pullback_to_fibonacci_zone`, `pullback_to_vwap`, `pullback_with_declining_volume`, `range_compression`, `range_contraction_candle`, `range_expansion`, `range_high_rejection`, `range_low_rejection`, `reference_period_sweep`, `relative_volume_by_session`, `resistance_becomes_support`, `retest_after_breakdown`, `retest_after_breakout`, `rising_three_methods`, `rsi_divergence`, `sell_side_liquidity_sweep`, `separating_lines_bearish`, `separating_lines_bullish`, `session_close_window`, `session_expired`, `session_high_swept`, `session_low_swept`, `session_open_window`, `shallow_pullback`, `sideways_market`, `specific_hour_range`, `specific_utc_session`, `spinning_top_bearish`, `spinning_top_bullish`, `stop_hunt_above_range`, `stop_hunt_below_range`, `strong_swing_high`, `strong_swing_low`, `support_becomes_resistance`, `sweep_and_displacement`, `sweep_and_reclaim`, `swing_high_formed`, `swing_low_formed`, `symmetrical_triangle_breakdown`, `symmetrical_triangle_breakout`, `three_black_crows`, `three_inside_down`, `three_inside_up`, `three_outside_down`, `three_outside_up`, `three_white_soldiers`, `thrusting_pattern`, `tight_consolidation`, `time_since_condition_true`, `time_since_last_alert`, `time_since_setup_detected`, `time_window`, `trend_continuation_after_pullback`, `tweezer_bottom`, `tweezer_top`, `ulcer_index`, `upside_tasuki_gap`, `volume_oscillator`, `volume_profile_proxy`, `weak_high`, `weak_low`, `weekday_only`, `weekend_filter`, `weekly_high_swept`, `weekly_low_swept`, `weekly_open`, `wick_breaks_high_returns_below`, `wick_breaks_low_returns_above`, `wide_range_candle`

## Deterministic Approximation Notes

- `volume_profile_proxy` uses typical-price bins weighted by candle volume. It is not a true exchange volume profile.
- `buy_sell_pressure_proxy` uses close location within candle range. It is not real aggressor-side order flow.
- FVG, order-block, smart-money, swing-strength, trendline, and structure conditions use documented OHLCV definitions. They are deterministic labels, not claims about institutional intent.
- Custom sessions and calendar rules are timezone-aware. UTC remains the default unless the strategy condition supplies a user timezone.

## Deferred Due to Provider or Runtime Limitations

## Partial Implementations

- Cross-symbol, crypto-index, macro-index, breadth, sector, news/event, order-book, trade-tape, derivatives, and universe-ranking interfaces exist, but no production provider is configured.
- Universe ranking is registered for a future two-pass scanner. It does not yet affect live scanner sorting.
- Post-evaluation risk-quality keys are registered, but the current engine calculates risk after the entry tree, so those keys cannot block the same tree evaluation yet.
- `time_since_condition_true` evaluates only when a persisted `condition_first_true_at` value is supplied. That timestamp is not yet stored for every condition.
- Provider-required cards are visible only through category selection or search and cannot be added until available.
- Generated capability families have registry metadata and shared evaluator-family tests. Positive, negative, and insufficient-data fixtures are representative rather than one handcrafted fixture for every generated alias/key.
- Every generated condition template is schema-validated in bulk, but provider-bound conditions cannot receive positive live-data tests until their providers exist.

## Unsupported or Unsafe Capabilities Rejected

- Automated trade placement or order execution.
- Wallet seed phrases, private keys, withdrawal permissions, or remote access.
- News-derived buy/sell recommendations or unverified sentiment predictions.
- Guaranteed-profit, future-price prediction, or AI-invented market values.
- Futures-only conditions on spot-only plans without a derivatives provider and entitlement.

## Files Changed

- `src/ai_market_monitor/engine/indicators.py`
- `src/ai_market_monitor/engine/candle_patterns.py`
- `src/ai_market_monitor/engine/price_action.py`
- `src/ai_market_monitor/engine/context_conditions.py`
- `src/ai_market_monitor/engine/capabilities.py`
- `src/ai_market_monitor/engine/condition_registry.py`
- `src/ai_market_monitor/engine/builder_templates.py`
- `src/ai_market_monitor/engine/evaluator.py`
- `src/ai_market_monitor/services/interpreter.py`
- `src/ai_market_monitor/services/interfaces.py`
- `src/ai_market_monitor/services/scanner.py`
- `src/ai_market_monitor/templates/dashboard.html`
- `src/ai_market_monitor/static/dashboard.js`
- `src/ai_market_monitor/static/dashboard.css`

## Tests Added

- Indicator warm-up and extended calculation coverage.
- Positive, negative, and insufficient-data candle-pattern cases.
- Breakout and fair-value-gap price-action cases.
- Timezone-safe weekend/weekday evaluation.
- Prompt alias conversion into condition keys.
- Provider-unavailable proof behavior.
- Registry deduplication, categories, provider badges, and builder markup.

