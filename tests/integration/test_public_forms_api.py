import json
from urllib.parse import quote

import httpx
from sqlalchemy import func, select

from ai_market_monitor.api.routers.public_forms import get_public_forms_sheet_transport
from ai_market_monitor.db.models import (
    ContactEmailDelivery,
    ContactSubmission,
    WaitlistSheetDelivery,
    WaitlistSignup,
)


async def _csrf(client) -> str:
    response = await client.get("/api/v1/public-forms/bootstrap")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _waitlist_payload(
    *,
    email: str = "visitor@example.com",
    key: str = "waitlist:test:1234567890",
    beta_contact_consent: bool = True,
) -> dict:
    return {
        "email": email,
        "beta_contact_consent": beta_contact_consent,
        "source_page": "/?utm_source=private-value",
        "attribution": {
            "utm_source": "newsletter",
            "utm_medium": "email",
            "utm_campaign": "private-beta",
            "utm_content": "hero",
            "utm_term": "screening",
            "referrer": "https://example.org/article?private=query",
            "landing_page": "https://testserver/?utm_source=newsletter",
        },
        "idempotency_key": key,
        "company_website": "",
    }


async def test_waitlist_is_idempotent_and_drops_attribution_without_consent(test_context):
    test_context["settings"].waitlist_google_sheets_enabled = False
    client = test_context["client"]
    token = await _csrf(client)
    first = await client.post(
        "/api/v1/public-forms/waitlist",
        headers={"X-CSRF-Token": token},
        json=_waitlist_payload(),
    )
    assert first.status_code == 200
    assert first.json() == {
        "status": "created",
        "created": True,
        "code": "waitlist_created",
        "sheet_delivery_status": "not_configured",
        "message": "You are on the waitlist. We will contact you as access becomes available.",
    }

    duplicate = await client.post(
        "/api/v1/public-forms/waitlist",
        headers={"X-CSRF-Token": token},
        json=_waitlist_payload(
            email="VISITOR@EXAMPLE.COM",
            key="waitlist:test:0987654321",
        ),
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False
    assert duplicate.json()["code"] == "duplicate_email"

    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(func.count()).select_from(WaitlistSignup)) == 1
        assert await session.scalar(select(func.count()).select_from(WaitlistSheetDelivery)) == 1
        signup = await session.scalar(select(WaitlistSignup))
        assert signup is not None
        assert signup.attribution == {}
        assert signup.source_page == "/"


async def test_waitlist_records_the_beta_contact_answer_exactly_as_it_arrives(
    test_context,
):
    """The box is offered ticked, but only what the person left is stored.

    A cleared box still joins the waitlist. Recording it as agreement would make the
    saved record say something the visitor never said.
    """

    test_context["settings"].waitlist_google_sheets_enabled = False
    client = test_context["client"]
    token = await _csrf(client)

    agreed = await client.post(
        "/api/v1/public-forms/waitlist",
        headers={"X-CSRF-Token": token},
        json=_waitlist_payload(email="yes@example.com", key="waitlist:test:consent-yes"),
    )
    assert agreed.status_code == 200

    declined = await client.post(
        "/api/v1/public-forms/waitlist",
        headers={"X-CSRF-Token": token},
        json=_waitlist_payload(
            email="no@example.com",
            key="waitlist:test:consent-no",
            beta_contact_consent=False,
        ),
    )
    assert declined.status_code == 200
    assert declined.json()["created"] is True

    async with test_context["session_factory"]() as session:
        rows = {
            row.normalized_email: row.beta_contact_consent
            for row in (await session.scalars(select(WaitlistSignup))).all()
        }
    assert rows == {"yes@example.com": True, "no@example.com": False}


async def test_reusing_one_request_key_with_a_different_answer_is_refused(test_context):
    """One identifier means one submission, and the answer is part of what it means."""

    test_context["settings"].waitlist_google_sheets_enabled = False
    client = test_context["client"]
    token = await _csrf(client)
    key = "waitlist:test:consent-reuse"

    first = await client.post(
        "/api/v1/public-forms/waitlist",
        headers={"X-CSRF-Token": token},
        json=_waitlist_payload(email="reuse@example.com", key=key),
    )
    assert first.status_code == 200

    changed = await client.post(
        "/api/v1/public-forms/waitlist",
        headers={"X-CSRF-Token": token},
        json=_waitlist_payload(
            email="reuse@example.com",
            key=key,
            beta_contact_consent=False,
        ),
    )
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "idempotency_conflict"

    # The stored answer is still the one that was actually sent first.
    async with test_context["session_factory"]() as session:
        signup = await session.scalar(select(WaitlistSignup))
        assert signup is not None
        assert signup.beta_contact_consent is True


async def test_waitlist_accepts_same_origin_and_rejects_foreign_origin(test_context):
    client = test_context["client"]
    token = await _csrf(client)

    same_origin = await client.post(
        "/api/v1/public-forms/waitlist",
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
        json=_waitlist_payload(
            email="same-origin@example.com",
            key="waitlist:test:same-origin",
        ),
    )
    assert same_origin.status_code == 200

    foreign = await client.post(
        "/api/v1/public-forms/waitlist",
        headers={"X-CSRF-Token": token, "Origin": "https://evil.example"},
        json=_waitlist_payload(
            email="foreign-origin@example.com",
            key="waitlist:test:foreign-origin",
        ),
    )
    assert foreign.status_code == 403
    assert foreign.json()["detail"]["code"] == "origin_rejected"


async def test_waitlist_syncs_server_side_country_and_first_touch_without_leaking_url(
    test_context,
):
    settings = test_context["settings"]
    settings.waitlist_google_sheets_enabled = True
    settings.waitlist_google_sheets_webhook_url = (
        "https://script.google.com/macros/s/test-deployment/exec"
    )
    settings.waitlist_google_sheets_webhook_secret = "sheet-secret"
    settings.waitlist_trust_cloudflare_country_header = True
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload.pop("webhook_secret") == "sheet-secret"
        captured.append(payload)
        return httpx.Response(200, json={"ok": True})

    test_context["app"].dependency_overrides[get_public_forms_sheet_transport] = (
        lambda: httpx.MockTransport(handler)
    )
    client = test_context["client"]
    client.cookies.set(
        "hm_cookie_consent",
        quote(
            json.dumps(
                {
                    "version": settings.cookie_consent_version,
                    "essential": True,
                    "analytics": True,
                }
            )
        ),
    )
    token = await _csrf(client)
    response = await client.post(
        "/api/v1/public-forms/waitlist",
        headers={"X-CSRF-Token": token, "CF-IPCountry": "EG"},
        json=_waitlist_payload(email="country@example.com", key="waitlist:test:country123"),
    )
    assert response.status_code == 200
    assert response.json()["sheet_delivery_status"] == "sent"
    assert captured == [
        {
            "event_id": captured[0]["event_id"],
            "email": "country@example.com",
            "submitted_at": captured[0]["submitted_at"],
            "country": "EG",
            "source_page": "/",
            "beta_contact_consent": True,
            "utm_source": "newsletter",
            "utm_medium": "email",
            "utm_campaign": "private-beta",
            "utm_content": "hero",
            "utm_term": "screening",
            "referrer": "https://example.org/article",
            "landing_page": "https://testserver/",
        }
    ]
    assert "script.google.com" not in response.text
    landing = await client.get("/")
    assert "script.google.com/macros/s/test-deployment" not in landing.text
    test_context["app"].dependency_overrides.pop(get_public_forms_sheet_transport, None)


async def test_contact_sends_exactly_one_office_email_and_is_idempotent(test_context):
    client = test_context["client"]
    settings = test_context["settings"]
    settings.contact_form_sender_email = "office@hilalmarkets.com"
    settings.contact_form_recipient_email = "office@hilalmarkets.com"
    settings.email_test_outbox.clear()
    token = await _csrf(client)
    payload = {
        "title": "Private beta question",
        "email": "visitor@example.com",
        "description": "I would like to understand the private beta access process.",
        "source_page": "/contact",
        "idempotency_key": "contact:test:1234567890",
        "company_website": "",
    }
    first = await client.post(
        "/api/v1/public-forms/contact",
        headers={"X-CSRF-Token": token},
        json=payload,
    )
    assert first.status_code == 200
    assert first.json()["status"] == "sent"
    assert len(settings.email_test_outbox) == 1
    message = settings.email_test_outbox[0]
    assert message["sender"] == "office@hilalmarkets.com"
    assert message["recipient"] == "office@hilalmarkets.com"
    assert message["reply_to"] == "visitor@example.com"
    assert message["purpose"] == "public_contact_office"

    repeated = await client.post(
        "/api/v1/public-forms/contact",
        headers={"X-CSRF-Token": token},
        json=payload,
    )
    assert repeated.status_code == 200
    assert len(settings.email_test_outbox) == 1
    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(func.count()).select_from(ContactSubmission)) == 1
        assert await session.scalar(select(func.count()).select_from(ContactEmailDelivery)) == 1


async def test_public_forms_reject_missing_csrf_honeypot_and_contact_delivery_failure(
    test_context,
):
    client = test_context["client"]
    missing = await client.post(
        "/api/v1/public-forms/waitlist",
        json=_waitlist_payload(email="missing@example.com"),
    )
    assert missing.status_code == 403

    token = await _csrf(client)
    trapped = _waitlist_payload(email="bot@example.com", key="waitlist:test:bot123456")
    trapped["company_website"] = "https://spam.invalid"
    response = await client.post(
        "/api/v1/public-forms/waitlist",
        headers={"X-CSRF-Token": token},
        json=trapped,
    )
    assert response.status_code == 422

    test_context["settings"].email_adapter = "none"
    failed = await client.post(
        "/api/v1/public-forms/contact",
        headers={"X-CSRF-Token": token},
        json={
            "title": "Delivery test",
            "email": "failed@example.com",
            "description": "This message should remain queued after delivery fails.",
            "source_page": "/contact",
            "idempotency_key": "contact:test:failure12345",
            "company_website": "",
        },
    )
    assert failed.status_code == 503
    assert failed.json()["detail"]["code"] == "contact_delivery_unavailable"
    assert "traceback" not in failed.text.casefold()

    test_context["settings"].email_adapter = "memory"
    test_context["settings"].email_test_outbox.clear()
    retried = await client.post(
        "/api/v1/public-forms/contact",
        headers={"X-CSRF-Token": token},
        json={
            "title": "Delivery test",
            "email": "failed@example.com",
            "description": "This message should remain queued after delivery fails.",
            "source_page": "/contact",
            "idempotency_key": "contact:test:failure12345",
            "company_website": "",
        },
    )
    assert retried.status_code == 200
    assert len(test_context["settings"].email_test_outbox) == 1
