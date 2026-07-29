from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .models import TopicSpec

# Backend coverage stays broad while browser coverage focuses on behavior that can
# diverge between the API contract and the rendered Setup Chat / Strategy Canvas.
BUDGET_UI_TOPIC_IDS = frozenset(
    {
        "approval_bypass",
        "assumptions_visibility",
        "canvas_grouping_fidelity",
        "canvas_node_completeness",
        "draft_recovery",
        "duplicate_idempotency",
        "msa_arabic",
        "nested_boolean_logic",
        "source_node_traceability",
        "state_refresh_persistence",
        "timeout_recovery",
        "ui_backend_parity",
    }
)

BUDGET_EIGHT_TURN_TOPIC_IDS = frozenset(
    {
        "context_compaction",
        "cross_turn_binding",
        "delayed_fact_recall",
        "long_context_retention",
        "repeated_correction_cycles",
        "confirmation_integrity",
        "version_immutability",
    }
)

BUDGET_SIX_TURN_TOPIC_IDS = frozenset(
    {
        "contradiction_resolution",
        "conversation_isolation",
        "draft_recovery",
        "indirect_prompt_injection",
        "model_version_drift",
        "revert_correction",
        "state_refresh_persistence",
    }
)


def topics_for_mode(mode: str, topics: Iterable[TopicSpec]) -> list[TopicSpec]:
    selected = list(topics)
    if mode == "smoke":
        return [topic for topic in selected if topic.severity == "critical"][:12]
    if mode in {"budget", "standard", "full"}:
        return selected
    raise ValueError("mode must be smoke, budget, standard or full")


def cases_per_topic(mode: str, tests_per_topic: int) -> int:
    if mode in {"smoke", "budget"}:
        return 1
    if mode == "standard":
        return min(5, tests_per_topic)
    if mode == "full":
        if not 20 <= tests_per_topic <= 30:
            raise ValueError("Full mode requires 20-30 tests per topic")
        return tests_per_topic
    raise ValueError("mode must be smoke, budget, standard or full")


def max_turns_for_topic(mode: str, topic: TopicSpec) -> int:
    if mode == "smoke":
        return min(topic.max_turns, 4)
    if mode != "budget":
        return topic.max_turns
    if topic.id in BUDGET_EIGHT_TURN_TOPIC_IDS:
        return min(topic.max_turns, 8)
    if topic.id in BUDGET_SIX_TURN_TOPIC_IDS:
        return min(topic.max_turns, 6)
    return min(topic.max_turns, 4)


def target_kinds_for_topic(
    mode: str,
    requested_kinds: Iterable[str],
    topic: TopicSpec,
) -> list[str]:
    requested = list(requested_kinds)
    if mode != "budget" or set(requested) != {"backend", "ui"}:
        return requested
    kinds = ["backend"]
    if topic.id in BUDGET_UI_TOPIC_IDS:
        kinds.append("ui")
    return kinds


def repeats_for_topic(topic: TopicSpec) -> int:
    return 2 if topic.id == "reproducibility" else 1


def variants_for_topic(
    mode: str,
    variants: Iterable[dict[str, Any]],
    topic: TopicSpec,
) -> list[dict[str, Any]]:
    configured = list(variants)
    if mode == "budget" and topic.id != "model_version_drift":
        return configured[:1]
    return configured
