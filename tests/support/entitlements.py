"""One way for a test to give its user a paid plan.

Several dashboard features are Monitor-plan features. `missed_alert_investigations` is
the one that bites most often: the free "basic" plan has it switched off in
`core/plans.py`, and every investigation route refuses a free account with HTTP 403. A
test that signs up and then calls such a route is measuring the paywall, not the route.

The fix is a plan, not a looser assertion, and the plan is granted here so the three test
files that need it cannot drift into three slightly different Subscription rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from ai_market_monitor.db.models import Subscription, UserIdentity
from ai_market_monitor.db.models.enums import IdentityProvider, SubscriptionStatus
from ai_market_monitor.services.entitlements import PlanCatalogService

#: The plan whose code is "trader" is presented to customers as "Monitor".
MONITOR_PLAN_CODE = "trader"


async def grant_monitor_plan(session_factory, *, user_id: UUID | None = None) -> UUID:
    """Give one user an active Monitor subscription.

    With no `user_id` the account created by the test's own sign-up is used, which is the
    only email identity in a fresh test database. Tests that build a `User` row directly,
    without signing up, pass the id themselves.
    """

    async with session_factory() as session:
        if user_id is None:
            user_id = await session.scalar(
                select(UserIdentity.user_id).where(
                    UserIdentity.provider == IdentityProvider.EMAIL
                )
            )
        assert user_id is not None, "No test user to grant the Monitor plan to."
        plan = await PlanCatalogService(session).get_or_sync(MONITOR_PLAN_CODE)
        now = datetime.now(UTC)
        session.add(
            Subscription(
                user_id=user_id,
                plan_id=plan.id,
                status=SubscriptionStatus.ACTIVE,
                provider="test",
                provider_subscription_id=f"monitor-plan-{uuid4()}",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
            )
        )
        await session.commit()
    return user_id
