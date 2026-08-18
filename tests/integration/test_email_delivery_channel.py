"""Email must be a delivery channel of equal standing, not a side path.

The same queue, the same retry, the same failure codes and the same trail behind "why
was I not told". A channel that behaves differently when something goes wrong is a
channel nobody can debug from the outside — and the person on the other end of it simply
stops hearing anything.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from ai_market_monitor.db.models import (
    AccountEmailDelivery,
    Alert,
    AlertDelivery,
    DashboardPreference,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import (
    AlertType,
    DeliveryChannel,
    DeliveryStatus,
    IdentityProvider,
)
from ai_market_monitor.services.account_emails import AccountEmailOutboxService
from ai_market_monitor.services.alert_emails import (
    EmailAlertDeliveryService,
    alert_email_address,
)
from ai_market_monitor.services.email_delivery import EmailDeliveryError
from ai_market_monitor.services.notifications import NotificationDispatcher
from tests.integration.test_dashboard_web import _signup_and_verify

ADDRESS = "trader@example.com"


async def _account(
    session,
    *,
    verified: bool = True,
    address: str = ADDRESS,
    wants_email: bool = True,
) -> User:
    """An account, with an email address and a choice about whether to be told there.

    `wants_email` is not a convenience. Email is off until a person turns it on, so a
    fixture that skipped the choice would be testing a channel nobody had asked for.
    """

    user = User(display_name="Trader", locale="en", timezone="UTC")
    session.add(user)
    await session.flush()
    session.add(
        UserIdentity(
            user_id=user.id,
            provider=IdentityProvider.EMAIL,
            provider_subject=address,
            normalized_identifier=address,
            display_identifier=address,
            is_verified=verified,
            is_primary=True,
            verified_at=datetime.now(UTC) if verified else None,
        )
    )
    chosen = ["web", "email"] if wants_email else ["web"]
    session.add(
        DashboardPreference(
            user_id=user.id,
            default_timezone="UTC",
            theme="light",
            notification_preferences={"alert_channels": chosen, "channels": chosen},
        )
    )
    await session.flush()
    return user


async def _alert(session, user: User) -> Alert:
    alert = Alert(
        user_id=user.id,
        alert_type=AlertType.CONFIRMED,
        deduplication_key=f"email-channel-{uuid4().hex}",
        title="SOL/USDT confirmed",
        body="Every condition became true.",
        proof_receipt={"symbol": "SOL/USDT", "setup_completion_score": 100},
        candle_timestamp=datetime.now(UTC),
    )
    session.add(alert)
    await session.flush()
    return alert


# ── Where it goes ────────────────────────────────────────────────────────────


async def test_an_alert_email_goes_to_the_confirmed_address(test_context):
    async with test_context["session_factory"]() as session:
        user = await _account(session)
        assert await alert_email_address(session, user.id) == ADDRESS


async def test_an_unconfirmed_address_never_receives_an_alert(test_context):
    """An address somebody typed is not an address they have been shown to control.
    Alerts name the coins a person watches, so sending them to an unproven address hands
    their activity to whoever owns it."""

    async with test_context["session_factory"]() as session:
        user = await _account(session, verified=False)
        assert await alert_email_address(session, user.id) is None


# ── Getting into the queue ───────────────────────────────────────────────────


async def test_choosing_email_puts_an_alert_in_the_queue(test_context):
    async with test_context["session_factory"]() as session:
        user = await _account(session)
        alert = await _alert(session, user)

        deliveries = await NotificationDispatcher(
            session, test_context["settings"]
        ).enqueue_user_alert(alert, channels=[DeliveryChannel.EMAIL])

        email = [item for item in deliveries if item.channel == DeliveryChannel.EMAIL]
        assert len(email) == 1
        assert email[0].destination_key == f"email:{ADDRESS}"
        assert email[0].status == DeliveryStatus.PENDING


async def test_nothing_is_queued_for_somebody_who_did_not_choose_email(test_context):
    """Email is off until a person turns it on. Sending to an address the platform
    happens to hold, because it happens to hold it, is not a notification — it is mail
    nobody asked for, from a product whose whole promise is that it asks first."""

    async with test_context["session_factory"]() as session:
        user = await _account(session, wants_email=False)
        alert = await _alert(session, user)

        deliveries = await NotificationDispatcher(
            session, test_context["settings"]
        ).enqueue_user_alert(alert)

        assert [item for item in deliveries if item.channel == DeliveryChannel.EMAIL] == []


async def test_nothing_is_queued_for_an_account_with_no_confirmed_address(test_context):
    """A row nothing can ever deliver is a row nothing will ever clear."""

    async with test_context["session_factory"]() as session:
        user = await _account(session, verified=False)
        alert = await _alert(session, user)

        deliveries = await NotificationDispatcher(
            session, test_context["settings"]
        ).enqueue_user_alert(alert, channels=[DeliveryChannel.EMAIL])

        assert [item for item in deliveries if item.channel == DeliveryChannel.EMAIL] == []


async def test_asking_twice_does_not_queue_the_same_alert_twice(test_context):
    async with test_context["session_factory"]() as session:
        user = await _account(session)
        alert = await _alert(session, user)
        dispatcher = NotificationDispatcher(session, test_context["settings"])

        await dispatcher.enqueue_user_alert(alert, channels=[DeliveryChannel.EMAIL])
        await dispatcher.enqueue_user_alert(alert, channels=[DeliveryChannel.EMAIL])

        rows = (
            await session.scalars(
                select(AlertDelivery).where(
                    AlertDelivery.alert_id == alert.id,
                    AlertDelivery.channel == DeliveryChannel.EMAIL,
                )
            )
        ).all()
        assert len(rows) == 1


# ── Sending it ───────────────────────────────────────────────────────────────


async def test_a_queued_email_is_sent_and_recorded(test_context):
    settings = test_context["settings"]
    settings.email_test_outbox.clear()

    async with test_context["session_factory"]() as session:
        user = await _account(session)
        alert = await _alert(session, user)
        await NotificationDispatcher(session, settings).enqueue_user_alert(
            alert, channels=[DeliveryChannel.EMAIL]
        )
        await session.commit()

        processed = await EmailAlertDeliveryService(session, settings).process_due()
        await session.commit()

        assert len(processed) == 1
        assert processed[0].status == DeliveryStatus.SENT
        assert processed[0].delivered_at is not None
        assert processed[0].provider_message_id

    sent = [item for item in settings.email_test_outbox if item["purpose"] == "alert"]
    assert len(sent) == 1
    assert sent[0]["recipient"] == ADDRESS
    assert sent[0]["sender"] == "no-reply@hilalmarkets.com"
    assert "SOL/USDT" in sent[0]["subject"]
    assert sent[0]["body"].strip(), "an alert email with no plain-text part"


async def test_a_sent_email_is_not_sent_again(test_context):
    settings = test_context["settings"]
    settings.email_test_outbox.clear()

    async with test_context["session_factory"]() as session:
        user = await _account(session)
        alert = await _alert(session, user)
        await NotificationDispatcher(session, settings).enqueue_user_alert(
            alert, channels=[DeliveryChannel.EMAIL]
        )
        await session.commit()

        service = EmailAlertDeliveryService(session, settings)
        await service.process_due()
        await session.commit()
        again = await service.process_due()
        await session.commit()

        assert again == []
    assert len([item for item in settings.email_test_outbox if item["purpose"] == "alert"]) == 1


# ── When it fails ────────────────────────────────────────────────────────────


class _Refusing:
    """A sender that always fails, the way the real one reports failure."""

    def __init__(self, *, retryable: bool):
        self.retryable = retryable
        self.attempts = 0

    async def send_transactional(self, **_kwargs):
        self.attempts += 1
        raise EmailDeliveryError(
            "The provider refused it.",
            code="smtp_provider_temporary" if self.retryable else "smtp_message_rejected",
            retryable=self.retryable,
        )


@pytest.mark.parametrize(
    ("retryable", "expected"),
    [
        (True, DeliveryStatus.FAILED_RETRYABLE),
        (False, DeliveryStatus.FAILED_PERMANENT),
    ],
)
async def test_a_failure_is_recorded_the_same_way_telegram_records_one(
    test_context, retryable: bool, expected: DeliveryStatus
):
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        user = await _account(session)
        alert = await _alert(session, user)
        await NotificationDispatcher(session, settings).enqueue_user_alert(
            alert, channels=[DeliveryChannel.EMAIL]
        )
        await session.commit()

        processed = await EmailAlertDeliveryService(
            session, settings, _Refusing(retryable=retryable)
        ).process_due()
        await session.commit()

        assert len(processed) == 1
        delivery = processed[0]
        assert delivery.status == expected
        assert delivery.last_error_code
        assert delivery.delivered_at is None
        # A retryable failure comes back later; a permanent one does not.
        assert (delivery.next_retry_at is not None) is retryable


async def test_a_retryable_failure_gives_up_after_five_tries(test_context):
    """The same five as Telegram. One number for "we tried enough" across the product,
    so a person cannot find one channel still retrying an alert the other abandoned."""

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        user = await _account(session)
        alert = await _alert(session, user)
        await NotificationDispatcher(session, settings).enqueue_user_alert(
            alert, channels=[DeliveryChannel.EMAIL]
        )
        await session.commit()

        sender = _Refusing(retryable=True)
        service = EmailAlertDeliveryService(session, settings, sender)
        delivery = None
        for _ in range(5):
            processed = await service.process_due()
            if not processed:
                break
            delivery = processed[0]
            # Due again straight away, so the loop can reach the limit.
            delivery.next_retry_at = datetime.now(UTC)
            await session.commit()

        assert sender.attempts == 5
        assert delivery is not None
        assert delivery.status == DeliveryStatus.FAILED_PERMANENT
        assert delivery.next_retry_at is None or delivery.attempt_count >= 5


# ── The welcome ──────────────────────────────────────────────────────────────


async def test_finishing_signup_queues_the_welcome_email(test_context):
    """A template nothing sends is not a feature.

    It is queued rather than sent inside the sign-up, so a mail provider having a bad
    minute cannot fail the sign-up itself. The account is made either way.
    """

    settings = test_context["settings"]
    settings.email_test_outbox.clear()
    await _signup_and_verify(test_context, email="welcome@example.com")

    async with test_context["session_factory"]() as session:
        queued = (
            await session.scalars(
                select(AccountEmailDelivery).where(
                    AccountEmailDelivery.template_kind == "signup_welcome"
                )
            )
        ).all()
        assert len(queued) == 1
        assert queued[0].recipient == "welcome@example.com"
        # Keyed on the account, so it can only ever be sent once.
        assert queued[0].event_key.endswith(str(queued[0].user_id))

        result = await AccountEmailOutboxService(session, settings).process_due()
        assert result["sent"] == 1

    sent = [
        item
        for item in settings.email_test_outbox
        if item.get("purpose") == "account_access_changed"
    ]
    assert sent, "the welcome email was queued but never sent"
    assert sent[-1]["subject"] == "Your Hilal Markets account is ready"
    assert "Here is what to do first" in sent[-1]["html_body"]
    assert sent[-1]["body"].strip(), "the welcome email has no plain-text part"


async def test_nothing_is_attempted_when_the_platform_cannot_send_email(test_context):
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        user = await _account(session)
        alert = await _alert(session, user)
        await NotificationDispatcher(session, settings).enqueue_user_alert(
            alert, channels=[DeliveryChannel.EMAIL]
        )
        await session.commit()

        settings.email_adapter = "none"
        try:
            assert await EmailAlertDeliveryService(session, settings).process_due() == []
        finally:
            settings.email_adapter = "memory"
