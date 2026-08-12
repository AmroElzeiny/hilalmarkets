"""Turn the repository's own files into a configured Open Interpreter session.

Everything the assistant knows comes from files that a person edits and Git tracks:
``AGENTS.md`` for the rules, ``.agents/skills/`` for the procedures,
``.agents/commands.json`` for the commands, and ``src/hm_oi/permissions.py`` for what it
may run. Nothing is hard-coded into a prompt string here. That matters because the
alternative — instructions living inside the tool — produces two descriptions of how to
work on this repository, and the one nobody edits is the one that goes stale.

``apply_to`` is deliberately tolerant of Open Interpreter's shape. It sets what it finds
and skips what it does not, so an upgrade that renames a setting degrades to "that
setting was not applied" instead of crashing at start-up. The one exception is the
permission guard: if the surface it needs is gone, the session is refused rather than run
unprotected. See ``hm_oi.guard.verify_guard_surface``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hm_oi import models as model_config
from hm_oi import telemetry
from hm_oi.catalog import Catalog, CommandSafety, load_catalog
from hm_oi.guard import install_guard
from hm_oi.paths import instructions_path, repo_root
from hm_oi.permissions import (
    READABLE_AREAS,
    WRITABLE_ARTIFACT_AREAS,
    Decision,
    PermissionPolicy,
    load_policy,
)
from hm_oi.routing import Tier
from hm_oi.skills import Skill, load_skills, skill_index

#: The tier a session starts at when the engineer does not say. NORMAL, because a session
#: is a conversation and most of a conversation is ordinary engineering. FAST would make
#: the first hard question of the day cheap, which is the wrong place to save money.
DEFAULT_TIER: Tier = Tier.NORMAL


@dataclass(frozen=True, slots=True)
class SessionProfile:
    """Everything one session needs, assembled from the repository."""

    root: Path
    tier: Tier
    instructions: str
    policy: PermissionPolicy
    catalog: Catalog
    skills: tuple[Skill, ...]
    model: model_config.TierModel
    api_key_present: bool
    session_budget_usd: float
    warnings: tuple[str, ...] = field(default=())


def _platform_section() -> str:
    """Which shell the model actually has, said plainly.

    Open Interpreter's ``shell`` language is whatever the operating system's default
    shell is. On Windows that is **cmd.exe** — not bash, and not PowerShell. A model that
    assumes POSIX writes ``find src -iname '*compar*'``, and cmd's completely unrelated
    ``find`` command answers ``File not found``. The model reads that as "the file does
    not exist", tries again differently, and the session spends minutes going nowhere.

    Observed exactly that way in a live run before this section existed. Naming the shell
    is the whole fix.
    """

    if os.name != "nt":
        return (
            "## This machine\n\n"
            "Linux. The `shell` language is a POSIX shell. `python` is also available and "
            "is the better choice for anything that reads files or parses output.\n"
        )

    return (
        "## This machine\n\n"
        "Windows. **The `shell` language here is `cmd.exe`, not bash.** POSIX tools do "
        "not exist or mean something entirely different — cmd's `find` searches text "
        "inside files and has nothing to do with the POSIX `find`, and `ls`, `cat`, "
        "`grep`, `head` and `which` are simply absent.\n\n"
        "Use one of these instead:\n\n"
        "- `python` — the best choice for reading files, listing directories, or parsing "
        "output. It behaves the same everywhere.\n"
        "- `powershell` — for shell work. `Get-ChildItem`, `Select-String`, "
        "`Get-Content`.\n"
        "- `shell` — only for tools that are real programs, such as `git` and "
        "`.venv/Scripts/python`. `git grep` and `git ls-files` work well and are fast.\n\n"
        "**Search with `git grep` or `git ls-files`, not by walking the directory tree.** "
        "`.venv` and `.oi-venv` sit inside this folder and hold tens of thousands of "
        "third-party files. A plain walk returns matches from SQLAlchemy and nltk before "
        "it reaches this project's own code. Git skips both because they are ignored.\n\n"
        "The commands in the catalogue below are written to work as they appear.\n"
    )


def _execution_section() -> str:
    """How to actually do something, put first and kept short.

    Open Interpreter already appends its own one-line execution note, but it lands after
    twenty thousand characters of project rules. In a live session the model read all of
    it, replied "I will check the repository guidance, then locate the owner", and
    stopped — a plan, no commands, no answer. It had been told *how* to work at length
    and *how to act* in one sentence, at the bottom.

    Six lines at the top fixed it. Position is the whole point: this is the only part of
    the message that turns intent into a command actually running.
    """

    # Deliberately says nothing about *how* a command is transmitted. Open Interpreter
    # sends it either as a tool call or as a fenced code block depending on the model,
    # and an instruction naming one of those is wrong for the other. What the model
    # needs to be told is to act rather than narrate, and what a refusal looks like.
    return (
        "## How you do things here\n\n"
        "You answer by running commands and reading their output, not by describing what\n"
        "you would run. One command, read the result, then decide the next one. Never\n"
        "plan several commands in prose and run none of them.\n\n"
        "You are working inside the repository checkout, so relative paths such as\n"
        "`src/ai_market_monitor/engine` work as written.\n\n"
        "Some commands are refused before they run. A refusal comes back as output\n"
        "beginning with `REFUSED` or `NEEDS A PERSON`, and it names the rule that stopped\n"
        "it. When you see one, stop and report it in your answer. Do not look for another\n"
        "way to reach the same thing.\n"
    )


def _skills_section(skills: tuple[Skill, ...]) -> str:
    return (
        "## Skills available in this repository\n\n"
        "Each is a procedure in `.agents/skills/<name>/SKILL.md`. Read the file before\n"
        "following it — this list is only an index.\n\n"
        f"{skill_index(skills)}\n\n"
        "Pick one when the task matches it. A skill marked read-only may not change any\n"
        "file while it is being followed.\n"
    )


def _commands_section(catalog: Catalog) -> str:
    unattended = catalog.unattended
    needs_reason = catalog.with_safety(
        CommandSafety.CREDENTIALED_PAID,
        CommandSafety.STAGING_ONLY,
        CommandSafety.PRODUCTION,
    )
    lines = [
        "## Commands",
        "",
        "`.agents/commands.json` is the authoritative list. Do not invent a command and",
        "do not copy one out of an old report — several documents here name commands",
        "that no longer exist.",
        "",
        "You may run these without asking:",
        "",
    ]
    lines += [f"- `{entry.id}` — {entry.title}: `{entry.command}`" for entry in unattended]
    lines += [
        "",
        "These cost money, touch a deployed environment, or are operator actions. Never",
        "run one without a stated reason and a person agreeing:",
        "",
    ]
    lines += [f"- `{entry.id}` ({entry.safety.value}) — {entry.title}" for entry in needs_reason]
    lines.append("")
    return "\n".join(lines)


def _policy_section(policy: PermissionPolicy) -> str:
    denied = policy.rules_by_decision(Decision.DENY)
    confirm = policy.rules_by_decision(Decision.CONFIRM)
    lines = [
        "## What is enforced",
        "",
        "These are checked in code before anything runs, not by asking you to be careful.",
        "A refusal arrives as command output explaining which rule stopped it.",
        "",
        "**Refused outright:**",
        "",
    ]
    lines += [f"- `{rule.rule_id}` — {rule.reason}" for rule in denied]
    lines += ["", "**Needs a person to agree first:**", ""]
    lines += [f"- `{rule.rule_id}` — {rule.reason}" for rule in confirm]
    lines += [
        "",
        f"Readable: {', '.join(READABLE_AREAS)} — and anything else in the repository that",
        "no rule above refuses.",
        f"Generated files belong in: {', '.join(WRITABLE_ARTIFACT_AREAS)}.",
        "",
        "If you are refused, do not look for another way to reach the same thing. Say what",
        "you needed and why, and let the engineer decide.",
        "",
    ]
    return "\n".join(lines)


def build_instructions(
    root: Path | None = None,
    *,
    catalog: Catalog | None = None,
    policy: PermissionPolicy | None = None,
    skills: tuple[Skill, ...] | None = None,
) -> str:
    """The full system message: the repository's rules, its skills, its commands.

    ``AGENTS.md`` comes first and unedited. It is the document a person maintains, and
    reformatting it here would create a second version of it that drifts.
    """

    root = root or repo_root()
    catalog = catalog if catalog is not None else load_catalog(root)
    policy = policy if policy is not None else load_policy(root)
    skills = skills if skills is not None else load_skills(root)

    try:
        agents_md = instructions_path(root).read_text(encoding="utf-8")
    except OSError:
        agents_md = (
            "# AGENTS.md is missing\n\n"
            "Refuse to make any change. Report that the project instructions could not "
            "be read, and ask the engineer to restore `AGENTS.md`.\n"
        )

    return "\n\n---\n\n".join(
        [
            _execution_section().strip(),
            _platform_section().strip(),
            agents_md.strip(),
            _skills_section(skills).strip(),
            _commands_section(catalog).strip(),
            _policy_section(policy).strip(),
        ]
    )


def build_profile(
    root: Path | None = None,
    *,
    tier: Tier = DEFAULT_TIER,
    env: dict[str, str] | None = None,
) -> SessionProfile:
    """Assemble a session profile without touching Open Interpreter.

    Separate from ``apply_to`` so the whole assembly can be tested, and reported by the
    doctor command, on a machine where Open Interpreter is not installed.
    """

    root = root or repo_root()
    catalog = load_catalog(root)
    policy = load_policy(root)
    skills = load_skills(root)
    bound = model_config.tier_models(root, env)[tier]
    key_present = model_config.api_key(env) is not None

    warnings: list[str] = []
    if not key_present:
        warnings.append(
            f"No API key. Set {model_config.API_KEY_ENV}, or set "
            f"{model_config.SHARED_KEY_OPT_IN_ENV}=1 to deliberately share the product key."
        )
    if not skills:
        warnings.append("No skills found under .agents/skills/.")
    if not catalog.entries:
        warnings.append("The command catalog is empty.")
    if not instructions_path(root).exists():
        warnings.append("AGENTS.md is missing; the session would start without its rules.")

    return SessionProfile(
        root=root,
        tier=tier,
        instructions=build_instructions(root, catalog=catalog, policy=policy, skills=skills),
        policy=policy,
        catalog=catalog,
        skills=skills,
        model=bound,
        api_key_present=key_present,
        session_budget_usd=model_config.session_budget_usd(env),
        warnings=tuple(warnings),
    )


def _set_if_present(target: Any, name: str, value: Any) -> bool:
    """Set an attribute only if the object already has it.

    Open Interpreter's settings move between versions. Creating an attribute it does not
    read would look like the setting was applied when it was not, which is worse than
    reporting that it was skipped.
    """

    if hasattr(target, name):
        setattr(target, name, value)
        return True
    return False


def apply_to(
    interpreter: Any,
    profile: SessionProfile,
    *,
    env: dict[str, str] | None = None,
    auto_run: bool = False,
) -> dict[str, Any]:
    """Configure a live Open Interpreter instance. Returns what was actually applied."""

    applied: dict[str, Any] = {}

    applied["custom_instructions"] = _set_if_present(
        interpreter, "custom_instructions", profile.instructions
    )
    # Every block of code is shown to the engineer before it runs, unless they explicitly
    # asked otherwise. This is Open Interpreter's own mechanism and it is the strongest
    # protection in the whole design — a person reading the command.
    applied["auto_run"] = _set_if_present(interpreter, "auto_run", bool(auto_run))
    applied["offline"] = _set_if_present(interpreter, "offline", False)
    applied["disable_telemetry"] = _set_if_present(interpreter, "disable_telemetry", True)
    applied["verbose"] = _set_if_present(interpreter, "verbose", False)

    llm = getattr(interpreter, "llm", None)
    if llm is not None:
        applied["model"] = _set_if_present(llm, "model", profile.model.model)
        applied["max_budget"] = _set_if_present(llm, "max_budget", profile.session_budget_usd)
        # Read the answer out of ordinary text rather than out of a tool call. The
        # reasoning models configured here refuse tool calls while a reasoning effort is
        # set, and the effort is the whole difference between the tiers. See
        # ``models.FUNCTION_CALLING_ENV`` for the provider's own wording.
        applied["supports_functions"] = _set_if_present(
            llm, "supports_functions", model_config.use_function_calling(env)
        )
        key = model_config.api_key(env)
        if key:
            applied["api_key"] = _set_if_present(llm, "api_key", key)
        base = model_config.base_url(env)
        if base:
            applied["api_base"] = _set_if_present(llm, "api_base", base)

    computer = getattr(interpreter, "computer", None)
    if computer is not None:
        # The assistant works on a repository. It does not need Open Interpreter's
        # screen, mouse, and clipboard helpers, and importing them would offer a set of
        # capabilities that nothing in OI-1 has any use for.
        applied["import_computer_api"] = _set_if_present(
            computer, "import_computer_api", False
        )

    # Last, and not optional: this raises if the surface it needs is missing.
    install_guard(interpreter, profile.policy)
    applied["guard"] = True

    telemetry.record(
        "session_start",
        {
            "tier": str(profile.tier),
            "model": profile.model.model,
            "provider": profile.model.provider,
            "budget_usd": profile.session_budget_usd,
            "skills": [skill.name for skill in profile.skills],
            "auto_run": bool(auto_run),
            "applied": applied,
        },
        profile.root,
    )
    return applied
