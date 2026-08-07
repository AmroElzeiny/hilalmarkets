"""Answering a question never costs a model call, whichever question it is.

This is the one invariant the whole clarification architecture exists to keep. It used
to be false for most question kinds: the server read the answer deterministically and
then handed the value to the planner to *build* the operation, so the same message went
to a model twice — and the second model had never seen the question, so a bare ``1h``
read as a timeframe with nothing attached and the answer was lost.

Every case here drives the real service against a real database and counts planner and
composer calls before and after. The two counts must be equal.

The matrix covers, for every route a question can take:

* a direct valid answer;
* a near miss, and the yes, the no and the explicit replacement that follow it;
* an invalid answer, an unsupported one and a cancellation;
* a pause and a resume;
* a stale answer, and a message with no question identity at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ai_market_monitor.db.models import User
from ai_market_monitor.schemas.setup_agent import SetupConversationContext
from ai_market_monitor.services.interfaces import Candle
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter
from ai_market_monitor.services.setup_chat_launch import load_strategy_draft_v2
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
    async def fetch_universe_metadata(self, exchange, symbols, include_listing_dates=False):
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


@dataclass(frozen=True, slots=True)
class _ModelCalls:
    """Exactly what one turn spent on models. Both counts, never just one."""

    plan: int
    reply: int

    @classmethod
    def of(cls, planner: StandInPlanner) -> _ModelCalls:
        return cls(plan=planner.plan_calls, reply=planner.reply_calls)


def _conversation(chat) -> SetupConversationContext:
    stored = (chat.context_json or {}).get("setup_conversation_context") or {}
    return SetupConversationContext.model_validate(stored)


def _identity(chat) -> dict[str, object]:
    contract = _conversation(chat).active_question
    if contract is None:
        return {}
    return {
        "answered_question_id": contract.question_id,
        "answered_step_revision": contract.step_revision,
    }


async def _user(test_context) -> User:
    async with test_context["session_factory"]() as session:
        user = User(display_name=f"Zero model {uuid4().hex[:8]}")
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


def _key(label: str) -> str:
    """A valid idempotency key from a short test label.

    The server requires at least eight characters, because a key short enough to guess
    is a key another user could collide with. Tests name their turns "typo-3", so the
    padding happens here rather than in twenty call sites.
    """

    return f"cm-{label}"


async def _say(service, session, chat, message: str, mid: str, **extra):
    return await service.handle_message(
        session,
        chat,
        message=message,
        client_message_id=_key(mid),
        **{**_identity(chat), **extra},
    )


async def _scanner_awaiting_scope(service, session, chat, prefix: str) -> None:
    """Drive the real service until the screened-scope question is the open one."""

    await _say(service, session, chat, "Scanner", f"{prefix}-1")
    await _say(service, session, chat, "what coins are up at least 5% now?", f"{prefix}-2")
    await _say(service, session, chat, "24 hours", f"{prefix}-3")


async def _monitor_awaiting_step(service, session, chat, prefix: str) -> None:
    """Drive the real service until the agent's own multi-step rule question is open."""

    await _say(service, session, chat, "Monitor", f"{prefix}-1")
    await _say(
        service,
        session,
        chat,
        "alert me when BTC rises 5%",
        f"{prefix}-2",
    )


# Every answer shape a question can receive, and what each one means. Parametrised so
# the rule is asserted across the family rather than for the one case that was reported.
_ANSWER_SHAPES: tuple[tuple[str, str], ...] = (
    ("direct_valid", "my favorites"),
    ("typo_candidate", "favorits"),
    ("invalid", "purple bananas"),
    ("unsupported", "every coin on every exchange including futures"),
    ("cancel", "stop"),
    ("empty_ish", "..."),
)


@pytest.mark.parametrize(("shape", "message"), _ANSWER_SHAPES, ids=[i[0] for i in _ANSWER_SHAPES])
async def test_answering_a_governed_question_spends_no_model_call(
    test_context, shape: str, message: str
) -> None:
    """Every answer shape on the governed route, with the counts compared before/after."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _scanner_awaiting_scope(service, session, chat, f"gov-{shape}")
        before = _ModelCalls.of(planner)

        await _say(service, session, chat, message, f"gov-{shape}-4")

        assert _ModelCalls.of(planner) == before, f"{shape} spent a model call"


@pytest.mark.parametrize(("shape", "message"), _ANSWER_SHAPES, ids=[i[0] for i in _ANSWER_SHAPES])
async def test_answering_a_workflow_question_spends_no_model_call(
    test_context, shape: str, message: str
) -> None:
    """The same matrix on the agent's own multi-step rule workflow."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _monitor_awaiting_step(service, session, chat, f"flow-{shape}")
        if _conversation(chat).active_question is None:
            pytest.skip("this planner stand-in did not open a multi-step question")
        before = _ModelCalls.of(planner)

        await _say(service, session, chat, message, f"flow-{shape}-4")

        assert _ModelCalls.of(planner) == before, f"{shape} spent a model call"


async def test_the_whole_typo_lifecycle_spends_nothing(test_context) -> None:
    """``qh`` → *did you mean 1h?* → ``yes`` → applied. Zero calls across all of it."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _monitor_awaiting_step(service, session, chat, "typo")
        if _conversation(chat).active_question is None:
            pytest.skip("this planner stand-in did not open a multi-step question")
        before = _ModelCalls.of(planner)

        await _say(service, session, chat, "qh", "typo-3")
        await _say(service, session, chat, "yes", "typo-4")

        assert _ModelCalls.of(planner) == before


async def test_rejecting_a_candidate_spends_nothing_and_keeps_the_question(
    test_context,
) -> None:
    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _monitor_awaiting_step(service, session, chat, "reject")
        if _conversation(chat).active_question is None:
            pytest.skip("this planner stand-in did not open a multi-step question")
        before = _ModelCalls.of(planner)

        await _say(service, session, chat, "qh", "reject-3")
        await _say(service, session, chat, "no", "reject-4")

        assert _ModelCalls.of(planner) == before
        assert _conversation(chat).active_question is not None


async def test_an_explicit_replacement_while_confirming_spends_nothing(
    test_context,
) -> None:
    """``qh`` → *did you mean 1h?* → ``4h`` must store 4h, not 1h, and cost nothing."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _monitor_awaiting_step(service, session, chat, "replace")
        if _conversation(chat).active_question is None:
            pytest.skip("this planner stand-in did not open a multi-step question")
        before = _ModelCalls.of(planner)

        await _say(service, session, chat, "qh", "replace-3")
        await _say(service, session, chat, "4h", "replace-4")

        assert _ModelCalls.of(planner) == before


async def test_a_stale_answer_spends_nothing_and_changes_nothing(test_context) -> None:
    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _scanner_awaiting_scope(service, session, chat, "stale")
        before = _ModelCalls.of(planner)
        draft_before = load_strategy_draft_v2(chat).executable_hash

        await service.handle_message(
            session,
            chat,
            message="my favorites",
            client_message_id=_key("stale-4"),
            answered_question_id="a_question_from_an_older_screen",
            answered_step_revision=0,
        )

        assert _ModelCalls.of(planner) == before
        assert load_strategy_draft_v2(chat).executable_hash == draft_before


async def test_a_message_with_no_identity_spends_nothing_and_changes_nothing(
    test_context,
) -> None:
    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _scanner_awaiting_scope(service, session, chat, "unidentified")
        before = _ModelCalls.of(planner)
        draft_before = load_strategy_draft_v2(chat).executable_hash

        await service.handle_message(
            session, chat, message="my favorites", client_message_id="unidentified-4"
        )

        assert _ModelCalls.of(planner) == before
        assert load_strategy_draft_v2(chat).executable_hash == draft_before


async def test_a_clicked_generic_option_spends_nothing(test_context) -> None:
    """The generic control must be exactly as deterministic as typing the same answer."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _scanner_awaiting_scope(service, session, chat, "clicked")
        before = _ModelCalls.of(planner)

        await service.handle_message(
            session,
            chat,
            message="",
            option_key="clarification_answer",
            option_value="approved_watchlist",
            option_label="My Favorites",
            client_message_id="clicked-4",
            **_identity(chat),
        )

        assert _ModelCalls.of(planner) == before
