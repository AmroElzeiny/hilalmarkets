"""Clarification answers through the real request path, with a real database.

The other clarification suites drive the agent directly. That is the right place to
prove *reading* an answer, and the wrong place to prove anything about routing,
idempotency or persistence — those live in the launch service, and a test that never
calls it cannot see them.

So every case here goes through ``AISetupChatService.handle_message``, the same entry
point the HTTP route calls, against the real session, the real draft loader, the real
idempotency record and the real governed option route. Only the market provider and the
planner are stand-ins.

What each group proves:

* an answer the reader cannot use is refused *by the service*, keeps the question, and
  never reaches the planner;
* typing an option and clicking it produce one identical canonical state;
* an answer written under a question that has since advanced is refused rather than
  landing on whatever field is current now;
* replaying the same ``client_message_id`` returns the committed outcome again rather
  than re-deciding it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    AISetupChatMessage,
    OnDemandScanRun,
    User,
)
from ai_market_monitor.db.models.enums import ShariaUniverseMode
from ai_market_monitor.schemas.setup_agent import SetupConversationContext
from ai_market_monitor.services.interfaces import Candle
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter
from ai_market_monitor.services.setup_chat_agent import SCAN_SCOPE_QUESTION
from ai_market_monitor.services.setup_chat_launch import (
    _load_conversation_context,
    load_strategy_draft_v2,
)
from tests.integration.test_setup_chat_launch_v2 import (
    AISetupChatService,
    StandInPlanner,
    _agent,
)
from tests.integration.test_setup_chat_scanner_execution import (
    PERCENTAGES,
    _scanner_settings,
    _seed_methodology,
)

pytestmark = pytest.mark.anyio


class _Provider:
    """Enough market surface for the governed Scanner, and nothing else."""

    def __init__(self) -> None:
        self.metadata_calls = 0

    async def fetch_universe_metadata(
        self, exchange, symbols, include_listing_dates=False
    ):
        self.metadata_calls += 1
        return {
            symbol: {"percentage_24h": PERCENTAGES[symbol]}
            for symbol in symbols
            if symbol in PERCENTAGES
        }

    async def fetch_ohlcv(self, exchange, symbol, timeframe, limit):
        end = datetime.now(UTC) - timedelta(minutes=15)
        return [
            Candle(
                timestamp=end - timedelta(minutes=15 * offset),
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1000,
            )
            for offset in range(limit - 1, -1, -1)
        ]


def _conversation(chat) -> SetupConversationContext:
    stored = (chat.context_json or {}).get("setup_conversation_context") or {}
    return SetupConversationContext.model_validate(stored)


async def _user(test_context) -> User:
    async with test_context["session_factory"]() as session:
        user = User(display_name=f"Clarification path {uuid4().hex[:8]}")
        session.add(user)
        await _seed_methodology(session)
        await session.commit()
        await session.refresh(user)
        return user


def _service(test_context, planner: StandInPlanner) -> AISetupChatService:
    return AISetupChatService(
        _scanner_settings(test_context["settings"]),
        _Provider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], planner),
    )


async def _scanner_awaiting_scope(service, session, chat, prefix: str) -> None:
    """Drive the real service to the point where the screened-scope question is open."""

    await service.handle_message(
        session, chat, message="Scanner", client_message_id=f"{prefix}-1"
    )
    await service.handle_message(
        session,
        chat,
        message="what coins are up at least 5% now?",
        client_message_id=f"{prefix}-2",
    )
    await service.handle_message(
        session, chat, message="24 hours", client_message_id=f"{prefix}-3"
    )


# ---------------------------------------------------------------------------------
# An unreadable answer is refused by the service, not routed away by it
# ---------------------------------------------------------------------------------


async def test_an_unreadable_scope_answer_keeps_the_question_through_the_real_path(
    test_context,
) -> None:
    """The reported shape: the scope question is open and the answer misses it."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _scanner_awaiting_scope(service, session, chat, "scope-miss")
        opened = _conversation(chat)
        assert opened.active_question is not None
        assert opened.active_question.question_id == SCAN_SCOPE_QUESTION
        held = dict(opened.pending_read_only_scan)
        before = load_strategy_draft_v2(chat).executable_hash

        await service.handle_message(
            session, chat, message="purple bananas", client_message_id="scope-miss-4"
        )

        after = _conversation(chat)
        assert after.active_question is not None, "the question survives"
        assert after.active_question.question_id == SCAN_SCOPE_QUESTION
        assert after.pending_read_only_scan == held, "the collected scan survives"
        assert load_strategy_draft_v2(chat).executable_hash == before
        assert await session.scalar(select(OnDemandScanRun)) is None
        latest = await _latest_assistant(session, chat)
        assert "nothing is set up" not in latest.casefold()


async def test_a_scope_typo_is_asked_about_rather_than_applied(test_context) -> None:
    """A near miss on a governed choice must never become a governed selection."""

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _scanner_awaiting_scope(service, session, chat, "scope-typo")
        before = load_strategy_draft_v2(chat)

        # One deletion away from "favorites" and nothing else. A real near miss, unlike
        # "favorite", which the alias table already reads exactly.
        await service.handle_message(
            session, chat, message="favorits", client_message_id="scope-typo-4"
        )

        after = load_strategy_draft_v2(chat)
        assert after.sharia_policy.universe_mode == before.sharia_policy.universe_mode, (
            "a near miss cannot move governed Sharia policy"
        )
        assert _conversation(chat).active_question is not None


# ---------------------------------------------------------------------------------
# Typed and clicked are one canonical transition
# ---------------------------------------------------------------------------------


async def test_typing_a_scope_answer_and_clicking_it_produce_identical_state(
    test_context,
) -> None:
    """Two sessions, two input shapes, one canonical result."""

    typed_user = await _user(test_context)
    clicked_user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        typed = await service.create_session(session, typed_user.id)
        await _scanner_awaiting_scope(service, session, typed, "typed")
        await service.handle_message(
            session, typed, message="my favorites", client_message_id="typed-4"
        )
        typed_draft = load_strategy_draft_v2(typed)

    async with test_context["session_factory"]() as session:
        clicked = await service.create_session(session, clicked_user.id)
        await _scanner_awaiting_scope(service, session, clicked, "clicked")
        await service.handle_message(
            session,
            clicked,
            message="",
            option_key="screened_universe_mode",
            option_value=ShariaUniverseMode.APPROVED_WATCHLIST.value,
            option_label="My Favorites",
            client_message_id="clicked-4",
        )
        clicked_draft = load_strategy_draft_v2(clicked)

    assert typed_draft.sharia_policy.universe_mode == (
        clicked_draft.sharia_policy.universe_mode
    )
    assert typed_draft.sharia_policy.universe_mode == (
        ShariaUniverseMode.APPROVED_WATCHLIST
    )
    # The identity that matters is the canonical one: same mode, same open items.
    assert {item.unresolved_id for item in typed_draft.unresolved_fields} == {
        item.unresolved_id for item in clicked_draft.unresolved_fields
    }


# ---------------------------------------------------------------------------------
# A stale answer cannot mutate a newer workflow
# ---------------------------------------------------------------------------------


async def test_a_click_from_a_stale_screen_is_refused_by_the_service(
    test_context,
) -> None:
    """The client says which question it answered. If it has moved on, it is refused."""

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _scanner_awaiting_scope(service, session, chat, "stale")
        before = load_strategy_draft_v2(chat)

        await service.handle_message(
            session,
            chat,
            message="",
            option_key="screened_universe_mode",
            option_value=ShariaUniverseMode.APPROVED_WATCHLIST.value,
            option_label="My Favorites",
            client_message_id="stale-4",
            answered_question_id="a_question_from_an_older_screen",
            answered_step_revision=0,
        )

        after = load_strategy_draft_v2(chat)
        assert after.sharia_policy.universe_mode == before.sharia_policy.universe_mode
        assert after.executable_hash == before.executable_hash, "nothing moved"
        assert _conversation(chat).active_question is not None, "the real question stays"
        latest = await _latest_assistant(session, chat)
        assert "earlier question" in latest.casefold()


async def test_an_answer_with_no_question_identity_still_works(test_context) -> None:
    """Typing carries no identity, so the check must never make typing impossible."""

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _scanner_awaiting_scope(service, session, chat, "no-identity")

        await service.handle_message(
            session, chat, message="my favorites", client_message_id="no-identity-4"
        )

        assert load_strategy_draft_v2(chat).sharia_policy.universe_mode == (
            ShariaUniverseMode.APPROVED_WATCHLIST
        )


# ---------------------------------------------------------------------------------
# Replay returns the committed outcome instead of deciding again
# ---------------------------------------------------------------------------------


async def test_replaying_a_clarification_answer_returns_the_same_outcome(
    test_context,
) -> None:
    """The same client message id twice must not answer the question twice."""

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _scanner_awaiting_scope(service, session, chat, "replay")
        await service.handle_message(
            session, chat, message="my favorites", client_message_id="replay-4"
        )
        settled = load_strategy_draft_v2(chat)
        settled_conversation = _conversation(chat)
        messages_before = await _message_count(session, chat)

        await service.handle_message(
            session, chat, message="my favorites", client_message_id="replay-4"
        )

        replayed = load_strategy_draft_v2(chat)
        assert replayed.executable_hash == settled.executable_hash
        assert replayed.version == settled.version, "a replay creates no new version"
        assert (
            _conversation(chat).active_question_id
            == settled_conversation.active_question_id
        )
        assert await _message_count(session, chat) == messages_before, (
            "a replay writes no second answer"
        )


async def test_replaying_an_unreadable_answer_repeats_the_same_refusal(
    test_context,
) -> None:
    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _scanner_awaiting_scope(service, session, chat, "replay-miss")
        await service.handle_message(
            session, chat, message="purple bananas", client_message_id="replay-miss-4"
        )
        first = await _latest_assistant(session, chat)
        count = await _message_count(session, chat)

        await service.handle_message(
            session, chat, message="purple bananas", client_message_id="replay-miss-4"
        )

        assert await _latest_assistant(session, chat) == first
        assert await _message_count(session, chat) == count


# ---------------------------------------------------------------------------------
# What is shown is what will validate the next answer
# ---------------------------------------------------------------------------------


async def test_the_question_the_trader_reads_is_the_stored_contract(
    test_context,
) -> None:
    """Every rendered clarification, checked against the record behind it."""

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        for index, message in enumerate(
            ("Scanner", "what coins are up at least 5% now?", "24 hours", "nonsense"),
            start=1,
        ):
            await service.handle_message(
                session, chat, message=message, client_message_id=f"render-{index}"
            )
            contract = _conversation(chat).active_question
            if contract is None:
                continue
            rendered = await _latest_assistant(session, chat)
            assert contract.question in rendered, (
                f"turn {index} showed something other than the stored question"
            )


# ---------------------------------------------------------------------------------
# Stored state written by another version still works, or fails closed
# ---------------------------------------------------------------------------------


def test_a_context_written_by_a_newer_version_keeps_its_open_question() -> None:
    """A rollback must not hide a live blocker by dropping the question that clears it.

    An older build reading a newer record sees a key it does not know. Discarding the
    whole record would take the visible question with it and leave the draft blocked for
    a reason nothing on screen mentions.
    """

    contract = {
        "question_id": "sharia.universe_mode",
        "question": "Which screened assets should HilalMarkets watch?",
        "reason": "a screened universe must be chosen",
        "target_type": "universe",
        "expected_answer_schema": '{"type":"string"}',
        "mutating": True,
    }
    stored = {
        "setup_conversation_context": {
            "active_language": "ar",
            "active_question_id": "sharia.universe_mode",
            "question_text": contract["question"],
            "active_question": contract,
            "a_field_from_a_future_release": {"anything": True},
        }
    }
    loaded = _load_conversation_context(stored)
    assert loaded.active_question is not None, "the question survives the unknown key"
    assert loaded.active_question.question_id == "sharia.universe_mode"
    assert loaded.active_language == "ar"


def test_an_unreadable_workflow_never_takes_the_question_down_with_it() -> None:
    """Fail closed on the part that cannot be rebuilt, not on the whole record."""

    contract = {
        "question_id": "q_1",
        "question": "Which candle period should I use?",
        "reason": "one choice is still required",
        "target_type": "condition_creation",
        "expected_answer_schema": '{"type":"string"}',
        "mutating": True,
        "workflow_id": "supported_1",
        "step_revision": 0,
    }
    stored = {
        "setup_conversation_context": {
            "active_question_id": "q_1",
            "active_question": contract,
            # Names a different step from the question, so the pair cannot be honoured.
            "pending_workflow": {
                "workflow_id": "supported_1",
                "workflow_kind": "supported_rule",
                "step_revision": 9,
                "question_id": "q_from_another_step",
                "current_field": "trigger_timeframe",
            },
        }
    }
    loaded = _load_conversation_context(stored)
    assert loaded.active_question is not None, "the answerable question survives"
    assert loaded.pending_workflow is None, "the part that cannot be reconciled is dropped"


def test_a_legacy_context_with_no_new_fields_loads_unchanged() -> None:
    """The ordinary case: a record written before any of this existed."""

    stored = {
        "setup_conversation_context": {
            "active_language": "en",
            "recent_references": ["BTC/USDT"],
            "clarifications_asked": 2,
        }
    }
    loaded = _load_conversation_context(stored)
    assert loaded.active_question is None
    assert loaded.paused_question is None, "a new field defaults rather than failing"
    assert loaded.clarifications_asked == 2
    assert loaded.recent_references == ["BTC/USDT"]


async def _latest_assistant(session, chat) -> str:
    row = await session.scalars(
        select(AISetupChatMessage)
        .where(
            AISetupChatMessage.session_id == chat.id,
            AISetupChatMessage.role == "assistant",
        )
        .order_by(AISetupChatMessage.sequence.desc())
        .limit(1)
    )
    message = row.first()
    return message.content if message is not None else ""


async def _message_count(session, chat) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(AISetupChatMessage)
            .where(AISetupChatMessage.session_id == chat.id)
        )
        or 0
    )
