from __future__ import annotations

import re
from dataclasses import dataclass

from ai_market_monitor.engine.capabilities import CapabilitySpec, all_capabilities

_REQUIRED_ALIAS_CONTEXT: dict[str, tuple[str, ...]] = {
    "support_retest": ("support",),
    "resistance_retest": ("resistance",),
    "weekly_high_low": ("weekly", "week"),
}


@dataclass(frozen=True, slots=True)
class CapabilityMatch:
    capability: CapabilitySpec
    phrase: str
    start: int
    end: int


def normalized_phrases(capability: CapabilitySpec) -> tuple[str, ...]:
    values = (
        capability.key.replace("_", " "),
        capability.label.casefold(),
        *capability.aliases,
    )
    return tuple(dict.fromkeys(value.casefold().strip() for value in values if value.strip()))


def find_capability_matches(
    text: str,
    *,
    executable_only: bool = True,
) -> list[CapabilityMatch]:
    normalized = text.casefold()
    matches: list[CapabilityMatch] = []
    for capability in all_capabilities():
        if executable_only and not capability.executable:
            continue
        candidates = []
        for phrase in normalized_phrases(capability):
            match = re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized)
            if match:
                candidates.append((match.start(), match.end(), phrase))
        if not candidates:
            continue
        start, end, phrase = min(candidates, key=lambda item: (item[0], -(item[1] - item[0])))
        required_context = _REQUIRED_ALIAS_CONTEXT.get(capability.key)
        if required_context and not any(term in phrase for term in required_context):
            continue
        matches.append(CapabilityMatch(capability, phrase, start, end))
    return sorted(matches, key=lambda item: (item.start, -(item.end - item.start)))
