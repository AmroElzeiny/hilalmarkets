"""The routing rules, asserted as rules rather than as a handful of examples.

Two properties matter more than any individual routing choice:

* **A cheap tier is never chosen for expensive work.** Being wrong in this direction is
  silent — a plausible, confident, wrong answer about a Sharia code path costs far more
  than the model call that was saved.
* **Choosing a tier costs nothing.** A router that calls a model to pick a model has
  spent the money it existed to save. This is checked structurally, not by trusting the
  implementation to stay pure.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hm_oi.routing import (
    CATEGORY_FLOOR,
    RANK,
    TaskCategory,
    TaskRequest,
    Tier,
    classify,
    component_for_path,
    route_task,
)

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------------
# Structural: the router cannot spend money
# ---------------------------------------------------------------------------------


def test_the_router_imports_nothing_that_could_make_a_network_call() -> None:
    """Classification is regular expressions and integers. It must stay that way.

    A future edit that reaches for a model "just to classify the hard ones" would make
    every lookup cost a request, which is the exact failure this module was written to
    avoid. Checked by reading the imports rather than by measuring, so it fails at the
    moment somebody writes it rather than the moment somebody notices the bill.
    """

    source = (ROOT / "src" / "hm_oi" / "routing.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(node.module.split(".")[0])

    forbidden = {
        "httpx", "requests", "urllib", "aiohttp", "openai", "anthropic",
        "litellm", "socket", "http",
    }
    assert not (imported & forbidden), (
        f"routing.py imports {sorted(imported & forbidden)}; "
        "choosing a tier must never require a network call"
    )


def test_routing_is_deterministic() -> None:
    """The same task routes the same way every time.

    A router that varies makes an incident impossible to reproduce: the question "why did
    it answer that cheaply?" has no answer if the next run chooses differently.
    """

    request = TaskRequest(text="fix the grounding check in the compiler")
    first = route_task(request)
    for _ in range(20):
        assert route_task(request).to_dict() == first.to_dict()


# ---------------------------------------------------------------------------------
# FAST: only for looking things up
# ---------------------------------------------------------------------------------

SIMPLE_LOOKUPS = [
    "which file defines route_task",
    "where is the timeframe parser",
    "list the test files for the boolean topology",
    "find the module that owns comparators",
    "what tests cover the capability resolver",
    "who calls provider_request",
    "show me the engine directory",
    "how many capabilities are registered",
]


@pytest.mark.parametrize("task", SIMPLE_LOOKUPS)
def test_a_plain_lookup_does_not_reach_the_deep_tier(task: str) -> None:
    """Requirement 7: a simple task must not invoke the expensive model.

    Every one of these is answered by reading a file. Paying premium rates for them is
    how a tool becomes too expensive to leave switched on.
    """

    decision = route_task(TaskRequest(text=task))
    assert decision.tier is not Tier.DEEP, (
        f"{task!r} routed DEEP because of {decision.escalation_reasons}"
    )


@pytest.mark.parametrize("task", SIMPLE_LOOKUPS)
def test_a_plain_lookup_reaches_the_cheapest_tier(task: str) -> None:
    decision = route_task(TaskRequest(text=task))
    assert decision.tier is Tier.FAST, (
        f"{task!r} routed {decision.tier} because of {decision.reasons}"
    )


# ---------------------------------------------------------------------------------
# DEEP: the work where being wrong is expensive
# ---------------------------------------------------------------------------------

ARCHITECTURE_TASKS = [
    "should the capability resolver own the operator table or should the compiler",
    "who owns the movement direction word list",
    "which module should handle grounding",
    "redesign the boundary between Scanner and Monitor",
    "is Redis the single source of truth for setup state",
]

SECURITY_TASKS = [
    "can another user read this strategy through the dashboard API",
    "is the CSRF token checked on the waitlist form",
    "does this log line leak a customer email address",
    "review the authentication on the new admin route",
    "is user input escaped before it reaches the template",
]

SETUP_CHAT_TASKS = [
    "setup chat read the percent move as a maximum instead of a minimum",
    "the planner ignored the second condition in the message",
    "grounding accepted a threshold that was never in the user's words",
    "apply_setup_turn patched the wrong condition",
    "the composer described a rule the draft does not contain",
]

HIGH_RISK_TASKS = [
    "add a column to the billing events table",
    "change how the Sharia screening picks a methodology",
    "adjust the approval hash binding",
    "write the alembic migration for the new entitlement field",
    "change what activation checks before it starts a monitor",
]


@pytest.mark.parametrize(
    "task", ARCHITECTURE_TASKS + SECURITY_TASKS + SETUP_CHAT_TASKS + HIGH_RISK_TASKS
)
def test_expensive_work_escalates_to_deep(task: str) -> None:
    """Requirement 8: architecture, security, semantics and risk all escalate."""

    decision = route_task(TaskRequest(text=task))
    assert decision.tier is Tier.DEEP, (
        f"{task!r} routed {decision.tier} with reasons {decision.reasons}"
    )


@pytest.mark.parametrize("task", SECURITY_TASKS)
def test_security_work_is_classified_as_security(task: str) -> None:
    """The category is reported, not only the tier. A security review labelled as a
    routine bug gets a routine review."""

    assert classify(TaskRequest(text=task)) is TaskCategory.SECURITY


@pytest.mark.parametrize("task", ARCHITECTURE_TASKS)
def test_ownership_questions_are_classified_as_architecture(task: str) -> None:
    """"Should X own Y" is an architecture question even when X is a Setup Chat module.

    This failed before: `should the capability resolver own the operator table` matched
    the Setup Chat vocabulary first and was reported as a semantics question, which sends
    the reader to the wrong skill.
    """

    assert classify(TaskRequest(text=task)) is TaskCategory.ARCHITECTURE


# ---------------------------------------------------------------------------------
# History and scope
# ---------------------------------------------------------------------------------


def test_a_failed_attempt_raises_the_tier_and_two_reach_deep() -> None:
    """A fix that did not work is evidence the first reading was wrong.

    Retrying at the same tier repeats the same mistake at the same price.
    """

    task = "the volume filter still lets the wrong candles through"
    first = route_task(TaskRequest(text=task, previous_attempts=0))
    second = route_task(TaskRequest(text=task, previous_attempts=1))
    third = route_task(TaskRequest(text=task, previous_attempts=2))

    assert RANK[second.tier] >= RANK[first.tier]
    assert third.tier is Tier.DEEP
    assert "repeated_failed_attempts" in third.reasons


def test_touching_three_components_is_treated_as_cross_component() -> None:
    """A change that spans components is where a locally correct edit breaks something
    else, and that is not visible from any one file."""

    decision = route_task(
        TaskRequest(
            text="rename the field",
            paths=(
                "src/ai_market_monitor/api/routers/dashboard_api.py",
                "src/ai_market_monitor/db/models/system_brain.py",
                "src/ai_market_monitor/static/hm-monitor-test.js",
            ),
        )
    )
    assert decision.tier is Tier.DEEP
    assert "cross_component_scope" in decision.reasons


def test_paths_are_read_out_of_the_task_text_when_not_supplied() -> None:
    """Most callers type the path in the sentence rather than passing it separately."""

    decision = route_task(
        TaskRequest(
            text=(
                "reconcile src/ai_market_monitor/engine/comparators.py with "
                "src/ai_market_monitor/api/routers/public.py and "
                "src/ai_market_monitor/db/models/system_brain.py"
            )
        )
    )
    assert set(decision.components) == {"api", "database", "engine"}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/ai_market_monitor/engine/comparators.py", "engine"),
        ("src/ai_market_monitor/api/routers/public.py", "api"),
        ("src\\ai_market_monitor\\services\\billing.py", "services"),
        ("src/ai_market_monitor/worker.py", "worker"),
        ("tests/browser/test_x.py", "browser_tests"),
        ("tests/unit/test_x.py", "tests"),
        ("alembic/versions/abc.py", "database"),
        ("src/hm_oi/routing.py", "engineering_tooling"),
        ("README.md", None),
    ],
)
def test_every_path_resolves_to_the_component_that_owns_it(
    path: str, expected: str | None
) -> None:
    """Longest prefix wins, so ``tests/browser`` is not swallowed by ``tests``."""

    assert component_for_path(path) == expected


# ---------------------------------------------------------------------------------
# The floor
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize("category", list(TaskCategory))
def test_every_category_has_a_declared_floor(category: TaskCategory) -> None:
    """A category added later without a floor would fall back to whatever ``dict.get``
    returned, and the safe answer is not ``None``."""

    assert category in CATEGORY_FLOOR


def test_an_unrecognised_task_is_not_treated_as_cheap() -> None:
    """A task nobody described well enough to classify is not a simple task.

    Guessing low here is the one guess that is silently wrong.
    """

    decision = route_task(TaskRequest(text="handle the thing we discussed"))
    assert decision.category is TaskCategory.UNKNOWN
    assert decision.tier is Tier.NORMAL


def test_an_empty_task_never_routes_fast() -> None:
    decision = route_task(TaskRequest(text="   "))
    assert decision.tier is not Tier.FAST


@pytest.mark.parametrize("tier", list(Tier))
def test_a_human_override_is_obeyed_and_recorded(tier: Tier) -> None:
    """An override that lowered the tier is the first thing to check when an answer was
    poor, so it has to appear in the reasons."""

    decision = route_task(
        TaskRequest(text="review the authentication on the admin route", forced_tier=tier)
    )
    assert decision.tier is tier
    if tier is not Tier.DEEP:
        assert f"forced_{tier.value}" in decision.reasons


def test_the_decision_carries_the_reason_it_escalated() -> None:
    """"It was expensive" is not an explanation. The escalating signal has to be named."""

    decision = route_task(TaskRequest(text="does this leak a customer email address"))
    assert decision.escalation_reasons
    assert "security_or_privacy_relevant" in decision.reasons


def test_the_logged_decision_contains_no_task_text() -> None:
    """The session log must not become a place where a pasted secret ends up."""

    secret = "the api key is sk-do-not-log-me and the password is hunter2"
    payload = route_task(TaskRequest(text=secret)).to_dict()
    assert "sk-do-not-log-me" not in str(payload)
    assert "hunter2" not in str(payload)
