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
    """Recorded user turns per scenario, in order.

    Two recording shapes exist, and this reads both.

    The **older** one stored each user turn under ``turns``. The probe folded them one at
    a time, which is what the chat service does.

    The **current** one stores ``canonical_state`` — the settled canonical text the setup
    chat actually compiles — plus ``source_scenario_id``. The raw turns are gone, but what
    remains is closer to production, not further from it: it is the exact string the
    compiler is given. It is replayed as a single final turn.

    Reading only the older shape is why this probe silently matched nothing against every
    run recorded since the canonical-state change: it exited with "No matching
    conversations" and a non-zero code, which reads like a broken run rather than an
    unreadable file.
    """
    path = RUNS_DIR / run_id / "cases.jsonl"
    if not path.exists():
        available = sorted(item.parent.name for item in RUNS_DIR.glob("*/cases.jsonl"))
        raise SystemExit(
            f"No cases.jsonl for run {run_id!r} (looked in {path}).\n"
            f"Runs that have one: {', '.join(available) or 'none'}"
        )
    conversations: dict[str, list[str]] = {}
    skipped_without_text = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        scenario_id = (
            (case.get("scenario") or {}).get("id")
            or case.get("source_scenario_id")
            or case.get("topic_id")
            or ""
        )
        if scenario and scenario not in scenario_id:
            continue
        turns = [
            turn.get("text") or ""
            for turn in case.get("turns") or []
            if isinstance(turn, dict) and turn.get("role") == "user"
        ]
        if turns:
            conversations.setdefault(scenario_id, turns)
        else:
            skipped_without_text += 1
    if skipped_without_text:
        print(
            f"note: {skipped_without_text} recorded case(s) carry a draft, not raw turns; "
            "use --drafts to replay those through the V2 compiler",
            file=sys.stderr,
        )
    return conversations


def load_drafts(run_id: str, scenario: str | None) -> dict[str, dict]:
    """Recorded canonical drafts per scenario.

    The current recording shape stores ``canonical_state``: the serialised draft the
    setup chat had built when the case ended. Compiling that is the closest deterministic
    check available to what production does, and it needs no model call.
    """
    path = RUNS_DIR / run_id / "cases.jsonl"
    if not path.exists():
        available = sorted(item.parent.name for item in RUNS_DIR.glob("*/cases.jsonl"))
        raise SystemExit(
            f"No cases.jsonl for run {run_id!r} (looked in {path}).\n"
            f"Runs that have one: {', '.join(available) or 'none'}"
        )
    drafts: dict[str, dict] = {}
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        case = json.loads(line)
        scenario_id = (
            case.get("source_scenario_id")
            or (case.get("scenario") or {}).get("id")
            or case.get("topic_id")
            or f"case-{index}"
        )
        if scenario and scenario not in scenario_id:
            continue
        canonical = case.get("canonical_state")
        if isinstance(canonical, dict) and canonical:
            drafts.setdefault(scenario_id, canonical)
    return drafts


#: Fields only the pre-V2 `StrategyDraftState` had. Their presence identifies a recording
#: this probe cannot read, which is a different thing from a draft that fails to compile.
_PRE_V2_MARKERS = frozenset({"patches", "approval_state", "unresolved_definitions"})


def _is_pre_v2_draft_state(payload: dict) -> bool:
    return not payload.get("schema_version") and bool(_PRE_V2_MARKERS & set(payload))


def replay_drafts(run_id: str, scenario: str | None, show_conditions: bool) -> int:
    """Compile every recorded draft and report what still blocks approval.

    Returns the number of drafts that raised. A raise here is what an HTTP 500 looks like
    from inside the compiler, which is the thing this probe exists to catch.
    """

    from ai_market_monitor.engine.strategy_compiler_v2 import (  # noqa: PLC0415
        StrategyV2CompileError,
        compile_strategy_draft_v2,
    )
    from ai_market_monitor.engine.strategy_draft_v2 import (  # noqa: PLC0415
        validate_draft_semantics,
    )
    from ai_market_monitor.schemas.strategy_draft_v2 import (  # noqa: PLC0415
        StrategyDraftV2,
    )

    drafts = load_drafts(run_id, scenario)
    if not drafts:
        raise SystemExit("No matching recorded drafts.")

    crashes = 0
    compiled = 0
    unreadable = 0
    tally: collections.Counter[str] = collections.Counter()
    for scenario_id, payload in drafts.items():
        if _is_pre_v2_draft_state(payload):
            # A recording from before the V2 draft existed. The probe cannot read it, and
            # saying so is the honest answer. Counting it as a compiler crash would report
            # this script's own limitation as a product failure.
            unreadable += 1
            continue
        print("=" * 92)
        print(scenario_id)
        try:
            # `StrategyDraftV2` migrates its own older schema versions, and deliberately
            # fails closed on a legacy identity by clearing the hashes and any approval.
            draft = StrategyDraftV2.model_validate(payload)
        except Exception as exc:  # noqa: BLE001 - reproducing the production failure
            crashes += 1
            print(f"  RAISED loading the recorded draft: {type(exc).__name__}")
            traceback.print_exc()
            continue
        violations = validate_draft_semantics(draft)
        unsupported = [item for item in draft.unsupported_requirements if item.blocking]
        unresolved = [item for item in draft.unresolved_fields if item.blocking]
        compile_status = "not_attempted"
        conditions = 0
        if draft.condition_ast is not None and not violations and not draft.authoring_blocking:
            try:
                definition = compile_strategy_draft_v2(draft)
            except StrategyV2CompileError as exc:
                compile_status = f"failed:{exc.code}"
                tally[exc.code] += 1
            except Exception as exc:  # noqa: BLE001 - a raise is the HTTP 500
                crashes += 1
                compile_status = "RAISED"
                print(f"  RAISED compiling: {type(exc).__name__}")
                traceback.print_exc()
            else:
                compile_status = "compiled"
                compiled += 1
                conditions = len(leaves(definition.conditions))
                if show_conditions:
                    for row in describe(definition):
                        print(f"        RULE  {row}")
        print(
            f"  compile={compile_status:<24} conditions={conditions:>2} "
            f"violations={len(violations):>2} unsupported={len(unsupported):>2} "
            f"unresolved={len(unresolved):>2}"
        )
        for item in violations:
            tally[item.split(":", 1)[0]] += 1
            print(f"        VIOLATION {item}")
        for item in unsupported:
            tally[f"unsupported:{item.key}"] += 1
            print(f"        UNSUPPORTED {item.key}: {item.missing_contract[:70]!r}")

    print("=" * 92)
    readable = len(drafts) - unreadable
    print(
        f"recorded drafts: {len(drafts)}   readable: {readable}   "
        f"compiled: {compiled}   crashes: {crashes}"
    )
    if unreadable:
        print(
            f"  {unreadable} recorded before the V2 draft existed and cannot be replayed "
            "(not a compiler failure)"
        )
    print(f"blocking findings: {sum(tally.values())}")
    for code, count in tally.most_common():
        print(f"  {count:>3}  {code}")
    return crashes


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
        "--drafts",
        action="store_true",
        help=(
            "compile each recorded canonical draft through the V2 compiler. Chosen "
            "automatically when a run stores drafts rather than raw user turns."
        ),
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
    if args.drafts or not conversations:
        # Every run recorded since the canonical-state change stores a draft rather than
        # raw turns, so this is the ordinary path now, not a fallback.
        return 1 if replay_drafts(args.run, args.scenario, args.show_conditions) else 0

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
