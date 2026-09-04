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
    BYTE_ORDER_MARK,
    FORBIDDEN_CLAIM_PHRASES,
    FORBIDDEN_PRODUCT_PHRASES,
    MOJIBAKE_MARKERS,
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


def _settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        **overrides,
    )


def test_turning_the_old_switch_off_still_opens_the_product() -> None:
    """The reconciliation the older switch needs, and the one I broke first time.

    Every deployment configures exposure with PUBLIC_WAITLIST_MODE alone. When the
    stage ignored it, setting it to false changed nothing: the site stayed on the
    waitlist while the setting said otherwise. That is the silent disagreement this
    whole layer exists to remove, so it is asserted rather than trusted.
    """

    open_site = _settings(public_waitlist_mode=False)
    assert open_site.resolved_launch_stage.effective is LaunchStage.PUBLIC_LAUNCH
    assert open_site.waitlist_mode is False
    assert open_site.stage_exposure.advertises_pricing is True


def test_leaving_the_old_switch_on_keeps_the_waitlist() -> None:
    pre_launch = _settings(public_waitlist_mode=True)
    assert pre_launch.resolved_launch_stage.effective is LaunchStage.PUBLIC_WAITLIST
    assert pre_launch.waitlist_mode is True


def test_the_switch_is_read_when_it_changes_not_only_at_startup() -> None:
    """Operators and tests flip it at runtime; a value cached at boot would go stale."""

    settings = _settings(public_waitlist_mode=True)
    assert settings.waitlist_mode is True
    settings.public_waitlist_mode = False
    assert settings.waitlist_mode is False


def test_an_explicit_stage_outranks_the_old_switch() -> None:
    """Once the stage is stated, it is the authority and the switch is only a cap."""

    narrowed = _settings(
        launch_stage=LaunchStage.INTERNAL,
        public_waitlist_mode=False,
    )
    assert narrowed.resolved_launch_stage.effective is LaunchStage.INTERNAL
    assert narrowed.waitlist_mode is False


def test_a_launched_site_with_billing_off_is_a_supported_state() -> None:
    """Prices stay on the page; the button says the plan is not on sale yet.

    Treating this as incoherent would refuse to boot a state the product supports on
    purpose, which is what an earlier version of the startup guard did.
    """

    from ai_market_monitor.core.startup import validate_runtime_configuration

    validate_runtime_configuration(
        _settings(public_waitlist_mode=False, billing_enabled=False)
    )


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


def test_the_lint_catches_a_byte_order_mark() -> None:
    """Without this the rule could be present and match nothing."""

    found = scan_text(f"{BYTE_ORDER_MARK}<!doctype html>", Path("example.html"))
    assert any("byte-order mark" in item.rule for item in found)


def test_no_file_a_browser_downloads_begins_with_a_byte_order_mark() -> None:
    """Three invisible bytes in front of `<!doctype html>` are not nothing.

    Windows PowerShell's `Set-Content -Encoding utf8` adds one to every file it writes.
    A bulk edit across the templates put one into thirty-seven files at once — every
    page still rendered, every test but one still passed, and the one that caught it did
    so only because it compared a rendered page against an exact string.

    Wider than the copy lint on purpose: this is a file-format rule, so it covers every
    template and every asset the browser fetches, not only the ones a customer's words
    come from.
    """

    watched = (
        *(ROOT / "src" / "ai_market_monitor" / "templates").rglob("*.html"),
        *(ROOT / "src" / "ai_market_monitor" / "static").glob("*.css"),
        *(ROOT / "src" / "ai_market_monitor" / "static").glob("*.js"),
    )
    assert len(watched) > 50, "the scan found almost nothing; it is broken, not the files"
    marked = [
        path.relative_to(ROOT).as_posix()
        for path in watched
        if path.read_bytes().startswith(b"\xef\xbb\xbf")
    ]
    assert marked == [], marked


def test_no_template_or_asset_holds_a_mangled_character() -> None:
    """A broken em dash is a broken em dash wherever a person reads it.

    The copy lint checks ``MOJIBAKE_MARKERS`` too, but only over *customer* copy — the
    public templates and the modules that write customer words. That left the admin
    console out, and on 4 September 2026 two em dashes in ``system_brain.html`` were
    written back mangled and nothing said so: every page rendered, ruff and mypy passed,
    and the copy lint was not looking at that file.

    Scoped like the byte-order-mark rule above, and for the same reason: this is a
    file-format rule, not a brand-voice one. Which words are allowed depends on who reads
    them; whether the bytes survived a round trip does not.
    """

    watched = (
        *(ROOT / "src" / "ai_market_monitor" / "templates").rglob("*.html"),
        *(ROOT / "src" / "ai_market_monitor" / "static").glob("*.css"),
        *(ROOT / "src" / "ai_market_monitor" / "static").glob("*.js"),
    )
    assert len(watched) > 50, "the scan found almost nothing; it is broken, not the files"
    damaged: list[str] = []
    for path in watched:
        text = path.read_text(encoding="utf-8")
        hits = sorted({marker for marker in MOJIBAKE_MARKERS if marker in text})
        if hits:
            damaged.append(f"{path.relative_to(ROOT).as_posix()}: {hits}")
    assert damaged == [], (
        "These files hold characters damaged by a lossy read/write round trip. Repair "
        "them in Python, never with a PowerShell file write:\n" + "\n".join(damaged)
    )


@pytest.mark.parametrize("marker", sorted(MOJIBAKE_MARKERS))
def test_the_mangled_character_scan_can_actually_fire(marker: str) -> None:
    """Every marker individually, so the rule above cannot be one that matches nothing.

    The exact failure this guards against: the two mangled em dashes were the sequence
    for one marker only, and a scan that happened to hold a different set would have
    passed over them in silence.
    """

    text = f"<p>Read the rules{marker} then decide.</p>"

    assert any(item in text for item in MOJIBAKE_MARKERS)
    assert any(item.rule for item in scan_text(text, Path("hilal/example.html")))


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
