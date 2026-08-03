import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    PublicChatAnswerEvent,
    PublicChatAnswerFeedback,
    PublicChatConversation,
    PublicChatTurn,
    PublicInquiry,
    PublicInquiryEmailDelivery,
    PublicInquiryRating,
    User,
)
from ai_market_monitor.schemas.public_chat import (
    PublicChatAnswerFeedbackRequest,
    PublicChatAnswerRequest,
    PublicInquiryRequest,
)
from ai_market_monitor.services.email_delivery import AuthEmailService, EmailDeliveryError
from ai_market_monitor.services.public_chat import PublicChatService


class FakePublicSupportResponses:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.payloads: list[dict] = []

    async def create(self, payload, *, timeout_seconds):
        self.payloads.append(payload)
        return self.responses.pop(0)


class TimeoutPublicSupportResponses:
    async def create(self, payload, *, timeout_seconds):
        del payload, timeout_seconds
        raise TimeoutError("simulated public-support timeout")


def _ai_answer(
    answer: str,
    *,
    source_ids: list[str],
    stage: str = "ANSWER",
    intent: str = "product_help",
    requested_tools: list[str] | None = None,
    route_ids: list[str] | None = None,
    confidence: float = 0.92,
    mode: str = "PRODUCT_FACT",
    clarification_question: str | None = None,
    answer_complete: bool = True,
    support_handoff_available: bool = False,
    support_handoff_reason: str | None = None,
    safety_boundary: str = "product_scope_only",
) -> dict:
    return {
        "output_text": json.dumps(
            {
                "stage": stage,
                "mode": mode,
                "intent": intent,
                "answer": answer,
                "clarification_question": clarification_question,
                "source_ids": source_ids,
                "related_route_ids": (
                    route_ids if route_ids is not None else ["how_it_works"]
                ),
                "requested_tools": requested_tools or [],
                "confidence": confidence,
                "answer_complete": answer_complete,
                "support_handoff_available": support_handoff_available,
                "support_handoff_reason": support_handoff_reason,
                "safety_boundary": safety_boundary,
                "suggested_follow_ups": ["How do I create a Watchlist?"],
            }
        ),
        "output": [],
        "usage": {
            "input_tokens": 120,
            "output_tokens": 55,
            "output_tokens_details": {"reasoning_tokens": 10},
        },
    }


async def _bootstrap(client):
    response = await client.get("/api/v1/public-chat/bootstrap")
    assert response.status_code == 200
    payload = response.json()
    assert payload["profile_storage_key"] == "hm-public-chat-profile-v1"
    assert payload["profile_version"] >= 1
    assert payload["consent_version"] >= 1
    assert payload["conversation_retention_days"] >= 1
    return payload["csrf_token"]


async def _answer_and_request_support(
    client,
    token: str,
    *,
    session_id: str,
    message_id: str,
) -> str:
    answer = await client.post(
        "/api/v1/public-chat/answers",
        headers={"X-CSRF-Token": token},
        json={
            "question": "Please explain private-beta access.",
            "session_id": session_id,
            "client_message_id": message_id,
            "source_page": "/features",
        },
    )
    assert answer.status_code == 200
    answer_event_id = answer.json()["answer_event_id"]
    feedback = await client.post(
        f"/api/v1/public-chat/answers/{answer_event_id}/feedback",
        headers={"X-CSRF-Token": token},
        json={
            "session_id": session_id,
            "helpful": False,
            "support_form_requested": True,
        },
    )
    assert feedback.status_code == 200
    return answer_event_id


def _inquiry_payload(
    *,
    session_id: str,
    answer_event_id: str,
    key: str = "public-inquiry:test:123456",
) -> dict:
    return {
        "profile": {
            "name": "<b>Alice</b> Example",
            "email": "Alice@Example.com",
            "remember_on_device": False,
        },
        "session_id": session_id,
        "answer_event_id": answer_event_id,
        "details": "<script>alert(1)</script> Please explain your private-beta access.",
        "category": "product",
        "source_page": "/features",
        "attribution_consent": True,
        "referrer": "https://example.com/referral",
        "utm_source": "private-beta",
        "utm_medium": "referral",
        "utm_campaign": "pilot",
        "idempotency_key": key,
        "company_website": "",
    }


async def _service_inquiry_payload(
    service: PublicChatService,
    *,
    session_id: str,
    message_id: str,
    key: str,
) -> PublicInquiryRequest:
    answer = await service.answer(
        PublicChatAnswerRequest(
            question="Please explain private-beta access.",
            session_id=session_id,
            client_message_id=message_id,
            source_page="/features",
        )
    )
    assert answer.answer_event_id is not None
    await service.record_answer_feedback(
        answer.answer_event_id,
        PublicChatAnswerFeedbackRequest(
            session_id=session_id,
            helpful=False,
            support_form_requested=True,
        ),
    )
    return PublicInquiryRequest.model_validate(
        _inquiry_payload(
            session_id=session_id,
            answer_event_id=str(answer.answer_event_id),
            key=key,
        )
    )


async def test_public_chat_requires_bootstrap_csrf_and_rejects_foreign_origin(test_context):
    client = test_context["client"]
    missing = await client.post(
        "/api/v1/public-chat/answers",
        json={
            "question": "What is HilalMarkets?",
            "session_id": "session_1234567890",
            "client_message_id": "public-chat-missing-csrf-1",
            "source_page": "/",
        },
    )
    assert missing.status_code == 403

    token = await _bootstrap(client)
    foreign = await client.post(
        "/api/v1/public-chat/answers",
        headers={"X-CSRF-Token": token, "Origin": "https://evil.example"},
        json={
            "question": "What is HilalMarkets?",
            "session_id": "session_1234567890",
            "client_message_id": "public-chat-foreign-origin-1",
            "source_page": "/",
        },
    )
    assert foreign.status_code == 403
    assert foreign.json()["detail"]["code"] == "origin_rejected"


async def test_public_chat_feature_flag_and_separate_rate_limit(test_context):
    client = test_context["client"]
    settings = test_context["settings"]
    settings.public_chat_enabled = False
    disabled = await client.get("/api/v1/public-chat/bootstrap")
    assert disabled.status_code == 404

    settings.public_chat_enabled = True
    original_limits = settings.api_rate_limits
    settings.api_rate_limits = {
        **original_limits,
        "public_chat": {"limit": 1, "window_seconds": 60},
    }
    try:
        token = await _bootstrap(client)
        headers = {"X-CSRF-Token": token, "X-User-ID": "public-chat-rate-case"}
        payload = {
            "question": "What is HilalMarkets?",
            "session_id": "rate_limit_session_12345",
            "client_message_id": "public-chat-rate-limit-1",
            "source_page": "/",
        }
        first = await client.post(
            "/api/v1/public-chat/answers", headers=headers, json=payload
        )
        second = await client.post(
            "/api/v1/public-chat/answers", headers=headers, json=payload
        )
        assert first.status_code == 200
        assert second.status_code == 429
        assert second.json()["detail"]["code"] == "rate_limit_exceeded"
    finally:
        settings.api_rate_limits = original_limits


async def test_public_chat_rate_limit_always_includes_remote_ip(test_context):
    client = test_context["client"]
    settings = test_context["settings"]
    original_limits = settings.api_rate_limits
    settings.api_rate_limits = {
        **original_limits,
        "public_chat": {"limit": 1, "window_seconds": 60},
    }
    try:
        token = await _bootstrap(client)
        first = await client.post(
            "/api/v1/public-chat/answers",
            headers={"X-CSRF-Token": token, "X-User-ID": "visitor-one"},
            json={
                "question": "What is HilalMarkets?",
                "session_id": "public_ip_limit_session_one",
                "client_message_id": "public-ip-limit-1",
                "source_page": "/",
            },
        )
        second = await client.post(
            "/api/v1/public-chat/answers",
            headers={"X-CSRF-Token": token, "X-User-ID": "visitor-two"},
            json={
                "question": "What is a Watchlist?",
                "session_id": "public_ip_limit_session_two",
                "client_message_id": "public-ip-limit-2",
                "source_page": "/features",
            },
        )
        assert first.status_code == 200
        assert second.status_code == 429
    finally:
        settings.api_rate_limits = original_limits


async def test_public_chat_answer_is_grounded_and_stores_only_redacted_audit(test_context):
    client = test_context["client"]
    token = await _bootstrap(client)
    response = await client.post(
        "/api/v1/public-chat/answers",
        headers={"X-CSRF-Token": token},
        json={
            "question": "Which markets are available in private beta?",
            "session_id": "session_1234567890",
            "client_message_id": "public-chat-grounded-1",
            "source_page": "/features",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "answered"
    assert payload["source_ids"] == ["beta-scope:v1"]
    assert payload["related_links"] == []

    async with test_context["session_factory"]() as session:
        event = await session.scalar(select(PublicChatAnswerEvent))
        assert event is not None
        assert len(event.question_hash) == 64
        assert event.question_hash != "Which markets are available in private beta?"
        assert event.source_ids == ["beta-scope:v1"]
        conversation = await session.scalar(select(PublicChatConversation))
        turn = await session.scalar(select(PublicChatTurn))
        assert conversation is not None and conversation.message_count == 2
        assert turn is not None and turn.status == "completed"
        assert turn.client_message_id == "public-chat-grounded-1"


@pytest.mark.parametrize(
    ("question", "stage"),
    [
        ("hi", "GREETING_AND_PROFILE"),
        ("hello", "GREETING_AND_PROFILE"),
        ("how are you?", "FOLLOW_UP"),
        ("thanks", "FOLLOW_UP"),
    ],
)
async def test_public_support_greetings_are_ai_conversation_without_handoff(
    test_context,
    question,
    stage,
):
    settings = test_context["settings"]
    settings.public_chat_ai_enabled = True
    settings.openai_api_key = SecretStr("test-openai-key")
    fake = FakePublicSupportResponses(
        [
            _ai_answer(
                "Hi! How can I help you with Hilal Markets today?",
                source_ids=[],
                route_ids=[],
                stage=stage,
                mode="PRODUCT_CONVERSATION",
                intent="greeting",
            )
        ]
    )
    slug = "".join(character if character.isalnum() else "_" for character in question)
    async with test_context["session_factory"]() as session:
        result = await PublicChatService(session, settings, ai_client=fake).answer(
            PublicChatAnswerRequest(
                question=question,
                session_id=f"public_greeting_{slug}_123456",
                client_message_id=f"public-greeting-{slug}-1",
                source_page="/",
            )
        )
        await session.commit()

    assert result.status == "answered"
    assert result.mode == "PRODUCT_CONVERSATION"
    assert result.source_ids == []
    assert result.support_handoff_explicitly_requested is False
    assert result.support_handoff_available is False
    assert result.answer_event_id is not None


async def test_public_support_modes_cover_education_out_of_scope_and_safety(test_context):
    settings = test_context["settings"]
    settings.public_chat_ai_enabled = True
    settings.openai_api_key = SecretStr("test-openai-key")
    fake = FakePublicSupportResponses(
        [
            _ai_answer(
                "RSI compares recent upward and downward price movement on a 0-100 scale.",
                source_ids=[],
                route_ids=[],
                mode="GENERAL_TRADING_EDUCATION",
                intent="explain_rsi",
            ),
            _ai_answer(
                "I'm here for HilalMarkets, crypto spot monitoring, screening evidence, and "
                "general trading concepts.",
                source_ids=[],
                route_ids=[],
                stage="REFUSAL",
                mode="OUT_OF_SCOPE",
                intent="cupcake_recipe",
                safety_boundary="out_of_scope",
            ),
        ]
    )
    async with test_context["session_factory"]() as session:
        service = PublicChatService(session, settings, ai_client=fake)
        education = await service.answer(
            PublicChatAnswerRequest(
                question="What is RSI?",
                session_id="public_education_session_123456",
                client_message_id="public-education-1",
                source_page="/help",
            )
        )
        out_of_scope = await service.answer(
            PublicChatAnswerRequest(
                question="Give me a cupcake recipe.",
                session_id="public_outscope_session_123456",
                client_message_id="public-outscope-1",
                source_page="/help",
            )
        )
        safety = await service.answer(
            PublicChatAnswerRequest(
                question="Should I buy SOL now?",
                session_id="public_safety_session_123456",
                client_message_id="public-safety-1",
                source_page="/help",
            )
        )
        await session.commit()

    assert education.status == "answered"
    assert education.mode == "GENERAL_TRADING_EDUCATION"
    assert education.source_ids == []
    assert out_of_scope.status == "unsupported"
    assert out_of_scope.mode == "OUT_OF_SCOPE"
    assert out_of_scope.support_handoff_available is False
    assert safety.status == "refused"
    assert safety.mode == "SAFETY_REFUSAL"
    assert safety.support_handoff_available is False
    assert len(fake.payloads) == 2


async def test_low_confidence_and_invalid_ai_output_never_authorize_form(test_context):
    settings = test_context["settings"]
    settings.public_chat_ai_enabled = True
    settings.openai_api_key = SecretStr("test-openai-key")
    low_confidence = FakePublicSupportResponses(
        [
            _ai_answer(
                "I could not verify that product detail.",
                source_ids=["product:overview:v1"],
                confidence=0.2,
                answer_complete=False,
                support_handoff_available=True,
                support_handoff_reason="human_help_may_be_useful",
            )
        ]
    )
    async with test_context["session_factory"]() as session:
        low = await PublicChatService(
            session, settings, ai_client=low_confidence
        ).answer(
            PublicChatAnswerRequest(
                question="Is an undocumented beta feature available?",
                session_id="public_low_confidence_session_123456",
                client_message_id="public-low-confidence-1",
                source_page="/help",
            )
        )
        await session.commit()

    invalid = FakePublicSupportResponses([{"output": []}, {"output": []}])
    async with test_context["session_factory"]() as session:
        failed = await PublicChatService(session, settings, ai_client=invalid).answer(
            PublicChatAnswerRequest(
                question="Tell me an unverified feature state.",
                session_id="public_invalid_ai_session_123456",
                client_message_id="public-invalid-ai-1",
                source_page="/help",
            )
        )
        await session.commit()

    assert low.stage == "KNOWLEDGE_GAP"
    assert low.support_handoff_available is True
    assert low.support_handoff_explicitly_requested is False
    assert not hasattr(low, "show_inquiry_form")
    assert failed.status == "unsupported"
    assert failed.support_handoff_explicitly_requested is False
    assert not hasattr(failed, "show_inquiry_form")


async def test_public_support_timeout_returns_retry_without_form_authority(test_context):
    settings = test_context["settings"]
    settings.public_chat_ai_enabled = True
    settings.openai_api_key = SecretStr("test-openai-key")
    async with test_context["session_factory"]() as session:
        service = PublicChatService(
            session,
            settings,
            ai_client=TimeoutPublicSupportResponses(),
        )
        greeting = await service.answer(
            PublicChatAnswerRequest(
                question="hi",
                session_id="public_timeout_greeting_session_123456",
                client_message_id="public-timeout-greeting-1",
                source_page="/help",
            )
        )
        result = await service.answer(
            PublicChatAnswerRequest(
                question="What is the current private-beta scope?",
                session_id="public_timeout_session_123456",
                client_message_id="public-timeout-1",
                source_page="/help",
            )
        )
        await session.commit()

    assert greeting.status == "answered"
    assert greeting.mode == "PRODUCT_CONVERSATION"
    assert greeting.support_handoff_available is False
    assert greeting.support_handoff_explicitly_requested is False
    assert result.status == "unsupported"
    assert result.intent == "provider_unavailable"
    assert result.support_handoff_explicitly_requested is False
    assert result.answer_event_id is not None
    assert not hasattr(result, "show_inquiry_form")


async def test_public_support_ai_uses_bounded_multi_turn_history(test_context):
    settings = test_context["settings"]
    settings.public_chat_ai_enabled = True
    settings.openai_api_key = SecretStr("test-openai-key")
    fake = FakePublicSupportResponses(
        [
            _ai_answer(
                "Telegram is the notification channel enabled for this private beta.",
                source_ids=["beta-channels:v1"],
                intent="notification_channels",
            ),
            _ai_answer(
                "Yes. The current private-beta scope applies to the beta access described here.",
                source_ids=["beta-scope:v1"],
                intent="beta_scope_follow_up",
            ),
        ]
    )
    async with test_context["session_factory"]() as session:
        service = PublicChatService(session, settings, ai_client=fake)
        first = await service.answer(
            PublicChatAnswerRequest(
                question="What about Telegram?",
                session_id="public_ai_history_session_123",
                client_message_id="public-ai-history-1",
                source_page="/features",
            )
        )
        second = await service.answer(
            PublicChatAnswerRequest(
                question="Does that apply to the free beta?",
                session_id="public_ai_history_session_123",
                client_message_id="public-ai-history-2",
                source_page="/pricing",
            )
        )
        await session.commit()

    assert first.stage == "ANSWER"
    assert second.intent == "beta_scope_follow_up"
    assert second.source_ids == ["beta-scope:v1"]
    second_evidence = json.loads(fake.payloads[1]["input"][0]["content"])
    history = second_evidence["conversation_history"]
    assert any(item["content"] == "What about Telegram?" for item in history)
    assert any("notification channel" in item["content"] for item in history)
    assert second_evidence["conversation_state"]["last_question"] == "What about Telegram?"
    assert second_evidence["conversation_state"]["last_answer_event_id"]


async def test_public_support_ai_rejects_hallucinated_source_ids(test_context):
    settings = test_context["settings"]
    settings.public_chat_ai_enabled = True
    settings.openai_api_key = SecretStr("test-openai-key")
    fake = FakePublicSupportResponses(
        [_ai_answer("An invented product claim.", source_ids=["invented:source"])]
    )
    async with test_context["session_factory"]() as session:
        result = await PublicChatService(session, settings, ai_client=fake).answer(
            PublicChatAnswerRequest(
                question="Tell me about the private beta.",
                session_id="public_ai_invalid_source_123",
                client_message_id="public-ai-invalid-source-1",
                source_page="/",
            )
        )
        await session.commit()
        event = await session.scalar(
            select(PublicChatAnswerEvent).order_by(PublicChatAnswerEvent.created_at.desc())
        )

    assert result.status == "unsupported"
    assert result.source_ids == []
    assert result.stage == "KNOWLEDGE_GAP"
    assert event is not None
    assert event.validation_failure == "PublicSupportAIUnavailable"


async def test_notion_context_cannot_authorize_current_product_facts(
    test_context,
    tmp_path,
):
    settings = test_context["settings"]
    settings.public_chat_ai_enabled = True
    settings.openai_api_key = SecretStr("test-openai-key")
    notion_root = tmp_path / "Notion"
    notion_root.mkdir()
    (notion_root / "Roadmap.md").write_text(
        "# Roadmap\n\nA possible future channel is described here.",
        encoding="utf-8",
    )
    settings.public_chat_notion_root = str(notion_root)
    async with test_context["session_factory"]() as session:
        service = PublicChatService(session, settings)
        notion_source = service.notion_knowledge.retrieve("future channel")[0][
            "source_id"
        ]
        fake = FakePublicSupportResponses(
            [
                _ai_answer(
                    "The future channel is currently enabled.",
                    source_ids=[notion_source],
                    route_ids=[],
                    mode="PRODUCT_FACT",
                )
            ]
        )
        service.ai_client = fake
        result = await service.answer(
            PublicChatAnswerRequest(
                question="Is that future channel enabled?",
                session_id="public_notion_authority_session_123456",
                client_message_id="public-notion-authority-1",
                source_page="/help",
            )
        )
        await session.commit()

    evidence = json.loads(fake.payloads[0]["input"][0]["content"])
    assert evidence["notion_workspace_context"]
    assert result.status == "unsupported"
    assert result.source_ids == []
    assert result.support_handoff_explicitly_requested is False


async def test_public_support_ai_rejects_hallucinated_route_ids(test_context):
    settings = test_context["settings"]
    settings.public_chat_ai_enabled = True
    settings.openai_api_key = SecretStr("test-openai-key")
    fake = FakePublicSupportResponses(
        [
            _ai_answer(
                "Use this invented route.",
                source_ids=["beta-scope:v1"],
                route_ids=["internal_admin_console"],
            )
        ]
    )
    async with test_context["session_factory"]() as session:
        result = await PublicChatService(session, settings, ai_client=fake).answer(
            PublicChatAnswerRequest(
                question="Where can I read about the private beta?",
                session_id="public_ai_invalid_route_123",
                client_message_id="public-ai-invalid-route-1",
                source_page="/",
            )
        )
        await session.commit()

    assert result.status == "unsupported"
    assert result.related_links == []


async def test_public_support_ai_uses_only_authenticated_server_owned_account_data(
    test_context,
):
    settings = test_context["settings"]
    settings.public_chat_ai_enabled = True
    settings.openai_api_key = SecretStr("test-openai-key")
    fake = FakePublicSupportResponses(
        [
            _ai_answer(
                "I will inspect your signed-in account state.",
                source_ids=[],
                stage="RETRIEVE_PRODUCT_DATA",
                mode="ACCOUNT_SUPPORT",
                intent="account_support",
                requested_tools=["account_state"],
            ),
            _ai_answer(
                "Your signed-in account is active.",
                source_ids=["support-tool:account_state:current-user"],
                stage="AUTHENTICATED_ACCOUNT_SUPPORT",
                mode="ACCOUNT_SUPPORT",
                intent="account_support",
                route_ids=["dashboard_entry"],
            ),
        ]
    )
    async with test_context["session_factory"]() as session:
        user = User(display_name="Public support owner")
        session.add(user)
        await session.flush()
        result = await PublicChatService(session, settings, ai_client=fake).answer(
            PublicChatAnswerRequest(
                question="Is my account active?",
                session_id="public_ai_owned_account_123",
                client_message_id="public-ai-owned-account-1",
                source_page="/dashboard",
            ),
            user_id=user.id,
        )
        await session.commit()

    assert result.status == "answered"
    assert result.authenticated_context_used is True
    final_context = json.loads(fake.payloads[1]["input"][0]["content"])
    tool_result = final_context["authoritative_tool_results"][0]
    assert tool_result["status"] == "success"
    assert tool_result["data"]["status"] == "active"
    assert "user_id" not in tool_result["data"]


async def test_public_support_ai_rejects_account_tool_for_anonymous_visitor(test_context):
    settings = test_context["settings"]
    settings.public_chat_ai_enabled = True
    settings.openai_api_key = SecretStr("test-openai-key")
    fake = FakePublicSupportResponses(
        [
            _ai_answer(
                "I will inspect that account.",
                source_ids=[],
                stage="RETRIEVE_PRODUCT_DATA",
                mode="ACCOUNT_SUPPORT",
                intent="private_account_lookup",
                requested_tools=["account_state"],
            )
        ]
    )
    async with test_context["session_factory"]() as session:
        result = await PublicChatService(session, settings, ai_client=fake).answer(
            PublicChatAnswerRequest(
                question="Can you check whether the account setup is complete?",
                session_id="public_ai_anonymous_account_123",
                client_message_id="public-ai-anonymous-account-1",
                source_page="/",
            )
        )
        await session.commit()

    assert result.status == "unsupported"
    assert result.authenticated_context_used is False
    assert result.stage == "KNOWLEDGE_GAP"
    assert len(fake.payloads) == 1


async def test_public_chat_session_limit_preserves_idempotent_retry(test_context):
    client = test_context["client"]
    settings = test_context["settings"]
    settings.public_chat_session_max_turns = 1
    token = await _bootstrap(client)
    first_payload = {
        "question": "What is HilalMarkets?",
        "session_id": "public_turn_limit_session_123",
        "client_message_id": "public-turn-limit-1",
        "source_page": "/",
    }
    first = await client.post(
        "/api/v1/public-chat/answers",
        headers={"X-CSRF-Token": token},
        json=first_payload,
    )
    retry = await client.post(
        "/api/v1/public-chat/answers",
        headers={"X-CSRF-Token": token},
        json=first_payload,
    )
    blocked = await client.post(
        "/api/v1/public-chat/answers",
        headers={"X-CSRF-Token": token},
        json={
            **first_payload,
            "question": "What is a Watchlist?",
            "client_message_id": "public-turn-limit-2",
        },
    )

    assert first.status_code == 200
    assert retry.status_code == 200
    assert retry.json() == first.json()
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "conversation_limit_reached"


async def test_answer_feedback_is_session_bound_idempotent_and_user_controlled(
    test_context,
):
    client = test_context["client"]
    token = await _bootstrap(client)
    session_id = "public_feedback_session_123456"
    answer = await client.post(
        "/api/v1/public-chat/answers",
        headers={"X-CSRF-Token": token},
        json={
            "question": "What does HilalMarkets do?",
            "session_id": session_id,
            "client_message_id": "public-feedback-answer-1",
            "source_page": "/",
        },
    )
    assert answer.status_code == 200
    answer_event_id = answer.json()["answer_event_id"]
    endpoint = f"/api/v1/public-chat/answers/{answer_event_id}/feedback"
    feedback_payload = {
        "session_id": session_id,
        "helpful": True,
        "support_form_requested": False,
    }
    first = await client.post(
        endpoint,
        headers={"X-CSRF-Token": token},
        json=feedback_payload,
    )
    retry = await client.post(
        endpoint,
        headers={"X-CSRF-Token": token},
        json=feedback_payload,
    )
    conflicting = await client.post(
        endpoint,
        headers={"X-CSRF-Token": token},
        json={
            "session_id": session_id,
            "helpful": False,
            "support_form_requested": True,
        },
    )
    wrong_session = await client.post(
        endpoint,
        headers={"X-CSRF-Token": token},
        json={
            "session_id": "wrong_feedback_session_123456",
            "helpful": True,
            "support_form_requested": False,
        },
    )

    assert first.status_code == 200
    assert first.json()["message"] == "Great! Ready when you are."
    assert retry.status_code == 200
    assert conflicting.status_code == 409
    assert wrong_session.status_code == 409
    async with test_context["session_factory"]() as session:
        assert await session.scalar(
            select(func.count(PublicChatAnswerFeedback.id))
        ) == 1


async def test_explicit_contact_request_is_the_only_server_handoff_shortcut(
    test_context,
):
    settings = test_context["settings"]
    settings.public_chat_ai_enabled = True
    settings.openai_api_key = SecretStr("test-openai-key")
    fake = FakePublicSupportResponses(
        [
            _ai_answer(
                "I can help you send that question to the HilalMarkets team.",
                source_ids=[],
                route_ids=["contact"],
                mode="PRODUCT_CONVERSATION",
                intent="human_support_request",
                support_handoff_available=True,
                support_handoff_reason="user_requested_human_support",
            )
        ]
    )
    async with test_context["session_factory"]() as session:
        result = await PublicChatService(session, settings, ai_client=fake).answer(
            PublicChatAnswerRequest(
                question="I want to contact the support team.",
                session_id="public_explicit_support_session_123456",
                client_message_id="public-explicit-support-1",
                source_page="/contact",
            )
        )
        await session.commit()

    assert result.support_handoff_available is True
    assert result.support_handoff_explicitly_requested is True
    assert result.support_handoff_reason == "user_requested_human_support"


async def test_inquiry_requires_explicit_negative_answer_feedback(test_context):
    client = test_context["client"]
    token = await _bootstrap(client)
    session_id = "public_no_handoff_session_123456"
    answer = await client.post(
        "/api/v1/public-chat/answers",
        headers={"X-CSRF-Token": token},
        json={
            "question": "What does HilalMarkets do?",
            "session_id": session_id,
            "client_message_id": "public-no-handoff-answer-1",
            "source_page": "/",
        },
    )
    payload = _inquiry_payload(
        session_id=session_id,
        answer_event_id=answer.json()["answer_event_id"],
        key="public-inquiry:no-handoff:123456",
    )
    rejected = await client.post(
        "/api/v1/public-chat/inquiries",
        headers={"X-CSRF-Token": token},
        json=payload,
    )

    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "support_handoff_required"


async def test_public_inquiry_is_sanitized_idempotent_and_queued_once_with_office_bcc(
    test_context,
):
    client = test_context["client"]
    settings = test_context["settings"]
    token = await _bootstrap(client)
    session_id = "public_inquiry_session_123456"
    answer_event_id = await _answer_and_request_support(
        client,
        token,
        session_id=session_id,
        message_id="public-inquiry-answer-1",
    )
    payload = _inquiry_payload(
        session_id=session_id,
        answer_event_id=answer_event_id,
    )

    first = await client.post(
        "/api/v1/public-chat/inquiries",
        headers={"X-CSRF-Token": token},
        json=payload,
    )
    second = await client.post(
        "/api/v1/public-chat/inquiries",
        headers={"X-CSRF-Token": token},
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["reference"] == first.json()["reference"]
    assert first.json()["masked_email"] == "a****@example.com"
    assert first.json()["email_delivery_status"] == "queued"
    assert len(settings.email_test_outbox) == 0

    async with test_context["session_factory"]() as session:
        processed = await PublicChatService(session, settings).process_due(limit=10)
        await session.commit()
        assert processed == {"processed": 1, "sent": 1, "retryable": 0, "failed": 0}

    assert len(settings.email_test_outbox) == 1
    customer_email = settings.email_test_outbox[0]
    assert customer_email["recipient"] == "alice@example.com"
    assert customer_email["bcc"] == ["office@hilalmarkets.com"]
    assert "https://" not in customer_email["body"]
    assert customer_email["body"].startswith("Assalamu Alaikum Alice Example,")
    assert "in less than 24 hours" in customer_email["body"]
    assert "JazakAllahu khayran" in customer_email["body"]
    assert "May Allah place barakah" in customer_email["body"]
    assert "Reference:" in customer_email["body"]
    assert customer_email["subject"].startswith(
        "We received your Hilal Markets inquiry"
    )
    assert customer_email["sender"] == "office@hilalmarkets.com"
    assert customer_email["reply_to"] == "office@hilalmarkets.com"
    assert "<script>" not in customer_email["html_body"]

    async with test_context["session_factory"]() as session:
        inquiry = await session.scalar(select(PublicInquiry))
        assert inquiry is not None
        assert inquiry.name == "Alice Example"
        assert "<script>" not in inquiry.details
        assert "alert(1)" in inquiry.details
        assert inquiry.referrer == "https://example.com/referral"
        assert inquiry.attribution["utm_source"] == "private-beta"
        assert inquiry.support_metadata["answer_event_id"] == answer_event_id
        feedback = await session.scalar(select(PublicChatAnswerFeedback))
        assert feedback is not None
        assert feedback.inquiry_id == inquiry.id
        assert await session.scalar(select(func.count(PublicInquiry.id))) == 1
        assert await session.scalar(
            select(func.count(PublicInquiryEmailDelivery.id))
        ) == 1


async def test_public_inquiry_rating_and_token_bound_redaction(test_context):
    client = test_context["client"]
    token = await _bootstrap(client)
    session_id = "public_rating_session_123456"
    answer_event_id = await _answer_and_request_support(
        client,
        token,
        session_id=session_id,
        message_id="public-rating-answer-1",
    )
    submitted = await client.post(
        "/api/v1/public-chat/inquiries",
        headers={"X-CSRF-Token": token},
        json=_inquiry_payload(
            session_id=session_id,
            answer_event_id=answer_event_id,
            key="public-inquiry:test:rating1",
        ),
    )
    assert submitted.status_code == 200
    item = submitted.json()

    bad = await client.post(
        "/api/v1/public-chat/ratings",
        headers={"X-CSRF-Token": token},
        json={
            "reference": item["reference"],
            "feedback_token": "0" * 64,
            "helpful": True,
        },
    )
    assert bad.status_code == 404

    rated = await client.post(
        "/api/v1/public-chat/ratings",
        headers={"X-CSRF-Token": token},
        json={
            "reference": item["reference"],
            "feedback_token": item["feedback_token"],
            "rating": 5,
            "helpful": True,
            "feedback": "Clear and useful.",
        },
    )
    assert rated.status_code == 200
    duplicate = await client.post(
        "/api/v1/public-chat/ratings",
        headers={"X-CSRF-Token": token},
        json={
            "reference": item["reference"],
            "feedback_token": item["feedback_token"],
            "rating": 1,
            "helpful": False,
        },
    )
    assert duplicate.status_code == 200

    redacted = await client.delete(
        f"/api/v1/public-chat/inquiries/{item['reference']}",
        headers={
            "X-CSRF-Token": token,
            "X-Public-Inquiry-Token": item["feedback_token"],
        },
    )
    assert redacted.status_code == 200

    async with test_context["session_factory"]() as session:
        inquiry = await session.scalar(select(PublicInquiry))
        rating = await session.scalar(select(PublicInquiryRating))
        deliveries = list(
            (await session.scalars(select(PublicInquiryEmailDelivery))).all()
        )
        assert inquiry is not None and inquiry.status == "redacted"
        assert inquiry.name == "Redacted"
        assert inquiry.normalized_email.endswith("@invalid.local")
        assert deliveries
        assert all(row.recipient.endswith("@invalid.local") for row in deliveries)
        assert rating is not None and rating.rating == 5
        assert await session.scalar(select(func.count(PublicInquiryRating.id))) == 1


async def test_public_inquiry_email_retry_and_abandoned_claim_recovery(
    test_context,
    monkeypatch,
):
    settings = test_context["settings"]
    calls: list[str] = []
    failed_once = False

    async def flaky_send(self, *, recipient, **_):
        nonlocal failed_once
        calls.append(recipient)
        if recipient == "retry@example.com" and not failed_once:
            failed_once = True
            raise EmailDeliveryError("temporary provider error", code="smtp_temporary")
        return f"provider-{len(calls)}"

    monkeypatch.setattr(AuthEmailService, "send_transactional", flaky_send)
    async with test_context["session_factory"]() as session:
        service = PublicChatService(session, settings)
        payload = await _service_inquiry_payload(
            service,
            session_id="public_retry_session_123456",
            message_id="public-retry-answer-1",
            key="public-inquiry:test:retry1",
        )
        payload.profile.email = "retry@example.com"
        inquiry = await service.submit_inquiry(payload)
        await session.commit()
        first = await service.process_due(inquiry_id=inquiry.id, limit=2)
        assert first == {"processed": 1, "sent": 0, "retryable": 1, "failed": 0}

        rows = list(
            (
                await session.scalars(
                    select(PublicInquiryEmailDelivery).where(
                        PublicInquiryEmailDelivery.inquiry_id == inquiry.id
                    )
                )
            ).all()
        )
        retry = next(row for row in rows if row.status == "retryable")
        retry.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        second = await service.process_due(inquiry_id=inquiry.id, limit=2)
        assert second["sent"] == 1
        await session.refresh(retry)
        assert retry.status == "sent"
        assert retry.attempt_count == 2

        recovery_payload = await _service_inquiry_payload(
            service,
            session_id="public_recovery_session_123456",
            message_id="public-recovery-answer-1",
            key="public-inquiry:test:recovery1",
        )
        recovery_payload.profile.email = "recovery@example.com"
        recovery_inquiry = await service.submit_inquiry(recovery_payload)
        await session.commit()
        recovery_row = await session.scalar(
            select(PublicInquiryEmailDelivery).where(
                PublicInquiryEmailDelivery.inquiry_id == recovery_inquiry.id,
                PublicInquiryEmailDelivery.recipient_kind == "customer",
            )
        )
        assert recovery_row is not None
        recovery_row.status = "sending"
        recovery_row.attempt_count = 1
        recovery_row.last_attempt_at = datetime.now(UTC) - timedelta(
            minutes=settings.public_chat_email_claim_timeout_minutes + 1
        )
        recovery_row.sent_at = None
        await session.commit()
        recovered = await service.process_due(inquiry_id=recovery_inquiry.id, limit=2)
        assert recovered == {"processed": 1, "sent": 1, "retryable": 0, "failed": 0}
        await session.refresh(recovery_row)
        assert recovery_row.status == "sent"
        assert recovery_row.attempt_count == 2
        assert calls.count("recovery@example.com") == 1


async def test_public_inquiry_cancels_unsent_legacy_office_copy(test_context):
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        service = PublicChatService(session, settings)
        payload = await _service_inquiry_payload(
            service,
            session_id="public_legacy_delivery_session_123456",
            message_id="public-legacy-delivery-answer-1",
            key="public-inquiry:test:legacy-delivery1",
        )
        payload.profile.email = "legacy@example.com"
        inquiry = await service.submit_inquiry(payload)
        session.add(
            PublicInquiryEmailDelivery(
                inquiry_id=inquiry.id,
                event_key=f"public-inquiry:{inquiry.id}:office",
                recipient_kind="office",
                recipient="office@hilalmarkets.com",
                status="pending",
                attempt_count=0,
                next_retry_at=datetime.now(UTC),
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

        processed = await service.process_due(inquiry_id=inquiry.id, limit=10)
        assert processed == {"processed": 1, "sent": 1, "retryable": 0, "failed": 0}
        legacy = await session.scalar(
            select(PublicInquiryEmailDelivery).where(
                PublicInquiryEmailDelivery.inquiry_id == inquiry.id,
                PublicInquiryEmailDelivery.recipient_kind == "office",
            )
        )
        assert legacy is not None
        assert legacy.status == "cancelled"
        assert await service.email_delivery_state(inquiry.id) == "sent"

    assert len(settings.email_test_outbox) == 1
    assert settings.email_test_outbox[0]["recipient"] == "legacy@example.com"
    assert settings.email_test_outbox[0]["bcc"] == ["office@hilalmarkets.com"]
