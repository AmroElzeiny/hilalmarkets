"""Feature flags decide what is offered. They can never decide what is allowed.

Two failure modes are guarded here.

The first is a flag taking down the product: the Setup surfaces used to share switches, so
turning the planner off also took authoring with it. Losing the assistant is a degraded
product; losing the Builder is a broken one, and no configuration may produce it.

The second is a flag being used as authority. A flag decides whether a feature is
*offered*. The compiler, the Sharia screening, the provider gate, ownership and approval
run identically either way — there is deliberately no flag that skips them, and this file
asserts that none exists.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from ai_market_monitor.services.feature_control import (
    ALWAYS_AVAILABLE,
    Feature,
    FeatureControlService,
    FeatureRule,
    RolloutConfig,
    bucket_for,
    load_rollout_config,
)


def _service(**rules) -> FeatureControlService:
    return FeatureControlService(
        RolloutConfig(rules={rule.feature: rule for rule in rules.values()}, version="test")
    )


# ---------------------------------------------------------------------------
# Each switch is independent
# ---------------------------------------------------------------------------


def test_turning_the_planner_off_leaves_the_builder_on() -> None:
    """The whole reason these are separate members of the enum."""

    service = _service(
        planner=FeatureRule(Feature.PLANNER, default_enabled=False),
        builder=FeatureRule(Feature.GUIDED_BUILDER, default_enabled=True),
    )

    assert service.is_enabled(Feature.PLANNER) is False
    assert service.is_enabled(Feature.GUIDED_BUILDER) is True


@pytest.mark.parametrize("feature", list(Feature))
def test_every_feature_can_be_switched_without_touching_another(feature) -> None:
    """One flag off must leave every other flag exactly where it was."""

    everything_on = {
        item: FeatureRule(item, default_enabled=True) for item in Feature
    }
    baseline = FeatureControlService(RolloutConfig(rules=dict(everything_on)))
    assert all(baseline.is_enabled(item) for item in Feature)

    switched = dict(everything_on)
    switched[feature] = FeatureRule(feature, default_enabled=False)
    service = FeatureControlService(RolloutConfig(rules=switched))

    for other in Feature:
        if other is feature:
            continue
        assert service.is_enabled(other) is True, other


def test_authoring_cannot_be_switched_off_by_configuration() -> None:
    """There is no supported state of this product where nobody can build a setup."""

    config = load_rollout_config({"guided_builder": {"default_enabled": False}})
    service = FeatureControlService(config)

    assert service.is_enabled(Feature.GUIDED_BUILDER) is True


def test_authoring_cannot_be_switched_off_by_the_environment_ceiling_either() -> None:
    config = load_rollout_config({}, environment_disabled=frozenset(Feature))
    service = FeatureControlService(config)

    assert service.is_enabled(Feature.GUIDED_BUILDER) is True
    # Everything else the ceiling names really is off.
    assert service.is_enabled(Feature.PLANNER) is False
    assert service.is_enabled(Feature.FREE_TEXT_AI) is False


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------


def test_the_environment_ceiling_beats_every_runtime_control() -> None:
    """The emergency brake. Somebody set it because they needed the feature off now."""

    config = RolloutConfig(
        rules={
            Feature.PLANNER: FeatureRule(
                Feature.PLANNER,
                default_enabled=True,
                percentage=100,
                allow_user_ids=frozenset({"vip"}),
            )
        },
        environment_disabled=frozenset({Feature.PLANNER}),
    )
    service = FeatureControlService(config)

    decision = service.decide(Feature.PLANNER, user_id="vip")
    assert decision.enabled is False
    assert decision.reason == "environment_disabled"


def test_the_denylist_beats_the_allowlist_and_the_percentage() -> None:
    service = _service(
        planner=FeatureRule(
            Feature.PLANNER,
            default_enabled=True,
            percentage=100,
            allow_user_ids=frozenset({"person"}),
            deny_user_ids=frozenset({"person"}),
        )
    )

    decision = service.decide(Feature.PLANNER, user_id="person")
    assert decision.enabled is False
    assert decision.reason == "denylist"


def test_the_allowlist_beats_a_zero_percentage() -> None:
    service = _service(
        planner=FeatureRule(
            Feature.PLANNER,
            default_enabled=False,
            percentage=0,
            allow_user_ids=frozenset({"person"}),
        )
    )

    decision = service.decide(Feature.PLANNER, user_id="person")
    assert decision.enabled is True
    assert decision.reason == "allowlist"
    assert service.is_enabled(Feature.PLANNER, user_id="somebody-else") is False


def test_a_cohort_turns_a_feature_on_for_its_members_only() -> None:
    service = _service(
        composer=FeatureRule(
            Feature.COMPOSER, default_enabled=False, cohorts=frozenset({"beta-wave-1"})
        )
    )

    inside = service.decide(
        Feature.COMPOSER, user_id="a", cohorts=frozenset({"beta-wave-1"})
    )
    outside = service.decide(
        Feature.COMPOSER, user_id="b", cohorts=frozenset({"beta-wave-2"})
    )

    assert inside.enabled is True
    assert inside.reason == "cohort"
    assert inside.cohort == "beta-wave-1"
    assert outside.enabled is False


# ---------------------------------------------------------------------------
# Percentage rollout is stable
# ---------------------------------------------------------------------------


def test_a_person_stays_in_the_same_bucket_between_requests() -> None:
    """A coin flip per request shows half the product at random and cannot be reproduced."""

    service = _service(
        planner=FeatureRule(Feature.PLANNER, default_enabled=False, percentage=50)
    )
    subject = str(uuid4())

    decisions = [service.decide(Feature.PLANNER, user_id=subject) for _ in range(50)]

    assert len({item.enabled for item in decisions}) == 1
    assert len({item.bucket for item in decisions}) == 1


def test_a_percentage_rollout_lands_near_the_share_it_names() -> None:
    service = _service(
        planner=FeatureRule(Feature.PLANNER, default_enabled=False, percentage=30)
    )
    subjects = [str(uuid4()) for _ in range(2000)]

    enabled = sum(1 for item in subjects if service.is_enabled(Feature.PLANNER, user_id=item))

    assert 0.25 <= enabled / len(subjects) <= 0.35


def test_buckets_are_independent_between_features() -> None:
    """One shared bucket would make every rollout hit the same unlucky people."""

    subjects = [str(uuid4()) for _ in range(500)]
    planner = [bucket_for(Feature.PLANNER, item) for item in subjects]
    composer = [bucket_for(Feature.COMPOSER, item) for item in subjects]

    assert planner != composer
    differing = sum(1 for a, b in zip(planner, composer, strict=True) if a != b)
    assert differing > len(subjects) * 0.9


def test_a_percentage_of_one_hundred_includes_everybody() -> None:
    service = _service(
        planner=FeatureRule(Feature.PLANNER, default_enabled=False, percentage=100)
    )

    assert all(
        service.is_enabled(Feature.PLANNER, user_id=str(uuid4())) for _ in range(200)
    )


@pytest.mark.parametrize("raw", [-10, 0, 55, 100, 500])
def test_an_out_of_range_percentage_is_clamped_not_crashed(raw) -> None:
    rule = FeatureRule(Feature.PLANNER, percentage=raw).normalised()
    assert 0 <= rule.percentage <= 100


# ---------------------------------------------------------------------------
# Malformed configuration fails to the safest state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "not a mapping",
        [],
        {"planner": "not a mapping"},
        {"planner": {"percentage": "lots"}},
        {"no_such_feature": {"default_enabled": True}},
        {"planner": {"allow_user_ids": "not-a-list"}},
    ],
)
def test_malformed_config_leaves_the_ai_off_and_authoring_on(payload) -> None:
    """A configuration mistake degrades one feature. It never breaks the request."""

    config = load_rollout_config(payload)
    service = FeatureControlService(config)

    assert service.is_enabled(Feature.GUIDED_BUILDER) is True
    assert service.is_enabled(Feature.PLANNER, user_id="anyone") is False


def test_an_unknown_flag_name_is_ignored_rather_than_applied() -> None:
    config = load_rollout_config(
        {"planner": {"default_enabled": True}, "teleporter": {"default_enabled": True}}
    )

    assert Feature.PLANNER in config.rules
    assert all(str(item) != "teleporter" for item in config.rules)


# ---------------------------------------------------------------------------
# A flag is not authority
# ---------------------------------------------------------------------------


def test_there_is_no_flag_for_any_governance_gate() -> None:
    """The gates are not features. A switch that could skip one is authority, not rollout.

    Asserted by name because the dangerous version of this mistake is somebody adding
    ``skip_sharia_screening`` to the enum and it looking like every other flag.
    """

    names = {str(item) for item in Feature}
    for forbidden in (
        "sharia",
        "screening",
        "approval",
        "approve",
        "ownership",
        "compiler",
        "provider_gate",
        "idempotency",
        "authorization",
        "skip",
        "bypass",
        "override",
    ):
        assert not any(forbidden in name for name in names), forbidden


def test_the_core_set_is_exactly_what_must_never_be_switched_off() -> None:
    assert frozenset({Feature.GUIDED_BUILDER}) == ALWAYS_AVAILABLE


# ---------------------------------------------------------------------------
# Auditability
# ---------------------------------------------------------------------------


def test_every_decision_carries_the_reason_it_was_reached() -> None:
    """"It was on" does not tell you whether they were allowlisted or inside the share."""

    service = _service(
        planner=FeatureRule(
            Feature.PLANNER,
            default_enabled=False,
            percentage=50,
            allow_user_ids=frozenset({"vip"}),
            deny_user_ids=frozenset({"banned"}),
            cohorts=frozenset({"wave"}),
        )
    )

    assert service.decide(Feature.PLANNER, user_id="banned").reason == "denylist"
    assert service.decide(Feature.PLANNER, user_id="vip").reason == "allowlist"
    assert (
        service.decide(Feature.PLANNER, user_id="c", cohorts=frozenset({"wave"})).reason
        == "cohort"
    )
    assert service.decide(Feature.PLANNER, user_id="d").reason.startswith("percentage")


def test_the_snapshot_stamps_every_feature_and_the_config_version() -> None:
    """Persisted on the turn so an incident replays against the same rollout."""

    config = load_rollout_config({"planner": {"default_enabled": True}}, version="rollout-7")
    snapshot = FeatureControlService(config).snapshot(user_id=str(uuid4()))

    assert snapshot["rollout_version"] == "rollout-7"
    assert set(snapshot["features"]) == {str(item) for item in Feature}
    for payload in snapshot["features"].values():
        assert "enabled" in payload
        assert payload["reason"]


def test_a_variant_is_carried_with_the_decision() -> None:
    """Model route and prompt version are decisions, not just on/off switches."""

    service = _service(
        route=FeatureRule(
            Feature.MODEL_ROUTE, default_enabled=True, variant="fast-model-v2"
        )
    )

    decision = service.decide(Feature.MODEL_ROUTE, user_id="a")
    assert decision.enabled is True
    assert decision.variant == "fast-model-v2"
