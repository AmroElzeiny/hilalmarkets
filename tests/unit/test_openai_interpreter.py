import json

import httpx
from pydantic import SecretStr

from ai_market_monitor.ai_explanations import OpenAISuggestionNarrator
from ai_market_monitor.core.config import Settings
from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.services.openai_interpreter import (
    OpenAIStrategyInterpreter,
    _extract_output_text,
    _loads_json_object,
)


class FakeOpenAIClient:
    async def create_draft(self, guided_setup: GuidedSetupRequest):
        return {
            "strategy_name": "AI Liquidity Sweep",
            "description": guided_setup.setup_text,
            "direction": "long",
            "market_type": "spot",
            "exchanges": ["binance"],
            "quote_assets": ["USDT"],
            "symbols": [],
            "excluded_symbols": [],
            "primary_timeframe": "15m",
            "higher_timeframes": ["4h"],
            "trigger_mode": "candle_close",
            "logic": {
                "operator": "AND",
                "conditions": [
                    {
                        "capability_key": "price_above_ema",
                        "condition_id": "price_above_4h_ema_200",
                        "name": "Price above 4h EMA 200",
                        "type": "indicator",
                        "operator": "gt",
                        "timeframe": "4h",
                        "operand_kind": "price",
                        "field": "close",
                        "right": {
                            "kind": "indicator",
                            "name": "ema",
                            "parameters": {"period": 200},
                        },
                        "weight": 1,
                        "mandatory": True,
                    }
                ],
            },
            "entry": {},
            "stop": {"method": "structure"},
            "targets": [{"label": "T1", "method": "risk_multiple", "value": 2.5}],
            "risk_rules": {
                "maximum_stop_percent": 2,
                "target_method": "risk_multiple",
                "target_value": 2.5,
                "minimum_reward_to_risk": 2.5,
            },
            "liquidity_rules": {"min_quote_volume_24h": 1000000},
            "near_miss_rules": {"thresholds": [70, 80, 90]},
            "alert_rules": {
                "forming_alerts": True,
                "near_miss_threshold": 70,
                "channels": ["telegram"],
                "maximum_alerts_per_hour": 10,
            },
            "expiry_rules": {},
            "assumptions": ["AI mapped trend filter to EMA 200."],
            "ambiguities": [],
            "unsupported_conditions": [],
        }


class BrokenOpenAIClient:
    async def create_draft(self, guided_setup: GuidedSetupRequest):
        raise ValueError("invalid ai response")


class WrongPercentOpenAIClient(FakeOpenAIClient):
    async def create_draft(self, guided_setup: GuidedSetupRequest):
        payload = await super().create_draft(guided_setup)
        payload["logic"]["conditions"] = [
            {
                "capability_key": "price_above_ema",
                "condition_id": "price_above_5",
                "name": "Price above 5",
                "type": "indicator",
                "operator": "gt",
                "timeframe": "15m",
                "operand_kind": "price",
                "field": "close",
                "threshold": 5,
                "weight": 1,
                "mandatory": True,
            }
        ]
        payload["higher_timeframes"] = []
        return payload


async def test_openai_interpreter_validates_ai_draft_into_strategy_schema():
    settings = Settings(
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        ai_interpreter_provider="openai",
        openai_api_key=SecretStr("test-key"),
    )
    guided = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        setup_text="Find liquidity sweeps above the four-hour 200 EMA.",
        trigger_mode="candle_close",
        maximum_stop_percent=2,
        minimum_reward_to_risk=2.5,
        delivery_channels=["telegram"],
    )

    preview = await OpenAIStrategyInterpreter(settings, client=FakeOpenAIClient()).interpret(guided)

    assert preview.interpreter == "openai-structured-v1:gpt-5.4-nano"
    assert preview.strategy.universe.exchange == "binance"
    assert preview.strategy.supporting_timeframes == ["4h"]
    assert preview.assumptions == ["AI mapped trend filter to EMA 200."]
    condition = preview.strategy.conditions.children[0]
    assert condition.capability_key == "price_above_ema"
    assert condition.left.field == "close"
    assert condition.right.name == "ema"


async def test_openai_percent_guard_preserves_percent_change_prompts():
    settings = Settings(
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        ai_interpreter_provider="openai",
        openai_api_key=SecretStr("test-key"),
    )
    guided = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        setup_text="find any symbol that grew 5% or more today",
        trigger_mode="candle_close",
        delivery_channels=["web"],
    )

    preview = await OpenAIStrategyInterpreter(
        settings,
        client=WrongPercentOpenAIClient(),
    ).interpret(guided)

    condition = preview.strategy.conditions.children[0]
    assert preview.interpreter == "rules-v2:openai_percent_guard"
    assert condition.left.name == "percent_change_up"
    assert condition.left.parameters["threshold_percent"] == 5
    assert condition.left.parameters["lookback"] == 96


async def test_openai_fallback_does_not_expose_internal_provider_message():
    settings = Settings(
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        ai_interpreter_provider="openai",
        openai_api_key=SecretStr("test-key"),
    )
    guided = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        setup_text="Find bullish liquidity sweeps with volume at least 1.5 times average.",
        trigger_mode="candle_close",
        maximum_stop_percent=2,
        minimum_reward_to_risk=2.5,
        delivery_channels=["telegram"],
    )

    preview = await OpenAIStrategyInterpreter(settings, client=BrokenOpenAIClient()).interpret(
        guided
    )

    assert preview.interpreter == "rules-v2:openai_fallback"
    assert "openai_error" in preview.raw_metadata
    assert not any("OpenAI interpretation" in item for item in preview.assumptions)
    assert not any("conservative rule parser" in item for item in preview.assumptions)


def test_openai_response_parser_accepts_top_level_output_text_and_json_fence():
    payload = {"output_text": '```json\n{"strategy_name":"Demo","assumptions":[]}\n```'}

    assert _loads_json_object(_extract_output_text(payload))["strategy_name"] == "Demo"


async def test_openai_suggestion_narrator_only_returns_bounded_wording():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["reasoning"]["effort"] == "low"
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "message": (
                            "This draft increases the cooldown using the validated diff. "
                            "Review and approve it before activation."
                        )
                    }
                )
            },
        )

    settings = Settings(
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        ai_interpreter_provider="openai",
        openai_api_key=SecretStr("test-key"),
    )
    message = await OpenAISuggestionNarrator(
        settings,
        transport=httpx.MockTransport(handler),
    ).narrate(
        action="make_less_noisy",
        deterministic_reason="Increase the alert cooldown.",
        diff=[{"path": "alerts.cooldown_seconds", "before": 900, "after": 1800}],
        bottleneck={"condition_key": "volume"},
    )

    assert message is not None
    assert "draft" in message.lower()
    assert "validated diff" in message.lower()
