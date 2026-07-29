import pytest

from ai_market_monitor.engine.setup_intent import decide_setup_intent
from ai_market_monitor.schemas.strategy_draft_v2 import SetupIntent


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hi, how are you?", SetupIntent.CONVERSATION),
        ("What does HilalMarkets monitor?", SetupIntent.PRODUCT_QUESTION),
        ("Approve this exact version", SetupIntent.APPROVAL_ACTION),
        ("Explain that more simply", SetupIntent.EXPLANATION_REQUEST),
        (
            "Monitor BTC/USDT when the 15m candle rises open-to-close by 4%",
            SetupIntent.STRATEGY_PATCH,
        ),
        ("Buy it for me now", SetupIntent.UNSUPPORTED_REQUEST),
        ("Yeah, let's not overcomplicate the BTC part.", SetupIntent.CONVERSATION),
        ("Not heavy formulas?", SetupIntent.CONVERSATION),
        ("No more questions.", SetupIntent.CONVERSATION),
        (
            "It ensures we're not accidentally mixing other pairs/data.",
            SetupIntent.CONVERSATION,
        ),
        ("Yalla approve this", SetupIntent.APPROVAL_ACTION),
        (
            "\u0648\u0627\u0641\u0642 \u0639\u0644\u0649 \u0647\u0630\u0647 "
            "\u0627\u0644\u0646\u0633\u062e\u0629",
            SetupIntent.APPROVAL_ACTION,
        ),
        (
            "Build BTCUSDT above 5% and keep approval explicit.",
            SetupIntent.STRATEGY_PATCH,
        ),
        (
            "Use 1h context, then show a literal Approve: yes/no gate.",
            SetupIntent.STRATEGY_PATCH,
        ),
        (
            "Do you want an approval required label?",
            SetupIntent.PRODUCT_QUESTION,
        ),
    ],
)
def test_intent_gate_is_mutually_exclusive(text, expected):
    decision = decide_setup_intent(text)

    assert decision.intent is expected
    assert isinstance(decision.intent, SetupIntent)
    assert decision.requires_structured_extraction is (
        expected is SetupIntent.STRATEGY_PATCH
    )


def test_explanation_does_not_become_strategy_patch():
    decision = decide_setup_intent("Why did you use that timeframe?")

    assert decision.intent is SetupIntent.EXPLANATION_REQUEST
    assert not decision.requires_structured_extraction
