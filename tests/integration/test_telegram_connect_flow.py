"""R5 вЂ” the Telegram account-link flow, end to end through the real update path.

The owner's report was one sentence: they enter the long code, press the bot's confirm,
and "it keeps sending me the same message to confirm". Four shapes of Telegram account can
reach that prompt, and each of them can be answered seven ways. Every combination is a row
here, and every row runs ``process_telegram_update`` вЂ” the same function the webhook runs вЂ”
against a recording adapter, so no row can pass by answering a question the bot never
actually asked.

Every row insists on the same four things, whatever else it expects:

* the update is answered, not crashed. An exception escaping the route, a receipt marked
  ``failed_*``, or the generic "Action needed" recovery text all mean the person was left
  holding a button that does nothing;
* the prompt has an end. Once the person has decided вЂ” by tap or by typing вЂ” nothing in
  that row brings the question back, including a plain message sent afterwards;
* the step is left behind. ``telegram_link``/``confirm`` is over and the pending token is
  gone from the conversation;
* the account ends in exactly the state the one ownership rule says, once, with one audit
  event and no uniqueness error.

The four shapes, and the defect each one used to prove:

``fresh``                a Telegram account the bot has never seen. The buttons were a
                         persistent reply keyboard, so they never left the screen (D-A).
``pressed_start``        one that pressed Start first, so a Telegram-only account already
                         holds the identity. Confirm then refused the person their own
                         account and left the step open for good (D-C, then D-B).
``replaces``             the dashboard account already has a *different* Telegram
                         connected: Confirm hit a uniqueness error instead of replacing it
                         (D-D), and nothing said so up front.
``other_real_account``   this Telegram belongs to a different account that has a sign-in
                         of its own: refused in plain words, never re-pointed (D-F).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.routers.telegram import process_telegram_update
from ai_market_monitor.core.dashboard_paths import CONNECTIONS_PATH
from ai_market_monitor.db.models import (
    AuditEvent,
    OnboardingSession,
    TelegramCallbackReceipt,
    TelegramConnection,
    TelegramConversationState,
    TelegramDashboardLink,
    TelegramUpdateReceipt,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import ConnectionStatus, IdentityProvider
from ai_market_monitor.services.telegram_account_links import (
    TelegramAccountLinkError,
    TelegramAccountLinkService,
)
from ai_market_monitor.telegram.adapter import TelegramDeliveryResult
from ai_market_monitor.telegram.service import TelegramBotService
from ai_market_monitor.telegram.types import (
    TelegramCallback,
    TelegramInboundMessage,
    TelegramOutboundMessage,
)

#: The sentence only the full confirmation prompt carries. A reminder may name the
#: buttons; repeating this after a decision is the loop the owner reported.
PROMPT_MARKER = "Connect this Telegram account to"

#: What the route writes when something inside it raised.
CRASH_TEXT = "Action needed: I could not"

TELEGRAM_ID = "tg-777"
CHAT_ID = "chat-777"
USERNAME = "market_trader"

CONFIRM = "telegram_link:confirm"
CANCEL = "telegram_link:cancel"
LINK_BUTTON_DATA = frozenset({CONFIRM, CANCEL})

OLD_TELEGRAM_ID = "tg-old"
OLD_CHAT_ID = "chat-old"
OLD_USERNAME = "old_handle"

SCENARIOS = ("fresh", "pressed_start", "replaces", "other_real_account")
ACTIONS = (
    "tap_confirm",
    "tap_cancel",
    "type_confirm",
    "type_yes",
    "start_again",
    "tap_confirm_twice",
    "same_update_twice",
)


class FakePreviewer:
    """The connect flow never previews a strategy; the service only needs the slot filled."""

    async def run(self, strategy):  # pragma: no cover - reaching this is the bug
        raise AssertionError("the Telegram connect flow must not run a market preview")


class RecordingTelegramAdapter:
    """Stands in for Telegram: keeps every message the bot asked to send, in order."""

    def __init__(self) -> None:
        self.sent: list[TelegramOutboundMessage] = []
        self.answered: list[str] = []

    async def deliver(self, message: TelegramOutboundMessage) -> TelegramDeliveryResult:
        self.sent.append(message)
        return TelegramDeliveryResult(message_ids=[f"msg-{len(self.sent)}"])

    async def answer_callback(self, callback_query_id: str, *, text: str | None = None) -> None:
        self.answered.append(callback_query_id)

    @property
    def last_message_id(self) -> str:
        """The id Telegram would have given the message the bot sent last."""

        return f"msg-{len(self.sent)}"

    def prompts(self, since: int = 0) -> list[TelegramOutboundMessage]:
        return [message for message in self.sent[since:] if PROMPT_MARKER in message.text]


@dataclass
class Turn:
    """One update posted to the route, and what the bot answered it with."""

    update_id: int
    result: dict[str, Any] | None
    error: BaseException | None
    sent: list[TelegramOutboundMessage]

    @property
    def reply(self) -> TelegramOutboundMessage:
        assert self.sent, "the update was answered with nothing at all"
        return self.sent[-1]


@dataclass
class Acted:
    """The decision phase of a row.

    ``outcome`` is the turn that states what happened. ``extra`` is a later turn that must
    not repeat the question (a second tap, a replay, a re-requested Start), and ``since``
    is where the decision phase starts in the adapter's log.
    """

    outcome: Turn
    extra: Turn | None
    since: int


# ---------------------------------------------------------------------------
# Updates, in the shape Telegram posts them
# ---------------------------------------------------------------------------


def message_update(
    update_id: int,
    text: str,
    *,
    telegram_user_id: str = TELEGRAM_ID,
    chat_id: str = CHAT_ID,
    message_id: int = 101,
    username: str = USERNAME,
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": 1781438400,
            "text": text,
            "from": {"id": telegram_user_id, "username": username},
            "chat": {"id": chat_id},
        },
    }


def callback_update(
    update_id: int,
    data: str,
    *,
    attached_message_id: str,
    callback_query_id: str,
    telegram_user_id: str = TELEGRAM_ID,
    chat_id: str = CHAT_ID,
    username: str = USERNAME,
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": callback_query_id,
            "from": {"id": telegram_user_id, "username": username},
            "message": {
                "message_id": attached_message_id,
                "date": 1781438400,
                "chat": {"id": chat_id},
            },
            "data": data,
        },
    }


# ---------------------------------------------------------------------------
# Seeding and reading back
# ---------------------------------------------------------------------------


async def _signed_up(session, email: str, *, display_name: str = "Owner") -> User:
    """An account with a sign-in of its own. That is what makes it a real account."""

    user = User(display_name=display_name)
    session.add(user)
    await session.flush()
    session.add(
        UserIdentity(
            user_id=user.id,
            provider=IdentityProvider.EMAIL,
            provider_subject=email,
            normalized_identifier=email,
            display_identifier=email,
            is_verified=True,
            is_primary=True,
            verified_at=datetime.now(UTC),
            profile_data={},
        )
    )
    await session.commit()
    return user


async def _hold_telegram(
    session,
    *,
    user: User,
    telegram_user_id: str,
    chat_id: str,
    username: str,
) -> None:
    """The rows a connected Telegram leaves behind: identity, connection, conversation."""

    session.add(
        UserIdentity(
            user_id=user.id,
            provider=IdentityProvider.TELEGRAM,
            provider_subject=telegram_user_id,
            display_identifier=username,
            is_verified=True,
            is_primary=False,
            verified_at=datetime.now(UTC),
            profile_data={},
        )
    )
    session.add(
        TelegramConnection(
            user_id=user.id,
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            username=username,
            status=ConnectionStatus.ACTIVE,
            connected_at=datetime.now(UTC),
        )
    )
    session.add(
        TelegramConversationState(
            user_id=user.id,
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            username=username,
            flow="main_menu",
            step="idle",
            state_data={},
            correlation_id=f"corr-{telegram_user_id}",
        )
    )
    await session.commit()


async def _start_link(session, settings, user_id: UUID) -> str:
    """The link the Connections page hands out, as the raw ``/start`` parameter."""

    url = await TelegramAccountLinkService(session, settings).create_dashboard_start_link(
        user_id=user_id
    )
    await session.commit()
    return url.rsplit("link_", 1)[1]


async def _drive(
    test_context: dict,
    session,
    adapter: RecordingTelegramAdapter,
    update: dict[str, Any],
) -> Turn:
    """One update through the route's own function.

    An exception is recorded rather than raised, so a broken row still reports what the
    person saw instead of ending the run.
    """

    before = len(adapter.sent)
    update_id = int(update["update_id"])
    try:
        result = await process_telegram_update(
            update,
            session=session,
            settings=test_context["settings"],
            previewer=FakePreviewer(),
            adapter=adapter,
        )
    except BaseException as exc:  # noqa: BLE001 - the row turns it into a named failure
        await session.rollback()
        return Turn(
            update_id=update_id,
            result=None,
            error=exc,
            sent=list(adapter.sent[before:]),
        )
    return Turn(
        update_id=update_id,
        result=result,
        error=None,
        sent=list(adapter.sent[before:]),
    )


async def _drive_link(test_context: dict, session, adapter, update_id: int, token: str) -> Turn:
    """``/start link_<token>``, the way the Connections page opens the bot."""

    return await _drive(
        test_context, session, adapter, message_update(update_id, f"/start link_{token}")
    )


async def _drive_tap(
    test_context: dict,
    session,
    adapter: RecordingTelegramAdapter,
    update_id: int,
    data: str,
    *,
    attached_message_id: str,
    callback_query_id: str,
) -> Turn:
    return await _drive(
        test_context,
        session,
        adapter,
        callback_update(
            update_id,
            data,
            attached_message_id=attached_message_id,
            callback_query_id=callback_query_id,
        ),
    )


async def _conversation(session, telegram_user_id: str) -> TelegramConversationState | None:
    return await session.scalar(
        select(TelegramConversationState).where(
            TelegramConversationState.telegram_user_id == telegram_user_id
        )
    )


async def _connection(session, telegram_user_id: str) -> TelegramConnection | None:
    return await session.scalar(
        select(TelegramConnection).where(TelegramConnection.telegram_user_id == telegram_user_id)
    )


async def _identity_owner(session, telegram_user_id: str) -> UUID | None:
    return await session.scalar(
        select(UserIdentity.user_id).where(
            UserIdentity.provider == IdentityProvider.TELEGRAM,
            UserIdentity.provider_subject == telegram_user_id,
        )
    )


async def _receipt(session, update_id: int) -> TelegramUpdateReceipt | None:
    return await session.scalar(
        select(TelegramUpdateReceipt).where(TelegramUpdateReceipt.update_id == str(update_id))
    )


async def _audit_count(session, action: str, *, target_id: str | None = None) -> int:
    statement = select(func.count(AuditEvent.id)).where(AuditEvent.action == action)
    if target_id is not None:
        statement = statement.where(AuditEvent.target_id == target_id)
    return int(await session.scalar(statement) or 0)


@dataclass
class Opened:
    """The prompt is on screen, plus everything a row needs to answer it."""

    dashboard: UUID
    token: str
    prompt_id: str
    turn: Turn
    shell: UUID | None
    other: UUID | None


async def _open_prompt(test_context: dict, session, adapter, scenario: str) -> Opened:
    """Build one of the four worlds, then put the connect prompt on screen."""

    settings = test_context["settings"]
    settings.telegram_bot_username = "trace_edge_bot"
    dashboard = await _signed_up(session, "owner@example.com")

    shell: UUID | None = None
    other: UUID | None = None
    if scenario == "fresh":
        pass
    elif scenario == "pressed_start":
        # Run the real thing rather than inserting rows, because the defect *is* what the
        # real ``/start`` writes: a Telegram-only account holding this Telegram identity.
        await _drive(test_context, session, adapter, message_update(1, "/start"))
        shell = await _identity_owner(session, TELEGRAM_ID)
        assert shell is not None, "/start made no account for this Telegram"
        assert shell != dashboard.id
        assert await _connection(session, TELEGRAM_ID) is not None
    elif scenario == "replaces":
        await _hold_telegram(
            session,
            user=dashboard,
            telegram_user_id=OLD_TELEGRAM_ID,
            chat_id=OLD_CHAT_ID,
            username=OLD_USERNAME,
        )
    elif scenario == "other_real_account":
        holder = await _signed_up(session, "someone.else@example.com", display_name="Other")
        other = holder.id
        await _hold_telegram(
            session,
            user=holder,
            telegram_user_id=TELEGRAM_ID,
            chat_id=CHAT_ID,
            username=USERNAME,
        )
    else:  # pragma: no cover - a broken parametrise, not a product state
        raise AssertionError(f"unknown scenario {scenario!r}")

    token = await _start_link(session, settings, dashboard.id)
    turn = await _drive(test_context, session, adapter, message_update(20, f"/start link_{token}"))
    assert turn.error is None, f"opening the prompt crashed: {turn.error!r}"
    assert turn.sent, "opening the prompt sent nothing"
    return Opened(
        dashboard=dashboard.id,
        token=token,
        prompt_id=adapter.last_message_id,
        turn=turn,
        shell=shell,
        other=other,
    )


# ---------------------------------------------------------------------------
# The seven ways the person answers
# ---------------------------------------------------------------------------


def _expected(scenario: str, action: str) -> str:
    if action == "tap_cancel":
        return "cancelled"
    if scenario == "other_real_account":
        return "refused"
    return "connected"


async def _act(
    test_context: dict,
    session,
    adapter: RecordingTelegramAdapter,
    opened: Opened,
    action: str,
) -> Acted:
    """Answer the prompt the way the row says."""

    if action == "tap_confirm":
        since = len(adapter.sent)
        turn = await _drive_tap(
            test_context,
            session,
            adapter,
            30,
            CONFIRM,
            attached_message_id=opened.prompt_id,
            callback_query_id="cb-1",
        )
        return Acted(outcome=turn, extra=None, since=since)
    if action == "tap_cancel":
        since = len(adapter.sent)
        turn = await _drive_tap(
            test_context,
            session,
            adapter,
            31,
            CANCEL,
            attached_message_id=opened.prompt_id,
            callback_query_id="cb-2",
        )
        return Acted(outcome=turn, extra=None, since=since)
    if action in {"type_confirm", "type_yes"}:
        text = "Confirm" if action == "type_confirm" else "Yes"
        since = len(adapter.sent)
        turn = await _drive(test_context, session, adapter, message_update(32, text))
        return Acted(outcome=turn, extra=None, since=since)
    if action == "start_again":
        # Pressing Start again asks the question a second time. That is allowed while the
        # question is still open; what must never happen is the question returning after
        # the person has answered it.
        since = len(adapter.sent)
        await _drive(test_context, session, adapter, message_update(34, "/start"))
        assert adapter.prompts(since), "pressing /start again lost the pending question"
        turn = await _drive_tap(
            test_context,
            session,
            adapter,
            35,
            CONFIRM,
            attached_message_id=adapter.last_message_id,
            callback_query_id="cb-3",
        )
        return Acted(outcome=turn, extra=None, since=len(adapter.sent) - len(turn.sent))
    if action == "tap_confirm_twice":
        since = len(adapter.sent)
        first = await _drive_tap(
            test_context,
            session,
            adapter,
            36,
            CONFIRM,
            attached_message_id=opened.prompt_id,
            callback_query_id="cb-4",
        )
        second = await _drive_tap(
            test_context,
            session,
            adapter,
            37,
            CONFIRM,
            attached_message_id=opened.prompt_id,
            callback_query_id="cb-5",
        )
        return Acted(outcome=first, extra=second, since=since)
    if action == "same_update_twice":
        since = len(adapter.sent)
        update = callback_update(
            38,
            CONFIRM,
            attached_message_id=opened.prompt_id,
            callback_query_id="cb-6",
        )
        first = await _drive(test_context, session, adapter, update)
        replay = await _drive(test_context, session, adapter, update)
        return Acted(outcome=first, extra=replay, since=since)
    raise AssertionError(f"unknown action {action!r}")  # pragma: no cover


def _assert_answered(turn: Turn) -> None:
    assert turn.error is None, f"the update raised {type(turn.error).__name__}: {turn.error}"
    assert turn.result is not None, "the route never answered the update"
    assert turn.result.get("accepted", True) is not False, turn.result
    assert not any(CRASH_TEXT in message.text for message in turn.sent), turn.reply.text


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("action", ACTIONS)
async def test_one_answer_ends_the_connect_prompt(
    test_context: dict, scenario: str, action: str
) -> None:
    """One decision ends the question: no loop, no crash, and the account is right."""

    expected = _expected(scenario, action)
    adapter = RecordingTelegramAdapter()
    async with test_context["session_factory"]() as session:
        opened = await _open_prompt(test_context, session, adapter, scenario)
        acted = await _act(test_context, session, adapter, opened, action)
        decided = acted.outcome

        # 1. answered, not crashed.
        _assert_answered(decided)
        if acted.extra is not None:
            _assert_answered(acted.extra)
        for turn in (decided, acted.extra):
            if turn is None:
                continue
            receipt = await _receipt(session, turn.update_id)
            if receipt is not None:
                assert receipt.error_code is None, f"the route recorded {receipt.error_code}"
                assert not receipt.status.startswith("failed"), receipt.status

        # 2. nothing after the decision repeats the question вЂ” including a plain message.
        probe = await _drive(test_context, session, adapter, message_update(40, "anything at all"))
        _assert_answered(probe)
        assert not adapter.prompts(acted.since), (
            "the confirmation prompt came back after the person had answered it: "
            + " | ".join(message.text[:80] for message in adapter.prompts(acted.since))
        )
        assert not any(
            button.callback_data in LINK_BUTTON_DATA for button in decided.reply.buttons
        ), f"a decision button survived the decision: {decided.reply.buttons}"

        # 3. the step is left behind, and the pending token with it.
        conversation = await _conversation(session, TELEGRAM_ID)
        assert conversation is not None
        assert (conversation.flow, conversation.step) != ("telegram_link", "confirm"), (
            f"the person was left in {conversation.flow}/{conversation.step} after "
            f"answering with {decided.reply.text!r}"
        )
        assert "telegram_dashboard_link_token" not in (conversation.state_data or {}), (
            "nobody forgot the pending link: it is still in the conversation"
        )

        # 4. the account ends where the one ownership rule says, once.
        link = await session.scalar(
            select(TelegramDashboardLink).where(TelegramDashboardLink.user_id == opened.dashboard)
        )
        connection = await _connection(session, TELEGRAM_ID)
        identity_owner = await _identity_owner(session, TELEGRAM_ID)
        linked_audits = await _audit_count(
            session, "telegram.account_linked", target_id=TELEGRAM_ID
        )

        if expected == "cancelled":
            assert connection is None or connection.user_id != opened.dashboard
            assert identity_owner != opened.dashboard
            assert link is not None and link.consumed_at is None
            assert "nothing was connected" in decided.reply.text.lower()
            return

        if expected == "refused":
            assert connection is not None and connection.user_id == opened.other
            assert identity_owner == opened.other
            assert linked_audits == 0, "a refusal wrote a connection audit event"
            assert "another hilal markets account" in decided.reply.text.lower()
            assert CONNECTIONS_PATH in decided.reply.text
            return

        assert connection is not None, f"nothing was connected: {decided.reply.text!r}"
        assert connection.user_id == opened.dashboard
        assert connection.chat_id == CHAT_ID
        assert connection.status == ConnectionStatus.ACTIVE
        assert identity_owner == opened.dashboard
        assert link is not None and link.consumed_at is not None
        assert linked_audits == 1, f"{linked_audits} connection audit events for one connection"
        assert CONNECTIONS_PATH in decided.reply.text

        if scenario == "pressed_start":
            # R6(a): the shell account gives up the Telegram rows and keeps its own history.
            assert opened.shell is not None and opened.shell != opened.dashboard
            assert await session.scalar(
                select(func.count(UserIdentity.id)).where(
                    UserIdentity.provider == IdentityProvider.TELEGRAM,
                    UserIdentity.user_id == opened.shell,
                )
            ) == 0, "the Telegram-only account still holds the identity it gave up"
            assert await session.scalar(
                select(func.count(OnboardingSession.id)).where(
                    OnboardingSession.user_id == opened.shell
                )
            ) == 1, "moving the identity also moved the shell account's own history"
            assert await _audit_count(
                session, "telegram.identity_moved", target_id=TELEGRAM_ID
            ) == 1
        if scenario == "replaces":
            # R6(c): one account, one Telegram, and no uniqueness error on the way.
            assert await session.scalar(
                select(func.count(TelegramConnection.id)).where(
                    TelegramConnection.user_id == opened.dashboard
                )
            ) == 1, "one account ended up holding two Telegram connections"
            assert await _connection(session, OLD_TELEGRAM_ID) is None, (
                "the replaced Telegram is still on the account it was replaced from"
            )


async def test_the_prompt_is_one_message_with_exactly_two_inline_buttons(
    test_context: dict,
) -> None:
    """R1: one message, Confirm and Cancel attached to it, nothing stuck on screen."""

    adapter = RecordingTelegramAdapter()
    async with test_context["session_factory"]() as session:
        opened = await _open_prompt(test_context, session, adapter, "fresh")
        prompt = opened.turn.reply
        assert [button.text for button in prompt.buttons] == ["Confirm", "Cancel"]
        assert [button.callback_data for button in prompt.buttons] == [CONFIRM, CANCEL]
        assert prompt.keyboard == "inline", (
            "the prompt asked for no keyboard style, so its buttons became a persistent "
            "reply keyboard that never leaves the screen"
        )
        assert "owner@example.com" in prompt.text
        assert prompt.edit_message_id is None
        assert prompt.menu == []


async def test_the_prompt_warns_when_it_replaces_a_telegram(test_context: dict) -> None:
    """R6(c): the person is told before, not after."""

    adapter = RecordingTelegramAdapter()
    async with test_context["session_factory"]() as session:
        opened = await _open_prompt(test_context, session, adapter, "replaces")
        text = opened.turn.reply.text.lower()
        assert "replaces" in text, opened.turn.reply.text
        assert f"@{OLD_USERNAME}" in opened.turn.reply.text, opened.turn.reply.text


async def test_a_tap_answers_on_the_prompt_itself(test_context: dict) -> None:
    """R2: the answer replaces the question, on the same message."""

    adapter = RecordingTelegramAdapter()
    async with test_context["session_factory"]() as session:
        opened = await _open_prompt(test_context, session, adapter, "fresh")
        acted = await _act(test_context, session, adapter, opened, "tap_confirm")
        _assert_answered(acted.outcome)
        assert acted.outcome.reply.edit_message_id == opened.prompt_id, (
            "the answer went out as a new message, so the question and its buttons stayed "
            "on screen beside it"
        )
        assert acted.outcome.reply.keyboard == "inline", (
            "an edit carried a keyboard Telegram does not accept on editMessageText"
        )


async def test_cancel_clears_the_question_and_forgets_the_link(test_context: dict) -> None:
    """R3."""

    adapter = RecordingTelegramAdapter()
    async with test_context["session_factory"]() as session:
        opened = await _open_prompt(test_context, session, adapter, "fresh")
        acted = await _act(test_context, session, adapter, opened, "tap_cancel")
        _assert_answered(acted.outcome)
        assert acted.outcome.reply.edit_message_id == opened.prompt_id
        assert "nothing was connected" in acted.outcome.reply.text.lower()
        assert not any(
            button.callback_data in LINK_BUTTON_DATA for button in acted.outcome.reply.buttons
        )
        conversation = await _conversation(session, TELEGRAM_ID)
        assert "telegram_dashboard_link_token" not in (conversation.state_data or {})
        assert (conversation.flow, conversation.step) != ("telegram_link", "confirm")


async def test_a_typed_answer_cannot_edit_and_clears_the_stale_keyboard(
    test_context: dict,
) -> None:
    """A typed reply has no message of ours to edit: send, and take the old keys away."""

    adapter = RecordingTelegramAdapter()
    async with test_context["session_factory"]() as session:
        opened = await _open_prompt(test_context, session, adapter, "fresh")
        acted = await _act(test_context, session, adapter, opened, "type_yes")
        _assert_answered(acted.outcome)
        assert acted.outcome.reply.edit_message_id is None, (
            "a typed reply tried to edit a message the bot never sent"
        )
        assert acted.outcome.reply.keyboard == "remove_reply_keyboard"


async def test_a_refused_chat_does_not_keep_acting_as_the_account_that_was_refused(
    test_context: dict,
) -> None:
    """R6(b): "no data changed" includes the conversation the question was asked in.

    Asking the question points the chat at the account named in the link. When the answer
    is no, leaving it there means this Telegram keeps being served that account's monitors
    and trial status without ever being connected to it.
    """

    adapter = RecordingTelegramAdapter()
    async with test_context["session_factory"]() as session:
        opened = await _open_prompt(test_context, session, adapter, "other_real_account")
        assert opened.other is not None
        # Asking the question is what moved the chat onto the offered account.
        asked = await _conversation(session, TELEGRAM_ID)
        assert asked is not None and asked.user_id == opened.dashboard
        acted = await _act(test_context, session, adapter, opened, "tap_confirm")
        _assert_answered(acted.outcome)

        refused = await _conversation(session, TELEGRAM_ID)
        assert refused is not None
        assert refused.user_id == opened.other, (
            "the refusal left this Telegram acting as the account that refused it"
        )
        assert (refused.flow, refused.step) != ("telegram_link", "confirm")


async def test_every_confirm_outcome_offers_the_connections_page(test_context: dict) -> None:
    """R4: no dead end. Each outcome names the next step and links to the real page."""

    adapter = RecordingTelegramAdapter()
    async with test_context["session_factory"]() as session:
        settings = test_context["settings"]
        settings.telegram_bot_username = "trace_edge_bot"
        dashboard = await _signed_up(session, "owner@example.com")

        # An expired link.
        expired_token = await _start_link(session, settings, dashboard.id)
        await session.execute(
            TelegramDashboardLink.__table__.update()
            .where(TelegramDashboardLink.user_id == dashboard.id)
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()
        await _drive_link(test_context, session, adapter, 50, expired_token)
        assert CONNECTIONS_PATH in adapter.sent[-1].text, adapter.sent[-1].text

        # A token nobody was given.
        await _drive(test_context, session, adapter, message_update(51, "/start link_not-a-token"))
        assert CONNECTIONS_PATH in adapter.sent[-1].text, adapter.sent[-1].text

        # A link that has already been spent.
        spent = await _signed_up(session, "spent@example.com", display_name="Spent")
        spent_token = await _start_link(session, settings, spent.id)
        await _drive_link(test_context, session, adapter, 52, spent_token)
        await _drive_tap(
            test_context,
            session,
            adapter,
            53,
            CONFIRM,
            attached_message_id=adapter.last_message_id,
            callback_query_id="cb-9",
        )
        await _drive_link(test_context, session, adapter, 54, spent_token)
        assert CONNECTIONS_PATH in adapter.sent[-1].text, adapter.sent[-1].text
        assert (
            await session.scalar(
                select(func.count(TelegramConnection.id)).where(
                    TelegramConnection.telegram_user_id == TELEGRAM_ID
                )
            )
            == 1
        ), "the same Telegram was connected twice"


async def test_an_unclear_reply_gets_a_line_not_the_prompt(test_context: dict) -> None:
    """R4: one vocabulary reads a reply; anything else gets a one-line reminder."""

    adapter = RecordingTelegramAdapter()
    async with test_context["session_factory"]() as session:
        opened = await _open_prompt(test_context, session, adapter, "fresh")
        turn = await _drive(test_context, session, adapter, message_update(60, "hello there"))
        assert turn.error is None, turn.error
        reminder = turn.reply
        assert PROMPT_MARKER not in reminder.text, "the whole prompt came back: a loop"
        assert len(reminder.text) < 220, reminder.text
        assert "confirm" in reminder.text.lower() and "cancel" in reminder.text.lower()
        # The question is still open, and a tap still answers it.
        decided = await _drive(
            test_context,
            session,
            adapter,
            callback_update(
                61,
                CONFIRM,
                attached_message_id=adapter.last_message_id,
                callback_query_id="cb-10",
            ),
        )
        _assert_answered(decided)
        connection = await _connection(session, TELEGRAM_ID)
        assert connection is not None and connection.user_id == opened.dashboard


_LEGACY_KEYBOARD_KEYS = "\u2705 Yes, connect"


@pytest.mark.parametrize(
    "text",
    [
        "Confirm",
        "confirm",
        "Yes",
        "yes",
        "Yes, connect",
        _LEGACY_KEYBOARD_KEYS,
        "connect",
        "ok",
        "sure",
    ],
)
async def test_the_shared_yes_vocabulary_answers_the_prompt(
    test_context: dict, text: str
) -> None:
    """R4: yes is read by the one shared vocabulary, including the leftover keyboard keys."""

    adapter = RecordingTelegramAdapter()
    async with test_context["session_factory"]() as session:
        await _open_prompt(test_context, session, adapter, "fresh")
        turn = await _drive(test_context, session, adapter, message_update(70, text))
        assert turn.error is None, turn.error
        assert PROMPT_MARKER not in turn.reply.text, f"{text!r} was not read as a yes"
        assert await _connection(session, TELEGRAM_ID) is not None, f"{text!r} did not connect"


@pytest.mark.parametrize("text", ["Cancel", "cancel", "No", "no", "Back", "back"])
async def test_the_shared_no_vocabulary_ends_the_prompt(
    test_context: dict, text: str
) -> None:
    adapter = RecordingTelegramAdapter()
    async with test_context["session_factory"]() as session:
        await _open_prompt(test_context, session, adapter, "fresh")
        turn = await _drive(test_context, session, adapter, message_update(71, text))
        assert turn.error is None, turn.error
        assert PROMPT_MARKER not in turn.reply.text, f"{text!r} was not read as a no"
        conversation = await _conversation(session, TELEGRAM_ID)
        assert (conversation.flow, conversation.step) != ("telegram_link", "confirm")
        assert "telegram_dashboard_link_token" not in (conversation.state_data or {})


async def test_a_second_link_offer_replaces_the_first_prompt(test_context: dict) -> None:
    """A fresh ``/start link_...`` re-opens the question instead of crashing on the old one."""

    adapter = RecordingTelegramAdapter()
    async with test_context["session_factory"]() as session:
        settings = test_context["settings"]
        opened = await _open_prompt(test_context, session, adapter, "fresh")
        second = await _start_link(session, settings, opened.dashboard)
        turn = await _drive(
            test_context, session, adapter, message_update(80, f"/start link_{second}")
        )
        assert turn.error is None, turn.error
        assert PROMPT_MARKER in turn.reply.text
        conversation = await _conversation(session, TELEGRAM_ID)
        assert (conversation.state_data or {})["telegram_dashboard_link_token"] == second
        decided = await _drive(
            test_context,
            session,
            adapter,
            callback_update(
                81,
                CONFIRM,
                attached_message_id=adapter.last_message_id,
                callback_query_id="cb-11",
            ),
        )
        _assert_answered(decided)
        connection = await _connection(session, TELEGRAM_ID)
        assert connection is not None and connection.user_id == opened.dashboard


async def test_a_stale_button_tap_after_cancel_does_not_resurrect_the_link(
    test_context: dict,
) -> None:
    """A person can still press the old message. It must not reconnect, and must not loop."""

    adapter = RecordingTelegramAdapter()
    async with test_context["session_factory"]() as session:
        opened = await _open_prompt(test_context, session, adapter, "fresh")
        acted = await _act(test_context, session, adapter, opened, "tap_cancel")
        _assert_answered(acted.outcome)
        before = len(adapter.sent)
        turn = await _drive(
            test_context,
            session,
            adapter,
            callback_update(
                90,
                CONFIRM,
                attached_message_id=opened.prompt_id,
                callback_query_id="cb-12",
            ),
        )
        _assert_answered(turn)
        assert not any(PROMPT_MARKER in message.text for message in adapter.sent[before:])
        assert await _connection(session, TELEGRAM_ID) is None
        conversation = await _conversation(session, TELEGRAM_ID)
        assert (conversation.flow, conversation.step) != ("telegram_link", "confirm")


async def test_a_confirm_that_breaks_internally_still_leaves_the_step(
    test_context: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4's last case: an unexpected failure must not be a trap."""

    adapter = RecordingTelegramAdapter()

    async def explode(self, **_kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(TelegramAccountLinkService, "complete_dashboard_start_link", explode)
    async with test_context["session_factory"]() as session:
        opened = await _open_prompt(test_context, session, adapter, "fresh")
        acted = await _act(test_context, session, adapter, opened, "tap_confirm")
        assert acted.outcome.error is None, f"the crash escaped the route: {acted.outcome.error!r}"
        assert not any(CRASH_TEXT in message.text for message in acted.outcome.sent)
        conversation = await _conversation(session, TELEGRAM_ID)
        assert (conversation.flow, conversation.step) != ("telegram_link", "confirm")
        assert "telegram_dashboard_link_token" not in (conversation.state_data or {})
        assert CONNECTIONS_PATH in acted.outcome.reply.text


async def test_the_ownership_rule_has_one_owner(test_context: dict) -> None:
    """D-F: both completion paths ask the same question in the same place.

    A rule with two copies is a rule that disagrees with itself, and this repository has
    been repaired out of that mistake before. The check is behavioural: whichever door a
    person came through, the same world produces the same answer.
    """

    adapter = RecordingTelegramAdapter()
    async with test_context["session_factory"]() as session:
        settings = test_context["settings"]
        settings.telegram_bot_username = "trace_edge_bot"

        # Door one: the dashboard link, confirmed in Telegram.
        first = await _signed_up(session, "one@example.com", display_name="One")
        token = await _start_link(session, settings, first.id)
        await _drive(test_context, session, adapter, message_update(100, f"/start link_{token}"))
        await _drive(
            test_context,
            session,
            adapter,
            callback_update(
                101,
                CONFIRM,
                attached_message_id=adapter.last_message_id,
                callback_query_id="cb-20",
            ),
        )
        moved = await _connection(session, TELEGRAM_ID)
        assert moved is not None and moved.user_id == first.id

        # Door two: the bot's own secure link, finished on the dashboard. A second
        # Telegram, held by a Telegram-only account, must be moved the same way.
        shell = User(display_name="Telegram shell")
        session.add(shell)
        await session.flush()
        second = await _signed_up(session, "two@example.com", display_name="Two")
        session.add(
            TelegramConnection(
                user_id=shell.id,
                telegram_user_id="tg-888",
                chat_id="chat-888",
                username="other_trader",
                status=ConnectionStatus.ACTIVE,
                connected_at=datetime.now(UTC),
            )
        )
        await session.commit()
        link_url = await TelegramAccountLinkService(session, settings).create(
            user_id=second.id,
            telegram_user_id="tg-888",
            target="signin",
        )
        await session.commit()
        await TelegramAccountLinkService(session, settings).complete(
            link_url.split("telegram_link=", 1)[1], user=second
        )
        await session.commit()

        moved_two = await _connection(session, "tg-888")
        assert moved_two is not None and moved_two.user_id == second.id
        assert await session.scalar(
            select(func.count(TelegramConnection.id)).where(TelegramConnection.user_id == shell.id)
        ) == 0, "the second door left the shell account holding the connection"
        assert await _audit_count(session, "telegram.identity_moved", target_id="tg-888") == 1


async def _hold_telegram_on_signed_up_account(session) -> UUID:
    """An account with a sign-in of its own that already holds ``tg-999``.

    Returns that account's id: the row a refusal must leave exactly where it was.
    """

    holder = User(display_name="Holder")
    session.add(holder)
    await session.flush()
    session.add(
        UserIdentity(
            user_id=holder.id,
            provider=IdentityProvider.EMAIL,
            provider_subject="locked@example.com",
            normalized_identifier="locked@example.com",
            display_identifier="locked@example.com",
            is_verified=True,
            is_primary=True,
            verified_at=datetime.now(UTC),
            profile_data={},
        )
    )
    session.add(
        UserIdentity(
            user_id=holder.id,
            provider=IdentityProvider.TELEGRAM,
            provider_subject="tg-999",
            display_identifier="locked",
            is_verified=True,
            is_primary=False,
            verified_at=datetime.now(UTC),
            profile_data={},
        )
    )
    session.add(
        TelegramConnection(
            user_id=holder.id,
            telegram_user_id="tg-999",
            chat_id="chat-999",
            username="locked",
            status=ConnectionStatus.ACTIVE,
            connected_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return holder.id


async def test_both_doors_refuse_a_telegram_that_is_someone_elses(test_context: dict) -> None:
    """R6(b), asked of the one rule from each door: same code, same plain sentence.

    One world, both doors. The rule refuses before it writes, so the session is still
    usable for the second door, and the last assertions are the ones that matter: nothing
    about the other person's account moved.
    """

    settings = test_context["settings"]
    settings.telegram_bot_username = "trace_edge_bot"

    async with test_context["session_factory"]() as session:
        holder_id = await _hold_telegram_on_signed_up_account(session)
        stranger = await _signed_up(session, "four@example.com", display_name="Four")
        service = TelegramAccountLinkService(session, settings)

        url = await service.create(
            user_id=stranger.id,
            telegram_user_id="tg-999",
            target="signin",
        )
        await session.commit()
        with pytest.raises(TelegramAccountLinkError) as door_two:
            await TelegramAccountLinkService(session, settings).complete(
                url.split("telegram_link=", 1)[1], user=stranger
            )
        assert door_two.value.code == "telegram_already_linked"
        assert "another hilal markets account" in str(door_two.value).lower()

        token = await _start_link(session, settings, stranger.id)
        with pytest.raises(TelegramAccountLinkError) as door_one:
            await TelegramAccountLinkService(session, settings).complete_dashboard_start_link(
                raw_token=token,
                telegram_user_id="tg-999",
                chat_id="chat-999",
                username="locked",
            )
        assert door_one.value.code == "telegram_already_linked"
        assert "another hilal markets account" in str(door_one.value).lower()

        assert (await _connection(session, "tg-999")).user_id == holder_id
        assert await _identity_owner(session, "tg-999") == holder_id
        assert await _audit_count(session, "telegram.account_linked", target_id="tg-999") == 0
        assert await _audit_count(session, "telegram.identity_moved", target_id="tg-999") == 0
        link_rows = (
            await session.scalars(
                select(TelegramDashboardLink).where(TelegramDashboardLink.user_id == stranger.id)
            )
        ).all()
        assert link_rows, "the link rows a refusal must not spend were never created"
        assert all(link.consumed_at is None for link in link_rows), (
            "a refusal spent a link the person can still use"
        )


async def test_a_telegram_already_on_this_account_connects_without_duplicates(
    test_context: dict,
) -> None:
    """R6(d): the same Telegram confirmed twice on the same account leaves one row each."""

    settings = test_context["settings"]
    settings.telegram_bot_username = "trace_edge_bot"
    async with test_context["session_factory"]() as session:
        owner = await _signed_up(session, "five@example.com", display_name="Five")
        await _hold_telegram(
            session,
            user=owner,
            telegram_user_id="tg-double",
            chat_id="chat-double",
            username="twice",
        )
        first = await _start_link(session, settings, owner.id)
        await TelegramAccountLinkService(session, settings).complete_dashboard_start_link(
            raw_token=first,
            telegram_user_id="tg-double",
            chat_id="chat-double",
            username="twice",
        )
        await session.commit()
        second = await _start_link(session, settings, owner.id)
        again = await TelegramAccountLinkService(session, settings).complete_dashboard_start_link(
            raw_token=second,
            telegram_user_id="tg-double",
            chat_id="chat-double",
            username="twice",
        )
        await session.commit()

        assert again.user.id == owner.id
        assert again.already_connected is True
        assert await session.scalar(
            select(func.count(TelegramConnection.id)).where(
                TelegramConnection.telegram_user_id == "tg-double"
            )
        ) == 1
        assert await session.scalar(
            select(func.count(UserIdentity.id)).where(
                UserIdentity.provider == IdentityProvider.TELEGRAM,
                UserIdentity.provider_subject == "tg-double",
            )
        ) == 1
        assert await _audit_count(session, "telegram.account_linked", target_id="tg-double") == 0


async def test_the_same_token_confirmed_twice_decides_once(test_context: dict) -> None:
    """R7 at the row, not at the conversation.

    The bot's own step-ending means a double tap normally arrives with no token to spend,
    which is a different guard. Two taps that raced each other both hold the token, so the
    only thing standing between them and a second connection is the link row being read
    again after it is locked. That is the path this test drives directly.
    """

    settings = test_context["settings"]
    settings.telegram_bot_username = "trace_edge_bot"
    async with test_context["session_factory"]() as session:
        dashboard = await _signed_up(session, "race@example.com")
        token = await _start_link(session, settings, dashboard.id)
        service = TelegramAccountLinkService(session, settings)
        first = await service.complete_dashboard_start_link(
            raw_token=token,
            telegram_user_id=TELEGRAM_ID,
            chat_id=CHAT_ID,
            username=USERNAME,
        )
        await session.commit()
        second = await service.complete_dashboard_start_link(
            raw_token=token,
            telegram_user_id=TELEGRAM_ID,
            chat_id=CHAT_ID,
            username=USERNAME,
        )
        await session.commit()

        assert first.user.id == dashboard.id
        assert second.user.id == dashboard.id
        assert second.already_connected is True, (
            "the second tap was decided as a new connection instead of the same one"
        )
        assert await _audit_count(session, "telegram.account_linked", target_id=TELEGRAM_ID) == 1
        assert await session.scalar(
            select(func.count(TelegramConnection.id)).where(
                TelegramConnection.telegram_user_id == TELEGRAM_ID
            )
        ) == 1

        # A *different* Telegram presenting the same spent token is refused, not connected.
        from_this_chat_away = TelegramAccountLinkService(session, settings)
        with pytest.raises(TelegramAccountLinkError) as refused:
            await from_this_chat_away.complete_dashboard_start_link(
                raw_token=token,
                telegram_user_id="tg-someone-else",
                chat_id="chat-someone-else",
                username="intruder",
            )
        assert refused.value.code == "telegram_link_used"
        assert await _connection(session, "tg-someone-else") is None


async def test_the_dashboard_door_is_idempotent_for_the_same_link_too(
    test_context: dict,
) -> None:
    """R6/R7: the second door answers a re-posted form the same way the first does."""

    settings = test_context["settings"]
    settings.telegram_bot_username = "trace_edge_bot"
    async with test_context["session_factory"]() as session:
        owner = await _signed_up(session, "form@example.com")
        url = await TelegramAccountLinkService(session, settings).create(
            user_id=owner.id,
            telegram_user_id="tg-form",
            target="signin",
        )
        await session.commit()
        token = url.split("telegram_link=", 1)[1]
        service = TelegramAccountLinkService(session, settings)
        assert await service.complete(token, user=owner) == "tg-form"
        await session.commit()
        again = await TelegramAccountLinkService(session, settings).complete(token, user=owner)
        await session.commit()

        assert again == "tg-form"
        assert await _audit_count(session, "telegram.account_linked", target_id="tg-form") == 1
        assert await session.scalar(
            select(func.count(TelegramConnection.id)).where(
                TelegramConnection.telegram_user_id == "tg-form"
            )
        ) == 1


async def test_the_same_callback_delivered_twice_connects_once(test_context: dict) -> None:
    """R7: Telegram redelivering one tap must not spend the link a second time.

    The two deliveries are separate calls to the bot service, which is what a redelivery
    looks like when the update receipt does not stop it first: the same callback id, twice.
    """

    async with test_context["session_factory"]() as session:
        settings = test_context["settings"]
        settings.telegram_bot_username = "trace_edge_bot"
        dashboard = await _signed_up(session, "once@example.com")
        token = await _start_link(session, settings, dashboard.id)
        service = TelegramBotService(
            session,
            settings,
            previewer=FakePreviewer(),
        )
        await service.handle_start(
            TelegramInboundMessage(
                telegram_user_id=TELEGRAM_ID,
                chat_id=CHAT_ID,
                username=USERNAME,
                text=f"/start link_{token}",
            )
        )
        tap = TelegramCallback(
            callback_query_id="cb-once",
            telegram_user_id=TELEGRAM_ID,
            chat_id=CHAT_ID,
            data=CONFIRM,
            message_id="msg-prompt",
        )
        first = await service.handle_callback(tap)
        again = await service.handle_callback(tap)

        assert again.text == first.text
        assert await _audit_count(session, "telegram.account_linked", target_id=TELEGRAM_ID) == 1
        assert await session.scalar(
            select(func.count(TelegramConnection.id)).where(
                TelegramConnection.telegram_user_id == TELEGRAM_ID
            )
        ) == 1
        assert await session.scalar(
            select(func.count(TelegramCallbackReceipt.id)).where(
                TelegramCallbackReceipt.callback_query_id == "cb-once"
            )
        ) == 1
        # The answer kept for the replay is the answer that was first given, keyboard
        # included: a replayed edit that lost its keyboard style would send a reply
        # keyboard to editMessageText, which Telegram refuses.
        receipt = await session.scalar(
            select(TelegramCallbackReceipt).where(
                TelegramCallbackReceipt.callback_query_id == "cb-once"
            )
        )
        stored = TelegramOutboundMessage.from_payload(receipt.result_payload)
        assert stored == again
        assert stored.edit_message_id == "msg-prompt"
        assert stored.keyboard == "inline"


async def test_confirming_a_link_locks_the_link_row_before_deciding(
    test_context: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R7: the second tap must re-read the link *after* the lock, not trust its memory.

    SQLite drops ``FOR UPDATE`` and PostgreSQL takes it, so the offline suite cannot
    interleave two transactions. What it can insist on is the statement the completion
    actually issues: a plain read would let two taps that arrive at the same moment each
    see an unconsumed link, and both connect.
    """

    locked: list[str] = []
    real_scalar = AsyncSession.scalar

    async def spy(self, statement, *args, **kwargs):
        compiled = str(statement.compile(dialect=postgresql.dialect()))
        if "FOR UPDATE" in compiled and "telegram_dashboard_links" in compiled:
            locked.append(compiled)
        return await real_scalar(self, statement, *args, **kwargs)

    async with test_context["session_factory"]() as session:
        settings = test_context["settings"]
        settings.telegram_bot_username = "trace_edge_bot"
        dashboard = await _signed_up(session, "locked@example.com")
        token = await _start_link(session, settings, dashboard.id)
        monkeypatch.setattr(AsyncSession, "scalar", spy)
        completion = await TelegramAccountLinkService(
            session, settings
        ).complete_dashboard_start_link(
            raw_token=token,
            telegram_user_id=TELEGRAM_ID,
            chat_id=CHAT_ID,
            username=USERNAME,
        )
        monkeypatch.undo()
        await session.commit()

        assert completion.user.id == dashboard.id
        assert locked, (
            "completing a link read the link row without locking it: two taps arriving "
            "together would both see an unconsumed link"
        )
