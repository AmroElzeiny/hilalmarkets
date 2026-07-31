"""The production-authority invariants, each asserted directly.

Every test names the invariant it proves and the production function that enforces it.
They assert a *rule* across a family of inputs — every claim type, every net effect, every
universe mode — so a fix that only helps one reported example fails here.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ai_market_monitor.core.startup import _weak_database_password
from ai_market_monitor.db.models.enums import ShariaUniverseMode
from ai_market_monitor.engine.claim_evidence import (
    EvidenceLedger,
    build_evidence_ledger,
    deterministic_claim_text,
    validate_claims,
)
from ai_market_monitor.engine.operation_reconciliation import reconcile_turn
from ai_market_monitor.engine.parameter_roles import (
    ParameterRoleSpec,
    ambiguous_role_pairs,
    role_grounding_errors,
    role_specs,
)
from ai_market_monitor.engine.strategy_draft_v2 import apply_strategy_patch
from ai_market_monitor.schemas.screening_execution import (
    PreflightManifest,
    ReviewedScreeningEvidence,
    ScreeningExecutionResult,
    symbol_set_hash,
)
from ai_market_monitor.schemas.setup_agent import (
    FactualClaim,
    OperationExecutionResult,
    SetupAgentReply,
)
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ApprovalBindingV2,
    ConditionNodeType,
    StrategyDraftV2,
    StrategyPatch,
)
from ai_market_monitor.services.setup_chat_agent import (
    _rebuild_reply_from_validated_claims,
)
from ai_market_monitor.services.strategy_patch_extractor import deterministic_strategy_patch
from ai_market_monitor.services.watchlist_snapshot import (
    SNAPSHOT_PREFIX,
    WatchlistSnapshot,
    is_content_identity,
)

TURN = "turn-authority-1"
RULE = "Monitor BTC/USDT on the 15m when the candle rises open-to-close by at least 5%"

#: Every universe mode the platform offers. Tests parametrise across all of them so a
#: rule that only holds for explicit asset lists cannot pass.
ALL_UNIVERSE_MODES = tuple(ShariaUniverseMode)

#: Every claim type the composer can emit.
ALL_CLAIM_TYPES = (
    "mutation",
    "readiness",
    "approval",
    "condition_explanation",
    "universe",
    "provider",
    "product_fact",
    "open_item",
)

#: Every net effect an operation can end a turn with.
ALL_NET_EFFECTS = ("effective", "overwritten", "cancelled", "no_net_effect", "rejected")


def _draft(text: str = RULE) -> StrategyDraftV2:
    patch = deterministic_strategy_patch(StrategyDraftV2(), text, source_turn_id=TURN)
    assert patch is not None, text
    return apply_strategy_patch(StrategyDraftV2(), patch).draft


def _condition_ids(draft: StrategyDraftV2) -> list[str]:
    if draft.condition_ast is None:
        return []
    return [
        node.node_id
        for node in draft.condition_ast.walk()
        if node.node_type is ConditionNodeType.CONDITION
    ]


def _definition(symbols: list[str]) -> StrategyDefinition:
    draft = _draft()
    from ai_market_monitor.engine.strategy_compiler_v2 import compile_strategy_draft_v2

    definition = compile_strategy_draft_v2(draft)
    return definition.model_copy(
        update={
            "universe": definition.universe.model_copy(
                update={"include_symbols": symbols}
            )
        }
    )


# ---------------------------------------------------------------------------
# 1. The screened universe is the executed universe.
#    Enforced by `services/setup_chat_launch._apply_screening_policy` returning a
#    `ScreeningExecutionResult`, and by that class's own validator.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "permitted",
    [
        ["BTC/USDT"],
        ["BTC/USDT", "ETH/USDT"],
        ["ADA/USDT", "BTC/USDT", "ETH/USDT", "SOL/USDT"],
    ],
)
def test_screening_result_cannot_carry_a_universe_it_did_not_permit(
    permitted: list[str],
) -> None:
    """The secured definition must hold exactly the permitted markets — no more, no less."""

    result = ScreeningExecutionResult(
        secured_definition=_definition(permitted),
        resolved_at=datetime.now(UTC),
        included_symbols=permitted,
    )
    assert list(result.secured_definition.universe.include_symbols) == permitted

    for wrong in (
        [*permitted, "DOGE/USDT"],  # one extra market slipped through
        permitted[:-1] if len(permitted) > 1 else [],  # one silently dropped
    ):
        with pytest.raises(ValueError, match="exactly the symbols screening permitted"):
            ScreeningExecutionResult(
                secured_definition=_definition(wrong),
                resolved_at=datetime.now(UTC),
                included_symbols=permitted,
            )


def test_resolved_symbol_set_hash_ignores_order_but_not_membership() -> None:
    """A review must not be invalidated by the order a resolver happened to return."""

    assert symbol_set_hash(["BTC/USDT", "ETH/USDT"]) == symbol_set_hash(
        ["ETH/USDT", "BTC/USDT"]
    )
    assert symbol_set_hash(["btc/usdt", " BTC/USDT "]) == symbol_set_hash(["BTC/USDT"])
    assert symbol_set_hash(["BTC/USDT"]) != symbol_set_hash(["BTC/USDT", "ETH/USDT"])


# ---------------------------------------------------------------------------
# 2. The market-data check states which promise it is making.
#    Enforced by `schemas/screening_execution.PreflightManifest`.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "contract", ["verified_all", "policy_verified_runtime_fail_closed", "not_required"]
)
def test_every_preflight_contract_describes_itself_without_overclaiming(
    contract: str,
) -> None:
    """No contract may say "all" unless it really checked all of them."""

    manifest = PreflightManifest(
        contract=contract,  # type: ignore[arg-type]
        verified_pairs=["BTC/USDT@15m"],
        unverified_symbols=["ETH/USDT"],
        required_timeframes=["15m"],
        symbol_cap=25,
    )
    described = manifest.describe().casefold()
    assert described
    if contract != "verified_all":
        assert "all " not in described


def test_preflight_manifest_hash_moves_with_what_was_checked() -> None:
    """Two different checks cannot share one identity."""

    base = PreflightManifest(
        contract="verified_all",
        verified_pairs=["BTC/USDT@15m"],
        required_timeframes=["15m"],
        symbol_cap=25,
    )
    # Order is not part of the identity; content is.
    assert (
        base.model_copy(update={"verified_pairs": ["BTC/USDT@15m"]}).manifest_hash
        == base.manifest_hash
    )
    for changed in (
        {"contract": "policy_verified_runtime_fail_closed"},
        {"verified_pairs": ["BTC/USDT@15m", "ETH/USDT@15m"]},
        {"unverified_symbols": ["SOL/USDT"]},
        {"required_timeframes": ["15m", "1h"]},
        {"symbol_cap": 10},
    ):
        assert base.model_copy(update=changed).manifest_hash != base.manifest_hash, changed


# ---------------------------------------------------------------------------
# 3. Approval is bound to the screening evidence the user reviewed.
#    Enforced by `services/setup_chat_launch.revalidate_for_approval` comparing the
#    stored `ReviewedScreeningEvidence` against a freshly derived one.
# ---------------------------------------------------------------------------


def _evidence(**overrides: object) -> ReviewedScreeningEvidence:
    base: dict[str, object] = {
        "screening_snapshot_hash": "a" * 64,
        "screening_policy_hash": "b" * 64,
        "methodology_id": str(uuid4()),
        "methodology_version": "1.0.0",
        "resolved_symbol_set_hash": symbol_set_hash(["BTC/USDT"]),
        "watchlist_snapshot_hash": f"{SNAPSHOT_PREFIX}{'c' * 64}",
        "provider_preflight_manifest_hash": "d" * 64,
        "preflight_contract": "verified_all",
        "reviewed_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ReviewedScreeningEvidence(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize("field_name", ReviewedScreeningEvidence.BOUND_FIELDS)
def test_any_bound_screening_fact_that_moves_is_reported_as_changed(
    field_name: str,
) -> None:
    """Every bound fact, not just the symbol list. One test per field, no exceptions."""

    reviewed = _evidence()
    moved = {
        "preflight_contract": "policy_verified_runtime_fail_closed",
    }.get(field_name, "z" * 64)
    now = reviewed.model_copy(update={field_name: moved})

    changed = reviewed.differences_from(now)
    assert changed, f"{field_name} moved but was not reported"
    sentence = reviewed.describe_change(now)
    assert "changed since you reviewed" in sentence
    # A beginner has to be able to read it: no field names in the message.
    assert field_name not in sentence


def test_identical_screening_evidence_reports_no_change() -> None:
    reviewed = _evidence()
    assert reviewed.differences_from(reviewed.model_copy()) == []
    assert reviewed.describe_change(reviewed.model_copy()) == "Nothing changed."


def test_an_unapproved_draft_cannot_hold_screening_evidence() -> None:
    """Evidence is a fact about an approval, so it cannot exist without one."""

    with pytest.raises(ValueError, match="unapproved drafts cannot retain"):
        ApprovalBindingV2(approved=False, screening_evidence=_evidence())


def test_approved_binding_carries_the_reviewed_evidence() -> None:
    binding = ApprovalBindingV2(
        approved=True,
        user_id=uuid4(),
        executable_version=1,
        executable_hash="e" * 64,
        conversation_snapshot_hash="f" * 64,
        screening_evidence=_evidence(),
        approved_at=datetime.now(UTC),
    )
    assert binding.screening_evidence is not None
    assert binding.screening_evidence.resolved_symbol_set_hash == symbol_set_hash(
        ["BTC/USDT"]
    )


# ---------------------------------------------------------------------------
# 4. A Favorites list is identified by its contents, never by a timestamp.
#    Enforced by `services/watchlist_snapshot`.
# ---------------------------------------------------------------------------


def _snapshot(assets: list[str], *, name: str = "My list") -> WatchlistSnapshot:
    watchlist_id = uuid4()
    return WatchlistSnapshot(
        watchlist_id=watchlist_id,
        name=name,
        ordered_asset_ids=assets,
        market_symbols=[f"{item}/USDT" for item in assets],
        created_at=datetime.now(UTC),
    )


def test_watchlist_identity_follows_membership_not_the_row() -> None:
    first = _snapshot(["BTC", "ETH"])
    # Same members in a different order, a different name, a later moment: same list.
    same = first.model_copy(
        update={
            "ordered_asset_ids": ["ETH", "BTC"],
            "market_symbols": ["ETH/USDT", "BTC/USDT"],
            "name": "Renamed",
            "created_at": datetime.now(UTC),
        }
    )
    assert same.content_hash == first.content_hash

    for changed in (["BTC"], ["BTC", "ETH", "SOL"], ["BTC", "SOL"]):
        moved = first.model_copy(
            update={
                "ordered_asset_ids": changed,
                "market_symbols": [f"{item}/USDT" for item in changed],
            }
        )
        assert moved.content_hash != first.content_hash, changed


@pytest.mark.parametrize(
    "legacy",
    [
        "2026-07-30T12:00:00+00:00",
        "2026-07-30T12:00:00",
        "",
        None,
        "some-other-token",
    ],
)
def test_a_legacy_timestamp_is_never_accepted_as_content_identity(
    legacy: str | None,
) -> None:
    """Fail closed: an identity that cannot be compared counts as changed."""

    assert not is_content_identity(legacy)


def test_a_content_hash_is_recognised_as_content_identity() -> None:
    assert is_content_identity(_snapshot(["BTC"]).content_hash)


# ---------------------------------------------------------------------------
# 5. Evidence is reconciled against the final state of the turn.
#    Enforced by `engine/operation_reconciliation.reconcile_turn`.
# ---------------------------------------------------------------------------


def _operation_result(
    operation_id: str,
    kind: str,
    *,
    ids: list[str],
    applied: bool = True,
    rejected: bool = False,
) -> OperationExecutionResult:
    """One step's own before/after record. Reconciliation judges it against the end."""

    return OperationExecutionResult(
        operation_id=operation_id,
        authorizing_segment_id="s1",
        operation_kind=kind,
        applied=applied,
        rejected=rejected,
        # The per-step hashes are audit detail; reconciliation reads the turn-level diff.
        before_executable_hash="0" * 64,
        after_executable_hash="1" * 64,
        workflow_revision_before=1,
        workflow_revision_after=2,
        affected_condition_ids=ids,
    )


def test_a_rule_added_then_removed_in_one_turn_is_never_reported_as_added() -> None:
    """The classic case: the id went into `last_changed_condition_ids` and vanished.

    The turn under test is *only* the add and the remove, so the before-state already
    carries the universe the same message would have set. Otherwise an unrelated symbol
    change would be what moved the executable identity, and the test would prove nothing
    about the rule.
    """

    seeded = _draft()
    node_id = _condition_ids(seeded)[0]
    node = next(
        item
        for item in seeded.condition_ast.walk()  # type: ignore[union-attr]
        if item.node_id == node_id
    )
    before = apply_strategy_patch(
        seeded,
        StrategyPatch(source_turn_id=TURN, remove_conditions=[node_id]),
    ).draft
    assert _condition_ids(before) == []

    during = apply_strategy_patch(
        before,
        StrategyPatch(source_turn_id=TURN, add_conditions=[node]),
    ).draft
    after = apply_strategy_patch(
        during,
        StrategyPatch(source_turn_id=TURN, remove_conditions=[node_id]),
    ).draft

    reconciliation = reconcile_turn(
        before,
        after,
        [
            _operation_result("op-1", "add_condition", ids=[node_id]),
            _operation_result("op-2", "remove_condition", ids=[node_id]),
        ],
    )
    by_id = {item.operation_id: item for item in reconciliation.operations}
    assert by_id["op-1"].net_effect == "cancelled"
    assert not by_id["op-1"].is_effective
    # Nothing that no longer exists may become a reference the next turn resolves.
    assert node_id not in reconciliation.final_condition_ids
    # And an approval must not be invalidated by a change that undid itself.
    assert reconciliation.executable_changed is False
    assert reconciliation.net_changes == ()


def test_an_operation_that_never_applied_is_reported_as_rejected() -> None:
    draft = _draft()
    reconciliation = reconcile_turn(
        draft,
        draft,
        [_operation_result("op-1", "add_condition", ids=[], applied=False, rejected=True)],
    )
    assert reconciliation.operations[0].net_effect == "rejected"
    assert reconciliation.effective == ()


def test_a_surviving_change_is_reported_as_effective() -> None:
    before = StrategyDraftV2()
    after = _draft()
    node_id = _condition_ids(after)[0]
    reconciliation = reconcile_turn(
        before,
        after,
        [_operation_result("op-1", "add_condition", ids=[node_id])],
    )
    assert reconciliation.operations[0].net_effect == "effective"
    assert node_id in reconciliation.final_condition_ids
    assert reconciliation.executable_changed is True


# ---------------------------------------------------------------------------
# 6. An unsupported requirement can only be cleared on purpose.
#    Enforced by `engine/strategy_draft_v2.apply_strategy_patch`, which no longer sweeps
#    unsupported items away when the same turn happens to touch a condition.
# ---------------------------------------------------------------------------


def test_editing_a_rule_does_not_clear_an_unsupported_requirement() -> None:
    from ai_market_monitor.schemas.strategy_draft_v2 import UnsupportedRequirementV2

    draft = apply_strategy_patch(
        _draft(),
        StrategyPatch(
            source_turn_id=TURN,
            unsupported_requirements=[
                UnsupportedRequirementV2(
                    key="order-book-depth",
                    source_turn_id=TURN,
                    source_fragment="watch the order book depth",
                    missing_contract="Order book depth is not available.",
                )
            ],
        ),
    ).draft
    assert len(draft.unsupported_requirements) == 1

    node_id = _condition_ids(draft)[0]
    node = next(
        item
        for item in draft.condition_ast.walk()  # type: ignore[union-attr]
        if item.node_id == node_id
    )
    from ai_market_monitor.schemas.strategy_draft_v2 import ConditionUpdateV2

    edited = apply_strategy_patch(
        draft,
        StrategyPatch(
            source_turn_id=TURN,
            update_conditions=[
                ConditionUpdateV2(
                    node_id=node_id,
                    replacement=node.model_copy(update={"threshold": 7.0}),
                )
            ],
        ),
    ).draft
    assert len(edited.unsupported_requirements) == 1, (
        "editing a rule must not clear a blocker nothing proved was resolved"
    )

    cleared = apply_strategy_patch(
        edited,
        StrategyPatch(source_turn_id=TURN, remove_unsupported_keys=["order-book-depth"]),
    ).draft
    assert cleared.unsupported_requirements == []


# ---------------------------------------------------------------------------
# 8. A number's role must be grounded, not only its value.
#    Enforced by `engine/parameter_roles.role_grounding_errors`, wired into
#    `engine/capability_contract._parameter_errors`.
# ---------------------------------------------------------------------------


_TWO_COUNT_SCHEMA = {
    "period": {
        "type": "integer",
        "x-semantic-unit": "count",
        "x-source-aliases": ["period", "length"],
    },
    "confirmation_candles": {
        "type": "integer",
        "x-semantic-unit": "count",
        "x-source-aliases": ["confirmation", "candles", "in a row"],
    },
}


@pytest.mark.parametrize(
    ("text", "supplied", "expect_error"),
    [
        # The role is named next to each number: accepted.
        (
            "RSI period 14 with confirmation over 3 candles",
            {"period": 14, "confirmation_candles": 3},
            False,
        ),
        # The same two numbers, swapped. Both values are still in the text, so value
        # grounding alone accepts it. Role grounding must not.
        (
            "RSI period 14 with confirmation over 3 candles",
            {"period": 3, "confirmation_candles": 14},
            True,
        ),
        # Attached grammar binds the number without a separate phrase.
        ("a 14-period RSI confirmed for 3 candles", {"period": 14}, False),
        # A number that is simply not in the text at all.
        ("RSI period 14", {"period": 14, "confirmation_candles": 9}, True),
    ],
)
def test_two_parameters_of_one_unit_cannot_swap_roles(
    text: str,
    supplied: dict[str, int],
    expect_error: bool,
) -> None:
    errors = role_grounding_errors(
        node_id="n1",
        supplied=supplied,
        defaults={},
        parameter_schema=_TWO_COUNT_SCHEMA,
        authorizing_text=text,
    )
    assert bool(errors) is expect_error, errors


def test_a_registry_default_the_trader_never_changed_needs_no_role_evidence() -> None:
    """They did not choose it, so there is nothing of theirs to find."""

    assert (
        role_grounding_errors(
            node_id="n1",
            supplied={"period": 14, "confirmation_candles": 1},
            defaults={"period": 14, "confirmation_candles": 1},
            parameter_schema=_TWO_COUNT_SCHEMA,
            authorizing_text="use RSI",
        )
        == []
    )


def test_a_lone_parameter_of_its_unit_is_left_to_value_grounding() -> None:
    """Nothing else could claim the number, so demanding a role phrase adds nothing."""

    assert (
        role_grounding_errors(
            node_id="n1",
            supplied={"period": 14},
            defaults={},
            parameter_schema={"period": {"type": "integer", "x-semantic-unit": "count"}},
            authorizing_text="RSI over 14",
        )
        == []
    )


def test_requires_role_phrase_forces_evidence_even_when_alone() -> None:
    errors = role_grounding_errors(
        node_id="n1",
        supplied={"period": 14},
        defaults={},
        parameter_schema={
            "period": {
                "type": "integer",
                "x-semantic-unit": "count",
                "x-requires-role-phrase": True,
            }
        },
        authorizing_text="use 14 somewhere",
    )
    assert errors == ["n1:parameter_role_not_grounded:period"]


def test_role_specs_read_every_declared_field_from_the_registry() -> None:
    specs = role_specs(_TWO_COUNT_SCHEMA)
    assert specs["period"].semantic_unit == "count"
    assert "length" in specs["period"].source_aliases
    assert specs["confirmation_candles"].requires_role_phrase is False
    # Longest first, so the specific phrase wins over a prefix of it.
    phrases = specs["confirmation_candles"].phrases
    assert list(phrases) == sorted(phrases, key=len, reverse=True)


def test_ambiguous_role_pairs_names_every_same_unit_pair() -> None:
    assert ambiguous_role_pairs(
        supplied={"period": 14, "confirmation_candles": 3},
        parameter_schema=_TWO_COUNT_SCHEMA,
    ) == [("confirmation_candles", "period")]


def test_the_shipped_registry_declares_role_metadata_for_every_parameter() -> None:
    """Metadata is not optional: a capability without it cannot be role-checked."""

    from ai_market_monitor.engine.capabilities import all_capabilities

    checked = 0
    for spec in all_capabilities():
        properties = (spec.parameter_schema or {}).get("properties") or {}
        for parameter in spec.parameters:
            rules = properties.get(parameter.name)
            assert isinstance(rules, dict), f"{spec.key}.{parameter.name}"
            assert rules.get("x-semantic-unit"), f"{spec.key}.{parameter.name}"
            assert "x-source-aliases" in rules, f"{spec.key}.{parameter.name}"
            checked += 1
    assert checked > 0


def test_role_spec_phrases_always_include_the_parameter_name() -> None:
    spec = ParameterRoleSpec(name="signal_period")
    assert "signal period" in spec.phrases
    assert "signal_period" in spec.phrases


# ---------------------------------------------------------------------------
# 9. A factual claim is checked by looking up ids, not by reading English.
#    Enforced by `engine/claim_evidence.validate_claims` and
#    `services/setup_chat_agent._rebuild_reply_from_validated_claims`.
# ---------------------------------------------------------------------------


def _ledger(**overrides: object) -> EvidenceLedger:
    execution: dict[str, object] = {
        "compile_status": "compiled",
        "screening_status": "passed",
        "provider_status": "available",
        "approval_eligible": True,
        "final_chat_status": "ready_for_approval",
        "approval_status": "not_requested",
        "unresolved_fields": [],
        "unsupported_requirements": [],
    }
    execution.update(overrides)
    return build_evidence_ledger(
        reconciled_operations=[
            {"operation_id": "op-1", "net_effect": "effective", "summary": "Added a rule."},
            {"operation_id": "op-2", "net_effect": "cancelled", "summary": "Undone."},
        ],
        execution=execution,
        draft_read_model={
            "conditions": [{"condition_id": "c1"}],
            "included_symbols": ["BTC/USDT"],
            "excluded_symbols": [],
        },
        screening_evidence={"resolved_symbol_set_hash": "a" * 64},
        preflight_evidence={"manifest_hash": "b" * 64, "contract": "verified_all"},
        product_knowledge={"pricing": "free tier available"},
    )


@pytest.mark.parametrize("claim_type", ALL_CLAIM_TYPES)
def test_no_claim_type_is_accepted_without_evidence(claim_type: str) -> None:
    """Every type, individually. A type that skipped the check would be invisible."""

    validated = validate_claims(
        [FactualClaim(claim_type=claim_type, text="something happened", evidence_ids=[])],
        _ledger(),
    )
    assert validated[0].accepted is False
    assert validated[0].reason == "no evidence cited"


@pytest.mark.parametrize("claim_type", ALL_CLAIM_TYPES)
def test_no_claim_type_is_accepted_on_an_invented_evidence_id(claim_type: str) -> None:
    validated = validate_claims(
        [
            FactualClaim(
                claim_type=claim_type,
                text="something happened",
                evidence_ids=["operation:does-not-exist"],
            )
        ],
        _ledger(),
    )
    assert validated[0].accepted is False


def test_a_mutation_claim_cannot_cite_an_operation_the_turn_undid() -> None:
    """`op-2` was cancelled, so it has no id and cannot be claimed."""

    ledger = _ledger()
    assert ledger.has("operation:op-1")
    assert not ledger.has("operation:op-2")

    validated = validate_claims(
        [
            FactualClaim(
                claim_type="mutation",
                text="I removed that rule for you.",
                evidence_ids=["operation:op-2"],
            )
        ],
        ledger,
    )
    assert validated[0].accepted is False


def test_a_readiness_claim_must_cite_every_gate() -> None:
    """Citing the one gate that suits the sentence used to be enough."""

    ledger = _ledger()
    partial = validate_claims(
        [
            FactualClaim(
                claim_type="readiness",
                text="This is ready.",
                evidence_ids=["status:compile"],
            )
        ],
        ledger,
    )
    assert partial[0].accepted is False
    assert "every gate" in (partial[0].reason or "")

    complete = validate_claims(
        [
            FactualClaim(
                claim_type="readiness",
                text="This is ready.",
                evidence_ids=[
                    "status:compile",
                    "status:screening",
                    "status:provider",
                    "status:approval_eligible",
                ],
            )
        ],
        ledger,
    )
    assert complete[0].accepted is True


def test_a_readiness_claim_is_refused_when_the_draft_is_not_eligible() -> None:
    ledger = _ledger(approval_eligible=False)
    validated = validate_claims(
        [
            FactualClaim(
                claim_type="readiness",
                text="This is ready.",
                evidence_ids=[
                    "status:compile",
                    "status:screening",
                    "status:provider",
                    "status:approval_eligible",
                ],
            )
        ],
        ledger,
    )
    assert validated[0].accepted is False


@pytest.mark.parametrize(
    ("claim_type", "wrong_id"),
    [
        ("mutation", "product:pricing"),
        ("readiness", "product:pricing"),
        ("approval", "condition:c1"),
        ("condition_explanation", "product:pricing"),
        ("universe", "product:pricing"),
        ("provider", "condition:c1"),
        ("product_fact", "condition:c1"),
        ("open_item", "product:pricing"),
    ],
)
def test_a_claim_cannot_rest_on_the_wrong_family_of_evidence(
    claim_type: str,
    wrong_id: str,
) -> None:
    validated = validate_claims(
        [FactualClaim(claim_type=claim_type, text="x", evidence_ids=[wrong_id])],
        _ledger(),
    )
    assert validated[0].accepted is False


@pytest.mark.parametrize(
    "text",
    [
        "I added the rule and it is ready to approve.",
        "تمت إضافة القاعدة وهي جاهزة للموافقة.",
        "Ana 3amalt el rule w heya ready.",
        "已添加规则，现在可以批准了。",
    ],
)
def test_an_unsupported_claim_is_refused_in_every_language(text: str) -> None:
    """The point of the whole design: the check reads ids, so language is irrelevant."""

    validated = validate_claims(
        [FactualClaim(claim_type="mutation", text=text, evidence_ids=[])],
        _ledger(),
    )
    assert validated[0].accepted is False


@pytest.mark.parametrize(
    "text",
    [
        "I added the rule for you.",
        "تمت إضافة القاعدة.",
        "已添加规则。",
    ],
)
def test_a_refused_claim_is_replaced_not_silently_dropped(text: str) -> None:
    """The user still learns what happened, from evidence rather than from wording."""

    reply = SetupAgentReply(
        message_without_question=f"Okay. {text}",
        conversational_text="Okay.",
        factual_claims=[
            FactualClaim(claim_type="mutation", text=text, evidence_ids=["operation:op-2"])
        ],
    )
    rebuilt = _rebuild_reply_from_validated_claims(reply, _ledger())
    assert text not in rebuilt.message_without_question
    assert rebuilt.factual_claims == []
    assert rebuilt.message_without_question.strip()
    # The deterministic replacement carries the real fact.
    assert "Added a rule." in rebuilt.message_without_question


def test_a_supported_claim_survives_untouched() -> None:
    reply = SetupAgentReply(
        message_without_question="Okay. I added the rule for you.",
        conversational_text="Okay.",
        factual_claims=[
            FactualClaim(
                claim_type="mutation",
                text="I added the rule for you.",
                evidence_ids=["operation:op-1"],
            )
        ],
    )
    rebuilt = _rebuild_reply_from_validated_claims(reply, _ledger())
    assert rebuilt.message_without_question == reply.message_without_question
    assert len(rebuilt.factual_claims) == 1


def test_a_reply_with_no_structured_claims_is_left_alone() -> None:
    """An older composer keeps working; this validation only ever removes."""

    reply = SetupAgentReply(message_without_question="Okay, understood.")
    assert _rebuild_reply_from_validated_claims(reply, _ledger()) is reply


def test_deterministic_text_reports_only_operations_that_survived() -> None:
    lines = deterministic_claim_text(_ledger())
    assert "Added a rule." in lines
    assert "Undone." not in lines


# ---------------------------------------------------------------------------
# 11. No published database password may boot a deployment.
#     Enforced by `core/startup._weak_database_password`.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "password",
    [
        "postgres",
        "password",
        "secret",
        "admin",
        "root",
        "market_monitor",
        "monitor",
        "short",  # under twelve characters
        "",
    ],
)
def test_a_weak_database_password_is_refused_in_deployment(password: str) -> None:
    url = f"postgresql+asyncpg://market_monitor:{password}@db:5432/market_monitor"
    assert _weak_database_password(url) is not None, password


def test_a_password_equal_to_the_username_is_refused() -> None:
    url = "postgresql+asyncpg://hilalmarkets:hilalmarkets@db:5432/market_monitor"
    reason = _weak_database_password(url)
    assert reason is not None
    assert "identical to the database user" in reason


def test_a_strong_distinct_password_is_accepted() -> None:
    secret = hashlib.sha256(b"a real deployment secret").hexdigest()[:32]
    url = f"postgresql+asyncpg://market_monitor:{secret}@db:5432/market_monitor"
    assert _weak_database_password(url) is None


def test_a_url_with_no_password_at_all_is_not_flagged_here() -> None:
    """Socket and IAM authentication carry no password; other checks cover those."""

    assert _weak_database_password("postgresql+asyncpg://db:5432/market_monitor") is None


# ---------------------------------------------------------------------------
# 9 (end to end). The whole composer path, with the real agent and a scripted model.
#     The one network call is faked; the payload builder, the ledger, validation and the
#     rebuild are all the production code.
# ---------------------------------------------------------------------------


def _agent_settings():
    from pydantic import SecretStr

    from ai_market_monitor.core.config import Settings

    return Settings(
        _env_file=None,
        app_env="test",
        app_secret_key="setup-agent-secret-with-at-least-32-characters",
        openai_api_key=SecretStr("test-key"),
        sharia_screening_enforced=False,
        setup_agent_max_estimated_cost_usd_per_turn=5,
    )


def _responses_body(text: str) -> dict[str, object]:
    return {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 20, "output_tokens": 8},
    }


@pytest.mark.parametrize(
    ("claimed_ids", "claim_text", "must_survive"),
    [
        # An id that exists and fits the claim: the wording is kept.
        (["operation:__effective__"], "I added that rule for you.", True),
        # An id that does not exist at all.
        (["operation:invented"], "I added that rule for you.", False),
        # No evidence cited.
        ([], "I added that rule for you.", False),
        # Right shape, wrong family.
        (["product:approval"], "I added that rule for you.", False),
    ],
)
async def test_the_real_composer_path_keeps_only_evidence_backed_wording(
    claimed_ids: list[str],
    claim_text: str,
    must_survive: bool,
) -> None:
    """End to end through `SetupChatAgent.run_turn`, with the model scripted."""

    import json as _json

    import httpx

    from ai_market_monitor.schemas.setup_agent import (
        ResponseDirective,
        SegmentKind,
        SetupAgentPlanEnvelope,
        SetupAgentTurnPlan,
        StrategyInstructionPlan,
        TurnSegment,
    )
    from ai_market_monitor.services.setup_chat_agent import (
        SetupAgentTurnInput,
        SetupChatAgent,
    )
    from tests.support.setup_agent_plans import operations_from_patch

    message = RULE
    patch = deterministic_strategy_patch(StrategyDraftV2(), message, source_turn_id=TURN)
    assert patch is not None
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN,
        segments=[
            TurnSegment(
                segment_id="s1",
                exact_source_text=message,
                start_offset=0,
                end_offset=len(message),
                kind=SegmentKind.STRATEGY_INSTRUCTION,
                action_required=True,
                confidence=0.95,
            )
        ],
        operations=operations_from_patch(patch, segment_id="s1"),
        strategy_instructions=[
            StrategyInstructionPlan(segment_id="s1", intent_summary="one rule")
        ],
        # A question to answer routes the turn to the contextual composer rather than the
        # evidence-only summary, so the path under test actually runs.
        questions_to_answer=["What did that change?"],
        response_points=[
            ResponseDirective(point="Explain the rule that was added.", kind="explain_change")
        ],
        overall_confidence=0.95,
    )

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content)
        name = body["text"]["format"]["name"]
        payload = _json.loads(body["input"])
        if name == "hilalmarkets_setup_turn_plan":
            return httpx.Response(
                200,
                json=_responses_body(
                    SetupAgentPlanEnvelope(plan=plan).model_dump_json()
                ),
            )
        seen["composer_payload"] = payload
        citable = list(payload.get("citable_evidence_ids") or [])
        # Resolve the placeholder to whichever operation really survived this turn.
        effective = next(
            (item for item in citable if item.startswith("operation:")),
            "operation:none",
        )
        ids = [effective if item == "operation:__effective__" else item for item in claimed_ids]
        return httpx.Response(
            200,
            json=_responses_body(
                _json.dumps(
                    {
                        "message_without_question": f"Okay. {claim_text}",
                        "conversational_text": "Okay.",
                        "factual_claims": [
                            {
                                "claim_type": "mutation",
                                "text": claim_text,
                                "evidence_ids": ids,
                            }
                        ],
                        "selected_clarification_id": None,
                    }
                )
            ),
        )

    agent = SetupChatAgent(_agent_settings(), transport=httpx.MockTransport(handler))
    result = await agent.run_turn(
        SetupAgentTurnInput(
            message=message,
            source_turn_id=TURN,
            draft=StrategyDraftV2(),
        )
    )

    payload = seen["composer_payload"]
    assert isinstance(payload, dict)
    # The composer is told exactly what it may cite, and given net effects, not steps.
    assert payload["citable_evidence_ids"], "the composer was given nothing to cite"
    assert "net_effect_of_each_operation" in payload

    if must_survive:
        assert claim_text in result.reply.message_without_question
        assert len(result.reply.factual_claims) == 1
    else:
        assert claim_text not in result.reply.message_without_question
        assert result.reply.factual_claims == []
    # Whatever happened to the claim, the user is never left with an empty answer.
    assert result.reply.message_without_question.strip()


def test_no_runtime_database_or_bytecode_is_tracked_in_version_control() -> None:
    """A public repository must not ship user data or generated files."""

    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    databases = [item for item in tracked if item.endswith((".db", ".sqlite", ".sqlite3"))]
    bytecode = [item for item in tracked if item.endswith(".pyc") or "__pycache__" in item]
    real_env = [
        item
        for item in tracked
        if item.split("/")[-1].startswith(".env") and not item.endswith(".example")
    ]
    assert databases == [], databases
    assert bytecode == [], bytecode[:10]
    assert real_env == [], real_env
