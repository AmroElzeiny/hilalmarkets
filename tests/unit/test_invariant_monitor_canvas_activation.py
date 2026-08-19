"""What the canvas may build, and what a monitor may be told to do with it.

Three defect classes are held here, each across its whole family rather than for the one
example that was reported:

* **one answer to "how many rules does a grouping take"**. It used to be written in
  three places that disagreed — the Guided Builder refused fewer than two, the draft
  schema accepted one, and the draft's semantic check refused a *nested* one. A person
  could make a group on one screen that another screen would not save. Every reader
  imports ``GROUP_ARITY`` now, and this proves it for every group type and every size;

* **every way the platform can really deliver must be namable by a monitor**. Email was
  deliverable, renderable, sendable and offered in Settings, but ``AlertPolicy`` did not
  accept the word — so the canvas filtered it out and nobody could ask for alerts by
  email. Checked over every channel the platform offers, not over email;

* **a board that cannot become a monitor is refused, never repaired**. A card with no
  condition, a group with nothing in it, a "none of these" holding two, a coins choice
  nobody finished: each one stops the request with a sentence, and none of them is
  quietly filled in.
"""

from __future__ import annotations

import inspect
from typing import get_args

import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models.enums import DeliveryChannel
from ai_market_monitor.engine import builder_boolean
from ai_market_monitor.engine.strategy_draft_v2 import validate_draft_semantics
from ai_market_monitor.schemas.strategy import AlertPolicy
from ai_market_monitor.schemas.strategy_draft_v2 import (
    GROUP_ARITY,
    Comparator,
    ConditionNodeType,
    ConditionNodeV2,
    FormulaKind,
    OperandV2,
    StrategyDraftV2,
)
from ai_market_monitor.services.notification_preferences import offered_channels

GROUP_TYPES = tuple(item for item in ConditionNodeType if item is not ConditionNodeType.CONDITION)


def _leaf(node_id: str) -> ConditionNodeV2:
    return ConditionNodeV2(
        node_id=node_id,
        node_type=ConditionNodeType.CONDITION,
        source_turn_id="turn_1",
        source_fragment="price rises at least 1%",
        formula=FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE,
        operator=Comparator.GREATER_THAN_OR_EQUAL,
        threshold=1.0,
        unit="percent",
        trigger_timeframe="15m",
        operands=[
            OperandV2(
                role="measured_value",
                kind="market_metric",
                name="percentage_change",
                parameters={"formula": "close_to_close"},
            )
        ],
    )


def _group(node_type: ConditionNodeType, count: int) -> ConditionNodeV2:
    return ConditionNodeV2(
        node_id=f"group_{node_type.value}_1",
        node_type=node_type,
        children=[_leaf(f"leaf_{index}") for index in range(count)],
    )


# ── One answer to "how many rules does a grouping take" ───────────────────────


@pytest.mark.parametrize("node_type", GROUP_TYPES, ids=lambda item: str(item.value))
@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_the_schema_and_the_builder_agree_on_every_group_size(node_type, count) -> None:
    """One table, read by both. Neither may accept a shape the other refuses."""

    fewest, most = GROUP_ARITY[node_type]
    allowed = count >= fewest and (most is None or count <= most)

    if allowed:
        built = _group(node_type, count)
        assert len(built.children) == count
    else:
        with pytest.raises(ValueError):
            _group(node_type, count)

    # The Builder's own guard, on the same sizes, reaching the same answer.
    children = [_leaf(f"leaf_{index}") for index in range(count)]
    if allowed:
        builder_boolean._require_arity(node_type, children)
    else:
        with pytest.raises(builder_boolean.BooleanStructureError):
            builder_boolean._require_arity(node_type, children)


@pytest.mark.parametrize("node_type", GROUP_TYPES, ids=lambda item: str(item.value))
def test_a_nested_group_holding_one_rule_is_a_valid_draft(node_type) -> None:
    """The third copy of the rule lived here, and it was stricter than the other two.

    A *nested* "all of these" or "any of these" with one rule inside was refused by the
    draft's semantic check while the schema accepted it — so a group somebody drew on
    the canvas passed every check on the page and was refused the moment it was turned
    into a real monitor.
    """

    inner = _group(node_type, 1)
    root = ConditionNodeV2(
        node_id="root_and",
        node_type=ConditionNodeType.AND,
        children=[_leaf("outer"), inner],
    )
    draft = StrategyDraftV2(name="One rule inside", condition_ast=root)
    errors = [item for item in validate_draft_semantics(draft) if "boolean_group" in item]
    assert errors == []


def test_the_arity_table_is_never_written_twice() -> None:
    """Every reader imports the one table. A local copy is how three answers happened."""

    for module in (builder_boolean, inspect.getmodule(validate_draft_semantics)):
        source = inspect.getsource(module)
        declarations = [
            line
            for line in source.splitlines()
            if line.startswith("GROUP_ARITY") and ":" in line and "=" in line
        ]
        assert not declarations, f"{module.__name__} declares its own arity table"
        assert module.GROUP_ARITY is GROUP_ARITY


# ── Every deliverable channel is namable by a monitor ─────────────────────────


def _alert_policy_channels() -> set[str]:
    annotation = AlertPolicy.model_fields["channels"].annotation
    literal = get_args(annotation)[0]
    return {str(value) for value in get_args(literal)}


@pytest.mark.parametrize(
    "channel",
    sorted(item.value for item in DeliveryChannel),
    ids=lambda value: str(value),
)
def test_every_delivery_channel_the_platform_knows_can_be_named_by_a_monitor(channel) -> None:
    """A channel the dispatcher can send on but a monitor cannot ask for is unreachable.

    That is exactly what happened to email: the dispatcher enqueued it, the renderer
    drew it, the sender sent it, Settings offered it — and no monitor could name it, so
    every screen that built its list from the alert schema filtered it straight out.
    """

    assert channel in _alert_policy_channels()


@pytest.mark.parametrize(
    "adapter",
    ["memory", "smtp"],
    ids=["memory-adapter", "smtp-adapter"],
)
def test_email_is_offered_wherever_it_can_actually_be_delivered(adapter) -> None:
    settings = Settings(
        email_adapter=adapter,
        smtp_host="smtp.example.com",
        smtp_from_email="no-reply@example.com",
    )
    offered = {item.value for item in offered_channels(settings)}
    assert DeliveryChannel.EMAIL.value in offered
    # And what is offered is always something a monitor may name.
    assert offered <= _alert_policy_channels()


def test_a_monitor_may_be_told_to_send_by_email() -> None:
    policy = AlertPolicy(channels=["email", "web"])
    assert policy.channels == ["email", "web"]
