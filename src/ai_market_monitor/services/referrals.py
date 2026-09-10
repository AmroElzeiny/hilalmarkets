"""The older name for two things the attribution service now owns.

This module used to decide who a referral belonged to and what a conversion was worth. It
does neither now: both live in
:mod:`ai_market_monitor.services.affiliate_attribution`, and everything here calls into it.

That is deliberate rather than tidy-up. Two modules that each work out who a customer
belongs to are two answers to one question, and this codebase has already paid for that
several times over. The methods stay because other code and other tests call them by
name, and because an admin action — granting a reward by hand — genuinely belongs here.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import (
    AuditEvent,
    ReferralCode,
    ReferralRelationship,
)
from ai_market_monitor.services.affiliate_attribution import (
    ReferralAttributionService,
    read_referral_code,
)


class ReferralError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ReferralService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.attribution = ReferralAttributionService(session)

    async def record_trial_referral(
        self, *, referred_user_id: UUID, referral_code: str
    ) -> ReferralRelationship | None:
        """Assign somebody to the affiliate whose code they typed.

        A thin front door onto
        :meth:`~ai_market_monitor.services.affiliate_attribution.ReferralAttributionService.assign`.
        The one thing it adds is the loud refusal: somebody using their own code is a
        mistake worth naming, where every other "nobody was credited" case is ordinary
        and answered with ``None``.
        """

        code = read_referral_code(referral_code)
        if code is not None:
            owner = await self.attribution.code_owned_by(code)
            if owner is not None and owner.owner_user_id == referred_user_id:
                raise ReferralError("self_referral", "Users cannot refer themselves.")

        before = await self.attribution.assignment_for(referred_user_id)
        relationship = await self.attribution.assign(
            user_id=referred_user_id,
            typed_code=referral_code,
        )
        if relationship is not None and before is None:
            self._audit(
                relationship.referrer_user_id,
                "referral.trial_recorded",
                "referral_relationship",
                None,
                {
                    "referred_user_id": str(referred_user_id),
                    "code": (relationship.metadata_json or {}).get("code", ""),
                },
            )
            await self.session.flush()
        return relationship

    async def grant_conversion_rewards(
        self, *, referred_user_id: UUID
    ) -> ReferralRelationship | None:
        """Record what this customer's active subscription earns their affiliate.

        The money itself is written by the attribution service, into the commission
        ledger, at the rate that matches whether this is the customer's first payment or
        a later one. Nothing is worked out here — a second place deciding what a payment
        is worth is exactly what this module stopped being.
        """

        relationship = await self.attribution.assignment_for(referred_user_id)
        if relationship is None:
            return None
        commission = await self.attribution.record_payment_for_active_subscription(
            customer_user_id=referred_user_id,
            # Keyed on the customer's first conversion, so calling this twice for the
            # same person records one earning rather than two.
            event_key=f"conversion:{referred_user_id}",
        )
        if commission is not None:
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

    async def code_for(self, code: str) -> ReferralCode | None:
        """The live code row, through the one reader. Kept for callers that ask."""

        reading = read_referral_code(code)
        if reading is None:
            return None
        return await self.session.scalar(
            select(ReferralCode).where(ReferralCode.code == reading)
        )

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
