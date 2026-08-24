from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor import __version__
from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AuditEvent,
    ShariaGovernanceRoleGrant,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, UserRole, UserStatus

OWNER_GOVERNANCE_ROLES = (
    "SYSTEM_ADMIN",
    "RESEARCHER",
    "REVIEWER",
    "PUBLISHER",
)


class GovernanceBootstrapError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


async def grant_owner_governance_roles(
    session: AsyncSession,
    *,
    email: str,
    reason: str,
    application_version: str = __version__,
) -> list[ShariaGovernanceRoleGrant]:
    """Idempotently grant governance authority to an existing verified admin."""

    normalized_email = email.strip().casefold()
    if not normalized_email or "@" not in normalized_email:
        raise GovernanceBootstrapError("valid_email_required", "A valid owner email is required.")
    normalized_reason = reason.strip()
    if len(normalized_reason) < 10:
        raise GovernanceBootstrapError(
            "bootstrap_reason_required",
            "Record a specific reason of at least 10 characters for this authority grant.",
        )

    identity = await session.scalar(
        select(UserIdentity).where(
            UserIdentity.provider == IdentityProvider.EMAIL,
            UserIdentity.normalized_identifier == normalized_email,
        )
    )
    if identity is None or not identity.is_verified:
        raise GovernanceBootstrapError(
            "verified_owner_required",
            "The owner must already have a verified email identity.",
        )
    owner = await session.get(User, identity.user_id)
    if (
        owner is None
        or owner.status != UserStatus.ACTIVE
        or owner.role != UserRole.ADMIN
    ):
        raise GovernanceBootstrapError(
            "active_admin_required",
            "The owner must already be an active administrator.",
        )

    now = datetime.now(UTC)
    existing = {
        grant.role: grant
        for grant in (
            await session.scalars(
                select(ShariaGovernanceRoleGrant).where(
                    ShariaGovernanceRoleGrant.user_id == owner.id,
                    ShariaGovernanceRoleGrant.role.in_(OWNER_GOVERNANCE_ROLES),
                )
            )
        ).all()
    }
    grants: list[ShariaGovernanceRoleGrant] = []
    for role in OWNER_GOVERNANCE_ROLES:
        grant = existing.get(role)
        previous_state = "missing"
        if grant is None:
            grant = ShariaGovernanceRoleGrant(
                user_id=owner.id,
                role=role,
                granted_by_user_id=owner.id,
                effective_at=now,
                revoked_at=None,
                created_at=now,
            )
            session.add(grant)
        elif grant.revoked_at is not None:
            previous_state = "revoked"
            grant.granted_by_user_id = owner.id
            grant.effective_at = now
            grant.revoked_at = None
        else:
            previous_state = "active"
        grants.append(grant)

        if previous_state == "active":
            continue
        session.add(
            AuditEvent(
                actor_user_id=owner.id,
                actor_type="admin",
                action="sharia.governance_role_bootstrapped",
                target_type="sharia_governance_role_grant",
                target_id=str(grant.id),
                metadata_redacted={
                    "actor_role": UserRole.ADMIN.value,
                    "governance_role": role,
                    "reason": normalized_reason,
                    "previous_state": previous_state,
                    "new_state": "active",
                    "source_ids": {"owner_user_id": str(owner.id)},
                    "application_version": application_version,
                    "occurred_at": now.isoformat(),
                },
                created_at=now,
            )
        )

    await session.flush()
    return grants


async def ensure_configured_owner_grants(
    session: AsyncSession,
    *,
    settings: Settings,
    email: str,
    reason: str,
) -> tuple[ShariaGovernanceRoleGrant, ...]:
    """Reconcile one configured System Brain owner's governance grants with the settings.

    ``SYSTEM_BRAIN_ADMIN_EMAILS`` already decides who is an owner: an address listed there
    is made an ``ADMIN`` on sign-up and let into System Brain. The grants that carry the
    authority to *act* there were written in one place only — while an account was being
    created — so three ordinary situations produced an owner with none:

    * the account already existed when the address was added to the setting;
    * the account was made an administrator afterwards, by
      ``scripts/grant_lifetime_admin.py``, which never wrote a grant;
    * the account predates the sign-up grant itself.

    In staging and production such an owner is refused every governance action with
    ``governance_grant_required``, and the only cure was a shell on the server. This is the
    one function that says "a configured owner holds the owner grants", so every door that
    admits one reconciles the same way.

    It never elevates anybody: an address the settings do not list, an unverified identity,
    a suspended account or a non-administrator all return empty. Granting stays in
    :func:`grant_owner_governance_roles`, which audits each newly activated role.

    It also never *restores* authority. It fills the empty case only — an owner holding no
    owner-role record at all. Once any record exists, active or revoked, a person decided
    it, and :mod:`scripts.bootstrap_governance_owner` is the audited way to change that
    decision. Reviving a revoked grant on the next sign-in would make revoking one
    meaningless.
    """

    normalized = email.strip().casefold()
    if normalized not in settings.system_brain_authorized_emails:
        return ()
    identity = await session.scalar(
        select(UserIdentity).where(
            UserIdentity.provider == IdentityProvider.EMAIL,
            UserIdentity.normalized_identifier == normalized,
        )
    )
    if identity is None:
        return ()
    already_decided = await session.scalar(
        select(ShariaGovernanceRoleGrant.id)
        .where(
            ShariaGovernanceRoleGrant.user_id == identity.user_id,
            ShariaGovernanceRoleGrant.role.in_(OWNER_GOVERNANCE_ROLES),
        )
        .limit(1)
    )
    if already_decided is not None:
        return ()
    try:
        grants = await grant_owner_governance_roles(session, email=normalized, reason=reason)
    except GovernanceBootstrapError:
        # The address is configured but the account is not yet a verified, active
        # administrator. Nothing to reconcile, and this must never become the failure of
        # whatever the caller was really doing — signing in, for one.
        return ()
    return tuple(grants)
