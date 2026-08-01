"""The dynamic-universe, preflight-evidence and response-proof invariants.

Each test names the invariant it proves and the production function that enforces it.
They assert a *rule* across a family вЂ” every universe mode, every contract, every claim
type, every operation kind вЂ” so a fix that only helps one reported example fails here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from ai_market_monitor.core.plans import (
    COMING_SOON_LABEL,
    PLAN_DEFINITIONS,
    PROMOTION_ENDS_AT,
    PUBLIC_PLAN_CODES,
    effective_monthly_price,
    original_monthly_price,
    plan_offer,
    plan_offer_payload,
    promotion_is_active,
    visible_plan_comparison,
    visible_plan_comparison_headers,
    visible_public_plan_codes,
)
from ai_market_monitor.core.universe_membership import (
    MembershipContract,
    is_dynamic_membership,
    membership_contract,
    membership_source_symbols,
)
from ai_market_monitor.db.models.enums import ShariaUniverseMode
from ai_market_monitor.engine.claim_evidence import (
    build_evidence_ledger,
    requires_factual_answer,
    validate_claims,
)
from ai_market_monitor.engine.draft_diff import diff_drafts
from ai_market_monitor.engine.operation_reconciliation import reconcile_turn
from ai_market_monitor.engine.operation_target import (
    OperationTarget,
    operation_targets,
    targets_by_operation_id,
    unsupported_key_for,
)
from ai_market_monitor.engine.runtime_preflight import (
    CONTRACT_INCOMPLETE,
    UNUSABLE_DATA,
    RuntimeDataContract,
    skip_reason,
)
from ai_market_monitor.engine.strategy_draft_v2 import apply_strategy_patch
from ai_market_monitor.schemas.preflight_cache import PreflightCacheEntry
from ai_market_monitor.schemas.screening_execution import (
    CompiledAuthoredDefinition,
    PreflightManifest,
    ReviewedScreeningEvidence,
    ScreeningExecutionResult,
    SecuredPreviewDefinition,
    symbol_set_hash,
)
from ai_market_monitor.schemas.setup_agent import (
    FactualClaim,
    OperationExecutionResult,
)
from ai_market_monitor.schemas.setup_authorization import AuthorizedPatchOperation
from ai_market_monitor.schemas.strategy import ShariaPolicyDefinition, StrategyDefinition
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeType,
    DraftFieldPatch,
    ProviderRuntimeStatusV2,
    ShariaPolicyV2,
    StrategyDraftV2,
    StrategyPatch,
    UnresolvedFieldV2,
)
from ai_market_monitor.services.strategy_patch_extractor import deterministic_strategy_patch

TURN = "turn-universe-1"
RULE = "Monitor BTC/USDT on the 15m when the candle rises open-to-close by at least 5%"

#: Every universe mode. Nothing below is written for one of them.
ALL_MODES = tuple(ShariaUniverseMode)

#: Every promise the market-data check can make.
ALL_CONTRACTS = ("verified_all", "policy_verified_runtime_fail_closed", "not_required")

#: Every operation kind the authorized tool accepts.
ALL_OPERATION_KINDS = (
    "set_fields",
    "set_sharia_policy",
    "add_condition",
    "update_condition",
    "remove_condition",
    "replace_groups",
    "add_inclusion",
    "add_exclusion",
    "remove_inclusion",
    "remove_exclusion",
    "add_unresolved",
    "update_unresolved",
    "add_unsupported",
    "resolve_unresolved_key",
    "remove_unsupported_key",
    "restore_snapshot",
)


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


def _definition(
    symbols: list[str],
    *,
    mode: ShariaUniverseMode = ShariaUniverseMode.ELIGIBLE_MARKET,
) -> StrategyDefinition:
    from ai_market_monitor.engine.strategy_compiler_v2 import compile_strategy_draft_v2

    definition = compile_strategy_draft_v2(_draft())
    policy = definition.universe.sharia_policy or ShariaPolicyDefinition()
    return definition.model_copy(
        update={
            "universe": definition.universe.model_copy(
                update={
                    "include_symbols": symbols,
                    "sharia_policy": policy.model_copy(
                        update={
                            "universe_mode": mode,
                            "approved_watchlist_id": (
                                uuid4()
                                if mode is ShariaUniverseMode.APPROVED_WATCHLIST
                                else None
                            ),
                        }
                    ),
                }
            )
        }
    )


def _operation_result(
    operation_id: str,
    kind: str,
    *,
    ids: list[str] | None = None,
    applied: bool = True,
    rejected: bool = False,
) -> OperationExecutionResult:
    return OperationExecutionResult(
        operation_id=operation_id,
        authorizing_segment_id="s1",
        operation_kind=kind,
        applied=applied,
        rejected=rejected,
        before_executable_hash="0" * 64,
        after_executable_hash="1" * 64,
        workflow_revision_before=1,
        workflow_revision_after=2,
        affected_condition_ids=list(ids or []),
    )


# ---------------------------------------------------------------------------
# 1. Cached statuses and their manifest are always one atomic result.
#    Enforced by `schemas/preflight_cache.PreflightCacheEntry` and
#    `services/setup_chat_launch.SetupChatLaunchService._read_preflight_cache`.
# ---------------------------------------------------------------------------


def _entry(**overrides: object) -> PreflightCacheEntry:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "definition_identity": "a" * 64,
        "statuses": [
            ProviderRuntimeStatusV2(
                provider="Fixture",
                capability="market:BTC/USDT:15m",
                status="available",
                checked_at=now,
            )
        ],
        "manifest": PreflightManifest(
            contract="verified_all",
            verified_pairs=["BTC/USDT@15m"],
            required_timeframes=["15m"],
            symbol_cap=25,
        ),
        "cached_at": now,
        "expires_at": now + timedelta(seconds=120),
    }
    base.update(overrides)
    return PreflightCacheEntry(**base)  # type: ignore[arg-type]


def test_a_cache_entry_is_accepted_only_when_every_part_holds() -> None:
    """The whole entry, or none of it. There is no half-usable data check."""

    now = datetime.now(UTC)
    good = _entry()
    assert good.matches("a" * 64)
    assert good.is_fresh(now)
    assert good.statuses_are_fresh(now, ttl_seconds=120)
    assert good.manifest_is_intact()


@pytest.mark.parametrize(
    ("overrides", "failing_check"),
    [
        ({"definition_identity": "b" * 64}, "identity"),
        ({"expires_at": datetime.now(UTC) - timedelta(seconds=1)}, "expiry"),
        ({"manifest": None}, "manifest"),
        (
            {
                "manifest": PreflightManifest(
                    contract="verified_all",
                    verified_pairs=[],
                    unverified_symbols=[],
                )
            },
            "manifest",
        ),
        (
            {
                "statuses": [
                    ProviderRuntimeStatusV2(
                        provider="Fixture",
                        capability="market:BTC/USDT:15m",
                        status="available",
                        checked_at=None,
                    )
                ]
            },
            "statuses",
        ),
        (
            {
                "statuses": [
                    ProviderRuntimeStatusV2(
                        provider="Fixture",
                        capability="market:BTC/USDT:15m",
                        status="available",
                        checked_at=datetime.now(UTC) - timedelta(seconds=600),
                    )
                ]
            },
            "statuses",
        ),
    ],
)
def test_every_way_a_cache_entry_can_be_wrong_is_rejected(
    overrides: dict[str, object],
    failing_check: str,
) -> None:
    """A cache hit must never produce availability without its own evidence."""

    now = datetime.now(UTC)
    entry = _entry(**overrides)
    checks = {
        "identity": entry.matches("a" * 64),
        "expiry": entry.is_fresh(now),
        "statuses": entry.statuses_are_fresh(now, ttl_seconds=120),
        "manifest": entry.manifest_is_intact(),
    }
    assert checks[failing_check] is False, overrides


def test_a_cache_entry_round_trips_its_manifest_exactly() -> None:
    """The restored manifest has to hash to what was stored, or it proves nothing."""

    entry = _entry()
    restored = PreflightCacheEntry.model_validate_json(entry.model_dump_json())
    assert restored.manifest is not None
    assert entry.manifest is not None
    assert restored.manifest.manifest_hash == entry.manifest.manifest_hash


def test_a_verified_all_manifest_must_really_cover_every_pair() -> None:
    """Enforced at approval by `revalidate_for_approval` calling `manifest.covers`."""

    manifest = PreflightManifest(
        contract="verified_all",
        verified_pairs=["BTC/USDT@15m", "ETH/USDT@15m"],
        required_timeframes=["15m"],
    )
    assert manifest.covers(["BTC/USDT", "ETH/USDT"])
    assert not manifest.covers(["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    # A sample promise makes no per-symbol claim, so it cannot be incomplete this way.
    sampled = manifest.model_copy(update={"contract": "policy_verified_runtime_fail_closed"})
    assert sampled.covers(["BTC/USDT", "ETH/USDT", "SOL/USDT"])


# ---------------------------------------------------------------------------
# 2, 3, 4. Authored policy, reviewed resolution and runtime resolution are three
#    different things, and each mode's membership rule is written down once.
#    Enforced by `core/universe_membership` and
#    `services/sharia_universe.ShariaUniverseResolver._technical_symbols`.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ALL_MODES)
def test_every_mode_has_exactly_one_membership_contract(
    mode: ShariaUniverseMode,
) -> None:
    contract = membership_contract(mode)
    assert isinstance(contract, MembershipContract)
    assert contract.mode is mode
    assert contract.approval_sentence.strip()
    # The sentence is for a beginner: no field names, no internal vocabulary.
    assert "universe_mode" not in contract.approval_sentence
    assert "include_symbols" not in contract.approval_sentence


def test_only_the_eligible_market_universe_is_dynamic() -> None:
    """A Favorites list is *editable*, which is not the same as dynamic.

    Calling it dynamic told the user their approval covered changes it does not cover.
    """

    assert is_dynamic_membership(ShariaUniverseMode.ELIGIBLE_MARKET) is True
    assert is_dynamic_membership(ShariaUniverseMode.APPROVED_WATCHLIST) is False
    assert is_dynamic_membership(ShariaUniverseMode.EXPLICIT_ASSETS) is False


@pytest.mark.parametrize("value", [None, "", "not_a_mode", "ELIGIBLE_MARKET "])
def test_an_unknown_membership_mode_fails_closed_to_fixed(value: object) -> None:
    """A mode nobody recognises must never be treated as "runtime may add markets"."""

    contract = membership_contract(value)  # type: ignore[arg-type]
    assert contract.dynamic is False
    assert contract.runtime_may_add is False


@pytest.mark.parametrize("mode", ALL_MODES)
def test_only_explicit_assets_takes_membership_from_include_symbols(
    mode: ShariaUniverseMode,
) -> None:
    """This is the rule that stops a dynamic universe being frozen.

    `_technical_symbols` reads `include_symbols` only when this returns a list. Returning
    `None` for the other two modes is what makes them re-resolve from the exchange or the
    Favorites list every time.
    """

    answer = membership_source_symbols(mode, authored_include_symbols=["BTC/USDT"])
    if mode is ShariaUniverseMode.EXPLICIT_ASSETS:
        assert answer == ["BTC/USDT"]
    else:
        assert answer is None


@pytest.mark.parametrize("mode", ALL_MODES)
def test_a_resolution_never_overwrites_the_authored_policy(
    mode: ShariaUniverseMode,
) -> None:
    """The exact defect: writing resolved symbols into the authored universe.

    An `eligible_market` monitor whose `include_symbols` were overwritten would be pinned
    to the assets eligible on the day it was approved.
    """

    authored = _definition([], mode=mode)
    permitted = ["BTC/USDT", "ETH/USDT"]
    result = ScreeningExecutionResult(
        authored_definition=authored,
        resolved_at=datetime.now(UTC),
        included_symbols=permitted,
    )
    assert list(result.authored_definition.universe.include_symbols) == []
    assert list(result.preflight_definition.universe.include_symbols) == permitted
    assert result.authored_definition.universe.sharia_policy is not None
    assert (
        result.authored_definition.universe.sharia_policy.universe_mode is mode
    ), "the policy the runtime re-resolves from must survive untouched"


@pytest.mark.parametrize("mode", ALL_MODES)
def test_the_two_definition_identities_are_separate_and_both_bound(
    mode: ShariaUniverseMode,
) -> None:
    """Approval compares the authored hash *and* the secured preview hash."""

    authored = _definition([], mode=mode)
    compiled = CompiledAuthoredDefinition(definition=authored)
    first = SecuredPreviewDefinition(
        authored=compiled,
        resolved_symbols=["BTC/USDT"],
        membership_kind=compiled.membership_kind,
    )
    second = SecuredPreviewDefinition(
        authored=compiled,
        resolved_symbols=["BTC/USDT", "ETH/USDT"],
        membership_kind=compiled.membership_kind,
    )
    # Same rules, different markets: the authored identity holds, the preview moves.
    assert first.authored_schema_hash == second.authored_schema_hash
    assert first.secured_preview_hash != second.secured_preview_hash


def test_the_preflight_copy_is_a_copy_and_is_never_persisted() -> None:
    """It carries the resolved markets for one data check and nothing else."""

    authored = _definition([], mode=ShariaUniverseMode.ELIGIBLE_MARKET)
    secured = SecuredPreviewDefinition(
        authored=CompiledAuthoredDefinition(definition=authored),
        resolved_symbols=["BTC/USDT"],
        membership_kind="governed_dynamic",
    )
    copy = secured.for_preflight()
    assert list(copy.universe.include_symbols) == ["BTC/USDT"]
    assert list(authored.universe.include_symbols) == []
    assert copy.canonical_hash() != authored.canonical_hash()


# ---------------------------------------------------------------------------
# 5, 7. The runtime keeps the market-data promise, one market at a time.
#    Enforced by `engine/runtime_preflight.skip_reason`, called from
#    `services/scanner.ScanRunner._evaluate_symbol`.
# ---------------------------------------------------------------------------


def _contract(**overrides: object) -> RuntimeDataContract:
    payload = {
        "preflight_manifest": {
            "contract": "verified_all",
            "verified_pairs": ["BTC/USDT@15m"],
            "required_timeframes": ["15m"],
            **overrides,
        }
    }
    return RuntimeDataContract.from_approval_evidence(payload)


@pytest.mark.parametrize("contract_name", ALL_CONTRACTS)
def test_unusable_data_skips_the_market_under_every_contract(
    contract_name: str,
) -> None:
    """Never evaluate on guessed, substituted or partial candles. No exceptions."""

    contract = _contract(contract=contract_name)
    reason = skip_reason(
        contract,
        symbol="BTC/USDT",
        timeframes=["15m"],
        data_is_usable=False,
        data_reason="candle_history_incomplete",
    )
    assert reason == "candle_history_incomplete"

    # And with no reason supplied, it still refuses rather than proceeding.
    assert (
        skip_reason(
            contract,
            symbol="BTC/USDT",
            timeframes=["15m"],
            data_is_usable=False,
        )
        == UNUSABLE_DATA
    )


def test_a_market_outside_a_verified_all_promise_is_refused() -> None:
    """The universe grew after approval, so the recorded promise no longer holds."""

    contract = _contract()
    assert (
        skip_reason(
            contract,
            symbol="BTC/USDT",
            timeframes=["15m"],
            data_is_usable=True,
        )
        is None
    )
    assert (
        skip_reason(
            contract,
            symbol="SOL/USDT",
            timeframes=["15m"],
            data_is_usable=True,
        )
        == CONTRACT_INCOMPLETE
    )


def test_a_sampled_promise_lets_an_unverified_market_run_once_its_data_checks_out() -> None:
    """That is what "checked again when it runs" means, and it is the only way in."""

    contract = _contract(contract="policy_verified_runtime_fail_closed")
    assert (
        skip_reason(
            contract,
            symbol="SOL/USDT",
            timeframes=["15m"],
            data_is_usable=True,
        )
        is None
    )
    assert (
        skip_reason(
            contract,
            symbol="SOL/USDT",
            timeframes=["15m"],
            data_is_usable=False,
        )
        == UNUSABLE_DATA
    )


@pytest.mark.parametrize("payload", [None, {}, {"preflight_manifest": {}}, {"x": 1}])
def test_a_missing_market_data_record_never_reads_as_everything_checked(
    payload: dict[str, object] | None,
) -> None:
    """An older approved version predates the record. "Unknown" is not "fine"."""

    contract = RuntimeDataContract.from_approval_evidence(payload)
    assert contract.contract == "policy_verified_runtime_fail_closed"
    assert contract.claims_complete_universe() is False
    assert contract.verified_pairs == frozenset()


def test_a_verified_all_promise_needs_every_required_timeframe() -> None:
    """One timeframe checked is not the market checked."""

    contract = _contract(
        verified_pairs=["BTC/USDT@15m"],
        required_timeframes=["15m", "1h"],
    )
    assert contract.covers("BTC/USDT", ["15m"]) is True
    assert contract.covers("BTC/USDT", ["15m", "1h"]) is False
    assert (
        skip_reason(
            contract,
            symbol="BTC/USDT",
            timeframes=["15m", "1h"],
            data_is_usable=True,
        )
        == CONTRACT_INCOMPLETE
    )


# ---------------------------------------------------------------------------
# 8, 9. A factual claim states a proposition, and the proposition is checked.
#    Enforced by `engine/claim_evidence.validate_claims`.
# ---------------------------------------------------------------------------


def _ledger(**overrides: object):
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
            {
                "operation_id": "add-btc",
                "net_effect": "effective",
                "operation_kind": "add_inclusion",
                "summary": "Added BTC/USDT.",
                "changes": [
                    {"kind": "symbol_included", "target": "BTC/USDT", "condition_ids": []}
                ],
            },
            {
                "operation_id": "add-eth",
                "net_effect": "effective",
                "operation_kind": "add_inclusion",
                "summary": "Added ETH/USDT.",
                "changes": [
                    {"kind": "symbol_included", "target": "ETH/USDT", "condition_ids": []}
                ],
            },
        ],
        execution=execution,
        draft_read_model={
            "conditions": [
                {
                    "condition_id": "c1",
                    "threshold": 5.0,
                    "trigger_timeframe": "15m",
                    "operator": "gte",
                    "movement_direction": "up",
                }
            ],
            "included_symbols": ["BTC/USDT", "ETH/USDT"],
            "excluded_symbols": [],
        },
        screening_evidence={"included_count": 2},
        preflight_evidence={"contract": "verified_all"},
        product_knowledge={"approval": "You approve with the Review and approve control."},
    )


def test_a_mutation_claim_citing_the_wrong_effective_operation_is_refused() -> None:
    """Both operations are real. Only one of them added ETH.

    Citing valid evidence for the wrong sentence used to pass every check.
    """

    ledger = _ledger()
    right = validate_claims(
        [
            FactualClaim(
                claim_id="k1",
                claim_type="mutation",
                subject_id="operation:add-eth",
                predicate="symbol_included",
                asserted_value="ETH/USDT",
                text="I added ETH for you.",
                evidence_ids=["operation:add-eth"],
            )
        ],
        ledger,
    )
    assert right[0].accepted is True

    wrong = validate_claims(
        [
            FactualClaim(
                claim_id="k1",
                claim_type="mutation",
                subject_id="operation:add-btc",
                predicate="symbol_included",
                asserted_value="ETH/USDT",
                text="I added ETH for you.",
                evidence_ids=["operation:add-btc"],
            )
        ],
        ledger,
    )
    assert wrong[0].accepted is False
    assert "not what the evidence says" in (wrong[0].reason or "")


def test_a_mutation_claim_with_no_proposition_is_refused() -> None:
    """The claim most able to describe the wrong thing must say what it changed."""

    validated = validate_claims(
        [
            FactualClaim(
                claim_type="mutation",
                text="I made that change.",
                evidence_ids=["operation:add-btc"],
            )
        ],
        _ledger(),
    )
    assert validated[0].accepted is False
    assert "must state what it changed" in (validated[0].reason or "")


def test_the_subject_must_be_one_of_the_cited_ids() -> None:
    validated = validate_claims(
        [
            FactualClaim(
                claim_type="mutation",
                subject_id="operation:add-eth",
                predicate="symbol_included",
                asserted_value="ETH/USDT",
                text="I added ETH.",
                evidence_ids=["operation:add-btc"],
            )
        ],
        _ledger(),
    )
    assert validated[0].accepted is False


def test_an_unsupported_predicate_is_refused_rather_than_guessed() -> None:
    validated = validate_claims(
        [
            FactualClaim(
                claim_type="condition_explanation",
                subject_id="condition:c1",
                predicate="colour_equals",
                asserted_value="blue",
                text="The rule is blue.",
                evidence_ids=["condition:c1"],
            )
        ],
        _ledger(),
    )
    assert validated[0].accepted is False
    assert "not a supported statement" in (validated[0].reason or "")


@pytest.mark.parametrize(
    ("predicate", "value", "accepted"),
    [
        ("threshold_equals", "5", True),
        ("threshold_equals", "8", False),
        ("timeframe_equals", "15m", True),
        ("timeframe_equals", "1h", False),
        ("operator_equals", "gte", True),
        ("operator_equals", "lte", False),
        ("direction_equals", "up", True),
        ("direction_equals", "down", False),
    ],
)
def test_a_condition_claim_must_state_the_value_the_draft_actually_holds(
    predicate: str,
    value: str,
    accepted: bool,
) -> None:
    """Every field a rule has, checked individually. Quoting the old number fails."""

    validated = validate_claims(
        [
            FactualClaim(
                claim_type="condition_explanation",
                subject_id="condition:c1",
                predicate=predicate,
                asserted_value=value,
                text="The rule says so.",
                evidence_ids=["condition:c1"],
            )
        ],
        _ledger(),
    )
    assert validated[0].accepted is accepted, validated[0].reason


@pytest.mark.parametrize(
    "text",
    [
        "I added ETH for you.",
        "ШЄЩ…ШЄ ШҐШ¶Ш§ЩЃШ© ETH.",
        "Ana zawedt ETH.",
        "е·Іж·»еЉ  ETHгЂ‚",
    ],
)
def test_the_proposition_check_is_the_same_in_every_language(text: str) -> None:
    """The wording varies; the proposition does not. That is the whole point."""

    ledger = _ledger()
    good = validate_claims(
        [
            FactualClaim(
                claim_type="mutation",
                subject_id="operation:add-eth",
                predicate="symbol_included",
                asserted_value="ETH/USDT",
                text=text,
                evidence_ids=["operation:add-eth"],
            )
        ],
        ledger,
    )
    bad = validate_claims(
        [
            FactualClaim(
                claim_type="mutation",
                subject_id="operation:add-eth",
                predicate="symbol_included",
                asserted_value="SOL/USDT",
                text=text,
                evidence_ids=["operation:add-eth"],
            )
        ],
        ledger,
    )
    assert good[0].accepted is True
    assert bad[0].accepted is False


def test_a_value_naming_a_market_that_is_not_in_the_final_setup_is_refused() -> None:
    """Rule 6: the thing named has to exist at the end of the turn."""

    validated = validate_claims(
        [
            FactualClaim(
                claim_type="universe",
                subject_id="universe:included",
                predicate="contains",
                asserted_value="DOGE/USDT",
                text="DOGE is on the list.",
                evidence_ids=["universe:included"],
            )
        ],
        _ledger(),
    )
    assert validated[0].accepted is False


def test_a_turn_that_changed_something_owes_the_user_a_fact() -> None:
    """Enforced by `requires_factual_answer`, read from the turn, not from the reply."""

    assert (
        requires_factual_answer(
            reconciled_operations=[{"net_effect": "effective"}],
            response_points=[],
            questions_to_answer=[],
        )
        is True
    )
    assert (
        requires_factual_answer(
            reconciled_operations=[{"net_effect": "cancelled"}],
            response_points=[{"kind": "acknowledge"}],
            questions_to_answer=[],
        )
        is False
    )
    assert (
        requires_factual_answer(
            reconciled_operations=[],
            response_points=[{"kind": "explain_change"}],
            questions_to_answer=[],
        )
        is True
    )
    assert (
        requires_factual_answer(
            reconciled_operations=[],
            response_points=[],
            questions_to_answer=["what changed?"],
        )
        is True
    )


# ---------------------------------------------------------------------------
# 10. Two same-kind operations never share unrelated change evidence.
#    Enforced by `engine/operation_target.operation_targets` and
#    `engine/operation_reconciliation.reconcile_turn`.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ALL_OPERATION_KINDS)
def test_every_operation_kind_declares_a_target(kind: str) -> None:
    """A kind with no target shares its evidence with its neighbours."""

    payloads: dict[str, dict[str, object]] = {
        "set_fields": {"fields": DraftFieldPatch(exchange="binance")},
        "set_sharia_policy": {"sharia_policy": ShariaPolicyV2()},
        "add_condition": {"condition": _condition_node()},
        "update_condition": {
            "condition": _condition_node(),
            "target_condition_id": "n1",
        },
        "remove_condition": {"target_condition_id": "n1"},
        "replace_groups": {"condition": _condition_node()},
        "add_inclusion": {"symbol": "BTC/USDT"},
        "add_exclusion": {"symbol": "BTC/USDT"},
        "remove_inclusion": {"symbol": "BTC/USDT"},
        "remove_exclusion": {"symbol": "BTC/USDT"},
        "add_unresolved": {"unresolved": _unresolved("u1")},
        "update_unresolved": {"unresolved": _unresolved("u2"), "target_key": "u1"},
        "add_unsupported": {"missing_contract": "order book depth"},
        "resolve_unresolved_key": {"target_key": "u1"},
        "remove_unsupported_key": {"target_key": "k1"},
        "restore_snapshot": {
            "target_snapshot_id": "snap-1",
            "target_executable_version": 1,
        },
    }
    operation = AuthorizedPatchOperation(
        operation_id="op-1",
        authorizing_segment_id="s1",
        kind=kind,  # type: ignore[arg-type]
        **payloads[kind],  # type: ignore[arg-type]
    )
    targets = operation_targets(operation)
    assert targets, f"{kind} declares no target"
    assert all(isinstance(item, OperationTarget) for item in targets)
    assert all(item.identity for item in targets)


def _condition_node():
    draft = _draft()
    assert draft.condition_ast is not None
    return next(
        node
        for node in draft.condition_ast.walk()
        if node.node_type is ConditionNodeType.CONDITION
    )


def _unresolved(key: str) -> UnresolvedFieldV2:
    return UnresolvedFieldV2(
        unresolved_id=key,
        source_turn_id=TURN,
        source_fragment="something",
        target_type="draft_field",
        target_field=key,
        question="Which one?",
        reason="needed",
    )


def test_two_add_inclusion_operations_do_not_share_each_others_symbols() -> None:
    """The reported defect, and the family it belongs to."""

    before = _draft()
    after = apply_strategy_patch(
        before,
        StrategyPatch(source_turn_id=TURN, add_inclusions=["SOL/USDT", "ADA/USDT"]),
    ).draft
    operations = [
        AuthorizedPatchOperation(
            operation_id="op-btc",
            authorizing_segment_id="s1",
            kind="add_inclusion",
            symbol="SOL/USDT",
        ),
        AuthorizedPatchOperation(
            operation_id="op-eth",
            authorizing_segment_id="s1",
            kind="add_inclusion",
            symbol="ADA/USDT",
        ),
    ]
    reconciliation = reconcile_turn(
        before,
        after,
        [
            _operation_result("op-btc", "add_inclusion"),
            _operation_result("op-eth", "add_inclusion"),
        ],
        targets_by_operation_id(operations, before),
    )
    by_id = {item.operation_id: item for item in reconciliation.operations}
    btc_targets = [change.target for change in by_id["op-btc"].net_changes]
    eth_targets = [change.target for change in by_id["op-eth"].net_changes]
    assert btc_targets == ["SOL/USDT"]
    assert eth_targets == ["ADA/USDT"]
    assert by_id["op-btc"].net_effect == "effective"
    assert by_id["op-eth"].net_effect == "effective"


def test_without_targets_the_old_sharing_is_what_happens() -> None:
    """Proves the targets are doing the work, not something else in the diff."""

    before = _draft()
    after = apply_strategy_patch(
        before,
        StrategyPatch(source_turn_id=TURN, add_inclusions=["SOL/USDT", "ADA/USDT"]),
    ).draft
    reconciliation = reconcile_turn(
        before,
        after,
        [
            _operation_result("op-btc", "add_inclusion"),
            _operation_result("op-eth", "add_inclusion"),
        ],
    )
    shared = {
        item.operation_id: sorted(change.target or "" for change in item.net_changes)
        for item in reconciliation.operations
    }
    assert shared["op-btc"] == ["ADA/USDT", "SOL/USDT"]
    assert shared["op-btc"] == shared["op-eth"]


def test_a_sharia_policy_change_is_diffed_and_attributed() -> None:
    """There was no policy diff at all, so these operations never looked effective."""

    before = _draft()
    after = apply_strategy_patch(
        before,
        StrategyPatch(
            source_turn_id=TURN,
            set_sharia_policy=before.sharia_policy.model_copy(
                update={"universe_mode": ShariaUniverseMode.EXPLICIT_ASSETS}
            ),
        ),
    ).draft
    kinds = [change.kind for change in diff_drafts(before, after)]
    assert "sharia_policy_changed" in kinds

    operation = AuthorizedPatchOperation(
        operation_id="op-policy",
        authorizing_segment_id="s1",
        kind="set_sharia_policy",
        sharia_policy=after.sharia_policy,
    )
    reconciliation = reconcile_turn(
        before,
        after,
        [_operation_result("op-policy", "set_sharia_policy")],
        targets_by_operation_id([operation], before),
    )
    assert reconciliation.operations[0].net_effect == "effective"
    assert [change.target for change in reconciliation.operations[0].net_changes] == [
        "sharia_policy.universe_mode"
    ]


def test_an_overwritten_operation_names_the_one_that_replaced_it() -> None:
    """"Overwritten" with no explanation is not evidence a user can act on."""

    seeded = _draft()
    node_id = _condition_ids(seeded)[0]
    node = next(
        item
        for item in seeded.condition_ast.walk()  # type: ignore[union-attr]
        if item.node_id == node_id
    )
    from ai_market_monitor.schemas.strategy_draft_v2 import ConditionUpdateV2

    first = apply_strategy_patch(
        seeded,
        StrategyPatch(
            source_turn_id=TURN,
            update_conditions=[
                ConditionUpdateV2(
                    node_id=node_id, replacement=node.model_copy(update={"threshold": 8.0})
                )
            ],
        ),
    ).draft
    after = apply_strategy_patch(
        first,
        StrategyPatch(
            source_turn_id=TURN,
            update_conditions=[
                ConditionUpdateV2(
                    node_id=node_id, replacement=node.model_copy(update={"threshold": 5.0})
                )
            ],
        ),
    ).draft
    reconciliation = reconcile_turn(
        seeded,
        after,
        [
            _operation_result("op-8", "update_condition", ids=[node_id]),
            _operation_result("op-5", "update_condition", ids=[node_id]),
        ],
    )
    by_id = {item.operation_id: item for item in reconciliation.operations}
    # Both wrote the same field; only the final value survives, so exactly one of them is
    # effective and the other names its successor.
    assert {item.net_effect for item in reconciliation.operations} <= {
        "effective",
        "overwritten",
        "no_net_effect",
    }
    superseded = [item for item in reconciliation.operations if item.superseded_by]
    for item in superseded:
        assert item.superseded_by in by_id
        assert item.superseded_by != item.operation_id


def test_the_unsupported_key_has_one_owner() -> None:
    """The patch builder and the reconciler read the same expression."""

    operation = AuthorizedPatchOperation(
        operation_id="op-1",
        authorizing_segment_id="s7",
        kind="add_unsupported",
        missing_contract="order book depth",
    )
    assert unsupported_key_for(operation) == "unsupported_s7"
    assert operation_targets(operation) == (
        OperationTarget("unsupported", "unsupported_s7"),
    )


# ---------------------------------------------------------------------------
# 12. Approval refuses any incomplete evidence chain.
#    Enforced by `services/setup_chat_launch.revalidate_for_approval` calling
#    `ReviewedScreeningEvidence.missing_evidence`.
# ---------------------------------------------------------------------------


def _complete_evidence(**overrides: object) -> ReviewedScreeningEvidence:
    base: dict[str, object] = {
        "screening_snapshot_hash": "a" * 64,
        "screening_policy_hash": "b" * 64,
        "methodology_id": str(uuid4()),
        "methodology_version": "1.0.0",
        "resolved_symbol_set_hash": symbol_set_hash(["BTC/USDT"]),
        "secured_preview_hash": "c" * 64,
        "provider_preflight_manifest_hash": "d" * 64,
        "preflight_contract": "verified_all",
        "membership_kind": "fixed_authored",
        "reviewed_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ReviewedScreeningEvidence(**base)  # type: ignore[arg-type]


def test_complete_evidence_reports_nothing_missing() -> None:
    assert _complete_evidence().missing_evidence() == []


@pytest.mark.parametrize("field_name", ReviewedScreeningEvidence.REQUIRED_FIELDS)
def test_every_required_fact_is_required_individually(field_name: str) -> None:
    """Absent evidence used to compare equal to absent evidence, and approval passed."""

    if field_name == "watchlist_snapshot_hash":
        pytest.skip("only required for a Favorites universe; covered separately")
    evidence = _complete_evidence(**{field_name: None})
    missing = evidence.missing_evidence()
    assert missing, f"{field_name} was absent but nothing objected"
    sentence = evidence.describe_missing()
    assert "cannot be approved yet" in sentence
    # A beginner has to be able to read it.
    assert field_name not in sentence


def test_a_favorites_universe_also_requires_its_list_identity() -> None:
    fixed = _complete_evidence(membership_kind="fixed_watchlist")
    assert fixed.missing_evidence() == ["the Favorites list this setup uses"]
    with_list = fixed.model_copy(update={"watchlist_snapshot_hash": "wlv2:" + "e" * 64})
    assert with_list.missing_evidence() == []


def test_a_dynamic_universe_does_not_require_a_favorites_list() -> None:
    assert _complete_evidence(membership_kind="governed_dynamic").missing_evidence() == []


@pytest.mark.parametrize(
    ("kind", "must_mention"),
    [
        ("fixed_authored", "approve the setup again"),
        ("fixed_watchlist", "approve the setup again"),
        ("governed_dynamic", "change on its own"),
    ],
)
def test_the_approval_states_what_it_promises_about_membership(
    kind: str,
    must_mention: str,
) -> None:
    """A fixed approval and a dynamic one are different promises to the user."""

    sentence = _complete_evidence(membership_kind=kind).membership_sentence
    assert must_mention in sentence


@pytest.mark.parametrize("field_name", ReviewedScreeningEvidence.BOUND_FIELDS)
def test_every_bound_fact_that_moves_is_reported_as_changed(field_name: str) -> None:
    reviewed = _complete_evidence(watchlist_snapshot_hash="wlv2:" + "e" * 64)
    moved = {
        "preflight_contract": "policy_verified_runtime_fail_closed",
        "membership_kind": "governed_dynamic",
    }.get(field_name, "z" * 64)
    now = reviewed.model_copy(update={field_name: moved})
    assert reviewed.differences_from(now), f"{field_name} moved but was not reported"
    assert "changed since you reviewed" in reviewed.describe_change(now)


# ---------------------------------------------------------------------------
# 13, 11 (production decisions). Billing off shows one plan and no dead buttons.
#    Enforced by `core/plans.visible_public_plan_codes` and `visible_plan_comparison`.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("billing_enabled", [True, False])
def test_prices_stay_on_the_page_in_both_billing_modes(billing_enabled: bool) -> None:
    """Checkout being off changes the button, never whether a price is shown."""

    assert visible_public_plan_codes(billing_enabled=billing_enabled) == PUBLIC_PLAN_CODES


@pytest.mark.parametrize("billing_enabled", [True, False])
def test_the_comparison_table_always_has_one_column_per_visible_plan(
    billing_enabled: bool,
) -> None:
    """A plan can never be a card with no column, or a column with no card."""

    headers = visible_plan_comparison_headers(billing_enabled=billing_enabled)
    rows = visible_plan_comparison(billing_enabled=billing_enabled)
    assert len(headers) == len(
        visible_public_plan_codes(billing_enabled=billing_enabled)
    )
    assert all(len(row) == len(headers) + 1 for row in rows)


def test_only_the_monitor_plan_is_on_sale_and_only_monthly() -> None:
    """Enforced by `core/plans.plan_offer`, read by every pricing surface."""

    assert plan_offer("trader").monthly_available is True
    assert plan_offer("pro").monthly_available is False
    for code in PUBLIC_PLAN_CODES:
        assert plan_offer(code).annual_available is False, code


def test_an_unknown_plan_is_never_for_sale() -> None:
    """Fail closed: a plan nobody described cannot be bought by accident."""

    offer = plan_offer("not-a-plan")
    assert offer.monthly_available is False
    assert offer.annual_available is False


def test_the_launch_price_and_the_countdown_come_from_one_rule() -> None:
    """A price on the page and a timer beside it must never disagree."""

    before = PROMOTION_ENDS_AT - timedelta(minutes=1)
    after = PROMOTION_ENDS_AT

    assert promotion_is_active(before) is True
    assert effective_monthly_price("trader", now=before) == Decimal("8.00")
    assert original_monthly_price("trader", now=before) == Decimal("12.00")

    assert promotion_is_active(after) is False
    assert effective_monthly_price("trader", now=after) == Decimal("12.00")
    # Nothing to cross out once the offer is over.
    assert original_monthly_price("trader", now=after) is None


@pytest.mark.parametrize("code", PUBLIC_PLAN_CODES)
def test_a_plan_with_no_promotion_has_nothing_crossed_out(code: str) -> None:
    if code == "trader":
        pytest.skip("the Monitor plan is the one on offer")
    assert original_monthly_price(code) is None
    assert effective_monthly_price(code) == PLAN_DEFINITIONS[code].monthly_price


@pytest.mark.parametrize("code", PUBLIC_PLAN_CODES)
def test_the_offer_payload_carries_everything_a_card_needs(code: str) -> None:
    """The landing page and the dashboard read this same object."""

    payload = plan_offer_payload(code, now=PROMOTION_ENDS_AT - timedelta(days=1))
    assert set(payload) == {
        "monthlyAvailable",
        "annualAvailable",
        "monthlyPrice",
        "annualPrice",
        "originalMonthlyPrice",
        "comingSoonLabel",
    }
    assert payload["comingSoonLabel"] == COMING_SOON_LABEL
    assert payload["annualAvailable"] is False
    # An interval that is not open carries no number at all, so the page source cannot
    # leak a price for something nobody can buy.
    assert payload["annualPrice"] is None
    if code == "trader":
        assert payload["monthlyPrice"] == 8.0
        assert payload["originalMonthlyPrice"] == 12.0
    else:
        assert payload["originalMonthlyPrice"] is None
    if not payload["monthlyAvailable"]:
        assert payload["monthlyPrice"] is None


def test_the_old_coordinator_stays_off_in_the_production_example() -> None:
    """Authenticated Setup Chat is served by the Setup Agent, not the coordinator."""

    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    values: dict[str, str] = {}
    for line in (root / ".env.production.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            values[key] = value
    assert values["AI_AGENT_CONTROL_ENABLED"] == "false"
    assert values["AI_AGENT_SHADOW_MODE"] == "false"
    assert values["AI_AGENT_ROLLOUT_PERCENT"] == "0"


# ---------------------------------------------------------------------------
# 14. Pure conversation remains non-mutating.
#    Enforced by `services/setup_chat_agent.SetupChatAgent.run_turn` returning before
#    `apply_setup_turn` when the plan requires no tool.
# ---------------------------------------------------------------------------


def test_a_watchlist_scope_is_built_the_same_way_from_a_draft_and_a_definition() -> None:
    """Two spellings of one scope guarantee a false "the list changed"."""

    from ai_market_monitor.engine.strategy_compiler_v2 import compile_strategy_draft_v2
    from ai_market_monitor.services.watchlist_snapshot import (
        scope_from_definition,
        scope_from_draft,
    )

    draft = _draft()
    definition = compile_strategy_draft_v2(draft)
    assert scope_from_draft(draft) == scope_from_definition(definition)


def test_a_watchlist_content_hash_is_recognisable_and_versioned() -> None:
    from ai_market_monitor.services.watchlist_snapshot import (
        SNAPSHOT_PREFIX,
        WatchlistMember,
        WatchlistSnapshot,
        is_content_identity,
        is_legacy_identity,
    )

    snapshot = WatchlistSnapshot(
        watchlist_id=UUID("55555555-5555-5555-5555-555555555555"),
        name="My list",
        exchange="binance",
        quote_currencies=["USDT"],
        members=[WatchlistMember(canonical_asset="BTC", canonical_asset_id="x")],
        created_at=datetime.now(UTC),
    )
    assert snapshot.content_hash.startswith(SNAPSHOT_PREFIX)
    assert is_content_identity(snapshot.content_hash)
    # The old string-concatenation identity cannot be compared with a governed one.
    assert is_legacy_identity("wlv1:" + "a" * 64)
    assert not is_content_identity("wlv1:" + "a" * 64)
    assert is_legacy_identity("2026-07-30T12:00:00+00:00")
