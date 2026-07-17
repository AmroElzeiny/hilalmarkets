from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    AuditEvent,
    ShariaGovernanceRoleGrant,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, UserRole, UserStatus
from ai_market_monitor.services.governance_bootstrap import (
    OWNER_GOVERNANCE_ROLES,
    GovernanceBootstrapError,
    grant_owner_governance_roles,
)


async def _owner(session, *, role: UserRole = UserRole.ADMIN) -> User:
    now = datetime.now(UTC)
    user = User(
        status=UserStatus.ACTIVE,
        role=role,
        display_name="Governance owner",
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    await session.flush()
    session.add(
        UserIdentity(
            user_id=user.id,
            provider=IdentityProvider.EMAIL,
            provider_subject="owner@example.com",
            normalized_identifier="owner@example.com",
            display_identifier="owner@example.com",
            is_verified=True,
            is_primary=True,
            verified_at=now,
            profile_data={},
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()
    return user


async def test_owner_governance_bootstrap_is_explicit_audited_and_idempotent(test_context):
    async with test_context["session_factory"]() as session:
        owner = await _owner(session)

        first = await grant_owner_governance_roles(
            session,
            email="OWNER@example.com",
            reason="Initial closed-beta governance owner provisioning.",
            application_version="test-version",
        )
        second = await grant_owner_governance_roles(
            session,
            email="owner@example.com",
            reason="Initial closed-beta governance owner provisioning.",
            application_version="test-version",
        )

        assert {grant.role for grant in first} == set(OWNER_GOVERNANCE_ROLES)
        assert {grant.id for grant in second} == {grant.id for grant in first}
        assert await session.scalar(
            select(func.count(ShariaGovernanceRoleGrant.id)).where(
                ShariaGovernanceRoleGrant.user_id == owner.id
            )
        ) == len(OWNER_GOVERNANCE_ROLES)
        events = list(
            (
                await session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.action == "sharia.governance_role_bootstrapped"
                    )
                )
            ).all()
        )
        assert len(events) == len(OWNER_GOVERNANCE_ROLES)
        assert {event.metadata_redacted["governance_role"] for event in events} == set(
            OWNER_GOVERNANCE_ROLES
        )
        assert all(
            event.metadata_redacted["application_version"] == "test-version"
            for event in events
        )


async def test_owner_governance_bootstrap_never_elevates_a_normal_user(test_context):
    async with test_context["session_factory"]() as session:
        user = await _owner(session, role=UserRole.USER)

        with pytest.raises(GovernanceBootstrapError) as exc:
            await grant_owner_governance_roles(
                session,
                email="owner@example.com",
                reason="Attempted bootstrap without administrator authority.",
            )

        assert exc.value.code == "active_admin_required"
        assert user.role == UserRole.USER
        assert await session.scalar(select(func.count(ShariaGovernanceRoleGrant.id))) == 0
