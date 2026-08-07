"""Setup Chat under real public usage: retries, races, crashes and big changes.

Every case here goes through ``AISetupChatService`` — the same entry point the HTTP
route calls — against the real database, the real turn records and the real recovery
worker. Only the market provider and the planner are stand-ins, because a paid call
cannot be part of an automated suite.

What is being proved falls into five groups:

* **Idempotency** — a repeated request is answered once, from the record.
* **Concurrency** — one mutating turn owns a session, enforced by the database.
* **Staleness** — a plan whose draft moved is re-aimed only when that is provably safe.
* **Recovery** — a turn interrupted at any point settles without re-applying anything.
* **Big changes** — nothing that could lose work is applied without being shown first.

Crashes are simulated the only honest way: by leaving a turn row in the state a crash
would leave it in, then running the real recovery worker over it. Mocking the recovery
decision would prove nothing about the code that makes it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    AISetupChatMessage,
    SetupChatDraftSnapshot,
    SetupChatPendingChange,
    SetupChatTurn,
    User,
)
from ai_market_monitor.engine.setup_turn_lifecycle import TurnStatus
from ai_market_monitor.schemas.ai_setup_chat import SetupChatErrorEnvelope
from ai_market_monitor.services.ai_setup_chat import AISetupChatService, SetupChatError
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter
from ai_market_monitor.services.setup_chat_launch import load_strategy_draft_v2
from ai_market_monitor.services.setup_chat_recovery import SetupChatRecoveryService
from tests.integration.test_setup_chat_launch_v2 import (
    MarketProvider,
    StandInPlanner,
    _agent,
    _launch_settings,
)

pytestmark = pytest.mark.anyio

BUILD = (
    "Monitor BTC/USDT when the 15m candle rises open-to-close "
    "by at least 5%, excluding ETH/USDT"
)
#: A genuinely different second version. The stand-in planner parses each message from
#: an empty draft, so an incremental phrase like "add RSI" produces no patch and no new
#: version — which would make an undo test pass while proving nothing.
BUILD_TWO = (
    "Monitor BTC/USDT when the 15m candle rises open-to-close "
    "by at least 8%, excluding ETH/USDT"
)


async def _user(test_context) -> User:
    async with test_context["session_factory"]() as session:
        user = User(display_name=f"Durability {uuid4().hex[:8]}")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def _service(test_context, planner: StandInPlanner) -> AISetupChatService:
    return AISetupChatService(
        _launch_settings(test_context["settings"]),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], planner),
    )


async def _turn(session, chat, client_message_id: str) -> SetupChatTurn:
    record = await session.scalar(
        select(SetupChatTurn).where(
            SetupChatTurn.chat_session_id == chat.id,
            SetupChatTurn.client_message_id == client_message_id,
        )
    )
    assert record is not None, f"no durable turn stored for {client_message_id}"
    return record


async def _message_count(session, chat) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(AISetupChatMessage)
            .where(AISetupChatMessage.session_id == chat.id)
        )
        or 0
    )


# ---------------------------------------------------------------------------------
# 1-3. Idempotency: the key is required, the same request replays, a reused key
#      carrying different words is refused rather than answered from the wrong record.
# ---------------------------------------------------------------------------------


async def test_1_a_message_without_a_request_id_is_refused(test_context) -> None:
    """No key means the server cannot tell a retry from a new paid turn."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        with pytest.raises(SetupChatError) as refused:
            await service.handle_message(session, chat, message=BUILD)

        assert refused.value.code == "CLIENT_MESSAGE_ID_REQUIRED"
        assert planner.plan_calls == 0, "a refused request costs nothing"
        assert load_strategy_draft_v2(chat).condition_ast is None


@pytest.mark.parametrize("bad", ["short", "has spaces!", "x" * 81])
async def test_1_a_malformed_request_id_is_refused(test_context, bad: str) -> None:
    """The shape is checked, not only the presence."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        with pytest.raises(SetupChatError) as refused:
            await service.handle_message(
                session, chat, message=BUILD, client_message_id=bad
            )
        assert refused.value.code == "CLIENT_MESSAGE_ID_REQUIRED"
        assert planner.plan_calls == 0


async def test_2_the_same_id_and_the_same_request_replays_exactly(test_context) -> None:
    """A network retry gets the committed answer back, not a second paid turn."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="replay-exact-1"
        )
        settled = load_strategy_draft_v2(chat)
        calls = planner.plan_calls
        messages = await _message_count(session, chat)

        await service.handle_message(
            session, chat, message=BUILD, client_message_id="replay-exact-1"
        )

        replayed = load_strategy_draft_v2(chat)
        assert planner.plan_calls == calls, "a replay makes no model call"
        assert replayed.executable_hash == settled.executable_hash
        assert replayed.executable_version == settled.executable_version
        assert await _message_count(session, chat) == messages, "no duplicate messages"


async def test_3_the_same_id_with_different_words_is_a_conflict(test_context) -> None:
    """Answering from the old record would show a reply to a message never sent."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="conflict-key-1"
        )
        settled = load_strategy_draft_v2(chat)
        calls = planner.plan_calls
        turns = await session.scalar(
            select(func.count()).select_from(SetupChatTurn).where(
                SetupChatTurn.chat_session_id == chat.id
            )
        )

        with pytest.raises(SetupChatError) as refused:
            await service.handle_message(
                session,
                chat,
                message="Something completely different",
                client_message_id="conflict-key-1",
            )

        assert refused.value.code == "IDEMPOTENCY_KEY_CONFLICT"
        assert planner.plan_calls == calls
        assert load_strategy_draft_v2(chat).executable_hash == settled.executable_hash
        after = await session.scalar(
            select(func.count()).select_from(SetupChatTurn).where(
                SetupChatTurn.chat_session_id == chat.id
            )
        )
        assert after == turns, "a conflict creates no second turn"


async def test_2_whitespace_only_differences_are_the_same_request(test_context) -> None:
    """A retry that gained a trailing newline is the same message, not a new one."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="whitespace-key-1"
        )
        calls = planner.plan_calls

        await service.handle_message(
            session, chat, message=f"  {BUILD}\n", client_message_id="whitespace-key-1"
        )
        assert planner.plan_calls == calls


# ---------------------------------------------------------------------------------
# 4-5. One mutating turn per session, and sessions stay independent.
# ---------------------------------------------------------------------------------


async def test_4_a_second_message_cannot_plan_while_one_is_running(test_context) -> None:
    """The claim is a database constraint, so two web workers cannot both pass it."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="holder-turn-1"
        )
        # Put the first turn back into the state a still-running turn is in, with a
        # live lease. This is exactly what the second request would see mid-flight.
        held = await _turn(session, chat, "holder-turn-1")
        held.status = TurnStatus.PLANNING.value
        held.session_claim = chat.id
        held.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        await session.commit()

        calls = planner.plan_calls
        with pytest.raises(SetupChatError) as refused:
            await service.handle_message(
                session, chat, message="Add RSI below 30", client_message_id="second-turn-1"
            )

        assert refused.value.code == "TURN_IN_PROGRESS"
        assert planner.plan_calls == calls, "the blocked turn never reached the planner"
        stored = await session.scalar(
            select(SetupChatTurn).where(
                SetupChatTurn.chat_session_id == chat.id,
                SetupChatTurn.client_message_id == "second-turn-1",
            )
        )
        assert stored is None, "a blocked message creates no turn to recover later"


async def test_4_the_database_refuses_two_live_claims_on_one_session(test_context) -> None:
    """Proved against the constraint itself, not against the code that respects it."""

    from sqlalchemy.exc import IntegrityError

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        first = SetupChatTurn(
            chat_session_id=chat.id,
            client_message_id="claim-a-0001",
            status=TurnStatus.PLANNING.value,
            session_claim=chat.id,
        )
        session.add(first)
        await session.commit()

        second = SetupChatTurn(
            chat_session_id=chat.id,
            client_message_id="claim-b-0001",
            status=TurnStatus.PLANNING.value,
            session_claim=chat.id,
        )
        session.add(second)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_5_two_sessions_for_one_user_stay_independent(test_context) -> None:
    """The claim is per session. One busy chat must not block another."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        busy = await service.create_session(session, user.id)
        other = await service.create_session(session, user.id)
        blocker = SetupChatTurn(
            chat_session_id=busy.id,
            client_message_id="busy-chat-0001",
            status=TurnStatus.PLANNING.value,
            session_claim=busy.id,
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        session.add(blocker)
        await session.commit()

        await service.handle_message(
            session, other, message=BUILD, client_message_id="other-chat-0001"
        )
        assert load_strategy_draft_v2(other).condition_ast is not None


async def test_4_a_second_web_worker_cannot_start_a_turn_on_a_busy_session(
    test_context,
) -> None:
    """Two web workers, two database sessions, one chat.

    This is the case a process-local lock cannot cover: the second request is served by
    a different process that shares nothing with the first except the database. The
    claim row is the only thing standing between them, so it is exercised across two
    real sessions rather than inside one.

    Written as two committed steps rather than as a true race: SQLite serialises
    writers, so `asyncio.gather` here would test the driver, not the product. The
    guarantee being proved — a live claim blocks everybody else — is the same.
    """

    from ai_market_monitor.db.models import AISetupChatSession

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        chat_id = chat.id
        # Worker one is mid-turn: its claim is committed and its lease is live.
        session.add(
            SetupChatTurn(
                chat_session_id=chat_id,
                client_message_id="worker-one-0001",
                status=TurnStatus.PLANNING.value,
                session_claim=chat_id,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        await session.commit()

    calls = planner.plan_calls
    async with test_context["session_factory"]() as session:
        second = await session.get(AISetupChatSession, chat_id)
        assert second is not None
        with pytest.raises(SetupChatError) as refused:
            await service.handle_message(
                session, second, message=BUILD, client_message_id="worker-two-0001"
            )
        assert refused.value.code == "TURN_IN_PROGRESS"
        assert refused.value.retryable is True, "the user is told to wait, not to fix it"

    async with test_context["session_factory"]() as session:
        settled = await session.get(AISetupChatSession, chat_id)
        assert settled is not None
        assert load_strategy_draft_v2(settled).condition_ast is None, "nothing was applied"
        assert planner.plan_calls == calls, "the blocked worker paid for nothing"
        committed = list(
            await session.scalars(
                select(SetupChatTurn).where(
                    SetupChatTurn.chat_session_id == chat_id,
                    SetupChatTurn.mutation_committed.is_(True),
                )
            )
        )
        assert committed == [], "no turn committed while the session was held"


async def test_4_a_failed_turn_gives_the_session_back_at_once(test_context) -> None:
    """After an error the user must be able to try again immediately.

    Waiting for the lease to expire and a recovery cycle to notice would mean minutes
    of being unable to send anything, after a failure that was not the user's fault.
    """

    user = await _user(test_context)
    planner = StandInPlanner(failure=RuntimeError("provider exploded"))
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        with pytest.raises(Exception):  # noqa: B017 - any failure class settles the turn
            await service.handle_message(
                session, chat, message=BUILD, client_message_id="failing-turn-01"
            )

    async with test_context["session_factory"]() as session:
        from ai_market_monitor.db.models import AISetupChatSession

        reloaded = await session.get(AISetupChatSession, chat.id)
        assert reloaded is not None
        await service.record_turn_failure(
            session,
            reloaded,
            envelope=SetupChatErrorEnvelope(
                error_code="PROVIDER_FAILED",
                request_id="req-1",
                stage="interpret",
                retryable=True,
                message="Something went wrong. Please try again.",
            ),
            client_message_id="failing-turn-01",
        )
        await session.commit()

        held = await session.scalar(
            select(SetupChatTurn).where(SetupChatTurn.session_claim == reloaded.id)
        )
        assert held is None, "a failed turn must not keep holding the session"

    # And a fresh message is accepted straight away.
    healthy = _service(test_context, StandInPlanner())
    async with test_context["session_factory"]() as session:
        from ai_market_monitor.db.models import AISetupChatSession

        reloaded = await session.get(AISetupChatSession, chat.id)
        assert reloaded is not None
        await healthy.handle_message(
            session, reloaded, message=BUILD, client_message_id="after-failure-01"
        )
        assert load_strategy_draft_v2(reloaded).condition_ast is not None


# ---------------------------------------------------------------------------------
# 6. No database lock is held across the model call.
# ---------------------------------------------------------------------------------


def test_6_the_draft_lock_is_taken_after_the_model_call_not_before() -> None:
    """Read from the code that does it, because a timing test here would be a guess.

    The row lock is taken inside the ``EXECUTING`` checkpoint, which the agent reaches
    only after its planner call has returned. Holding it across the provider call would
    make one slow answer block the user's whole session.
    """

    import inspect

    from ai_market_monitor.services.setup_chat_launch import SetupChatLaunchService

    source = inspect.getsource(SetupChatLaunchService._turn_stage_callback)
    lock_at = source.index("with_for_update=True")
    guard_at = source.index("TurnStatus.EXECUTING.value")
    assert guard_at < lock_at, "the lock must be inside the post-planning checkpoint"
    # And nowhere else in the turn path does the draft get locked for update.
    handle = inspect.getsource(SetupChatLaunchService._run_agent_turn)
    assert "with_for_update" not in handle


# ---------------------------------------------------------------------------------
# 7-9. Stale plans: re-aimed when provably safe, refused otherwise.
# ---------------------------------------------------------------------------------


def test_7_an_unrelated_concurrent_edit_allows_a_deterministic_rebase() -> None:
    from ai_market_monitor.engine.plan_freshness import PlanningAuthority, plan_freshness

    before = PlanningAuthority(
        executable_hash="a",
        workflow_state_hash="w",
        active_question_id=None,
        active_step_revision=None,
        conditions=(("c1", "pct|gte|5|percent|15m|up"),),
        condition_order=("c1",),
        methodology_id=None,
        methodology_version=None,
        watchlist_id=None,
        watchlist_version=None,
        universe_mode="eligible_market",
        capability_registry_version="r1",
        approved=False,
        approval_hash=None,
        mode="monitor",
    )
    # Another tab added a second, unrelated rule. The rule this plan edits is untouched.
    now = replace(
        before,
        executable_hash="b",
        conditions=(("c1", "pct|gte|5|percent|15m|up"), ("c2", "rsi|lt|30||1h|")),
        condition_order=("c1", "c2"),
    )
    verdict = plan_freshness(
        before,
        now,
        operation_kinds=("update_condition",),
        target_condition_ids=("c1",),
    )
    assert verdict.decision == "rebase"


def test_8_a_deleted_or_edited_target_refuses_the_plan() -> None:
    from ai_market_monitor.engine.plan_freshness import PlanningAuthority, plan_freshness

    base = PlanningAuthority(
        executable_hash="a",
        workflow_state_hash="w",
        active_question_id=None,
        active_step_revision=None,
        conditions=(("c1", "pct|gte|5|percent|15m|up"), ("c2", "rsi|lt|30||1h|")),
        condition_order=("c1", "c2"),
        methodology_id=None,
        methodology_version=None,
        watchlist_id=None,
        watchlist_version=None,
        universe_mode="eligible_market",
        capability_registry_version="r1",
        approved=False,
        approval_hash=None,
        mode="monitor",
    )
    deleted = replace(
        base,
        executable_hash="b",
        conditions=(("c2", "rsi|lt|30||1h|"),),
        condition_order=("c2",),
    )
    assert plan_freshness(
        base, deleted, operation_kinds=("update_condition",), target_condition_ids=("c1",)
    ).is_refusal

    edited = replace(
        base,
        executable_hash="b",
        conditions=(("c1", "pct|gte|9|percent|15m|up"), ("c2", "rsi|lt|30||1h|")),
    )
    assert plan_freshness(
        base, edited, operation_kinds=("update_condition",), target_condition_ids=("c1",)
    ).is_refusal

    reordered = replace(
        base,
        executable_hash="b",
        conditions=(("c2", "rsi|lt|30||1h|"), ("c1", "pct|gte|5|percent|15m|up")),
        condition_order=("c2", "c1"),
    )
    # "Remove the second condition" was resolved to an id under the old ordering.
    assert plan_freshness(
        base,
        reordered,
        operation_kinds=("remove_condition",),
        target_condition_ids=("c1",),
    ).is_refusal


def test_9_a_governed_change_always_refuses_a_stale_plan() -> None:
    from ai_market_monitor.engine.plan_freshness import PlanningAuthority, plan_freshness

    base = PlanningAuthority(
        executable_hash="a",
        workflow_state_hash="w",
        active_question_id=None,
        active_step_revision=None,
        conditions=(("c1", "pct|gte|5|percent|15m|up"),),
        condition_order=("c1",),
        methodology_id="m1",
        methodology_version="1",
        watchlist_id=None,
        watchlist_version=None,
        universe_mode="eligible_market",
        capability_registry_version="r1",
        approved=False,
        approval_hash=None,
        mode="monitor",
    )
    for field, value in (
        ("methodology_id", "m2"),
        ("methodology_version", "2"),
        ("universe_mode", "approved_watchlist"),
        ("capability_registry_version", "r2"),
        ("mode", "scanner"),
        ("approved", True),
    ):
        moved = replace(base, executable_hash="b", **{field: value})
        verdict = plan_freshness(
            base, moved, operation_kinds=("set_fields",), target_condition_ids=()
        )
        assert verdict.is_refusal, f"{field} moving must refuse the plan"


def test_9_a_whole_draft_operation_is_never_rebased() -> None:
    from ai_market_monitor.engine.plan_freshness import PlanningAuthority, plan_freshness

    base = PlanningAuthority(
        executable_hash="a",
        workflow_state_hash="w",
        active_question_id=None,
        active_step_revision=None,
        conditions=(("c1", "x"),),
        condition_order=("c1",),
        methodology_id=None,
        methodology_version=None,
        watchlist_id=None,
        watchlist_version=None,
        universe_mode="eligible_market",
        capability_registry_version="r1",
        approved=False,
        approval_hash=None,
        mode="monitor",
    )
    moved = replace(
        base,
        executable_hash="b",
        conditions=(("c1", "x"), ("c2", "y")),
        condition_order=("c1", "c2"),
    )
    for kind in ("replace_groups", "restore_snapshot", "set_sharia_policy"):
        assert plan_freshness(
            base, moved, operation_kinds=(kind,), target_condition_ids=()
        ).is_refusal, f"{kind} must never be re-aimed"


def test_7_an_answered_question_alone_refuses_a_plan_written_under_the_old_one() -> None:
    from ai_market_monitor.engine.plan_freshness import PlanningAuthority, plan_freshness

    base = PlanningAuthority(
        executable_hash="a",
        workflow_state_hash="w",
        active_question_id="q1",
        active_step_revision=0,
        conditions=(),
        condition_order=(),
        methodology_id=None,
        methodology_version=None,
        watchlist_id=None,
        watchlist_version=None,
        universe_mode="eligible_market",
        capability_registry_version="r1",
        approved=False,
        approval_hash=None,
        mode="monitor",
    )
    advanced = replace(base, workflow_state_hash="w2", active_step_revision=1)
    assert plan_freshness(
        base, advanced, operation_kinds=("update_unresolved",), target_condition_ids=()
    ).is_refusal


# ---------------------------------------------------------------------------------
# 10-18. Crash recovery, at every checkpoint.
# ---------------------------------------------------------------------------------


async def _crashed_at(test_context, service, planner, status: TurnStatus, key: str):
    """Build a real committed turn, then rewind its record to a crashed state."""

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, (await _user(test_context)).id)
        await service.handle_message(session, chat, message=BUILD, client_message_id=key)
        record = await _turn(session, chat, key)
        record.status = status.value
        record.session_claim = chat.id
        # Long past its lease, which is what a crash looks like from outside.
        record.lease_expires_at = datetime.now(UTC) - timedelta(hours=1)
        if status in {TurnStatus.RECEIVED, TurnStatus.PLANNING, TurnStatus.PLANNED}:
            # Nothing had committed at these points.
            record.execution_result_json = None
            record.assistant_message_id = None
            record.mutation_committed = False
        elif status in {TurnStatus.EXECUTED, TurnStatus.COMPOSING}:
            # The mutation committed; the reply had not been written.
            record.assistant_message_id = None
        await session.commit()
        return chat.id


@pytest.mark.parametrize(
    "status",
    [TurnStatus.RECEIVED, TurnStatus.PLANNING, TurnStatus.PLANNED],
)
async def test_10_12_a_crash_before_execution_releases_the_turn(
    test_context, status: TurnStatus
) -> None:
    """Nothing committed, so the turn is abandoned and the session comes back."""

    planner = StandInPlanner()
    service = _service(test_context, planner)
    chat_id = await _crashed_at(test_context, service, planner, status, f"crash-{status.value}")

    async with test_context["session_factory"]() as session:
        outcome = await SetupChatRecoveryService(
            _launch_settings(test_context["settings"])
        ).run_once(session)
        assert outcome.examined >= 1

    async with test_context["session_factory"]() as session:
        record = await session.scalar(
            select(SetupChatTurn).where(SetupChatTurn.chat_session_id == chat_id)
        )
        assert record is not None
        assert record.status == TurnStatus.ABANDONED.value
        assert record.session_claim is None, "the session must be usable again"


async def test_13_a_crash_during_execution_never_reapplies_the_mutation(
    test_context,
) -> None:
    """``EXECUTING`` with no stored result means nothing committed. It is released."""

    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, (await _user(test_context)).id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="crash-executing-1"
        )
        record = await _turn(session, chat, "crash-executing-1")
        applied = load_strategy_draft_v2(chat)
        record.status = TurnStatus.EXECUTING.value
        record.execution_result_json = None
        record.session_claim = chat.id
        record.lease_expires_at = datetime.now(UTC) - timedelta(hours=1)
        await session.commit()
        chat_id = chat.id

    async with test_context["session_factory"]() as session:
        await SetupChatRecoveryService(
            _launch_settings(test_context["settings"])
        ).run_once(session)

    async with test_context["session_factory"]() as session:
        from ai_market_monitor.db.models import AISetupChatSession

        settled = await session.get(AISetupChatSession, chat_id)
        assert settled is not None
        after = load_strategy_draft_v2(settled)
        assert after.executable_version == applied.executable_version
        assert after.executable_hash == applied.executable_hash, "recovery applied nothing"


@pytest.mark.parametrize("status", [TurnStatus.EXECUTED, TurnStatus.COMPOSING])
async def test_14_15_a_crash_after_execution_answers_from_the_stored_result(
    test_context, status: TurnStatus
) -> None:
    """The draft already moved. Recovery writes the reply and never plans again."""

    planner = StandInPlanner()
    service = _service(test_context, planner)
    chat_id = await _crashed_at(
        test_context, service, planner, status, f"crash-after-{status.value}"
    )
    calls = planner.plan_calls

    async with test_context["session_factory"]() as session:
        outcome = await SetupChatRecoveryService(
            _launch_settings(test_context["settings"])
        ).run_once(session)
        assert outcome.recovered >= 1

    assert planner.plan_calls == calls, "recovery must never call the planner"

    async with test_context["session_factory"]() as session:
        record = await session.scalar(
            select(SetupChatTurn).where(SetupChatTurn.chat_session_id == chat_id)
        )
        assert record is not None
        assert record.status == TurnStatus.COMPLETED.value
        assert record.assistant_message_id is not None, "the user gets their answer"
        assert record.session_claim is None
        assert (record.recovery_usage_json or {}).get("model_calls") == 0
        reply = await session.get(AISetupChatMessage, record.assistant_message_id)
        assert reply is not None
        assert reply.payload.get("model_call_count") == 0


async def test_16_the_recovery_worker_is_idempotent_across_restarts(test_context) -> None:
    """Running it twice settles the turn once and writes one reply."""

    planner = StandInPlanner()
    service = _service(test_context, planner)
    chat_id, before = await _crossed_worker_setup(test_context, service)

    settings = _launch_settings(test_context["settings"])
    async with test_context["session_factory"]() as session:
        await SetupChatRecoveryService(settings).run_once(session)
    async with test_context["session_factory"]() as session:
        # A brand new worker, exactly as a restart would create.
        second = await SetupChatRecoveryService(settings).run_once(session)
        assert second.examined == 0, "a settled turn is not examined again"

    async with test_context["session_factory"]() as session:
        replies = await session.scalar(
            select(func.count())
            .select_from(AISetupChatMessage)
            .where(
                AISetupChatMessage.session_id == chat_id,
                AISetupChatMessage.role == "assistant",
            )
        )
        record = await session.scalar(
            select(SetupChatTurn).where(SetupChatTurn.chat_session_id == chat_id)
        )
        assert record is not None and record.status == TurnStatus.COMPLETED.value
        assert record.assistant_message_id is not None
        assert replies == before + 1, "recovery wrote one answer; the restart added none"


async def _crossed_worker_setup(test_context, service) -> tuple[object, int]:
    """A committed turn whose reply never got written, exactly as a crash leaves it.

    Returns the chat id and how many assistant messages exist afterwards, because the
    build turn may legitimately have written more than one and an absolute count would
    make the test assert something it does not mean.
    """

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, (await _user(test_context)).id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="worker-restart-1"
        )
        record = await _turn(session, chat, "worker-restart-1")
        # Delete the reply so recovery has real work to do.
        if record.assistant_message_id is not None:
            reply = await session.get(AISetupChatMessage, record.assistant_message_id)
            record.assistant_message_id = None
            if reply is not None:
                await session.delete(reply)
        record.status = TurnStatus.EXECUTED.value
        record.session_claim = chat.id
        record.lease_expires_at = datetime.now(UTC) - timedelta(hours=1)
        await session.commit()
        remaining = int(
            await session.scalar(
                select(func.count())
                .select_from(AISetupChatMessage)
                .where(
                    AISetupChatMessage.session_id == chat.id,
                    AISetupChatMessage.role == "assistant",
                )
            )
            or 0
        )
        return chat.id, remaining


async def test_17_a_lease_stops_a_second_worker_claiming_one_turn(test_context) -> None:
    """Once a worker holds a live lease, no other worker may take the same turn.

    Proved against ``_claim`` itself rather than by racing two cycles. The claim is a
    conditional UPDATE that only matches an expired lease, so the guarantee is in the
    WHERE clause — and asserting it directly holds on every database, instead of
    depending on how one of them happens to serialise concurrent writes.
    """

    planner = StandInPlanner()
    service = _service(test_context, planner)
    chat_id, _ = await _crossed_worker_setup(test_context, service)
    settings = _launch_settings(test_context["settings"])

    async with test_context["session_factory"]() as session:
        stalled = await session.scalar(
            select(SetupChatTurn).where(SetupChatTurn.chat_session_id == chat_id)
        )
        assert stalled is not None
        turn_id = stalled.id

        first = SetupChatRecoveryService(settings)
        second = SetupChatRecoveryService(settings)
        assert first.worker_id != second.worker_id, "each worker is its own owner"

        won = await first._claim(session, turn_id)  # noqa: SLF001
        assert won is not None, "the first worker takes the expired lease"
        assert won.lease_owner == first.worker_id
        assert won.lease_expires_at is not None

        lost = await second._claim(session, turn_id)  # noqa: SLF001
        assert lost is None, "a live lease is not claimable by anybody else"
        await session.refresh(won)
        assert won.lease_owner == first.worker_id, "the owner did not change"


async def test_17_two_recovery_cycles_settle_a_turn_once(test_context) -> None:
    """Whatever order workers run in, one answer is written and the turn settles once."""

    planner = StandInPlanner()
    service = _service(test_context, planner)
    chat_id, before = await _crossed_worker_setup(test_context, service)
    settings = _launch_settings(test_context["settings"])

    async def cycle() -> int:
        async with test_context["session_factory"]() as session:
            return (await SetupChatRecoveryService(settings).run_once(session)).recovered

    recovered = [await cycle(), await cycle()]
    assert sum(recovered) == 1, f"exactly one cycle recovers the turn, got {recovered}"

    async with test_context["session_factory"]() as session:
        replies = await session.scalar(
            select(func.count())
            .select_from(AISetupChatMessage)
            .where(
                AISetupChatMessage.session_id == chat_id,
                AISetupChatMessage.role == "assistant",
            )
        )
        assert replies == before + 1, "one new answer, not two"


async def test_18_a_committed_operation_is_never_applied_twice(test_context) -> None:
    """Recovery of a committed turn leaves the draft byte-for-byte identical."""

    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, (await _user(test_context)).id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="no-double-apply-1"
        )
        committed = load_strategy_draft_v2(chat)
        record = await _turn(session, chat, "no-double-apply-1")
        record.status = TurnStatus.EXECUTED.value
        record.assistant_message_id = None
        record.session_claim = chat.id
        record.lease_expires_at = datetime.now(UTC) - timedelta(hours=1)
        await session.commit()
        chat_id = chat.id

    settings = _launch_settings(test_context["settings"])
    for _ in range(3):
        async with test_context["session_factory"]() as session:
            await SetupChatRecoveryService(settings).run_once(session)

    async with test_context["session_factory"]() as session:
        from ai_market_monitor.db.models import AISetupChatSession

        settled = await session.get(AISetupChatSession, chat_id)
        assert settled is not None
        after = load_strategy_draft_v2(settled)
        assert after.executable_hash == committed.executable_hash
        assert after.executable_version == committed.executable_version


async def test_the_recovery_worker_can_be_switched_off(test_context) -> None:
    """An emergency switch stops new recovery work without hiding stored results."""

    settings = _launch_settings(test_context["settings"]).model_copy(
        update={"setup_chat_recovery_disabled": True}
    )
    async with test_context["session_factory"]() as session:
        outcome = await SetupChatRecoveryService(settings).run_once(session)
        assert outcome.skipped == 1
        assert outcome.examined == 0


# ---------------------------------------------------------------------------------
# 19-22. Undo, Restore and Reset.
# ---------------------------------------------------------------------------------


async def test_19_20_undo_reverts_the_last_material_change_only(test_context) -> None:
    """A conversation-only turn made no version, so it can never be an undo target."""

    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, (await _user(test_context)).id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="undo-build-0001"
        )
        built = load_strategy_draft_v2(chat)
        # A chat turn that changes nothing. Undo must ignore it entirely.
        await service.handle_message(
            session, chat, message="Hi, how are you?", client_message_id="undo-chat-0001"
        )
        assert load_strategy_draft_v2(chat).executable_hash == built.executable_hash

        await service.handle_message(
            session,
            chat,
            message=BUILD_TWO,
            client_message_id="undo-second-0001",
        )
        second = load_strategy_draft_v2(chat)
        assert second.executable_hash != built.executable_hash

        calls = planner.plan_calls
        await service.handle_draft_action(
            session,
            chat,
            action="undo_last_material_change",
            client_message_id="undo-action-0001",
        )

        after = load_strategy_draft_v2(chat)
        assert planner.plan_calls == calls, "undo never asks a model to rebuild the past"
        assert after.executable_hash == built.executable_hash, "the earlier rules are back"
        assert after.executable_version > second.executable_version, (
            "undo makes a new version rather than rewriting history"
        )
        assert not after.approval.approved, "undo clears any approval"


async def test_19_undo_is_idempotent_under_the_same_key(test_context) -> None:
    """A double-clicked Undo undoes once."""

    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, (await _user(test_context)).id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="undo-idem-b-001"
        )
        await service.handle_message(
            session,
            chat,
            message=BUILD_TWO,
            client_message_id="undo-idem-s-001",
        )
        await service.handle_draft_action(
            session,
            chat,
            action="undo_last_material_change",
            client_message_id="undo-idem-a-001",
        )
        once = load_strategy_draft_v2(chat)

        await service.handle_draft_action(
            session,
            chat,
            action="undo_last_material_change",
            client_message_id="undo-idem-a-001",
        )
        assert load_strategy_draft_v2(chat).executable_hash == once.executable_hash
        assert load_strategy_draft_v2(chat).executable_version == once.executable_version


async def test_19_undo_with_nothing_to_undo_says_so(test_context) -> None:
    """It reports the truth instead of pretending something was undone."""

    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, (await _user(test_context)).id)
        before = load_strategy_draft_v2(chat)
        await service.handle_draft_action(
            session,
            chat,
            action="undo_last_material_change",
            client_message_id="undo-empty-0001",
        )
        assert load_strategy_draft_v2(chat).executable_hash == before.executable_hash


async def test_21_restore_creates_a_new_version_and_keeps_history(test_context) -> None:
    """The historical snapshot is never mutated or replaced."""

    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, (await _user(test_context)).id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="restore-b-00001"
        )
        first = load_strategy_draft_v2(chat)
        await service.handle_message(
            session,
            chat,
            message=BUILD_TWO,
            client_message_id="restore-s-00001",
        )
        latest = load_strategy_draft_v2(chat)

        target = await session.scalar(
            select(SetupChatDraftSnapshot).where(
                SetupChatDraftSnapshot.chat_session_id == chat.id,
                SetupChatDraftSnapshot.executable_hash == first.executable_hash,
            )
        )
        assert target is not None, "the earlier version was saved"
        snapshot_payload = dict(target.draft_json)
        count_before = await session.scalar(
            select(func.count()).select_from(SetupChatDraftSnapshot).where(
                SetupChatDraftSnapshot.chat_session_id == chat.id
            )
        )

        await service.handle_draft_action(
            session,
            chat,
            action="restore_snapshot",
            client_message_id="restore-a-00001",
            snapshot_id=str(target.id),
            expected_executable_version=target.executable_version,
            confirmed=True,
        )

        after = load_strategy_draft_v2(chat)
        assert after.executable_hash == first.executable_hash, "the old rules are back"
        assert after.executable_version > latest.executable_version, "as a new version"
        await session.refresh(target)
        assert target.draft_json == snapshot_payload, "the snapshot itself is untouched"
        count_after = await session.scalar(
            select(func.count()).select_from(SetupChatDraftSnapshot).where(
                SetupChatDraftSnapshot.chat_session_id == chat.id
            )
        )
        assert count_after >= count_before, "no saved version was deleted"
        assert not after.approval.approved, "a restored plan is never silently active"


async def test_21_restoring_a_snapshot_this_user_does_not_own_is_refused(
    test_context,
) -> None:
    """A snapshot id alone must never reach into somebody else's setup."""

    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        mine = await service.create_session(session, (await _user(test_context)).id)
        theirs = await service.create_session(session, (await _user(test_context)).id)
        await service.handle_message(
            session, theirs, message=BUILD, client_message_id="other-owner-001"
        )
        foreign = await session.scalar(
            select(SetupChatDraftSnapshot).where(
                SetupChatDraftSnapshot.chat_session_id == theirs.id
            )
        )
        assert foreign is not None

        with pytest.raises(SetupChatError) as refused:
            await service.handle_draft_action(
                session,
                mine,
                action="restore_snapshot",
                client_message_id="cross-owner-0001",
                snapshot_id=str(foreign.id),
                confirmed=True,
            )
        assert refused.value.code == "SNAPSHOT_NOT_FOUND"


async def test_22_reset_requires_confirmation_and_keeps_saved_versions(
    test_context,
) -> None:
    """Clearing a draft loses work, so it never happens on a bare request."""

    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, (await _user(test_context)).id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="reset-build-0001"
        )
        built = load_strategy_draft_v2(chat)
        snapshots_before = await session.scalar(
            select(func.count()).select_from(SetupChatDraftSnapshot).where(
                SetupChatDraftSnapshot.chat_session_id == chat.id
            )
        )

        with pytest.raises(SetupChatError) as refused:
            await service.handle_draft_action(
                session,
                chat,
                action="reset_current_draft",
                client_message_id="reset-noconf-001",
            )
        assert refused.value.code == "RESET_CONFIRMATION_REQUIRED"
        assert load_strategy_draft_v2(chat).executable_hash == built.executable_hash

        await service.handle_draft_action(
            session,
            chat,
            action="reset_current_draft",
            client_message_id="reset-confirm-01",
            confirmed=True,
        )
        after = load_strategy_draft_v2(chat)
        assert after.condition_ast is None, "the draft is clear"
        assert after.executable_version > built.executable_version
        snapshots_after = await session.scalar(
            select(func.count()).select_from(SetupChatDraftSnapshot).where(
                SetupChatDraftSnapshot.chat_session_id == chat.id
            )
        )
        assert snapshots_after >= snapshots_before, "reset deletes no saved version"


async def test_32_reset_does_not_touch_monitors_it_was_not_asked_about(
    test_context,
) -> None:
    """Reset clears one draft. Archiving, stopping and deleting are other actions."""

    from ai_market_monitor.db.models import SetupInstance

    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, (await _user(test_context)).id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="reset-scope-0001"
        )
        before = await session.scalar(select(func.count()).select_from(SetupInstance))

        await service.handle_draft_action(
            session,
            chat,
            action="reset_current_draft",
            client_message_id="reset-scope-0002",
            confirmed=True,
        )
        after = await session.scalar(select(func.count()).select_from(SetupInstance))
        assert after == before, "no running monitor was touched"


# ---------------------------------------------------------------------------------
# 23-27. Destructive changes are proposals until confirmed.
# ---------------------------------------------------------------------------------


def test_23_the_classifier_decides_from_state_not_from_wording() -> None:
    """Every reason maps to a canonical fact, so phrasing cannot change the verdict."""

    from ai_market_monitor.engine.destructive_change import (
        DestructiveReason,
        classify_destructive_change,
    )
    from ai_market_monitor.engine.draft_diff import DraftChange
    from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2

    empty = StrategyDraftV2()
    removals = [
        DraftChange(kind="condition_removed", target="c1", detail="one"),
        DraftChange(kind="condition_removed", target="c2", detail="two"),
    ]
    verdict = classify_destructive_change(
        before=empty, after=empty, changes=removals, operation_kinds=("remove_condition",) * 2
    )
    assert DestructiveReason.MULTIPLE_CONDITIONS_REMOVED in verdict.reasons
    assert verdict.requires_confirmation
    assert verdict.summary_lines, "the user is told in plain words"

    methodology = [
        DraftChange(
            kind="sharia_policy_changed",
            target="sharia_policy.methodology_id",
            before="a",
            after="b",
        )
    ]
    assert DestructiveReason.METHODOLOGY_CHANGED in classify_destructive_change(
        before=empty, after=empty, changes=methodology
    ).reasons

    cleared = [
        DraftChange(
            kind="sharia_policy_changed",
            target="sharia_policy.explicit_symbols",
            before="BTC/USDT",
            after=None,
        )
    ]
    assert DestructiveReason.ASSET_SELECTION_CLEARED in classify_destructive_change(
        before=empty, after=empty, changes=cleared
    ).reasons

    # Adding to a list is not destructive. Only emptying it is.
    added = [
        DraftChange(
            kind="sharia_policy_changed",
            target="sharia_policy.explicit_symbols",
            before=None,
            after="BTC/USDT",
        )
    ]
    assert DestructiveReason.ASSET_SELECTION_CLEARED not in classify_destructive_change(
        before=empty, after=empty, changes=added
    ).reasons


def test_23_one_ordinary_edit_is_not_treated_as_destructive() -> None:
    """Confirming every small change would teach the user to click through."""

    from ai_market_monitor.engine.destructive_change import classify_destructive_change
    from ai_market_monitor.engine.draft_diff import DraftChange
    from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2

    empty = StrategyDraftV2()
    verdict = classify_destructive_change(
        before=empty,
        after=empty,
        changes=[
            DraftChange(kind="threshold_changed", target="c1", before="5", after="6"),
        ],
        operation_kinds=("update_condition",),
    )
    assert not verdict.requires_confirmation


def test_23_the_cheap_screen_never_says_no_to_a_wholesale_change() -> None:
    """A false "no" here would apply a destructive change with no confirmation."""

    from types import SimpleNamespace

    from ai_market_monitor.engine.destructive_change import may_be_destructive

    for kind in ("replace_groups", "restore_snapshot", "set_sharia_policy"):
        assert may_be_destructive([SimpleNamespace(kind=kind, fields=None)])
    assert may_be_destructive(
        [SimpleNamespace(kind="remove_condition", fields=None)] * 2
    )
    # Adding one rule, or renaming the plan, must not pay for a projection.
    assert not may_be_destructive([SimpleNamespace(kind="add_condition", fields=None)])


async def test_24_25_a_destructive_change_waits_and_applies_exactly_once(
    test_context,
) -> None:
    """Nothing moves until the user says yes; then the stored operations run once."""

    from ai_market_monitor.schemas.setup_authorization import AuthorizedPatchOperation
    from ai_market_monitor.schemas.setup_change_review import SetupDraftDiff

    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, (await _user(test_context)).id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="destr-build-0001"
        )
        built = load_strategy_draft_v2(chat)
        node = built.condition_ast.walk_conditions()[0] if hasattr(
            built.condition_ast, "walk_conditions"
        ) else next(
            item for item in built.condition_ast.walk() if item.node_type.value == "condition"
        )

        # A proposal the server itself would have built: remove the only rule.
        removal = AuthorizedPatchOperation(
            operation_id="op-remove-1",
            authorizing_segment_id="server_draft_action",
            kind="remove_condition",
            target_condition_id=node.node_id,
        )
        proposal = SetupChatPendingChange(
            chat_session_id=chat.id,
            user_id=chat.user_id,
            proposal_id=uuid4().hex,
            status="pending",
            executable_hash=built.executable_hash,
            workflow_state_hash=built.workflow_state_hash,
            executable_version=built.executable_version,
            operations_json=[removal.model_dump(mode="json")],
            operation_payload_hash="",
            diff_json=SetupDraftDiff().model_dump(mode="json"),
            reasons_json=["multiple_conditions_removed"],
            summary_json=["This removes more than one of your rules."],
            invalidates_approval=False,
            governance_notes_json=[],
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            created_at=datetime.now(UTC),
        )
        from ai_market_monitor.schemas.setup_change_review import PendingDestructiveChange

        proposal.operation_payload_hash = PendingDestructiveChange.model_validate(
            {
                "proposal_id": proposal.proposal_id,
                "source_turn_id": proposal.proposal_id,
                "client_message_id": "",
                "executable_hash": proposal.executable_hash,
                "workflow_state_hash": proposal.workflow_state_hash,
                "executable_version": proposal.executable_version,
                "operations": proposal.operations_json,
                "diff": proposal.diff_json,
                "reasons": proposal.reasons_json,
                "summary_lines": proposal.summary_json,
                "invalidates_approval": False,
                "governance_notes": [],
                "created_at": proposal.created_at,
                "expires_at": proposal.expires_at,
                "status": "pending",
            }
        ).operation_payload_hash
        session.add(proposal)
        await session.commit()

        # 23. While a proposal waits, an ordinary message changes nothing.
        calls = planner.plan_calls
        await service.handle_message(
            session,
            chat,
            message="Also add RSI below 30",
            client_message_id="destr-blocked-001",
        )
        assert load_strategy_draft_v2(chat).executable_hash == built.executable_hash
        assert planner.plan_calls == calls, "a blocked message costs nothing"

        # 24. Confirming applies the stored operations, with no model call.
        await service.handle_draft_action(
            session,
            chat,
            action="confirm_pending_change",
            client_message_id="destr-confirm-01",
            proposal_id=proposal.proposal_id,
            confirmed=True,
        )
        after = load_strategy_draft_v2(chat)
        assert planner.plan_calls == calls, "confirming never re-plans"
        assert after.executable_hash != built.executable_hash, "the change happened"
        await session.refresh(proposal)
        assert proposal.status == "applied"

        # And confirming again does nothing at all.
        with pytest.raises(SetupChatError) as settled:
            await service.handle_draft_action(
                session,
                chat,
                action="confirm_pending_change",
                client_message_id="destr-confirm-02",
                proposal_id=proposal.proposal_id,
                confirmed=True,
            )
        assert settled.value.code == "PENDING_CHANGE_ALREADY_SETTLED"
        assert load_strategy_draft_v2(chat).executable_hash == after.executable_hash


async def test_25_cancelling_a_proposal_leaves_the_draft_untouched(test_context) -> None:
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, (await _user(test_context)).id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="cancel-build-001"
        )
        built = load_strategy_draft_v2(chat)
        proposal = SetupChatPendingChange(
            chat_session_id=chat.id,
            user_id=chat.user_id,
            proposal_id=uuid4().hex,
            status="pending",
            executable_hash=built.executable_hash,
            workflow_state_hash=built.workflow_state_hash,
            executable_version=built.executable_version,
            operations_json=[],
            operation_payload_hash="x",
            diff_json={},
            reasons_json=["draft_reset"],
            summary_json=["This clears your setup and starts it again."],
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            created_at=datetime.now(UTC),
        )
        session.add(proposal)
        await session.commit()

        await service.handle_draft_action(
            session,
            chat,
            action="cancel_pending_change",
            client_message_id="cancel-action-01",
            proposal_id=proposal.proposal_id,
        )
        await session.refresh(proposal)
        assert proposal.status == "cancelled"
        assert load_strategy_draft_v2(chat).executable_hash == built.executable_hash


async def test_26_a_stale_proposal_can_never_be_applied(test_context) -> None:
    """The draft moved after it was offered, so the stored operations are refused."""

    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, (await _user(test_context)).id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="stale-build-0001"
        )
        proposal = SetupChatPendingChange(
            chat_session_id=chat.id,
            user_id=chat.user_id,
            proposal_id=uuid4().hex,
            status="pending",
            # Built against a draft that is not the current one.
            executable_hash="0" * 64,
            workflow_state_hash="0" * 64,
            executable_version=1,
            operations_json=[],
            operation_payload_hash="x",
            diff_json={},
            reasons_json=["condition_tree_replaced"],
            summary_json=["This replaces all of your rules with new ones."],
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            created_at=datetime.now(UTC),
        )
        session.add(proposal)
        await session.commit()
        before = load_strategy_draft_v2(chat)

        await service.handle_draft_action(
            session,
            chat,
            action="confirm_pending_change",
            client_message_id="stale-confirm-01",
            proposal_id=proposal.proposal_id,
            confirmed=True,
        )
        await session.refresh(proposal)
        assert proposal.status == "stale"
        assert load_strategy_draft_v2(chat).executable_hash == before.executable_hash


async def test_26_an_expired_proposal_is_never_applied(test_context) -> None:
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, (await _user(test_context)).id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="expire-build-001"
        )
        built = load_strategy_draft_v2(chat)
        proposal = SetupChatPendingChange(
            chat_session_id=chat.id,
            user_id=chat.user_id,
            proposal_id=uuid4().hex,
            status="pending",
            executable_hash=built.executable_hash,
            workflow_state_hash=built.workflow_state_hash,
            executable_version=built.executable_version,
            operations_json=[],
            operation_payload_hash="x",
            diff_json={},
            reasons_json=["draft_reset"],
            summary_json=["This clears your setup."],
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
            created_at=datetime.now(UTC) - timedelta(hours=1),
        )
        session.add(proposal)
        await session.commit()

        await service.handle_draft_action(
            session,
            chat,
            action="confirm_pending_change",
            client_message_id="expire-confirm-1",
            proposal_id=proposal.proposal_id,
            confirmed=True,
        )
        await session.refresh(proposal)
        assert proposal.status == "stale"
        assert load_strategy_draft_v2(chat).executable_hash == built.executable_hash


# ---------------------------------------------------------------------------------
# 27-28. The diff matches canonical state, and approval moves only when it should.
# ---------------------------------------------------------------------------------


def test_27_the_diff_matches_the_canonical_before_and_after() -> None:
    from ai_market_monitor.engine.change_review import build_draft_diff
    from ai_market_monitor.schemas.strategy_draft_v2 import (
        MarketScopeV2,
        StrategyDraftV2,
    )

    before = StrategyDraftV2()
    same = build_draft_diff(before, before)
    assert same.empty, "no change is reported as no change"
    assert not same.added_conditions and not same.removed_conditions

    after = before.model_copy(
        update={"market_scope": MarketScopeV2(exchange="kraken", quote_asset="USD")}
    )
    diff = build_draft_diff(before, after)
    assert not diff.empty
    targets = {item.target for item in diff.market_scope_changes}
    assert "market_scope.exchange" in targets
    assert diff.executable_hash_before == before.executable_hash
    assert diff.executable_hash_after == after.executable_hash


async def test_27_the_reported_diff_is_the_one_the_draft_really_took(
    test_context,
) -> None:
    """Built by comparing stored drafts, not by reading the assistant's sentence."""

    from ai_market_monitor.engine.change_review import build_draft_diff
    from ai_market_monitor.schemas.setup_change_review import SetupDraftDiff
    from ai_market_monitor.services.setup_chat_launch import LAST_DIFF_KEY

    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, (await _user(test_context)).id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="diff-build-00001"
        )
        built = load_strategy_draft_v2(chat)
        await service.handle_draft_action(
            session,
            chat,
            action="reset_current_draft",
            client_message_id="diff-reset-00001",
            confirmed=True,
        )
        cleared = load_strategy_draft_v2(chat)
        stored = (chat.context_json or {}).get(LAST_DIFF_KEY)
        assert isinstance(stored, dict), "the turn recorded what it changed"
        reported = SetupDraftDiff.model_validate(stored)
        recomputed = build_draft_diff(built, cleared)
        assert reported.removed_conditions == recomputed.removed_conditions
        assert reported.executable_hash_before == built.executable_hash
        assert reported.executable_hash_after == cleared.executable_hash


async def test_28_approval_is_cleared_only_when_executable_state_moves(
    test_context,
) -> None:
    """Answering a question advances the workflow without touching an approval."""

    from ai_market_monitor.schemas.strategy_draft_v2 import UnresolvedFieldV2

    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, (await _user(test_context)).id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="approval-diff-01"
        )
        draft = load_strategy_draft_v2(chat)

    # A blocker added to the draft moves the workflow hash and nothing else. If it moved
    # the executable hash too, every question asked after approval would invalidate it.
    with_question = draft.model_copy(
        update={
            "unresolved_fields": [
                UnresolvedFieldV2(
                    key="timeframe",
                    unresolved_id="u1",
                    question="Which timeframe should I watch?",
                    reason="It was not said.",
                    source_turn_id="t1",
                    source_fragment=BUILD[:80],
                )
            ],
            # Blanked so the model recomputes both from the new content. `model_copy`
            # does not re-run validators, so keeping the old hashes would compare the
            # draft against itself and prove nothing.
            "executable_hash": "",
            "workflow_state_hash": "",
        }
    )
    rebuilt = with_question.__class__.model_validate(with_question.model_dump(mode="json"))
    assert rebuilt.executable_hash == draft.executable_hash, (
        "an open question must not invalidate an approved setup"
    )
    assert rebuilt.workflow_state_hash != draft.workflow_state_hash


# ---------------------------------------------------------------------------------
# 29. Telemetry: a replay and a recovery never rewrite what the paid turn cost.
# ---------------------------------------------------------------------------------


async def test_29_a_replay_preserves_the_original_measured_usage(test_context) -> None:
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, (await _user(test_context)).id)
        await service.handle_message(
            session, chat, message=BUILD, client_message_id="telemetry-key-01"
        )
        record = await _turn(session, chat, "telemetry-key-01")
        original = dict(record.telemetry_json or {})
        assert original, "the paid turn recorded what it spent"

        await service.handle_message(
            session, chat, message=BUILD, client_message_id="telemetry-key-01"
        )
        await session.refresh(record)
        assert dict(record.telemetry_json or {}) == original, (
            "a free replay must not overwrite the paid original's measurement"
        )
        replay = (chat.context_json or {}).get("last_idempotent_replay")
        assert isinstance(replay, dict) and replay["cache_hit"] is True, (
            "replay latency is recorded separately"
        )


async def test_29_recovery_usage_is_recorded_apart_from_the_original(
    test_context,
) -> None:
    planner = StandInPlanner()
    service = _service(test_context, planner)
    chat_id, _ = await _crossed_worker_setup(test_context, service)

    async with test_context["session_factory"]() as session:
        await SetupChatRecoveryService(
            _launch_settings(test_context["settings"])
        ).run_once(session)

    async with test_context["session_factory"]() as session:
        record = await session.scalar(
            select(SetupChatTurn).where(SetupChatTurn.chat_session_id == chat_id)
        )
        assert record is not None
        assert (record.recovery_usage_json or {}).get("model_calls") == 0
        assert record.telemetry_json is not None, "the original measurement survives"
