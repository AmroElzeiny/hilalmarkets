"""The Guided Builder is the product path; the assistant is an accelerator over it.

Every test here drives the real service against a real database and counts model calls
before and after. The whole point of the Builder is that the counts never move: a person
must be able to create, edit, arrange, review and take a Watch Plan to approval with the
assistant switched off entirely.

The cases are numbered to match the acceptance list they prove.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import User
from ai_market_monitor.engine.builder_operations import (
    describe_condition,
    mechanic_catalog,
    offered_mechanics,
)
from ai_market_monitor.engine.builder_starters import STARTERS
from ai_market_monitor.engine.builder_state import builder_state
from ai_market_monitor.services.ai_setup_chat import SetupChatError
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
    """Exactly what a stretch of work spent on models. Both counts, never just one."""

    plan: int
    reply: int

    @classmethod
    def of(cls, planner: StandInPlanner) -> _ModelCalls:
        return cls(plan=planner.plan_calls, reply=planner.reply_calls)


def _key(label: str) -> str:
    """A valid idempotency key from a short test label."""

    return f"cm-builder-{label}-{uuid4().hex[:8]}"


async def _user(test_context) -> User:
    async with test_context["session_factory"]() as session:
        user = User(display_name=f"Builder {uuid4().hex[:8]}")
        session.add(user)
        await _seed_methodology(session)
        await session.commit()
        await session.refresh(user)
        return user


def _service(test_context, planner: StandInPlanner, **overrides) -> AISetupChatService:
    settings = _scanner_settings(test_context["settings"])
    if overrides:
        settings = settings.model_copy(update=overrides)
    return AISetupChatService(
        settings,
        _Provider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(settings, planner),
    )


async def _act(service, session, chat, action: str, label: str, **extra):
    return await service.handle_builder_action(
        session,
        chat,
        action=action,
        client_message_id=_key(label),
        **extra,
    )


#: One complete supported setup, expressed only as guided clicks. Used by several tests
#: so the "can this be built without AI" question is asked the same way every time.
_RISE_RULE = {
    "mechanic_key": "open_to_close_percentage",
    "values": {"direction": "up", "comparator": "gte", "threshold": 5, "timeframe": "15m"},
}

#: The same rule, typed as a sentence. Worded so the test planner's deterministic reader
#: produces it — the point of the comparison is the *stored rule*, not the wording, and a
#: sentence the stand-in cannot read would be testing the stand-in.
_ASSISTANT_SAME_RULE = (
    "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"
)


async def _guided_setup(service, session, chat, prefix: str) -> None:
    """Mode, screened coins and one rule — the whole flow, with no message typed."""

    await _act(service, session, chat, "select_mode", f"{prefix}-mode", value="monitor")
    await _act(
        service, session, chat, "select_universe", f"{prefix}-scope", value="eligible_market"
    )
    await _act(service, session, chat, "add_condition", f"{prefix}-rule", **_RISE_RULE)


# ---------------------------------------------------------------------------
# 1. A complete supported Watch Plan, with zero model calls.
# ---------------------------------------------------------------------------


async def test_1_a_complete_watch_plan_is_built_with_zero_model_calls(test_context) -> None:
    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        before = _ModelCalls.of(planner)

        await _guided_setup(service, session, chat, "zero")

        assert _ModelCalls.of(planner) == before, "the guided path called a model"
        draft = load_strategy_draft_v2(chat)
        assert draft.condition_ast is not None, "no rule was written"
        assert draft.mode.value == "monitor"
        state = builder_state(draft)
        assert [item["key"] for item in state["steps"] if item["complete"]] >= [
            "mode",
            "assets",
            "conditions",
            "logic",
        ]


@pytest.mark.parametrize(
    "mechanic_key",
    [item.key for item in offered_mechanics()],
    ids=[item.key for item in offered_mechanics()],
)
async def test_1b_every_offered_mechanic_can_actually_be_added(
    test_context, mechanic_key: str
) -> None:
    """A catalogue entry a person cannot use is worse than one that is not listed.

    Parametrised across the whole catalogue on purpose: a fix that only makes the
    reported mechanic work must fail here.
    """

    from ai_market_monitor.engine.builder_operations import _probe_values, find_mechanic

    mechanic = find_mechanic(mechanic_key)
    assert mechanic is not None
    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _act(service, session, chat, "select_mode", "mech-mode", value="monitor")
        before = _ModelCalls.of(planner)

        await _act(
            service,
            session,
            chat,
            "add_condition",
            "mech-add",
            mechanic_key=mechanic_key,
            values=_probe_values(mechanic),
        )

        assert _ModelCalls.of(planner) == before
        draft = load_strategy_draft_v2(chat)
        assert draft.condition_ast is not None, f"{mechanic_key} produced no rule"


async def test_1c_a_blocking_question_is_cleared_without_typing_anything(
    test_context,
) -> None:
    """Choosing Monitor opens a governed question about which coins to watch.

    A person must be able to answer it by clicking, not by writing a sentence — that is
    the difference between "the assistant is optional" and "the assistant is required
    for the parts that block you".
    """

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _act(service, session, chat, "select_mode", "clar-mode", value="monitor")
        opened = load_strategy_draft_v2(chat)
        blocking = [item.unresolved_id for item in opened.unresolved_fields if item.blocking]
        assert blocking, "choosing a mode opened no question to answer"
        before = _ModelCalls.of(planner)

        await _act(
            service, session, chat, "select_universe", "clar-answer", value="eligible_market"
        )

        assert _ModelCalls.of(planner) == before, "answering a question called a model"
        after = load_strategy_draft_v2(chat)
        still_open = {item.unresolved_id for item in after.unresolved_fields if item.blocking}
        assert not (set(blocking) & still_open), "the question the click answered is still open"


async def test_1d_a_setup_built_only_by_clicking_reaches_the_approval_gate(
    test_context,
) -> None:
    """The last step of the guided flow is the existing approval route, unchanged.

    This asserts readiness, not approval itself: approval is a separate authenticated
    action bound to an exact version and hash, and nothing in the Builder may grant it.
    """

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _guided_setup(service, session, chat, "approve")
        draft = load_strategy_draft_v2(chat)

    assert not draft.authoring_blocking, "the guided flow left the setup blocked"
    assert not draft.approval.approved, "the Builder approved a setup by itself"
    state = builder_state(draft)
    assert next(item for item in state["steps"] if item["key"] == "review")["complete"]
    assert not next(item for item in state["steps"] if item["key"] == "approval")["complete"], (
        "approval must still be an explicit action the person takes"
    )


# ---------------------------------------------------------------------------
# 2. The same setup built by hand and by the assistant is the same draft.
# ---------------------------------------------------------------------------


async def test_2_manual_and_assistant_setups_produce_the_same_canonical_rule(
    test_context,
) -> None:
    """Same meaning, same stored rule — because there is one mutation authority.

    The two paths write different provenance (a sentence somebody typed, versus a
    sentence the server rendered), and that difference is deliberate evidence. Every
    field that decides what the market is watched *for* must match exactly.
    """

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        manual_chat = await service.create_session(session, user.id)
        await _guided_setup(service, session, manual_chat, "same-manual")
        manual = load_strategy_draft_v2(manual_chat)

        chat_chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat_chat,
            message="Monitor",
            client_message_id=_key("same-ai-1"),
        )
        await service.handle_message(
            session,
            chat_chat,
            message=_ASSISTANT_SAME_RULE,
            client_message_id=_key("same-ai-2"),
        )
        assistant = load_strategy_draft_v2(chat_chat)

    manual_rule = _only_rule(manual)
    assistant_rule = _only_rule(assistant)
    assert assistant_rule is not None, "the assistant path built no rule to compare"
    assert manual_rule is not None
    for field in ("formula", "operator", "threshold", "unit", "trigger_timeframe"):
        assert getattr(manual_rule, field) == getattr(assistant_rule, field), field


def _only_rule(draft):
    from ai_market_monitor.schemas.strategy_draft_v2 import ConditionNodeType

    if draft.condition_ast is None:
        return None
    rules = [
        node
        for node in draft.condition_ast.walk()
        if node.node_type == ConditionNodeType.CONDITION
    ]
    return rules[0] if rules else None


# ---------------------------------------------------------------------------
# 3 and 4. Each surface sees what the other wrote, immediately.
# ---------------------------------------------------------------------------


async def test_3_a_manual_edit_is_the_state_the_next_assistant_turn_reads(
    test_context,
) -> None:
    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _guided_setup(service, session, chat, "hand-off")
        built = load_strategy_draft_v2(chat)

        await service.handle_message(
            session,
            chat,
            message="what does this watch?",
            client_message_id=_key("hand-off-ask"),
        )

        after = load_strategy_draft_v2(chat)
        assert after.executable_hash == built.executable_hash, (
            "the assistant turn did not read the draft the Builder had just written"
        )


async def test_4_an_assistant_edit_is_visible_in_the_builder_without_another_request(
    test_context,
) -> None:
    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session, chat, message="Monitor", client_message_id=_key("ai-first-1")
        )
        await service.handle_message(
            session,
            chat,
            message=_ASSISTANT_SAME_RULE,
            client_message_id=_key("ai-first-2"),
        )

        state = builder_state(load_strategy_draft_v2(chat))
        assert state["conditions"], "the Builder cannot see the rule the assistant wrote"
        rule = state["conditions"][0]
        assert rule["sentence"], "the rule has no readable description"
        # Visible is not enough. A rule the assistant wrote must be *editable* by hand,
        # or the assistant is still required for anything it happened to create.
        assert rule["editable"], rule["not_editable_reason"]
        assert rule["values"], "the card has no values to edit"


# ---------------------------------------------------------------------------
# 5. Reloading shows the same thing.
# ---------------------------------------------------------------------------


async def test_5_reloading_restores_the_same_builder_state(test_context) -> None:
    """State lives in the draft, not in the page, so a refresh cannot lose it."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _guided_setup(service, session, chat, "reload")
        first = builder_state(load_strategy_draft_v2(chat))
        chat_id = chat.id

    async with test_context["session_factory"]() as session:
        reloaded = await service.owned_session(session, user.id, chat_id)
        assert builder_state(load_strategy_draft_v2(reloaded)) == first


# ---------------------------------------------------------------------------
# 6 and 7. Nothing is substituted, and a wrong combination fails closed.
# ---------------------------------------------------------------------------


async def test_6_an_unsupported_mechanic_cannot_be_selected_or_substituted(
    test_context,
) -> None:
    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _act(service, session, chat, "select_mode", "sub-mode", value="monitor")
        before = load_strategy_draft_v2(chat)

        with pytest.raises(SetupChatError) as raised:
            await _act(
                service,
                session,
                chat,
                "add_condition",
                "sub-add",
                mechanic_key="moon_phase_reversal",
                values={"comparator": "gte", "threshold": 5, "timeframe": "1h"},
            )

        assert raised.value.code == "MECHANIC_UNKNOWN"
        assert load_strategy_draft_v2(chat).executable_hash == before.executable_hash, (
            "an unknown mechanic changed the draft anyway"
        )


_BAD_COMBINATIONS: tuple[tuple[str, str, dict[str, object], str], ...] = (
    (
        "operator_the_formula_does_not_own",
        "open_to_close_percentage",
        {"direction": "up", "comparator": "crosses_above", "threshold": 5, "timeframe": "1h"},
        "COMPARISON_NOT_OFFERED",
    ),
    (
        "direction_the_formula_forbids",
        "high_to_low_percentage",
        {"direction": "up", "comparator": "gte", "threshold": 5, "timeframe": "1h"},
        "DIRECTION_NOT_OFFERED",
    ),
    (
        "timeframe_that_does_not_exist",
        "open_to_close_percentage",
        {"direction": "up", "comparator": "gte", "threshold": 5, "timeframe": "7s"},
        "TIMEFRAME_NOT_OFFERED",
    ),
    (
        "threshold_out_of_range",
        "open_to_close_percentage",
        {"direction": "up", "comparator": "gte", "threshold": 100000, "timeframe": "1h"},
        "VALUE_OUT_OF_RANGE",
    ),
    (
        "a_field_the_form_does_not_have",
        "open_to_close_percentage",
        {
            "direction": "up",
            "comparator": "gte",
            "threshold": 5,
            "timeframe": "1h",
            "leverage": 10,
        },
        "FIELD_NOT_OFFERED",
    ),
)


@pytest.mark.parametrize(
    ("label", "mechanic_key", "values", "code"),
    _BAD_COMBINATIONS,
    ids=[item[0] for item in _BAD_COMBINATIONS],
)
async def test_7_invalid_combinations_fail_closed_and_change_nothing(
    test_context, label: str, mechanic_key: str, values: dict[str, object], code: str
) -> None:
    """Refused, never coerced to the nearest thing that would have worked."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _act(service, session, chat, "select_mode", f"bad-{label}", value="monitor")
        before = load_strategy_draft_v2(chat)

        with pytest.raises(SetupChatError) as raised:
            await _act(
                service,
                session,
                chat,
                "add_condition",
                f"bad-{label}-add",
                mechanic_key=mechanic_key,
                values=values,
            )

        assert raised.value.code == code
        assert load_strategy_draft_v2(chat).executable_hash == before.executable_hash


# ---------------------------------------------------------------------------
# 8. Sharia rules are identical on both surfaces.
# ---------------------------------------------------------------------------


async def test_8_the_screened_universe_rules_are_the_same_on_both_surfaces(
    test_context,
) -> None:
    """Choosing coins in the Builder runs the same governed code the chat runs.

    They share one producer — the option-operation builder — so this asserts the
    resulting policy, which is the thing a divergence would show up in.
    """

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        manual_chat = await service.create_session(session, user.id)
        await _act(service, session, manual_chat, "select_mode", "pol-1", value="monitor")
        await _act(
            service, session, manual_chat, "select_universe", "pol-2", value="eligible_market"
        )
        manual = load_strategy_draft_v2(manual_chat).sharia_policy

        chat_chat = await service.create_session(session, user.id)
        await service.handle_message(
            session, chat_chat, message="Monitor", client_message_id=_key("pol-ai-1")
        )
        await service.handle_message(
            session,
            chat_chat,
            message="",
            option_key="screened_universe_mode",
            option_value="eligible_market",
            client_message_id=_key("pol-ai-2"),
        )
        assistant = load_strategy_draft_v2(chat_chat).sharia_policy

    assert manual.universe_mode == assistant.universe_mode
    assert manual.methodology_id == assistant.methodology_id
    assert manual.methodology_version == assistant.methodology_version
    assert manual.allowed_statuses == assistant.allowed_statuses


# ---------------------------------------------------------------------------
# 9. Approval survives what does not change the rules.
# ---------------------------------------------------------------------------


async def test_9_only_a_material_change_moves_the_executable_identity(test_context) -> None:
    """Renaming is not editing. Editing a threshold is.

    Approval binds to the executable hash, so this asserts the hash directly — the
    thing approval is bound to — rather than a status string that could agree by luck.
    """

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _guided_setup(service, session, chat, "material")
        built = load_strategy_draft_v2(chat)

        await _act(service, session, chat, "rename_plan", "material-name", value="My plan")
        renamed = load_strategy_draft_v2(chat)
        assert renamed.executable_hash == built.executable_hash, (
            "renaming changed what the setup watches"
        )

        node_id = _only_rule(renamed).node_id
        await _act(
            service,
            session,
            chat,
            "update_condition",
            "material-edit",
            node_id=node_id,
            mechanic_key="open_to_close_percentage",
            values={"direction": "up", "comparator": "gte", "threshold": 6, "timeframe": "15m"},
        )
        edited = load_strategy_draft_v2(chat)

    assert edited.executable_hash != built.executable_hash, "editing a threshold changed nothing"
    assert edited.executable_version > built.executable_version


# ---------------------------------------------------------------------------
# 10 and 11. The assistant being unavailable never blocks or loses anything.
# ---------------------------------------------------------------------------


async def test_10_a_full_setup_can_be_completed_with_the_assistant_switched_off(
    test_context,
) -> None:
    """Every AI switch off. The Builder must still finish the job."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(
        test_context,
        planner,
        setup_free_text_enabled=False,
        setup_planner_enabled=False,
        setup_composer_enabled=False,
    )

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _guided_setup(service, session, chat, "offline")

        assert _ModelCalls.of(planner) == _ModelCalls(plan=0, reply=0)
        draft = load_strategy_draft_v2(chat)
        assert draft.condition_ast is not None
        assert not draft.authoring_blocking, "the setup could not be finished offline"


async def test_11_progress_survives_an_assistant_failure(test_context) -> None:
    """A failing assistant is not a failing setup.

    The draft is written before any model is called, so a provider that never answers
    leaves the person exactly where they were — with everything they had built.
    """

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _guided_setup(service, session, chat, "outage")
        saved = load_strategy_draft_v2(chat)

        planner.failure = RuntimeError("provider unreachable")
        # The failure is the point of the test, so it is caused and then ignored.
        with suppress(Exception):
            await service.handle_message(
                session,
                chat,
                message="also add a volume rule",
                client_message_id=_key("outage-ai"),
            )

        after = load_strategy_draft_v2(chat)

    assert after.executable_hash == saved.executable_hash, "an AI failure moved the draft"
    assert after.condition_ast is not None, "an AI failure lost the rules"


# ---------------------------------------------------------------------------
# 12. Scanner and Monitor stay different things.
# ---------------------------------------------------------------------------


async def test_12_scanner_and_monitor_remain_distinct(test_context) -> None:
    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        monitor_chat = await service.create_session(session, user.id)
        await _act(service, session, monitor_chat, "select_mode", "mode-m", value="monitor")

        scanner_chat = await service.create_session(session, user.id)
        await _act(service, session, scanner_chat, "select_mode", "mode-s", value="scanner")

        monitor = load_strategy_draft_v2(monitor_chat)
        scanner = load_strategy_draft_v2(scanner_chat)

    assert monitor.mode.value == "monitor"
    assert scanner.mode.value == "scanner"
    assert monitor.executable_hash != scanner.executable_hash, "the two modes are the same draft"


# ---------------------------------------------------------------------------
# 13. Starting points use only mechanics the platform runs.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "starter_key",
    [item.key for item in STARTERS],
    ids=[item.key for item in STARTERS],
)
async def test_13_every_starting_point_applies_with_zero_model_calls(
    test_context, starter_key: str
) -> None:
    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        before = _ModelCalls.of(planner)

        await _act(
            service, session, chat, "apply_starter", f"start-{starter_key}", value=starter_key
        )

        assert _ModelCalls.of(planner) == before, f"{starter_key} called a model"
        draft = load_strategy_draft_v2(chat)
        assert draft.condition_ast is not None, f"{starter_key} produced no rule"
        for view in (describe_condition(node) for node in _rules(draft)):
            assert view.mechanic_key is not None, f"{starter_key} used an unknown mechanic"


def _rules(draft):
    from ai_market_monitor.schemas.strategy_draft_v2 import ConditionNodeType

    return [
        node
        for node in (draft.condition_ast.walk() if draft.condition_ast else [])
        if node.node_type == ConditionNodeType.CONDITION
    ]


def test_13b_no_starting_point_names_a_mechanic_the_platform_withholds() -> None:
    """Checked without a database, so a bad starter fails fast and cheaply."""

    available = {item.key for item in mechanic_catalog() if item.available}
    for starter in STARTERS:
        for rule in starter.rules:
            assert rule.mechanic_key in available, (
                f"{starter.key} uses {rule.mechanic_key}, which is not offered"
            )


# ---------------------------------------------------------------------------
# 15. An unfinished draft never touches anything already approved.
# ---------------------------------------------------------------------------


async def test_15_an_unfinished_builder_draft_leaves_other_sessions_alone(
    test_context,
) -> None:
    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        finished = await service.create_session(session, user.id)
        await _guided_setup(service, session, finished, "left-alone")
        settled = load_strategy_draft_v2(finished)
        finished_id = finished.id

        scratch = await service.create_session(session, user.id)
        await _act(service, session, scratch, "select_mode", "scratch-1", value="scanner")
        await _act(
            service,
            session,
            scratch,
            "add_condition",
            "scratch-2",
            mechanic_key="fixed_reference_level",
            values={"comparator": "gte", "threshold": 70000, "timeframe": "1d"},
        )

        reloaded = await service.owned_session(session, user.id, finished_id)

    assert load_strategy_draft_v2(reloaded).executable_hash == settled.executable_hash


# ---------------------------------------------------------------------------
# Arranging rules, and the confirmation rules around it.
# ---------------------------------------------------------------------------


async def test_rules_can_be_reordered_without_being_called_a_rewrite(test_context) -> None:
    """Reordering keeps every rule, so it is not a destructive change.

    Before this, any tree write was reported as "this replaces all of your rules" —
    untrue, and the fastest way to teach somebody to confirm without reading.
    """

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _act(service, session, chat, "select_mode", "arr-mode", value="monitor")
        await _act(service, session, chat, "add_condition", "arr-1", **_RISE_RULE)
        await _act(
            service,
            session,
            chat,
            "add_condition",
            "arr-2",
            mechanic_key="previous_candle_reference",
            values={"reference_field": "high", "comparator": "gt", "timeframe": "1h"},
        )
        draft = load_strategy_draft_v2(chat)
        ids = [node.node_id for node in _rules(draft)]
        assert len(ids) == 2

        await _act(
            service,
            session,
            chat,
            "arrange_conditions",
            "arr-3",
            order=list(reversed(ids)),
            join="and",
        )
        after = load_strategy_draft_v2(chat)

    assert [node.node_id for node in _rules(after)] == list(reversed(ids))
    assert {node.node_id for node in _rules(after)} == set(ids), "reordering lost a rule"


async def test_an_arrangement_that_drops_a_rule_is_refused(test_context) -> None:
    """A reorder is not a delete. One that loses a rule is a bug, not a tidy-up."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _act(service, session, chat, "select_mode", "drop-mode", value="monitor")
        await _act(service, session, chat, "add_condition", "drop-1", **_RISE_RULE)
        await _act(
            service,
            session,
            chat,
            "add_condition",
            "drop-2",
            mechanic_key="previous_candle_reference",
            values={"reference_field": "high", "comparator": "gt", "timeframe": "1h"},
        )
        ids = [node.node_id for node in _rules(load_strategy_draft_v2(chat))]

        with pytest.raises(SetupChatError) as raised:
            await _act(
                service, session, chat, "arrange_conditions", "drop-3", order=ids[:1], join="and"
            )

        assert raised.value.code == "ORDER_INCOMPLETE"
        assert len(_rules(load_strategy_draft_v2(chat))) == 2


async def test_editing_a_rule_that_no_longer_exists_is_refused(test_context) -> None:
    """The page may be showing an older version. Applying the edit anyway would change
    a rule nobody pointed at."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _act(service, session, chat, "select_mode", "gone-mode", value="monitor")
        await _act(service, session, chat, "add_condition", "gone-1", **_RISE_RULE)

        with pytest.raises(SetupChatError) as raised:
            await _act(
                service,
                session,
                chat,
                "update_condition",
                "gone-2",
                node_id="condition_that_never_existed",
                mechanic_key="open_to_close_percentage",
                values={
                    "direction": "up",
                    "comparator": "gte",
                    "threshold": 7,
                    "timeframe": "1h",
                },
            )

    assert raised.value.code == "CONDITION_NOT_FOUND"


async def test_a_double_clicked_button_acts_once(test_context) -> None:
    """The same key twice is one change, replayed — not two rules."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _act(service, session, chat, "select_mode", "twice-mode", value="monitor")
        key = _key("twice")

        await service.handle_builder_action(
            session, chat, action="add_condition", client_message_id=key, **_RISE_RULE
        )
        await service.handle_builder_action(
            session, chat, action="add_condition", client_message_id=key, **_RISE_RULE
        )

        assert len(_rules(load_strategy_draft_v2(chat))) == 1


# ---------------------------------------------------------------------------
# Degraded mode is reported as an assistant problem, never a setup problem.
# ---------------------------------------------------------------------------


def test_an_assistant_outage_is_never_reported_as_a_setup_problem() -> None:
    from ai_market_monitor.services.ai_availability import (
        AIUnavailableReason,
        availability_for,
        degraded_message,
        reason_for_code,
    )

    for code in ("AI_PROVIDER_UNAVAILABLE", "CIRCUIT_OPEN", "AI_QUOTA_EXCEEDED"):
        reason = reason_for_code(code)
        assert reason is not None, f"{code} was not classified as an assistant problem"
        message = degraded_message(reason)
        assert "progress is saved" in message
        for forbidden in ("compil", "sharia", "halal", "screen", "provider "):
            assert forbidden not in message.casefold(), f"{code} blames the setup"

    # A real finding about the setup must not be dressed up as an assistant problem.
    for code in ("VALUE_NOT_GROUNDED", "SCREENING_BLOCKED", "PATCH_REJECTED"):
        assert reason_for_code(code) is None, f"{code} was hidden behind an AI message"

    settings = Settings(setup_planner_enabled=False)
    availability = availability_for(settings)
    assert availability.builder is True, "turning the planner off took the Builder down"
    assert availability.assistant_available is False
    assert availability.reason is AIUnavailableReason.FEATURE_OFF
