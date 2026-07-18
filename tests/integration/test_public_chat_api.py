import json
from datetime import UTC, datetime, timedelta

from pydantic import SecretStr
from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    PublicChatAnswerEvent,
    PublicChatConversation,
    PublicChatTurn,
    PublicInquiry,
    PublicInquiryEmailDelivery,
    PublicInquiryRating,
    User,
)
from ai_market_monitor.schemas.public_chat import PublicChatAnswerRequest
from ai_market_monitor.services.email_delivery import AuthEmailService, EmailDeliveryError
from ai_market_monitor.services.public_chat import PublicChatService


class FakePublicSupportResponses:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.payloads: list[dict] = []

    async def create(self, payload, *, timeout_seconds):
        self.payloads.append(payload)
        return self.responses.pop(0)


def _ai_answer(
    answer: str,
    *,
    source_ids: list[str],
    stage: str = "ANSWER",
    intent: str = "product_help",
    requested_tools: list[str] | None = None,
    route_ids: list[str] | None = None,
    confidence: float = 0.92,
) -> dict:
    return {
        "output_text": json.dumps(
            {
                "stage": stage,
                "intent": intent,
                "answer": answer,
                "tone": "friendly",
                "clarification_question": None,
                "source_ids": source_ids,
                "related_route_ids": route_ids or ["how_it_works"],
                "requested_tools": requested_tools or [],
                "confidence": confidence,
                "answer_complete": True,
                "show_inquiry_form": False,
                "inquiry_category": None,
                "handoff_reason": None,
                "safety_boundary": "product_scope_only",
                "suggested_follow_ups": ["How do I create a Watch Plan?"],
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


def _inquiry_payload(*, key: str = "public-inquiry:test:123456") -> dict:
    return {
        "profile": {
            "name": "<b>Alice</b> Example",
            "email": "Alice@Example.com",
            "remember_on_device": False,
        },
        "details": "<script>alert(1)</script> Please explain your private-beta access.",
        "category": "product",
        "source_page": "/features",
        "attribution_consent": True,
        "referrer": "https://example.com/referral",
        "utm_source": "private-beta",
        "utm_medium": "referral",
        "utm_campaign": "pilot",
        "knowledge_gap_category": "beta_access",
        "idempotency_key": key,
        "company_website": "",
    }


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
                "question": "What is a Watch Plan?",
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
    assert payload["related_links"][0]["path"] == "/how-it-works"

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
    assert result.related_links[-1].route_id == "contact"


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
                intent="account_support",
                requested_tools=["account_state"],
            ),
            _ai_answer(
                "Your signed-in account is active.",
                source_ids=["support-tool:account_state:current-user"],
                stage="AUTHENTICATED_ACCOUNT_SUPPORT",
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
            "question": "What is a Watch Plan?",
            "client_message_id": "public-turn-limit-2",
        },
    )

    assert first.status_code == 200
    assert retry.status_code == 200
    assert retry.json() == first.json()
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "conversation_limit_reached"


async def test_public_inquiry_is_sanitized_idempotent_and_queued_for_both_recipients(
    test_context,
):
    client = test_context["client"]
    settings = test_context["settings"]
    token = await _bootstrap(client)
    payload = _inquiry_payload()

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
        assert processed == {"processed": 2, "sent": 2, "retryable": 0, "failed": 0}

    assert len(settings.email_test_outbox) == 2
    assert {item["recipient"] for item in settings.email_test_outbox} == {
        "alice@example.com",
        "office@hilalmarkets.com",
    }
    customer_email = next(
        item for item in settings.email_test_outbox
        if item["recipient"] == "alice@example.com"
    )
    office_email = next(
        item for item in settings.email_test_outbox
        if item["recipient"] == "office@hilalmarkets.com"
    )
    assert "Help Center:" in customer_email["body"]
    assert "Support:" in customer_email["body"]
    assert "Privacy:" in customer_email["body"]
    assert "Reference:" in customer_email["body"]
    assert "Knowledge gap:" in office_email["body"]
    assert "Source page:" in office_email["body"]
    assert "Attribution:" in office_email["body"]
    assert "<script>" not in customer_email["html_body"]
    assert "<script>" not in office_email["html_body"]

    async with test_context["session_factory"]() as session:
        inquiry = await session.scalar(select(PublicInquiry))
        assert inquiry is not None
        assert inquiry.name == "Alice Example"
        assert "<script>" not in inquiry.details
        assert "alert(1)" in inquiry.details
        assert inquiry.referrer == "https://example.com/referral"
        assert inquiry.attribution["utm_source"] == "private-beta"
        assert await session.scalar(select(func.count(PublicInquiry.id))) == 1
        assert await session.scalar(
            select(func.count(PublicInquiryEmailDelivery.id))
        ) == 2


async def test_public_inquiry_rating_and_token_bound_redaction(test_context):
    client = test_context["client"]
    token = await _bootstrap(client)
    submitted = await client.post(
        "/api/v1/public-chat/inquiries",
        headers={"X-CSRF-Token": token},
        json=_inquiry_payload(key="public-inquiry:test:rating1"),
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
    payload = _inquiry_payload(key="public-inquiry:test:retry1")
    payload["profile"]["email"] = "retry@example.com"

    async with test_context["session_factory"]() as session:
        service = PublicChatService(session, settings)
        from ai_market_monitor.schemas.public_chat import PublicInquiryRequest

        inquiry = await service.submit_inquiry(PublicInquiryRequest.model_validate(payload))
        await session.commit()
        first = await service.process_due(inquiry_id=inquiry.id, limit=2)
        assert first == {"processed": 2, "sent": 1, "retryable": 1, "failed": 0}

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

        recovery_payload = _inquiry_payload(key="public-inquiry:test:recovery1")
        recovery_payload["profile"]["email"] = "recovery@example.com"
        recovery_inquiry = await service.submit_inquiry(
            PublicInquiryRequest.model_validate(recovery_payload)
        )
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
        assert recovered == {"processed": 2, "sent": 2, "retryable": 0, "failed": 0}
        await session.refresh(recovery_row)
        assert recovery_row.status == "sent"
        assert recovery_row.attempt_count == 2
        assert calls.count("recovery@example.com") == 1
