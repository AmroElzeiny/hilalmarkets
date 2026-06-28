import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import PLAN_DEFINITIONS
from ai_market_monitor.db.models import (
    AdminOverride,
    Alert,
    AlertDelivery,
    AuditEvent,
    BillingEvent,
    EntitlementSnapshot,
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
)
from ai_market_monitor.db.models.enums import (
    AlertType,
    ConnectionStatus,
    DeliveryChannel,
    DeliveryStatus,
    StrategyStatus,
    StrategyVersionStatus,
    SubscriptionStatus,
    TrialStatus,
)
from ai_market_monitor.services.admin import AdminCommercialService
from ai_market_monitor.services.billing import BillingError, BillingService, BillingWebhookVerifier
from ai_market_monitor.services.entitlements import (
    EntitlementError,
    EntitlementService,
    PlanCatalogService,
    UsageService,
)
from ai_market_monitor.services.referrals import ReferralError, ReferralService
from ai_market_monitor.services.trials import TrialLifecycleService
from tests.factories import load_strategy


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
        assert pro.discord_enabled is True
        assert pro.features["limits"]["active_strategies"] == 10


async def test_trial_lifecycle_uses_pro_trial_and_expires(test_context):
    settings = Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        trial_days=14,
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
        assert (cycle.ends_at - cycle.starts_at).days == 14
        expired = await TrialLifecycleService(session, settings).expire_due(
            now=cycle.ends_at + timedelta(seconds=1)
        )
        assert expired == [trial]
        assert trial.status == TrialStatus.EXPIRED


async def test_trial_cycle_renews_when_no_qualifying_alert_was_delivered(test_context):
    settings = Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        trial_days=14,
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
        renewed = await trial_service.expire_due(now=first_cycle.ends_at + timedelta(seconds=1))
        assert renewed == [trial]
        assert trial.status == TrialStatus.ACTIVE
        assert first_cycle.status == "renewed"
        assert first_cycle.renewal_decision == "auto_renewed_no_qualifying_alert"
        second_cycle = await session.scalar(
            select(TrialCycle).where(
                TrialCycle.trial_id == trial.id,
                TrialCycle.cycle_number == 2,
            )
        )
        assert second_cycle is not None
        assert trial.ends_at == second_cycle.ends_at


async def test_trial_qualifying_delivery_is_attributed_once(test_context):
    settings = Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        trial_days=14,
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
        trial_days=14,
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
        trial_days=14,
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
        for index in range(3):
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
        with pytest.raises(EntitlementError, match="Plan allows 3 active"):
            await EntitlementService(session).enforce_strategy_activation(user.id, definition)

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
        assert discord_error.value.code == "discord_not_included"


async def test_lifetime_plan_removes_practical_activation_and_delivery_limits(test_context):
    async with test_context["session_factory"]() as session:
        user = await create_user(session, "Lifetime User")
        await activate_subscription(session, user.id, "lifetime")
        definition = load_strategy()
        definition.universe.include_symbols = [f"COIN{i}/USDT" for i in range(500)]
        definition.supporting_timeframes = ["1m", "4h"]
        definition.alerts.channels = ["telegram", "discord"]

        context = await EntitlementService(session).enforce_strategy_activation(
            user.id,
            definition,
        )

        assert context.plan.code == "lifetime"
        assert context.feature_enabled("discord") is True
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

        for index in range(4):
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
        assert active_count == 3
        assert paused_count == 1


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
    settings = Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        billing_webhook_secret="secret",
    )
    async with test_context["session_factory"]() as session:
        user = await create_user(session)
        payload = {
            "payment_id": "pay_456",
            "payment_status": "finished",
            "order_id": f"amm|{user.id}|pro|abc123",
        }
        body = json.dumps(payload).encode()
        signed_body = json.dumps(
            {
                "order_id": payload["order_id"],
                "payment_id": payload["payment_id"],
                "payment_status": payload["payment_status"],
            },
            separators=(",", ":"),
        ).encode()
        from hashlib import sha512

        signature = hmac.new(b"secret", signed_body, sha512).hexdigest()
        result = await BillingService(session, settings).process_verified_webhook(
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
