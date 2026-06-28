from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import AdminOverride, AuditEvent, Subscription
from ai_market_monitor.db.models.enums import SubscriptionStatus
from ai_market_monitor.services.entitlements import PlanCatalogService
from ai_market_monitor.services.trials import TrialLifecycleService


class AdminError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class AdminCommercialService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def grant_trial_extension(
        self,
        *,
        admin_user_id: UUID,
        target_user_id: UUID,
        days: int,
        reason: str,
        trial_service: TrialLifecycleService,
    ):
        trial = await trial_service.extend(
            target_user_id,
            days=days,
            admin_user_id=admin_user_id,
            reason=reason,
        )
        await self.record_override(
            admin_user_id=admin_user_id,
            target_user_id=target_user_id,
            override_type="trial_extension",
            reason=reason,
            payload={"days": days, "trial_id": str(trial.id)},
            expires_at=trial.ends_at,
        )
        return trial

    async def grant_complimentary_subscription(
        self,
        *,
        admin_user_id: UUID,
        target_user_id: UUID,
        plan_code: str,
        days: int,
        reason: str,
    ) -> Subscription:
        if days <= 0:
            raise AdminError(
                "invalid_duration",
                "Complimentary subscription days must be positive.",
            )
        plan = await PlanCatalogService(self.session).get_or_sync(plan_code)
        now = datetime.now(UTC)
        subscription = Subscription(
            user_id=target_user_id,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            provider="admin",
            provider_customer_id=None,
            provider_subscription_id=f"comp_{target_user_id}_{now.timestamp()}",
            current_period_start=now,
            current_period_end=now + timedelta(days=days),
        )
        self.session.add(subscription)
        await self.session.flush()
        await self.record_override(
            admin_user_id=admin_user_id,
            target_user_id=target_user_id,
            override_type="complimentary_subscription",
            reason=reason,
            payload={"plan_code": plan_code, "days": days, "subscription_id": str(subscription.id)},
            expires_at=subscription.current_period_end,
        )
        return subscription

    async def record_override(
        self,
        *,
        admin_user_id: UUID,
        target_user_id: UUID,
        override_type: str,
        reason: str,
        payload: dict,
        expires_at: datetime | None = None,
    ) -> AdminOverride:
        if not reason.strip():
            raise AdminError("reason_required", "Admin overrides require a reason.")
        now = datetime.now(UTC)
        override = AdminOverride(
            admin_user_id=admin_user_id,
            target_user_id=target_user_id,
            override_type=override_type,
            reason=reason,
            payload=payload,
            effective_at=now,
            expires_at=expires_at,
            created_at=now,
        )
        self.session.add(override)
        self.session.add(
            AuditEvent(
                actor_user_id=admin_user_id,
                actor_type="admin",
                action=f"admin_override.{override_type}",
                target_type="user",
                target_id=str(target_user_id),
                metadata_redacted={"reason": reason, "payload": payload},
                created_at=now,
            )
        )
        await self.session.flush()
        return override
