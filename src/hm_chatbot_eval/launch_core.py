from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from ai_market_monitor.engine.setup_intent import decide_setup_intent
from ai_market_monitor.engine.strategy_compiler_v2 import (
    StrategyV2CompileError,
    compile_strategy_draft_v2,
)
from ai_market_monitor.engine.strategy_draft_v2 import (
    apply_strategy_patch,
    validate_draft_semantics,
)
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ApprovalBindingV2,
    ConditionNodeType,
    ConditionUpdateV2,
    ReversionV2,
    SetupIntent,
    StrategyDraftV2,
    StrategyPatch,
    UnsupportedRequirementV2,
)
from ai_market_monitor.services.strategy_patch_extractor import (
    deterministic_strategy_patch,
)


@dataclass(frozen=True, slots=True)
class LaunchCoreContract:
    id: str
    message: str
    formula: str
    operator: str
    threshold: float | None
    #: The trigger timeframe: the candle whose close fires the rule.
    timeframe: str
    movement_direction: str
    strategy_bias: str = "neutral"
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    exchange: str = "binance"
    quote_asset: str = "USDT"
    #: The supporting roles. Asserting only the trigger let a context or confirming
    #: timeframe silently take the trigger's place, which is the substitution the
    #: semantic contract forbids, so each role is stated and checked separately.
    context: tuple[str, ...] = ()
    confirmation: tuple[str, ...] = ()
    reference: str | None = None
    expected_blocking_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class LaunchCoreResult:
    contract: LaunchCoreContract
    passed: bool
    checks: dict[str, bool]
    errors: tuple[str, ...]
    draft: dict[str, Any] | None
    compiled: dict[str, Any] | None
    elapsed_ms: float
    model_calls: int = 0
    cost_usd: float = 0


def launch_core_contracts() -> list[LaunchCoreContract]:
    """Stable launch grammar, generated independently of paid evaluator topics."""

    return [
        LaunchCoreContract(
            id="open-close-long-gte",
            message=(
                "Monitor BTC/USDT on Binance when the 15m candle rises "
                "open-to-close by at least 5%, excluding ETH/USDT"
            ),
            formula="open_to_close_percentage",
            operator="gte",
            threshold=5,
            timeframe="15m",
            movement_direction="up",
            include=("BTC/USDT",),
            exclude=("ETH/USDT",),
        ),
        LaunchCoreContract(
            id="close-close-short-gte",
            message="Monitor SOL/USDT when the 1h close-to-close move drops by at least 2%",
            formula="close_to_close_percentage",
            operator="gte",
            threshold=2,
            timeframe="1h",
            movement_direction="down",
            include=("SOL/USDT",),
        ),
        LaunchCoreContract(
            id="high-low-short-gte",
            message="Scan BTC/USDT when the 4h high-to-low move drops by at least 4%",
            formula="high_to_low_percentage",
            operator="gte",
            threshold=4,
            timeframe="4h",
            movement_direction="down",
            include=("BTC/USDT",),
        ),
        LaunchCoreContract(
            id="open-close-upper-bound",
            message="Monitor ETH/USDT when 15m open-to-close percent_move <= 1.5%",
            formula="open_to_close_percentage",
            operator="lte",
            threshold=1.5,
            timeframe="15m",
            movement_direction="up",
            include=("ETH/USDT",),
        ),
        LaunchCoreContract(
            id="daily-reference-current",
            message="Monitor BTC/USDT when today's 1h move rises by at least 3%",
            formula="reference_to_current_percentage",
            operator="gte",
            threshold=3,
            timeframe="1h",
            movement_direction="up",
            include=("BTC/USDT",),
        ),
        LaunchCoreContract(
            id="low-high-long-gte",
            message="Monitor BTC/USDT when the 15m low-to-high move rises by at least 6%",
            formula="low_to_high_percentage",
            operator="gte",
            threshold=6,
            timeframe="15m",
            movement_direction="up",
            include=("BTC/USDT",),
        ),
        LaunchCoreContract(
            id="fixed-cross-above",
            message="Monitor BTC/USDT when price crosses above 50000 on 15m",
            formula="cross",
            operator="crosses_above",
            threshold=50000,
            timeframe="15m",
            movement_direction="up",
            include=("BTC/USDT",),
        ),
        LaunchCoreContract(
            id="fixed-cross-below",
            message="Monitor BTC/USDT when price crosses below 49000 on 15m",
            formula="cross",
            operator="crosses_below",
            threshold=49000,
            timeframe="15m",
            movement_direction="down",
            include=("BTC/USDT",),
        ),
        LaunchCoreContract(
            id="previous-candle-close",
            message=(
                "Monitor BTC/USDT when the 15m close is above "
                "the previous candle close"
            ),
            formula="previous_candle_reference",
            operator="gt",
            threshold=None,
            timeframe="15m",
            movement_direction="up",
            include=("BTC/USDT",),
        ),
        LaunchCoreContract(
            id="lookback-high",
            message=(
                "Monitor BTC/USDT when the 15m close is above the highest high "
                "of the previous 20 candles"
            ),
            formula="lookback_reference_level",
            operator="gt",
            threshold=None,
            timeframe="15m",
            movement_direction="up",
            include=("BTC/USDT",),
        ),
        LaunchCoreContract(
            id="sweep-below-reclaim",
            message=(
                "Monitor BTC/USDT when price sweeps below the previous candle low "
                "and reclaims it on 15m"
            ),
            formula="sweep_and_reclaim",
            operator="is_true",
            threshold=None,
            timeframe="15m",
            movement_direction="down",
            include=("BTC/USDT",),
        ),
        LaunchCoreContract(
            id="fixed-level-lt",
            message="Monitor BTC/USDT when price is below 48000 on 15m",
            formula="fixed_reference_level",
            operator="lt",
            threshold=48000,
            timeframe="15m",
            movement_direction="down",
            include=("BTC/USDT",),
        ),
        LaunchCoreContract(
            id="fixed-level-eq-bybit-usdc",
            message=(
                "Monitor ETH/USDC on Bybit when price is equal to 3500 on 1h"
            ),
            formula="fixed_reference_level",
            operator="eq",
            threshold=3500,
            timeframe="1h",
            movement_direction="neutral",
            include=("ETH/USDC",),
            exchange="bybit",
            quote_asset="USDC",
        ),
        LaunchCoreContract(
            id="context-role-separate-from-trigger",
            message=(
                "Monitor BTC/USDT using the 4h chart as context when the 15m candle "
                "rises open-to-close by at least 2%"
            ),
            formula="open_to_close_percentage",
            operator="gte",
            threshold=2,
            timeframe="15m",
            movement_direction="up",
            include=("BTC/USDT",),
            context=("4h",),
            reference="15m",
        ),
        LaunchCoreContract(
            id="confirmation-role-never-becomes-trigger",
            message=(
                "Monitor SOL/USDT when the 15m candle rises open-to-close by at "
                "least 2%, confirmed on the 1h"
            ),
            formula="open_to_close_percentage",
            operator="gte",
            threshold=2,
            timeframe="15m",
            movement_direction="up",
            include=("SOL/USDT",),
            confirmation=("1h",),
            reference="1h",
        ),
        LaunchCoreContract(
            id="reference-timeframe-differs-from-trigger",
            message="Monitor ETH/USDT when today's 1h move rises by at least 3%",
            formula="reference_to_current_percentage",
            operator="gte",
            threshold=3,
            timeframe="1h",
            movement_direction="up",
            include=("ETH/USDT",),
            reference="1d",
        ),
    ]


def run_launch_core(output_root: Path, *, run_id: str | None = None) -> tuple[dict[str, Any], Path]:
    actual_run_id = run_id or datetime.now(UTC).strftime("launch-core-%Y%m%dT%H%M%SZ")
    run_dir = output_root / actual_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    results = [_run_contract(contract) for contract in launch_core_contracts()]
    conversation_checks = _conversation_checks()
    structural_checks = _structural_checks()
    measured = len(results)
    passed = sum(result.passed for result in results)
    formula_accuracy = _accuracy(results, "formula")
    operator_accuracy = _accuracy(results, "operator")
    threshold_accuracy = _accuracy(results, "threshold")
    timeframe_accuracy = _accuracy(results, "timeframe_roles")
    universe_accuracy = _accuracy(results, "universe")
    movement_direction_accuracy = _accuracy(results, "movement_direction")
    strategy_bias_accuracy = _accuracy(results, "strategy_bias")
    latencies = sorted(result.elapsed_ms for result in results)
    p95_latency_ms = (
        latencies[min(len(latencies) - 1, max(0, round(0.95 * len(latencies)) - 1))]
        if latencies
        else 0.0
    )
    deterministic_latency_within_target = bool(latencies) and max(latencies) <= 2_000
    stable_pass = (
        measured > 0
        and passed == measured
        and all(conversation_checks.values())
        and all(structural_checks.values())
        and deterministic_latency_within_target
    )
    summary: dict[str, Any] = {
        "suite": "launch-core",
        "run_id": actual_run_id,
        "cases": measured,
        "passed": passed,
        "pass_rate": passed / measured if measured else 0,
        "semantic_accuracy": (
            sum(
                (
                    formula_accuracy,
                    operator_accuracy,
                    threshold_accuracy,
                    timeframe_accuracy,
                    universe_accuracy,
                    movement_direction_accuracy,
                    strategy_bias_accuracy,
                )
            )
            / 7
        ),
        "formula_accuracy": formula_accuracy,
        "operator_accuracy": operator_accuracy,
        "threshold_accuracy": threshold_accuracy,
        "timeframe_role_accuracy": timeframe_accuracy,
        "universe_accuracy": universe_accuracy,
        "movement_direction_accuracy": movement_direction_accuracy,
        "strategy_bias_accuracy": strategy_bias_accuracy,
        "grouping_accuracy": float(structural_checks["nested_ast_preserved"]),
        "correction_reversion_adherence": float(
            structural_checks["correction_and_reversion"]
        ),
        "approval_integrity": float(structural_checks["approval_integrity"]),
        "false_executable_rate": (
            0.0 if structural_checks["blocking_unsupported_not_executable"] else 1.0
        ),
        "model_calls": 0,
        "cost_usd": 0.0,
        "maximum_case_latency_ms": max(latencies, default=0.0),
        "p95_case_latency_ms": p95_latency_ms,
        "deterministic_latency_within_target": deterministic_latency_within_target,
        "stable_regression_status": "PASS" if stable_pass else "FAIL",
        "exploratory_status": "NOT_MEASURED",
        "critical_safety_status": "PASS" if stable_pass else "FAIL",
        "workflow_status": "PASS" if stable_pass else "FAIL",
        "infrastructure_status": "PASS",
        "conversation_non_mutation": conversation_checks,
        "structural_checks": structural_checks,
    }
    _write_reports(run_dir, results, summary)
    return summary, run_dir


def _run_contract(contract: LaunchCoreContract) -> LaunchCoreResult:
    from time import monotonic

    started = monotonic()
    errors: list[str] = []
    draft_payload: dict[str, Any] | None = None
    compiled_payload: dict[str, Any] | None = None
    checks = {
        "formula": False,
        "operator": False,
        "threshold": False,
        "timeframe": False,
        "timeframe_roles": False,
        "movement_direction": False,
        "strategy_bias": False,
        "universe": False,
        "schema": False,
        "semantic_invariants": False,
    }
    try:
        draft = StrategyDraftV2()
        patch = deterministic_strategy_patch(
            draft,
            contract.message,
            source_turn_id=f"launch-core-{contract.id}",
        )
        if patch is None:
            raise ValueError("deterministic patch was not produced")
        draft = apply_strategy_patch(draft, patch).draft
        draft_payload = draft.model_dump(mode="json")
        conditions = [
            node
            for node in (draft.condition_ast.walk() if draft.condition_ast else [])
            if node.node_type == ConditionNodeType.CONDITION
        ]
        if len(conditions) != 1:
            raise ValueError(f"expected one condition, received {len(conditions)}")
        condition = conditions[0]
        checks["formula"] = condition.formula is not None and (
            condition.formula.value == contract.formula
        )
        checks["operator"] = condition.operator is not None and (
            condition.operator.value == contract.operator
        )
        checks["threshold"] = condition.threshold == contract.threshold
        checks["timeframe"] = condition.trigger_timeframe == contract.timeframe
        # Every role is compared, not only the trigger. A contract that states no
        # supporting role must produce none: an invented context timeframe is as
        # wrong as a missing one.
        checks["timeframe_roles"] = (
            condition.trigger_timeframe == contract.timeframe
            and tuple(condition.context_timeframes) == contract.context
            and tuple(condition.confirmation_timeframes) == contract.confirmation
            and (
                contract.reference is None
                or condition.reference_timeframe == contract.reference
            )
        )
        checks["movement_direction"] = (
            condition.movement_direction.value == contract.movement_direction
        )
        checks["strategy_bias"] = condition.strategy_bias.value == contract.strategy_bias
        checks["universe"] = (
            tuple(draft.universe.included_symbols) == contract.include
            and tuple(draft.universe.excluded_symbols) == contract.exclude
            and draft.market_scope.exchange == contract.exchange
            and draft.market_scope.quote_asset == contract.quote_asset
        )
        semantic_errors = validate_draft_semantics(draft)
        if contract.expected_blocking_prefix is not None:
            checks["semantic_invariants"] = (
                len(semantic_errors) == 1
                and semantic_errors[0].startswith(contract.expected_blocking_prefix)
            )
            try:
                compile_strategy_draft_v2(draft)
            except StrategyV2CompileError as exc:
                checks["schema"] = (
                    exc.code == "semantic_validation_failed"
                    and contract.expected_blocking_prefix in str(exc)
                )
            else:
                errors.append("expected the draft to remain explicitly blocked")
        else:
            checks["semantic_invariants"] = not semantic_errors
            compiled = compile_strategy_draft_v2(draft)
            compiled_payload = compiled.model_dump(mode="json")
            checks["schema"] = True
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    for name, ok in checks.items():
        if not ok:
            errors.append(f"{name} contract failed")
    return LaunchCoreResult(
        contract=contract,
        passed=all(checks.values()),
        checks=checks,
        errors=tuple(errors),
        draft=draft_payload,
        compiled=compiled_payload,
        elapsed_ms=round((monotonic() - started) * 1000, 3),
    )


def _conversation_checks() -> dict[str, bool]:
    phrases = (
        "Hi, how are you?",
        "Yeah, let's not overcomplicate the BTC part.",
        "Not heavy formulas?",
        "No more questions.",
        "It ensures we're not accidentally mixing other pairs or data.",
    )
    return {
        phrase: decide_setup_intent(phrase).intent
        in {
            SetupIntent.CONVERSATION,
            SetupIntent.EXPLANATION_REQUEST,
            SetupIntent.PRODUCT_QUESTION,
        }
        for phrase in phrases
    }


def _structural_checks() -> dict[str, bool]:
    grouping_message = (
        "the 15m candle rises open-to-close by at least 2% AND "
        "(the 1h close-to-close move rises by at least 3% OR NOT "
        "the 4h high-to-low move drops by at least 4%)"
    )
    grouping = _draft_from_message(grouping_message, "launch-core-grouping")
    nested_ast_preserved = (
        grouping.condition_ast is not None
        and _ast_shape(grouping.condition_ast)
        == "and(condition,or(condition,not(condition)))"
    )

    base = _draft_from_message(
        "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%",
        "launch-core-correction-base",
    )
    correction_and_reversion = False
    if base.condition_ast is not None:
        replacement = base.condition_ast.model_copy(
            update={
                "source_turn_id": "launch-core-correction",
                "source_fragment": (
                    "Change the 15m open-to-close threshold to at least 8%"
                ),
                "threshold": 8.0,
            }
        )
        changed = apply_strategy_patch(
            base,
            StrategyPatch(
                source_turn_id="launch-core-correction",
                update_conditions=[
                    ConditionUpdateV2(
                        node_id=base.condition_ast.node_id,
                        replacement=replacement,
                    )
                ],
            ),
        ).draft
        reverted = apply_strategy_patch(
            changed,
            StrategyPatch(
                source_turn_id="launch-core-reversion",
                reversion=ReversionV2(target_version=base.executable_version),
            ),
            history=[base.model_dump(mode="json")],
        ).draft
        correction_and_reversion = (
            changed.condition_ast is not None
            and changed.condition_ast.threshold == 8
            and reverted.condition_ast is not None
            and reverted.condition_ast.threshold == 3
            and reverted.executable_version == changed.executable_version + 1
            and not reverted.approval.approved
        )

    approval_integrity = False
    try:
        approved = StrategyDraftV2.model_validate(
            base.model_copy(
                update={
                    "approval": ApprovalBindingV2(
                        approved=True,
                        user_id=uuid4(),
                        executable_version=base.executable_version,
                        executable_hash=base.executable_hash,
                        conversation_snapshot_hash="a" * 64,
                        approved_at=datetime.now(UTC),
                    )
                }
            ).model_dump(mode="json")
        )
        try:
            StrategyDraftV2.model_validate(
                base.model_copy(
                    update={
                        "approval": ApprovalBindingV2(
                            approved=True,
                            user_id=uuid4(),
                            executable_version=base.executable_version + 1,
                            executable_hash=base.executable_hash,
                            conversation_snapshot_hash="b" * 64,
                            approved_at=datetime.now(UTC),
                        )
                    }
                ).model_dump(mode="json")
            )
        except ValidationError:
            approval_integrity = approved.approval.approved
    except ValidationError:
        approval_integrity = False

    blocked = StrategyDraftV2.model_validate(
        base.model_copy(
            update={
                "unsupported_requirements": [
                    UnsupportedRequirementV2(
                        key="missing_exact_mechanic",
                        source_turn_id="launch-core-unsupported",
                        source_fragment="Use an unavailable exact mechanic",
                        missing_contract="No exact executable primitive is registered.",
                    )
                ],
                "workflow_state_hash": "",
            }
        ).model_dump(mode="json")
    )
    blocking_unsupported_not_executable = False
    try:
        compile_strategy_draft_v2(blocked)
    except StrategyV2CompileError as exc:
        blocking_unsupported_not_executable = exc.code == "draft_blocked"

    return {
        "nested_ast_preserved": nested_ast_preserved,
        "correction_and_reversion": correction_and_reversion,
        "approval_integrity": approval_integrity,
        "blocking_unsupported_not_executable": blocking_unsupported_not_executable,
        "no_unrequested_capabilities": all(
            not any(
                node.capability_key
                for node in draft.condition_ast.walk()
                if node.node_type == ConditionNodeType.CONDITION
            )
            for draft in (
                grouping,
                base,
            )
            if draft.condition_ast is not None
        ),
    }


def _draft_from_message(message: str, source_turn_id: str) -> StrategyDraftV2:
    draft = StrategyDraftV2()
    patch = deterministic_strategy_patch(
        draft,
        message,
        source_turn_id=source_turn_id,
    )
    if patch is None:
        raise ValueError(f"launch-core patch was not produced for {source_turn_id}")
    return apply_strategy_patch(draft, patch).draft


def _ast_shape(node: Any) -> str:
    if not node.children:
        return node.node_type.value
    return (
        f"{node.node_type.value}("
        + ",".join(_ast_shape(child) for child in node.children)
        + ")"
    )


def _accuracy(results: list[LaunchCoreResult], key: str) -> float:
    return sum(result.checks[key] for result in results) / len(results) if results else 0


def _write_reports(
    run_dir: Path,
    results: list[LaunchCoreResult],
    summary: dict[str, Any],
) -> None:
    serializable = [
        {
            **asdict(result),
            "contract": asdict(result.contract),
        }
        for result in results
    ]
    (run_dir / "results.json").write_text(
        json.dumps(serializable, indent=2),
        encoding="utf-8",
    )
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    rows = "\n".join(
        f"| {item.contract.id} | {'PASS' if item.passed else 'FAIL'} | "
        f"{item.elapsed_ms:.3f} | {', '.join(item.errors) or '-'} |"
        for item in results
    )
    markdown = (
        "# HilalMarkets Launch-Core Report\n\n"
        f"- Stable regression: **{summary['stable_regression_status']}**\n"
        f"- Cases: **{summary['passed']}/{summary['cases']}**\n"
        f"- Semantic accuracy: **{summary['semantic_accuracy']:.1%}**\n"
        f"- Structural checks: **{sum(summary['structural_checks'].values())}/"
        f"{len(summary['structural_checks'])}**\n"
        f"- Deterministic p95: **{summary['p95_case_latency_ms']:.3f} ms**\n"
        "- Model calls: **0**\n"
        "- Cost: **$0.00**\n\n"
        "| Contract | Status | ms | Errors |\n"
        "|---|---:|---:|---|\n"
        f"{rows}\n"
    )
    (run_dir / "report.md").write_text(markdown, encoding="utf-8")
    html_rows = "".join(
        "<tr>"
        f"<td>{html.escape(item.contract.id)}</td>"
        f"<td>{'PASS' if item.passed else 'FAIL'}</td>"
        f"<td>{item.elapsed_ms:.3f}</td>"
        f"<td>{html.escape(', '.join(item.errors) or '-')}</td>"
        "</tr>"
        for item in results
    )
    (run_dir / "report.html").write_text(
        (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>HilalMarkets Launch-Core Report</title>"
            "<style>body{font-family:system-ui;max-width:960px;margin:40px auto;"
            "color:#2b2e35}table{width:100%;border-collapse:collapse}"
            "th,td{padding:10px;border:1px solid #e1e5ea;text-align:left}"
            "h1{color:#0f5132}</style></head><body>"
            "<h1>HilalMarkets Launch-Core Report</h1>"
            f"<p><strong>{summary['stable_regression_status']}</strong> "
            f"{summary['passed']}/{summary['cases']} cases; "
            f"semantic accuracy {summary['semantic_accuracy']:.1%}; "
            "zero model calls and zero cost.</p>"
            "<table><thead><tr><th>Contract</th><th>Status</th><th>ms</th>"
            f"<th>Errors</th></tr></thead><tbody>{html_rows}</tbody></table>"
            "</body></html>"
        ),
        encoding="utf-8",
    )
