# Condition Registry

Generated from `ai_market_monitor.engine.condition_registry`.

- Schema version: `2.0`
- Total capabilities: `471`
- Executable now: `321`
- Deferred or provider-bound: `150`
- Logic operators: `12`

| Category | Key | Status | Example sentence | Required data | Comparators | Notes |
|---|---|---|---|---|---|---|
| Advanced Logic | `btc_trend_filter` | `recognized_not_executable` | Altcoin scans gated by BTC trend. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Cross-market benchmark filters need multi-symbol candle evaluation. |
| Advanced Logic | `correlation_filter` | `recognized_not_executable` | Filter by correlation to another asset. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Requires correlation time-series service. |
| Advanced Logic | `eth_trend_filter` | `recognized_not_executable` | Altcoin scans gated by ETH trend. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Cross-market benchmark filters need multi-symbol candle evaluation. |
| Advanced Logic | `fibonacci_extension_targets` | `recognized_not_executable` | Targets can reference Fibonacci extensions. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Recognized for future target generation; current targets remain fixed percent, structure, or R multiple. |
| Advanced Logic | `fibonacci_retracement_zone` | `recognized_not_executable` | Price enters a configured Fibonacci retracement zone. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Needs deterministic swing-anchor selection before live scanning. |
| Advanced Logic | `golden_pocket_zone` | `recognized_not_executable` | Price enters the 0.618-0.65 retracement zone. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Needs deterministic swing anchors. |
| Alert Behavior | `alert_only_on_state_change` | `requires_runtime_context` | Alert Only On State Change is satisfied. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: alert_behavior The platform enforces related policy outside the candle rule tree. A unified runtime-context operand is deferred. |
| Alert Behavior | `daily_alert_budget_condition` | `requires_runtime_context` | Daily Alert Budget Condition is satisfied. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: alert_behavior The platform enforces related policy outside the candle rule tree. A unified runtime-context operand is deferred. |
| Alert Behavior | `maximum_alert_lateness_condition` | `requires_runtime_context` | Maximum Alert Lateness Condition is satisfied. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: alert_behavior The platform enforces related policy outside the candle rule tree. A unified runtime-context operand is deferred. |
| Alert Behavior | `maximum_alerts_per_hour_condition` | `requires_runtime_context` | Maximum Alerts Per Hour Condition is satisfied. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: alert_behavior The platform enforces related policy outside the candle rule tree. A unified runtime-context operand is deferred. |
| Alert Behavior | `same_strategy_alert_cooldown` | `requires_runtime_context` | Same Strategy Alert Cooldown is satisfied. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: alert_behavior The platform enforces related policy outside the candle rule tree. A unified runtime-context operand is deferred. |
| Alert Behavior | `same_symbol_alert_cooldown` | `requires_runtime_context` | Same Symbol Alert Cooldown is satisfied. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: alert_behavior The platform enforces related policy outside the candle rule tree. A unified runtime-context operand is deferred. |
| Candle Pattern | `abandoned_baby_bearish` | `implemented` | Abandoned Baby Bearish appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `abandoned_baby_bullish` | `implemented` | Abandoned Baby Bullish appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `bearish_harami` | `implemented` | Bearish Harami appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `bearish_engulfing` | `implemented` | Bearish candle engulfs previous bullish body. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Candle Pattern | `belt_hold_bearish` | `implemented` | Belt Hold Bearish appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `belt_hold_bullish` | `implemented` | Belt Hold Bullish appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `bullish_harami` | `implemented` | Bullish Harami appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `bullish_engulfing` | `implemented` | Bullish candle engulfs previous bearish body. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Candle Pattern | `candle_anatomy` | `implemented` | Candle Anatomy meets the configured threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Candle Pattern | `dark_cloud_cover` | `implemented` | Dark Cloud Cover appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `doji` | `implemented` | Very small candle body relative to range. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Candle Pattern | `downside_tasuki_gap` | `implemented` | Downside Tasuki Gap appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `dragonfly_doji` | `implemented` | Dragonfly Doji appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `evening_doji_star` | `implemented` | Evening Doji Star appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `evening_star` | `implemented` | Evening Star appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `falling_three_methods` | `implemented` | Falling Three Methods appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `gravestone_doji` | `implemented` | Gravestone Doji appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `green_candle` | `implemented` | Close is above open. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Candle Pattern | `hammer` | `implemented` | Long lower wick with small body. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Candle Pattern | `hanging_man` | `implemented` | Hanging Man appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `harami_cross_bearish` | `implemented` | Harami Cross Bearish appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `harami_cross_bullish` | `implemented` | Harami Cross Bullish appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `in_neck_bearish` | `implemented` | In Neck Bearish appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `inside_bar` | `implemented` | Current high/low is inside previous high/low. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Candle Pattern | `inverted_hammer` | `implemented` | Inverted Hammer appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `kicking_bearish` | `implemented` | Kicking Bearish appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `kicking_bullish` | `implemented` | Kicking Bullish appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `long_legged_doji` | `implemented` | Long Legged Doji appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `long_lower_shadow` | `implemented` | Long Lower Shadow appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `long_upper_shadow` | `implemented` | Long Upper Shadow appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `marubozu_bearish` | `implemented` | Marubozu Bearish appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `marubozu_bullish` | `implemented` | Marubozu Bullish appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `matching_low` | `implemented` | Matching Low appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `morning_doji_star` | `implemented` | Morning Doji Star appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `morning_star` | `implemented` | Morning Star appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `on_neck_bearish` | `implemented` | On Neck Bearish appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `outside_bar` | `implemented` | Current high/low exceeds previous high/low. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Candle Pattern | `piercing_pattern` | `implemented` | Piercing Pattern appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `pin_bar` | `implemented` | Long wick rejection candle. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Candle Pattern | `red_candle` | `implemented` | Close is below open. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Candle Pattern | `rising_three_methods` | `implemented` | Rising Three Methods appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `separating_lines_bearish` | `implemented` | Separating Lines Bearish appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `separating_lines_bullish` | `implemented` | Separating Lines Bullish appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `shooting_star` | `implemented` | Long upper wick with small body. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Candle Pattern | `spinning_top_bearish` | `implemented` | Spinning Top Bearish appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `spinning_top_bullish` | `implemented` | Spinning Top Bullish appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `strong_close_near_high` | `implemented` | Close is near the candle high. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Candle Pattern | `strong_close_near_low` | `implemented` | Close is near the candle low. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Candle Pattern | `three_black_crows` | `implemented` | Three Black Crows appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `three_inside_down` | `implemented` | Three Inside Down appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `three_inside_up` | `implemented` | Three Inside Up appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `three_outside_down` | `implemented` | Three Outside Down appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `three_outside_up` | `implemented` | Three Outside Up appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `three_white_soldiers` | `implemented` | Three White Soldiers appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `thrusting_pattern` | `implemented` | Thrusting Pattern appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `tweezer_bottom` | `implemented` | Tweezer Bottom appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `tweezer_top` | `implemented` | Tweezer Top appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Candle Pattern | `upside_tasuki_gap` | `implemented` | Upside Tasuki Gap appears on the selected timeframe. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `bearish_fair_value_gap` | `implemented` | Bearish Fair Value Gap is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `bearish_order_block_candidate` | `implemented` | Bearish Order Block Candidate is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `bearish_liquidity_sweep` | `implemented` | High sweeps prior highs and closes back below. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Liquidity Smart Money | `bullish_fair_value_gap` | `implemented` | Bullish Fair Value Gap is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `bullish_order_block_candidate` | `implemented` | Bullish Order Block Candidate is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `bullish_liquidity_sweep` | `implemented` | Low sweeps prior lows and closes back above. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Liquidity Smart Money | `buy_side_liquidity_sweep` | `implemented` | Buy Side Liquidity Sweep is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `equal_highs_liquidity_pool` | `implemented` | Equal Highs Liquidity Pool is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `equal_lows_liquidity_pool` | `implemented` | Equal Lows Liquidity Pool is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `fvg_midpoint_touched` | `implemented` | Fvg Midpoint Touched is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `fvg_still_open` | `implemented` | Fvg Still Open is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `liquidity_grab_close_inside` | `implemented` | Liquidity Grab Close Inside is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `order_block_invalidated` | `implemented` | Order Block Invalidated is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `order_block_mitigated` | `implemented` | Order Block Mitigated is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `order_block_rejection` | `implemented` | Order Block Rejection is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `price_enters_fvg` | `implemented` | Price Enters Fvg is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `price_fills_fvg` | `implemented` | Price Fills Fvg is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `price_rejects_fvg` | `implemented` | Price Rejects Fvg is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `price_returns_to_order_block` | `implemented` | Price Returns To Order Block is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `sell_side_liquidity_sweep` | `implemented` | Sell Side Liquidity Sweep is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `stop_hunt_above_range` | `implemented` | Stop Hunt Above Range is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `stop_hunt_below_range` | `implemented` | Stop Hunt Below Range is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `sweep_and_displacement` | `implemented` | Sweep And Displacement is confirmed. | ohlcv | is_true, is_false |  |
| Liquidity Smart Money | `sweep_and_reclaim` | `implemented` | Sweep And Reclaim is confirmed. | ohlcv | is_true, is_false |  |
| Market Context | `ai_coins_trending` | `provider_required` | AI Coins Trending is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: token_categories Configure a Token category provider before this condition can be activated. |
| Market Context | `altcoin_market_cap_vs_ma` | `provider_required` | Altcoin Market Cap vs MA is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: crypto_index Configure a Crypto index provider before this condition can be activated. |
| Market Context | `altseason_context` | `provider_required` | Altseason-style Context is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: crypto_index Configure a Crypto index provider before this condition can be activated. |
| Market Context | `btc_dominance_trend` | `provider_required` | BTC Dominance Trend is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: crypto_index Configure a Crypto index provider before this condition can be activated. |
| Market Context | `btc_usdt_trend_filter` | `provider_required` | BTC/USDT Trend Filter is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: cross_market Configure a Cross-market candles before this condition can be activated. |
| Market Context | `breadth_thrust` | `provider_required` | Breadth Thrust is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: market_breadth Configure a Universe breadth aggregator before this condition can be activated. |
| Market Context | `category_outperforming_market` | `provider_required` | Category Outperforming Market is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: token_categories Configure a Token category provider before this condition can be activated. |
| Market Context | `category_underperforming_market` | `provider_required` | Category Underperforming Market is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: token_categories Configure a Token category provider before this condition can be activated. |
| Market Context | `dxy_trend_filter` | `provider_required` | DXY Trend Filter is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: macro_market Configure a External macro provider before this condition can be activated. |
| Market Context | `defi_coins_trending` | `provider_required` | DeFi Coins Trending is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: token_categories Configure a Token category provider before this condition can be activated. |
| Market Context | `eth_usdt_trend_filter` | `provider_required` | ETH/USDT Trend Filter is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: cross_market Configure a Cross-market candles before this condition can be activated. |
| Market Context | `exchange_tokens_trending` | `provider_required` | Exchange Tokens Trending is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: token_categories Configure a Token category provider before this condition can be activated. |
| Market Context | `funding_rate_extreme` | `provider_required` | Funding Rate Extreme is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: derivatives Configure a Derivatives market provider before this condition can be activated. |
| Market Context | `funding_rate_negative` | `provider_required` | Funding Rate Negative is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: derivatives Configure a Derivatives market provider before this condition can be activated. |
| Market Context | `funding_rate_positive` | `provider_required` | Funding Rate Positive is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: derivatives Configure a Derivatives market provider before this condition can be activated. |
| Market Context | `gaming_coins_trending` | `provider_required` | Gaming Coins Trending is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: token_categories Configure a Token category provider before this condition can be activated. |
| Market Context | `gold_trend_filter` | `provider_required` | Gold Trend Filter is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: macro_market Configure a External macro provider before this condition can be activated. |
| Market Context | `layer1_coins_trending` | `provider_required` | Layer 1 Coins Trending is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: token_categories Configure a Token category provider before this condition can be activated. |
| Market Context | `leveraged_token_exclusion` | `implemented` | Exclude leveraged tokens. | market_metadata | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Market Context | `listing_age_filter` | `implemented` | Minimum market listing age. | market_metadata | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Market Context | `long_liquidation_spike` | `provider_required` | Long Liquidation Spike is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: derivatives Configure a Derivatives market provider before this condition can be activated. |
| Market Context | `market_breadth_deteriorating` | `provider_required` | Market Breadth Deteriorating is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: market_breadth Configure a Universe breadth aggregator before this condition can be activated. |
| Market Context | `market_breadth_improving` | `provider_required` | Market Breadth Improving is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: market_breadth Configure a Universe breadth aggregator before this condition can be activated. |
| Market Context | `market_cap_minimum` | `recognized_not_executable` | External market-cap filter. | market_cap_provider | gt, gte, lt, lte, eq, crosses_above, crosses_below | Requires a separate market-cap provider API. |
| Market Context | `meme_coins_trending` | `provider_required` | Meme Coins Trending is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: token_categories Configure a Token category provider before this condition can be activated. |
| Market Context | `meme_coin_exclusion` | `recognized_not_executable` | Exclude meme coins by external tags. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Requires a token-tag provider before deterministic filtering. |
| Market Context | `min_quote_volume_24h` | `implemented` | Market must meet 24h quote-volume minimum. | ticker | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Market Context | `min_average_candle_volume` | `implemented` | Average candle volume must meet minimum. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Market Context | `nasdaq_trend_filter` | `provider_required` | NASDAQ Trend Filter is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: macro_market Configure a External macro provider before this condition can be activated. |
| Market Context | `open_interest_falling` | `provider_required` | Open Interest Falling is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: derivatives Configure a Derivatives market provider before this condition can be activated. |
| Market Context | `open_interest_rising` | `provider_required` | Open Interest Rising is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: derivatives Configure a Derivatives market provider before this condition can be activated. |
| Market Context | `price_down_oi_down` | `provider_required` | Price Down + OI Down is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: derivatives Configure a Derivatives market provider before this condition can be activated. |
| Market Context | `price_down_oi_up` | `provider_required` | Price Down + OI Up is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: derivatives Configure a Derivatives market provider before this condition can be activated. |
| Market Context | `price_up_oi_down` | `provider_required` | Price Up + OI Down is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: derivatives Configure a Derivatives market provider before this condition can be activated. |
| Market Context | `price_up_oi_up` | `provider_required` | Price Up + OI Up is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: derivatives Configure a Derivatives market provider before this condition can be activated. |
| Market Context | `risk_off_crypto_context` | `provider_required` | Risk-off Crypto Context is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: crypto_index Configure a Crypto index provider before this condition can be activated. |
| Market Context | `risk_on_crypto_context` | `provider_required` | Risk-on Crypto Context is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: crypto_index Configure a Crypto index provider before this condition can be activated. |
| Market Context | `spx_trend_filter` | `provider_required` | SPX Trend Filter is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: macro_market Configure a External macro provider before this condition can be activated. |
| Market Context | `short_liquidation_spike` | `provider_required` | Short Liquidation Spike is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: derivatives Configure a Derivatives market provider before this condition can be activated. |
| Market Context | `spread_filter` | `implemented` | Maximum spread in basis points. | ticker, order_book | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Market Context | `stablecoin_dominance_trend` | `provider_required` | Stablecoin Dominance Trend is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: crypto_index Configure a Crypto index provider before this condition can be activated. |
| Market Context | `stablecoin_exclusion` | `implemented` | Exclude stablecoin base assets. | market_metadata | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Market Context | `total_market_cap_trend` | `provider_required` | TOTAL Market Cap Trend is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: crypto_index Configure a Crypto index provider before this condition can be activated. |
| Market Context | `total2_trend` | `provider_required` | TOTAL2 Trend is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: crypto_index Configure a Crypto index provider before this condition can be activated. |
| Market Context | `total3_trend` | `provider_required` | TOTAL3 Trend is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: crypto_index Configure a Crypto index provider before this condition can be activated. |
| Market Context | `us10y_trend_filter` | `provider_required` | US 10Y Yield Trend Filter is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: macro_market Configure a External macro provider before this condition can be activated. |
| Market Context | `usdt_dominance_trend` | `provider_required` | USDT Dominance Trend is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: crypto_index Configure a Crypto index provider before this condition can be activated. |
| Market Context | `universe_above_ema200_percent` | `provider_required` | Universe Above EMA 200 % is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: market_breadth Configure a Universe breadth aggregator before this condition can be activated. |
| Market Context | `universe_above_ema50_percent` | `provider_required` | Universe Above EMA 50 % is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: market_breadth Configure a Universe breadth aggregator before this condition can be activated. |
| Market Context | `universe_n_day_high_percent` | `provider_required` | Universe Making N-day Highs % is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: market_breadth Configure a Universe breadth aggregator before this condition can be activated. |
| Market Context | `universe_positive_24h_percent` | `provider_required` | Universe Positive 24h % is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: market_breadth Configure a Universe breadth aggregator before this condition can be activated. |
| Market Context | `universe_volume_spike_percent` | `provider_required` | Universe Volume Spike % is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: market_breadth Configure a Universe breadth aggregator before this condition can be activated. |
| Market Context | `vix_trend_filter` | `provider_required` | VIX Trend Filter is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: macro_market Configure a External macro provider before this condition can be activated. |
| Market Structure | `break_of_structure_bearish` | `implemented` | Close breaks below prior swing structure. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Market Structure | `change_of_character_bearish` | `implemented` | Close loses prior structure after bullish pressure. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Market Structure | `break_of_structure_bullish` | `implemented` | Close breaks above prior swing structure. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Market Structure | `change_of_character_bullish` | `implemented` | Close reclaims prior structure after bearish pressure. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Market Structure | `external_structure_break` | `implemented` | External Structure Break is confirmed. | ohlcv | is_true, is_false |  |
| Market Structure | `internal_structure_break` | `implemented` | Internal Structure Break is confirmed. | ohlcv | is_true, is_false |  |
| Market Structure | `market_structure_shift_bearish` | `implemented` | Market Structure Shift Bearish is confirmed. | ohlcv | is_true, is_false |  |
| Market Structure | `market_structure_shift_bullish` | `implemented` | Market Structure Shift Bullish is confirmed. | ohlcv | is_true, is_false |  |
| Market Structure | `protected_high` | `implemented` | Protected High is confirmed. | ohlcv | is_true, is_false |  |
| Market Structure | `protected_low` | `implemented` | Protected Low is confirmed. | ohlcv | is_true, is_false |  |
| Market Structure | `strong_swing_high` | `implemented` | Strong Swing High is confirmed. | ohlcv | is_true, is_false |  |
| Market Structure | `strong_swing_low` | `implemented` | Strong Swing Low is confirmed. | ohlcv | is_true, is_false |  |
| Market Structure | `swing_high_formed` | `implemented` | Swing High Formed is confirmed. | ohlcv | is_true, is_false |  |
| Market Structure | `swing_low_formed` | `implemented` | Swing Low Formed is confirmed. | ohlcv | is_true, is_false |  |
| Market Structure | `weak_high` | `implemented` | Weak High is confirmed. | ohlcv | is_true, is_false |  |
| Market Structure | `weak_low` | `implemented` | Weak Low is confirmed. | ohlcv | is_true, is_false |  |
| Momentum | `adx_trend_strength` | `implemented` | ADX exceeds a trend-strength threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `commodity_channel_index` | `implemented` | CCI crosses above 100. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `connors_rsi` | `implemented` | Connors RSI is below 10. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `macd_histogram_flip` | `implemented` | MACD histogram crosses zero. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `macd_histogram_slope` | `implemented` | MACD histogram is increasing or decreasing. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `macd_line_cross_signal` | `implemented` | MACD line crosses MACD signal. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `momentum_indicator` | `implemented` | Momentum is above zero and rising. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `money_flow_index` | `implemented` | MFI is below 20. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `rsi_cross` | `implemented` | RSI crosses above or below a configured level. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `rsi_divergence` | `recognized_not_executable` | Bullish or bearish RSI divergence recognition. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Divergence needs swing-point pairing. It is recognized and routed for future deterministic implementation. |
| Momentum | `rsi_exits_overbought` | `implemented` | RSI crosses below 70 after overbought. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `rsi_exits_oversold` | `implemented` | RSI crosses above 30 after oversold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `rsi_threshold` | `implemented` | RSI is above or below a configured level. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `rate_of_change` | `implemented` | Twelve-period ROC is positive. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `relative_vigor_index` | `implemented` | RVI crosses above its signal. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `stochastic_kd_cross` | `implemented` | Stochastic K crosses D. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `stochastic_rsi` | `implemented` | Stochastic RSI K crosses above D below 20. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `stochastic_exit` | `implemented` | Stochastic exits oversold or overbought. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `true_strength_index` | `implemented` | TSI crosses above its signal. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `ultimate_oscillator` | `implemented` | Ultimate Oscillator crosses above 30. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Momentum | `williams_percent_r` | `implemented` | Williams %R crosses above -80. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| News Events | `airdrop_snapshot_event` | `provider_required` | Airdrop / Snapshot Event is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `cpi_event_window` | `provider_required` | CPI Event Window is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `institutional_news_event` | `provider_required` | ETF / Institutional News is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `event_actual_above_forecast` | `provider_required` | Event Actual Above Forecast is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `event_actual_below_forecast` | `provider_required` | Event Actual Below Forecast is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `event_surprise_magnitude` | `provider_required` | Event Surprise Magnitude is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `fomc_event_window` | `provider_required` | FOMC Event Window is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `fed_rate_decision_window` | `provider_required` | Fed Rate Decision Window is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `gdp_event_window` | `provider_required` | GDP Event Window is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `governance_vote_event` | `provider_required` | Governance Vote is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `economic_calendar_event` | `provider_required` | High-impact Economic Calendar Event is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `high_impact_market_news` | `provider_required` | High-impact Market News is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `mainnet_launch_event` | `provider_required` | Mainnet Launch is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `major_exchange_delisting_event` | `provider_required` | Major Exchange Delisting is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `major_exchange_listing_event` | `provider_required` | Major Exchange Listing is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `nfp_event_window` | `provider_required` | NFP Event Window is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `protocol_upgrade_event` | `provider_required` | Protocol Upgrade is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `regulatory_headline_event` | `provider_required` | Regulatory Headline is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `security_exploit_event` | `provider_required` | Security Exploit / Hack News is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `stablecoin_depeg_event` | `provider_required` | Stablecoin Depeg Event is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `token_unlock_occurred` | `provider_required` | Token Unlock Just Occurred is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| News Events | `token_unlock_upcoming` | `provider_required` | Token Unlock Upcoming is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: event_feed Configure a Event and economic calendar provider before this condition can be activated. |
| Order Book Liquidity | `aggressive_buy_volume_proxy` | `provider_required` | Aggressive Buy Volume Proxy is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: order_book Configure a Order-book snapshot provider before this condition can be activated. |
| Order Book Liquidity | `aggressive_sell_volume_proxy` | `provider_required` | Aggressive Sell Volume Proxy is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: order_book Configure a Order-book snapshot provider before this condition can be activated. |
| Order Book Liquidity | `average_trade_size_spike` | `provider_required` | Average Trade Size Spike is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: order_book Configure a Order-book snapshot provider before this condition can be activated. |
| Order Book Liquidity | `bid_ask_depth_imbalance` | `provider_required` | Bid / Ask Depth Imbalance is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: order_book Configure a Order-book snapshot provider before this condition can be activated. |
| Order Book Liquidity | `spread_above_threshold` | `provider_required` | Bid / Ask Spread Above Threshold is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: order_book Configure a Order-book snapshot provider before this condition can be activated. |
| Order Book Liquidity | `spread_below_threshold` | `provider_required` | Bid / Ask Spread Below Threshold is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: order_book Configure a Order-book snapshot provider before this condition can be activated. |
| Order Book Liquidity | `large_wall_above_price` | `provider_required` | Large Wall Above Price is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: order_book Configure a Order-book snapshot provider before this condition can be activated. |
| Order Book Liquidity | `large_wall_below_price` | `provider_required` | Large Wall Below Price is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: order_book Configure a Order-book snapshot provider before this condition can be activated. |
| Order Book Liquidity | `liquidity_wall_added` | `provider_required` | Liquidity Wall Added is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: order_book Configure a Order-book snapshot provider before this condition can be activated. |
| Order Book Liquidity | `liquidity_wall_pulled` | `provider_required` | Liquidity Wall Pulled is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: order_book Configure a Order-book snapshot provider before this condition can be activated. |
| Order Book Liquidity | `order_book_depth_above` | `provider_required` | Order Book Depth Above Threshold is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: order_book Configure a Order-book snapshot provider before this condition can be activated. |
| Order Book Liquidity | `approaching_liquidity_wall` | `provider_required` | Price Approaching Liquidity Wall is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: order_book Configure a Order-book snapshot provider before this condition can be activated. |
| Order Book Liquidity | `slippage_below_threshold` | `provider_required` | Slippage Estimate Below Threshold is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: order_book Configure a Order-book snapshot provider before this condition can be activated. |
| Order Book Liquidity | `trade_buy_sell_imbalance` | `provider_required` | Trade Buy / Sell Imbalance is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: order_book Configure a Order-book snapshot provider before this condition can be activated. |
| Order Book Liquidity | `trade_count_spike` | `provider_required` | Trade Count Spike is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: order_book Configure a Order-book snapshot provider before this condition can be activated. |
| Order Book Liquidity | `volume_burst_seconds` | `provider_required` | Volume Burst in Last N Seconds is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: order_book Configure a Order-book snapshot provider before this condition can be activated. |
| Price | `pivot_points` | `implemented` | Pivot Points meets the configured threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `above_range` | `implemented` | Above Range is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `all_time_high_breakout` | `implemented` | All Time High Breakout is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `auto_channel_breakdown` | `implemented` | Auto Channel Breakdown is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `auto_channel_breakout` | `implemented` | Auto Channel Breakout is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `auto_channel_lower_touch` | `implemented` | Auto Channel Lower Touch is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `auto_channel_upper_touch` | `implemented` | Auto Channel Upper Touch is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `below_range` | `implemented` | Below Range is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `break_and_retest_confirmed` | `implemented` | Break And Retest Confirmed is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `breakdown_from_consolidation` | `implemented` | Breakdown From Consolidation is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `breakout_from_consolidation` | `implemented` | Breakout From Consolidation is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `breakout_with_volume_confirmation` | `implemented` | Breakout With Volume Confirmation is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `breakout_without_volume_confirmation` | `implemented` | Breakout Without Volume Confirmation is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `breaks_n_candle_high` | `implemented` | Breaks N Candle High is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `breaks_n_candle_low` | `implemented` | Breaks N Candle Low is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `close_above_previous_day_high` | `implemented` | Close Above Previous Day High is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `close_above_previous_week_high` | `implemented` | Close Above Previous Week High is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `close_below_previous_day_low` | `implemented` | Close Below Previous Day Low is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `close_below_previous_week_low` | `implemented` | Close Below Previous Week Low is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `closes_above_n_candle_high` | `implemented` | Closes Above N Candle High is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `closes_below_n_candle_low` | `implemented` | Closes Below N Candle Low is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `compression_before_breakout` | `implemented` | Compression Before Breakout is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `consecutive_inside_bars` | `implemented` | Consecutive Inside Bars is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `consolidation_range` | `implemented` | Recent range is narrow relative to price. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `correction_leg_detected` | `implemented` | Correction Leg Detected is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `daily_high_swept` | `implemented` | Daily High Swept is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `daily_low_swept` | `implemented` | Daily Low Swept is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `daily_high_low` | `implemented` | Daily high/low break or sweep. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `deep_pullback` | `implemented` | Deep Pullback is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `displacement_candle_bearish` | `implemented` | Displacement Candle Bearish is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `displacement_candle_bullish` | `implemented` | Displacement Candle Bullish is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `dynamic_trendline` | `implemented` | Dynamic Trendline is confirmed. | ohlcv | gt, gte, lt, lte, eq |  |
| Price Action | `equal_highs` | `implemented` | Recent highs cluster within tolerance. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `equal_lows` | `implemented` | Recent lows cluster within tolerance. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `failed_breakdown` | `implemented` | Failed Breakdown is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `failed_breakout` | `implemented` | Failed Breakout is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `higher_high` | `implemented` | Latest high exceeds lookback high. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `higher_low` | `implemented` | Latest low remains above lookback low. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `impulse_leg_detected` | `implemented` | Impulse Leg Detected is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `impulse_candle` | `implemented` | Current candle range and close location show impulse. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `inside_range` | `implemented` | Inside Range is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `large_body_relative_to_atr` | `implemented` | Large Body Relative To Atr is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `last_down_before_bullish_displacement` | `implemented` | Last Down Before Bullish Displacement is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `last_up_before_bearish_displacement` | `implemented` | Last Up Before Bearish Displacement is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `level_distance_percent` | `implemented` | Level Distance Percent is confirmed. | ohlcv | gt, gte, lt, lte, eq |  |
| Price Action | `level_strength_score` | `implemented` | Level Strength Score is confirmed. | ohlcv | gt, gte, lt, lte, eq |  |
| Price Action | `linear_regression_channel_breakout` | `implemented` | Linear Regression Channel Breakout is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `linear_regression_channel_touch` | `implemented` | Linear Regression Channel Touch is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `lower_high` | `implemented` | Latest high remains below lookback high. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `lower_low` | `implemented` | Latest low breaks lookback low. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `monthly_high_low` | `implemented` | Monthly high/low break or sweep. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `multiple_touches_of_level` | `implemented` | Multiple Touches Of Level is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `n_day_high_breakout` | `implemented` | N Day High Breakout is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `n_day_low_breakdown` | `implemented` | N Day Low Breakdown is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `narrow_range_candle` | `implemented` | Narrow Range Candle is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `new_n_day_high` | `implemented` | Latest high exceeds the previous N-day high. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `new_n_day_low` | `implemented` | Latest low breaks the previous N-day low. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `nr4_candle` | `implemented` | Nr4 Candle is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `nr7_candle` | `implemented` | Nr7 Candle is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `percent_change_lookback` | `implemented` | Price increases or decreases by X percent over lookback. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `pivot_high_low` | `implemented` | Pivot swing detection. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `previous_high_swept` | `implemented` | Previous High Swept is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `previous_low_swept` | `implemented` | Previous Low Swept is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `price_bounces_from_support` | `implemented` | Price Bounces From Support is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `price_bounces_from_trendline` | `implemented` | Price Bounces From Trendline is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `price_breaks_trendline` | `implemented` | Price Breaks Trendline is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `price_closes_above_level` | `implemented` | Price Closes Above Level is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `price_closes_below_level` | `implemented` | Price Closes Below Level is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `price_near_horizontal_level` | `implemented` | Price Near Horizontal Level is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `price_rejects_level` | `implemented` | Price Rejects Level is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `price_rejects_resistance` | `implemented` | Price Rejects Resistance is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `price_retests_broken_trendline` | `implemented` | Price Retests Broken Trendline is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `price_touches_level` | `implemented` | Price Touches Level is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `price_touches_trendline` | `implemented` | Price Touches Trendline is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `pullback_ending_reversal_candle` | `implemented` | Pullback Ending Reversal Candle is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `pullback_to_breakout_level` | `implemented` | Pullback To Breakout Level is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `pullback_to_ema` | `implemented` | Pullback To Ema is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `pullback_to_fibonacci_zone` | `implemented` | Pullback To Fibonacci Zone is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `pullback_to_vwap` | `implemented` | Pullback To Vwap is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `pullback_with_declining_volume` | `implemented` | Pullback With Declining Volume is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `pullback_depth_percent` | `implemented` | Pullback depth from recent swing high or low. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `range_compression` | `implemented` | Range Compression is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `range_contraction_candle` | `implemented` | Range Contraction Candle is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `range_expansion` | `implemented` | Range Expansion is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `range_high_rejection` | `implemented` | Range High Rejection is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `range_low_rejection` | `implemented` | Range Low Rejection is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `range_breakdown` | `implemented` | Close breaks below recent range low. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `range_breakout` | `implemented` | Close breaks above recent range high. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `resistance_becomes_support` | `implemented` | Resistance Becomes Support is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `resistance_retest` | `implemented` | Price retests resistance and closes below it. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `retest_after_breakdown` | `implemented` | Retest After Breakdown is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `retest_after_breakout` | `implemented` | Retest After Breakout is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `breakout_retest` | `implemented` | Price retests a prior breakout level and holds. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `session_high_swept` | `implemented` | Session High Swept is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `session_low_swept` | `implemented` | Session Low Swept is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `shallow_pullback` | `implemented` | Shallow Pullback is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `sideways_market` | `implemented` | Sideways Market is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `support_becomes_resistance` | `implemented` | Support Becomes Resistance is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `support_retest` | `implemented` | Price retests support and closes above it. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `tight_consolidation` | `implemented` | Tight Consolidation is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `trend_continuation_after_pullback` | `implemented` | Trend Continuation After Pullback is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `weekly_high_swept` | `implemented` | Weekly High Swept is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `weekly_low_swept` | `implemented` | Weekly Low Swept is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `weekly_high_low` | `implemented` | Weekly high/low break or sweep. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Price Action | `wick_breaks_high_returns_below` | `implemented` | Wick Breaks High Returns Below is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `wick_breaks_low_returns_above` | `implemented` | Wick Breaks Low Returns Above is confirmed. | ohlcv | is_true, is_false |  |
| Price Action | `wide_range_candle` | `implemented` | Wide Range Candle is confirmed. | ohlcv | is_true, is_false |  |
| Ranking Universe | `bottom_percent_volatility` | `provider_required` | Bottom X% by Volatility is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: universe_ranking Configure a Two-pass universe ranking service before this condition can be activated. |
| Ranking Universe | `highest_compression_score` | `provider_required` | Highest Compression Score is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: universe_ranking Configure a Two-pass universe ranking service before this condition can be activated. |
| Ranking Universe | `highest_volume_expansion` | `provider_required` | Highest Volume Expansion is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: universe_ranking Configure a Two-pass universe ranking service before this condition can be activated. |
| Ranking Universe | `near_24h_high` | `provider_required` | Near 24h High is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: universe_ranking Configure a Two-pass universe ranking service before this condition can be activated. |
| Ranking Universe | `near_24h_low` | `provider_required` | Near 24h Low is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: universe_ranking Configure a Two-pass universe ranking service before this condition can be activated. |
| Ranking Universe | `strongest_btc_relative_strength` | `provider_required` | Strongest BTC-relative Strength is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: universe_ranking Configure a Two-pass universe ranking service before this condition can be activated. |
| Ranking Universe | `strongest_breakout_score` | `provider_required` | Strongest Breakout Score is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: universe_ranking Configure a Two-pass universe ranking service before this condition can be activated. |
| Ranking Universe | `strongest_pullback_score` | `provider_required` | Strongest Pullback Score is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: universe_ranking Configure a Two-pass universe ranking service before this condition can be activated. |
| Ranking Universe | `top_percent_1h_volume_change` | `provider_required` | Top X% by 1h Volume Change is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: universe_ranking Configure a Two-pass universe ranking service before this condition can be activated. |
| Ranking Universe | `top_percent_24h_volume` | `provider_required` | Top X% by 24h Volume is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: universe_ranking Configure a Two-pass universe ranking service before this condition can be activated. |
| Ranking Universe | `top_percent_distance_ema` | `provider_required` | Top X% by Distance from EMA is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: universe_ranking Configure a Two-pass universe ranking service before this condition can be activated. |
| Ranking Universe | `top_percent_momentum` | `provider_required` | Top X% by Momentum is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: universe_ranking Configure a Two-pass universe ranking service before this condition can be activated. |
| Ranking Universe | `top_percent_relative_volume` | `provider_required` | Top X% by Relative Volume is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: universe_ranking Configure a Two-pass universe ranking service before this condition can be activated. |
| Ranking Universe | `top_percent_trend_strength` | `provider_required` | Top X% by Trend Strength is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: universe_ranking Configure a Two-pass universe ranking service before this condition can be activated. |
| Ranking Universe | `top_percent_volatility` | `provider_required` | Top X% by Volatility is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: universe_ranking Configure a Two-pass universe ranking service before this condition can be activated. |
| Relative Strength | `eth_btc_relative_strength` | `provider_required` | ETH/BTC Relative Strength is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: cross_market Configure a Cross-market candles before this condition can be activated. |
| Relative Strength | `pair_beta_btc` | `provider_required` | Pair Beta vs BTC is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: cross_market Configure a Cross-market candles before this condition can be activated. |
| Relative Strength | `pair_correlation_btc` | `provider_required` | Pair Correlation with BTC is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: cross_market Configure a Cross-market candles before this condition can be activated. |
| Relative Strength | `pair_move_relative_btc` | `provider_required` | Pair Move Relative to BTC is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: cross_market Configure a Cross-market candles before this condition can be activated. |
| Relative Strength | `pair_volatility_vs_btc` | `provider_required` | Pair Volatility vs BTC is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: cross_market Configure a Cross-market candles before this condition can be activated. |
| Relative Strength | `symbol_outperforming_btc` | `provider_required` | Symbol Outperforming BTC is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: cross_market Configure a Cross-market candles before this condition can be activated. |
| Relative Strength | `symbol_outperforming_eth` | `provider_required` | Symbol Outperforming ETH is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: cross_market Configure a Cross-market candles before this condition can be activated. |
| Relative Strength | `symbol_underperforming_btc` | `provider_required` | Symbol Underperforming BTC is present as context; it does not recommend a trade. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: cross_market Configure a Cross-market candles before this condition can be activated. |
| Risk Trade Quality | `atr_stop` | `implemented` | Stop can be placed by ATR multiple. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Risk Trade Quality | `candle_overextended` | `requires_post_evaluation_context` | Candle Overextended satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `distance_to_reference` | `implemented` | Distance to Market Reference meets the configured threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Risk Trade Quality | `invalidation_not_calculable` | `requires_post_evaluation_context` | Invalidation Not Calculable satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `liquidity_obstacle_before_target` | `requires_post_evaluation_context` | Liquidity Obstacle Before Target satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `maximum_alert_lateness` | `requires_post_evaluation_context` | Maximum Alert Lateness satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `maximum_data_latency` | `requires_post_evaluation_context` | Maximum Data Latency satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `minimum_candle_liquidity` | `requires_post_evaluation_context` | Minimum Candle Liquidity satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `minimum_clean_path_to_target` | `requires_post_evaluation_context` | Minimum Clean Path To Target satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `price_moved_too_far_from_trigger` | `requires_post_evaluation_context` | Price Moved Too Far From Trigger satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `r_multiple_before_obstacle` | `requires_post_evaluation_context` | R Multiple Before Obstacle satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `reward_to_risk_after_fees` | `requires_post_evaluation_context` | Reward To Risk After Fees satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `reward_to_risk_after_slippage` | `requires_post_evaluation_context` | Reward To Risk After Slippage satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `risk_context_incomplete` | `requires_post_evaluation_context` | Risk Context Incomplete satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `setup_age_too_old` | `requires_post_evaluation_context` | Setup Age Too Old satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `spread_too_wide_at_alert` | `requires_post_evaluation_context` | Spread Too Wide At Alert satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `stop_distance_atr_units` | `requires_post_evaluation_context` | Stop Distance Atr Units satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `stop_distance_too_tight` | `requires_post_evaluation_context` | Stop Distance Too Tight satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `stop_distance_too_wide` | `requires_post_evaluation_context` | Stop Distance Too Wide satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `target_distance_next_resistance` | `requires_post_evaluation_context` | Target Distance Next Resistance satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `target_distance_next_support` | `requires_post_evaluation_context` | Target Distance Next Support satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `target_overlaps_obstacle` | `requires_post_evaluation_context` | Target Overlaps Obstacle satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `ulcer_index` | `implemented` | Ulcer Index meets the configured threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Risk Trade Quality | `volatility_too_high` | `requires_post_evaluation_context` | Volatility Too High satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Risk Trade Quality | `volatility_too_low` | `requires_post_evaluation_context` | Volatility Too Low satisfies the configured quality limit. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: risk_context The current rule engine calculates risk after the entry condition tree. This key is registered but cannot block the same evaluation yet. |
| Setup Lifecycle | `setup_age_minutes` | `requires_runtime_context` | Setup Age Minutes is satisfied. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: setup_lifecycle The platform enforces related policy outside the candle rule tree. A unified runtime-context operand is deferred. |
| Setup Lifecycle | `setup_entry_zone_active` | `requires_runtime_context` | Setup Entry Zone Active is satisfied. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: setup_lifecycle The platform enforces related policy outside the candle rule tree. A unified runtime-context operand is deferred. |
| Setup Lifecycle | `setup_first_detected_within` | `requires_runtime_context` | Setup First Detected Within is satisfied. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: setup_lifecycle The platform enforces related policy outside the candle rule tree. A unified runtime-context operand is deferred. |
| Setup Lifecycle | `setup_not_expired` | `requires_runtime_context` | Setup Not Expired is satisfied. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: setup_lifecycle The platform enforces related policy outside the candle rule tree. A unified runtime-context operand is deferred. |
| Setup Lifecycle | `setup_not_invalidated` | `requires_runtime_context` | Setup Not Invalidated is satisfied. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: setup_lifecycle The platform enforces related policy outside the candle rule tree. A unified runtime-context operand is deferred. |
| Setup Lifecycle | `setup_state_is` | `requires_runtime_context` | Setup State Is is satisfied. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Provider: setup_lifecycle The platform enforces related policy outside the candle rule tree. A unified runtime-context operand is deferred. |
| Time Session | `asia_session` | `implemented` | Asia Session in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `avoid_daily_reset` | `implemented` | Avoid Daily Reset in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `avoid_low_liquidity_hours` | `implemented` | Avoid Low Liquidity Hours in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `condition_after_timestamp` | `implemented` | Condition After Timestamp in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `condition_before_timestamp` | `implemented` | Condition Before Timestamp in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `condition_valid_until` | `implemented` | Condition Valid Until in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `daily_open` | `implemented` | Daily Open in the user's configured timezone. | candle_timestamp | gt, gte, lt, lte, eq |  |
| Time Session | `day_of_week` | `implemented` | Day Of Week in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `first_n_minutes_of_session` | `implemented` | First N Minutes Of Session in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `killzone_filter` | `implemented` | Signal candle timestamp must fall inside a named killzone. | candle_timestamp | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Time Session | `last_n_minutes_of_session` | `implemented` | Last N Minutes Of Session in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `london_session` | `implemented` | London Session in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `monthly_open` | `implemented` | Monthly Open in the user's configured timezone. | candle_timestamp | gt, gte, lt, lte, eq |  |
| Time Session | `new_day_breakout` | `implemented` | New Day Breakout in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `new_week_breakout` | `implemented` | New Week Breakout in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `new_york_session` | `implemented` | New York Session in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `previous_session_high_low` | `recognized_not_executable` | Previous session high/low break or sweep. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | Needs session segmentation across exchange candles. |
| Time Session | `session_close_window` | `implemented` | Session Close Window in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `session_expired` | `implemented` | Session Expired in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `session_open_window` | `implemented` | Session Open Window in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `specific_hour_range` | `implemented` | Specific Hour Range in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `specific_utc_session` | `implemented` | Specific Utc Session in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `time_since_condition_true` | `implemented` | Time Since Condition True in the user's configured timezone. | candle_timestamp | gt, gte, lt, lte, eq |  |
| Time Session | `time_since_last_alert` | `implemented` | Time Since Last Alert in the user's configured timezone. | candle_timestamp | gt, gte, lt, lte, eq |  |
| Time Session | `time_since_setup_detected` | `implemented` | Time Since Setup Detected in the user's configured timezone. | candle_timestamp | gt, gte, lt, lte, eq |  |
| Time Session | `time_window` | `implemented` | Signal candle timestamp must fall inside a time window. | candle_timestamp | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Time Session | `weekday_only` | `implemented` | Weekday Only in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `weekend_filter` | `implemented` | Weekend Filter in the user's configured timezone. | candle_timestamp | is_true, is_false |  |
| Time Session | `weekly_open` | `implemented` | Weekly Open in the user's configured timezone. | candle_timestamp | gt, gte, lt, lte, eq |  |
| Trend | `aroon` | `implemented` | Aroon oscillator crosses above zero. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `directional_movement_components` | `implemented` | Plus DI crosses above minus DI while ADX is above 25. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `ma_distance_percent` | `implemented` | Percent distance between close and EMA/SMA. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `double_exponential_moving_average` | `implemented` | EMA-derived average designed to reduce lag. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `ema_crossover` | `implemented` | Fast EMA crosses slow EMA. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `ema_slope` | `implemented` | EMA slope is positive or negative. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `ema_stack` | `implemented` | Multiple EMAs are ordered bullish or bearish. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `elder_impulse` | `implemented` | Elder Impulse is bullish. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `hull_moving_average` | `implemented` | Low-lag moving average composed from weighted moving averages. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `ichimoku_cloud` | `implemented` | Price is above the Ichimoku cloud. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `kaufman_adaptive_moving_average` | `implemented` | Adaptive average that changes smoothing with price efficiency. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `linear_regression_moving_average` | `implemented` | Endpoint value of a least-squares regression line. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `ma_reclaim` | `implemented` | Close crosses back above a moving average. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `ma_retest` | `implemented` | Price retests an EMA/SMA and closes back in trend direction. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `parabolic_sar` | `implemented` | Parabolic SAR flips bullish. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `price_above_ema` | `implemented` | Close is above a configured EMA. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `price_above_sma` | `implemented` | Close is above a configured SMA. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `price_below_ema` | `implemented` | Close is below a configured EMA. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `price_below_sma` | `implemented` | Close is below a configured SMA. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `sma_crossover` | `implemented` | Fast SMA crosses slow SMA. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `sma_slope` | `implemented` | SMA slope is positive or negative. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `supertrend` | `implemented` | SuperTrend flips bullish. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `triple_exponential_moving_average` | `implemented` | Triple-smoothed EMA combination designed to reduce lag. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `volume_weighted_moving_average` | `implemented` | Moving average weighted by each candle's volume. | ohlcv, volume | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `weighted_moving_average` | `implemented` | Moving average with linearly increasing weight on recent candles. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Trend | `zero_lag_ema` | `implemented` | EMA calculated from lag-adjusted source values. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volatility Squeeze | `atr_percent` | `implemented` | ATR as a percent of close. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volatility Squeeze | `atr_threshold` | `implemented` | ATR is above or below a configured value. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volatility Squeeze | `bollinger_percent_b` | `implemented` | Bollinger %B crosses above 0.5. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volatility Squeeze | `bollinger_touch` | `implemented` | High or low touches a Bollinger band. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volatility Squeeze | `bollinger_bandwidth_expansion` | `implemented` | Bollinger bandwidth is expanding. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volatility Squeeze | `bollinger_close_outside` | `implemented` | Close finishes outside upper or lower Bollinger band. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volatility Squeeze | `bollinger_reentry` | `implemented` | Price closes back inside Bollinger bands after closing outside. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volatility Squeeze | `bollinger_squeeze` | `implemented` | Bollinger Bands are inside Keltner Channels or have just fired. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volatility Squeeze | `choppiness_index` | `implemented` | Choppiness Index meets the configured threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volatility Squeeze | `donchian_channels` | `implemented` | Close breaks above the prior 20-candle Donchian high. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volatility Squeeze | `historical_volatility` | `implemented` | Historical Volatility meets the configured threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volatility Squeeze | `keltner_channels` | `implemented` | Close is above the upper Keltner Channel. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volatility Squeeze | `normalized_atr` | `implemented` | Normalized ATR meets the configured threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volatility Squeeze | `range_expansion_candle` | `implemented` | Current candle range is larger than average. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volatility Squeeze | `volatility_contraction` | `implemented` | Recent range is tightly compressed. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volume Flow | `accumulation_distribution` | `implemented` | Accumulation / Distribution Line meets the configured threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volume Flow | `buy_sell_pressure_proxy` | `implemented` | Buy / Sell Pressure Proxy meets the configured threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | OHLCV approximation; true trade-at-price or order-flow data is not available. |
| Volume Flow | `chaikin_money_flow` | `implemented` | Chaikin Money Flow meets the configured threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volume Flow | `dollar_volume` | `implemented` | Dollar Volume meets the configured threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volume Flow | `ease_of_movement` | `implemented` | Ease of Movement meets the configured threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volume Flow | `force_index` | `implemented` | Force Index meets the configured threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volume Flow | `on_balance_volume` | `implemented` | On Balance Volume meets the configured threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volume Flow | `price_vs_vwap` | `implemented` | Close is above or below VWAP. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volume Flow | `relative_volume_by_session` | `implemented` | Relative Volume by Session meets the configured threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volume Flow | `relative_volume_rising` | `implemented` | Relative volume is increasing. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volume Flow | `vwap_deviation_percent` | `implemented` | Percent distance between close and VWAP. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volume Flow | `vwap_reclaim` | `implemented` | Price crosses back above VWAP. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volume Flow | `volume_oscillator` | `implemented` | Volume Oscillator meets the configured threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volume Flow | `volume_profile_proxy` | `implemented` | Volume Profile Proxy meets the configured threshold. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below | OHLCV approximation; true trade-at-price or order-flow data is not available. |
| Volume Flow | `volume_breakout_confirmation` | `implemented` | Breakout is confirmed by relative volume. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volume Flow | `volume_dry_up` | `implemented` | Volume is below average during pullback. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volume Flow | `volume_ratio` | `implemented` | Volume is above or below its average. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
| Volume Flow | `volume_spike` | `implemented` | Volume is far above average. | ohlcv | gt, gte, lt, lte, eq, crosses_above, crosses_below |  |
