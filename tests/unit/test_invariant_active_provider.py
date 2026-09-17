"""Every ACTIVE feature calls OpenCode Go, and every stale one is untouched.

Mission WP3. ``services/ai_provider.py`` owns the provider table, the request
URL and headers, and the answer reader. The active features below must send
model ``muse-spark-1.3-contributor`` with effort ``high`` to the OpenCode Go
``/responses`` endpoint with a Bearer key and an ``x-opencode-session`` header,
and must never send ``service_tier``. The stale Setup Chat family keeps
byte-identical OpenAI requests through the same shared clients.

A second rule lives here too: the provider groups a conversation's turns by a
conversation or run id carried in ``session_key``. It is never a user id and
never an email. Features without a conversation id use one random id per call.
"""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services import ai_provider
from ai_market_monitor.services.ai_provider import MUSE_SPARK_MODEL

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "ai_market_monitor"
SERVICES = SRC / "services"

MUSE = MUSE_SPARK_MODEL
OPENCODE_URL = "https://opencode.ai/zen/go/v1/responses"


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": "test",
        "app_secret_key": SecretStr("active-provider-test-secret-at-least-32-chars"),
        "opencode_go_api_key": SecretStr("zen-test-key"),
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


class SessionCapturingClient:
    """A provider client double that records the conversation grouping key."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.payloads: list[dict[str, Any]] = []
        self.session_keys: list[str | None] = []

    async def create(
        self,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
        session_key: str | None = None,
    ) -> dict[str, Any]:
        self.payloads.append(payload)
        self.session_keys.append(session_key)
        return self.response


def _message_response(text: str) -> dict[str, Any]:
    return {
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": text}]},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


# --------------------------------------------------------------------------------
# ACTIVE features: model, effort, endpoint, headers, no service_tier.
# --------------------------------------------------------------------------------


async def test_hilal_turn_sends_muse_high_without_service_tier() -> None:
    from ai_market_monitor.services.hilal_chat_agent import HilalChatAgent

    reply = {"mode": "ANSWER", "reply": "Here is what is recorded.", "language": "English"}
    client = SessionCapturingClient(_message_response(json.dumps(reply)))
    call = await HilalChatAgent(_settings(), client=client).answer(
        question="Is BTC listed?",
        history=[],
        evidence={},
        first_time=False,
        display_name=None,
        session_key="hilal-conversation-1",
    )
    assert call.model == MUSE
    assert call.reasoning_effort == "high"
    payload = client.payloads[0]
    assert payload["model"] == MUSE
    assert payload["reasoning"] == {"effort": "high"}
    assert "service_tier" not in payload
    assert client.session_keys == ["hilal-conversation-1"]


async def test_public_support_turn_sends_muse_high_without_service_tier() -> None:
    from ai_market_monitor.services.public_support_ai import PublicSupportAIService

    answer = {
        "stage": "ANSWER",
        "mode": "PRODUCT_FACT",
        "intent": "product_help",
        "answer": "Hilal Markets monitors crypto spot rules.",
        "clarification_question": None,
        "source_ids": [],
        "related_route_ids": [],
        "requested_tools": [],
        "confidence": 0.9,
        "answer_complete": True,
        "support_handoff_available": False,
        "support_handoff_reason": None,
        "safety_boundary": "product_scope_only",
        "suggested_follow_ups": [],
    }
    client = SessionCapturingClient(_message_response(json.dumps(answer)))
    call = await PublicSupportAIService(_settings(), client=client).respond(
        question="What does Hilal Markets do?",
        history=[],
        conversation_state={},
        knowledge_documents=[],
        allowed_tools=[],
        authenticated=False,
        session_key="public-conversation-1",
    )
    assert call.model == MUSE
    assert call.reasoning_effort == "high"
    payload = client.payloads[0]
    assert payload["model"] == MUSE
    assert payload["reasoning"] == {"effort": "high"}
    assert "service_tier" not in payload
    assert client.session_keys == ["public-conversation-1"]


async def test_system_brain_assistant_sends_muse_high_without_service_tier(
    test_context, monkeypatch
) -> None:
    from ai_market_monitor.db.models import User
    from ai_market_monitor.db.models.enums import UserRole
    from ai_market_monitor.schemas.system_brain import SystemBrainAssistantRequest
    from ai_market_monitor.services.system_brain_assistant import (
        SystemBrainAssistantService,
    )

    settings = test_context["settings"]
    settings.opencode_go_api_key = "zen-test-key"
    body = {
        "answer": "One retained error needs review.",
        "findings": [],
        "suggested_actions": [],
        "evidence_refs": [],
        "limitations": [],
        "model": MUSE,
        "reasoning_effort": "high",
    }
    client = SessionCapturingClient(_message_response(json.dumps(body)))
    service = SystemBrainAssistantService(settings, client=client)

    async def bounded_context(_session, _question):
        return {"operational_errors": {"failed_runs": []}}

    monkeypatch.setattr(service, "_context", bounded_context)
    async with test_context["session_factory"]() as session:
        admin = User(display_name="Reviewer", role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()
        result = await service.answer(
            session,
            admin_user_id=admin.id,
            request=SystemBrainAssistantRequest(message="What failed?", history=[]),
        )

    assert result.model == MUSE
    assert result.reasoning_effort == "high"
    payload = client.payloads[0]
    assert payload["model"] == MUSE
    assert payload["reasoning"] == {"effort": "high"}
    assert "service_tier" not in payload
    # No conversation id exists on this single-shot call, so one random id
    # per call: recorded as None here, the shared client turns it random.
    assert client.session_keys == [None]


def _valid_dossier_analysis() -> dict[str, Any]:
    profile = {
        "project_identity": "Example project.",
        "primary_activity": "Spot trading venue.",
        "token_role": "Utility token.",
        "staking": "No native staking.",
        "lending_and_yield": "No lending.",
        "derivatives": "No derivatives.",
        "treasury_and_governance": "Team treasury.",
        "tokenomics_and_backing": "Fixed supply.",
    }
    return {
        "canonical_identity_conclusion": "uncertain",
        "profile": profile,
        "relevant_activity_categories": ["spot_trading"],
        "evidence_references": [],
        "missing_evidence": ["whitepaper"],
        "contradictions": [],
        "change_type": "initial_research",
        "potential_impact_severity": "low",
        "potentially_affected_methodology_areas": [],
        "human_review_required": True,
        "human_review_reason": "A person must confirm the identity.",
        "recommended_next_action": "human_review",
        "confidence": 0.4,
        "explicit_limitations": ["Only one source was readable."],
    }


async def test_sharia_dossier_call_sends_muse_high_without_service_tier() -> None:
    from ai_market_monitor.services.sharia_research import ShariaAIResearchClient

    seen: list[httpx.Request] = []
    bodies: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                **_message_response(json.dumps(_valid_dossier_analysis())),
                "usage": {"input_tokens": 40, "output_tokens": 60},
            },
        )

    client = ShariaAIResearchClient(
        _settings(), transport=httpx.MockTransport(handler)
    )
    result = await client.analyze({"evidence_package": "fixture"})
    assert result.analysis.human_review_required is True
    assert str(seen[0].url) == OPENCODE_URL
    assert seen[0].headers["authorization"] == "Bearer zen-test-key"
    assert seen[0].headers["x-opencode-session"]
    assert bodies[0]["model"] == MUSE
    assert bodies[0]["reasoning"] == {"effort": "high"}
    assert "service_tier" not in bodies[0]


async def test_sharia_source_discovery_sends_muse_high_without_service_tier() -> None:
    from ai_market_monitor.services.sharia_source_ai_discovery import AISourceDiscovery

    seen: list[httpx.Request] = []
    bodies: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200, json=_message_response(json.dumps({"addresses": []}))
        )

    settings = _settings(sharia_source_ai_discovery_enabled=True)
    discovery = AISourceDiscovery(settings, transport=httpx.MockTransport(handler))
    assert discovery.configured is True
    assert await discovery.suggest(asset_name="Example", symbol="EXM", official_website=None) == ()
    assert str(seen[0].url) == OPENCODE_URL
    assert seen[0].headers["authorization"] == "Bearer zen-test-key"
    assert seen[0].headers["x-opencode-session"]
    assert bodies[0]["model"] == MUSE
    assert bodies[0]["reasoning"] == {"effort": "high"}
    assert "service_tier" not in bodies[0]


def test_source_monitoring_reaches_the_model_only_through_the_dossier_client() -> None:
    from ai_market_monitor.services.sharia_research import ShariaAIResearchClient
    from ai_market_monitor.services.sharia_source_monitoring import (
        ShariaSourceMonitoringService,
    )

    service = ShariaSourceMonitoringService(None, _settings())  # type: ignore[arg-type]
    assert isinstance(service.ai, ShariaAIResearchClient)


# --------------------------------------------------------------------------------
# The flex-to-default retry is a NO-OP on OpenCode Go, kept on OpenAI.
# --------------------------------------------------------------------------------


async def test_muse_flex_retry_never_resends_with_a_tier() -> None:
    from ai_market_monitor.services.sharia_research import (
        ShariaAIResearchClient,
        ShariaResearchError,
    )

    bodies: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(429, json={"error": {"type": "rate_limit", "code": "429"}})

    settings = _settings(
        provider_retry_max_attempts=1,
        sharia_ai_allow_standard_fallback=True,
        sharia_ai_service_tier="flex",
    )
    client = ShariaAIResearchClient(settings, transport=httpx.MockTransport(handler))
    payload = client._payload({}, repair=False, invalid_output=None, service_tier="flex")
    with pytest.raises(ShariaResearchError) as exc_info:
        await client._post(payload)
    assert exc_info.value.code == "openai_retry_exhausted"
    assert len(bodies) == 1
    assert "service_tier" not in bodies[0]


async def test_openai_flex_retry_still_falls_back_to_default() -> None:
    from ai_market_monitor.services.sharia_research import (
        ShariaAIResearchClient,
        ShariaResearchError,
    )

    bodies: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(429, json={"error": {"type": "rate_limit", "code": "429"}})

    settings = _settings(
        openai_api_key=SecretStr("sk-test-openai-key"),
        opencode_go_api_key=None,
        sharia_ai_model="gpt-5.4-nano",
        provider_retry_max_attempts=1,
        sharia_ai_allow_standard_fallback=True,
        sharia_ai_service_tier="flex",
    )
    client = ShariaAIResearchClient(settings, transport=httpx.MockTransport(handler))
    payload = client._payload({}, repair=False, invalid_output=None, service_tier="flex")
    with suppress(ShariaResearchError):
        await client._post(payload)
    assert [body.get("service_tier") for body in bodies] == ["flex", "default"]


# --------------------------------------------------------------------------------
# status:"incomplete" is its own plain-words error, never "invalid JSON".
# --------------------------------------------------------------------------------


async def test_incomplete_max_output_tokens_is_not_invalid_json() -> None:
    from pydantic import BaseModel

    from ai_market_monitor.services.openai_structured_call import (
        StructuredCallError,
        structured_call,
    )

    class _Answer(BaseModel):
        answer: str

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [],
                "usage": {"input_tokens": 10, "output_tokens": 64},
            },
        )

    with pytest.raises(StructuredCallError) as exc_info:
        await structured_call(
            _settings(),
            schema_model=_Answer,
            schema_name="test_answer",
            instructions="Answer.",
            payload={"request": "answer"},
            model=MUSE,
            reasoning_effort="high",
            max_output_tokens=64,
            transport=httpx.MockTransport(handler),
        )
    assert exc_info.value.code == "TARGET_MAX_OUTPUT_TOKENS"
    assert "JSON" not in str(exc_info.value)


# --------------------------------------------------------------------------------
# System Brain tool loop: message + function_call in one turn, reasoning ignored.
# --------------------------------------------------------------------------------


async def test_system_brain_tool_loop_runs_tools_past_message_and_reasoning_items(
    test_context,
) -> None:
    import json as std_json

    from sqlalchemy import func, select

    from ai_market_monitor.db.models import (
        AgentToolCall,
        SystemBrainMessage,
        User,
    )
    from ai_market_monitor.db.models.enums import UserRole
    from ai_market_monitor.schemas.system_brain import SystemBrainAgentTurnRequest
    from ai_market_monitor.services.system_brain_agent import (
        SystemBrainAgentService,
        SystemBrainConversationService,
    )

    settings = test_context["settings"]
    settings.system_brain_ai_enabled = True
    settings.opencode_go_api_key = "zen-test-key"
    settings.system_brain_ai_model = MUSE
    settings.system_brain_ai_reasoning_effort = "high"
    final = {
        "answer": "The repository index has no matching retained evidence.",
        "findings": [],
        "opportunities": [],
        "suggested_actions": [],
        "evidence_refs": ["query:repository_search:no-result"],
        "limitations": ["No indexed file matched; missing evidence is not a zero result."],
    }
    client = SessionCapturingClient(
        {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Let me look that up."}],
                },
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "plan"}]},
                {
                    "type": "function_call",
                    "name": "repository_search",
                    "call_id": "call-1",
                    "arguments": std_json.dumps({"query": "impossible-index-term", "limit": 5}),
                },
            ],
            "usage": {"input_tokens": 30, "output_tokens": 40},
        }
    )
    second = _message_response(std_json.dumps(final))
    responses = [client.response, second]

    async def sequenced(payload, *, timeout_seconds, session_key=None):
        client.payloads.append(payload)
        client.session_keys.append(session_key)
        return responses.pop(0)

    client.create = sequenced  # type: ignore[method-assign]
    async with test_context["session_factory"]() as session:
        admin = User(display_name="Admin", role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()
        conversation = await SystemBrainConversationService().create(
            session, admin_user_id=admin.id, title="Tool loop evidence"
        )
        await session.commit()
        service = SystemBrainAgentService(settings, client=client)
        response = await service.run_turn(
            session,
            conversation.id,
            admin_user_id=admin.id,
            request=SystemBrainAgentTurnRequest(
                message="Search the code for impossible-index-term",
                client_message_id="brain-tool-loop-0001",
            ),
        )
        tool_calls = int(await session.scalar(select(func.count(AgentToolCall.id))) or 0)
        assistant_rows = list(
            (
                await session.scalars(
                    select(SystemBrainMessage).where(SystemBrainMessage.role == "assistant")
                )
            ).all()
        )

    assert response.status == "completed"
    assert tool_calls == 1
    assert assistant_rows and assistant_rows[0].content == final["answer"]
    # The tool ran and the loop asked for the final answer afterwards.
    assert len(client.payloads) == 2
    continuation = client.payloads[1]["input"]
    continuation_types = [item.get("type") for item in continuation if isinstance(item, dict)]
    assert "function_call" in continuation_types
    assert "function_call_output" in continuation_types
    assert "reasoning" not in continuation_types
    # Both turns of one conversation carry the conversation id, never a user id.
    assert client.session_keys == [str(conversation.id), str(conversation.id)]


def test_public_chat_groups_turns_by_conversation_id_not_identity() -> None:
    source = (SERVICES / "public_chat.py").read_text(encoding="utf-8")
    assert "session_key=str(conversation.id)" in source
    assert "session_key=str(user_id)" not in source
    assert "session_key=user_id" not in source


def test_system_brain_agent_groups_turns_by_conversation_id_not_identity() -> None:
    source = (SERVICES / "system_brain_agent.py").read_text(encoding="utf-8")
    assert "session_key=str(conversation.id)" in source
    assert "session_key=str(admin_user_id)" not in source


# --------------------------------------------------------------------------------
# Stale family: byte-identical OpenAI requests through the shared clients.
# --------------------------------------------------------------------------------


async def test_stale_structured_call_body_is_byte_identical() -> None:
    from pydantic import BaseModel

    from ai_market_monitor.services.agent_tools import strict_json_schema
    from ai_market_monitor.services.openai_structured_call import structured_call

    class _Answer(BaseModel):
        answer: str

    bodies: list[bytes] = []
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        bodies.append(request.content)
        return httpx.Response(
            200,
            json={
                "service_tier": "flex",
                "output_text": json.dumps({"answer": "ok"}),
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        )

    settings = _settings(
        openai_api_key=SecretStr("sk-test-openai-key"),
        opencode_go_api_key=None,
        sharia_ai_model="gpt-5.4-nano",
    )
    result, _ = await structured_call(
        settings,
        schema_model=_Answer,
        schema_name="test_answer",
        instructions="Answer.",
        payload={"request": "answer"},
        model="gpt-5.4-nano",
        reasoning_effort="low",
        max_output_tokens=64,
        service_tier="flex",
        transport=httpx.MockTransport(handler),
    )
    assert result.answer == "ok"
    expected = {
        "model": "gpt-5.4-nano",
        "store": False,
        "stream": False,
        "max_output_tokens": 64,
        "reasoning": {"effort": "low"},
        "text": {
            "format": {
                "type": "json_schema",
                "name": "test_answer",
                "strict": True,
                "schema": strict_json_schema(_Answer),
            }
        },
        "instructions": "Answer.",
        "input": json.dumps({"request": "answer"}, ensure_ascii=False, sort_keys=True),
        "service_tier": "flex",
    }
    assert list(json.loads(bodies[0]).keys()) == list(expected.keys())
    assert json.loads(bodies[0]) == expected
    assert str(seen[0].url) == "https://api.openai.com/v1/responses"
    assert seen[0].headers["authorization"] == "Bearer sk-test-openai-key"
    assert "x-opencode-session" not in seen[0].headers


async def test_stale_agent_client_passes_the_payload_through_untouched() -> None:
    from ai_market_monitor.services.agent_control import OpenAIAgentResponsesClient

    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_message_response('{"ok":true}'))

    settings = _settings(
        openai_api_key=SecretStr("sk-test-openai-key"), opencode_go_api_key=None
    )
    payload = {
        "model": "gpt-5.4-nano",
        "input": [{"role": "user", "content": "hi"}],
        "store": False,
    }
    await OpenAIAgentResponsesClient(
        settings, transport=httpx.MockTransport(handler)
    ).create(payload, timeout_seconds=20.0)
    assert json.loads(seen[0].content) == payload
    assert str(seen[0].url) == "https://api.openai.com/v1/responses"
    assert "x-opencode-session" not in seen[0].headers


# --------------------------------------------------------------------------------
# R7: the coin scraper, the evidence crawler and the screen call no model.
# --------------------------------------------------------------------------------


R7_MODEL_FREE_MODULES = [
    "services/unscreened_coin_research.py",
    "services/coin_evidence_crawler.py",
    "services/automated_screen_pipeline.py",
]

R7_MODEL_LITERALS = (
    "ai_provider",
    "openai",
    "opencode",
    "/responses",
    "service_tier",
    "Authorization",
    "Bearer",
    "reasoning_effort",
    "max_output_tokens",
)


@pytest.mark.parametrize("module", R7_MODEL_FREE_MODULES)
@pytest.mark.parametrize("literal", R7_MODEL_LITERALS)
def test_r7_coin_bot_modules_call_no_model(module: str, literal: str) -> None:
    source = (SRC / module).read_text(encoding="utf-8")
    assert literal.lower() not in source.lower(), f"{module} references a model ({literal})"


def test_r7_coin_bot_modules_hold_no_api_key_name() -> None:
    for module in R7_MODEL_FREE_MODULES:
        source = (SRC / module).read_text(encoding="utf-8")
        assert "api_key" not in source.lower(), f"{module} reads an API key"


def test_active_provider_modules_never_send_service_tier_to_opencode() -> None:
    for module in [
        "hilal_chat_agent.py",
        "public_support_ai.py",
        "public_chat.py",
        "system_brain_agent.py",
        "system_brain_assistant.py",
        "sharia_source_ai_discovery.py",
    ]:
        source = (SERVICES / module).read_text(encoding="utf-8")
        assert "service_tier" not in source, f"{module} still names service_tier"


def test_build_request_sends_no_service_tier_header_for_muse() -> None:
    request = ai_provider.build_responses_request(_settings(), MUSE, session_key="conv")
    assert request.provider == "opencode_go"
    assert set(request.headers) == {"Authorization", "Content-Type", "x-opencode-session"}
