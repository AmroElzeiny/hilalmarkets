import hmac
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.plans import (
    PLAN_DEFINITIONS,
    PROMOTION_ENDS_AT,
    PURCHASABLE_PLAN_CODES,
    STRATEGY_APPROVAL_LIMIT_KEY,
    STRATEGY_APPROVAL_WINDOW_DAYS,
    UNLIMITED_SYMBOL_CAP,
    effective_monthly_price,
    original_monthly_price,
    plan_name,
    plan_offer_payload,
    promotional_monthly_price,
)
from ai_market_monitor.db.models import (
    AdminOverride,
    Alert,
    AlertDelivery,
    AuditEvent,
    BillingCheckoutAttempt,
    BillingEvent,
    EntitlementSnapshot,
    PaymentEmailDelivery,
    Plan,
    ReferralCode,
    ReferralRelationship,
    Strategy,
    StrategyVersion,
    Subscription,
    TelegramConnection,
    TrialAlertAttribution,
    TrialCycle,
    UsageRecord,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import (
    AlertType,
    ConnectionStatus,
    DeliveryChannel,
    DeliveryStatus,
    IdentityProvider,
    StrategyStatus,
    StrategyVersionStatus,
    SubscriptionStatus,
    TrialStatus,
)
from ai_market_monitor.services.admin import AdminCommercialService
from ai_market_monitor.services.admin_notifications import AdminNotificationService
from ai_market_monitor.services.billing import (
    BillingError,
    BillingService,
    BillingWebhookVerifier,
    CreemBillingProvider,
    billing_provider_capabilities,
)
from ai_market_monitor.services.entitlements import (
    EntitlementError,
    EntitlementService,
    PlanCatalogService,
    UsageService,
)
from ai_market_monitor.services.plan_limits import plan_limit_notice
from ai_market_monitor.services.referrals import ReferralError, ReferralService
from ai_market_monitor.services.trials import TrialLifecycleService
from tests.factories import load_strategy
from tests.support.billing_config import live_billing_overrides

MONITOR_CHECKOUT_AMOUNT = effective_monthly_price("trader")
MONITOR_CHECKOUT_AMOUNT_TEXT = f"{MONITOR_CHECKOUT_AMOUNT:.2f}"


def _crypto_only_server(**overrides: object) -> Settings:
    """A test server that takes crypto through NOWPayments and has card payments off.

    Built from `live_billing_overrides`, the one description of a server that can take
    money, rather than written out here. Six tests in this file each wrote their own
    copy, and none of the six had a NOWPayments key or had billing switched on. They
    broke together the day checkout started asking the plan page's own question — can
    this server really sell this plan this way? — and got the honest answer: no.

    The IPN tests sign with the shared ``billing_webhook_secret``. NOWPayments uses that
    secret only when it has no IPN secret of its own, so the IPN secret is left out.
    """

    values: dict[str, object] = {
        "app_env": "test",
        "app_secret_key": "test-secret-key-with-at-least-thirty-two-characters",
        **live_billing_overrides(),
        "billing_provider": "nowpayments",
        "billing_card_provider": "disabled",
        "billing_webhook_secret": "secret",
        "nowpayments_ipn_secret": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


async def create_user(session, display_name: str = "Trader") -> User:
    user = User(display_name=display_name)
    session.add(user)
    await session.flush()
    return user


async def activate_subscription(session, user_id, plan_code: str) -> Subscription:
    plan = await PlanCatalogService(session).get_or_sync(plan_code)
    subscription = Subscription(
        user_id=user_id,
        plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
        provider="test",
        provider_customer_id=f"cus_{user_id}",
        provider_subscription_id=f"sub_{user_id}_{plan_code}",
        current_period_start=datetime.now(UTC),
        current_period_end=datetime.now(UTC) + timedelta(days=30),
    )
    session.add(subscription)
    await session.flush()
    return subscription


async def test_plan_catalog_syncs_central_definitions(test_context):
    async with test_context["session_factory"]() as session:
        await PlanCatalogService(session).sync_defaults()
        assert await session.scalar(select(func.count(Plan.id))) == len(PLAN_DEFINITIONS)
        pro = await session.scalar(select(Plan).where(Plan.code == "pro"))
        assert pro is not None
        # The legacy column remains readable for old rows, but new catalog rows
        # can no longer grant an active Discord entitlement.
        assert pro.discord_enabled is False
        assert pro.features["limits"]["active_strategies"] == 10


def test_public_plan_limits_match_the_published_catalog():
    basic = PLAN_DEFINITIONS["demo"]
    monitor = PLAN_DEFINITIONS["trader"]
    pro = PLAN_DEFINITIONS["pro"]

    assert basic.limits["saved_strategies"] == 2
    assert basic.limits["strategy_approvals_per_30_days"] == 2
    assert basic.limits["active_strategies"] == 1
    assert basic.limits["alerts_per_week"] == 2
    assert basic.limits["user_initiated_scans_per_week"] == 1
    assert basic.features["ai_assistant"] is True
    assert basic.features["missed_alert_investigations"] is False
    assert monitor.limits["active_strategies"] == 3
    assert monitor.limits["alerts_per_day"] == 20
    assert monitor.limits["strategy_approvals_per_30_days"] == 20
    assert monitor.limits["on_demand_scans_per_month"] == 10
    assert monitor.limits["forensic_investigations_per_month"] == 100_000
    assert pro.monthly_price == Decimal("25.00")
    assert pro.limits["active_strategies"] == 10
    assert pro.limits["alerts_per_day"] == 100_000
    assert pro.limits["strategy_approvals_per_30_days"] == UNLIMITED_SYMBOL_CAP
    assert pro.limits["on_demand_scans_per_month"] == 100


def test_every_plan_carries_the_thirty_day_approval_limit():
    """One window for every plan; only the number in it changes.

    The approval allowance is counted over the same 30 days on every plan, so the
    gate reads one key. A plan that omits the key would fall through to "no limit"
    without anybody noticing, which is how a free account could approve for ever.
    """

    assert STRATEGY_APPROVAL_WINDOW_DAYS == 30
    for code, plan in PLAN_DEFINITIONS.items():
        assert STRATEGY_APPROVAL_LIMIT_KEY in plan.limits, code
        assert isinstance(plan.limits[STRATEGY_APPROVAL_LIMIT_KEY], int), code
        assert plan.limits[STRATEGY_APPROVAL_LIMIT_KEY] > 0, code


def test_paid_plans_are_priced_and_promoted_the_way_the_pages_say():
    """The launch price is the price everywhere until it ends - no code needed.

    Every surface (dashboard, landing page, checkout, crypto invoice) asks the same
    two functions, so a page can never print one number while checkout charges
    another.
    """

    before_the_deadline = PROMOTION_ENDS_AT - timedelta(days=1)
    after_the_deadline = PROMOTION_ENDS_AT + timedelta(days=1)

    # Read at a fixed moment inside the offer. Left on the real clock, these four lines
    # began failing on the morning the launch offer ended, 20 September 2026, although
    # nothing about the prices had changed.
    assert original_monthly_price("trader", now=before_the_deadline) == Decimal("15.00")
    assert original_monthly_price("pro", now=before_the_deadline) == Decimal("25.00")
    assert promotional_monthly_price("trader", now=before_the_deadline) == Decimal("9.00")
    assert promotional_monthly_price("pro", now=before_the_deadline) == Decimal("17.00")
    assert original_monthly_price("trader", now=after_the_deadline) is None
    assert promotional_monthly_price("trader", now=after_the_deadline) is None

    for code, promoted, normal in (
        ("trader", Decimal("9.00"), Decimal("15.00")),
        ("pro", Decimal("17.00"), Decimal("25.00")),
    ):
        assert effective_monthly_price(code, now=before_the_deadline) == promoted
        assert effective_monthly_price(code, now=after_the_deadline) == normal
        payload = plan_offer_payload(code, now=before_the_deadline)
        assert payload["promotionRunning"] is True
        assert payload["promotionEndsAt"] == PROMOTION_ENDS_AT.isoformat()
        assert payload["monthlyAvailable"] is True
        assert "discountCode" not in payload


async def test_basic_approval_limit_counts_distinct_strategies_over_30_days(test_context):
    async with test_context["session_factory"]() as session:
        user = await create_user(session, "Basic approval limit")
        definition = load_strategy()
        strategies = [
            Strategy(user_id=user.id, name=f"Strategy {index}")
            for index in range(3)
        ]
        session.add_all(strategies)
        await session.flush()
        for strategy in strategies[:2]:
            session.add(
                StrategyVersion(
                    strategy_id=strategy.id,
                    version_number=1,
                    status=StrategyVersionStatus.APPROVED,
                    source_type="structured",
                    schema_json=definition.model_dump(mode="json"),
                    schema_hash=definition.canonical_hash(),
                    approved_by_user_id=user.id,
                    approved_schema_hash=definition.canonical_hash(),
                    approved_at=datetime.now(UTC),
                    preview_status="succeeded",
                )
            )
        await session.flush()

        service = EntitlementService(session)
        with pytest.raises(EntitlementError) as error:
            await service.enforce_strategy_approval(
                user.id,
                strategy_id=strategies[2].id,
            )
        assert error.value.code == "strategy_approval_limit"

        # A revision of one of the two approved strategies does not consume a third slot.
        await service.enforce_strategy_approval(
            user.id,
            strategy_id=strategies[0].id,
        )


async def test_trial_lifecycle_uses_pro_trial_and_expires(test_context):
    settings = Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        trial_days=7,
        delivery_settlement_grace_minutes=0,
    )
    async with test_context["session_factory"]() as session:
        user = await create_user(session)
        trial = await TrialLifecycleService(session, settings).activate(user.id)
        assert trial.status == TrialStatus.ELIGIBLE
        entitlement = await EntitlementService(session).current(user.id)
        assert entitlement.plan.code == "pro_trial"
        await TrialLifecycleService(session, settings).start_monitoring_cycle(user.id)
        cycle = await session.scalar(select(TrialCycle).where(TrialCycle.trial_id == trial.id))
        assert cycle is not None
        assert (cycle.ends_at - cycle.starts_at).days == 7
        expired = await TrialLifecycleService(session, settings).expire_due(
            now=cycle.ends_at + timedelta(seconds=1)
        )
        assert expired == [trial]
        assert trial.status == TrialStatus.EXPIRED


async def test_monitor_trial_expires_after_seven_days_without_auto_renewal(test_context):
    settings = Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        trial_days=7,
        delivery_settlement_grace_minutes=0,
    )
    async with test_context["session_factory"]() as session:
        user = await create_user(session)
        session.add(
            TelegramConnection(
                user_id=user.id,
                telegram_user_id="trial-telegram",
                chat_id="trial-chat",
                status=ConnectionStatus.ACTIVE,
                alerts_enabled=True,
            )
        )
        session.add(
            Strategy(
                user_id=user.id,
                name="Trial monitor",
                status=StrategyStatus.ACTIVE,
                activated_at=datetime.now(UTC),
            )
        )
        await session.flush()
        trial_service = TrialLifecycleService(session, settings)
        trial = await trial_service.activate(user.id)
        await trial_service.start_monitoring_cycle(user.id)
        first_cycle = await session.scalar(
            select(TrialCycle).where(TrialCycle.trial_id == trial.id)
        )
        assert first_cycle is not None
        expired = await trial_service.expire_due(now=first_cycle.ends_at + timedelta(seconds=1))
        assert expired == [trial]
        assert trial.status == TrialStatus.EXPIRED
        assert first_cycle.status == "expired"
        assert first_cycle.renewal_decision == "trial_period_completed"
        second_cycle = await session.scalar(
            select(TrialCycle).where(
                TrialCycle.trial_id == trial.id,
                TrialCycle.cycle_number == 2,
            )
        )
        assert second_cycle is None
        assert trial.ends_at == first_cycle.ends_at


async def test_trial_qualifying_delivery_is_attributed_once(test_context):
    settings = Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        trial_days=7,
    )
    async with test_context["session_factory"]() as session:
        user = await create_user(session)
        definition = load_strategy()
        strategy = Strategy(
            user_id=user.id,
            name="Trial monitor",
            status=StrategyStatus.ACTIVE,
            activated_at=datetime.now(UTC),
        )
        session.add(strategy)
        await session.flush()
        version = StrategyVersion(
            strategy_id=strategy.id,
            version_number=1,
            status=StrategyVersionStatus.ACTIVE,
            source_type="structured",
            schema_json=definition.model_dump(mode="json"),
            schema_hash=definition.canonical_hash(),
            approved_by_user_id=user.id,
            approved_schema_hash=definition.canonical_hash(),
            approved_at=datetime.now(UTC),
            preview_status="succeeded",
            activated_at=datetime.now(UTC),
        )
        session.add(version)
        await session.flush()
        strategy.active_version_id = version.id
        trial_service = TrialLifecycleService(session, settings)
        trial = await trial_service.activate(user.id)
        await trial_service.start_monitoring_cycle(user.id)
        cycle = await session.scalar(select(TrialCycle).where(TrialCycle.trial_id == trial.id))
        assert cycle is not None
        alert = Alert(
            user_id=user.id,
            strategy_version_id=version.id,
            setup_instance_id=None,
            alert_type=AlertType.CONFIRMED,
            deduplication_key="trial-confirmed-alert",
            title="Confirmed",
            body="Confirmed",
            proof_receipt={"symbol": "SOL/USDT", "conditions": []},
        )
        session.add(alert)
        await session.flush()
        first_delivery = AlertDelivery(
            alert_id=alert.id,
            channel=DeliveryChannel.TELEGRAM,
            destination_key="chat:trial-chat",
            status=DeliveryStatus.SENT,
            delivered_at=datetime.now(UTC),
        )
        second_delivery = AlertDelivery(
            alert_id=alert.id,
            channel=DeliveryChannel.DISCORD,
            destination_key="dm:123",
            status=DeliveryStatus.DELIVERED,
            delivered_at=datetime.now(UTC),
        )
        session.add_all([first_delivery, second_delivery])
        await session.flush()
        await trial_service.record_successful_delivery(first_delivery)
        await trial_service.record_successful_delivery(second_delivery)
        assert cycle.qualifying_alerts_delivered == 1
        assert await session.scalar(select(func.count(TrialAlertAttribution.id))) == 1


async def test_trial_alert_cap_uses_current_definition_when_plan_json_is_stale(test_context):
    settings = Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        trial_days=7,
        trial_alerts_per_cycle=500,
    )
    async with test_context["session_factory"]() as session:
        user = await create_user(session)
        trial_service = TrialLifecycleService(session, settings)
        trial = await trial_service.activate(user.id)
        await trial_service.start_monitoring_cycle(user.id)
        plan = await session.get(Plan, trial.plan_id)
        assert plan is not None
        stale_features = dict(plan.features)
        stale_limits = dict(stale_features.get("limits") or {})
        stale_limits["alerts_per_trial_cycle"] = 5
        stale_features["limits"] = stale_limits
        plan.features = stale_features
        cycle = await session.scalar(select(TrialCycle).where(TrialCycle.trial_id == trial.id))
        assert cycle is not None
        cycle.qualifying_alerts_delivered = 5
        await session.flush()

        cap_reached, _, cap = await trial_service.trial_alert_cap_reached(user.id)

        assert cap == PLAN_DEFINITIONS["pro_trial"].limits["alerts_per_trial_cycle"]
        assert cap_reached is False


async def test_active_subscription_bypasses_trial_alert_cap(test_context):
    settings = Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        trial_days=7,
        trial_alerts_per_cycle=1,
    )
    async with test_context["session_factory"]() as session:
        user = await create_user(session)
        trial_service = TrialLifecycleService(session, settings)
        trial = await trial_service.activate(user.id)
        await trial_service.start_monitoring_cycle(user.id)
        cycle = await session.scalar(select(TrialCycle).where(TrialCycle.trial_id == trial.id))
        assert cycle is not None
        cycle.qualifying_alerts_delivered = 99
        await activate_subscription(session, user.id, "pro")
        await session.flush()

        cap_reached, cycle_for_cap, cap = await trial_service.trial_alert_cap_reached(user.id)

        assert cap_reached is False
        assert cycle_for_cap is None
        assert cap == 0


async def test_entitlement_blocks_active_strategy_timeframe_symbol_and_discord_limits(
    test_context,
):
    async with test_context["session_factory"]() as session:
        user = await create_user(session)
        await activate_subscription(session, user.id, "trader")
        # The catalog owns the number. Reading it here means a change to the plan
        # never leaves this test asserting a limit the product no longer has.
        allowed = int(PLAN_DEFINITIONS["trader"].limits["active_strategies"])
        for index in range(allowed):
            session.add(
                Strategy(
                    user_id=user.id,
                    name=f"Active {index}",
                    status=StrategyStatus.ACTIVE,
                    activated_at=datetime.now(UTC),
                )
            )
        await session.flush()
        definition = load_strategy()
        with pytest.raises(EntitlementError) as active_error:
            await EntitlementService(session).enforce_strategy_activation(user.id, definition)
        assert active_error.value.code == "active_strategy_limit"
        notice = str(active_error.value)
        assert notice == plan_limit_notice(
            "active_strategy_limit", plan_code="trader", allowed=allowed
        )
        # A beginner has to be told the number, the plan they are on, and what to do.
        assert str(allowed) in notice
        assert plan_name("trader") in notice
        assert plan_name("pro") in notice

        second_user = await create_user(session, "Symbol limit")
        await activate_subscription(session, second_user.id, "trader")
        too_many_symbols = definition.model_copy(deep=True)
        too_many_symbols.universe.include_symbols = [f"COIN{i}/USDT" for i in range(201)]
        with pytest.raises(EntitlementError) as symbol_error:
            await EntitlementService(session).enforce_strategy_activation(
                second_user.id, too_many_symbols
            )
        assert symbol_error.value.code == "symbol_limit"

        duplicated_symbols = definition.model_copy(deep=True)
        duplicated_symbols.universe.include_symbols = [
            "SOL/USDT",
            "sol-usdt",
            "SOL/USDT",
        ]
        await EntitlementService(session).enforce_strategy_activation(
            second_user.id,
            duplicated_symbols,
        )

        discord_definition = definition.model_copy(deep=True)
        discord_definition.alerts.channels = ["discord"]
        with pytest.raises(EntitlementError) as discord_error:
            await EntitlementService(session).enforce_strategy_activation(
                second_user.id, discord_definition
            )
        assert discord_error.value.code == "delivery_channel_retired"


async def test_lifetime_plan_removes_practical_activation_and_delivery_limits(test_context):
    async with test_context["session_factory"]() as session:
        user = await create_user(session, "Lifetime User")
        await activate_subscription(session, user.id, "lifetime")
        definition = load_strategy()
        definition.universe.include_symbols = [f"COIN{i}/USDT" for i in range(500)]
        definition.supporting_timeframes = ["1m", "4h"]
        definition.alerts.channels = ["telegram"]

        context = await EntitlementService(session).enforce_strategy_activation(
            user.id,
            definition,
        )

        assert context.plan.code == "lifetime"
        assert context.feature_enabled("advanced_forensics") is True
        assert context.limit("active_strategies") >= 100_000
        assert context.limit("symbols_per_strategy") >= 100_000
        assert context.limit("alerts_per_day") >= 100_000


async def test_billing_webhook_is_idempotent_and_downgrade_pauses_excess_strategies(
    test_context,
):
    async with test_context["session_factory"]() as session:
        user = await create_user(session)
        await PlanCatalogService(session).sync_defaults()
        created = await BillingService(session, test_context["settings"]).process_event(
            provider="stripe",
            payload={
                "id": "evt_create",
                "type": "customer.subscription.created",
                "data": {
                    "user_id": str(user.id),
                    "plan_code": "pro",
                    "provider_customer_id": "cus_123",
                    "provider_subscription_id": "sub_123",
                    "status": "active",
                    "current_period_start": "2035-01-01T00:00:00+00:00",
                    "current_period_end": "2035-02-01T00:00:00+00:00",
                    "card": {"last4": "4242", "client_secret": "hidden"},
                },
            },
        )
        replay = await BillingService(session, test_context["settings"]).process_event(
            provider="stripe",
            payload={"id": "evt_create", "type": "customer.subscription.created", "data": {}},
        )
        assert created.replayed is False
        assert replay.replayed is True
        assert await session.scalar(select(func.count(BillingEvent.id))) == 1
        event = await session.scalar(select(BillingEvent))
        assert event.payload_redacted["data"]["card"] == "[redacted]"
        assert await session.scalar(select(func.count(EntitlementSnapshot.id))) == 1

        # Two more monitors than the smaller plan allows, so the downgrade has to
        # pause exactly two of them - whatever the two plan limits happen to be.
        smaller_plan_allows = int(PLAN_DEFINITIONS["trader"].limits["active_strategies"])
        started = smaller_plan_allows + 2
        for index in range(started):
            session.add(
                Strategy(
                    user_id=user.id,
                    name=f"Strategy {index}",
                    status=StrategyStatus.ACTIVE,
                    activated_at=datetime.now(UTC) + timedelta(minutes=index),
                )
            )
        await session.flush()
        await BillingService(session, test_context["settings"]).process_event(
            provider="stripe",
            payload={
                "id": "evt_downgrade",
                "type": "customer.subscription.updated",
                "data": {
                    "user_id": str(user.id),
                    "plan_code": "trader",
                    "provider_customer_id": "cus_123",
                    "provider_subscription_id": "sub_123",
                    "status": "active",
                    "current_period_end": "2035-02-01T00:00:00+00:00",
                },
            },
        )
        active_count = await session.scalar(
            select(func.count(Strategy.id)).where(Strategy.status == StrategyStatus.ACTIVE)
        )
        paused_count = await session.scalar(
            select(func.count(Strategy.id)).where(Strategy.status == StrategyStatus.PAUSED)
        )
        assert active_count == smaller_plan_allows
        assert paused_count == started - smaller_plan_allows


async def test_webhook_signature_verification(test_context):
    settings = Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        billing_webhook_secret="secret",
    )
    body = json.dumps({"id": "evt", "type": "test"}).encode()
    import hmac
    from hashlib import sha256

    signature = hmac.new(b"secret", body, sha256).hexdigest()
    BillingWebhookVerifier(settings).verify(body, signature)
    with pytest.raises(BillingError):
        BillingWebhookVerifier(settings).verify(body, "bad")

    timestamp = int(datetime.now(UTC).timestamp())
    stripe_signature = hmac.new(
        b"secret",
        str(timestamp).encode("ascii") + b"." + body,
        sha256,
    ).hexdigest()
    BillingWebhookVerifier(settings).verify(
        body,
        f"t={timestamp},v1={stripe_signature}",
        provider="stripe",
        now=datetime.fromtimestamp(timestamp, tz=UTC),
    )
    with pytest.raises(BillingError, match="too old"):
        BillingWebhookVerifier(settings).verify(
            body,
            f"t={timestamp},v1={stripe_signature}",
            provider="stripe",
            now=datetime.fromtimestamp(timestamp + 301, tz=UTC),
        )

    nowpayments_body = json.dumps(
        {
            "payment_id": "pay_123",
            "payment_status": "finished",
            "order_id": "amm|user|pro|abc",
        }
    ).encode()
    from hashlib import sha512

    sorted_body = json.dumps(
        {
            "order_id": "amm|user|pro|abc",
            "payment_id": "pay_123",
            "payment_status": "finished",
        },
        separators=(",", ":"),
    ).encode()
    nowpayments_signature = hmac.new(b"secret", sorted_body, sha512).hexdigest()
    BillingWebhookVerifier(settings).verify(
        nowpayments_body,
        nowpayments_signature,
        provider="nowpayments",
    )
    with pytest.raises(BillingError):
        BillingWebhookVerifier(settings).verify(nowpayments_body, "bad", provider="nowpayments")


async def test_nowpayments_finished_ipn_creates_subscription(test_context):
    settings = _crypto_only_server()
    async with test_context["session_factory"]() as session:
        user = await create_user(session)
        service = BillingService(session, settings)
        prepared = await service.prepare_checkout(
            user_id=user.id,
            plan_code="trader",
            billing_cycle="monthly",
            request_key="nowpayments-finished",
            terms_accepted=True,
        )
        payload = {
            "payment_id": "pay_456",
            "payment_status": "finished",
            "order_id": f"hm|{prepared.attempt.id}|trader",
            "price_amount": MONITOR_CHECKOUT_AMOUNT_TEXT,
            "price_currency": "USD",
            "pay_amount": "100.00",
            "actually_paid": "100.00",
            "pay_currency": "USDT",
        }
        body = json.dumps(payload).encode()
        signed_body = json.dumps(
            {
                "actually_paid": payload["actually_paid"],
                "order_id": payload["order_id"],
                "pay_amount": payload["pay_amount"],
                "pay_currency": payload["pay_currency"],
                "payment_id": payload["payment_id"],
                "payment_status": payload["payment_status"],
                "price_amount": payload["price_amount"],
                "price_currency": payload["price_currency"],
            },
            separators=(",", ":"),
        ).encode()
        from hashlib import sha512

        signature = hmac.new(b"secret", signed_body, sha512).hexdigest()
        result = await service.process_verified_webhook(
            provider="nowpayments",
            body=body,
            signature=signature,
        )
        assert result.processing_status == "processed"
        subscription = await session.scalar(
            select(Subscription).where(Subscription.user_id == user.id)
        )
        assert subscription is not None
        assert subscription.provider == "nowpayments"
        assert subscription.status == SubscriptionStatus.ACTIVE
        assert subscription.cancel_at_period_end is True
        assert subscription.current_period_end is not None

        with pytest.raises(BillingError) as duplicate:
            await service.process_event(
                provider="nowpayments",
                payload={
                    "id": "evt-second-payment-for-one-checkout",
                    "type": "payment.finished",
                    "data": {
                        "checkout_attempt_id": str(prepared.attempt.id),
                        "provider_subscription_id": "nowpayments_second_payment",
                        "status": "active",
                        "amount": MONITOR_CHECKOUT_AMOUNT_TEXT,
                        "currency": "USD",
                        "settlement_expected_amount": "100.00",
                        "settlement_actual_amount": "100.00",
                        "settlement_currency": "USDT",
                    },
                },
            )
        assert duplicate.value.code == "checkout_already_completed"


async def test_nowpayments_partial_payment_never_grants_access(test_context):
    settings = _crypto_only_server()
    async with test_context["session_factory"]() as session:
        user = await create_user(session, "Partial payment user")
        service = BillingService(session, settings)
        prepared = await service.prepare_checkout(
            user_id=user.id,
            plan_code="trader",
            billing_cycle="one_time_30_day",
            request_key="nowpayments-partial",
            terms_accepted=True,
        )

        result = await service.process_event(
            provider="nowpayments",
            payload={
                "id": "evt-nowpayments-partial",
                "type": "payment.partially_paid",
                "data": {
                    "checkout_attempt_id": str(prepared.attempt.id),
                    "provider_subscription_id": "nowpayments_partial",
                    "status": "partially_paid",
                    "amount": MONITOR_CHECKOUT_AMOUNT_TEXT,
                    "currency": "USD",
                },
            },
        )

        assert result.processing_status == "processed"
        assert prepared.attempt.status == "partially_paid"
        assert await session.scalar(
            select(Subscription).where(Subscription.user_id == user.id)
        ) is None


@pytest.mark.parametrize(
    ("actually_paid", "expected_code"),
    [
        ("99.99", "payment_underpaid"),
        ("100.01", "payment_overpaid"),
    ],
)
async def test_nowpayments_settlement_uses_actual_crypto_received(
    test_context,
    actually_paid,
    expected_code,
):
    settings = _crypto_only_server(
        billing_payment_amount_tolerance_percent=0,
        billing_allow_overpayment=False,
    )
    async with test_context["session_factory"]() as session:
        user = await create_user(session, f"Settlement {expected_code}")
        service = BillingService(session, settings)
        prepared = await service.prepare_checkout(
            user_id=user.id,
            plan_code="trader",
            billing_cycle="one_time_30_day",
            request_key=f"settlement-{expected_code}",
            terms_accepted=True,
        )
        payload = {
            "id": f"evt-settlement-{expected_code}",
            "type": "payment.finished",
            "data": {
                "checkout_attempt_id": str(prepared.attempt.id),
                "provider_subscription_id": f"sub-settlement-{expected_code}",
                "status": "active",
                "amount": MONITOR_CHECKOUT_AMOUNT_TEXT,
                "currency": "USD",
                "settlement_expected_amount": "100.00",
                "settlement_actual_amount": actually_paid,
                "settlement_currency": "USDT",
            },
        }

        with pytest.raises(BillingError) as error:
            await service.process_event(provider="nowpayments", payload=payload)

        assert error.value.code == expected_code


def test_billing_provider_capabilities_match_real_provider_semantics():
    stripe = billing_provider_capabilities("stripe")
    nowpayments = billing_provider_capabilities("nowpayments")
    creem = billing_provider_capabilities("creem")

    assert stripe.supports_recurring_billing is True
    assert stripe.supports_customer_portal is True
    assert stripe.supports_automatic_cancellation is True
    assert stripe.supports_refunds is True
    assert nowpayments.supports_recurring_billing is False
    assert nowpayments.supports_customer_portal is False
    assert nowpayments.supports_automatic_cancellation is False
    assert nowpayments.supports_refunds is False
    assert nowpayments.supports_invoice_receipts is True
    assert creem.supports_recurring_billing is True
    assert creem.supports_customer_portal is True
    assert creem.supports_automatic_cancellation is True
    assert creem.supports_refunds is True


async def test_checkout_allows_both_paid_plans_monthly_and_nothing_else(test_context):
    """Every plan on sale can be bought monthly; nothing else can be bought at all.

    The rule is asserted for every purchasable plan, not for one of them, so opening
    or closing a plan cannot leave this test agreeing with the old shape of the shop.
    """

    settings = _crypto_only_server()
    async with test_context["session_factory"]() as session:
        user = await create_user(session, "Monthly only")
        service = BillingService(session, settings)

        assert set(PURCHASABLE_PLAN_CODES) == {"trader", "pro"}

        for plan_code in PURCHASABLE_PLAN_CODES:
            monthly = await service.prepare_checkout(
                user_id=user.id,
                plan_code=plan_code,
                billing_cycle="monthly",
                request_key=f"open-{plan_code}-monthly",
                terms_accepted=True,
            )
            assert monthly.attempt.billing_cycle == "one_time_30_day"
            assert monthly.attempt.amount == effective_monthly_price(plan_code)

        closed: list[tuple[str, str, str]] = [
            # A plan nobody is selling can never be paid for, by any route.
            ("demo", "monthly", "plan_not_available"),
            ("creator", "monthly", "plan_not_available"),
        ]
        for plan_code in PURCHASABLE_PLAN_CODES:
            closed.append((plan_code, "annual", "billing_cycle_not_available"))
            closed.append((plan_code, "trial_7_day", "billing_cycle_not_available"))

        for plan_code, billing_cycle, expected_code in closed:
            with pytest.raises(BillingError) as error:
                await service.prepare_checkout(
                    user_id=user.id,
                    plan_code=plan_code,
                    billing_cycle=billing_cycle,
                    request_key=f"closed-{plan_code}-{billing_cycle}",
                    terms_accepted=True,
                )
            assert error.value.code == expected_code, (plan_code, billing_cycle)


#: One thing a crypto server can lack, and the setting that takes it away.
_CRYPTO_SERVER_GAPS: dict[str, dict[str, object]] = {
    "nothing_missing": {},
    "billing_switched_off": {"billing_enabled": False},
    "no_nowpayments_key": {"nowpayments_api_key": None},
    "no_way_to_prove_a_payment": {"billing_webhook_secret": None},
}


@pytest.mark.parametrize("gap", list(_CRYPTO_SERVER_GAPS))
@pytest.mark.parametrize("plan_code", PURCHASABLE_PLAN_CODES)
async def test_checkout_takes_crypto_exactly_when_the_plan_page_offers_it(
    test_context, plan_code: str, gap: str
):
    """The page and checkout give one answer, for every plan and every way a server can
    be unable to sell by crypto.

    The page reads `plan_checkout_availability`. Checkout used to read only the price
    list, so a server with crypto switched on and no NOWPayments key — or with billing
    switched off, or with no secret to prove a payment arrived — opened a checkout the
    page would never offer. The customer then met the refusal at NOWPayments, or paid
    and was never confirmed.
    """

    from ai_market_monitor.services.billing import plan_checkout_availability

    settings = _crypto_only_server(**_CRYPTO_SERVER_GAPS[gap])
    offered = plan_checkout_availability(
        settings, plan_code=plan_code, active_paid_plan_codes=frozenset()
    )["crypto_monthly"]
    # Pinned, so the rule cannot pass by both sides saying no to everything.
    assert offered is (gap == "nothing_missing")

    async with test_context["session_factory"]() as session:
        user = await create_user(session, f"Crypto {plan_code} {gap}")
        service = BillingService(session, settings)
        request: dict[str, object] = {
            "user_id": user.id,
            "plan_code": plan_code,
            "billing_cycle": "monthly",
            "request_key": f"crypto-{plan_code}-{gap}",
            "terms_accepted": True,
        }
        if offered:
            prepared = await service.prepare_checkout(**request)  # type: ignore[arg-type]
            assert prepared.attempt.amount == effective_monthly_price(plan_code)
            return
        with pytest.raises(BillingError) as refused:
            await service.prepare_checkout(**request)  # type: ignore[arg-type]
        assert refused.value.code == "plan_not_available"
        assert await session.scalar(select(func.count(BillingCheckoutAttempt.id))) == 0


async def test_creem_creates_a_unique_server_bound_checkout(monkeypatch):
    settings = Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        creem_api_key="creem-test-key",
        creem_product_ids={"trader_monthly": "prod_monitor_monthly"},
    )
    provider = CreemBillingProvider(settings)
    recorded: list[dict] = []

    async def fake_post(path, payload):
        recorded.append({"path": path, "payload": payload})
        return {
            "id": f"ch_{payload['request_id']}",
            "checkout_url": f"https://checkout.creem.io/{payload['request_id']}",
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    user_id = uuid4()
    first_attempt = uuid4()
    second_attempt = uuid4()

    first = await provider.create_checkout_session(
        user_id=user_id,
        checkout_attempt_id=first_attempt,
        plan_code="trader",
        plan_name=plan_name("trader"),
        amount=effective_monthly_price("trader"),
        currency="USD",
        billing_cycle="monthly_auto_renewal",
        customer_email="verified@example.com",
        success_url="https://hilalmarkets.com/billing/success",
        cancel_url="https://hilalmarkets.com/billing/cancel",
    )
    second = await provider.create_checkout_session(
        user_id=user_id,
        checkout_attempt_id=second_attempt,
        plan_code="trader",
        plan_name=plan_name("trader"),
        amount=effective_monthly_price("trader"),
        currency="USD",
        billing_cycle="monthly_auto_renewal",
        customer_email="verified@example.com",
        success_url="https://hilalmarkets.com/billing/success",
        cancel_url="https://hilalmarkets.com/billing/cancel",
    )

    assert first.checkout_url != second.checkout_url
    assert recorded[0]["path"] == "/v1/checkouts"
    assert recorded[0]["payload"]["request_id"] == str(first_attempt)
    assert recorded[0]["payload"]["product_id"] == "prod_monitor_monthly"
    assert recorded[0]["payload"]["customer"] == {"email": "verified@example.com"}
    assert "email" not in recorded[0]["payload"]["metadata"]


async def test_signed_creem_payment_activates_once_and_queues_one_receipt(test_context):
    settings = Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        billing_enabled=True,
        billing_card_provider="creem",
        creem_api_key="creem-test-key",
        creem_webhook_secret="creem-webhook-secret",
        creem_product_ids={"trader_monthly": "prod_monitor_monthly"},
    )
    async with test_context["session_factory"]() as session:
        user = await create_user(session, "Creem customer")
        session.add(
            UserIdentity(
                user_id=user.id,
                provider=IdentityProvider.EMAIL,
                provider_subject="creem@example.com",
                normalized_identifier="creem@example.com",
                display_identifier="creem@example.com",
                is_verified=True,
                is_primary=True,
                verified_at=datetime.now(UTC),
                profile_data={},
            )
        )
        service = BillingService(session, settings)
        prepared = await service.prepare_checkout(
            user_id=user.id,
            plan_code="trader",
            billing_cycle="monthly",
            request_key="creem-paid-order",
            terms_accepted=True,
            billing_profile={
                "first_name": "Creem",
                "last_name": "Customer",
                "address_line1": "1 Market Street",
                "country": "Egypt",
            },
        )
        payload = {
            "id": "evt_creem_paid",
            "eventType": "subscription.paid",
            "object": {
                "object": "subscription",
                "id": "sub_creem_paid",
                "status": "active",
                "customer": {"id": "cus_creem"},
                "product": {
                    "id": "prod_monitor_monthly",
                    "price": int(MONITOR_CHECKOUT_AMOUNT * 100),
                    "currency": "USD",
                },
                "metadata": {
                    "checkout_attempt_id": str(prepared.attempt.id),
                    "user_id": str(user.id),
                    "plan_code": "trader",
                    "billing_cycle": "monthly_auto_renewal",
                },
                "current_period_start_date": "2035-01-01T00:00:00+00:00",
                "current_period_end_date": "2035-02-01T00:00:00+00:00",
                "last_transaction_id": "txn_creem_paid",
            },
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        signature = hmac.new(
            b"creem-webhook-secret",
            body,
            digestmod="sha256",
        ).hexdigest()

        first = await service.process_verified_webhook(
            provider="creem",
            body=body,
            signature=signature,
        )
        replay = await service.process_verified_webhook(
            provider="creem",
            body=body,
            signature=signature,
        )

        subscription = await session.scalar(
            select(Subscription).where(Subscription.user_id == user.id)
        )
        assert first.replayed is False
        assert replay.replayed is True
        assert subscription is not None
        assert subscription.provider == "creem"
        assert subscription.status == SubscriptionStatus.ACTIVE
        assert prepared.attempt.status == "completed"
        assert prepared.attempt.billing_profile["country"] == "Egypt"
        assert await session.scalar(select(func.count(PaymentEmailDelivery.id))) == 1


async def test_creem_trial_checkout_is_closed_in_favor_of_paid_monthly(test_context):
    settings = Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        billing_enabled=True,
        billing_card_provider="creem",
        creem_api_key="creem-test-key",
        creem_webhook_secret="creem-webhook-secret",
        creem_product_ids={"trader_trial": "prod_monitor_trial"},
    )
    async with test_context["session_factory"]() as session:
        user = await create_user(session, "Creem trial")
        service = BillingService(session, settings)
        with pytest.raises(BillingError) as error:
            await service.prepare_checkout(
                user_id=user.id,
                plan_code="trader",
                billing_cycle="trial_7_day",
                request_key="creem-trial-order",
                terms_accepted=True,
            )

        assert error.value.code == "billing_cycle_not_available"
        assert await session.scalar(
            select(func.count(BillingCheckoutAttempt.id))
        ) == 0


async def test_one_time_access_expires_at_verified_period_end(test_context):
    settings = Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        billing_provider="nowpayments",
        billing_card_provider="disabled",
        billing_crypto_provider="nowpayments",
    )
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        user = await create_user(session, "Expired access")
        plan = await PlanCatalogService(session).get_or_sync("pro")
        subscription = Subscription(
            user_id=user.id,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            provider="nowpayments",
            provider_subscription_id="nowpayments_expired_access",
            current_period_start=now - timedelta(days=31),
            current_period_end=now - timedelta(seconds=1),
            cancel_at_period_end=True,
        )
        session.add(subscription)
        await session.flush()

        expired = await BillingService(session, settings).expire_ended_access(now=now)

        assert expired == 1
        assert subscription.status == SubscriptionStatus.EXPIRED
        entitlement = await EntitlementService(session).current(user.id)
        assert entitlement.plan.code == "demo"


@pytest.mark.parametrize(
    ("amount", "currency", "expected_code"),
    [
        (
            f"{MONITOR_CHECKOUT_AMOUNT - Decimal('0.01'):.2f}",
            "USD",
            "payment_underpaid",
        ),
        (
            f"{MONITOR_CHECKOUT_AMOUNT + Decimal('0.01'):.2f}",
            "USD",
            "payment_overpaid",
        ),
        (MONITOR_CHECKOUT_AMOUNT_TEXT, "EUR", "payment_currency_mismatch"),
    ],
)
async def test_verified_payment_must_match_checkout_amount_and_currency(
    test_context,
    amount,
    currency,
    expected_code,
):
    settings = _crypto_only_server(
        billing_payment_amount_tolerance_percent=0,
        billing_allow_overpayment=False,
    )
    async with test_context["session_factory"]() as session:
        user = await create_user(session, f"Mismatch {expected_code}")
        service = BillingService(session, settings)
        prepared = await service.prepare_checkout(
            user_id=user.id,
            plan_code="trader",
            billing_cycle="one_time_30_day",
            request_key=f"mismatch-{expected_code}",
            terms_accepted=True,
        )
        payload = {
            "id": f"evt-{expected_code}",
            "type": "payment.finished",
            "data": {
                "checkout_attempt_id": str(prepared.attempt.id),
                "user_id": str(user.id),
                "plan_code": "trader",
                "provider_subscription_id": f"sub-{expected_code}",
                "status": "active",
                "amount": amount,
                "currency": currency,
            },
        }

        with pytest.raises(BillingError) as error:
            await service.process_event(provider="nowpayments", payload=payload)

        assert error.value.code == expected_code


async def test_refund_revokes_an_active_subscription(test_context):
    async with test_context["session_factory"]() as session:
        user = await create_user(session, "Refunded user")
        service = BillingService(session, test_context["settings"])
        common = {
            "user_id": str(user.id),
            "plan_code": "pro",
            "provider_customer_id": "cus_refund",
            "provider_subscription_id": "sub_refund",
        }
        await service.process_event(
            provider="stripe",
            payload={
                "id": "evt_refund_create",
                "type": "customer.subscription.created",
                "data": {**common, "status": "active"},
            },
        )
        await service.process_event(
            provider="stripe",
            payload={
                "id": "evt_refund",
                "type": "payment.refunded",
                "data": common,
            },
        )

        subscription = await session.scalar(
            select(Subscription).where(
                Subscription.provider_subscription_id == "sub_refund"
            )
        )
        assert subscription is not None
        assert subscription.status == SubscriptionStatus.CANCELED
        assert subscription.canceled_at is not None


async def test_nowpayments_webhook_sends_single_admin_payment_notification(
    test_context,
    monkeypatch,
):
    test_context["settings"].billing_webhook_secret = SecretStr("secret")
    sent = []

    async def fake_payment_notice(
        self,
        *,
        user_id,
        email,
        plan_code,
        provider,
        event_type,
    ):
        sent.append(
            {
                "user_id": user_id,
                "email": email,
                "plan_code": plan_code,
                "provider": provider,
                "event_type": event_type,
            }
        )

    monkeypatch.setattr(
        AdminNotificationService,
        "send_payment_received",
        fake_payment_notice,
    )
    async with test_context["session_factory"]() as session:
        user = await create_user(session, "Paid User")
        session.add(
            UserIdentity(
                user_id=user.id,
                provider=IdentityProvider.EMAIL,
                provider_subject="paid@example.com",
                normalized_identifier="paid@example.com",
                display_identifier="paid@example.com",
                is_verified=True,
                is_primary=True,
                verified_at=datetime.now(UTC),
                profile_data={},
            )
        )
        nowpayments_settings = _crypto_only_server()
        prepared = await BillingService(session, nowpayments_settings).prepare_checkout(
            user_id=user.id,
            plan_code="trader",
            billing_cycle="monthly",
            request_key="admin-payment-notice",
            terms_accepted=True,
        )
        attempt_id = prepared.attempt.id
        await session.commit()

    test_context["app"].dependency_overrides[get_settings] = lambda: nowpayments_settings

    payload = {
        "payment_id": "pay_admin_notice",
        "payment_status": "finished",
        "order_id": f"hm|{attempt_id}|trader",
        "price_amount": MONITOR_CHECKOUT_AMOUNT_TEXT,
        "price_currency": "USD",
        "pay_amount": "100.00",
        "actually_paid": "100.00",
        "pay_currency": "USDT",
    }
    body = json.dumps(payload).encode()
    signed_body = json.dumps(
        {
            "actually_paid": payload["actually_paid"],
            "order_id": payload["order_id"],
            "pay_amount": payload["pay_amount"],
            "pay_currency": payload["pay_currency"],
            "payment_id": payload["payment_id"],
            "payment_status": payload["payment_status"],
            "price_amount": payload["price_amount"],
            "price_currency": payload["price_currency"],
        },
        separators=(",", ":"),
    ).encode()
    from hashlib import sha512

    signature = hmac.new(b"secret", signed_body, sha512).hexdigest()

    first = await test_context["client"].post(
        "/api/v1/billing/webhooks/nowpayments",
        content=body,
        headers={"x-nowpayments-sig": signature},
    )
    replay = await test_context["client"].post(
        "/api/v1/billing/webhooks/nowpayments",
        content=body,
        headers={"x-nowpayments-sig": signature},
    )

    assert first.status_code == 200, first.text
    assert first.json()["replayed"] is False
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert sent == [
        {
            "user_id": user.id,
            "email": "paid@example.com",
            "plan_code": "trader",
            "provider": "nowpayments",
            "event_type": "payment.finished",
        }
    ]


async def test_past_due_subscription_update_does_not_convert_trial(test_context):
    async with test_context["session_factory"]() as session:
        user = await create_user(session)
        trial = await TrialLifecycleService(session, test_context["settings"]).activate(user.id)
        await BillingService(session, test_context["settings"]).process_event(
            provider="stripe",
            payload={
                "id": "evt_past_due",
                "type": "customer.subscription.updated",
                "data": {
                    "user_id": str(user.id),
                    "plan_code": "pro",
                    "provider_customer_id": "cus_past_due",
                    "provider_subscription_id": "sub_past_due",
                    "status": "past_due",
                },
            },
        )
        assert trial.status == TrialStatus.ELIGIBLE


async def test_usage_tracking_referrals_and_admin_overrides(test_context):
    async with test_context["session_factory"]() as session:
        user = await create_user(session, "Usage")
        referrer = await create_user(session, "Referrer")
        now = datetime.now(UTC)
        first = await UsageService(session).record(
            user.id,
            "alerts_generated",
            period_start=now,
            period_end=now + timedelta(days=1),
            idempotency_key="alert-1",
        )
        second = await UsageService(session).record(
            user.id,
            "alerts_generated",
            period_start=now,
            period_end=now + timedelta(days=1),
            idempotency_key="alert-1",
        )
        assert first.id == second.id
        assert await session.scalar(select(func.count(UsageRecord.id))) == 1

        code = ReferralCode(owner_user_id=referrer.id, code="CREATOR10", is_active=True)
        session.add(code)
        await session.flush()
        relationship = await ReferralService(session).record_trial_referral(
            referred_user_id=user.id,
            referral_code="CREATOR10",
        )
        assert relationship is not None
        assert relationship.reward_status == "pending_paid_conversion"
        with pytest.raises(ReferralError):
            await ReferralService(session).record_trial_referral(
                referred_user_id=referrer.id,
                referral_code="CREATOR10",
            )

        admin = await create_user(session, "Admin")
        override = await AdminCommercialService(session).record_override(
            admin_user_id=admin.id,
            target_user_id=user.id,
            override_type="limit_adjustment",
            reason="Support-approved beta limit increase",
            payload={"active_strategies": 5},
        )
        assert override.id is not None
        assert await session.scalar(select(func.count(AdminOverride.id))) == 1
        assert await session.scalar(select(func.count(AuditEvent.id))) >= 1
        assert await session.scalar(select(func.count(ReferralRelationship.id))) == 1


async def test_a_referral_records_what_was_charged_not_the_list_price(test_context):
    """An affiliate's share is a share of money that really moved.

    While a launch offer runs, the plan's catalogue price and the price the customer
    paid are two different numbers. This used to write the catalogue price, so an
    affiliate would have been credited a share of $20 for somebody who paid the launch
    price. The completed checkout is the only row that knows what was actually charged.
    """

    async with test_context["session_factory"]() as session:
        await PlanCatalogService(session).sync_defaults()
        buyer = await create_user(session, "Referred Buyer")
        referrer = await create_user(session, "Referrer")
        plan = await session.scalar(select(Plan).where(Plan.code == "trader"))
        assert plan is not None

        session.add(ReferralCode(owner_user_id=referrer.id, code="LAUNCH10", is_active=True))
        await session.flush()
        await ReferralService(session).record_trial_referral(
            referred_user_id=buyer.id,
            referral_code="LAUNCH10",
        )

        charged = effective_monthly_price("trader")
        now = datetime.now(UTC)
        session.add(
            BillingCheckoutAttempt(
                user_id=buyer.id,
                plan_id=plan.id,
                billing_cycle="monthly",
                provider="static",
                status="completed",
                idempotency_key=uuid4().hex,
                terms_version="v1",
                amount=charged,
                currency="USD",
                terms_accepted_at=now,
                expires_at=now + timedelta(hours=1),
                completed_at=now,
            )
        )
        session.add(
            Subscription(
                user_id=buyer.id,
                plan_id=plan.id,
                status=SubscriptionStatus.ACTIVE,
                provider="static",
            )
        )
        await session.flush()

        relationship = await ReferralService(session).grant_conversion_rewards(
            referred_user_id=buyer.id
        )
        assert relationship is not None
        assert relationship.metadata_json["paid_amount_usd"] == str(charged)
        assert relationship.metadata_json["paid_amount_source"] == "checkout"
        # And it is the charged number, not the one printed in the plan catalogue.
        assert relationship.metadata_json["paid_amount_usd"] != str(plan.price_monthly) or (
            charged == plan.price_monthly
        )


async def test_a_referral_with_no_payment_row_says_where_its_number_came_from(test_context):
    """A subscription granted by hand has no payment behind it, and says so.

    Falling back silently to the plan's price is how a balance comes to show money that
    was never received. The row records which of the two numbers it used.
    """

    async with test_context["session_factory"]() as session:
        await PlanCatalogService(session).sync_defaults()
        buyer = await create_user(session, "Granted Buyer")
        referrer = await create_user(session, "Granting Referrer")
        plan = await session.scalar(select(Plan).where(Plan.code == "trader"))
        assert plan is not None

        session.add(ReferralCode(owner_user_id=referrer.id, code="GRANTED10", is_active=True))
        await session.flush()
        await ReferralService(session).record_trial_referral(
            referred_user_id=buyer.id,
            referral_code="GRANTED10",
        )
        session.add(
            Subscription(
                user_id=buyer.id,
                plan_id=plan.id,
                status=SubscriptionStatus.ACTIVE,
                provider=None,
            )
        )
        await session.flush()

        relationship = await ReferralService(session).grant_conversion_rewards(
            referred_user_id=buyer.id
        )
        assert relationship is not None
        assert relationship.metadata_json["paid_amount_source"] == "plan_price"
        assert relationship.metadata_json["paid_amount_usd"] == str(plan.price_monthly)
