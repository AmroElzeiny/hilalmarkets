from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import ValidationError

from ai_market_monitor.engine.capability_compatibility import compatibility_by_key
from ai_market_monitor.engine.condition_registry import condition_registry_payload
from ai_market_monitor.schemas.strategy import ConditionRule, StrategyDefinition

MATRIX_COLUMNS = (
    "capability_key",
    "display_label",
    "category",
    "builder_category",
    "aliases_prompt_phrases",
    "free_plan_allowed",
    "light_mode_allowed",
    "provider_required",
    "availability",
    "executable_flag",
    "implementation_status",
    "operand_kind",
    "operand_name",
    "default_comparator",
    "default_parameters",
    "supported_comparators",
    "supported_timeframes",
    "condition_template_generation_status",
    "schema_validation_status",
    "prompt_alias_match_status",
    "manual_builder_add_status",
    "evaluator_support_status",
    "market_data_requirement",
    "preview_scan_support",
    "live_scanner_support",
    "proof_receipt_support",
    "dashboard_rendering_support",
    "telegram_discord_rendering_support",
    "current_status",
    "reason",
    "fix_needed",
)


def concept_e2e_rows() -> list[dict[str, Any]]:
    compatibility = compatibility_by_key()
    rows: list[dict[str, Any]] = []
    for item in condition_registry_payload(include_provider_required=True)["items"]:
        key = item["key"]
        template = item.get("condition_template") or {}
        compatibility_row = compatibility.get(key)
        condition_status, condition_error = _condition_template_status(template)
        strategy_status, strategy_error = _strategy_schema_status(template)
        availability = item.get("availability", "unknown")
        evaluator_supported = bool(
            compatibility_row and compatibility_row.evaluator_supported
        )
        prompt_aliases = item.get("prompt_aliases") or []
        provider_required = bool(
            item.get("provider_required") or availability == "provider_required"
        )
        manual_add = (
            "addable"
            if availability == "available" and item.get("executable", True)
            else "draft_only"
            if provider_required
            else "hidden"
        )
        status, reason, fix_needed = _status(
            item=item,
            availability=availability,
            provider_required=provider_required,
            condition_status=condition_status,
            strategy_status=strategy_status,
            evaluator_supported=evaluator_supported,
            prompt_alias_count=len(prompt_aliases),
            condition_error=condition_error,
            strategy_error=strategy_error,
        )
        scanner_support = "yes" if status == "GREEN" else "blocked" if provider_required else "no"
        rows.append(
            {
                "capability_key": key,
                "display_label": item.get("display_name") or item.get("label"),
                "category": item.get("category"),
                "builder_category": item.get("builder_category"),
                "aliases_prompt_phrases": ", ".join(prompt_aliases),
                "free_plan_allowed": item.get("free_plan"),
                "light_mode_allowed": item.get("light_mode"),
                "provider_required": item.get("provider_required") or "",
                "availability": availability,
                "executable_flag": item.get("executable"),
                "implementation_status": item.get("implementation_status"),
                "operand_kind": item.get("operand_kind"),
                "operand_name": item.get("operand_name"),
                "default_comparator": item.get("default_comparator"),
                "default_parameters": item.get("default_parameters"),
                "supported_comparators": ", ".join(item.get("supported_comparators") or []),
                "supported_timeframes": ", ".join(item.get("supported_timeframes") or []),
                "condition_template_generation_status": condition_status,
                "schema_validation_status": strategy_status,
                "prompt_alias_match_status": "mapped" if prompt_aliases else "missing_alias",
                "manual_builder_add_status": manual_add,
                "evaluator_support_status": "supported" if evaluator_supported else "unsupported",
                "market_data_requirement": ", ".join(item.get("required_data") or []),
                "preview_scan_support": scanner_support,
                "live_scanner_support": scanner_support,
                "proof_receipt_support": (
                    "yes"
                    if status == "GREEN"
                    else "unavailable_proof_only"
                    if provider_required
                    else "no"
                ),
                "dashboard_rendering_support": (
                    "yes" if template and manual_add != "hidden" else "hidden"
                ),
                "telegram_discord_rendering_support": (
                    "proof_renderer" if status in {"GREEN", "PROVIDER_REQUIRED"} else "not_live"
                ),
                "current_status": status,
                "reason": reason,
                "fix_needed": fix_needed,
            }
        )
    return rows


def matrix_status_counts(rows: list[dict[str, Any]] | None = None) -> Counter[str]:
    return Counter(row["current_status"] for row in (rows or concept_e2e_rows()))


def matrix_markdown(rows: list[dict[str, Any]] | None = None) -> str:
    rows = rows or concept_e2e_rows()
    counts = matrix_status_counts(rows)
    lines = [
        "# Trading Concept End-to-End Matrix",
        "",
        "Generated from `condition_registry_payload(include_provider_required=True)` and compatibility/schema checks.",
        "",
        "## Summary",
        "",
        *[f"- {status}: {counts.get(status, 0)}" for status in _status_order(counts)],
        "",
        "## Matrix",
        "",
        "|" + "|".join(MATRIX_COLUMNS) + "|",
        "|" + "|".join("---" for _ in MATRIX_COLUMNS) + "|",
    ]
    for row in rows:
        lines.append("|" + "|".join(_cell(row.get(column)) for column in MATRIX_COLUMNS) + "|")
    lines.append("")
    return "\n".join(lines)


def _condition_template_status(template: dict[str, Any]) -> tuple[str, str | None]:
    try:
        ConditionRule.model_validate(template)
    except (ValidationError, ValueError, TypeError) as exc:
        return "invalid", str(exc).splitlines()[0]
    return "valid", None


def _strategy_schema_status(template: dict[str, Any]) -> tuple[str, str | None]:
    try:
        condition = ConditionRule.model_validate(template)
        StrategyDefinition.model_validate(
            {
                "name": f"Audit {condition.key}",
                "base_timeframe": condition.timeframe,
                "supporting_timeframes": [],
                "trigger_mode": "candle_close",
                "universe": {
                    "exchange": "binance",
                    "market_type": "spot",
                    "quote_currencies": ["USDT"],
                    "min_historical_candles": 1,
                },
                "conditions": {
                    "key": "entry_conditions",
                    "operator": "and",
                    "children": [condition.model_dump(mode="json")],
                },
                "entry": {"calculation": "signal_close"},
                "risk": {
                    "enabled": False,
                    "stop_method": "structure",
                    "target_method": "risk_multiple",
                },
                "alerts": {"channels": ["web"]},
            }
        )
    except (ValidationError, ValueError, TypeError) as exc:
        return "invalid", str(exc).splitlines()[0]
    return "valid", None


def _status(
    *,
    item: dict[str, Any],
    availability: str,
    provider_required: bool,
    condition_status: str,
    strategy_status: str,
    evaluator_supported: bool,
    prompt_alias_count: int,
    condition_error: str | None,
    strategy_error: str | None,
) -> tuple[str, str, str]:
    if provider_required:
        return (
            "PROVIDER_REQUIRED",
            "Requires external/runtime provider data and must block mandatory activation.",
            "Configure provider placeholders and keep mandatory live activation blocked.",
        )
    if availability in {"planned", "experimental"}:
        return ("PLANNED", f"Availability is {availability}.", "Keep hidden or draft-only.")
    if availability == "unsupported" or item.get("executable") is False:
        return (
            "PLANNED",
            "Concept is recognized but hidden from executable builder paths.",
            "Implement evaluator support or keep labeled as unsupported.",
        )
    if condition_status != "valid":
        return ("RED", condition_error or "Invalid condition template.", "Fix builder template.")
    if strategy_status != "valid":
        return ("RED", strategy_error or "Invalid StrategyDefinition.", "Fix schema mapping.")
    if not evaluator_supported:
        return (
            "RED",
            "Marked available but compatibility check cannot prove evaluator support.",
            "Implement evaluator support or downgrade availability.",
        )
    if prompt_alias_count <= 0:
        return (
            "YELLOW",
            "Evaluator and builder work, but prompt reachability is weak.",
            "Add trader-language aliases and prompt tests.",
        )
    return ("GREEN", "Available through builder schema, evaluator, scanner proof path.", "None.")


def _status_order(counts: Counter[str]) -> list[str]:
    ordered = ["GREEN", "YELLOW", "RED", "PROVIDER_REQUIRED", "PLANNED"]
    return [status for status in ordered if status in counts]


def _cell(value: Any) -> str:
    if isinstance(value, dict):
        value = ", ".join(f"{key}={inner}" for key, inner in sorted(value.items()))
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(item) for item in value)
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", " ")[:500]
