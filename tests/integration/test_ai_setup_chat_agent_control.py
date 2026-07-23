import json
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import func, select

from ai_market_monitor.api.routers.dashboard_api import get_ai_setup_chat_service
from ai_market_monitor.db.models import (
    AgentRun,
    AISetupChatMessage,
    AISetupChatSession,
    AIUsageEvent,
)
from ai_market_monitor.schemas.ai_setup_chat import SetupChatInterviewResult
from ai_market_monitor.schemas.strategy import InterpretationPreview
from ai_market_monitor.services.ai_setup_chat import AISetupChatService
from tests.factories import load_strategy


class FakeResponsesClient:
    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        self.responses = list(responses)
        self.payloads: list[dict[str, Any]] = []

    async def create(self, payload, *, timeout_seconds):
        self.payloads.append(payload)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class NeverAgentClient:
    async def create(self, payload, *, timeout_seconds):
        raise AssertionError("The bounded agent must remain disabled")


class ReadyInterviewer:
    async def respond(self, **_) -> SetupChatInterviewResult:
        return SetupChatInterviewResult(
            intent="setup",
            assistant_message="The deterministic draft is ready for review.",
            ready_to_compile=True,
            setup_summary="RSI is below 30 on 15m Binance USDT spot pairs.",
        )


class FixedInterpreter:
    async def interpret(self, guided) -> InterpretationPreview:
        strategy = load_strategy().model_copy(deep=True)
        rule = _first_rule(strategy.conditions)
        if "35" in guided.setup_text:
            rule.right.value = 35
            rule.source_fragment = "Use RSI below 35 on 15m instead"
        return InterpretationPreview(
            strategy=strategy,
            assumptions=[],
            interpreter="bounded-agent-integration-test",
        )


class SnapshotProvider:
    async def list_symbols(self, exchange, quote_currencies):
        return ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

    async def fetch_universe_metadata(self, exchange, symbols, **_):
        return {
            "BTC/USDT": {"percentage_24h": 1.2},
            "ETH/USDT": {"percentage_24h": -0.4},
            "SOL/USDT": {"percentage_24h": 3.1},
        }


class UnavailableProvider:
    async def list_symbols(self, exchange, quote_currencies):
        raise ConnectionError("provider unavailable")


def _tool_call(name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "output": [
            {
                "type": "function_call",
                "name": name,
                "arguments": json.dumps(arguments),
                "call_id": call_id,
            }
        ],
        "usage": {"input_tokens": 50, "output_tokens": 15},
    }


def _final(message: str, intent: str, status: str = "completed") -> dict[str, Any]:
    return {
        "output": [],
        "output_text": json.dumps(
            {
                "message": message,
                "intent": intent,
                "status": status,
                "evidence_refs": [],
                "suggested_actions": [],
                "requires_user_confirmation": False,
            }
        ),
        "usage": {"input_tokens": 50, "output_tokens": 15},
    }


def _first_rule(node):
    if hasattr(node, "children"):
        return _first_rule(node.children[0])
    return node


async def _signup(test_context, email: str) -> None:
    client = test_context["client"]
    response = await client.post(
        "/signup",
        data={
            "email": email,
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    code = test_context["settings"].email_test_outbox[-1]["code"]
    verified = await client.post(
        "/signup/verify",
        data={"email": email, "code": code},
        follow_redirects=False,
    )
    assert verified.status_code == 303


def _configure(test_context, *, enabled: bool, shadow: bool = False) -> None:
    settings = test_context["settings"]
    settings.openai_api_key = SecretStr("test-key")
    settings.ai_agent_control_enabled = enabled
    settings.ai_agent_shadow_mode = shadow
    settings.ai_agent_rollout_percent = 100
    settings.ai_agent_max_estimated_cost_usd_per_turn = 0.5


async def _new_chat(test_context) -> str:
    response = await test_context["client"].post("/api/v1/dashboard/setup-chat/sessions")
    assert response.status_code == 201
    return response.json()["id"]


async def test_feature_flag_disabled_preserves_legacy_chat_path(test_context):
    await _signup(test_context, "agent-disabled@example.com")
    _configure(test_context, enabled=False)
    service = AISetupChatService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
        agent_client=NeverAgentClient(),
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service
    chat_id = await _new_chat(test_context)

    response = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": "RSI below 30 on 15m Binance spot pairs.",
            "client_message_id": "legacy-agent-disabled-1",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready_for_approval"
    async with test_context["session_factory"]() as session:
        assert int(await session.scalar(select(func.count(AgentRun.id))) or 0) == 0


async def test_zero_percent_rollout_preserves_legacy_chat_path(test_context):
    await _signup(test_context, "agent-zero-rollout@example.com")
    _configure(test_context, enabled=True)
    test_context["settings"].ai_agent_rollout_percent = 0
    service = AISetupChatService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
        agent_client=NeverAgentClient(),
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service
    chat_id = await _new_chat(test_context)

    response = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": "RSI below 30 on 15m Binance spot pairs.",
            "client_message_id": "agent-zero-rollout-1",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready_for_approval"
    async with test_context["session_factory"]() as session:
        assert int(await session.scalar(select(func.count(AgentRun.id))) or 0) == 0


async def test_agent_unavailable_falls_back_without_duplicate_user_message(test_context):
    await _signup(test_context, "agent-fallback@example.com")
    _configure(test_context, enabled=True)
    agent = FakeResponsesClient([ValueError("OpenAI unavailable")])
    service = AISetupChatService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
        agent_client=agent,
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service
    chat_id = await _new_chat(test_context)

    response = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": "RSI below 30 on 15m Binance spot pairs.",
            "client_message_id": "agent-fallback-1",
        },
    )
    assert response.status_code == 200, response.text
    user_messages = [item for item in response.json()["messages"] if item["role"] == "user"]
    assert len(user_messages) == 1
    assert user_messages[0]["client_message_id"] == "agent-fallback-1"
    async with test_context["session_factory"]() as session:
        run = await session.scalar(select(AgentRun))
        assert run.status == "fallback"
        assert run.fallback_used is True


async def test_agent_market_snapshot_renders_authoritative_tool_payload(test_context):
    await _signup(test_context, "agent-snapshot@example.com")
    _configure(test_context, enabled=True)
    agent = FakeResponsesClient(
        [
            _tool_call(
                "get_market_snapshot",
                {"exchange": None, "quote_currency": None},
                "api-snapshot-1",
            ),
            _final("Here is the provider-backed market snapshot.", "market_snapshot"),
        ]
    )
    service = AISetupChatService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
        agent_client=agent,
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service
    chat_id = await _new_chat(test_context)
    response = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={"message": "How is the market today?", "client_message_id": "snapshot-1"},
    )
    assert response.status_code == 200, response.text
    latest = response.json()["messages"][-1]
    assert latest["message_type"] == "market_snapshot"
    assert latest["payload"]["provider_name"] == "SnapshotProvider"
    assert latest["payload"]["symbols_checked"] == 3
    assert latest["payload"]["evidence_refs"]
    assert latest["payload"]["_traceedge_model"] == "gpt-5.4-nano"
    assert latest["payload"]["usage"]["input_tokens"] == 100
    assert latest["payload"]["usage"]["output_tokens"] == 30
    assert latest["payload"]["usage"]["estimated_cost_usd"] > 0
    assert latest["payload"]["usage"]["models"] == ["gpt-5.4-nano"]
    assert len([item for item in response.json()["messages"] if item["role"] == "user"]) == 1
    async with test_context["session_factory"]() as session:
        run = await session.scalar(select(AgentRun))
        usage_events = list((await session.scalars(select(AIUsageEvent))).all())
        assert usage_events
        assert all(
            event.raw_usage["_traceedge_correlation_id"] == run.correlation_id
            for event in usage_events
        )


async def test_provider_unavailability_is_explicit_and_contains_no_invented_values(test_context):
    await _signup(test_context, "agent-provider-down@example.com")
    _configure(test_context, enabled=True)
    agent = FakeResponsesClient(
        [
            _tool_call(
                "get_market_snapshot",
                {"exchange": None, "quote_currency": None},
                "provider-down-1",
            ),
            _final(
                "The configured provider could not supply a snapshot; no values were invented.",
                "unavailable",
                "failed",
            ),
        ]
    )
    service = AISetupChatService(
        test_context["settings"],
        UnavailableProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
        agent_client=agent,
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service
    chat_id = await _new_chat(test_context)
    response = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={"message": "How is the market today?"},
    )
    assert response.status_code == 200, response.text
    latest = response.json()["messages"][-1]
    assert latest["message_type"] == "agent_unavailable"
    assert "no values were invented" in latest["content"]
    assert latest["payload"]["evidence_refs"] == []


async def test_shadow_mode_records_comparison_then_uses_legacy_flow(test_context):
    await _signup(test_context, "agent-shadow@example.com")
    _configure(test_context, enabled=True, shadow=True)
    agent = FakeResponsesClient(
        [
            _tool_call(
                "resolve_trading_capabilities",
                {
                    "fragments": ["RSI below 30 on 15m"],
                    "default_timeframe": "15m",
                },
                "shadow-resolve-1",
            )
        ]
    )
    service = AISetupChatService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
        agent_client=agent,
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service
    chat_id = await _new_chat(test_context)
    response = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={"message": "RSI below 30 on 15m", "client_message_id": "shadow-msg-1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready_for_approval"
    async with test_context["session_factory"]() as session:
        run = await session.scalar(select(AgentRun))
        assert run.status == "shadow_compared"
        assert run.comparison["comparison_pending"] is False
        assert run.comparison["legacy_status_after"] == "ready_for_approval"
        assert run.comparison["legacy_expected_first_tool"] == (
            "resolve_trading_capabilities"
        )
        assert run.comparison["agent_first_tool_correct"] is True
        messages = list(
            (
                await session.scalars(
                    select(AISetupChatMessage).where(
                        AISetupChatMessage.session_id == run.chat_session_id,
                        AISetupChatMessage.role == "user",
                    )
                )
            ).all()
        )
        assert len(messages) == 1


async def test_correction_recompiles_hash_and_next_call_receives_chat_history(test_context):
    await _signup(test_context, "agent-correction@example.com")
    _configure(test_context, enabled=True)
    agent = FakeResponsesClient(
        [
            _tool_call(
                "resolve_trading_capabilities",
                {
                    "fragments": ["RSI below 30 on 15m"],
                    "default_timeframe": "15m",
                },
                "correction-1",
            ),
            _tool_call("compile_strategy_draft", {}, "correction-2"),
            _final("Review this deterministic draft before approval.", "draft_ready"),
            _tool_call(
                "resolve_trading_capabilities",
                {
                    "fragments": ["Use RSI below 35 on 15m instead"],
                    "default_timeframe": "15m",
                },
                "correction-3",
            ),
            _tool_call("compile_strategy_draft", {}, "correction-4"),
            _final("The corrected deterministic draft is ready to review.", "draft_ready"),
        ]
    )
    service = AISetupChatService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
        agent_client=agent,
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service
    chat_id = await _new_chat(test_context)
    first = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={"message": "RSI below 30 on 15m", "client_message_id": "correction-msg-1"},
    )
    assert first.status_code == 200, first.text
    first_hash = first.json()["schema_hash"]
    second = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": "Use RSI below 35 on 15m instead",
            "client_message_id": "correction-msg-2",
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["schema_hash"] != first_hash
    assert len([item for item in second.json()["messages"] if item["role"] == "user"]) == 2

    second_turn_first_payload = agent.payloads[3]
    state = json.loads(second_turn_first_payload["input"][0]["content"])
    history_text = " ".join(item["content"] for item in state["conversation"])
    assert "RSI below 30 on 15m" in history_text
    async with test_context["session_factory"]() as session:
        chat = await session.get(AISetupChatSession, UUID(chat_id))
        assert chat.approved_at is None
        assert chat.approved_strategy_id is None
