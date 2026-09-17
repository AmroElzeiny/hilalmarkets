"""One owner for AI provider routing, and one reader for provider answers.

Every active assistant used to build its own provider request: its own URL from
``openai_base_url``, its own ``Authorization`` header, its own idea of where the
answer text lives. Four copies of the same connection code means four places a
new provider has to be added, and four chances to get one of them wrong.

``services/ai_provider.py`` is the one owner now: model id to provider, request
URL and headers, the ``is_configured`` gate, and the single Responses-API text
reader. The active modules below may call it. They may not build a request
themselves.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services import ai_provider
from ai_market_monitor.services.ai_provider import (
    AIProviderConfigError,
    extract_response_text,
    is_configured,
    resolve_provider,
)
from ai_market_monitor.services.system_brain import estimate_usage_cost

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "ai_market_monitor"

MUSE = "muse-spark-1.3-contributor"

#: Every module with a live AI call site (mission WP1 section 3). None of them
#: may build its own provider URL or its own auth header. ``ai_provider`` is
#: the only module allowed to hold those literals.
ACTIVE_MODULES = [
    "services/public_chat.py",
    "services/public_support_ai.py",
    "services/hilal_chat_agent.py",
    "services/agent_control.py",
    "services/system_brain_agent.py",
    "services/system_brain_assistant.py",
    "services/openai_structured_call.py",
    "services/sharia_research.py",
    "services/sharia_source_monitoring.py",
    "services/sharia_source_resolution.py",
    "services/sharia_source_ai_discovery.py",
    "services/agent_policy.py",
]

#: Literals that build a provider request. No active module may hold them.
OWN_REQUEST_LITERALS = (
    "openai_base_url",
    "opencode_go_base_url",
    '"Authorization"',
    "api.openai.com",
    "/responses",
)

#: What the owner itself must hold. The owner builds URLs from settings, so it
#: never hardcodes the host; everything else it holds exactly once.
OWNER_LITERALS = (
    "openai_base_url",
    "opencode_go_base_url",
    '"Authorization"',
    "/responses",
    "x-opencode-session",
)


def _settings(**changes) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": "test",
        "app_secret_key": SecretStr("ai-provider-test-secret-at-least-thirty-two-chars"),
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------
# R9: no active module builds its own provider request.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("module", ACTIVE_MODULES)
@pytest.mark.parametrize("literal", OWN_REQUEST_LITERALS)
def test_r9_no_active_module_builds_its_own_provider_request(
    module: str, literal: str
) -> None:
    source = (SRC / module).read_text(encoding="utf-8")
    assert literal not in source, f"{module} builds its own request ({literal})"


def test_r9_the_provider_module_is_the_single_owner() -> None:
    source = (SRC / "services" / "ai_provider.py").read_text(encoding="utf-8")
    for literal in OWNER_LITERALS:
        assert literal in source, f"ai_provider lost ownership of {literal}"


# --------------------------------------------------------------------------------
# Model id to provider: a closed table, fail closed.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5.4-nano",
        "gpt-5.4-mini",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-5.6-luna",
        "text-embedding-3-small",
    ],
)
def test_openai_family_models_route_to_openai(model: str) -> None:
    assert resolve_provider(model) == "openai"


def test_muse_routes_to_opencode_go() -> None:
    assert resolve_provider(MUSE) == "opencode_go"


@pytest.mark.parametrize("model", ["gpt-99", "claude-4", "", "  "])
def test_an_unknown_model_id_is_refused_not_guessed(model: str) -> None:
    with pytest.raises(AIProviderConfigError):
        resolve_provider(model)


def test_opencode_go_does_not_honour_service_tier() -> None:
    assert ai_provider.provider_honours_service_tier("openai") is True
    assert ai_provider.provider_honours_service_tier("opencode_go") is False


# --------------------------------------------------------------------------------
# R11: is_configured — each active feature, key present / missing / placeholder.
# --------------------------------------------------------------------------------

#: Setting name holding each active feature's model.
FEATURE_MODELS = {
    "PUBLIC_CHAT_AI_MODEL": "public_chat_ai_model",
    "HILAL_CHAT_AI_MODEL": "hilal_chat_ai_model",
    "SYSTEM_BRAIN_AI_MODEL": "system_brain_ai_model",
    "SHARIA_AI_MODEL": "sharia_ai_model",
    "SHARIA_SOURCE_AI_MODEL": "sharia_source_ai_model",
}

PLACEHOLDERS = ["REPLACE_ME_WITH_REAL_KEY", "YOUR_OPENCODE_KEY", "CHANGE_ME"]


@pytest.mark.parametrize("setting", sorted(FEATURE_MODELS))
def test_r11_active_feature_with_no_key_is_not_configured(setting: str) -> None:
    field = FEATURE_MODELS[setting]
    settings = _settings(**{field: MUSE})
    assert is_configured(settings, getattr(settings, field)) is False


@pytest.mark.parametrize("setting", sorted(FEATURE_MODELS))
def test_r11_active_feature_needs_the_opencode_key_not_the_openai_key(
    setting: str,
) -> None:
    """An OpenAI key must never satisfy an active Muse feature."""
    field = FEATURE_MODELS[setting]
    settings = _settings(**{field: MUSE, "openai_api_key": SecretStr("sk-test-openai-key")})
    assert is_configured(settings, getattr(settings, field)) is False


@pytest.mark.parametrize("setting", sorted(FEATURE_MODELS))
def test_r11_active_feature_with_its_key_is_configured(setting: str) -> None:
    field = FEATURE_MODELS[setting]
    settings = _settings(
        **{field: MUSE, "opencode_go_api_key": SecretStr("zen-test-key")}
    )
    assert is_configured(settings, getattr(settings, field)) is True


@pytest.mark.parametrize("setting", sorted(FEATURE_MODELS))
@pytest.mark.parametrize("placeholder", PLACEHOLDERS)
def test_r11_a_placeholder_key_is_not_a_configured_key(
    setting: str, placeholder: str
) -> None:
    field = FEATURE_MODELS[setting]
    settings = _settings(
        **{field: MUSE, "opencode_go_api_key": SecretStr(placeholder)}
    )
    assert is_configured(settings, getattr(settings, field)) is False


@pytest.mark.parametrize("setting", sorted(FEATURE_MODELS))
def test_r11_blank_key_means_not_configured(setting: str) -> None:
    field = FEATURE_MODELS[setting]
    settings = _settings(**{field: MUSE, "opencode_go_api_key": ""})
    assert is_configured(settings, getattr(settings, field)) is False


def test_r11_openai_family_model_checks_the_openai_key() -> None:
    assert (
        is_configured(
            _settings(
                sharia_ai_model="gpt-5.4-nano",
                openai_api_key=SecretStr("sk-test-openai-key"),
            ),
            "gpt-5.4-nano",
        )
        is True
    )
    assert (
        is_configured(
            _settings(
                sharia_ai_model="gpt-5.4-nano",
                opencode_go_api_key=SecretStr("zen-test-key"),
            ),
            "gpt-5.4-nano",
        )
        is False
    )


def test_r11_unknown_model_is_a_configuration_error() -> None:
    with pytest.raises(AIProviderConfigError):
        is_configured(_settings(sharia_ai_model="gpt-99"), "gpt-99")


# --------------------------------------------------------------------------------
# Request building: auth, session header, no service tier for OpenCode Go.
# --------------------------------------------------------------------------------


def test_muse_request_carries_bearer_auth_and_a_random_session_id() -> None:
    settings = _settings(opencode_go_api_key=SecretStr("zen-test-key"))
    first = ai_provider.build_responses_request(settings, MUSE)
    second = ai_provider.build_responses_request(settings, MUSE)
    assert first.url == "https://opencode.ai/zen/go/v1/responses"
    assert first.headers["Authorization"] == "Bearer zen-test-key"
    assert first.headers["Content-Type"] == "application/json"
    assert first.provider == "opencode_go"
    assert first.headers["x-opencode-session"]
    assert first.headers["x-opencode-session"] != second.headers["x-opencode-session"]


def test_session_header_is_stable_per_conversation_and_never_an_identity() -> None:
    settings = _settings(opencode_go_api_key=SecretStr("zen-test-key"))
    conversation = "12345678-1234-1234-1234-1234567890ab"
    first = ai_provider.build_responses_request(settings, MUSE, session_key=conversation)
    second = ai_provider.build_responses_request(settings, MUSE, session_key=conversation)
    other = ai_provider.build_responses_request(
        settings, MUSE, session_key="87654321-4321-4321-4321-ba0987654321"
    )
    assert first.headers["x-opencode-session"] == second.headers["x-opencode-session"]
    assert first.headers["x-opencode-session"] != other.headers["x-opencode-session"]
    assert "amroe" not in first.headers["x-opencode-session"]
    assert "@" not in first.headers["x-opencode-session"]


def test_openai_request_is_unchanged() -> None:
    settings = _settings(openai_api_key=SecretStr("sk-test-openai-key"))
    request = ai_provider.build_responses_request(settings, "gpt-5.4-nano")
    assert request.url == "https://api.openai.com/v1/responses"
    assert request.headers == {
        "Authorization": "Bearer sk-test-openai-key",
        "Content-Type": "application/json",
    }
    assert request.provider == "openai"


def test_building_without_a_key_is_a_configuration_error() -> None:
    with pytest.raises(AIProviderConfigError):
        ai_provider.build_responses_request(_settings(), MUSE)


# --------------------------------------------------------------------------------
# Shared response-text reader.
# --------------------------------------------------------------------------------


def test_reader_reads_message_content_past_reasoning_items_without_output_text() -> None:
    payload = {
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": '{"answer":"split'},
                    {"type": "text", "text": ' response"}'},
                ],
            },
        ]
    }
    assert extract_response_text(payload) == '{"answer":"split response"}'


def test_reader_keeps_the_recorded_output_text_shape_working() -> None:
    assert extract_response_text({"output_text": '{"answer":"ok"}'}) == '{"answer":"ok"}'


def test_reader_prefers_output_messages_over_a_stale_shortcut() -> None:
    payload = {
        "output_text": "stale",
        "output": [
            {"type": "message", "content": [{"type": "text", "text": "live"}]}
        ],
    }
    assert extract_response_text(payload) == "live"


def test_reader_with_no_text_is_a_failure_not_an_empty_answer() -> None:
    with pytest.raises(ValueError):
        extract_response_text({"output": [{"type": "reasoning"}]})
    with pytest.raises(ValueError):
        extract_response_text({})


# --------------------------------------------------------------------------------
# R13: Muse pricing entry and cost estimate.
# --------------------------------------------------------------------------------


def test_r13_muse_pricing_matches_the_documented_source() -> None:
    settings = _settings()
    pricing = settings.openai_model_pricing_usd_per_million[MUSE]
    assert float(pricing["input"]) == 0.1
    assert float(pricing["output"]) == 0.2
    assert float(pricing["cached_input"]) == 0.002


def test_r13_muse_turn_cost_is_estimated_not_refused() -> None:
    from ai_market_monitor.services.ai_spend import estimate_turn_cost_usd

    cost = estimate_turn_cost_usd(
        _settings(),
        model=MUSE,
        service_tier=None,
        max_input_tokens=1_000_000,
        max_output_tokens=131072,
    )
    assert cost is not None
    assert float(cost) == pytest.approx(0.1 + 131072 * 0.2 / 1_000_000)


def test_r13_muse_usage_cost_counts_cached_input_at_cache_read() -> None:
    cost = estimate_usage_cost(
        _settings(),
        model=MUSE,
        usage={
            "input_tokens": 1000,
            "input_tokens_details": {"cached_tokens": 400},
            "output_tokens": 100,
        },
    )
    assert float(cost) == pytest.approx((600 * 0.1 + 400 * 0.002 + 100 * 0.2) / 1_000_000)


# --------------------------------------------------------------------------------
# R14: the new key is redacted everywhere the OpenAI key is.
# --------------------------------------------------------------------------------


def test_r14_security_review_redacts_the_new_key() -> None:
    from ai_market_monitor.services.security_review import SecurityReviewService

    service = SecurityReviewService()
    assert service.redact({"opencode_go_api_key": "zen-secret-value"}) == {
        "opencode_go_api_key": "[redacted]"
    }
    assert service.redact({"OPENCODE_GO_API_KEY": "zen-secret-value"}) == {
        "OPENCODE_GO_API_KEY": "[redacted]"
    }


def test_r14_reliability_metrics_drop_the_new_key_name() -> None:
    from ai_market_monitor.services.reliability_metrics import is_safe_field

    assert is_safe_field("opencode_go_api_key") is False
    assert is_safe_field("OPENCODE_GO_API_KEY") is False


def test_r14_metric_labels_refuse_the_new_key_name_and_value_shapes() -> None:
    from ai_market_monitor.observability.labels import (
        MetricLabelError,
        SensitiveValueError,
        assert_no_sensitive_content,
        validate_labels,
    )

    with pytest.raises(MetricLabelError):
        validate_labels({"opencode_go_api_key": "anything"})
    with pytest.raises(SensitiveValueError):
        assert_no_sensitive_content("Bearer zen-synthetic-session-key", field="test")
    assert validate_labels({"provider": "opencode_go"}) == {"provider": "opencode_go"}


# --------------------------------------------------------------------------------
# Stale paths keep byte-identical OpenAI requests (no stale file is edited).
# --------------------------------------------------------------------------------


async def test_stale_shared_client_request_is_byte_identical() -> None:
    from ai_market_monitor.services.agent_control import OpenAIAgentResponsesClient

    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"ok":true}'}],
                    }
                ],
                "usage": {},
            },
        )

    settings = _settings(openai_api_key=SecretStr("sk-test-openai-key"))
    client = OpenAIAgentResponsesClient(
        settings, transport=httpx.MockTransport(handler)
    )
    await client.create({"model": "gpt-5.4-nano", "input": []}, timeout_seconds=20.0)

    assert len(seen) == 1
    assert str(seen[0].url) == "https://api.openai.com/v1/responses"
    assert seen[0].headers["authorization"] == "Bearer sk-test-openai-key"
    assert seen[0].headers["content-type"] == "application/json"
    assert "x-opencode-session" not in seen[0].headers


async def test_stale_structured_call_keeps_its_service_tier() -> None:
    from pydantic import BaseModel

    from ai_market_monitor.services.openai_structured_call import structured_call

    class _Answer(BaseModel):
        answer: str

    bodies: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "service_tier": "flex",
                "output_text": json.dumps({"answer": "ok"}),
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        )

    result, _ = await structured_call(
        _settings(openai_api_key=SecretStr("sk-test-openai-key")),
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
    assert bodies[0]["service_tier"] == "flex"


async def test_muse_structured_call_never_sends_service_tier() -> None:
    from pydantic import BaseModel

    from ai_market_monitor.services.openai_structured_call import structured_call

    class _Answer(BaseModel):
        answer: str

    bodies: list[dict] = []
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps({"answer": "ok"})}
                        ],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        )

    result, _ = await structured_call(
        _settings(opencode_go_api_key=SecretStr("zen-test-key")),
        schema_model=_Answer,
        schema_name="test_answer",
        instructions="Answer.",
        payload={"request": "answer"},
        model=MUSE,
        reasoning_effort="high",
        max_output_tokens=64,
        service_tier="flex",
        transport=httpx.MockTransport(handler),
    )
    assert result.answer == "ok"
    assert "service_tier" not in bodies[0]
    assert str(seen[0].url) == "https://opencode.ai/zen/go/v1/responses"
    assert "x-opencode-session" in seen[0].headers


async def test_agent_control_muse_path_carries_stable_conversation_session_header() -> None:
    """The active Muse path through agent_control groups one conversation.

    Two turns of the same conversation must carry the same
    ``x-opencode-session`` header. Two different conversations must carry
    different headers. The id is a conversation id, never a user id or email.
    """
    from ai_market_monitor.services.agent_control import OpenAIAgentResponsesClient

    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"ok":true}'}],
                    }
                ],
                "usage": {},
            },
        )

    settings = _settings(opencode_go_api_key=SecretStr("zen-test-key"))
    client = OpenAIAgentResponsesClient(
        settings, transport=httpx.MockTransport(handler)
    )
    conversation_a = "12345678-1234-1234-1234-1234567890ab"
    conversation_b = "87654321-4321-4321-4321-ba0987654321"
    await client.create(
        {"model": MUSE, "input": []}, timeout_seconds=20.0, session_key=conversation_a
    )
    await client.create(
        {"model": MUSE, "input": []}, timeout_seconds=20.0, session_key=conversation_a
    )
    await client.create(
        {"model": MUSE, "input": []}, timeout_seconds=20.0, session_key=conversation_b
    )

    assert len(seen) == 3
    first = seen[0].headers["x-opencode-session"]
    second = seen[1].headers["x-opencode-session"]
    third = seen[2].headers["x-opencode-session"]
    assert first == second
    assert first != third
    assert "@" not in first
    assert "amroe" not in first

    source = (SRC / "services" / "agent_control.py").read_text(encoding="utf-8")
    assert "session_key=str(chat.id)" in source
    assert "session_key=str(chat.user_id)" not in source
