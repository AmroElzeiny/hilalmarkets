from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import (
    AuditEvent,
    Plan,
    ReferralCode,
    ReferralRelationship,
    Subscription,
)
from ai_market_monitor.db.models.enums import SubscriptionStatus


class ReferralError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ReferralService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record_trial_referral(
        self, *, referred_user_id: UUID, referral_code: str
    ) -> ReferralRelationship | None:
        code = await self.session.scalar(
            select(ReferralCode).where(ReferralCode.code == referral_code, ReferralCode.is_active)
        )
        if code is None:
            return None
        if code.owner_user_id is None:
            return None
        if code.owner_user_id == referred_user_id:
            raise ReferralError("self_referral", "Users cannot refer themselves.")
        existing = await self.session.scalar(
            select(ReferralRelationship).where(
                ReferralRelationship.referred_user_id == referred_user_id
            )
        )
        if existing is not None:
            return existing
        if code.expires_at and code.expires_at <= datetime.now(UTC):
            return None
        if code.max_uses is not None and code.use_count >= code.max_uses:
            return None
        relationship = ReferralRelationship(
            referrer_user_id=code.owner_user_id,
            referred_user_id=referred_user_id,
            referral_code_id=code.id,
            status="trial_activated",
            reward_status="pending_paid_conversion",
            metadata_json={"code": referral_code},
        )
        code.use_count += 1
        self.session.add(relationship)
        self._audit(
            code.owner_user_id,
            "referral.trial_recorded",
            "referral_relationship",
            None,
            {"referred_user_id": str(referred_user_id), "code": referral_code},
        )
        await self.session.flush()
        return relationship

    async def grant_conversion_rewards(
        self, *, referred_user_id: UUID
    ) -> ReferralRelationship | None:
        relationship = await self.session.scalar(
            select(ReferralRelationship).where(
                ReferralRelationship.referred_user_id == referred_user_id
            )
        )
        if relationship is None or relationship.reward_status == "granted":
            return relationship
        paid = (
            await self.session.execute(
                select(Subscription.id, Plan.price_monthly, Plan.code)
                .join(Plan, Plan.id == Subscription.plan_id)
                .where(
                    Subscription.user_id == referred_user_id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                )
            )
        ).first()
        if paid is None:
            return relationship
        subscription_id, price_monthly, plan_code = paid
        relationship.status = "paid_converted"
        relationship.reward_status = "eligible_after_first_paid_month"
        relationship.metadata_json = {
            **relationship.metadata_json,
            "paid_subscription_id": str(subscription_id),
            # What the customer actually paid, written down at the moment it became true.
            # An affiliate's commission is a share of this, and nothing else: without it
            # the share would have to be taken of an assumed plan price, which is how a
            # balance comes to show money that was never received. The plan's price can
            # change tomorrow; this row is what happened today.
            "paid_amount_usd": str(price_monthly),
            "paid_plan_code": plan_code,
        }
        self._audit(
            relationship.referrer_user_id,
            "referral.reward_eligible",
            "referral_relationship",
            relationship.id,
            {"referred_user_id": str(referred_user_id)},
        )
        await self.session.flush()
        return relationship

    async def mark_reward_granted(
        self, *, relationship_id: UUID, admin_user_id: UUID, reward_days: int
    ) -> ReferralRelationship:
        relationship = await self.session.get(ReferralRelationship, relationship_id)
        if relationship is None:
            raise ReferralError("referral_missing", "Referral relationship not found.")
        if relationship.reward_status == "granted":
            return relationship
        relationship.reward_status = "granted"
        relationship.reward_granted_at = datetime.now(UTC)
        relationship.metadata_json = {**relationship.metadata_json, "reward_days": reward_days}
        self.session.add(
            AuditEvent(
                actor_user_id=admin_user_id,
                actor_type="admin",
                action="referral.reward_granted",
                target_type="referral_relationship",
                target_id=str(relationship.id),
                metadata_redacted={"reward_days": reward_days},
                created_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        return relationship

    def _audit(
        self,
        actor_user_id: UUID,
        action: str,
        target_type: str,
        target_id: UUID | None,
        metadata: dict,
    ) -> None:
        self.session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                actor_type="user",
                action=action,
                target_type=target_type,
                target_id=str(target_id) if target_id else None,
                metadata_redacted=metadata,
                created_at=datetime.now(UTC),
            )
        )
