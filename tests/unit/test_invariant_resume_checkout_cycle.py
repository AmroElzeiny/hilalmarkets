"""Invariant: the resume link checks the attempt's own billing cycle, not the aggregate.

A checkout attempt remembers the billing cycle the customer chose when they started it.
The aggregate ``purchasable`` flag is true when *any* cycle of the plan is still for
sale, so without checking the cycle the saved annual link forwards the customer to the
payment page even after the annual offer was withdrawn - taking money for a plan the rule
says they may not buy.

This file proves the rule for every purchasable plan, for both monthly and annual.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from ai_market_monitor.api.routers import dashboard as dashboard_module
from ai_market_monitor.api.routers.dashboard import resume_billing_checkout
from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import PURCHASABLE_PLAN_CODES
from ai_market_monitor.db.models import BillingCheckoutAttempt, Plan, User
from ai_market_monitor.db.models.enums import UserStatus
from ai_market_monitor.services import billing as billing_module
from ai_market_monitor.services.billing import plan_checkout_availability


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "app_env": "test",
        "app_secret_key": "test-secret-key-with-at-least-thirty-two-characters",
        "billing_enabled": True,
        "billing_provider": "static",
        "billing_card_provider": "disabled",
        "billing_crypto_provider": "disabled",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class _StubScalars:
    """What ``session.scalars(...)`` returns: a result you can turn into a list."""

    def all(self) -> list[Any]:
        return []


class _StubSession:
    """A minimal async-session replacement good enough for resume_billing_checkout.

    The route asks the database for the attempt, the plan, and the user's active paid
    plans. Each test seeds the answers it needs and the route asks for them by id. The
    route also runs a small handful of ``select(...)``/``scalar(...)`` queries; we only
    return them when the test asks for them.
    """

    def __init__(
        self,
        *,
        attempt: BillingCheckoutAttempt,
        plan: Plan,
        user_id: UUID,
    ) -> None:
        self.attempt = attempt
        self.plan = plan
        self.user_id = user_id
        self.committed = False
        self.added: list[Any] = []

    async def get(self, _model: type, key: Any):
        if _model is BillingCheckoutAttempt and key == self.attempt.id:
            return self.attempt
        if _model is Plan and key == self.plan.id:
            return self.plan
        return None

    async def scalars(self, _statement: Any) -> _StubScalars:
        # Once the cycle is allowed, the route asks whether this person is already on a
        # different paid plan, so an old period can be linked to the new payment. These
        # tests are about the cycle check, and every one of them describes an account
        # holding no paid plan (``active_paid_plan_codes`` is empty above). An empty
        # answer here is that same account, not a convenience.
        return _StubScalars()

    async def commit(self) -> None:
        self.committed = True

    def add(self, instance: Any) -> None:
        self.added.append(instance)


def _attempt(
    *,
    plan_id: UUID,
    billing_cycle: str,
    user_id: UUID,
    checkout_url: str = "https://checkout.example.com/abc",
    provider_session_id: str | None = "ch_abc",
    expires_at: datetime | None = None,
) -> BillingCheckoutAttempt:
    if expires_at is None:
        expires_at = datetime.now(UTC) + timedelta(hours=2)
    return BillingCheckoutAttempt(
        id=uuid4(),
        user_id=user_id,
        plan_id=plan_id,
        billing_cycle=billing_cycle,
        provider="creem",
        provider_session_id=provider_session_id,
        checkout_url=checkout_url,
        status="pending",
        idempotency_key="test-key",
        terms_version="v1",
        amount=Decimal("15.00"),
        currency="USD",
        terms_accepted_at=datetime.now(UTC),
        expires_at=expires_at,
        billing_profile={},
    )


def _user() -> User:
    return User(
        id=uuid4(),
        status=UserStatus.ACTIVE,
        display_name="Test User",
    )


def _withdrawn_cycles_for_plan(
    monkeypatch: pytest.MonkeyPatch, plan_code: str, withdrawn: Iterable[str]
) -> None:
    """Make every method on every cycle but the ones in ``withdrawn`` available."""

    def fake_payment_method_available(_settings, *, method, plan_code, billing_cycle):
        del method
        return (plan_code, billing_cycle) not in {(plan_code, c) for c in withdrawn}

    # Patch the name in both modules because the owner reads it through its own
    # import in ``services/billing.py`` and the route reads it through ``dashboard``.
    monkeypatch.setattr(billing_module, "payment_method_available", fake_payment_method_available)
    monkeypatch.setattr(
        dashboard_module,
        "payment_method_available",
        fake_payment_method_available,
    )


def test_resume_checks_attempt_own_cycle_not_aggregate_purchasable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An annual attempt whose annual cycle is withdrawn must NOT forward the customer.

    The aggregate ``purchasable`` for this plan is true if any other cycle (e.g. monthly)
    is still for sale. The fix passes the attempt's billing_cycle to the owner and checks
    that cycle's flag, so an annual link on a withdrawn annual plan is refused.
    """

    settings = _settings()
    plan_id = uuid4()
    plan = Plan(
        id=plan_id,
        code="pro",
        name="Pro",
        description="",
        price_monthly=Decimal("30.00"),
        currency="USD",
        max_active_strategies=5,
        max_symbols_per_strategy=20,
        minimum_scan_interval_seconds=60,
        telegram_enabled=False,
        discord_enabled=False,
        backtest_enabled=False,
        features={},
        is_active=True,
    )

    # Withdraw annual for the Pro plan, leaving monthly available.
    _withdrawn_cycles_for_plan(monkeypatch, "pro", withdrawn=("annual",))

    async def empty_active_paid(_session, *, user_id):
        del user_id
        return frozenset()

    async def false_repriced(_session, *, user_id):
        del user_id
        return False

    monkeypatch.setattr(dashboard_module, "active_paid_plan_codes", empty_active_paid)
    monkeypatch.setattr(dashboard_module, "paid_access_can_be_repriced", false_repriced)

    attempt = _attempt(plan_id=plan_id, billing_cycle="annual_auto_renewal", user_id=uuid4())
    user = _user()
    attempt.user_id = user.id
    session = _StubSession(attempt=attempt, plan=plan, user_id=user.id)

    response = asyncio.run(
        resume_billing_checkout(
            attempt_id=attempt.id,
            user=user,
            session=session,
            settings=settings,
        )
    )
    assert response.status_code == 303, response
    assert response.headers["location"] == "/dashboard/billing?error=change_plan_instead"


def test_resume_allows_attempt_when_its_cycle_is_still_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity half of the rule: when the cycle is for sale, the resume link works.

    If we wrongly tighten the new check, this case catches it.
    """

    settings = _settings()
    plan_id = uuid4()
    plan = Plan(
        id=plan_id,
        code="pro",
        name="Pro",
        description="",
        price_monthly=Decimal("30.00"),
        currency="USD",
        max_active_strategies=5,
        max_symbols_per_strategy=20,
        minimum_scan_interval_seconds=60,
        telegram_enabled=False,
        discord_enabled=False,
        backtest_enabled=False,
        features={},
        is_active=True,
    )

    # Withdraw nothing. The aggregate and the per-cycle flag both say purchasable.
    _withdrawn_cycles_for_plan(monkeypatch, "pro", withdrawn=())

    async def empty_active_paid(_session, *, user_id):
        del user_id
        return frozenset()

    async def false_repriced(_session, *, user_id):
        del user_id
        return False

    monkeypatch.setattr(dashboard_module, "active_paid_plan_codes", empty_active_paid)
    monkeypatch.setattr(dashboard_module, "paid_access_can_be_repriced", false_repriced)

    attempt = _attempt(plan_id=plan_id, billing_cycle="annual_auto_renewal", user_id=uuid4())
    user = _user()
    attempt.user_id = user.id
    session = _StubSession(attempt=attempt, plan=plan, user_id=user.id)

    response = asyncio.run(
        resume_billing_checkout(
            attempt_id=attempt.id,
            user=user,
            session=session,
            settings=settings,
        )
    )
    assert response.status_code == 303, response
    assert response.headers["location"].startswith("https://checkout.example.com/")


@pytest.mark.parametrize("plan_code", list(PURCHASABLE_PLAN_CODES))
def test_aggregate_purchasable_does_not_rescue_a_withdrawn_cycle(
    monkeypatch: pytest.MonkeyPatch,
    plan_code: str,
) -> None:
    """Parametrised across every paid plan: aggregate purchasable must not rescue a
    cycle that was withdrawn.

    The aggregate ``purchasable`` is true whenever any cycle is for sale; the saved link
    is for one specific cycle. The fix must reject the link when *that* cycle is gone,
    for every plan, in both directions:

    * monthly attempt, monthly cycle withdrawn but annual remains -> refuse
    * annual attempt, annual cycle withdrawn but monthly remains -> refuse

    The old code returned ``True`` because aggregate ``purchasable`` was true. The new
    code asks the cycle's own flag and refuses when the answer is no.
    """

    settings = _settings()
    plan_id = uuid4()
    plan = Plan(
        id=plan_id,
        code=plan_code,
        name=plan_code.title(),
        description="",
        price_monthly=Decimal("15.00"),
        currency="USD",
        max_active_strategies=1,
        max_symbols_per_strategy=10,
        minimum_scan_interval_seconds=60,
        telegram_enabled=False,
        discord_enabled=False,
        backtest_enabled=False,
        features={},
        is_active=True,
    )

    for withdrawn, attempt_cycle in (
        ("monthly", "monthly_auto_renewal"),
        ("annual", "annual_auto_renewal"),
    ):
        _withdrawn_cycles_for_plan(monkeypatch, plan_code, withdrawn=(withdrawn,))

        async def empty_active_paid(_session, *, user_id):
            del user_id
            return frozenset()

        async def false_repriced(_session, *, user_id):
            del user_id
            return False

        monkeypatch.setattr(dashboard_module, "active_paid_plan_codes", empty_active_paid)
        monkeypatch.setattr(dashboard_module, "paid_access_can_be_repriced", false_repriced)

        attempt = _attempt(plan_id=plan_id, billing_cycle=attempt_cycle, user_id=uuid4())
        user = _user()
        attempt.user_id = user.id
        session = _StubSession(attempt=attempt, plan=plan, user_id=user.id)

        # Sanity: aggregate says purchasable, because at least one cycle is still on sale.
        # This is exactly the failure mode the fix is meant to remove.
        aggregate = plan_checkout_availability(
            settings,
            plan_code=plan_code,
            active_paid_plan_codes=frozenset(),
            held_access_can_be_repriced=False,
        )
        assert aggregate["purchasable"], (
            f"aggregate purchasable must be true for {plan_code} when only {withdrawn} is "
            f"withdrawn - if this is false, the test is no longer exercising the failure"
        )

        response = asyncio.run(
            resume_billing_checkout(
                attempt_id=attempt.id,
                user=user,
                session=session,
                settings=settings,
            )
        )
        assert response.status_code == 303, response
        assert response.headers["location"] == "/dashboard/billing?error=change_plan_instead", (
            f"plan={plan_code} cycle={attempt_cycle} withdrawn={withdrawn}"
        )
