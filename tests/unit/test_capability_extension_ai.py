import json

import httpx
import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.schemas.capability_extensions import MechanicReview
from ai_market_monitor.services.capability_extension_ai import (
    CapabilityExtensionAI,
    CapabilityExtensionAIError,
)


def _settings(**changes) -> Settings:
    values = {
        "app_env": "test",
        "app_secret_key": SecretStr(
            "extension-ai-test-secret-at-least-thirty-two-characters"
        ),
        "openai_api_key": SecretStr("server-only-test-key"),
        "capability_extension_ai_max_attempts": 1,
    }
    values.update(changes)
    return Settings(**values)


def _draft_payload() -> dict:
    return {
        "label": "Large candle body",
        "deterministic_definition": (
            "The current candle body occupies more than 70 percent of its full range."
        ),
        "timeframe": "15m",
        "parameters": [],
        "resolved_parameters": {},
        "expression": {
            "op": "gt",
            "left": {"op": "candle_metric", "name": "body_percent"},
            "right": {"op": "constant", "value": 70},
        },
        "proof_template": "Candle body percentage was checked against 70 percent.",
        "assumptions": [],
        "expected_frequency": "occasional",
        "logic_fidelity_statement": "This directly implements the requested body condition.",
    }


def _response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "output_text": json.dumps(payload),
            "usage": {"input_tokens": 120, "output_tokens": 60},
        },
    )


async def test_extension_ai_uses_requested_model_effort_history_and_flex_tier():
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        schema_name = body["text"]["format"]["name"]
        if schema_name == "traceedge_mechanic_draft":
            return _response(_draft_payload())
        if schema_name == "traceedge_mechanic_review":
            return _response(
                {
                    "verdict": "pass",
                    "failure_source": "none",
                    "preserves_user_logic": True,
                    "confidence": 0.95,
                    "candidate_quality": "balanced",
                    "issues": [],
                    "recommended_changes": [],
                    "explanation": "The implementation matches the requested measurable logic.",
                }
            )
        return _response(
            {
                "revised_draft": _draft_payload(),
                "changed_implementation_only": True,
                "user_logic_changed": False,
                "applied_changes": ["Corrected implementation"],
                "deferred_changes": [],
            }
        )

    ai = CapabilityExtensionAI(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    history = [{"role": "user", "content": "Keep the threshold at 70%."}]
    draft = await ai.draft(
        prompt="Find large candle bodies",
        history=history,
        timeframe="15m",
        model="gpt-5.4-nano",
        reasoning_effort="low",
    )
    review = await ai.review(
        prompt="Find large candle bodies",
        history=history,
        draft=draft,
        build_log=[],
        market_report={"classification": "balanced"},
        model="gpt-5.4-mini",
        reasoning_effort="high",
        service_tier="flex",
    )
    await ai.repair(
        prompt="Find large candle bodies",
        history=history,
        draft=draft,
        review=MechanicReview.model_validate(review),
        build_log=[],
        reasoning_effort="low",
    )

    assert requests[0]["model"] == "gpt-5.4-nano"
    assert requests[0]["reasoning"] == {"effort": "low"}
    assert "service_tier" not in requests[0]
    assert history[0]["content"] in requests[0]["input"]
    assert requests[1]["model"] == "gpt-5.4-mini"
    assert requests[1]["reasoning"] == {"effort": "high"}
    assert requests[1]["service_tier"] == "flex"
    assert requests[2]["model"] == "gpt-5.4-nano"
    assert requests[2]["reasoning"] == {"effort": "low"}
    assert requests[2]["service_tier"] == "flex"
    assert "server-only-test-key" not in json.dumps(requests)


async def test_extension_ai_fails_closed_without_api_key():
    settings = _settings(openai_api_key=None)
    with pytest.raises(CapabilityExtensionAIError, match="OPENAI_API_KEY"):
        await CapabilityExtensionAI(settings).draft(
            prompt="Find large candle bodies",
            history=[],
            timeframe="15m",
        )
