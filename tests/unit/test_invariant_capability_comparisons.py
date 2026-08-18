"""A capability must agree with itself about how it is compared.

Two fields described one fact — the comparison a rule starts on, and the comparisons it
allows — and they were declared separately, so they were free to disagree. 149 of them
did: they said "my comparison is `is_true`" while listing only numeric comparisons.
Nothing failed. The template builder resolved the contradiction on its own by rewriting
`is_true` into `>= 0`, and a rule that was supposed to fire on one event silently
matched every candle instead.

That is the worst shape a bug can take in this product: not a crash, not a refusal, but
a monitor that quietly watches something other than what it says.

These tests are written over the whole registry, one case per capability, because the
fault was never in a particular capability — it was in the two of them being allowed to
disagree at all.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.builder_templates import condition_template
from ai_market_monitor.engine.capabilities import CAPABILITIES, executable_capabilities
from ai_market_monitor.schemas.strategy import UNARY_COMPARATORS

ALL = sorted(CAPABILITIES, key=lambda item: item.key)
EXECUTABLE = sorted(executable_capabilities(), key=lambda item: item.key)


@pytest.mark.parametrize("capability", ALL, ids=lambda item: item.key)
def test_a_capability_allows_the_comparison_it_starts_on(capability):
    assert capability.default_comparator in capability.supported_comparators, (
        f"{capability.key} starts on {capability.default_comparator!r} but allows only "
        f"{capability.supported_comparators!r}"
    )


@pytest.mark.parametrize("capability", ALL, ids=lambda item: item.key)
def test_a_capability_is_either_a_yes_no_or_a_measurement_never_both(capability):
    """Mixing the two is what let a yes/no rule be rewritten as a numeric one."""
    allowed = set(capability.supported_comparators)
    unary = allowed & set(UNARY_COMPARATORS)
    measured = allowed - set(UNARY_COMPARATORS)
    assert not (unary and measured), (
        f"{capability.key} allows both yes/no and measured comparisons: {sorted(allowed)}"
    )
    assert allowed, f"{capability.key} allows no comparison at all"


@pytest.mark.parametrize("capability", EXECUTABLE, ids=lambda item: item.key)
def test_a_yes_no_capability_never_compiles_into_a_measurement(capability):
    """The exact failure, checked on the thing a person would actually run."""
    if capability.default_comparator not in UNARY_COMPARATORS:
        return
    template = condition_template(capability, timeframe="15m")
    assert template["comparator"] in UNARY_COMPARATORS, (
        f"{capability.key} answers yes or no, but its template compares with "
        f"{template['comparator']!r} against {template['right']!r}"
    )
    assert template["right"] is None, (
        f"{capability.key} answers yes or no, so it has nothing to compare against, "
        f"but its template carries {template['right']!r}"
    )


@pytest.mark.parametrize("capability", EXECUTABLE, ids=lambda item: item.key)
def test_a_measured_capability_always_has_something_to_compare_against(capability):
    if capability.default_comparator in UNARY_COMPARATORS:
        return
    template = condition_template(capability, timeframe="15m")
    right = template["right"]
    assert right is not None, f"{capability.key} compares, but against nothing"
    if right.get("kind") == "constant":
        assert not isinstance(right["value"], bool), (
            f"{capability.key} compares against the bare value {right['value']!r}, "
            "which is not a number"
        )


@pytest.mark.parametrize("capability", ALL, ids=lambda item: item.key)
def test_a_yes_no_capability_carries_no_measured_level(capability):
    """A level on a yes/no rule is a number nobody can ever use."""
    if capability.default_comparator not in UNARY_COMPARATORS:
        return
    assert capability.default_threshold is True, (
        f"{capability.key} answers yes or no but declares a level of "
        f"{capability.default_threshold!r}"
    )


@pytest.mark.parametrize("capability", ALL, ids=lambda item: item.key)
def test_a_measured_capability_never_carries_a_yes_no_level(capability):
    if capability.default_comparator in UNARY_COMPARATORS:
        return
    assert not isinstance(capability.default_threshold, bool), (
        f"{capability.key} measures a number but declares a level of "
        f"{capability.default_threshold!r}"
    )


def test_the_registry_refuses_a_capability_that_does_not_say_how_it_is_compared():
    """Silence used to mean "yes/no" by accident. It now means "say so"."""
    from ai_market_monitor.engine.capabilities import _cap

    with pytest.raises(ValueError, match="does not say how it is compared"):
        _cap(
            "invented_metric_for_this_test",
            "Invented metric",
            "market_filter",
            "market_filter",
            "A metric that never said whether it is a flag or a measurement.",
            operand_kind="market_metric",
            operand_name="invented_metric_for_this_test",
        )


def test_the_registry_refuses_a_capability_that_contradicts_itself():
    from ai_market_monitor.engine.capabilities import _cap

    with pytest.raises(ValueError, match="does not list it in supported_comparators"):
        _cap(
            "contradictory_metric_for_this_test",
            "Contradictory metric",
            "market_filter",
            "market_filter",
            "Says one comparison and allows another.",
            operand_kind="market_metric",
            operand_name="contradictory_metric_for_this_test",
            default_comparator="is_true",
            supported_comparators=("gt", "gte"),
        )


def test_the_template_builder_refuses_rather_than_choosing_a_comparison():
    """CLAUDE.md: never substitute a nearest comparator, never fall back to a default.

    The builder used to reach for "gte" when a capability's own comparison was not in
    its list. Refusing keeps the mistake where a person can see it.
    """
    from dataclasses import replace

    broken = replace(
        CAPABILITIES[0],
        default_comparator="is_true",
        supported_comparators=("gt", "gte"),
    )
    with pytest.raises(ValueError, match="No comparison is chosen in its place"):
        condition_template(broken)
