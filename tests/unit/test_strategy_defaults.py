from ai_market_monitor.schemas.strategy import NearMissPolicy, StrategyDefinition


def test_near_miss_policy_defaults_to_enabled():
    assert NearMissPolicy().enabled is True


def test_external_strategy_shape_enables_near_miss_by_default():
    strategy = StrategyDefinition.model_validate(
        {
            "strategy_name": "Simple Finder",
            "market_type": "spot",
            "exchanges": ["binance"],
            "quote_assets": ["USDT"],
            "primary_timeframe": "15m",
            "trigger_mode": "candle_close",
            "logic": {
                "operator": "AND",
                "conditions": [
                    {
                        "id": "rsi_above_50",
                        "name": "RSI above 50",
                        "type": "indicator",
                        "indicator": "rsi",
                        "operator": "gte",
                        "threshold": 50,
                        "timeframe": "15m",
                        "required_data": ["ohlcv"],
                    }
                ],
            },
            "risk_rules": {"enabled": False},
            "alert_rules": {"channels": ["web"]},
        }
    )

    assert strategy.near_miss.enabled is True


def test_condition_timeframes_are_auto_declared_for_validation():
    strategy = StrategyDefinition.model_validate(
        {
            "strategy_name": "Multi Timeframe Finder",
            "market_type": "spot",
            "exchanges": ["binance"],
            "quote_assets": ["USDT"],
            "primary_timeframe": "15m",
            "trigger_mode": "candle_close",
            "logic": {
                "operator": "AND",
                "conditions": [
                    {
                        "id": "daily_doji",
                        "name": "Daily doji",
                        "type": "candle_pattern",
                        "pattern": "doji",
                        "operator": "is_true",
                        "timeframe": "1d",
                        "required_data": ["ohlcv"],
                    }
                ],
            },
            "risk_rules": {"enabled": False},
            "alert_rules": {"channels": ["web"]},
        }
    )

    assert strategy.supporting_timeframes == ["1d"]


def test_internal_research_monitor_defaults_entry_risk_and_targets_to_neutral():
    strategy = StrategyDefinition.model_validate(
        {
            "name": "Research Only RSI Monitor",
            "base_timeframe": "15m",
            "supporting_timeframes": [],
            "trigger_mode": "candle_close",
            "universe": {
                "exchange": "binance",
                "market_type": "spot",
                "quote_currencies": ["USDT"],
                "include_symbols": ["SOL/USDT"],
            },
            "conditions": {
                "key": "required_research_rules",
                "operator": "and",
                "children": [
                    {
                        "key": "rsi_below_30",
                        "label": "RSI below 30",
                        "condition_type": "indicator",
                        "timeframe": "15m",
                        "left": {
                            "kind": "indicator",
                            "name": "rsi",
                            "parameters": {"period": 14},
                        },
                        "comparator": "lt",
                        "right": {"kind": "constant", "value": 30},
                        "required": True,
                        "required_data": ["ohlcv"],
                    }
                ],
            },
            "alerts": {"channels": ["web"]},
        }
    )

    assert strategy.risk.enabled is False
    assert strategy.targets == []
    assert strategy.entry.calculation == "signal_close"
