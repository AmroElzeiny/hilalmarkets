from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from ai_market_monitor.engine.strategy_compiler_v2 import (
    StrategyV2CompileError,
    compile_strategy_draft_v2,
    validate_compiled_equivalence,
)
from ai_market_monitor.engine.strategy_draft_migration import migrate_legacy_draft
from ai_market_monitor.engine.strategy_draft_v2 import (
    DraftPatchError,
    apply_strategy_patch,
    validate_draft_semantics,
)
from ai_market_monitor.schemas.strategy import Comparator
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ApprovalBindingV2,
    ConditionNodeType,
    ConditionNodeV2,
    ConditionUpdateV2,
    DraftDirection,
    FormulaKind,
    OperandV2,
    ReversionV2,
    ShariaPolicyV2,
    StrategyDraftV2,
    StrategyPatch,
    UnresolvedFieldV2,
)
from ai_market_monitor.services.strategy_patch_extractor import (
    deterministic_strategy_patch,
)


def _condition(
    *,
    node_id: str = "move",
    formula: FormulaKind = FormulaKind.OPEN_TO_CLOSE_PERCENTAGE,
    direction: DraftDirection = DraftDirection.LONG,
    operator: Comparator = Comparator.GREATER_THAN_OR_EQUAL,
    threshold: float = 5,
    timeframe: str = "15m",
) -> ConditionNodeV2:
    return ConditionNodeV2(
        node_id=node_id,
        node_type=ConditionNodeType.CONDITION,
        source_turn_id="turn-12345678",
        source_fragment="the 15m candle rises open-to-close by at least 5%",
        direction=direction,
        formula=formula,
        operands=[
            OperandV2(
                role="measured_value",
                kind="market_metric",
                name="percentage_change",
                parameters={"formula": formula.value},
            )
        ],
        operator=operator,
        threshold=threshold,
        unit="percent",
        trigger_timeframe=timeframe,
        reference_timeframe=timeframe,
        reference_definition="open to close",
    )


def test_deterministic_patch_preserves_formula_operator_threshold_and_universe():
    draft = StrategyDraftV2()
    patch = deterministic_strategy_patch(
        draft,
        (
            "Monitor BTC/USDT on Binance when the 15m candle rises open-to-close "
            "by at least 5%, excluding ETH/USDT"
        ),
        source_turn_id="turn-12345678",
    )

    assert patch is not None
    updated = apply_strategy_patch(draft, patch).draft
    assert updated.universe.included_symbols == ["BTC/USDT"]
    assert updated.universe.excluded_symbols == ["ETH/USDT"]
    assert updated.condition_ast is not None
    assert updated.condition_ast.formula is FormulaKind.OPEN_TO_CLOSE_PERCENTAGE
    assert updated.condition_ast.operator is Comparator.GREATER_THAN_OR_EQUAL
    assert updated.condition_ast.threshold == 5
    assert updated.condition_ast.trigger_timeframe == "15m"
    assert validate_draft_semantics(updated) == []

    compiled = compile_strategy_draft_v2(updated)
    rule = compiled.conditions.children[0]
    assert rule.left.parameters["formula"] == "open_to_close"
    assert rule.comparator is Comparator.GREATER_THAN_OR_EQUAL
    assert rule.right is not None and rule.right.value == 5
    assert "ETH/USDT" not in compiled.universe.include_symbols


def test_multi_condition_nodes_keep_independent_semantics_and_grouping():
    first = _condition(node_id="first", threshold=5, timeframe="15m")
    second = _condition(
        node_id="second",
        formula=FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE,
        direction=DraftDirection.SHORT,
        operator=Comparator.LESS_THAN_OR_EQUAL,
        threshold=-2,
        timeframe="1h",
    )
    root = ConditionNodeV2(
        node_id="root_or",
        node_type=ConditionNodeType.OR,
        children=[
            first,
            ConditionNodeV2(
                node_id="not_second",
                node_type=ConditionNodeType.NOT,
                children=[second],
            ),
        ],
    )
    draft = StrategyDraftV2(condition_ast=root)

    compiled = compile_strategy_draft_v2(draft)

    assert compiled.conditions.operator.value == "or"
    assert compiled.conditions.children[1].operator.value == "not"
    assert compiled.conditions.children[0].ast_path == [0]
    assert compiled.conditions.children[1].children[0].ast_path == [1, 0]
    assert compiled.base_timeframe == "15m"
    assert compiled.supporting_timeframes == ["1h"]


def test_later_deterministic_condition_appends_without_deleting_existing_rule():
    draft = StrategyDraftV2(condition_ast=_condition(node_id="first", threshold=5))
    patch = deterministic_strategy_patch(
        draft,
        "Also require the 1h candle to fall close-to-close by at most -2%",
        source_turn_id="turn-87654321",
    )

    assert patch is not None
    updated = apply_strategy_patch(draft, patch).draft
    assert updated.condition_ast is not None
    conditions = [
        item
        for item in updated.condition_ast.walk()
        if item.node_type is ConditionNodeType.CONDITION
    ]

    assert [item.node_id for item in conditions][0] == "first"
    assert [item.threshold for item in conditions] == [5, -2]
    assert [item.trigger_timeframe for item in conditions] == ["15m", "1h"]


@pytest.mark.parametrize(
    ("message", "formula", "operator", "threshold", "reference"),
    [
        (
            "Monitor BTC/USDT when price crosses above 50000 on 15m",
            FormulaKind.CROSS,
            Comparator.CROSSES_ABOVE,
            50000,
            "fixed price level 50000",
        ),
        (
            "Monitor BTC/USDT when price crosses below 49000 on 15m",
            FormulaKind.CROSS,
            Comparator.CROSSES_BELOW,
            49000,
            "fixed price level 49000",
        ),
        (
            "Monitor BTC/USDT when the 15m close is above the previous candle close",
            FormulaKind.PREVIOUS_CANDLE_REFERENCE,
            Comparator.GREATER_THAN,
            None,
            "previous closed candle close",
        ),
        (
            (
                "Monitor BTC/USDT when the 15m close is above the highest high "
                "of the previous 20 candles"
            ),
            FormulaKind.LOOKBACK_REFERENCE_LEVEL,
            Comparator.GREATER_THAN,
            None,
            "highest_high of previous 20 candles",
        ),
    ],
)
def test_core_reference_primitives_compile_without_capability_search(
    message,
    formula,
    operator,
    threshold,
    reference,
):
    patch = deterministic_strategy_patch(
        StrategyDraftV2(),
        message,
        source_turn_id="turn-12345678",
    )

    assert patch is not None
    draft = apply_strategy_patch(StrategyDraftV2(), patch).draft
    assert draft.condition_ast is not None
    assert draft.condition_ast.formula is formula
    assert draft.condition_ast.operator is operator
    assert draft.condition_ast.threshold == threshold
    assert draft.condition_ast.reference_definition == reference
    assert draft.unsupported_requirements == []

    compiled = compile_strategy_draft_v2(draft)
    rule = compiled.conditions.children[0]
    assert rule.comparator is operator
    assert rule.capability_key is None


def test_trigger_context_confirmation_and_reference_roles_compile_distinctly():
    patch = deterministic_strategy_patch(
        StrategyDraftV2(),
        (
            "Monitor BTC/USDT with 4h context when the 15m trigger candle "
            "rises open-to-close by at least 3%, with 1h confirmation"
        ),
        source_turn_id="turn-12345678",
    )

    assert patch is not None
    draft = apply_strategy_patch(StrategyDraftV2(), patch).draft
    assert draft.condition_ast is not None
    assert draft.condition_ast.trigger_timeframe == "15m"
    assert draft.condition_ast.context_timeframes == ["4h"]
    assert draft.condition_ast.confirmation_timeframes == ["1h"]
    assert draft.condition_ast.reference_timeframe == "15m"

    errors = validate_draft_semantics(draft)
    assert errors == []
    compiled = compile_strategy_draft_v2(draft)
    rule = compiled.conditions.children[0]
    assert rule.timeframe == "15m"
    assert rule.context_timeframes == ["4h"]
    assert rule.confirmation_timeframes == ["1h"]
    assert rule.reference_timeframe == "15m"
    assert set(compiled.supporting_timeframes) == {"1h", "4h"}


def test_neutral_strategy_compiles_as_one_neutral_evaluation():
    draft = StrategyDraftV2(
        condition_ast=_condition(direction=DraftDirection.NEUTRAL)
    )

    compiled = compile_strategy_draft_v2(draft)

    assert compiled.direction.value == "neutral"


def test_static_sharia_policy_changes_executable_identity_and_invalidates_approval():
    draft = StrategyDraftV2(condition_ast=_condition())
    approved = StrategyDraftV2.model_validate(
        draft.model_copy(
            update={
                "approval": ApprovalBindingV2(
                    approved=True,
                    user_id=uuid4(),
                    executable_version=draft.executable_version,
                    executable_hash=draft.executable_hash,
                    schema_hash="b" * 64,
                    conversation_snapshot_hash="a" * 64,
                    approved_at=datetime.now(UTC),
                )
            }
        ).model_dump(mode="json")
    )
    changed_policy = ShariaPolicyV2(
        universe_mode="explicit_assets",
        explicit_symbols=["BTC/USDT", "ETH/USDT"],
        allowed_statuses=approved.sharia_policy.allowed_statuses,
        compliance_change_behavior=approved.sharia_policy.compliance_change_behavior,
    )

    result = apply_strategy_patch(
        approved,
        StrategyPatch(
            source_turn_id="turn-sharia-policy",
            set_sharia_policy=changed_policy,
        ),
    )

    assert result.draft.executable_version == approved.executable_version + 1
    assert result.draft.executable_hash != approved.executable_hash
    assert result.draft.sharia_policy.explicit_symbols == ["BTC/USDT", "ETH/USDT"]
    assert result.draft.approval.approved is False


def test_compiler_preserves_source_turn_and_explicit_lookback_and_detects_drift():
    condition = _condition().model_copy(
        update={
            "source_turn_id": "turn-provenance-1234",
            "lookback": 20,
            "condition_symbols": ["BTC/USDT"],
        }
    )
    draft = StrategyDraftV2(condition_ast=condition)

    compiled = compile_strategy_draft_v2(draft)
    rule = compiled.conditions.children[0]

    assert rule.source_turn_id == "turn-provenance-1234"
    assert rule.source_fragment == condition.source_fragment
    assert rule.source_operands
    assert rule.condition_symbols == ["BTC/USDT"]
    assert rule.resolved_parameters["lookback"] == 20
    assert validate_compiled_equivalence(draft, compiled) == []

    drifted_rule = rule.model_copy(
        update={
            "source_turn_id": "turn-wrong",
            "source_operands": [],
            "condition_symbols": [],
            "resolved_parameters": {
                **rule.resolved_parameters,
                "lookback": 19,
            },
        }
    )
    drifted = compiled.model_copy(
        update={
            "conditions": compiled.conditions.model_copy(
                update={"children": [drifted_rule]}
            )
        }
    )

    errors = validate_compiled_equivalence(draft, drifted)
    assert "source_turn_id:move" in errors
    assert "operands:move" in errors
    assert "condition_symbols:move" in errors
    assert "lookback:move" in errors


def test_latest_correction_wins_and_invalidates_approval():
    initial = StrategyDraftV2(condition_ast=_condition())
    approved = StrategyDraftV2.model_validate(
        initial.model_copy(
            update={
                "approval": ApprovalBindingV2(
                    approved=True,
                    user_id=uuid4(),
                    draft_version=initial.version,
                    semantic_hash=initial.semantic_hash,
                    schema_hash="b" * 64,
                    conversation_snapshot_hash="a" * 64,
                    approved_at=datetime.now(UTC),
                )
            }
        ).model_dump(mode="json")
    )
    replacement = _condition(
        node_id="move",
        operator=Comparator.LESS_THAN_OR_EQUAL,
        threshold=2,
    )
    result = apply_strategy_patch(
        approved,
        StrategyPatch(
            source_turn_id="turn-87654321",
            update_conditions=[
                ConditionUpdateV2(node_id="move", replacement=replacement)
            ],
        ),
    )

    assert result.draft.version == approved.version + 1
    assert not result.draft.approval.approved
    assert result.draft.condition_ast is not None
    assert result.draft.condition_ast.operator is Comparator.LESS_THAN_OR_EQUAL
    assert result.draft.condition_ast.threshold == 2


def test_patch_never_clears_unresolved_targets_without_explicit_resolution():
    draft = StrategyDraftV2(
        unresolved_fields=[
            UnresolvedFieldV2(
                key="reference_price_definition",
                source_turn_id="turn-old-reference",
                source_fragment="Which reference price defines the move?",
                question="Which reference price defines the move?",
            ),
            UnresolvedFieldV2(
                key="one_hour_context_definition",
                source_turn_id="turn-old-context",
                source_fragment="What should the 1h context be?",
                question="What should the 1h context be?",
            ),
            UnresolvedFieldV2(
                key="watchlist_scope",
                source_turn_id="turn-old-scope",
                source_fragment="Which assets should be included?",
                question="Which assets should be included?",
            ),
            UnresolvedFieldV2(
                key="delivery_channel",
                source_turn_id="turn-old-delivery",
                source_fragment="Which delivery channel should be used?",
                question="Which delivery channel should be used?",
            ),
        ]
    )
    condition = _condition(
        formula=FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE,
        direction=DraftDirection.SHORT,
        operator=Comparator.GREATER_THAN_OR_EQUAL,
        threshold=5,
        timeframe="1m",
    ).model_copy(
        update={
            "source_fragment": (
                "Use the last 1h close as reference and the 1m close as current."
            ),
            "context_timeframes": ["1h"],
            "reference_timeframe": "1h",
            "reference_definition": "last closed 1h close to current closed 1m close",
        }
    )

    result = apply_strategy_patch(
        draft,
        StrategyPatch(
            source_turn_id="turn-answer-1234",
            add_inclusions=["ETHUSDT"],
            add_exclusions=["BTCUSDT"],
            add_conditions=[condition],
        ),
    )

    assert [item.key for item in result.draft.unresolved_fields] == [
        "reference_price_definition",
        "one_hour_context_definition",
        "watchlist_scope",
        "delivery_channel",
    ]


def test_existing_condition_does_not_clear_new_unrelated_unresolved_question():
    draft = StrategyDraftV2(condition_ast=_condition())

    result = apply_strategy_patch(
        draft,
        StrategyPatch(
            source_turn_id="turn-scope-1234",
            add_inclusions=["ETHUSDT"],
            unresolved_references=[
                UnresolvedFieldV2(
                    key="delivery_channel",
                    source_turn_id="turn-scope-1234",
                    source_fragment="Notify me somehow.",
                    question="Which delivery channel should be used?",
                )
            ],
        ),
    )

    assert [item.key for item in result.draft.unresolved_fields] == [
        "delivery_channel"
    ]


def test_snapshot_reversion_restores_semantics_as_new_unapproved_version():
    original = StrategyDraftV2(condition_ast=_condition(threshold=3))
    changed = apply_strategy_patch(
        original,
        StrategyPatch(
            source_turn_id="turn-87654321",
            update_conditions=[
                ConditionUpdateV2(
                    node_id="move",
                    replacement=_condition(threshold=8),
                )
            ],
        ),
    ).draft
    reverted = apply_strategy_patch(
        changed,
        StrategyPatch(
            source_turn_id="turn-22222222",
            reversion=ReversionV2(target_version=original.version),
        ),
        history=[original.model_dump(mode="json")],
    ).draft

    assert reverted.version == changed.version + 1
    assert reverted.condition_ast is not None
    assert reverted.condition_ast.threshold == 3
    assert not reverted.approval.approved


def test_unknown_condition_update_is_rejected_without_state_change():
    draft = StrategyDraftV2(condition_ast=_condition())

    with pytest.raises(DraftPatchError):
        apply_strategy_patch(
            draft,
            StrategyPatch(
                source_turn_id="turn-87654321",
                update_conditions=[
                    ConditionUpdateV2(
                        node_id="missing",
                        replacement=_condition(node_id="missing"),
                    )
                ],
            ),
        )

    assert draft.condition_ast is not None
    assert draft.condition_ast.threshold == 5


def test_approval_cannot_bind_to_wrong_version_or_hash():
    draft = StrategyDraftV2(condition_ast=_condition())

    with pytest.raises(ValidationError):
        StrategyDraftV2.model_validate(
            draft.model_copy(
                update={
                    "approval": ApprovalBindingV2(
                        approved=True,
                        user_id=uuid4(),
                        draft_version=draft.version + 1,
                        semantic_hash=draft.semantic_hash,
                        schema_hash="b" * 64,
                        conversation_snapshot_hash="b" * 64,
                        approved_at=datetime.now(UTC),
                    )
                }
            ).model_dump(mode="json")
        )


def test_legacy_unknown_condition_is_blocked_without_formula_substitution():
    source = StrategyDraftV2(condition_ast=_condition())
    definition = compile_strategy_draft_v2(source)
    rule = definition.conditions.children[0]
    unknown_rule = rule.model_copy(
        update={
            "left": rule.left.model_copy(update={"parameters": {}}),
            "resolved_parameters": {},
            "capability_key": None,
            "source_fragment": "legacy proprietary market mechanic",
        }
    )
    legacy = definition.model_copy(
        update={
            "conditions": definition.conditions.model_copy(
                update={"children": [unknown_rule]}
            )
        }
    )

    migrated = migrate_legacy_draft(
        legacy.model_dump(mode="json"),
        setup_mode="monitor",
    )

    assert migrated.condition_ast is not None
    migrated_condition = migrated.condition_ast.children[0]
    assert migrated_condition.formula is FormulaKind.CAPABILITY
    assert migrated_condition.capability_key == "legacy_unmapped_condition"
    assert migrated.blocking
    assert migrated.unsupported_requirements[0].source_fragment == (
        "legacy proprietary market mechanic"
    )
    with pytest.raises(StrategyV2CompileError) as captured:
        compile_strategy_draft_v2(migrated)
    assert captured.value.code == "draft_blocked"


@settings(max_examples=40, deadline=None)
@given(
    formula=st.sampled_from(
        [
            FormulaKind.OPEN_TO_CLOSE_PERCENTAGE,
            FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE,
            FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE,
            FormulaKind.HIGH_TO_LOW_PERCENTAGE,
            FormulaKind.LOW_TO_HIGH_PERCENTAGE,
        ]
    ),
    direction=st.sampled_from(list(DraftDirection)),
    operator=st.sampled_from(
        [
            Comparator.GREATER_THAN,
            Comparator.GREATER_THAN_OR_EQUAL,
            Comparator.LESS_THAN,
            Comparator.LESS_THAN_OR_EQUAL,
            Comparator.EQUAL,
        ]
    ),
    threshold=st.floats(
        min_value=-100,
        max_value=100,
        allow_nan=False,
        allow_infinity=False,
    ),
    timeframe=st.sampled_from(["1m", "15m", "1h", "4h", "1d"]),
)
def test_formula_properties_survive_v2_compilation(
    formula,
    direction,
    operator,
    threshold,
    timeframe,
):
    draft = StrategyDraftV2(
        condition_ast=_condition(
            formula=formula,
            direction=direction,
            operator=operator,
            threshold=threshold,
            timeframe=timeframe,
        )
    )
    incompatible = (
        formula is FormulaKind.HIGH_TO_LOW_PERCENTAGE
        and direction is DraftDirection.LONG
    ) or (
        formula is FormulaKind.LOW_TO_HIGH_PERCENTAGE
        and direction is DraftDirection.SHORT
    )
    if incompatible:
        with pytest.raises(StrategyV2CompileError):
            compile_strategy_draft_v2(draft)
        return

    compiled = compile_strategy_draft_v2(draft)
    rule = compiled.conditions.children[0]

    assert rule.left.parameters["formula"] == {
        FormulaKind.OPEN_TO_CLOSE_PERCENTAGE: "open_to_close",
        FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE: "close_to_close",
        FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE: "reference_to_current",
        FormulaKind.HIGH_TO_LOW_PERCENTAGE: "high_to_low",
        FormulaKind.LOW_TO_HIGH_PERCENTAGE: "low_to_high",
    }[formula]
    assert rule.comparator is operator
    assert rule.right is not None and rule.right.value == threshold
    assert rule.timeframe == timeframe
