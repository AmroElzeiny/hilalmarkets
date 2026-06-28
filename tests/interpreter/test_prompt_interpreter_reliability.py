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


SUPPORTED_PROMPTS = [
    *(f"Find coins up {value}% today." for value in range(1, 21)),
    *(f"Show USDT spot pairs that dropped {value}% in the last 24h." for value in range(1, 21)),
    *(f"RSI below {value} on 15m." for value in range(20, 40)),
    *(f"RSI above {value} on 1h." for value in range(50, 70)),
    *(f"Price above 4h EMA {period}." for period in range(20, 220, 20)),
    *(f"Price below 1h SMA {period}." for period in range(20, 220, 20)),
    *(f"Volume at least {value / 10:.1f}x average." for value in range(11, 21)),
    "RSI crosses back above 30 with volume above average.",
    "MACD histogram turns positive on 1h.",
    "Price above 4h EMA 200 and RSI exits oversold on 15m.",
    "Breakout above yesterday high.",
    "Break and retest previous range high.",
    "Liquidity sweep below prior low then bullish engulfing.",
    "Previous candle must be bullish.",
    "Previous candle must be bearish.",
    "No bearish engulfing in the last 5 candles.",
    "Find symbols with 5 days in a row daily candles red.",
    "Find symbols where the daily candle is not doji.",
    "Volume spike with price above EMA 200.",
    "VWAP reclaim with strong volume.",
]

BLOCKED_PROMPTS = [
    "Find strong coins.",
    "Find coins ready to pump.",
    "Good setups near support.",
    "High probability trades.",
    "Only if BTC is above EMA 200 on 1h.",
    "Find alts outperforming BTC.",
    "ETH/BTC trending up.",
    "Max stop 2%, minimum 2R.",
]

PROMPT_CASES = [(prompt, False) for prompt in SUPPORTED_PROMPTS[:100]] + [
    (prompt, True) for prompt in BLOCKED_PROMPTS
]


@pytest.mark.parametrize(("prompt", "expected_blocked"), PROMPT_CASES)
async def test_prompt_interpreter_reliability_contract(prompt: str, expected_blocked: bool):
    preview = await RuleBasedStrategyInterpreter().interpret(_guided(prompt))
    report = preview.raw_metadata.get("prompt_coverage_report")

    assert report, prompt
    assert "coverage_score" in report
    assert "confidence_score" in report
    assert preview.strategy.canonical_hash()
    assert preview.activation_blocked is expected_blocked

    unclassified = [
        row for row in report["mapping_table"] if row["bucket"] == "unclassified"
    ]
    assert not unclassified, prompt

    for condition in preview.strategy.conditions.children:
        assert condition.source_fragment, condition.key
        assert condition.confidence is not None, condition.key


async def test_optional_vs_required_scope_is_preserved():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided("Must have RSI below 30, volume confirmation is optional.")
    )
    conditions = {condition.key: condition for condition in preview.strategy.conditions.children}

    assert conditions["rsi_below_30"].required is True
    assert conditions["relative_volume"].required is False
    assert preview.activation_blocked is False


async def test_cross_symbol_context_blocks_until_provider_context_exists():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided("Only if BTC is above EMA 200 on 1h.")
    )

    assert preview.activation_blocked is True
    assert any(
        issue.code == "cross_symbol_context_provider_required"
        for issue in preview.unsupported_conditions
    )
