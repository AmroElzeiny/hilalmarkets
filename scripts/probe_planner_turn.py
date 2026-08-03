"""Run one Setup Chat turn through the real planner and the real compiler.

This is the reproduction probe for the compact-planner path. It is the only cheap way
to see *why* a turn failed: the evaluator artifacts record the public refusal
(``COMPILER_INVARIANT_VIOLATION``/HTTP 422) but never the compiler's own typed reason,
its intent reference, or the exact model envelope that produced it.

Two modes:

``--live``
    Make one real structured planner call for the message, then compile it. Costs a few
    tenths of a cent. Writes the envelope next to the report so it can be replayed
    offline forever after.

``--envelope FILE``
    Replay a previously captured envelope through the compiler with no model call.

Usage::

    python scripts/probe_planner_turn.py --live --message "..." --save envelope.json
    python scripts/probe_planner_turn.py --envelope envelope.json --message "..."
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_market_monitor.core.config import Settings  # noqa: E402
from ai_market_monitor.engine.capability_shortlist import (  # noqa: E402
    build_capability_shortlist,
)
from ai_market_monitor.engine.planner_intent_compiler import (  # noqa: E402
    IntentCompileError,
    compile_planner_intents,
)
from ai_market_monitor.schemas.planner_intent import PlannerIntentEnvelope  # noqa: E402
from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2  # noqa: E402
from ai_market_monitor.services.setup_chat_agent import (  # noqa: E402
    SetupAgentTurnInput,
    SetupChatAgent,
    _classify_plan_failure,
)

TURN_ID = "probe-turn-0001"


def _settings() -> Settings:
    return Settings()


def _draft(payload: dict[str, Any] | None) -> StrategyDraftV2:
    if payload:
        return StrategyDraftV2.model_validate(payload)
    return StrategyDraftV2()


async def _plan_live(message: str, draft: StrategyDraftV2) -> PlannerIntentEnvelope:
    from ai_market_monitor.services.ai_model_routing import (  # noqa: PLC0415
        select_setup_model,
    )

    settings = _settings()
    agent = SetupChatAgent(settings)
    turn = SetupAgentTurnInput(message=message, source_turn_id=TURN_ID, draft=draft)
    shortlist = build_capability_shortlist(turn.normalized_message)
    route = select_setup_model(settings, current_message=turn.normalized_message)
    envelope, usage = await agent._plan_once(turn, shortlist, route)  # noqa: SLF001
    print(f"planner model: {route.model}   usage: {json.dumps(usage, default=str)}")
    return envelope


def _report(envelope: PlannerIntentEnvelope, message: str, draft: StrategyDraftV2) -> int:
    print("=" * 92)
    print("ENVELOPE")
    print(json.dumps(envelope.model_dump(mode="json", exclude_none=True), indent=1))
    print("=" * 92)
    shortlist = build_capability_shortlist(message)
    try:
        compilation = compile_planner_intents(
            envelope,
            draft=draft,
            message=message,
            source_turn_id=TURN_ID,
            shortlist=shortlist,
        )
    except IntentCompileError as exc:
        failure = _classify_plan_failure(
            code=exc.code,
            details=exc.details,
            envelope=envelope,
            message=message,
            declared_outcome=exc.outcome,
            intent_ref=exc.intent_ref,
            target_path=exc.target_path,
            segment_ref=exc.segment_ref,
        )
        print("COMPILER RAISED")
        print(f"  raw code      : {exc.code}")
        print(f"  raw outcome   : {exc.outcome}")
        print(f"  raw details   : {exc.details}")
        print(f"  intent_ref    : {exc.intent_ref}")
        print(f"  target_path   : {exc.target_path}")
        print(f"  segment_ref   : {exc.segment_ref}")
        print("  --- what the user actually sees ---")
        print(f"  public code   : {failure.code}")
        print(f"  public outcome: {failure.outcome}")
        print(f"  repairable    : {failure.outcome.name == 'SEMANTIC_INTENT_REPAIR_REQUIRED'}")
        return 1
    print("COMPILED")
    print(f"  operations : {len(compilation.plan.operations)}")
    for operation in compilation.plan.operations:
        print(f"    {json.dumps(operation.model_dump(mode='json', exclude_none=True))[:600]}")
    print(f"  derivations: {list(compilation.derivations)}")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", required=True)
    parser.add_argument("--live", action="store_true", help="make one real planner call")
    parser.add_argument("--envelope", default=None, help="replay a captured envelope")
    parser.add_argument("--save", default=None, help="write the live envelope here")
    parser.add_argument("--draft", default=None, help="a serialized StrategyDraftV2 to start from")
    args = parser.parse_args()

    draft = _draft(
        json.loads(Path(args.draft).read_text(encoding="utf-8")) if args.draft else None
    )
    if args.envelope:
        envelope = PlannerIntentEnvelope.model_validate_json(
            Path(args.envelope).read_text(encoding="utf-8")
        )
    elif args.live:
        envelope = await _plan_live(args.message, draft)
        if args.save:
            Path(args.save).write_text(
                envelope.model_dump_json(indent=1, exclude_none=True), encoding="utf-8"
            )
            print(f"wrote {args.save}")
    else:
        raise SystemExit("choose --live or --envelope")
    return _report(envelope, args.message, draft)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
