from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.database import SessionFactory, engine
from ai_market_monitor.db.models import (
    AdminOverride,
    AuditEvent,
    EntitlementSnapshot,
    Subscription,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import (
    IdentityProvider,
    SubscriptionStatus,
    UserRole,
    UserStatus,
)
from ai_market_monitor.services.entitlements import EntitlementService, PlanCatalogService


DEFAULT_EMAILS = ("amroelzene@gmail.com", "uxui.fa@gmail.com")


async def _email_identity(session: AsyncSession, email: str) -> UserIdentity | None:
    normalized = email.strip().lower()
    return await session.scalar(
        select(UserIdentity).where(
            UserIdentity.provider == IdentityProvider.EMAIL,
            UserIdentity.normalized_identifier == normalized,
        )
    )


async def _user_for_email(
    session: AsyncSession,
    email: str,
    *,
    create_missing: bool,
) -> User:
    normalized = email.strip().lower()
    identity = await _email_identity(session, normalized)
    if identity is not None:
        user = await session.get(User, identity.user_id)
        if user is None:
            raise RuntimeError(f"Email identity exists without user: {normalized}")
        return user

    if not create_missing:
        raise RuntimeError(
            f"{normalized} is not registered in this database. "
            "Sign up first, or rerun with --create-missing."
        )

    now = datetime.now(UTC)
    user = User(
        status=UserStatus.ACTIVE,
        role=UserRole.ADMIN,
        display_name=normalized.split("@", 1)[0],
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
            provider_subject=normalized,
            normalized_identifier=normalized,
            display_identifier=normalized,
            password_hash=None,
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


async def _grant_lifetime_admin(
    session: AsyncSession,
    *,
    target: User,
    actor: User,
    email: str,
    reason: str,
) -> dict[str, str]:
    now = datetime.now(UTC)
    lifetime_plan = await PlanCatalogService(session).get_or_sync("lifetime")

    target.status = UserStatus.ACTIVE
    target.role = UserRole.ADMIN
    target.updated_at = now

    provider_subscription_id = f"lifetime:{target.id}"
    subscription = await session.scalar(
        select(Subscription).where(
            Subscription.provider == "admin",
            Subscription.provider_subscription_id == provider_subscription_id,
        )
    )
    if subscription is None:
        subscription = Subscription(
            user_id=target.id,
            plan_id=lifetime_plan.id,
            status=SubscriptionStatus.ACTIVE,
            provider="admin",
            provider_customer_id=None,
            provider_subscription_id=provider_subscription_id,
            current_period_start=now,
            current_period_end=None,
            cancel_at_period_end=False,
            canceled_at=None,
            created_at=now,
            updated_at=now,
        )
        session.add(subscription)
        subscription_action = "created"
    else:
        subscription.user_id = target.id
        subscription.plan_id = lifetime_plan.id
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.current_period_end = None
        subscription.cancel_at_period_end = False
        subscription.canceled_at = None
        subscription.updated_at = now
        subscription_action = "updated"

    override = await session.scalar(
        select(AdminOverride).where(
            AdminOverride.target_user_id == target.id,
            AdminOverride.override_type == "lifetime_admin_grant",
            AdminOverride.revoked_at.is_(None),
        )
    )
    payload = {
        "email": email,
        "plan_code": "lifetime",
        "role": "admin",
        "subscription_id": provider_subscription_id,
    }
    if override is None:
        session.add(
            AdminOverride(
                admin_user_id=actor.id,
                target_user_id=target.id,
                override_type="lifetime_admin_grant",
                reason=reason,
                payload=payload,
                effective_at=now,
                expires_at=None,
                revoked_at=None,
                created_at=now,
            )
        )
    else:
        override.admin_user_id = actor.id
        override.reason = reason
        override.payload = payload
        override.expires_at = None

    session.add(
        AuditEvent(
            actor_user_id=actor.id,
            actor_type="admin",
            action="admin.lifetime_access_granted",
            target_type="user",
            target_id=str(target.id),
            metadata_redacted=payload,
            created_at=now,
        )
    )
    await session.flush()

    await EntitlementService(session).snapshot(target.id)
    snapshot = await session.scalar(
        select(EntitlementSnapshot)
        .where(EntitlementSnapshot.user_id == target.id)
        .order_by(EntitlementSnapshot.created_at.desc())
    )
    return {
        "email": email,
        "user_id": str(target.id),
        "role": target.role.value,
        "subscription": subscription_action,
        "plan": snapshot.plan_code if snapshot else "lifetime",
    }


async def grant(emails: Sequence[str], *, create_missing: bool, actor_email: str | None) -> None:
    async with SessionFactory() as session:
        normalized_emails = [email.strip().lower() for email in emails if email.strip()]
        if not normalized_emails:
            raise RuntimeError("No emails were provided.")

        actor = await _user_for_email(
            session,
            actor_email.strip().lower() if actor_email else normalized_emails[0],
            create_missing=True,
        )
        actor.status = UserStatus.ACTIVE
        actor.role = UserRole.ADMIN
        await session.flush()

        results = []
        for email in normalized_emails:
            target = await _user_for_email(session, email, create_missing=create_missing)
            results.append(
                await _grant_lifetime_admin(
                    session,
                    target=target,
                    actor=actor,
                    email=email,
                    reason="Private beta founder/admin lifetime access.",
                )
            )
        await session.commit()

    await engine.dispose()
    for item in results:
        print(
            f"OK {item['email']} user_id={item['user_id']} "
            f"role={item['role']} plan={item['plan']} subscription={item['subscription']}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grant TraceEdge admin role and lifetime subscription to email users."
    )
    parser.add_argument(
        "emails",
        nargs="*",
        default=list(DEFAULT_EMAILS),
        help="Email addresses to grant. Defaults to the project owner/admin emails.",
    )
    parser.add_argument(
        "--create-missing",
        action="store_true",
        help="Create verified email users when they do not exist in the active database.",
    )
    parser.add_argument(
        "--actor-email",
        default=None,
        help="Email to record as the admin actor. Defaults to the first granted email.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(
        grant(
            args.emails,
            create_missing=args.create_missing,
            actor_email=args.actor_email,
        )
    )
