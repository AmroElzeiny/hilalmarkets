"""``python -m hm_oi`` — inspect the engineering assistant's setup without starting it.

Every subcommand here is deliberately free and offline. That is the point: the routing
rules, the permission policy, the skills and the command catalog can all be checked, in
CI and by a person, without an API key, without Open Interpreter installed, and without
spending anything. A safety rule that can only be observed by running a paid session is a
safety rule nobody checks.

``argparse`` rather than a CLI library because this must keep working in the product's
virtual environment, and adding a dependency to a shipped project so an engineering tool
can print a table would be the wrong trade.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from hm_oi import models as model_config
from hm_oi import telemetry
from hm_oi.catalog import CommandSafety, load_catalog
from hm_oi.paths import repo_root
from hm_oi.permissions import Decision, load_policy
from hm_oi.profile import build_profile
from hm_oi.routing import TaskRequest, Tier, route_task
from hm_oi.skills import load_skills


def _emit(payload: object, as_json: bool, lines: Sequence[str]) -> None:
    """Print either machine-readable JSON or the human version.

    Both are produced from the same values, so the two can never say different things.
    """

    if as_json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print("\n".join(lines))


def _cmd_doctor(args: argparse.Namespace) -> int:
    root = repo_root()
    profile = build_profile(root, tier=Tier(args.tier))
    configuration = model_config.configuration_summary(root)

    payload = {
        "repo_root": str(root),
        "instructions_characters": len(profile.instructions),
        "skills": [skill.to_dict() for skill in profile.skills],
        "commands": len(profile.catalog.entries),
        "commands_unattended": len(profile.catalog.unattended),
        "policy_rules": len(profile.policy.rules),
        "policy_denied": len(profile.policy.rules_by_decision(Decision.DENY)),
        "policy_confirm": len(profile.policy.rules_by_decision(Decision.CONFIRM)),
        "models": configuration,
        "warnings": list(profile.warnings),
    }

    lines = [
        f"repository        {root}",
        f"instructions      {len(profile.instructions):,} characters"
        f" ({'AGENTS.md found' if (root / 'AGENTS.md').exists() else 'AGENTS.md MISSING'})",
        f"skills            {len(profile.skills)}: "
        f"{', '.join(skill.name for skill in profile.skills) or 'none'}",
        f"commands          {len(profile.catalog.entries)} "
        f"({len(profile.catalog.unattended)} may run unattended)",
        f"policy            {len(profile.policy.rules)} rules "
        f"({len(profile.policy.rules_by_decision(Decision.DENY))} refuse, "
        f"{len(profile.policy.rules_by_decision(Decision.CONFIRM))} need a person)",
        f"api key           {configuration['api_key_source']}",
        f"session budget    ${configuration['session_budget_usd']:.2f}",
        "tiers",
    ]
    for tier, info in configuration["tiers"].items():
        lines.append(f"  {tier:<7} {info['model']}  (effort: {info['reasoning_effort']})")
    if profile.warnings:
        lines.append("warnings")
        lines += [f"  ! {item}" for item in profile.warnings]
    else:
        lines.append("warnings          none")

    _emit(payload, args.json, lines)
    # A missing key is a warning, not a failure: everything except starting a session
    # works without one, and the doctor command is most useful before one is set.
    return 0


def _cmd_route(args: argparse.Namespace) -> int:
    request = TaskRequest(
        text=" ".join(args.task),
        paths=tuple(args.path or ()),
        previous_attempts=int(args.attempts),
        forced_tier=Tier(args.force) if args.force else None,
    )
    decision = model_config.bind_model(route_task(request))
    if args.log:
        telemetry.record_routing(decision)

    lines = [
        f"tier         {decision.tier.value.upper()}",
        f"category     {decision.category.value}",
        f"model        {decision.model}  (effort: {decision.reasoning_effort})",
        f"components   {', '.join(decision.components) or 'none identified'}",
        f"reasons      {', '.join(decision.reasons)}",
        f"escalated by {', '.join(decision.escalation_reasons) or 'nothing — this is the floor'}",
    ]
    _emit(decision.to_dict(), args.json, lines)
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    policy = load_policy()
    command = " ".join(args.command)
    verdict = policy.evaluate(command)

    lines = [
        f"decision   {verdict.decision.value.upper()}",
        f"rule       {verdict.rule_id or '(none matched)'}",
        f"reason     {verdict.reason}",
    ]
    if len(verdict.matched_rules) > 1:
        lines.append(f"also hit   {', '.join(verdict.matched_rules)}")
    _emit(verdict.to_dict(), args.json, lines)
    # A non-zero exit for a refusal, so this is usable from a shell script.
    return 1 if verdict.refused else 0


def _cmd_plan(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    plan = catalog.test_plan(args.area)
    payload = {"area": args.area, **plan}
    lines = [
        f"area   {args.area}",
        f"first  {', '.join(plan['first'])}",
        f"then   {', '.join(plan['then'])}",
    ]
    _emit(payload, args.json, lines)
    return 0


def _cmd_commands(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    entries = catalog.for_area(args.area) if args.area else catalog.entries
    if args.safety:
        entries = tuple(item for item in entries if item.safety == CommandSafety(args.safety))

    payload = [entry.to_dict() for entry in entries]
    lines = []
    for entry in entries:
        marker = "auto" if entry.auto_run else "ask "
        lines.append(f"[{marker}] {entry.id:<22} {entry.safety.value:<18} {entry.title}")
        lines.append(f"         {entry.command}")
    _emit(payload, args.json, lines or ["(nothing matched)"])
    return 0


def _cmd_skills(args: argparse.Namespace) -> int:
    skills = load_skills()
    payload = [skill.to_dict() for skill in skills]
    lines = [
        f"{skill.name:<28} tier>={skill.minimum_tier.value:<7} "
        f"{'read-only' if skill.read_only else 'may write'}  {skill.description}"
        for skill in skills
    ]
    _emit(payload, args.json, lines or ["(no skills found)"])
    return 0


def _cmd_instructions(args: argparse.Namespace) -> int:
    profile = build_profile(tier=Tier(args.tier))
    print(profile.instructions)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m hm_oi",
        description="Inspect the HilalMarkets engineering assistant configuration.",
    )
    # ``--json`` is defined on every subcommand rather than once at the top, so it can be
    # written after the subcommand where a person naturally puts it. Defining it in both
    # places would let the subcommand's default silently overwrite the top-level value.
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--json", action="store_true", help="machine-readable output")

    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", parents=[shared], help="report the whole configuration")
    doctor.add_argument("--tier", default=Tier.NORMAL.value, choices=[t.value for t in Tier])
    doctor.set_defaults(func=_cmd_doctor)

    route = sub.add_parser(
        "route", parents=[shared], help="show which tier a task would use, and why"
    )
    route.add_argument("task", nargs="+")
    route.add_argument("--path", action="append", help="a repository path the task touches")
    route.add_argument("--attempts", default=0, type=int, help="failed attempts so far")
    route.add_argument("--force", default=None, choices=[t.value for t in Tier])
    route.add_argument("--log", action="store_true", help="write the decision to the session log")
    route.set_defaults(func=_cmd_route)

    check = sub.add_parser(
        "check", parents=[shared], help="ask the permission policy about a command"
    )
    check.add_argument("command", nargs="+")
    check.set_defaults(func=_cmd_check)

    plan = sub.add_parser("plan", parents=[shared], help="which tests to run for an area")
    plan.add_argument("area")
    plan.set_defaults(func=_cmd_plan)

    commands = sub.add_parser(
        "commands", parents=[shared], help="list the authoritative commands"
    )
    commands.add_argument("--area", default=None)
    commands.add_argument("--safety", default=None, choices=[s.value for s in CommandSafety])
    commands.set_defaults(func=_cmd_commands)

    skills = sub.add_parser("skills", parents=[shared], help="list the installed skills")
    skills.set_defaults(func=_cmd_skills)

    instructions = sub.add_parser(
        "instructions",
        parents=[shared],
        help="print the system message a session would receive",
    )
    instructions.add_argument("--tier", default=Tier.NORMAL.value, choices=[t.value for t in Tier])
    instructions.set_defaults(func=_cmd_instructions)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = args.func(args)
    return int(result)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
