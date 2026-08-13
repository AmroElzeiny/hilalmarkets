"""The skills and the command catalog must describe the repository as it is now.

This is the failure mode these tests exist for: a skill that confidently names
``engine/foo.py``, a module renamed eighteen months ago, sends every investigation to a
file that is not there. It reads perfectly well. Nobody notices until somebody follows it.

So every path and every symbol named in a skill is checked against the real tree, and
every command in the catalog is checked against the real scripts. A document that goes
stale now fails a test instead of quietly misleading the next reader.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hm_oi.catalog import CommandSafety, load_catalog
from hm_oi.paths import repo_root
from hm_oi.permissions import Decision, load_policy
from hm_oi.profile import build_instructions, build_profile
from hm_oi.routing import Tier
from hm_oi.skills import load_skills

ROOT = repo_root()
SKILLS = load_skills(ROOT)
CATALOG = load_catalog(ROOT)

EXPECTED_SKILLS = {
    "hm-repo-investigator",
    "hm-bug-investigator",
    "hm-setup-chat-investigator",
    "hm-test-runner",
    "hm-release-reviewer",
    # Added with the autonomous builder. It takes a failed Setup Chat conversation and
    # names the layer that broke. Read-only, and restricted to committed synthetic
    # fixtures until the product can redact and delete conversation data - see
    # hm_oi.conversation_source and docs/OI_AUTONOMOUS_BUILDER.md.
    "hm-conversation-regression",
    # Added with the operational investigator. All five read sanitized, allowlisted
    # evidence and return a diagnosis or INSUFFICIENT EVIDENCE - never a guess, and
    # never an action. See docs/OI_OPERATIONAL_INVESTIGATOR.md.
    "hm-ai-quality-investigator",
    "hm-provider-incident-investigator",
    "hm-cost-anomaly-investigator",
    "hm-worker-investigator",
    "hm-scanner-incident-investigator",
}


# ---------------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------------


def test_every_required_skill_is_installed_and_discoverable() -> None:
    """Requirement 2."""

    assert {skill.name for skill in SKILLS} == EXPECTED_SKILLS


@pytest.mark.parametrize("skill", SKILLS, ids=lambda item: item.name)
def test_every_skill_declares_what_it_is_for(skill) -> None:
    assert skill.description.strip(), f"{skill.name} has no description"
    assert len(skill.description) > 30
    assert skill.body.strip(), f"{skill.name} has no procedure in it"


@pytest.mark.parametrize("skill", SKILLS, ids=lambda item: item.name)
def test_an_investigating_skill_may_not_change_files(skill) -> None:
    """Investigation and repair are separate decisions.

    A skill that diagnoses and fixes in one pass is how a symptom gets renamed instead of
    understood, so the read-only ones say so and the flag is checked rather than trusted.
    """

    if "investigator" in skill.name or "reviewer" in skill.name:
        assert skill.read_only, f"{skill.name} must be read-only"


def test_the_hardest_skills_are_not_answered_by_the_cheapest_model() -> None:
    """A short question can be the hardest one of the day.

    Setup Chat semantics and release review are where a plausible wrong answer is most
    expensive, so neither may run below the deep tier however the request was phrased.
    """

    by_name = {skill.name: skill for skill in SKILLS}
    assert by_name["hm-setup-chat-investigator"].minimum_tier is Tier.DEEP
    assert by_name["hm-release-reviewer"].minimum_tier is Tier.DEEP


# ---------------------------------------------------------------------------------
# The skills describe the repository that exists
# ---------------------------------------------------------------------------------

#: Paths written in a skill, either repository-relative or relative to the application
#: package (``engine/x.py`` is how the code refers to itself in comments).
_PATH_RE = re.compile(
    r"(?<![\w/])((?:src|tests|scripts|docs|alembic|\.agents|tools)/[\w./\-]+"
    r"|(?:engine|services|api|db|schemas|core|templates|static)/[\w./\-]+\.\w+)"
)
_PACKAGE_RELATIVE = ("engine/", "services/", "api/", "db/", "schemas/", "core/",
                     "templates/", "static/")


def _candidates(reference: str) -> list[Path]:
    """Every real file a written reference could mean.

    A skill writes ``engine/turn_fragments.classify_fragment`` — a module and a symbol
    inside it — and ``engine/comparators.py``. Both are correct English and only the
    second is a path, so the module-plus-symbol form is resolved by trimming the trailing
    dotted names back to a file that exists.
    """

    cleaned = reference.split(":")[0].rstrip(".,;`)")
    base = (
        ROOT / "src" / "ai_market_monitor" / cleaned
        if cleaned.startswith(_PACKAGE_RELATIVE)
        else ROOT / cleaned
    )
    options = [base]
    if not cleaned.endswith(".py"):
        parts = cleaned.split(".")
        for cut in range(len(parts) - 1, 0, -1):
            stem = ".".join(parts[:cut])
            options.append(
                ROOT / "src" / "ai_market_monitor" / f"{stem}.py"
                if cleaned.startswith(_PACKAGE_RELATIVE)
                else ROOT / f"{stem}.py"
            )
    return options


def _resolve(reference: str) -> Path:
    options = _candidates(reference)
    return next((item for item in options if item.exists()), options[0])


def _referenced_paths(text: str) -> set[str]:
    """Paths a document names, ignoring the ones that are patterns or placeholders.

    ``services/ai_*`` and ``tests/unit/test_invariant_*.py`` describe a family of files
    rather than one file, and ``tests/unit/<file>.py`` is an example to fill in. Checking
    either against the filesystem would report a document as stale for being readable.
    """

    found: set[str] = set()
    for match in _PATH_RE.finditer(text):
        trailing = text[match.end() : match.end() + 1]
        if trailing == "*" or "<" in match.group(1):
            continue
        found.add(match.group(1))
    return found


@pytest.mark.parametrize("skill", SKILLS, ids=lambda item: item.name)
def test_every_file_a_skill_names_actually_exists(skill) -> None:
    """Requirement 9, for all five skills rather than only the Setup Chat one."""

    missing = sorted(
        reference
        for reference in _referenced_paths(skill.body)
        if not _resolve(reference).exists()
    )
    assert not missing, (
        f"{skill.name} points at files that do not exist: {missing}. "
        "Update the skill; do not delete the check."
    )


def test_the_setup_chat_skill_names_the_real_failure_taxonomy() -> None:
    """Requirement 9, at the level that actually matters.

    The skill tells an investigator to read the recorded failure and decide whether the
    model or the provider was at fault. If the names it gives have drifted from the enum,
    the whole first step of the procedure is wrong.
    """

    from ai_market_monitor.engine.setup_failure_taxonomy import (
        FailureOwner,
        SetupFailureClass,
    )

    body = next(item for item in SKILLS if item.name == "hm-setup-chat-investigator").body

    owners = {str(member.value) for member in FailureOwner}
    named_owners = set(re.findall(r"`(MODEL|COMPILER|CANONICAL_VALIDATOR|USER|PROVIDER)`", body))
    assert named_owners, "the skill no longer names any failure owner"
    assert {name.casefold() for name in named_owners} <= {item.casefold() for item in owners}

    for member in ("USER_INFORMATION_REQUIRED", "UNSUPPORTED_REQUIREMENT", "PROVIDER_FAILURE"):
        assert member in body, f"the skill stopped mentioning {member}"
        assert hasattr(SetupFailureClass, member), f"{member} is gone from the enum"


def test_the_setup_chat_skill_points_at_the_real_turn_entry_point() -> None:
    """``apply_setup_turn`` is the hinge of the whole pipeline. The skill gives its line."""

    from ai_market_monitor.engine import setup_turn_execution

    assert hasattr(setup_turn_execution, "apply_setup_turn")

    body = next(item for item in SKILLS if item.name == "hm-setup-chat-investigator").body
    match = re.search(r"setup_turn_execution\.py:(\d+)", body)
    assert match, "the skill no longer gives a line for apply_setup_turn"

    source = (
        ROOT / "src" / "ai_market_monitor" / "engine" / "setup_turn_execution.py"
    ).read_text(encoding="utf-8-sig").splitlines()
    line = int(match.group(1))
    nearby = "\n".join(source[max(0, line - 4) : line + 3])
    assert "def apply_setup_turn" in nearby, (
        f"the skill says apply_setup_turn is at line {line}; it is not there any more"
    )


def test_the_setup_chat_skill_uses_the_real_stage_vocabulary() -> None:
    """Stage names are a shared vocabulary. A skill inventing its own would send an
    investigator looking for timings that are never recorded under that name."""

    from ai_market_monitor.engine.turn_timing import STAGES

    assert "turn_timing.py" in next(
        item for item in SKILLS if item.name == "hm-setup-chat-investigator"
    ).body
    assert "planner_provider_wait" in STAGES
    assert "response_composition" in STAGES


def test_the_owner_table_in_the_instructions_names_modules_that_exist() -> None:
    """AGENTS.md lists which module owns which concept. A wrong entry there sends every
    duplicate-parser search to the wrong file, which is the search that finds real bugs."""

    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8-sig")
    missing = sorted(
        reference for reference in _referenced_paths(text) if not _resolve(reference).exists()
    )
    assert not missing, f"AGENTS.md points at files that do not exist: {missing}"


# ---------------------------------------------------------------------------------
# The command catalog
# ---------------------------------------------------------------------------------


def test_the_catalog_is_not_empty() -> None:
    assert len(CATALOG.entries) > 20


@pytest.mark.parametrize("entry", CATALOG.entries, ids=lambda item: item.id)
def test_every_script_named_in_the_catalog_exists(entry) -> None:
    for match in re.finditer(r"(scripts/[\w.\-]+\.(?:py|ps1))", entry.command):
        assert (ROOT / match.group(1)).exists(), (
            f"`{entry.id}` names {match.group(1)}, which is not in the repository"
        )


@pytest.mark.parametrize("entry", CATALOG.entries, ids=lambda item: item.id)
def test_nothing_expensive_or_deployed_runs_unattended(entry) -> None:
    """A paid provider call, a staging environment and a production stack all need a
    person to have said why first."""

    if entry.safety not in {CommandSafety.SAFE_LOCAL, CommandSafety.TEST_ONLY}:
        assert not entry.auto_run, f"`{entry.id}` is {entry.safety} and must not auto-run"


def test_a_dangerous_entry_cannot_be_marked_auto_run_by_editing_the_file() -> None:
    """The catalog is a file the assistant can edit. ``auto_run`` on a paid command is
    the single most valuable field to get wrong, so the loader overrules it."""

    from hm_oi.catalog import _entry_from

    entry = _entry_from(
        {
            "id": "sneaky",
            "title": "x",
            "command": "hm-chatbot-eval run",
            "safety": "credentialed_paid",
            "auto_run": True,
        }
    )
    assert entry is not None
    assert entry.auto_run is False


@pytest.mark.parametrize(
    "area",
    ["engine", "compiler", "setup_chat", "services", "api", "database", "frontend",
     "worker", "evaluator", "engineering_tooling"],
)
def test_every_area_has_a_test_plan(area: str) -> None:
    """Requirement 5."""

    plan = CATALOG.test_plan(area)
    assert plan["first"], f"{area} has nothing to run first"
    assert plan["then"], f"{area} has no adjacent regressions"


def test_an_unknown_area_still_runs_something() -> None:
    """"I did not recognise the area, so I ran no tests" is the worst possible answer."""

    plan = CATALOG.test_plan("something-nobody-declared")
    assert plan["first"] == ["tests/unit"]
    assert plan["then"]


def test_the_paid_commands_are_marked_as_paid() -> None:
    paid = {entry.id for entry in CATALOG.with_safety(CommandSafety.CREDENTIALED_PAID)}
    assert {"eval-run", "probe-live", "eval-isolated-smoke"} <= paid


def test_the_free_compiler_probe_is_marked_free() -> None:
    """The replay probe is the cheapest real evidence available for a compiler change.

    If it were marked as paid, nobody would reach for it, and every investigation would
    start with a provider call instead.
    """

    replay = CATALOG.by_id("replay")
    assert replay is not None
    assert replay.safety is CommandSafety.SAFE_LOCAL
    assert replay.auto_run


# ---------------------------------------------------------------------------------
# What a session actually receives
# ---------------------------------------------------------------------------------


def test_the_session_receives_the_project_instructions() -> None:
    """Requirement 1."""

    instructions = build_instructions(ROOT)
    agents_md = (ROOT / "AGENTS.md").read_text(encoding="utf-8-sig")

    assert agents_md.strip()[:400] in instructions
    for phrase in [
        "AI must never determine",
        "PostgreSQL is the authoritative persistent state",
        "Activation is a separate action from approval",
        "Fix the defect class, not the reported instance",
    ]:
        assert phrase in instructions, f"the instructions lost: {phrase!r}"


def test_the_session_is_told_which_skills_exist() -> None:
    instructions = build_instructions(ROOT)
    for name in EXPECTED_SKILLS:
        assert name in instructions


def test_the_session_is_told_what_is_refused_and_why() -> None:
    """The model is told the rules as well as being stopped by them.

    Not as the enforcement — that is ``hm_oi.guard`` — but so a refusal is predictable
    and the session does not waste turns discovering the boundary by hitting it.
    """

    instructions = build_instructions(ROOT)
    policy = load_policy(ROOT)
    for rule in policy.rules_by_decision(Decision.DENY):
        assert rule.rule_id in instructions


def test_the_session_is_told_which_commands_are_free() -> None:
    instructions = build_instructions(ROOT)
    assert "replay" in instructions
    assert "eval-run" in instructions


def test_a_missing_agents_file_makes_the_session_refuse_to_work(tmp_path) -> None:
    """Losing the rules must be loud.

    A session that started with no instructions would behave like a general-purpose
    assistant on a repository full of governed decisions, and would look normal doing it.
    """

    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "alembic.ini").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    agents = tmp_path / ".agents"
    agents.mkdir()
    (agents / "commands.json").write_text('{"commands": []}', encoding="utf-8")

    instructions = build_instructions(tmp_path)
    assert "AGENTS.md is missing" in instructions
    assert "Refuse to make any change" in instructions


def test_the_profile_reports_what_is_wrong_rather_than_starting_broken() -> None:
    profile = build_profile(ROOT, tier=Tier.NORMAL, env={})
    assert profile.skills
    assert profile.catalog.entries
    # No key in a bare environment: that is a warning, not a crash, because everything
    # except starting a session works without one.
    assert any("API key" in warning for warning in profile.warnings)


@pytest.mark.parametrize("tier", list(Tier))
def test_every_tier_binds_to_a_hosted_model(tier: Tier) -> None:
    """No tier may fall back to running a model locally. The VPS cannot host one."""

    profile = build_profile(ROOT, tier=tier, env={})
    assert profile.model.model
    assert "/" in profile.model.model, "a model string must name its hosted provider"
    assert profile.model.provider in {"openai", "anthropic", "azure", "gemini", "groq"}
