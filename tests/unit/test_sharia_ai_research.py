import json

import httpx
import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.sharia_research import (
    ShariaAIResearchClient,
    ShariaResearchError,
)


def _settings(**changes) -> Settings:
    values = {
        "app_env": "test",
        "app_secret_key": SecretStr(
            "sharia-ai-test-secret-at-least-thirty-two-characters"
        ),
        "openai_api_key": SecretStr("server-only-sharia-test-key"),
        "sharia_ai_model": "gpt-5.4-nano",
        "sharia_ai_reasoning_effort": "low",
        "sharia_ai_service_tier": "flex",
    }
    values.update(changes)
    return Settings(**values)


def _valid_analysis() -> dict:
    return {
        "canonical_identity_conclusion": "confirmed",
        "profile": {
            "project_identity": "A verified native network asset.",
            "primary_activity": "Peer-to-peer settlement.",
            "token_role": "Native network unit.",
            "staking": "No native proof-of-stake evidence.",
            "lending_and_yield": "Third-party products are separate uses.",
            "derivatives": "Derivative products are outside spot scope.",
            "treasury_and_governance": "No protocol treasury was established.",
            "tokenomics_and_backing": "Protocol-defined issuance.",
        },
        "relevant_activity_categories": ["native_asset"],
        "evidence_references": [
            {
                "snapshot_id": "snapshot-1",
                "category": "official_documentation",
                "finding": "The official documentation describes a native network unit.",
            }
        ],
        "missing_evidence": [],
        "contradictions": [],
        "change_type": "initial_research",
        "potential_impact_severity": "none",
        "potentially_affected_methodology_areas": [],
        "human_review_required": True,
        "human_review_reason": "Publication requires authenticated human review.",
        "recommended_next_action": "human_review",
        "confidence": 0.92,
        "explicit_limitations": ["This factual analysis is not a religious ruling."],
    }


async def test_sharia_ai_uses_one_flex_request_for_aggregated_evidence():
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(_valid_analysis()),
                "usage": {"input_tokens": 120, "output_tokens": 60},
                "service_tier": "flex",
            },
        )

    result = await ShariaAIResearchClient(
        _settings(),
        transport=httpx.MockTransport(handler),
    ).analyze(
        {
            "asset": {"symbol": "BTC"},
            "sources": [
                {"snapshot_id": "snapshot-1", "text": "Official website evidence"},
                {"snapshot_id": "snapshot-2", "text": "Official documentation evidence"},
            ],
        }
    )

    assert len(requests) == 1
    assert requests[0]["model"] == "gpt-5.4-nano"
    assert requests[0]["reasoning"] == {"effort": "low"}
    assert requests[0]["service_tier"] == "flex"
    assert requests[0]["store"] is False
    assert requests[0]["text"]["format"]["strict"] is True
    assert "server-only-sharia-test-key" not in json.dumps(requests)
    assert result.returned_service_tier == "flex"
    assert result.analysis.human_review_required is True


async def test_invalid_ai_output_gets_one_repair_then_fails_closed():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"output_text": '{"status":"publish"}'})

    with pytest.raises(ShariaResearchError, match="failed the factual-dossier schema"):
        await ShariaAIResearchClient(
            _settings(),
            transport=httpx.MockTransport(handler),
        ).analyze({"asset": {"symbol": "BTC"}, "sources": []})

    assert calls == 2
