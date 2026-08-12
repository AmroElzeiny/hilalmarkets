"""Launch stage, product boundaries and customer wording.

The three together answer one question: what does this product say it is, right
now. Each is server-owned, so each is tested over its whole set rather than over
the one stage or the one phrase that prompted the change.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.copy_rules import (
    FORBIDDEN_CLAIM_PHRASES,
    FORBIDDEN_PRODUCT_PHRASES,
    customer_copy_sources,
    scan_customer_copy,
    scan_text,
)
from ai_market_monitor.core.launch_stage import (
    ALLOWED_STAGE_TRANSITIONS,
    STAGE_EXPOSURE,
    STAGE_ORDER,
    LaunchStage,
    StageTransitionError,
    assert_transition_allowed,
    resolve_launch_stage,
)
from ai_market_monitor.core.product_boundaries import (
    BOUNDARY_REGISTRY,
    EVALUATION_MODES,
    NON_NEGOTIABLE_BOUNDARIES,
    EvaluationMode,
    SupportState,
    UnsupportedCapability,
    refuse,
    supported_capabilities,
    unsupported_capabilities,
)

ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------
# Launch stage
# --------------------------------------------------------------------------


def test_every_stage_declares_its_exposure() -> None:
    assert set(STAGE_EXPOSURE) == set(LaunchStage)
    assert set(STAGE_ORDER) == set(LaunchStage)


@pytest.mark.parametrize("stage", list(LaunchStage), ids=lambda item: item.value)
def test_checkout_is_never_offered_without_pricing(stage: LaunchStage) -> None:
    exposure = STAGE_EXPOSURE[stage]
    if exposure.exposes_checkout:
        assert exposure.advertises_pricing


@pytest.mark.parametrize(
    "stage",
    [LaunchStage.INTERNAL, LaunchStage.PRIVATE_BETA_INVITE, LaunchStage.PUBLIC_WAITLIST],
    ids=lambda item: item.value,
)
def test_no_pre_launch_stage_sells_anything(stage: LaunchStage) -> None:
    """Nothing is buyable until the product is actually open."""

    exposure = STAGE_EXPOSURE[stage]
    assert not exposure.advertises_pricing
    assert not exposure.exposes_checkout
    assert not exposure.advertises_account_entry
    assert not exposure.assistant_may_offer_account
    assert "pricing" in exposure.hidden_pages


def test_only_public_launch_opens_the_product() -> None:
    exposure = STAGE_EXPOSURE[LaunchStage.PUBLIC_LAUNCH]
    assert exposure.advertises_pricing
    assert exposure.exposes_checkout
    assert exposure.advertises_account_entry
    assert exposure.hidden_pages == frozenset()


@pytest.mark.parametrize(
    "current,target",
    [
        (current, target)
        for current, targets in ALLOWED_STAGE_TRANSITIONS.items()
        for target in targets
    ],
    ids=lambda item: getattr(item, "value", str(item)),
)
def test_a_declared_transition_is_allowed(current, target) -> None:
    assert_transition_allowed(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (current, target)
        for current, target in itertools.product(LaunchStage, LaunchStage)
        if target not in ALLOWED_STAGE_TRANSITIONS[current]
    ],
    ids=lambda item: getattr(item, "value", str(item)),
)
def test_an_undeclared_transition_fails_closed(current, target) -> None:
    with pytest.raises(StageTransitionError):
        assert_transition_allowed(current, target)


def test_widening_cannot_skip_a_stage() -> None:
    """Internal to public launch would skip the two states meant to find problems."""

    with pytest.raises(StageTransitionError):
        assert_transition_allowed(LaunchStage.INTERNAL, LaunchStage.PUBLIC_LAUNCH)


@pytest.mark.parametrize("stage", list(LaunchStage), ids=lambda item: item.value)
def test_narrowing_is_always_possible_in_an_emergency(stage: LaunchStage) -> None:
    """Pulling the product back must never be blocked by the state machine."""

    if stage is not LaunchStage.INTERNAL:
        assert_transition_allowed(stage, LaunchStage.INTERNAL)


@pytest.mark.parametrize("stage", list(LaunchStage), ids=lambda item: item.value)
def test_the_environment_ceiling_only_ever_narrows(stage: LaunchStage) -> None:
    resolved = resolve_launch_stage(stage, waitlist_ceiling=True)
    assert STAGE_ORDER.index(resolved.effective) <= STAGE_ORDER.index(stage)
    assert STAGE_ORDER.index(resolved.effective) <= STAGE_ORDER.index(
        LaunchStage.PUBLIC_WAITLIST
    )


def test_the_ceiling_reports_when_it_disagrees_with_the_configured_stage() -> None:
    resolved = resolve_launch_stage(LaunchStage.PUBLIC_LAUNCH, waitlist_ceiling=True)
    assert resolved.configured is LaunchStage.PUBLIC_LAUNCH
    assert resolved.effective is LaunchStage.PUBLIC_WAITLIST
    assert resolved.clamped_by_environment is True


def test_without_the_ceiling_the_configured_stage_stands() -> None:
    resolved = resolve_launch_stage(LaunchStage.PUBLIC_LAUNCH, waitlist_ceiling=False)
    assert resolved.effective is LaunchStage.PUBLIC_LAUNCH
    assert resolved.clamped_by_environment is False


def test_waitlist_mode_is_derived_from_the_stage_not_read_from_the_setting() -> None:
    """The setting is a ceiling. The stage is the authority."""

    settings = Settings(
        _env_file=None,
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        launch_stage=LaunchStage.PUBLIC_LAUNCH,
        public_waitlist_mode=False,
    )
    assert settings.waitlist_mode is False
    assert settings.stage_exposure.advertises_pricing is True

    clamped = Settings(
        _env_file=None,
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        launch_stage=LaunchStage.PUBLIC_LAUNCH,
        public_waitlist_mode=True,
    )
    assert clamped.waitlist_mode is True
    assert clamped.stage_exposure.advertises_pricing is False
    assert clamped.resolved_launch_stage.clamped_by_environment is True


def test_an_unrecognised_stage_is_refused_at_configuration_time() -> None:
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
            launch_stage="soft_launch",
        )


# --------------------------------------------------------------------------
# Product boundaries
# --------------------------------------------------------------------------


def test_boundary_keys_are_unique() -> None:
    keys = [entry.key for entry in BOUNDARY_REGISTRY]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("entry", BOUNDARY_REGISTRY, ids=lambda item: item.key)
def test_every_capability_has_a_customer_readable_reason(entry) -> None:
    """Including the supported ones.

    "Yes" without "and here is what that means" is how a feature gets believed to do
    more than it does.
    """

    assert entry.title.strip()
    assert entry.reason.strip()
    assert len(entry.reason) > 30
    assert entry.support in set(SupportState)


@pytest.mark.parametrize(
    "entry", unsupported_capabilities(), ids=lambda item: item.key
)
def test_every_unsupported_capability_produces_an_explicit_refusal(entry) -> None:
    refusal = refuse(entry.key)
    assert isinstance(refusal, UnsupportedCapability)
    assert refusal.key == entry.key
    assert entry.title in refusal.customer_message()
    assert refusal.reason == entry.reason


@pytest.mark.parametrize("entry", supported_capabilities(), ids=lambda item: item.key)
def test_a_supported_capability_is_never_refused(entry) -> None:
    """A refusal for something that works means the caller read the wrong key."""

    with pytest.raises(ValueError, match="supported capability"):
        refuse(entry.key)


def test_a_refusal_object_offers_no_substitute() -> None:
    """The whole point.

    A field holding a nearby capability is exactly the hook that turns "we cannot do
    that" into "we quietly did something else".
    """

    refusal = refuse("trade_execution")
    attributes = set(UnsupportedCapability.__dataclass_fields__)
    assert not {"alternative", "suggested_alternative", "instead", "fallback"} & attributes
    assert refusal.is_permanent


@pytest.mark.parametrize(
    "key",
    ["trade_execution", "brokerage_custody", "buy_sell_recommendations", "financial_advice"],
)
def test_the_four_non_negotiable_boundaries_are_permanent(key: str) -> None:
    assert refuse(key).support is SupportState.OUT_OF_SCOPE


def test_the_non_negotiable_statements_are_present_and_plain() -> None:
    assert len(NON_NEGOTIABLE_BOUNDARIES) == 4
    for statement in NON_NEGOTIABLE_BOUNDARIES:
        assert statement.startswith("Hilal Markets")
        assert statement.endswith(".")


def test_an_unknown_capability_raises_rather_than_inventing_one() -> None:
    with pytest.raises(KeyError):
        refuse("teleportation")


# --------------------------------------------------------------------------
# Scanner and Monitor
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode", list(EvaluationMode), ids=lambda item: item.value
)
def test_each_mode_states_trigger_cadence_cost_and_what_it_does_not_do(mode) -> None:
    definition = EVALUATION_MODES[mode]
    assert definition.trigger.strip()
    assert definition.cadence.strip()
    assert definition.cost_note.strip()
    assert definition.does_not.strip()


def test_the_two_modes_are_not_variants_of_one_thing() -> None:
    scanner = EVALUATION_MODES[EvaluationMode.SCANNER]
    monitor = EVALUATION_MODES[EvaluationMode.MONITOR]
    assert scanner.requires_approval is False
    assert monitor.requires_approval is True
    assert scanner.trigger != monitor.trigger
    assert scanner.cadence != monitor.cadence


# --------------------------------------------------------------------------
# Customer copy
# --------------------------------------------------------------------------


def test_customer_copy_sources_are_actually_found() -> None:
    """A lint over an empty file list passes for the wrong reason."""

    sources = customer_copy_sources(ROOT)
    assert len(sources) >= 4


def test_no_forbidden_phrase_appears_in_any_customer_copy_source() -> None:
    violations = scan_customer_copy(ROOT)
    assert not violations, "\n".join(item.describe(ROOT) for item in violations)


@pytest.mark.parametrize("phrase", FORBIDDEN_CLAIM_PHRASES)
def test_the_lint_actually_catches_each_forbidden_claim(phrase: str) -> None:
    """Without this the lint could match nothing and still report success."""

    found = scan_text(f"<p>Our product is {phrase} today.</p>", Path("example.html"))
    assert any(item.rule == "forbidden claim" for item in found)


@pytest.mark.parametrize("phrase", FORBIDDEN_PRODUCT_PHRASES)
def test_the_lint_actually_catches_each_deprecated_term(phrase: str) -> None:
    found = scan_text(f"<p>Open your {phrase}.</p>", Path("example.html"))
    assert any(item.rule == "deprecated product term" for item in found)


def test_technical_usage_must_be_spelled_shariah() -> None:
    found = scan_text("<p>Sharia screening explained.</p>", Path("example.html"))
    assert any("Shariah" in item.rule for item in found)


def test_the_spelling_rule_leaves_internal_identifiers_alone() -> None:
    """Renaming an API path or an asset filename is not a copy fix.

    The rule is case sensitive precisely so that a route, a stylesheet name and a
    Jinja macro survive it untouched.
    """

    identifiers = (
        '<div data-endpoint="/api/v1/sharia/market-quotes"></div>',
        "{% macro sharia_status(label) %}",
        "<link href=\"/static/sharia-product.css\">",
        "from ai_market_monitor.services.sharia_universe import ShariaUniverseResolver",
    )
    for text in identifiers:
        assert scan_text(text, Path("example.html")) == ()
