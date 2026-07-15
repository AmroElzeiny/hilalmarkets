from pydantic import SecretStr
from sqlalchemy import select

from ai_market_monitor.core.security import hash_password
from ai_market_monitor.db.models import CapabilityAliasProposal, User, UserIdentity
from ai_market_monitor.db.models.enums import IdentityProvider


def _configure(test_context) -> None:
    settings = test_context["settings"]
    settings.system_brain_admin_username = "contact@trace-edge.com"
    settings.system_brain_admin_password_hash = SecretStr(hash_password("Admin-Test-Password!"))
    settings.auth_test_fixed_code = "123456"


async def _login(test_context):
    response = await test_context["client"].post(
        "/system-brain/login",
        data={
            "username": "contact@trace-edge.com",
            "password": "Admin-Test-Password!",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/system-brain/verify"
    verified = await test_context["client"].post(
        "/system-brain/verify",
        data={"code": "123456"},
        follow_redirects=False,
    )
    assert verified.status_code == 303


async def test_system_brain_web_requires_otp_and_renders_console(test_context):
    _configure(test_context)
    login = await test_context["client"].get("/system-brain")
    assert login.status_code == 200
    assert "Continue securely" in login.text
    assert login.headers["cache-control"] == "no-store, max-age=0"

    await _login(test_context)
    dashboard = await test_context["client"].get("/system-brain")
    assert dashboard.status_code == 200
    for heading in (
        "Most common unmatched fragments",
        "Supported prompts with low confidence",
        "Clarifications users choose",
        "False candidate rankings",
        "Provider-blocked requests",
        "Capabilities with poor alias coverage",
        "Proposed aliases awaiting approval",
        "Status per capability",
        "Registered users",
        "Tokens and estimated cost",
        "Agent safety and rollout",
        "Application-managed tools",
    ):
        assert heading in dashboard.text
    stylesheet = (await test_context["client"].get("/static/system-brain.css")).text
    assert "#082f29" in stylesheet
    assert "#0a0a0a" not in stylesheet


async def test_system_brain_can_require_cloudflare_access_before_app_auth(test_context):
    _configure(test_context)
    settings = test_context["settings"]
    settings.system_brain_cloudflare_access_required = True

    missing = await test_context["client"].get("/system-brain")
    assert missing.status_code == 403

    wrong_identity = await test_context["client"].get(
        "/system-brain",
        headers={
            "cf-access-authenticated-user-email": "other@example.com",
            "cf-access-jwt-assertion": "test-assertion",
        },
    )
    assert wrong_identity.status_code == 403

    outer_gate_passed = await test_context["client"].get(
        "/system-brain",
        headers={
            "cf-access-authenticated-user-email": "contact@trace-edge.com",
            "cf-access-jwt-assertion": "test-assertion",
        },
    )
    assert outer_gate_passed.status_code == 200
    assert "Continue securely" in outer_gate_passed.text


async def test_system_brain_excludes_named_accounts_and_reviews_alias(test_context):
    _configure(test_context)
    async with test_context["session_factory"]() as session:
        for email in ("amroelzene@gmail.com", "visible@example.com"):
            user = User(display_name="Hidden" if email.startswith("amro") else "Visible Person")
            session.add(user)
            await session.flush()
            session.add(
                UserIdentity(
                    user_id=user.id,
                    provider=IdentityProvider.EMAIL,
                    provider_subject=email,
                    normalized_identifier=email,
                    display_identifier=email,
                    is_verified=True,
                )
            )
        proposal = CapabilityAliasProposal(
            alias="daily floor sweep",
            normalized_alias="daily floor sweep",
            capability_key="previous_daily_low_sweep",
            status="pending",
            evidence_count=2,
        )
        session.add(proposal)
        await session.commit()
        proposal_id = proposal.id

    await _login(test_context)
    dashboard = await test_context["client"].get("/system-brain")
    assert "visible@example.com" in dashboard.text
    assert "amroelzene@gmail.com" not in dashboard.text
    csrf = dashboard.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    reviewed = await test_context["client"].post(
        f"/system-brain/aliases/{proposal_id}/approve",
        data={"csrf_token": csrf, "review_note": "Verified against user choice evidence"},
        follow_redirects=False,
    )
    assert reviewed.status_code == 303
    async with test_context["session_factory"]() as session:
        stored = await session.scalar(
            select(CapabilityAliasProposal).where(CapabilityAliasProposal.id == proposal_id)
        )
        assert stored.status == "approved"
        assert stored.review_note == "Verified against user choice evidence"
