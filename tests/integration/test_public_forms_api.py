import json
from datetime import UTC, datetime, timedelta
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
from ai_market_monitor.services.public_forms import PublicFormsService


async def _csrf(client) -> str:
    response = await client.get("/api/v1/public-forms/bootstrap")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _waitlist_payload(
    *,
    email: str = "visitor@example.com",
    key: str = "waitlist:test:1234567890",
) -> dict:
    return {
        "email": email,
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


async def test_the_waitlist_records_nothing_but_the_email_and_its_own_metadata(
    test_context,
):
    """The form asks for an email address, so that is all a signup row may hold.

    A pre-ticked "contact me about beta testing" box was offered here for a short time.
    Ticked-by-default is not a choice the person made, so the question was withdrawn. This
    checks the row itself, not the page: a field the server still accepted would go on
    recording an answer nobody gave, invisibly.
    """

    test_context["settings"].waitlist_google_sheets_enabled = False
    client = test_context["client"]
    token = await _csrf(client)

    joined = await client.post(
        "/api/v1/public-forms/waitlist",
        headers={"X-CSRF-Token": token},
        json=_waitlist_payload(email="yes@example.com", key="waitlist:test:consent-yes"),
    )
    assert joined.status_code == 200

    async with test_context["session_factory"]() as session:
        signup = await session.scalar(select(WaitlistSignup))
    assert signup is not None
    assert signup.normalized_email == "yes@example.com"
    assert not hasattr(signup, "beta_contact_consent")


async def test_a_withdrawn_consent_field_is_refused_rather_than_quietly_ignored(
    test_context,
):
    """A browser still sending the old field must be told, not silently obeyed.

    The request model is strict, so an unknown field is a rejected request. That matters:
    accepting and dropping it would look identical to accepting and storing it, and
    nobody would find out which one was happening.
    """

    client = test_context["client"]
    token = await _csrf(client)

    payload = _waitlist_payload(email="stale@example.com", key="waitlist:test:stale-1")
    payload["beta_contact_consent"] = True

    response = await client.post(
        "/api/v1/public-forms/waitlist",
        headers={"X-CSRF-Token": token},
        json=payload,
    )
    assert response.status_code == 422

    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(WaitlistSignup)) is None


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


async def test_waitlist_sends_the_sheet_exactly_what_the_apps_script_reads(test_context):
    """The request body is the six fields the deployed Apps Script reads, and no others.

    The script authorises on ``secret``. While the server sent ``webhook_secret`` the
    script answered "unauthorized" to every signup and no row was ever written. This
    checks the body on the wire, not the code that builds it, so the two sides cannot
    drift apart again unnoticed.

    The extra values the old body carried are not lost: the time, the page and the
    first-touch attribution are still recorded in Hilal Markets' own database, which is
    checked below. They are simply not sent to a receiver that ignores them.
    """

    settings = test_context["settings"]
    settings.waitlist_google_sheets_enabled = True
    settings.waitlist_google_sheets_webhook_url = (
        "https://script.google.com/macros/s/test-deployment/exec"
    )
    settings.waitlist_google_sheets_webhook_secret = "sheet-secret"
    settings.waitlist_trust_cloudflare_country_header = True
    captured: list[dict] = []
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        captured.append(json.loads(request.content))
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
            "secret": "sheet-secret",
            "email": "country@example.com",
            "name": "",
            "source": "hilalmarkets_waitlist",
            "country": "EG",
            "status": "waitlist",
        }
    ]

    # The secret travels server to server only. It is never in anything a browser reads.
    assert hosts == ["script.google.com"]
    assert "sheet-secret" not in response.text
    assert "script.google.com" not in response.text
    landing = await client.get("/")
    assert "sheet-secret" not in landing.text
    assert "script.google.com/macros/s/test-deployment" not in landing.text
    bootstrap = await client.get("/api/v1/public-forms/bootstrap")
    assert "sheet-secret" not in bootstrap.text

    # Nothing was dropped from this product's own record of the signup.
    async with test_context["session_factory"]() as session:
        signup = await session.scalar(
            select(WaitlistSignup).where(
                WaitlistSignup.normalized_email == "country@example.com"
            )
        )
    assert signup is not None
    assert signup.country_code == "EG"
    assert signup.source_page == "/"
    assert signup.submitted_at is not None
    assert signup.attribution == {
        "utm_source": "newsletter",
        "utm_medium": "email",
        "utm_campaign": "private-beta",
        "utm_content": "hero",
        "utm_term": "screening",
        "referrer": "https://example.org/article",
        "landing_page": "https://testserver/",
    }
    test_context["app"].dependency_overrides.pop(get_public_forms_sheet_transport, None)


async def test_a_country_the_server_does_not_know_is_written_as_the_word_unknown(
    test_context,
):
    settings = test_context["settings"]
    settings.waitlist_google_sheets_enabled = True
    settings.waitlist_google_sheets_webhook_url = (
        "https://script.google.com/macros/s/test-deployment/exec"
    )
    settings.waitlist_google_sheets_webhook_secret = "sheet-secret"
    settings.waitlist_trust_cloudflare_country_header = False
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    test_context["app"].dependency_overrides[get_public_forms_sheet_transport] = (
        lambda: httpx.MockTransport(handler)
    )
    client = test_context["client"]
    token = await _csrf(client)
    response = await client.post(
        "/api/v1/public-forms/waitlist",
        headers={"X-CSRF-Token": token},
        json=_waitlist_payload(email="nowhere@example.com", key="waitlist:test:nocountry1"),
    )
    assert response.status_code == 200
    assert captured == [
        {
            "secret": "sheet-secret",
            "email": "nowhere@example.com",
            "name": "",
            "source": "hilalmarkets_waitlist",
            "country": "unknown",
            "status": "waitlist",
        }
    ]
    test_context["app"].dependency_overrides.pop(get_public_forms_sheet_transport, None)


async def test_a_sheet_failure_keeps_the_signup_and_leaves_the_delivery_retryable(
    test_context,
):
    """A refusal by the sheet is a delivery problem, not a signup problem.

    The person joined the waitlist. That record is this product's, and it stands whatever
    Google answers. The delivery row goes back to "retryable" so the queue tries again,
    and the second try succeeds without the person doing anything.
    """

    settings = test_context["settings"]
    settings.waitlist_google_sheets_enabled = True
    settings.waitlist_google_sheets_webhook_url = (
        "https://script.google.com/macros/s/test-deployment/exec"
    )
    settings.waitlist_google_sheets_webhook_secret = "sheet-secret"
    attempts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(json.loads(request.content))
        if len(attempts) == 1:
            return httpx.Response(200, json={"ok": False, "error": "unauthorized"})
        return httpx.Response(200, json={"ok": True})

    test_context["app"].dependency_overrides[get_public_forms_sheet_transport] = (
        lambda: httpx.MockTransport(handler)
    )
    client = test_context["client"]
    token = await _csrf(client)
    response = await client.post(
        "/api/v1/public-forms/waitlist",
        headers={"X-CSRF-Token": token},
        json=_waitlist_payload(email="retry@example.com", key="waitlist:test:retry12345"),
    )
    assert response.status_code == 200
    assert response.json()["created"] is True
    assert response.json()["sheet_delivery_status"] == "retrying"

    async with test_context["session_factory"]() as session:
        signup = await session.scalar(
            select(WaitlistSignup).where(
                WaitlistSignup.normalized_email == "retry@example.com"
            )
        )
        delivery = await session.scalar(
            select(WaitlistSheetDelivery).where(
                WaitlistSheetDelivery.signup_id == signup.id
            )
        )
    assert signup is not None
    assert signup.status == "active"
    assert delivery is not None
    assert delivery.status == "retryable"
    assert delivery.attempt_count == 1
    assert delivery.next_retry_at is not None

    # The queue retries it later. The second attempt sends the same six fields.
    async with test_context["session_factory"]() as session:
        service = PublicFormsService(
            session,
            settings,
            sheet_transport=httpx.MockTransport(handler),
        )
        delivery = await session.scalar(
            select(WaitlistSheetDelivery).where(
                WaitlistSheetDelivery.signup_id == signup.id
            )
        )
        delivery.next_retry_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
        result = await service.process_waitlist_due(signup_id=signup.id, limit=1)
    assert result["sent"] == 1
    assert attempts[1] == attempts[0]
    assert attempts[0]["secret"] == "sheet-secret"

    async with test_context["session_factory"]() as session:
        delivery = await session.scalar(
            select(WaitlistSheetDelivery).where(
                WaitlistSheetDelivery.signup_id == signup.id
            )
        )
        assert await session.scalar(
            select(func.count())
            .select_from(WaitlistSignup)
            .where(WaitlistSignup.normalized_email == "retry@example.com")
        ) == 1
    assert delivery.status == "sent"
    assert delivery.delivered_at is not None
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
