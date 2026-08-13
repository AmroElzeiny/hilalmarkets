"""Reproduce every OI-4 finding in one command. Free, offline, no model call.

    .venv/Scripts/python tools/oi/reproduce_findings.py

Each block prints the before and after of the product's own canonical draft state, so a
reader can check the claim without taking this report's word for anything. The findings
themselves are written up in ``docs/OI_ADVERSARIAL_QA_RUN.md``.

This file lives in ``tools/oi/`` rather than ``src/hm_oi/`` because it imports the
product, and ``scripts/check_oi_boundary.py`` refuses that import inside ``src/hm_oi``.

**It fixes nothing.** It only shows.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ai_market_monitor.engine.capability_resolver import CapabilityResolver
from ai_market_monitor.engine.strategy_state import StrategyDraftState, patches_for_turn


def replay(history: str, prompt: str, field: str) -> tuple[object, object]:
    state = StrategyDraftState()
    state = state.apply(patches_for_turn(history, state, turn=1))
    before = state.value(field)
    state = state.apply(patches_for_turn(prompt, state, turn=2))
    return before, state.value(field)


print("OI4-001  a question changes the monitored timeframe")
b, a = replay(
    "Watch RSI below 30 on 1h.",
    "What does RSI 30 mean, and is 15m the same as 15 minutes?",
    "base_timeframe",
)
print(f"         before={b!r}  after={a!r}   <- the trader asked a question")

print()
print("OI4-003  a rejected timeframe becomes the timeframe")
for phrasing in ("Not 15m.", "Never use 15m.", "Don't use 15m.", "Anything but 15m.", "No 15m."):
    b, a = replay("Watch RSI below 30 on 1h.", phrasing, "base_timeframe")
    print(f"         {phrasing:<20} before={b!r}  after={a!r}")

print()
print("OI4-004  a rejected direction becomes the direction")
for phrasing in ("Not short.", "Never use short.", "Don't use short."):
    b, a = replay("Watch RSI below 30 on 1h for long setups.", phrasing, "direction")
    print(f"         {phrasing:<20} before={b!r}  after={a!r}")

print()
print("OI4-005  a rejected symbol is included instead of excluded")
for phrasing in ("Not BTCUSDT.", "Never use BTCUSDT.", "Anything but BTCUSDT."):
    state = StrategyDraftState()
    state = state.apply(patches_for_turn("Watch RSI below 30 on 1h.", state, turn=1))
    state = state.apply(patches_for_turn(phrasing, state, turn=2))
    print(
        f"         {phrasing:<22} include={state.value('include_symbols')} "
        f"exclude={state.value('exclude_symbols')}"
    )

print()
print(
    "OI4-006  capability resolution finds nothing in Arabic, "
    "so the model's enum guard is dropped"
)
for label, prompt in (
    ("english ", "Alert me when RSI drops below 30 on 15m"),
    ("arabic  ", "راقب مؤشر RSI تحت 30 على فريم 15 دقيقة"),
    ("egyptian", "نبهني لما الـ RSI ينزل تحت 30 على 15 دقيقة"),
    ("arabizi ", "3ayez alert lama el RSI yenzel ta7t 30 3ala 15 de2i2a"),
):
    keys = list(CapabilityResolver().resolve_prompt(prompt).candidate_keys)
    guard = f"{len(keys)} allowed keys" if keys else "NO ENUM - the model may name anything"
    print(f"         {label} candidates={keys}  ->  capability_key enum: {guard}")

print()
print("OI4-007  an unsupported request leaves a value on the draft instead of a refusal")
for label, prompt in (
    ("leverage      ", "Watch BTCUSDT with 10x leverage on 15m."),
    ("stop/take      ", "Set a stop loss at 2% and take profit at 5%."),
    ("shares and fx  ", "Watch Apple stock and EURUSD when RSI is under 30."),
    ("buy advice     ", "Just tell me which coin to buy right now."),
):
    state = StrategyDraftState()
    patches = patches_for_turn(prompt, state, turn=1)
    kept = {
        p.field: p.value
        for p in patches
        if p.field not in {"mechanic_fragments", "boolean_groups", "formula_fragments"}
    }
    print(f"         {label} {prompt!r}\n                        draft took {kept}")
