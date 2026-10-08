"""A visitor without an account can report a problem on a Passport, as a member can.

What is asserted:

* a visitor's report is recorded exactly like a member's — one report row, one review
  case for a reviewer — with the visitor's email address standing in for the account;
* the visitor's door is guarded like the Contact form: the public forms' token, the
  hidden trap field, a real email address, and the per-email allowance;
* a report about a coin that does not exist, or sent before launch, is refused;
* a reviewer sees who reported it and where to write back, for a visitor and a member;
* the database refuses a report with neither an account nor an email address.
"""

from __future__ import annotations

import re
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ai_market_monitor.db.models import (
    AuditEvent,
    CanonicalAsset,
    ShariaPassportProblemReport,
)
from ai_market_monitor.services.sharia_admin_dashboard import ShariaAdminDashboardService
from tests.integration.test_dashboard_web import _signup_and_verify
from tests.integration.test_public_passports import _seed


async def _asset_id(context) -> str:
    async with context["session_factory"]() as session:
        coin = await session.scalar(select(CanonicalAsset).where(CanonicalAsset.symbol == "BTC"))
        return str(coin.id)


async def _token(client) -> str:
    response = await client.get("/api/v1/public-forms/bootstrap")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _report(email: str = "Visitor@Example.com", **extra) -> dict:
    return {
        "report_type": "broken_source",
        "details": "The second source link answers with a not-found page.",
        "email": email,
        "company_website": "",
        **extra,
    }


async def _send(context, body: dict, *, token: str | None = None, asset_id: str | None = None):
    client = context["client"]
    asset_id = asset_id or await _asset_id(context)
    headers = {"X-CSRF-Token": token if token is not None else await _token(client)}
    return await client.post(
        f"/api/v1/public-forms/passports/{asset_id}/problem-reports", headers=headers, json=body
    )


async def test_a_visitor_report_becomes_a_review_case_with_their_email(test_context):
    await _seed(test_context, count=1)
    response = await _send(test_context, _report())
    assert response.status_code == 201, response.text
    assert "recorded for review" in response.json()["message"]

    async with test_context["session_factory"]() as session:
        (row,) = (await session.scalars(select(ShariaPassportProblemReport))).all()
        assert row.reporter_user_id is None
        assert row.reporter_email == "visitor@example.com"
        assert row.review_case_id is not None
        audit = await session.scalar(
            select(AuditEvent).where(AuditEvent.action == "sharia.passport_problem_reported")
        )
        assert audit.actor_type == "visitor" and audit.actor_user_id is None
        detail = await ShariaAdminDashboardService(session).case_detail(row.review_case_id)
    assert detail["reporter"] == {
        "who": "A visitor without an account",
        "email": "visitor@example.com",
    }


async def test_a_member_report_shows_the_reviewer_their_account_address(test_context):
    await _signup_and_verify(test_context, email="passport-member@example.com")
    await _seed(test_context, count=1)
    client = test_context["client"]
    page = await client.get("/passports/btc")
    token = re.search(r'data-csrf-token="([0-9a-f]{64})"', page.text).group(1)
    response = await client.post(
        f"/api/v1/sharia/passports/{await _asset_id(test_context)}/problem-reports",
        headers={"X-CSRF-Token": token},
        json={"report_type": "other", "details": "A member's report about this Passport."},
    )
    assert response.status_code == 201, response.text
    async with test_context["session_factory"]() as session:
        (row,) = (await session.scalars(select(ShariaPassportProblemReport))).all()
        assert row.reporter_user_id is not None and row.reporter_email is None
        detail = await ShariaAdminDashboardService(session).case_detail(row.review_case_id)
    assert detail["reporter"] == {"who": "A member", "email": "passport-member@example.com"}


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"token": ""}, 403),
        ({"body": {"company_website": "https://spam.invalid"}}, 422),
        ({"body": {"email": "not-an-address"}}, 422),
        ({"body": {"details": "too short"}}, 422),
        ({"asset_id": "00000000-0000-0000-0000-000000000001"}, 404),
    ],
    ids=["no-token", "trap-field", "bad-email", "short-details", "unknown-coin"],
)
async def test_the_visitor_door_refuses_what_the_contact_form_refuses(
    test_context, change, status
):
    await _seed(test_context, count=1)
    response = await _send(
        test_context,
        _report(**change.get("body", {})),
        token=change.get("token"),
        asset_id=change.get("asset_id"),
    )
    assert response.status_code == status, response.text
    async with test_context["session_factory"]() as session:
        assert (await session.scalars(select(ShariaPassportProblemReport))).all() == []


async def test_one_address_may_send_only_its_allowance(test_context):
    await _seed(test_context, count=1)
    allowance = test_context["settings"].support_intake_max_per_email
    for _ in range(allowance):
        assert (await _send(test_context, _report("quota@example.com"))).status_code == 201
    refused = await _send(test_context, _report("quota@example.com"))
    assert refused.status_code == 429
    assert "retry-after" in refused.headers
    async with test_context["session_factory"]() as session:
        rows = (await session.scalars(select(ShariaPassportProblemReport))).all()
    assert len(rows) == allowance


async def test_before_launch_the_visitor_door_is_closed(waitlist_context):
    await _seed(waitlist_context, count=1)
    response = await _send(waitlist_context, _report())
    assert response.status_code == 404


async def test_the_database_refuses_a_report_with_no_one_to_answer(test_context):
    await _seed(test_context, count=1)
    asset_id = await _asset_id(test_context)
    async with test_context["session_factory"]() as session:
        session.add(
            ShariaPassportProblemReport(
                canonical_asset_id=UUID(asset_id),
                report_type="other",
                details="Nobody to write back to.",
                state="open",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
