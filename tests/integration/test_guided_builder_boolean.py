"""Nested Boolean logic, built and edited entirely through guided clicks.

``engine/builder_boolean.py`` is unit-tested on its own. These drive the whole path the
browser drives — request schema, service dispatch, mutation authority, stored draft — and
count model calls, because the point of the Builder is that authoring never needs one.

The defect being guarded: ``arrange_conditions`` can only express one flat root join, so
before the structural actions existed, "A and (B or C)" became "A and B and C" the next
time anything was rearranged. That is a different strategy that still compiles and still
fires, which is the worst kind of wrong.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from ai_market_monitor.schemas.ai_setup_chat import SetupBuilderActionRequest
from ai_market_monitor.schemas.strategy_draft_v2 import ConditionNodeType
from ai_market_monitor.services.ai_setup_chat import SetupChatError
from ai_market_monitor.services.setup_chat_launch import load_strategy_draft_v2
from tests.integration.test_guided_builder import (
    _act,
    _service,
    _user,
)
from tests.integration.test_setup_chat_launch_v2 import StandInPlanner

pytestmark = pytest.mark.anyio


def _rule(threshold: int) -> dict:
    """One guided rule. The threshold only exists to tell them apart."""

    return {
        "mechanic_key": "open_to_close_percentage",
        "values": {
            "direction": "up",
            "comparator": "gte",
            "threshold": threshold,
            "timeframe": "15m",
        },
    }


def _tree(chat):
    return load_strategy_draft_v2(chat).condition_ast


def _leaf_ids(node) -> list[str]:
    return [
        item.node_id
        for item in (node.walk() if node else [])
        if item.node_type is ConditionNodeType.CONDITION
    ]


def _groups(node) -> list:
    return [
        item
        for item in (node.walk() if node else [])
        if item.node_type is not ConditionNodeType.CONDITION
    ]


async def _three_rules(service, session, chat, prefix: str) -> list[str]:
    await _act(service, session, chat, "select_mode", f"{prefix}-mode", value="monitor")
    for index in range(3):
        await _act(service, session, chat, "add_condition", f"{prefix}-{index}", **_rule(index + 1))
    return _leaf_ids(_tree(chat))


# ---------------------------------------------------------------------------
# The request contract refuses a structural change with no target.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "payload"),
    [
        ("group_conditions", {}),
        ("group_conditions", {"node_ids": ["a", "b"]}),
        ("group_conditions", {"operator": "or"}),
        ("ungroup_conditions", {}),
        ("set_group_operator", {"group_id": "g1"}),
        ("set_group_operator", {"operator": "or"}),
        ("move_condition", {"node_id": "a"}),
        ("move_condition", {"group_id": "g1"}),
    ],
)
def test_a_structural_request_without_its_target_never_reaches_the_service(
    action, payload
) -> None:
    with pytest.raises(ValueError):
        SetupBuilderActionRequest(
            action=action, client_message_id=f"k{uuid4().hex[:12]}", **payload
        )


def test_the_request_schema_rejects_an_operator_the_compiler_does_not_have() -> None:
    with pytest.raises(ValueError):
        SetupBuilderActionRequest(
            action="group_conditions",
            client_message_id=f"k{uuid4().hex[:12]}",
            node_ids=["a", "b"],
            operator="xor",
        )


# ---------------------------------------------------------------------------
# Building and editing nested logic with no model call.
# ---------------------------------------------------------------------------


async def test_nested_logic_is_built_and_stored_with_no_model_call(test_context) -> None:
    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        leaves = await _three_rules(service, session, chat, "nest")
        assert len(leaves) == 3
        before = (planner.plan_calls, planner.reply_calls)

        await _act(
            service,
            session,
            chat,
            "group_conditions",
            "nest-group",
            node_ids=leaves[1:],
            operator="or",
        )

        tree = _tree(chat)
        assert tree.node_type is ConditionNodeType.AND
        assert [child.node_id for child in tree.children] == [leaves[0], "group_or_1"]
        inner = next(item for item in tree.walk() if item.node_id == "group_or_1")
        assert inner.node_type is ConditionNodeType.OR
        assert [child.node_id for child in inner.children] == leaves[1:]
        # Every rule survived, and the assistant was never asked.
        assert sorted(_leaf_ids(tree)) == sorted(leaves)
        assert (planner.plan_calls, planner.reply_calls) == before


async def test_every_structural_action_round_trips_through_the_service(test_context) -> None:
    """Group, change the join, move a rule, ungroup — no rule lost at any step."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        leaves = await _three_rules(service, session, chat, "round")
        before = (planner.plan_calls, planner.reply_calls)

        await _act(
            service, session, chat, "group_conditions", "round-g",
            node_ids=leaves[1:], operator="or",
        )
        assert sorted(_leaf_ids(_tree(chat))) == sorted(leaves)

        await _act(
            service, session, chat, "set_group_operator", "round-op",
            group_id="group_or_1", operator="and",
        )
        tree = _tree(chat)
        assert sorted(_leaf_ids(tree)) == sorted(leaves)
        inner_id = next(
            item.node_id for item in _groups(tree) if item.node_id != tree.node_id
        )

        # Ungroup while the group still has a parent: its rules rise into that parent.
        await _act(
            service, session, chat, "ungroup_conditions", "round-ungroup",
            group_id=inner_id,
        )
        tree = _tree(chat)
        assert sorted(_leaf_ids(tree)) == sorted(leaves)
        assert _groups(tree) == [tree]

        # Regroup, then move the last outside rule in. The outer group is left holding
        # one child, and it **stays**. It used to collapse onto that child, because a
        # group with one rule in it was refused on the next validation; one rule is
        # enough for a group now, and a group somebody made is not deleted because they
        # moved a rule out of it. No rule is lost either way, which is the property that
        # matters, and the meaning is unchanged.
        await _act(
            service, session, chat, "group_conditions", "round-g2",
            node_ids=leaves[1:], operator="or",
        )
        regrouped_id = next(
            item.node_id for item in _groups(_tree(chat)) if item.node_id != _tree(chat).node_id
        )
        outer_id = _tree(chat).node_id
        await _act(
            service, session, chat, "move_condition", "round-move",
            node_id=leaves[0], group_id=regrouped_id, position=0,
        )
        tree = _tree(chat)
        assert sorted(_leaf_ids(tree)) == sorted(leaves)
        assert tree.node_id == outer_id
        assert [child.node_id for child in tree.children] == [regrouped_id]
        inner = tree.children[0]
        assert [child.node_id for child in inner.children][0] == leaves[0]
        assert (planner.plan_calls, planner.reply_calls) == before


async def test_the_outermost_grouping_cannot_be_dissolved(test_context) -> None:
    """Removing the root would leave the setup with no way to join its rules at all."""

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        leaves = await _three_rules(service, session, chat, "root")
        root_id = _tree(chat).node_id

        with pytest.raises(SetupChatError) as raised:
            await _act(
                service, session, chat, "ungroup_conditions", "root-bad", group_id=root_id
            )
        assert raised.value.code == "ROOT_REQUIRED"
        assert sorted(_leaf_ids(_tree(chat))) == sorted(leaves)


async def test_a_group_survives_an_unrelated_rule_being_added(test_context) -> None:
    """The flattening defect: an edit elsewhere must not dissolve existing structure."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        leaves = await _three_rules(service, session, chat, "keep")
        await _act(
            service, session, chat, "group_conditions", "keep-g",
            node_ids=leaves[1:], operator="or",
        )

        await _act(service, session, chat, "add_condition", "keep-extra", **_rule(9))

        tree = _tree(chat)
        inner = next(
            (item for item in _groups(tree) if item.node_type is ConditionNodeType.OR),
            None,
        )
        assert inner is not None, "the OR group was flattened by an unrelated add"
        assert [child.node_id for child in inner.children] == leaves[1:]
        assert len(_leaf_ids(tree)) == 4


# ---------------------------------------------------------------------------
# Fail closed, through the real service.
# ---------------------------------------------------------------------------


async def test_grouping_across_two_branches_is_refused(test_context) -> None:
    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        leaves = await _three_rules(service, session, chat, "split")
        await _act(
            service, session, chat, "group_conditions", "split-g",
            node_ids=leaves[1:], operator="or",
        )

        with pytest.raises(SetupChatError) as raised:
            await _act(
                service, session, chat, "group_conditions", "split-bad",
                node_ids=[leaves[0], leaves[1]], operator="or",
            )
        assert raised.value.code == "SELECTION_SPLIT"

        # And the draft is unchanged: a refused edit writes nothing.
        assert sorted(_leaf_ids(_tree(chat))) == sorted(leaves)


async def test_a_negation_over_two_rules_is_refused(test_context) -> None:
    """``NOT`` takes exactly one child in the compiler, so the Builder refuses two."""

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        leaves = await _three_rules(service, session, chat, "neg")

        with pytest.raises(SetupChatError) as raised:
            await _act(
                service, session, chat, "group_conditions", "neg-bad",
                node_ids=leaves[:2], operator="not",
            )
        assert raised.value.code == "GROUP_TOO_LARGE"

        await _act(
            service, session, chat, "group_conditions", "neg-ok",
            node_ids=[leaves[0]], operator="not",
        )
        tree = _tree(chat)
        negation = next(
            item for item in _groups(tree) if item.node_type is ConditionNodeType.NOT
        )
        assert [child.node_id for child in negation.children] == [leaves[0]]


async def test_a_structural_action_on_a_missing_node_is_refused(test_context) -> None:
    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        leaves = await _three_rules(service, session, chat, "ghost")

        for label, action, payload in (
            ("g1", "ungroup_conditions", {"group_id": "no_such_group"}),
            ("g2", "set_group_operator", {"group_id": "no_such_group", "operator": "or"}),
            ("g3", "move_condition", {"node_id": leaves[0], "group_id": "no_such_group"}),
        ):
            with pytest.raises(SetupChatError):
                await _act(service, session, chat, action, f"ghost-{label}", **payload)

        assert sorted(_leaf_ids(_tree(chat))) == sorted(leaves)


async def test_the_builder_state_carries_the_shape_the_client_draws(test_context) -> None:
    """The client cannot draw groups it is not told about.

    ``join`` describes only the outermost join, which is why nested logic used to be
    invisible in the Builder — and invisible structure is structure the next rearrange
    destroys without anybody noticing.
    """

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        leaves = await _three_rules(service, session, chat, "state")
        await _act(
            service, session, chat, "group_conditions", "state-g",
            node_ids=leaves[1:], operator="or",
        )

        from ai_market_monitor.engine.builder_state import builder_state

        state = builder_state(load_strategy_draft_v2(chat))
        rows = state["structure"]
        by_id = {row["node_id"]: row for row in rows}

        assert by_id[leaves[0]]["depth"] == 2
        assert by_id[leaves[1]]["depth"] == 3
        assert by_id[leaves[1]]["parent_id"] == "group_or_1"
        assert by_id["group_or_1"]["kind"] == "group"
        assert by_id["group_or_1"]["operator"] == "or"
        # Every stored node appears exactly once, so nothing the person built is missing
        # from what the client is asked to draw.
        assert len(rows) == len(load_strategy_draft_v2(chat).condition_ast.walk())


def test_every_builder_state_field_survives_the_response_schema() -> None:
    """The state the server builds must be a state the response model accepts.

    ``SetupBuilderState`` forbids extra fields, so a key added to ``builder_state`` and
    not to the schema does not degrade — it raises on every session response, taking the
    whole Builder down. That is exactly what adding ``structure`` did the first time.
    """

    from ai_market_monitor.engine.builder_state import builder_state
    from ai_market_monitor.schemas.ai_setup_chat import SetupBuilderState
    from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2

    produced = set(builder_state(StrategyDraftV2()))
    accepted = set(SetupBuilderState.model_fields)

    assert produced <= accepted, produced - accepted
    # And the round trip really works, not just the field names.
    SetupBuilderState.model_validate(builder_state(StrategyDraftV2()))


async def test_a_repeated_structural_click_acts_once(test_context) -> None:
    """The same idempotency rule every other Builder write follows."""

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        leaves = await _three_rules(service, session, chat, "dupe")
        key = f"k{uuid4().hex[:16]}"

        for _ in range(2):
            await service.handle_builder_action(
                session,
                chat,
                action="group_conditions",
                client_message_id=key,
                node_ids=leaves[1:],
                operator="or",
            )

        groups = [item for item in _groups(_tree(chat)) if item.node_type is ConditionNodeType.OR]
        assert len(groups) == 1
        assert sorted(_leaf_ids(_tree(chat))) == sorted(leaves)
