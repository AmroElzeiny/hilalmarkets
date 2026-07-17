from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    PublicChatAnswerEvent,
    PublicInquiry,
    PublicInquiryEmailDelivery,
    PublicInquiryRating,
)
from ai_market_monitor.services.email_delivery import AuthEmailService, EmailDeliveryError
from ai_market_monitor.services.public_chat import PublicChatService


async def _bootstrap(client):
    response = await client.get("/api/v1/public-chat/bootstrap")
    assert response.status_code == 200
    payload = response.json()
    assert payload["profile_storage_key"] == "hm-public-chat-profile-v1"
    assert payload["profile_version"] >= 1
    assert payload["consent_version"] >= 1
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


async def test_public_chat_answer_is_grounded_and_stores_only_redacted_audit(test_context):
    client = test_context["client"]
    token = await _bootstrap(client)
    response = await client.post(
        "/api/v1/public-chat/answers",
        headers={"X-CSRF-Token": token},
        json={
            "question": "Which markets are available in private beta?",
            "session_id": "session_1234567890",
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


async def test_public_inquiry_is_sanitized_idempotent_and_delivered_to_both_recipients(
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
    assert first.json()["email_delivery_status"] == "sent"
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
