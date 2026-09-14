"""Removing a Telegram always works, keeps the legal record, and silences the old chat.

Three rules, each asked of every path that can produce the failure:

* **a Telegram can always be removed** — by the Connections page form, by the page's own
  request, and by linking a different Telegram in the bot. The owner could not unlink at
  all: the risk-note acceptance had been given inside the bot, it pointed at the Telegram
  sign-in with ``RESTRICT``, and PostgreSQL refused the delete. SQLite does not enforce
  foreign keys unless asked, so every row here asks first — without that, these tests
  pass on the very schema that broke in production;
* **the acceptance outlives the sign-in** and still says which sign-in gave it;
* **a queued alert only reaches a chat still connected to the alert's owner**, in every
  state a chat can leave that account by, and for every kind of queued row the sender
  picks up.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select, text

from ai_market_monitor.api.routers.telegram import telegram_polling_offset
from ai_market_monitor.core.csrf import csrf_token
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    DisclaimerAcceptance,
    TelegramConnection,
    TelegramConversationState,
    TelegramUpdateReceipt,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import (
    AlertType,
    ConnectionStatus,
    DeliveryChannel,
    DeliveryStatus,
    IdentityProvider,
)
from ai_market_monitor.services.alert_limits import TELEGRAM_CHAT_NOT_CONNECTED
from ai_market_monitor.services.notifications import TelegramDeliveryService
from ai_market_monitor.services.risk_disclaimer import has_accepted, record_acceptance
from ai_market_monitor.services.telegram_account_links import TelegramAccountLinkService
from ai_market_monitor.telegram.adapter import TelegramDeliveryResult
from ai_market_monitor.telegram.types import TelegramOutboundMessage
from tests.integration.test_dashboard_test_connections import CONNECTIONS, _csrf
from tests.integration.test_dashboard_web import _signup_and_verify
from tests.integration.test_telegram_connect_flow import _start_link

OLD_TELEGRAM = "tg-old-unlink"
OLD_CHAT = "chat-old-unlink"
NEW_TELEGRAM = "tg-new-unlink"
NEW_CHAT = "chat-new-unlink"


async def _enforce_foreign_keys(test_context: dict) -> None:
    """Make the in-memory database refuse what PostgreSQL refuses."""

    async with test_context["session_factory"]() as session:
        await session.execute(text("PRAGMA foreign_keys=ON"))
        await session.commit()
        assert (await session.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1


async def _telegram_with_disclaimer_accepted_in_bot(test_context: dict, email: str) -> UUID:
    await _signup_and_verify(test_context, email=email)
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User).order_by(User.created_at.desc()))
        assert user is not None
        identity = UserIdentity(
            user_id=user.id,
            provider=IdentityProvider.TELEGRAM,
            provider_subject=OLD_TELEGRAM,
            display_identifier="@old",
            is_verified=True,
            is_primary=False,
            profile_data={},
        )
        session.add_all(
            [
                identity,
                TelegramConnection(
                    user_id=user.id,
                    telegram_user_id=OLD_TELEGRAM,
                    chat_id=OLD_CHAT,
                    username="old",
                    status=ConnectionStatus.ACTIVE,
                    connected_at=datetime.now(UTC),
                ),
                TelegramConversationState(
                    user_id=user.id,
                    telegram_user_id=OLD_TELEGRAM,
                    chat_id=OLD_CHAT,
                    flow="main_menu",
                    step="idle",
                    state_data={},
                    correlation_id="unlink-old",
                ),
            ]
        )
        await session.flush()
        await record_acceptance(
            session,
            user_id=user.id,
            version=settings.disclaimer_version,
            source="telegram",
            identity_id=identity.id,
        )
        await session.commit()
        record = await session.scalar(
            select(DisclaimerAcceptance).where(DisclaimerAcceptance.user_id == user.id)
        )
        assert record is not None and record.identity_id == identity.id, (
            "the acceptance this test is about was not tied to the Telegram sign-in"
        )
        return user.id


async def _remove_by(test_context: dict, door: str, user_id: UUID) -> None:
    client = test_context["client"]
    settings = test_context["settings"]
    if door == "connections_form":
        page = await client.get(f"{CONNECTIONS}?confirm_unlink=telegram")
        response = await client.post(
            f"{CONNECTIONS}/telegram/unlink",
            data={"csrf_token": _csrf(page.text)},
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text[:400]
        return
    if door == "connections_request":
        response = await client.delete(
            "/api/v1/dashboard/integrations/telegram",
            headers={"X-CSRF-Token": csrf_token(settings, user_id)},
        )
        assert response.status_code == 200, response.text[:400]
        return
    if door == "new_telegram_linked_in_bot":
        settings.telegram_bot_username = "trace_edge_bot"
        async with test_context["session_factory"]() as session:
            token = await _start_link(session, settings, user_id)
            await TelegramAccountLinkService(session, settings).complete_dashboard_start_link(
                raw_token=token,
                telegram_user_id=NEW_TELEGRAM,
                chat_id=NEW_CHAT,
                username="new",
            )
            await session.commit()
        return
    raise AssertionError(f"unknown door {door!r}")  # pragma: no cover


@pytest.mark.parametrize(
    "door", ["connections_form", "connections_request", "new_telegram_linked_in_bot"]
)
async def test_a_telegram_that_accepted_the_risk_note_can_always_be_removed(
    test_context: dict, door: str
) -> None:
    user_id = await _telegram_with_disclaimer_accepted_in_bot(
        test_context, f"unlink-{door.replace('_', '-')}@example.com"
    )
    await _enforce_foreign_keys(test_context)

    await _remove_by(test_context, door, user_id)

    async with test_context["session_factory"]() as session:
        assert await session.scalar(
            select(TelegramConnection).where(TelegramConnection.telegram_user_id == OLD_TELEGRAM)
        ) is None, "the old Telegram is still connected"
        assert await session.scalar(
            select(UserIdentity).where(
                UserIdentity.provider == IdentityProvider.TELEGRAM,
                UserIdentity.provider_subject == OLD_TELEGRAM,
            )
        ) is None, "the old Telegram can still act as this account"
        assert await session.scalar(
            select(TelegramConversationState).where(
                TelegramConversationState.telegram_user_id == OLD_TELEGRAM
            )
        ) is None, "the old chat still holds a conversation for this account"

        record = await session.scalar(
            select(DisclaimerAcceptance).where(DisclaimerAcceptance.user_id == user_id)
        )
        assert record is not None, "removing Telegram deleted a legal record"
        assert record.identity_id is None
        assert (record.identity_provider, record.identity_subject) == ("telegram", OLD_TELEGRAM)
        assert await has_accepted(
            session, user_id=user_id, version=test_context["settings"].disclaimer_version
        )

        if door == "new_telegram_linked_in_bot":
            new = await session.scalar(
                select(TelegramConnection).where(TelegramConnection.user_id == user_id)
            )
            assert new is not None and new.telegram_user_id == NEW_TELEGRAM


async def test_a_telegram_taken_over_by_another_account_leaves_the_old_record_intact(
    test_context: dict,
) -> None:
    """The newest link wins, and the previous holder's acceptance stays theirs."""

    first = await _telegram_with_disclaimer_accepted_in_bot(test_context, "holder-a@example.com")
    await _signup_and_verify(test_context, email="holder-b@example.com")
    await _enforce_foreign_keys(test_context)
    settings = test_context["settings"]
    settings.telegram_bot_username = "trace_edge_bot"
    async with test_context["session_factory"]() as session:
        second = await session.scalar(select(User).order_by(User.created_at.desc()))
        assert second is not None and second.id != first
        token = await _start_link(session, settings, second.id)
        await TelegramAccountLinkService(session, settings).complete_dashboard_start_link(
            raw_token=token,
            telegram_user_id=OLD_TELEGRAM,
            chat_id=OLD_CHAT,
            username="old",
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        connection = await session.scalar(
            select(TelegramConnection).where(TelegramConnection.telegram_user_id == OLD_TELEGRAM)
        )
        assert connection is not None and connection.user_id == second.id
        assert await session.scalar(
            select(TelegramConnection).where(TelegramConnection.user_id == first)
        ) is None, "the account that lost the Telegram still has a connection"
        record = await session.scalar(
            select(DisclaimerAcceptance).where(DisclaimerAcceptance.user_id == first)
        )
        assert record is not None, "the takeover deleted the other account's legal record"
        assert record.identity_id is None, "the record still points at a sign-in it lost"
        assert (record.identity_provider, record.identity_subject) == ("telegram", OLD_TELEGRAM)


async def test_an_acceptance_without_a_named_sign_in_copies_the_primary_one(
    test_context: dict,
) -> None:
    await _signup_and_verify(test_context, email="disclaimer-copy@example.com")
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User).order_by(User.created_at.desc()))
        assert user is not None
        await record_acceptance(
            session,
            user_id=user.id,
            version=test_context["settings"].disclaimer_version,
            source="web",
        )
        await session.commit()
        record = await session.scalar(
            select(DisclaimerAcceptance).where(DisclaimerAcceptance.user_id == user.id)
        )
        assert record is not None
        assert record.identity_provider == "email"
        assert record.identity_subject


# ---------------------------------------------------------------------------
# Queued alerts and the chat they were queued for
# ---------------------------------------------------------------------------


class RecordingAdapter:
    def __init__(self) -> None:
        self.sent: list[TelegramOutboundMessage] = []

    async def deliver(self, message: TelegramOutboundMessage) -> TelegramDeliveryResult:
        self.sent.append(message)
        return TelegramDeliveryResult(message_ids=[f"message-{len(self.sent)}"])


CHAT_STATES = (
    "still_connected",
    "unlinked",
    "replaced_by_new_telegram",
    "moved_to_another_account",
    "alerts_turned_off",
    "revoked",
)
QUEUED_KINDS = ("pending", "retry_due", "sent_without_message_id")


@pytest.mark.parametrize("queued", QUEUED_KINDS)
@pytest.mark.parametrize("state", CHAT_STATES)
async def test_a_queued_alert_only_reaches_a_chat_still_connected_to_its_owner(
    test_context: dict, state: str, queued: str
) -> None:
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        owner = User(display_name="Owner")
        other = User(display_name="Other")
        session.add_all([owner, other])
        await session.flush()
        connection = TelegramConnection(
            user_id=owner.id,
            telegram_user_id="tg-owner",
            chat_id="chat-owner",
            username="owner",
            status=ConnectionStatus.ACTIVE,
            alerts_enabled=True,
            connected_at=now,
        )
        alert = Alert(
            user_id=owner.id,
            alert_type=AlertType.CONFIRMED,
            deduplication_key=f"routing-{state}-{queued}",
            title="BTC/USDT setup confirmed",
            body="All conditions are met.",
            proof_receipt={},
        )
        session.add_all([connection, alert])
        await session.flush()
        delivery = AlertDelivery(
            alert_id=alert.id,
            channel=DeliveryChannel.TELEGRAM,
            destination_key="chat:chat-owner",
            status={
                "pending": DeliveryStatus.PENDING,
                "retry_due": DeliveryStatus.FAILED_RETRYABLE,
                "sent_without_message_id": DeliveryStatus.SENT,
            }[queued],
            next_retry_at=now - timedelta(minutes=1) if queued == "retry_due" else None,
        )
        session.add(delivery)
        await session.flush()

        if state == "unlinked":
            await session.delete(connection)
        elif state == "replaced_by_new_telegram":
            connection.telegram_user_id = "tg-owner-new"
            connection.chat_id = "chat-owner-new"
        elif state == "moved_to_another_account":
            connection.user_id = other.id
        elif state == "alerts_turned_off":
            connection.alerts_enabled = False
        elif state == "revoked":
            connection.status = ConnectionStatus.REVOKED
        await session.commit()

        adapter = RecordingAdapter()
        await TelegramDeliveryService(
            session, test_context["settings"], adapter  # type: ignore[arg-type]
        ).process_due()
        await session.commit()
        await session.refresh(delivery)

        if state == "still_connected":
            assert [message.chat_id for message in adapter.sent] == ["chat-owner"]
            assert delivery.status == DeliveryStatus.SENT
            return
        assert adapter.sent == [], f"{state}: an alert went to a chat that left its owner"
        assert delivery.status == DeliveryStatus.CANCELED
        assert delivery.last_error_code == TELEGRAM_CHAT_NOT_CONNECTED
        assert delivery.next_retry_at is None


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------


def _receipt(update_id: str, created_at: datetime) -> TelegramUpdateReceipt:
    return TelegramUpdateReceipt(
        update_id=update_id,
        payload_hash="0" * 64,
        status="processed",
        response_payload={},
        provider_message_ids=[],
        created_at=created_at,
    )


async def test_polling_continues_after_the_newest_saved_update_when_numbers_restart(
    test_context: dict,
) -> None:
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        assert await telegram_polling_offset(session) is None

        # The old bot's last update is a higher number than anything the new bot sends.
        session.add(_receipt("589536818", now - timedelta(days=60)))
        session.add(_receipt("279923433", now - timedelta(minutes=5)))
        await session.commit()
        assert await telegram_polling_offset(session) == 279923434

        session.add(_receipt("279923440", now))
        await session.commit()
        assert await telegram_polling_offset(session) == 279923441
