from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter


def _guided(text: str) -> GuidedSetupRequest:
    return GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        setup_text=text,
        trigger_mode="candle_close",
        maximum_stop_percent=2,
        minimum_reward_to_risk=2,
        delivery_channels=["telegram"],
    )


async def test_interpreter_understands_percent_moves_and_sessions():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided("coins increasing by 5% near NY session")
    )

    children = preview.strategy.conditions.children
    names = [child.left.name for child in children]
    assert "percent_change_up" in names
    assert "time_window" in names
    assert "percent_move" in preview.raw_metadata["detected_categories"]
    assert "session_timing" in preview.raw_metadata["detected_categories"]
    assert preview.activation_blocked is False


async def test_interpreter_understands_crossing_all_time_high():
    preview = await RuleBasedStrategyInterpreter().interpret(
        _guided("coins crossing all time highs in the last 6 months")
    )

    child = preview.strategy.conditions.children[0]
    assert child.left.name == "higher_high"
    assert child.left.parameters["lookback"] > 1000
    assert "all_time_high" in preview.raw_metadata["detected_categories"]
