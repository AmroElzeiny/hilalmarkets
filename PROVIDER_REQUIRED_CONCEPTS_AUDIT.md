# Provider-Required Concepts Audit

Date: 2026-06-27

## Live Registry Result

- Total concepts: 473
- Normal beta UI executable concepts: 330
- Hidden provider-required concepts: 142
- Hidden unsupported concepts: 1

Normal `condition_registry_payload()` now returns only `availability == "available"` items. Audit/admin code can call `condition_registry_payload(include_provider_required=True)` to inspect hidden concepts. Mandatory provider-required conditions still block activation if found in saved strategies.

## Implementation Decision

- `FREE_CONFIRMED` with current tested adapter: none newly enabled in this pass.
- `FREE_WITH_LIMITS`: Binance public spot/order-book concepts can be researched, but only the currently tested OHLCV/order-book paths remain visible.
- `PAID_ONLY`: none asserted without provider terms review.
- `UNCLEAR_HUMAN_CHECK`: all 142 provider-required concepts remain hidden until adapter, terms, rate limits, and proof support are verified.
- `NOT_SAFE_TO_SUPPORT`: none newly classified as unsafe in code; event/news concepts require strong legal/source checks before public use.

## Hidden Concepts By Provider Family

### alert_behavior

- `same_symbol_alert_cooldown`
- `same_strategy_alert_cooldown`
- `maximum_alerts_per_hour_condition`
- `daily_alert_budget_condition`
- `alert_only_on_state_change`
- `maximum_alert_lateness_condition`

### cross_market

- `btc_usdt_trend_filter`
- `eth_usdt_trend_filter`
- `eth_btc_relative_strength`
- `symbol_outperforming_btc`
- `symbol_underperforming_btc`
- `symbol_outperforming_eth`
- `pair_correlation_btc`
- `pair_beta_btc`
- `pair_volatility_vs_btc`
- `pair_move_relative_btc`

### crypto_index

- `total_market_cap_trend`
- `total2_trend`
- `total3_trend`
- `btc_dominance_trend`
- `usdt_dominance_trend`
- `stablecoin_dominance_trend`
- `altcoin_market_cap_vs_ma`
- `altseason_context`
- `risk_on_crypto_context`
- `risk_off_crypto_context`

### derivatives

- `long_liquidation_spike`
- `short_liquidation_spike`
- `open_interest_rising`
- `open_interest_falling`
- `funding_rate_positive`
- `funding_rate_negative`
- `funding_rate_extreme`
- `price_up_oi_up`
- `price_up_oi_down`
- `price_down_oi_up`
- `price_down_oi_down`

### event_feed

- `major_exchange_listing_event`
- `major_exchange_delisting_event`
- `token_unlock_upcoming`
- `token_unlock_occurred`
- `airdrop_snapshot_event`
- `mainnet_launch_event`
- `protocol_upgrade_event`
- `governance_vote_event`
- `security_exploit_event`
- `stablecoin_depeg_event`
- `institutional_news_event`
- `regulatory_headline_event`
- `high_impact_market_news`
- `cpi_event_window`
- `fomc_event_window`
- `fed_rate_decision_window`
- `nfp_event_window`
- `gdp_event_window`
- `economic_calendar_event`
- `event_actual_above_forecast`
- `event_actual_below_forecast`
- `event_surprise_magnitude`

### macro_market

- `dxy_trend_filter`
- `spx_trend_filter`
- `nasdaq_trend_filter`
- `gold_trend_filter`
- `us10y_trend_filter`
- `vix_trend_filter`

### market_breadth

- `universe_above_ema50_percent`
- `universe_above_ema200_percent`
- `universe_positive_24h_percent`
- `universe_n_day_high_percent`
- `universe_volume_spike_percent`
- `breadth_thrust`
- `market_breadth_deteriorating`
- `market_breadth_improving`

### market_cap_provider

- `market_cap_minimum`

### order_book

- `spread_below_threshold`
- `spread_above_threshold`
- `order_book_depth_above`
- `bid_ask_depth_imbalance`
- `large_wall_above_price`
- `large_wall_below_price`
- `liquidity_wall_pulled`
- `liquidity_wall_added`
- `approaching_liquidity_wall`
- `slippage_below_threshold`
- `trade_count_spike`
- `average_trade_size_spike`
- `aggressive_buy_volume_proxy`
- `aggressive_sell_volume_proxy`
- `trade_buy_sell_imbalance`
- `volume_burst_seconds`

### risk_context

- `stop_distance_atr_units`
- `stop_distance_too_tight`
- `stop_distance_too_wide`
- `target_distance_next_resistance`
- `target_distance_next_support`
- `r_multiple_before_obstacle`
- `liquidity_obstacle_before_target`
- `minimum_clean_path_to_target`
- `price_moved_too_far_from_trigger`
- `candle_overextended`
- `spread_too_wide_at_alert`
- `volatility_too_high`
- `volatility_too_low`
- `setup_age_too_old`
- `invalidation_not_calculable`
- `risk_context_incomplete`
- `target_overlaps_obstacle`
- `reward_to_risk_after_fees`
- `reward_to_risk_after_slippage`
- `maximum_alert_lateness`
- `maximum_data_latency`
- `minimum_candle_liquidity`

### setup_lifecycle

- `setup_state_is`
- `setup_age_minutes`
- `setup_first_detected_within`
- `setup_entry_zone_active`
- `setup_not_invalidated`
- `setup_not_expired`

### token_categories

- `meme_coin_exclusion`
- `category_outperforming_market`
- `category_underperforming_market`
- `ai_coins_trending`
- `defi_coins_trending`
- `meme_coins_trending`
- `layer1_coins_trending`
- `gaming_coins_trending`
- `exchange_tokens_trending`

### universe_ranking

- `top_percent_24h_volume`
- `top_percent_1h_volume_change`
- `top_percent_relative_volume`
- `top_percent_momentum`
- `top_percent_volatility`
- `bottom_percent_volatility`
- `top_percent_trend_strength`
- `top_percent_distance_ema`
- `near_24h_high`
- `near_24h_low`
- `highest_volume_expansion`
- `highest_compression_score`
- `strongest_breakout_score`
- `strongest_pullback_score`
- `strongest_btc_relative_strength`

## Activation Behavior

- Hidden provider-required concepts are not returned to normal UI capability payloads.
- Prompt executable candidate sets remain restricted to available concepts.
- If an old saved strategy contains a mandatory provider-required condition, validation blocks activation with `required_data_unavailable`.
- If an old saved strategy contains an optional provider-required condition, validation warns and proof records unavailable state.
