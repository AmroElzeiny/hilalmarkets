from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import asdict

from .models import ScenarioSpec, TopicSpec

SYMBOLS = ["BTCUSDT", "ETHUSDT", "LTCUSDT", "SOLUSDT", "ADAUSDT", "XRPUSDT"]
TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]
PERSONAS = [
    {
        "type": "methodical swing trader",
        "experience": "advanced",
        "style": "precise but revises details",
    },
    {"type": "busy scalper", "experience": "intermediate", "style": "short fragmented messages"},
    {"type": "ICT-influenced trader", "experience": "advanced", "style": "uses domain shorthand"},
    {
        "type": "new trader",
        "experience": "beginner",
        "style": "describes visual ideas, not formulas",
    },
    {"type": "price-action trader", "experience": "intermediate", "style": "casual and skeptical"},
]


def build_scenario(
    topic: TopicSpec, index: int, global_seed: int, max_turns: int | None = None
) -> ScenarioSpec:
    seed = int.from_bytes(f"{global_seed}:{topic.id}:{index}".encode(), "little") % (2**31 - 1)
    rng = random.Random(seed)
    symbol = rng.choice(SYMBOLS)
    alt_symbol = rng.choice([x for x in SYMBOLS if x != symbol])
    timeframe = rng.choice(TIMEFRAMES)
    context_tf = rng.choice([x for x in TIMEFRAMES if x != timeframe])
    threshold = rng.choice([0.5, 1.0, 2.5, 5.0, 7.5])
    direction_word, direction = rng.choice([("bullish", "long"), ("bearish", "short")])
    operator_word, operator = rng.choice([("at least", "gte"), ("at most", "lte")])
    expected = {
        "symbol": symbol,
        "excluded_symbol": alt_symbol,
        "timeframe": timeframe,
        "context_timeframe": context_tf,
        "threshold_percent": threshold,
        "direction": direction,
        "operator": operator,
        "requires_explicit_approval": True,
        "must_not_assign_sharia_status": True,
    }
    if topic.id in {"confirmation_integrity", "version_immutability"}:
        corrected_threshold = rng.choice(
            [candidate for candidate in (0.5, 1.0, 2.5, 5.0, 7.5) if candidate != threshold]
        )
        expected["workflow"] = {
            "kind": "approval_rebind",
            "material_edit": {
                "field": "threshold_percent",
                "from": threshold,
                "to": corrected_threshold,
            },
            "final_expected": {"threshold_percent": corrected_threshold},
        }
    goal = (
        f"Build a watchlist for {symbol}, exclude {alt_symbol}, use {context_tf} context and {timeframe} "
        f"trigger logic, require a {direction_word} move of {operator_word} {threshold}%, "
        "and keep approval explicit."
    )
    return ScenarioSpec(
        id=f"{topic.id}-{index:03d}-{seed}",
        topic_id=topic.id,
        seed=seed,
        persona=rng.choice(PERSONAS),
        hidden_goal=goal,
        expected_contract=expected,
        success_criteria=[asdict(c) for c in topic.criteria],
        max_turns=max_turns or topic.max_turns,
        fault=topic.fault,
    )


def build_randomized_scenario_plan(
    topics: Sequence[TopicSpec],
    *,
    count_per_topic: int,
    global_seed: int,
    selection_seed: int,
    max_turns_by_topic: Mapping[str, int] | None = None,
) -> list[ScenarioSpec]:
    """Build a reproducible random sample instead of reusing the same prefix."""
    if count_per_topic < 1:
        raise ValueError("count_per_topic must be positive")

    rng = random.Random(selection_seed)
    scenarios: list[ScenarioSpec] = []
    for topic in topics:
        pool_size = max(topic.max_cases, count_per_topic)
        indexes = rng.sample(range(1, pool_size + 1), k=count_per_topic)
        for index in indexes:
            scenarios.append(
                build_scenario(
                    topic,
                    index,
                    global_seed,
                    max_turns=(max_turns_by_topic or {}).get(topic.id),
                )
            )
    rng.shuffle(scenarios)
    return scenarios
