"""Replay an evaluator run's recorded user turns through the real interpreter.

This is the cheap regression probe behind `INVARIANT_REMEDIATION.md`. It calls no
model and no evaluator: it takes the user turns exactly as recorded in a run's
`cases.jsonl`, feeds them through the production request builder and compiler, and
reports what each conversation compiles to.

It answers the two questions the report cannot:

* does any turn still raise, which is what an HTTP 500 looks like from inside; and
* which findings still block approval, and which fragment produced each one.

Each turn is folded into the typed :class:`StrategyDraftState` and compiled from
`canonical_compiler_text`, because that is what the chat service does. An earlier
version of this script joined the accumulated user turns and compiled that blob
instead. That path only runs as a fallback, and it over-reported blocking findings
more than threefold — 154 against the 46 production actually produced — because every
superseded phrasing was recompiled on every later turn. `--raw` still selects it, for
measuring the fallback itself.

Usage::

    python scripts/replay_recorded_turns.py --run 20260727T081613Z
    python scripts/replay_recorded_turns.py --run 20260727T081613Z --scenario timeframe
    python scripts/replay_recorded_turns.py --run 20260727T081613Z --show-conditions
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_market_monitor.engine.strategy_state import (  # noqa: E402
    StrategyDraftState,
    canonical_compiler_text,
    is_reversion_request,
    patches_for_turn,
    revert_patches,
)
from ai_market_monitor.schemas.strategy import (  # noqa: E402
    Comparator,
    ConditionGroup,
    ConditionRule,
    StrategyDefinition,
    StrategyDirection,
)
from ai_market_monitor.services.ai_setup_chat import _guided_setup  # noqa: E402
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter  # noqa: E402

RUNS_DIR = REPO_ROOT / "chatbot_eval_runs"


def advance(state: StrategyDraftState, text: str) -> StrategyDraftState:
    """Fold one user turn into the typed state, exactly as the chat service does."""
    cleaned = " ".join(text.split())
    if not cleaned:
        return state
    if is_reversion_request(cleaned):
        reverted = revert_patches(state, source_text=cleaned)
        if reverted:
            return state.apply(reverted)
    return state.apply(patches_for_turn(cleaned, state))


def resolved_state(state: StrategyDraftState) -> dict[str, object]:
    """The settled values, JSON-safe, matching `_resolved_strategy_state`."""
    resolved: dict[str, object] = {}
    for name, value in state.resolved().items():
        if isinstance(value, StrategyDirection | Comparator):
            resolved[name] = value.value
        elif isinstance(value, tuple):
            resolved[name] = list(value)
        else:
            resolved[name] = value
    return resolved


def load_conversations(run_id: str, scenario: str | None) -> dict[str, list[str]]:
    """Recorded user turns per scenario, in order."""
    path = RUNS_DIR / run_id / "cases.jsonl"
    if not path.exists():
        raise SystemExit(f"No cases.jsonl for run {run_id!r} (looked in {path})")
    conversations: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        scenario_id = (case.get("scenario") or {}).get("id") or ""
        if scenario and scenario not in scenario_id:
            continue
        turns = [
            turn.get("text") or "" for turn in case.get("turns") or [] if turn.get("role") == "user"
        ]
        if turns:
            conversations.setdefault(scenario_id, turns)
    return conversations


def leaves(node: ConditionRule | ConditionGroup) -> list[ConditionRule]:
    if isinstance(node, ConditionGroup):
        return [leaf for child in node.children for leaf in leaves(child)]
    return [node]


def describe(strategy: StrategyDefinition) -> list[str]:
    rows = []
    for leaf in leaves(strategy.conditions):
        value = leaf.right.value if leaf.right is not None else None
        rows.append(f"{leaf.key} [{leaf.timeframe}] {leaf.comparator.value} {value}")
    return rows


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="run id under chatbot_eval_runs/")
    parser.add_argument("--scenario", default=None, help="substring filter on scenario id")
    parser.add_argument(
        "--show-conditions", action="store_true", help="print each compiled condition"
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        default=None,
        help="write a per-conversation summary here, for before/after comparison",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help=(
            "compile the joined accumulated turns instead of canonical state. "
            "This is only the fallback path; it over-reports findings."
        ),
    )
    args = parser.parse_args()
    summary: dict[str, dict[str, object]] = {}

    conversations = load_conversations(args.run, args.scenario)
    if not conversations:
        raise SystemExit("No matching conversations.")

    interpreter = RuleBasedStrategyInterpreter()
    crashes = 0
    tally: collections.Counter[str] = collections.Counter()

    for scenario_id, turns in conversations.items():
        print("=" * 92)
        print(scenario_id)
        history: list[str] = []
        state = StrategyDraftState()
        for index, text in enumerate(turns, start=1):
            history.append(text)
            state = advance(state, text)
            try:
                if args.raw:
                    request = _guided_setup("\n".join(history))
                else:
                    settled = resolved_state(state)
                    request = _guided_setup(
                        canonical_compiler_text(settled, fallback="\n".join(history)),
                        resolved_state=settled,
                    )
                preview = await interpreter.interpret(request)
            except Exception as exc:  # noqa: BLE001 - reproducing the production failure
                crashes += 1
                print(f"  u{index}: RAISED (this is what the HTTP 500 was)")
                traceback.print_exc()
                summary[scenario_id] = {
                    "crashed_at_turn": index,
                    "error": type(exc).__name__,
                    "blocking": None,
                    "conditions": None,
                }
                break
            blocking = [issue for issue in preview.unsupported_conditions if issue.blocking]
            if index == len(turns):
                # Only the final turn is tallied: that is the state the trader is
                # actually left in, and it is what decides approval eligibility.
                for issue in blocking:
                    tally[issue.code] += 1
            print(
                f"  u{index}: chars={len(request.setup_text or ''):>5} "
                f"conditions={len(leaves(preview.strategy.conditions)):>2} "
                f"blocking={len(blocking):>2}"
            )
            if index == len(turns):
                for issue in blocking:
                    print(f"        BLOCK {issue.code}: {(issue.source_fragment or '')[:70]!r}")
                if args.show_conditions:
                    for row in describe(preview.strategy):
                        print(f"        RULE  {row}")
                summary[scenario_id] = {
                    "crashed_at_turn": None,
                    "error": None,
                    "blocking": len(blocking),
                    "blocking_codes": sorted(issue.code for issue in blocking),
                    "conditions": len(leaves(preview.strategy.conditions)),
                }

    print("=" * 92)
    print(f"conversations: {len(conversations)}   crashes: {crashes}")
    print(f"blocking findings left at the end of the conversation: {sum(tally.values())}")
    for code, count in tally.most_common():
        print(f"  {count:>3}  {code}")
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"wrote {args.json_path}")
    return 1 if crashes else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
