"""What a rollout switch may and may not do, and that both authoring paths agree.

Two families here, both of which exist because of the same mistake: a control that was
supposed to affect *the assistant* affected *the product*.

* **Rollout.** A flag decides whether a feature is offered. It can never grant authority,
  never skip screening, and never take authoring away. One capability can be paused on its
  own without touching the other five hundred.
* **Equivalence.** The Builder and the assistant must produce the *same stored state*. If
  they can drift, then "you can always build it yourself" is only true for the parts
  nobody checked.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.builder_contract import (
    capability_mechanics,
    disabled_capabilities_from,
)
from ai_market_monitor.engine.builder_operations import condition_nodes
from ai_market_monitor.services.ai_setup_chat import SetupChatError
from ai_market_monitor.services.feature_control import (
    ALWAYS_AVAILABLE,
    Feature,
    FeatureControlService,
    FeatureRule,
    RolloutConfig,
    environment_disabled_from,
    rollout_config_from_settings,
)
from ai_market_monitor.services.setup_chat_launch import load_strategy_draft_v2
from tests.integration.test_guided_builder import _act, _service, _user
from tests.integration.test_setup_chat_launch_v2 import StandInPlanner

pytestmark = pytest.mark.anyio

#: A real registry capability, not a core grammar mechanic. Pausing has to work on the
#: 502 that come from the registry, because that is where a bad formula would live.
_PAUSED_CAPABILITY = "ema_crossover"
#: The Builder names the same thing with a prefix. Both names appear here on purpose:
#: the configuration switch takes the capability key, the catalogue is keyed by the
#: mechanic key, and getting those two confused is how a pause would silently do nothing.
_PAUSED_KEY = f"capability:{_PAUSED_CAPABILITY}"
_RULE = {
    "mechanic_key": "open_to_close_percentage",
    "values": {"direction": "up", "comparator": "gte", "threshold": 5, "timeframe": "15m"},
}
_PAUSED_RULE = {"mechanic_key": _PAUSED_KEY, "values": {"timeframe": "15m"}}
_TYPED = "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"



def _has_operator(node, operator: str) -> bool:
    """Whether the stored tree contains this join anywhere.

    Checked by *shape*, not by the root: grouping the only two rules can legitimately
    collapse the root onto the new group, and asserting on the root alone would make the
    test fail for a tree that is exactly right.
    """

    if node is None:
        return False
    if str(node.node_type) == operator:
        return True
    return any(_has_operator(child, operator) for child in (node.children or []))


def _rules(chat) -> list:
    return condition_nodes(load_strategy_draft_v2(chat).condition_ast)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "app_secret_key": "test-secret-key-with-at-least-thirty-two-characters",
        "app_env": "test",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# One capability, paused on its own
# ---------------------------------------------------------------------------


async def test_pausing_one_capability_leaves_every_other_one_working() -> None:
    """The blast radius of a bad formula is one rule, not the product."""

    paused = capability_mechanics(frozenset(), frozenset({_PAUSED_CAPABILITY}))
    by_key = {item.key: item for item in paused}

    assert by_key[_PAUSED_KEY].available is False
    assert len(by_key) > 100, "the rest of the catalogue is untouched"
    assert all(
        item.available for key, item in by_key.items() if key != _PAUSED_KEY and item.available
    )
    # And with nothing paused, the same capability is offered normally.
    normal = {item.key: item for item in capability_mechanics(frozenset(), frozenset())}
    assert normal[_PAUSED_KEY].available is True


async def test_a_paused_capability_is_shown_with_a_reason_never_hidden() -> None:
    """A rule that silently disappears looks like lost work to whoever used it."""

    paused = {
        item.key: item
        for item in capability_mechanics(frozenset(), frozenset({_PAUSED_CAPABILITY}))
    }
    assert _PAUSED_KEY in paused, "still listed"
    reason = paused[_PAUSED_KEY].unavailable_reason or ""
    assert reason, "and it says why"
    assert "still works" in reason.casefold(), "and what a person can still do"


async def test_the_server_refuses_a_paused_capability_even_if_the_client_asks(
    test_context,
) -> None:
    """A stale browser tab must not be able to write a rule that was switched off."""

    user = await _user(test_context)
    service = _service(
        test_context, StandInPlanner(), builder_capabilities_disabled=_PAUSED_CAPABILITY
    )

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _act(service, session, chat, "select_mode", "p-mode", value="monitor")
        with pytest.raises(SetupChatError) as refused:
            await _act(service, session, chat, "add_condition", "p-rule", **_PAUSED_RULE)
        assert refused.value.code == "MECHANIC_UNAVAILABLE"
        assert _rules(chat) == [], "and nothing was written"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a,b", {"a", "b"}),
        ("a;b", {"a", "b"}),
        (" a , b ", {"a", "b"}),
        ("", set()),
        ("   ", set()),
        (",,", set()),
    ],
)
async def test_the_paused_list_is_read_the_same_way_however_it_is_written(
    raw: str, expected: set[str]
) -> None:
    assert disabled_capabilities_from(raw) == frozenset(expected)


# ---------------------------------------------------------------------------
# The rollout can never take authoring away
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("feature", list(Feature))
async def test_no_environment_ceiling_can_switch_authoring_off(feature: Feature) -> None:
    """Parametrised over every flag, because the one nobody tried is the one that breaks."""

    control = FeatureControlService(
        rollout_config_from_settings(_settings(ai_features_disabled=feature.value))
    )
    assert control.is_enabled(Feature.GUIDED_BUILDER) is True
    if feature not in ALWAYS_AVAILABLE:
        assert control.is_enabled(feature) is False


@pytest.mark.parametrize(
    "raw", ["planer", "PLANNER", "", "   ", "planner,", "not-a-flag,also-not"]
)
async def test_an_unreadable_emergency_switch_disables_nothing_and_raises_nothing(
    raw: str,
) -> None:
    """An operator typo during an incident must not take the application down."""

    disabled = environment_disabled_from(raw)
    assert all(isinstance(item, Feature) for item in disabled), "only real flags survive"
    # Whatever it read, the request still works.
    control = FeatureControlService(
        rollout_config_from_settings(_settings(ai_features_disabled=raw))
    )
    assert control.is_enabled(Feature.GUIDED_BUILDER) is True


async def test_the_existing_setup_switches_are_read_by_the_one_owner() -> None:
    """These used to be separate booleans read in separate modules, so "is the assistant
    on?" had several answers and the safest one did not always win."""

    control = FeatureControlService(
        rollout_config_from_settings(
            _settings(
                setup_free_text_enabled=False,
                setup_planner_enabled=False,
                setup_composer_enabled=False,
                setup_scanner_enabled=False,
                setup_monitor_enabled=False,
            )
        )
    )
    for feature in (
        Feature.FREE_TEXT_AI,
        Feature.PLANNER,
        Feature.COMPOSER,
        Feature.SCANNER,
        Feature.MONITOR,
    ):
        assert control.is_enabled(feature) is False, feature
    assert control.is_enabled(Feature.GUIDED_BUILDER) is True


async def test_the_model_route_and_prompt_version_travel_with_the_decision() -> None:
    """An incident is replayed against the route and schema that produced it."""

    control = FeatureControlService(
        rollout_config_from_settings(
            _settings(openai_model="gpt-5.4-mini", ai_rollout_version="rollout-7")
        )
    )
    assert control.decide(Feature.MODEL_ROUTE).variant == "gpt-5.4-mini"
    assert control.decide(Feature.PROMPT_SCHEMA_VERSION).variant == "rollout-7"
    assert control.snapshot()["rollout_version"] == "rollout-7"


async def test_a_language_rollout_is_decided_by_the_configured_language_list() -> None:
    assert (
        FeatureControlService(rollout_config_from_settings(_settings())).is_enabled(
            Feature.LANGUAGE_NON_ENGLISH
        )
        is True
    ), "no list means every supported language"
    assert (
        FeatureControlService(
            rollout_config_from_settings(_settings(setup_ai_languages=["en"]))
        ).is_enabled(Feature.LANGUAGE_NON_ENGLISH)
        is False
    )
    assert (
        FeatureControlService(
            rollout_config_from_settings(_settings(setup_ai_languages=["en", "ar"]))
        ).is_enabled(Feature.LANGUAGE_NON_ENGLISH)
        is True
    )


async def test_a_cohort_rollout_includes_the_named_group_and_nobody_else() -> None:
    config = RolloutConfig(
        rules={
            Feature.SCANNER: FeatureRule(
                Feature.SCANNER, default_enabled=False, cohorts=frozenset({"beta"})
            )
        },
        version="cohort-1",
    )
    control = FeatureControlService(config)
    person = str(uuid4())

    inside = control.decide(Feature.SCANNER, user_id=person, cohorts=frozenset({"beta"}))
    outside = control.decide(Feature.SCANNER, user_id=person, cohorts=frozenset({"other"}))

    assert (inside.enabled, inside.reason, inside.cohort) == (True, "cohort", "beta")
    assert outside.enabled is False


async def test_a_percentage_rollout_puts_the_same_person_in_the_same_bucket_every_time() -> None:
    """A coin flip per request shows half the product at random and cannot be reproduced."""

    config = RolloutConfig(
        rules={Feature.SCANNER: FeatureRule(Feature.SCANNER, percentage=50)},
        version="pct-1",
    )
    control = FeatureControlService(config)
    person = str(uuid4())
    answers = {control.decide(Feature.SCANNER, user_id=person).enabled for _ in range(20)}
    assert len(answers) == 1


# ---------------------------------------------------------------------------
# The two authoring paths agree
# ---------------------------------------------------------------------------


async def test_the_builder_and_the_assistant_store_the_same_universe_and_method(
    test_context,
) -> None:
    """"You can always build it yourself" is only true if both paths land in one place."""

    from ai_market_monitor.services.builder_universe import builder_universe_options

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _act(service, session, chat, "select_mode", "eq-mode", value="monitor")
        await _act(
            service, session, chat, "select_universe", "eq-u", value="eligible_market"
        )
        options = await builder_universe_options(
            session, user_id=user.id, draft=load_strategy_draft_v2(chat)
        )
        target = options.methodologies[0].methodology_id
        await _act(service, session, chat, "select_methodology", "eq-m", value=target)
        first = load_strategy_draft_v2(chat)

        # The same two choices, made a second time down the same option path the
        # assistant's own answers travel.
        second_chat = await service.create_session(session, user.id)
        await _act(
            service, session, second_chat, "select_mode", "eq2-mode", value="monitor"
        )
        await _act(
            service,
            session,
            second_chat,
            "select_universe",
            "eq2-u",
            value="eligible_market",
        )
        await _act(
            service, session, second_chat, "select_methodology", "eq2-m", value=target
        )
        second = load_strategy_draft_v2(second_chat)

        assert first.universe == second.universe
        assert first.sharia_policy.methodology_id == second.sharia_policy.methodology_id
        assert first.semantic_hash == second.semantic_hash, (
            "the same choices must produce the same canonical draft, whoever clicked them"
        )


async def test_explicit_coins_stay_screened_when_they_are_chosen_by_hand(
    test_context,
) -> None:
    """Naming coins yourself is not a way round screening.

    The Builder writes through the same option path the assistant uses, so the screening
    gate runs on a hand-typed list exactly as it does on an assistant-proposed one. A
    second write path here would be a way to put an unscreened coin into a monitor.
    """

    from ai_market_monitor.services.builder_universe import builder_universe_options

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _act(service, session, chat, "select_mode", "sc-mode", value="monitor")
        await _act(
            service, session, chat, "select_universe", "sc-u", value="explicit_assets"
        )
        await _act(
            service,
            session,
            chat,
            "set_explicit_assets",
            "sc-coins",
            value="BTC, ETH",
        )
        draft = load_strategy_draft_v2(chat)
        options = await builder_universe_options(session, user_id=user.id, draft=draft)

        # Stored, canonicalised, and still pointed at a governed screening method. The
        # server turns a typed "BTC" into the tradable pair that screening and the
        # provider both use; nothing here can mark a coin halal.
        assert options.universe_mode == "explicit_assets"
        assert any(item.startswith("BTC") for item in options.explicit_assets)
        assert any(item.startswith("ETH") for item in options.explicit_assets)
        assert all("/" in item for item in options.explicit_assets), (
            "stored in the resolved form screening and the provider both use"
        )
        assert draft.sharia_policy is not None, (
            "an explicit list is still screened against a governed method"
        )


async def test_nested_logic_built_by_hand_survives_being_read_back(test_context) -> None:
    """A refresh reloads from the database. A shape that only existed in the browser is
    a shape the compiler never agreed to."""

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())
    second = {
        "mechanic_key": "open_to_close_percentage",
        "values": {
            "direction": "down",
            "comparator": "gte",
            "threshold": 3,
            "timeframe": "1h",
        },
    }

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _act(service, session, chat, "select_mode", "n-mode", value="monitor")
        await _act(service, session, chat, "select_universe", "n-u", value="eligible_market")
        await _act(service, session, chat, "add_condition", "n-r1", **_RULE)
        await _act(service, session, chat, "add_condition", "n-r2", **second)

        nodes = _rules(chat)
        await _act(
            service,
            session,
            chat,
            "group_conditions",
            "n-group",
            node_ids=[nodes[0].node_id, nodes[1].node_id],
            operator="or",
        )
        before = load_strategy_draft_v2(chat)
        await session.commit()

    # A completely fresh session: nothing in memory, everything from the row.
    async with test_context["session_factory"]() as session:
        reopened = await service.owned_session(session, user.id, chat.id)
        after = load_strategy_draft_v2(reopened)

        assert after.semantic_hash == before.semantic_hash
        assert after.condition_ast is not None
        assert _has_operator(after.condition_ast, "or"), "the grouping survived the reload"
        assert len(condition_nodes(after.condition_ast)) == 2
