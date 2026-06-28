import pytest

from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter


def _guided(prompt: str, timeframe: str = "15m") -> GuidedSetupRequest:
    return GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe=timeframe,
        setup_mode="free_text",
        setup_text=prompt,
        trigger_mode="candle_close",
        delivery_channels=["web"],
    )


PROMPT_CASES = [
    ("price", "Find coins up 5% today", False),
    ("price", "Show symbols that dropped 3% in the last 24h", False),
    ("price", "Price above 1000 dollars", False),
    ("price", "All time high breakout on 15m", False),
    ("indicators", "RSI below 30 on 15m", False),
    ("indicators", "RSI above 50 on 1h", False),
    ("indicators", "MFI below 20 and price above HMA 55 on 1h", False),
    ("indicators", "MACD histogram turns positive on 1h", False),
    ("trend", "Price above 4h EMA 200", False),
    ("trend", "Price below 1h SMA 50", False),
    ("trend", "EMA 20 crosses above EMA 50", False),
    ("trend", "ADX above 25", False),
    ("momentum", "Stochastic RSI crosses above 20", False),
    ("momentum", "ROC above 2", False),
    ("volume_flow", "Volume at least 1.5x average", False),
    ("volume_flow", "Volume spike with price above EMA 200", False),
    ("volume_flow", "VWAP reclaim with strong volume", False),
    ("volatility", "ATR above 2 on 15m", False),
    ("volatility", "Bollinger squeeze on 15m", False),
    ("candle", "Previous candle must be bullish", False),
    ("candle", "Previous candle must be bearish", False),
    ("candle", "Find symbols where the daily candle is not doji", False),
    ("candle", "No bearish engulfing in the last 5 candles", False),
    ("candle", "Find symbols with 5 days in a row daily candles red", False),
    ("price_action", "Breakout above yesterday high", False),
    ("price_action", "Break and retest previous range high", False),
    ("price_action", "Liquidity sweep below prior low then bullish engulfing", False),
    ("price_action", "Sweep highs and bearish engulfing", False),
    ("market_structure", "bullish break of structure", False),
    ("market_structure", "higher high on 15m", False),
    ("liquidity", "equal highs liquidity pool then sweep highs", False),
    ("time", "Only alert during New York session", False),
    ("time", "Alert near midnight UTC", False),
    ("time", "previous candle bullish today", False),
    ("context", "Only if BTC is above EMA 200 on 1h", True),
    ("context", "Find alts outperforming BTC", True),
    ("context", "ETH/BTC trending up", True),
    ("risk", "Max stop 2%, minimum 2R", True),
    ("alert", "confirmed setups only with max 10 alerts per hour", True),
    ("logic", "RSI below 30 and volume confirmation optional", False),
    ("logic", "Must have RSI below 30, volume confirmation is optional", False),
    ("logic", "avoid bearish engulfing with RSI above 50", False),
    ("logic", "without doji candle and volume above average", False),
    ("logic", "unless BTC is weak, RSI below 30", True),
    ("finder", "bring me symbols with previous green daily candle", False),
    ("finder", "find me symbols that had a 1 minute candle up 1% in the last 60 minutes", False),
    ("finder", "find any symbol that grew 5% or more today", False),
    ("finder", "coins decreasing by 4% in the last week", False),
    ("provider", "top 10 percent by market cap", True),
    ("provider", "large order book wall above price", True),
    ("provider", "open interest rising", True),
    ("provider", "funding rate negative", True),
    ("planned", "pivot high and low", True),
    ("volatility", "range expansion candle", False),
    ("price_action", "moving average retest", False),
    ("vague", "Find strong coins", True),
    ("vague", "Find coins ready to pump", True),
    ("vague", "Good setups near support", True),
    ("vague", "High probability trades", True),
    ("indicators", "price above Keltner channel", False),
]


@pytest.mark.parametrize(("category", "prompt", "expected_blocked"), PROMPT_CASES)
async def test_prompt_to_strategy_end_to_end_contract(
    category: str, prompt: str, expected_blocked: bool
):
    preview = await RuleBasedStrategyInterpreter().interpret(_guided(prompt))
    report = preview.raw_metadata.get("prompt_coverage_report")

    assert category
    assert report, prompt
    assert preview.activation_blocked is expected_blocked, prompt
    assert preview.strategy.canonical_hash()

    unclassified = [
        row for row in report["mapping_table"] if row["bucket"] == "unclassified"
    ]
    assert unclassified == [], prompt
    for condition in preview.strategy.conditions.children:
        assert condition.source_fragment, condition.key
        assert condition.confidence is not None, condition.key
    for issue in [*preview.ambiguities, *preview.unsupported_conditions]:
        assert issue.source_fragment, issue.code
