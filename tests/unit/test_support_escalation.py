from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ai_market_monitor.db.models import (
    DiscordConnection,
    IntegrationHealth,
    Plan,
    Subscription,
    SupportRequest,
    TelegramConnection,
    User,
)
from ai_market_monitor.db.models.enums import (
    ConnectionStatus,
    HealthStatus,
    SubscriptionStatus,
    SupportRequestStatus,
)
from ai_market_monitor.services.entitlements import PlanCatalogService
from ai_market_monitor.services.support import SupportEscalationService


async def test_support_escalation_attaches_diagnostic_context(test_context):
    async with test_context["session_factory"]() as session:
        plan = await PlanCatalogService(session).get_or_sync("pro")
        assert isinstance(plan, Plan)
        user = User(display_name="Support User")
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                plan_id=plan.id,
                status=SubscriptionStatus.ACTIVE,
                provider="stripe",
                provider_customer_id="cus_support",
                provider_subscription_id="sub_support",
                current_period_start=datetime.now(UTC),
                current_period_end=datetime.now(UTC) + timedelta(days=30),
            )
        )
        session.add(
            TelegramConnection(
                user_id=user.id,
                telegram_user_id="tg-support",
                status=ConnectionStatus.ACTIVE,
            )
        )
        session.add(
            DiscordConnection(
                user_id=user.id,
                discord_user_id="discord-support",
                status=ConnectionStatus.ACTIVE,
            )
        )
        session.add(
            IntegrationHealth(
                integration="discord",
                scope_key="dm:discord-support",
                status=HealthStatus.DEGRADED,
                consecutive_failures=2,
                last_error_code="RateLimited",
                checked_at=datetime.now(UTC),
            )
        )
        service = SupportEscalationService(session)
        ticket = await service.create_ticket(
            user_id=user.id,
            category="discord_delivery",
            description="My alert did not arrive.",
            source="discord",
        )
        assert ticket.context["plan"] == "pro"
        assert ticket.context["telegram_connection"] is True
        assert ticket.context["discord_connection"] is True
        assert ticket.context["integration_health"][0]["last_error_code"] == "RateLimited"

        await service.escalate(
            support_request_id=ticket.id,
            actor_user_id=None,
            tier=3,
            reason="Delivery logs conflict with provider status.",
        )
        await service.resolve(
            support_request_id=ticket.id,
            actor_user_id=user.id,
            resolution="Provider rate limit caused delayed delivery.",
        )
        row = await session.scalar(select(SupportRequest).where(SupportRequest.id == ticket.id))
        assert row.status == SupportRequestStatus.RESOLVED
        assert row.context["escalation_tier"] == 3
        assert "resolution" in row.context
