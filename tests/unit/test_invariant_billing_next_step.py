"""Invariant: every unfinished payment offers exactly one safe way forward.

The rule lives in :func:`ai_market_monitor.api.routers.dashboard._billing_history_rows`.
Both surfaces read the ``next_step`` field it produces. No surface re-derives the rule,
and no surface offers a second attempt while a payment is already in flight.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from ai_market_monitor.api.routers.dashboard import (
    _NOT_ON_SALE_REASON,
    _billing_history_rows,
)
from ai_market_monitor.api.routers.dashboard_test import (
    _PAYMENT_WORDS,
    _payment_row,
)
from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import BillingCheckoutAttempt, Plan


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


def _plan(plan_id: UUID | None = None, code: str = "trader") -> Plan:
    return Plan(
        id=plan_id or uuid4(),
        code=code,
        name="Trader",
        description="A plan",
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


def _attempt(
    *,
    status: str,
    plan_id: UUID | None = None,
    checkout_url: str | None = "https://checkout.example.com/abc",
    provider_session_id: str | None = "ch_abc",
    expires_at: datetime | None = None,
    billing_cycle: str = "monthly",
    amount: Decimal = Decimal("15.00"),
) -> BillingCheckoutAttempt:
    now = datetime.now(UTC)
    return BillingCheckoutAttempt(
        id=uuid4(),
        user_id=uuid4(),
        plan_id=plan_id or uuid4(),
        billing_cycle=billing_cycle,
        provider="nowpayments",
        provider_session_id=provider_session_id,
        checkout_url=checkout_url,
        status=status,
        idempotency_key=f"test-{uuid4()}",
        terms_version="2026-01",
        amount=amount,
        currency="USD",
        terms_accepted_at=now,
        expires_at=expires_at or (now + timedelta(hours=1)),
        billing_profile={},
    )


@pytest.fixture
def settings() -> Settings:
    return _settings()


@pytest.fixture(autouse=True)
def _plan_is_on_sale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most tests check the status rule, not the catalog."""

    import ai_market_monitor.api.routers.dashboard as dashboard_module

    monkeypatch.setattr(
        dashboard_module,
        "plan_is_on_sale",
        lambda *_args, **_kwargs: True,
    )


class TestPaymentStatusOffers:
    """Each known status maps to exactly one offer."""

    @pytest.mark.parametrize(
        "status",
        [pytest.param(status, id=status) for status in _PAYMENT_WORDS],
    )
    def test_known_status_offer(self, status: str, settings: Settings) -> None:
        plan = _plan()
        attempt = _attempt(status=status, plan_id=plan.id)
        rows = _billing_history_rows(
            [attempt], {plan.id: plan}, settings, now=datetime.now(UTC)
        )
        (row,) = rows

        if status in {"completed", "refunded", "processing", "creating"}:
            assert row["next_step"] is None, status
            assert row["blocked_reason"] is None, status
            assert row["can_resume"] is False, status
            assert row["resume_url"] is None, status
        elif status == "pending":
            # Default _attempt creates a live pending URL.
            assert row["next_step"] is not None, status
            assert row["next_step"]["kind"] == "resume", status
            assert row["next_step"]["label"] == "Finish paying", status
            assert row["can_resume"] is True, status
            assert row["resume_url"] is not None, status
        else:
            assert row["next_step"] is not None, status
            assert row["next_step"]["kind"] == "retry", status
            assert row["next_step"]["label"] == "Try again", status
            assert row["can_resume"] is False, status
            assert row["resume_url"] is None, status

    def test_unrecognised_status_offers_nothing(self, settings: Settings) -> None:
        plan = _plan()
        attempt = _attempt(status="mystery", plan_id=plan.id)
        rows = _billing_history_rows(
            [attempt], {plan.id: plan}, settings, now=datetime.now(UTC)
        )
        (row,) = rows
        assert row["next_step"] is None
        assert row["blocked_reason"] is None
        assert row["can_resume"] is False
        assert row["resume_url"] is None


class TestPendingVariants:
    """A pending attempt can be resumable or retryable, depending on its data."""

    def test_pending_with_live_url_is_resumable(self, settings: Settings) -> None:
        plan = _plan()
        attempt = _attempt(
            status="pending",
            plan_id=plan.id,
            checkout_url="https://checkout.example.com/abc",
            provider_session_id="ch_abc",
        )
        rows = _billing_history_rows(
            [attempt], {plan.id: plan}, settings, now=datetime.now(UTC)
        )
        (row,) = rows
        assert row["next_step"]["kind"] == "resume"
        assert row["next_step"]["url"] == f"/dashboard/billing/checkout/{attempt.id}/resume"
        assert row["can_resume"] is True

    def test_pending_with_expired_url_is_retryable(self, settings: Settings) -> None:
        plan = _plan()
        now = datetime.now(UTC)
        attempt = _attempt(
            status="pending",
            plan_id=plan.id,
            checkout_url="https://checkout.example.com/abc",
            provider_session_id="ch_abc",
            expires_at=now - timedelta(hours=1),
        )
        rows = _billing_history_rows([attempt], {plan.id: plan}, settings, now=now)
        (row,) = rows
        assert row["next_step"]["kind"] == "retry"
        assert row["can_resume"] is False

    def test_pending_with_missing_url_is_retryable(self, settings: Settings) -> None:
        plan = _plan()
        attempt = _attempt(
            status="pending",
            plan_id=plan.id,
            checkout_url=None,
            provider_session_id="ch_abc",
        )
        rows = _billing_history_rows(
            [attempt], {plan.id: plan}, settings, now=datetime.now(UTC)
        )
        (row,) = rows
        assert row["next_step"]["kind"] == "retry"

    def test_pending_with_missing_session_id_is_retryable(
        self, settings: Settings
    ) -> None:
        plan = _plan()
        attempt = _attempt(
            status="pending",
            plan_id=plan.id,
            checkout_url="https://checkout.example.com/abc",
            provider_session_id=None,
        )
        rows = _billing_history_rows(
            [attempt], {plan.id: plan}, settings, now=datetime.now(UTC)
        )
        (row,) = rows
        assert row["next_step"]["kind"] == "retry"


class TestPlanNotOnSale:
    """A plan that can no longer be bought must not be retried or resumed."""

    def test_plan_not_on_sale_blocks_resume_and_retry(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        import ai_market_monitor.api.routers.dashboard as dashboard_module

        monkeypatch.setattr(
            dashboard_module, "plan_is_on_sale", lambda *_args, **_kwargs: False
        )

        plan = _plan()
        attempt = _attempt(
            status="pending",
            plan_id=plan.id,
            checkout_url="https://checkout.example.com/abc",
            provider_session_id="ch_abc",
        )
        rows = _billing_history_rows(
            [attempt], {plan.id: plan}, settings, now=datetime.now(UTC)
        )
        (row,) = rows

        assert row["next_step"] is None
        assert row["blocked_reason"] == _NOT_ON_SALE_REASON
        assert row["can_resume"] is False
        assert row["resume_url"] is None


class TestBannerAndRowAgree:
    """The top banner and the payment row read the same owner's decision."""

    def test_banner_picks_the_first_actionable_row(self, settings: Settings) -> None:
        plan = _plan()
        live = _attempt(
            status="pending",
            plan_id=plan.id,
            checkout_url="https://checkout.example.com/live",
            provider_session_id="ch_live",
        )
        failed = _attempt(status="failed", plan_id=plan.id)
        rows = _billing_history_rows(
            [live, failed], {plan.id: plan}, settings, now=datetime.now(UTC)
        )
        payments = [_payment_row(row, "UTC") for row in rows]
        unfinished = next(
            (row for row in payments if row["next_step"] is not None), None
        )

        assert unfinished is not None
        assert unfinished["next_step"]["kind"] == "resume"
        assert unfinished["plan_name"] == payments[0]["plan_name"]


class TestRetryDoesNotCreateCharge:
    """"Try again" must never point at a route that creates a charge."""

    def test_retry_next_step_has_no_charge_url(self, settings: Settings) -> None:
        plan = _plan()
        attempt = _attempt(status="failed", plan_id=plan.id)
        rows = _billing_history_rows(
            [attempt], {plan.id: plan}, settings, now=datetime.now(UTC)
        )
        (row,) = rows
        assert row["next_step"]["kind"] == "retry"
        assert row["next_step"]["url"] is None

    def test_subscription_template_retry_uses_existing_checkout_trigger(
        self,
    ) -> None:
        import re
        from pathlib import Path

        template = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "ai_market_monitor"
            / "templates"
            / "hilal"
            / "dashboard_test"
            / "subscription.html"
        )
        text = template.read_text(encoding="utf-8")
        # The retry branch must open the page's own checkout dialog, not a charge route.
        match = re.search(
            r"{% if payment\.next_step\.kind == 'resume' %}.*?{% else %}(.*?){% endif %}",
            text,
            flags=re.DOTALL,
        )
        assert match is not None, "retry branch not found"
        retry_block = match.group(1)
        assert 'data-s-choose="{{ payment.plan_code }}"' in retry_block
        assert "/dashboard/billing/checkout" not in retry_block

    def test_billing_history_template_retry_uses_existing_checkout_trigger(
        self,
    ) -> None:
        import re
        from pathlib import Path

        template = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "ai_market_monitor"
            / "templates"
            / "hilal"
            / "dashboard"
            / "partials"
            / "billing_history.html"
        )
        text = template.read_text(encoding="utf-8")
        match = re.search(
            r"{% if row\.next_step\.kind == 'resume' %}.*?{% else %}(.*?){% endif %}",
            text,
            flags=re.DOTALL,
        )
        assert match is not None, "retry branch not found"
        retry_block = match.group(1)
        assert "data-billing-dialog-trigger" in retry_block
        assert "/dashboard/billing/checkout" not in retry_block
