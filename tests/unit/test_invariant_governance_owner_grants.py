"""A configured System Brain owner always holds the authority the console needs.

``SYSTEM_BRAIN_ADMIN_EMAILS`` decides who owns governance: an address listed there is made
an application ``ADMIN`` and let into System Brain. The grants that carry the authority to
*act* there were written in one place only — while an account was being created — so three
ordinary situations produced an owner with none:

* the account already existed when the address joined the setting;
* the account was made an administrator afterwards by ``scripts/grant_lifetime_admin.py``,
  which wrote no grant at all;
* the account predates the sign-up grant.

In staging and production every governance action was then refused with
``governance_grant_required`` — "You have no review permission in this environment." — and
the only cure was a shell on the server.

These tests hold the rule, not the one account that met it. Every door into the dashboard
reconciles, every door that must refuse still refuses, and no new door can appear without
failing :func:`test_only_web_auth_mints_a_dashboard_session`.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from ai_market_monitor.core.security import hash_password, token_digest
from ai_market_monitor.db.models import (
    AuditEvent,
    EmailAuthChallenge,
    ShariaGovernanceRoleGrant,
    TelegramDashboardLink,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, UserRole, UserStatus
from ai_market_monitor.services.dashboard_links import (
    DashboardLinkService,
    DashboardLinkTokenService,
)
from ai_market_monitor.services.governance_bootstrap import (
    OWNER_GOVERNANCE_ROLES,
    ensure_configured_owner_grants,
)
from ai_market_monitor.services.web_auth import WebAuthService

OWNER_EMAIL = "owner@hilalmarkets.com"
OWNER_PASSWORD = "Governance-Owner-Passw0rd!"

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"


def _configure_owner(settings, *, emails: str = OWNER_EMAIL) -> None:
    settings.system_brain_admin_emails = emails


async def _account(
    session,
    *,
    email: str = OWNER_EMAIL,
    role: UserRole = UserRole.ADMIN,
    status: UserStatus = UserStatus.ACTIVE,
    verified: bool = True,
) -> User:
    """An account that already exists — the case the sign-up grant could never reach."""

    now = datetime.now(UTC)
    user = User(
        status=status,
        role=role,
        display_name="Owner",
        timezone="UTC",
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    await session.flush()
    session.add(
        UserIdentity(
            user_id=user.id,
            provider=IdentityProvider.EMAIL,
            provider_subject=email,
            normalized_identifier=email,
            display_identifier=email,
            password_hash=hash_password(OWNER_PASSWORD),
            is_verified=verified,
            is_primary=True,
            verified_at=now if verified else None,
            profile_data={},
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()
    return user


async def _active_roles(session, user_id) -> set[str]:
    rows = (
        await session.scalars(
            select(ShariaGovernanceRoleGrant.role).where(
                ShariaGovernanceRoleGrant.user_id == user_id,
                ShariaGovernanceRoleGrant.revoked_at.is_(None),
            )
        )
    ).all()
    return set(rows)


# --- every door in ------------------------------------------------------------------


async def _door_password_signin(service: WebAuthService, user: User) -> None:
    signed_in = await service.signin_email(email=OWNER_EMAIL, password=OWNER_PASSWORD)
    await service.create_session(signed_in)


async def _door_emailed_code(service: WebAuthService, user: User) -> None:
    now = datetime.now(UTC)
    code = "123456"
    secret = service.settings.app_secret_key.get_secret_value().encode("utf-8")
    service.session.add(
        EmailAuthChallenge(
            user_id=user.id,
            email=OWNER_EMAIL,
            purpose="login",
            code_digest=hmac.new(
                secret,
                f"{OWNER_EMAIL}:login:{code}".encode(),
                hashlib.sha256,
            ).hexdigest(),
            created_at=now,
            expires_at=now + timedelta(minutes=10),
            attempts=0,
            max_attempts=5,
        )
    )
    await service.session.flush()
    signed_in = await service.signin_with_email_code(email=OWNER_EMAIL, code=code)
    await service.create_session(signed_in)


async def _door_password_signup_path(service: WebAuthService, user: User) -> None:
    """The sign-up entry point, met by an account that already exists."""

    signed_in, created = await service.signup_or_signin_email(
        email=OWNER_EMAIL,
        password=OWNER_PASSWORD,
        display_name="Owner",
    )
    assert created is False
    await service.create_session(signed_in)


async def _door_one_click_link(service: WebAuthService, user: User) -> None:
    now = datetime.now(UTC)
    raw = "dashboard-link-token-for-the-owner"
    link = TelegramDashboardLink(
        user_id=user.id,
        telegram_user_id="1234567890",
        token_digest=token_digest(raw),
        target_path="/dashboard",
        created_at=now,
        expires_at=now + timedelta(minutes=15),
    )
    service.session.add(link)
    await service.session.flush()
    signed = DashboardLinkTokenService(service.settings).issue(link.id, raw)
    await DashboardLinkService(service.session, service.settings).consume(signed)


DOORS = {
    "password_sign_in": _door_password_signin,
    "emailed_code": _door_emailed_code,
    "sign_up_entry_point": _door_password_signup_path,
    "one_click_dashboard_link": _door_one_click_link,
}


@pytest.mark.parametrize("door_name", sorted(DOORS))
async def test_every_sign_in_door_gives_the_configured_owner_full_authority(
    test_context, door_name
):
    settings = test_context["settings"]
    _configure_owner(settings)
    async with test_context["session_factory"]() as session:
        user = await _account(session)
        assert await _active_roles(session, user.id) == set()

        await DOORS[door_name](WebAuthService(session, settings), user)

        assert await _active_roles(session, user.id) == set(OWNER_GOVERNANCE_ROLES)


@pytest.mark.parametrize("role", sorted(OWNER_GOVERNANCE_ROLES))
async def test_each_owner_role_is_granted_and_audited_once(test_context, role):
    settings = test_context["settings"]
    _configure_owner(settings)
    async with test_context["session_factory"]() as session:
        user = await _account(session)
        service = WebAuthService(session, settings)

        await service.create_session(user)
        await service.create_session(user)

        assert await session.scalar(
            select(func.count(ShariaGovernanceRoleGrant.id)).where(
                ShariaGovernanceRoleGrant.user_id == user.id,
                ShariaGovernanceRoleGrant.role == role,
            )
        ) == 1
        events = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "sharia.governance_role_bootstrapped"
                )
            )
        ).all()
        assert [
            event for event in events if event.metadata_redacted["governance_role"] == role
        ] != []
        assert len(events) == len(OWNER_GOVERNANCE_ROLES)


# --- who must still be refused --------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "kwargs", "configured"),
    [
        ("not_a_configured_owner", {}, "somebody-else@hilalmarkets.com"),
        ("no_owner_configured_at_all", {}, ""),
        ("not_an_administrator", {"role": UserRole.USER}, OWNER_EMAIL),
        ("email_not_verified", {"verified": False}, OWNER_EMAIL),
        ("account_suspended", {"status": UserStatus.SUSPENDED}, OWNER_EMAIL),
    ],
)
async def test_sign_in_never_grants_authority_to_anybody_else(
    test_context, case, kwargs, configured
):
    settings = test_context["settings"]
    _configure_owner(settings, emails=configured)
    async with test_context["session_factory"]() as session:
        user = await _account(session, **kwargs)

        await WebAuthService(session, settings).create_session(user)

        assert await _active_roles(session, user.id) == set(), case
        assert await session.scalar(select(func.count(ShariaGovernanceRoleGrant.id))) == 0
        assert user.role == kwargs.get("role", UserRole.ADMIN)


@pytest.mark.parametrize("revoked_role", sorted(OWNER_GOVERNANCE_ROLES))
async def test_sign_in_never_revives_a_revoked_grant(test_context, revoked_role):
    """Taking authority away has to stick, or revoking it means nothing."""

    settings = test_context["settings"]
    _configure_owner(settings)
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        user = await _account(session)
        for role in OWNER_GOVERNANCE_ROLES:
            session.add(
                ShariaGovernanceRoleGrant(
                    user_id=user.id,
                    role=role,
                    granted_by_user_id=user.id,
                    effective_at=now,
                    revoked_at=now if role == revoked_role else None,
                    created_at=now,
                )
            )
        await session.flush()

        await WebAuthService(session, settings).create_session(user)

        assert await _active_roles(session, user.id) == set(OWNER_GOVERNANCE_ROLES) - {
            revoked_role
        }


async def test_reconciler_alone_decides_who_is_an_owner(test_context):
    """The membership test lives in one function, so no caller can widen it."""

    settings = test_context["settings"]
    _configure_owner(settings)
    async with test_context["session_factory"]() as session:
        stranger = await _account(session, email="stranger@hilalmarkets.com")

        granted = await ensure_configured_owner_grants(
            session,
            settings=settings,
            email="stranger@hilalmarkets.com",
            reason="A caller asking for an address the settings do not name.",
        )

        assert granted == ()
        assert await _active_roles(session, stranger.id) == set()


# --- no fifth door can appear ---------------------------------------------------------


def test_only_web_auth_mints_a_dashboard_session():
    """Every way in reconciles because every way in goes through ``create_session``.

    A module that built a ``WebSession`` row of its own would be a door that hands out a
    signed-in browser without passing the reconciler — the exact shape of the hole this
    fixes, one layer up.
    """

    offenders = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path.name == "web_auth.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped.startswith("class ") and "WebSession(" in stripped:
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}:{number}")
    assert offenders == []
