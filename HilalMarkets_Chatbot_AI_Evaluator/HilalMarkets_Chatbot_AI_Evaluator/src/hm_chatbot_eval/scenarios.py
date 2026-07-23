from __future__ import annotations

import random
from dataclasses import asdict

from .models import ScenarioSpec, TopicSpec

SYMBOLS = ["BTCUSDT", "ETHUSDT", "LTCUSDT", "SOLUSDT", "ADAUSDT", "XRPUSDT"]
TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]
PERSONAS = [
    {"type": "methodical swing trader", "experience": "advanced", "style": "precise but revises details"},
    {"type": "busy scalper", "experience": "intermediate", "style": "short fragmented messages"},
    {"type": "ICT-influenced trader", "experience": "advanced", "style": "uses domain shorthand"},
    {"type": "new trader", "experience": "beginner", "style": "describes visual ideas, not formulas"},
    {"type": "price-action trader", "experience": "intermediate", "style": "casual and skeptical"},
]


def build_scenario(topic: TopicSpec, index: int, global_seed: int, max_turns: int | None = None) -> ScenarioSpec:
    seed = int.from_bytes(f"{global_seed}:{topic.id}:{index}".encode(), "little") % (2**31 - 1)
    rng = random.Random(seed)
    symbol = rng.choice(SYMBOLS)
    alt_symbol = rng.choice([x for x in SYMBOLS if x != symbol])
    timeframe = rng.choice(TIMEFRAMES)
    context_tf = rng.choice([x for x in TIMEFRAMES if x != timeframe])
    threshold = rng.choice([0.5, 1.0, 2.5, 5.0, 7.5])
    direction = rng.choice(["bullish", "bearish"])
    operator = rng.choice(["greater_than_or_equal", "less_than_or_equal"])
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
    goal = (
        f"Build a watchlist for {symbol}, exclude {alt_symbol}, use {context_tf} context and {timeframe} "
        f"trigger logic, require a {threshold}% {direction} move, and keep approval explicit. "
        f"Topic-specific objective: {topic.objective}"
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
