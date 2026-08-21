"""Authoring a setup must never require the assistant.

The Guided Builder existed, but it offered 47 of the platform's 502 launch-supported
mechanics: the rest were filtered out by ``beginner_friendly and not provider_required``
and the person was told to "use the assistant". That made the AI the only way to author
90% of the product, so an AI outage, an exhausted budget or a disabled feature flag took
most of the feature set down with it.

These tests assert the rule for the whole family, not for a sample. A capability may be
*hard* or *need a data feed* — both are described in the contract. Neither may make the
capability unreachable without a model call.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.builder_contract import (
    FEED_IN_PLAIN_WORDS,
    builder_mechanics,
    capability_mechanics,
    find_mechanic,
)
from ai_market_monitor.engine.builder_operations import (
    ASSISTANT_ONLY_REASON,
    _probe_values,
    build_condition,
    describe_condition,
    mechanic_catalog,
    offered_mechanics,
)
from ai_market_monitor.engine.capabilities import all_capabilities
from ai_market_monitor.engine.capability_shortlist import (
    configured_runtime_provider_requirements,
)

#: The feeds the launch adapter actually implements. Availability is computed against
#: this rather than assumed, so a mechanic is never offered whose data never arrives.
LAUNCH_PROVIDERS = configured_runtime_provider_requirements("ccxt")

#: Every capability the platform says it supports at launch.
LAUNCH_CAPABILITIES = tuple(
    spec for spec in all_capabilities() if spec.executable and spec.availability == "available"
)


def test_the_platform_still_has_the_capability_count_these_tests_assume() -> None:
    """A guard on the guard: if the registry shrinks, the rest of this file proves less."""

    assert len(LAUNCH_CAPABILITIES) > 400


def test_every_launch_supported_capability_is_offered_by_the_builder() -> None:
    """No launch capability may be reachable only through the assistant.

    This is the assertion that fails if the old ``beginner_friendly`` filter is ever
    reintroduced: it would silently drop 455 mechanics and this counts them.
    """

    offered = {item.capability_key for item in capability_mechanics(LAUNCH_PROVIDERS)}
    missing = sorted({spec.key for spec in LAUNCH_CAPABILITIES} - offered)

    assert missing == [], (
        f"{len(missing)} launch-supported capabilities cannot be authored in the "
        f"Builder at all: {missing[:10]}"
    )


def test_no_mechanic_is_withheld_because_a_form_could_not_be_built_for_it() -> None:
    """The catalog marks a mechanic assistant-only when its form cannot produce a rule.

    That reason is the honest one, and it must never be reached: a capability the
    Builder cannot express is a capability the Builder has to grow a field for, not one
    to hand back to the AI.
    """

    stuck = [
        item.key
        for item in mechanic_catalog(LAUNCH_PROVIDERS)
        if item.unavailable_reason == ASSISTANT_ONLY_REASON
    ]

    assert stuck == [], f"{len(stuck)} mechanics still need the assistant: {stuck[:10]}"


def test_a_mechanic_is_unavailable_only_for_a_reason_a_person_can_read() -> None:
    """Every refusal names its cause. "Not available" with no reason is a dead end."""

    for item in mechanic_catalog(LAUNCH_PROVIDERS):
        if item.available:
            continue
        assert item.unavailable_reason, item.key
        assert len(item.unavailable_reason) > 20, item.key


def test_every_mechanic_blocked_on_data_names_the_feed_it_needs() -> None:
    """The only remaining reason to refuse a launch capability is a missing data feed.

    That is an infrastructure fact, not an AI dependency, and it is stated in words with
    the feed named — never as "ask the assistant".

    This used to require the platform's **own** name for the feed to appear in the
    sentence, so all 143 refusals read "(risk_context)", "(universe_ranking)",
    "(token_categories)" to a beginner. The rule was right and the wording was wrong: the
    reason must identify the feed, in words the reader knows. It is checked here against
    the same table the sentence is built from, so a feed added without a plain-words name
    fails this rather than leaking its internal name to a screen.
    """

    blocked = [item for item in mechanic_catalog(LAUNCH_PROVIDERS) if not item.available]
    assert blocked, "expected some capabilities to await a data feed"

    for item in blocked:
        assert item.provider_requirements, item.key
        assert not item.provider_requirements_met, item.key
        reason = item.unavailable_reason or ""
        assert "assistant" not in reason.casefold(), item.key
        assert any(
            FEED_IN_PLAIN_WORDS.get(feed, feed.replace("_", " ")) in reason
            for feed in item.provider_requirements
        ), (item.key, reason)
        # And the internal name itself never reaches the reader.
        assert not any(feed in reason for feed in item.provider_requirements), (
            f"{item.key} shows the platform's internal feed name to a beginner: {reason}"
        )


def test_difficulty_is_described_and_never_used_to_hide_a_mechanic() -> None:
    """``beginner_friendly`` groups the list. It must not shorten it."""

    caps = capability_mechanics(LAUNCH_PROVIDERS)
    beginner = [item for item in caps if item.beginner_friendly]
    advanced = [item for item in caps if not item.beginner_friendly]

    assert beginner, "the simple grouping disappeared"
    assert advanced, "the advanced grouping disappeared"
    # The advanced ones are the majority, and they are present, not filtered away.
    assert len(advanced) > len(beginner)
    assert len(caps) == len(LAUNCH_CAPABILITIES)


@pytest.mark.parametrize(
    "mechanic",
    list(offered_mechanics(LAUNCH_PROVIDERS)),
    ids=[item.key for item in offered_mechanics(LAUNCH_PROVIDERS)],
)
def test_every_offered_mechanic_builds_a_real_rule_with_no_model_call(mechanic) -> None:
    """Author each mechanic from its own declared fields, with nothing else supplied.

    ``_probe_values`` reads only what the contract publishes, so passing this proves the
    form is self-sufficient: a person with the Builder open and no assistant can produce
    a valid, compiling rule for this mechanic.
    """

    node, sentence = build_condition(
        mechanic_key=mechanic.key,
        values=_probe_values(mechanic),
        source_turn_id="zero-ai-authoring",
        configured_providers=LAUNCH_PROVIDERS,
    )

    assert node is not None
    assert sentence.strip(), mechanic.key
    # And the rule reads back into the same form, so it can be edited without the AI too.
    view = describe_condition(node)
    assert view.mechanic_key == mechanic.key, mechanic.key
    assert view.editable, (mechanic.key, view.not_editable_reason)


def test_membership_does_not_depend_on_which_feeds_are_connected() -> None:
    """Losing a data feed must not make an existing rule uneditable.

    Availability changes with the configured adapter; the *set* of mechanics does not.
    If membership moved too, a rule authored while a feed was live would stop being
    recognised the moment that feed went away, and the person could not even read it.
    """

    with_feeds = {item.key for item in builder_mechanics(LAUNCH_PROVIDERS)}
    without_feeds = {item.key for item in builder_mechanics(frozenset())}

    assert with_feeds == without_feeds
    # Availability may only shrink when feeds are taken away, never grow.
    live = sum(1 for item in builder_mechanics(LAUNCH_PROVIDERS) if item.available)
    dark = sum(1 for item in builder_mechanics(frozenset()) if item.available)
    assert dark <= live


def test_connecting_a_feed_unblocks_exactly_the_capabilities_that_named_it() -> None:
    """The blocked list is a real dependency, not a label.

    Each blocked mechanic names the feed it is waiting for. Adding that one feed must
    release exactly those mechanics and nothing else — if the count moved by any other
    amount, the reason shown to the person would not be the true reason.
    """

    blocked = [
        item
        for item in capability_mechanics(LAUNCH_PROVIDERS)
        if not item.provider_requirements_met
    ]
    assert blocked

    feed = blocked[0].provider_requirements[0]
    waiting_on_feed = {
        item.key
        for item in blocked
        if set(item.provider_requirements) <= (set(LAUNCH_PROVIDERS) | {feed})
    }
    assert waiting_on_feed

    before = {item.key for item in capability_mechanics(LAUNCH_PROVIDERS) if item.available}
    after = {
        item.key
        for item in capability_mechanics(frozenset(set(LAUNCH_PROVIDERS) | {feed}))
        if item.available
    }

    assert after - before == waiting_on_feed
    assert before - after == set()


def test_a_capability_needing_a_feed_is_refused_at_authoring_not_silently_swapped() -> None:
    """Fail closed: never substitute a nearest mechanic for one that cannot run."""

    blocked = next(
        item
        for item in mechanic_catalog(LAUNCH_PROVIDERS)
        if not item.available and item.capability_key
    )

    from ai_market_monitor.engine.builder_operations import BuilderActionError

    with pytest.raises(BuilderActionError) as raised:
        build_condition(
            mechanic_key=blocked.key,
            values=_probe_values(blocked),
            source_turn_id="zero-ai-authoring",
            configured_providers=LAUNCH_PROVIDERS,
        )
    assert raised.value.code == "MECHANIC_UNAVAILABLE"


def test_the_contract_lookup_covers_every_capability_key() -> None:
    """``find_mechanic`` resolves each capability, so nothing is orphaned by key shape."""

    for spec in LAUNCH_CAPABILITIES:
        assert find_mechanic(f"capability:{spec.key}") is not None, spec.key
