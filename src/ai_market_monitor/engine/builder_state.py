"""The Guided Builder's view of a draft, read from the draft and nothing else.

This is what makes the two surfaces agree. A rule the assistant wrote and a rule a
person clicked together are the same stored object, so the Builder describes both the
same way, and the next assistant turn reads whatever the Builder last wrote. There is no
second copy of the strategy anywhere, and nothing here is recomputed from a transcript.

It is a pure read: it never mutates, never calls a provider, and never decides approval.
"""

from __future__ import annotations

from typing import Any

from ai_market_monitor.engine.builder_operations import (
    condition_nodes,
    current_join,
    describe_condition,
)
from ai_market_monitor.engine.setup_lifecycle import blocking_state
from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2

#: Each guided step, in the order a person walks them, with the words on the tab.
STEP_LABELS: tuple[tuple[str, str], ...] = (
    ("mode", "What to build"),
    ("assets", "Which coins"),
    ("conditions", "Your rules"),
    ("logic", "How they combine"),
    ("review", "Review"),
    ("market", "Market data"),
    ("approval", "Approve"),
)


def builder_state(draft: StrategyDraftV2, *, editable: bool = True) -> dict[str, Any]:
    """Describe one draft as the guided Builder shows it."""

    nodes = condition_nodes(draft.condition_ast)
    conditions = [describe_condition(node).to_dict() for node in nodes]
    policy = draft.sharia_policy
    return {
        "mode": draft.mode.value,
        "name": draft.name,
        "universe_mode": policy.universe_mode.value if policy.universe_mode else None,
        "universe_summary": _universe_summary(draft),
        "methodology_summary": _methodology_summary(draft),
        "conditions": conditions,
        "join": current_join(draft.condition_ast),
        "steps": _steps(draft, nodes_count=len(nodes)),
        "open_questions": [
            {
                "key": item.unresolved_id,
                "question": item.question,
                "reason": item.reason,
                "options": list(item.allowed_options),
                "blocking": item.blocking,
            }
            for item in draft.unresolved_fields
        ],
        "unsupported": [
            {
                "key": item.key,
                "missing": item.missing_contract,
                "quote": item.source_fragment,
                "blocking": item.blocking,
            }
            for item in draft.unsupported_requirements
        ],
        "provider_requirements": [
            {
                "provider": item.provider,
                "capability": item.capability,
                "status": _provider_status(draft, item.provider, item.capability),
            }
            for item in draft.static_provider_requirements
        ],
        "editable": editable,
    }


def _provider_status(draft: StrategyDraftV2, provider: str, capability: str) -> str:
    for item in draft.runtime_state.provider_status:
        if item.provider == provider and item.capability == capability:
            return item.status
    return "unknown"


def _universe_summary(draft: StrategyDraftV2) -> str | None:
    """Which coins this watches, in one sentence.

    Says what is *chosen*, never whether anything is halal. Sharia status comes from the
    platform's governed screening, and a summary line is not allowed to imply one.
    """

    policy = draft.sharia_policy
    mode = policy.universe_mode.value if policy.universe_mode else None
    if mode == "approved_watchlist":
        return (
            "One of your Favorites lists."
            if policy.approved_watchlist_id
            else "A Favorites list — you still need to pick which one."
        )
    if mode == "explicit_assets":
        count = len(policy.explicit_symbols)
        if not count:
            return "Coins you name yourself — none typed in yet."
        return f"{count} coin{'s' if count != 1 else ''} you named."
    if mode == "eligible_market":
        return "Every coin that passes the screening."
    return None


def _methodology_summary(draft: StrategyDraftV2) -> str | None:
    policy = draft.sharia_policy
    if policy.methodology_id is None:
        return None
    return f"Screening method version {policy.methodology_version}."


def _steps(draft: StrategyDraftV2, *, nodes_count: int) -> list[dict[str, Any]]:
    """Which guided steps are done, and what each unfinished one still needs.

    Completeness is read from the draft, not remembered from what the person clicked. A
    step that a chat message completed shows as done in the Builder without anybody
    telling the Builder about it.
    """

    policy = draft.sharia_policy
    universe_open = any(
        item.blocking and item.unresolved_id.startswith("sharia.")
        for item in draft.unresolved_fields
    )
    universe_chosen = policy.universe_mode is not None and not universe_open
    blocking = blocking_state(draft)
    providers_ok = all(
        _provider_status(draft, item.provider, item.capability) != "unavailable"
        for item in draft.static_provider_requirements
    )
    approved = (
        draft.approval.approved
        and draft.approval.executable_version == draft.executable_version
        and draft.approval.executable_hash == draft.executable_hash
    )
    done: dict[str, tuple[bool, str | None]] = {
        # A mode is always set, so this step is never the thing holding somebody up.
        "mode": (True, None),
        "assets": (
            universe_chosen,
            None if universe_chosen else "Choose which coins this should watch.",
        ),
        "conditions": (
            nodes_count > 0,
            None if nodes_count else "Add at least one rule to watch for.",
        ),
        "logic": (
            nodes_count <= 1 or bool(current_join(draft.condition_ast)),
            None
            if nodes_count <= 1 or current_join(draft.condition_ast)
            else "Choose whether all of your rules must match, or any one of them.",
        ),
        "review": (
            blocking is None,
            None if blocking is None else "A few things still need your answer.",
        ),
        "market": (
            providers_ok,
            None if providers_ok else "One rule needs market data this account cannot use.",
        ),
        "approval": (
            approved,
            None if approved else "Read it through, then approve to start watching.",
        ),
    }
    return [
        {
            "key": key,
            "label": label,
            "complete": done[key][0],
            "todo": done[key][1],
        }
        for key, label in STEP_LABELS
    ]
