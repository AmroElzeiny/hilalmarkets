"""What the engineering assistant may run, asserted across the whole family.

The point of these tests is not that one particular command is refused. It is that every
*way of writing* a forbidden action is refused, in every shell the assistant can reach.
A policy that stops `rm -rf` but not `Remove-Item -Recurse` protects a Linux developer
and nobody else, and this repository is worked on from Windows.
"""

from __future__ import annotations

import json

import pytest

from hm_oi.guard import guard_is_installed, install_guard, uninstall_guard
from hm_oi.permissions import (
    BUILTIN_RULES,
    Decision,
    PermissionPolicy,
    Rule,
    load_policy,
)

POLICY = load_policy()


# ---------------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------------

SECRET_READS = [
    "cat .env",
    "Get-Content .env",
    "type .env",
    "less .env",
    "head -n 5 .env",
    "Get-Content .env.production",
    "cat .env.production",
    "grep OPENAI_API_KEY .env",
    "Select-String -Path .env -Pattern KEY",
    "python -c \"print(open('.env').read())\"",
    "cat ~/.ssh/id_rsa",
    "Get-Content $HOME\\.aws\\credentials",
    "cat service-account.json",
    "cat secrets.yaml",
]


@pytest.mark.parametrize("command", SECRET_READS)
def test_no_way_of_reading_a_secret_file_is_allowed(command: str) -> None:
    """Reading is refused, not only writing.

    The transcript of a session is sent to a model provider. A key that is read is a key
    that has left the machine, whether or not anything else is done with it.
    """

    verdict = POLICY.evaluate(command)
    assert verdict.refused, f"{command!r} was {verdict.decision}"


SAFE_EXAMPLE_READS = [
    "cat .env.example",
    "Get-Content .env.example",
    "Get-Content .env.production.example",
    "grep OPENAI .env.example",
]


@pytest.mark.parametrize("command", SAFE_EXAMPLE_READS)
def test_the_checked_in_example_files_stay_readable(command: str) -> None:
    """``.env.example`` holds placeholders and is in Git.

    Refusing it would block the normal way of finding out which settings exist, and a
    policy that refuses harmless things is a policy people learn to work around.
    """

    verdict = POLICY.evaluate(command)
    assert verdict.allowed, f"{command!r} was {verdict.decision} by {verdict.rule_id}"


# ---------------------------------------------------------------------------------
# Production
# ---------------------------------------------------------------------------------

PRODUCTION_ACTIONS = [
    "psql postgres://u:p@db.prod.example.com/hm -c 'select 1'",
    "psql postgresql://u:p@10.1.2.3/hm",
    "pg_dump -h db.prod.example.com hm > dump.sql",
    "dropdb hilalmarkets",
    "redis-cli -h cache.prod.example.com FLUSHALL",
    "redis-cli FLUSHDB",
    "kubectl delete pod hilalmarkets-web-0",
    "helm upgrade hilalmarkets ./chart",
    "terraform apply",
    "docker push registry.example.com/hilalmarkets:latest",
    "systemctl restart hilalmarkets",
    "service hilalmarkets restart",
    "ssh deploy@vps.example.com",
    "docker compose -f docker-compose.prod.yml up -d",
    "bash deploy/release.sh",
    "AI_FEATURES_DISABLED=planner",
]


@pytest.mark.parametrize("command", PRODUCTION_ACTIONS)
def test_production_is_out_of_reach(command: str) -> None:
    """Requirement 10: the assistant cannot modify production-authoritative state."""

    verdict = POLICY.evaluate(command)
    assert verdict.refused, f"{command!r} was {verdict.decision}"


# ---------------------------------------------------------------------------------
# Governed product decisions
# ---------------------------------------------------------------------------------

GOVERNED_ACTIONS = [
    "UPDATE strategies SET sharia_status = 'halal' WHERE id = 1",
    "INSERT INTO sharia_assessments (status) VALUES ('halal')",
    "publish sharia status for BTC",
    "approve strategy 42",
    "activate monitor for user 7",
    "python scripts/bootstrap_governance_owner.py",
    "python scripts/grant_lifetime_admin.py",
    "UPDATE billing_events SET plan_code = 'pro'",
    "entitlement = 'unlimited'",
    "stripe charges create --amount 1000",
]


@pytest.mark.parametrize("command", GOVERNED_ACTIONS)
def test_governed_decisions_are_never_the_assistants_to_make(command: str) -> None:
    """Sharia status, approval, activation and money all have their own authority.

    Refused even against a local database. A habit formed locally is the habit that gets
    used somewhere it matters.
    """

    verdict = POLICY.evaluate(command)
    assert verdict.refused, f"{command!r} was {verdict.decision}"


# ---------------------------------------------------------------------------------
# Destruction
# ---------------------------------------------------------------------------------

DESTRUCTIVE_ACTIONS = [
    "rm -rf build",
    "rm -f important.py",
    "Remove-Item -Recurse -Force reports",
    "rmdir /s /q dist",
    "python -c \"import shutil; shutil.rmtree('src')\"",
    "git reset --hard origin/main",
    "git clean -fdx",
    "git push --force origin main",
    "git filter-branch --tree-filter 'rm -f secret' HEAD",
    "alembic downgrade -1",
    "Remove-Item alembic/versions/abc123.py",
    "DROP TABLE strategies",
    "TRUNCATE TABLE users",
    "DELETE FROM alerts",
]


@pytest.mark.parametrize("command", DESTRUCTIVE_ACTIONS)
def test_nothing_irreversible_is_allowed(command: str) -> None:
    verdict = POLICY.evaluate(command)
    assert verdict.refused, f"{command!r} was {verdict.decision}"


# ---------------------------------------------------------------------------------
# Needs a person
# ---------------------------------------------------------------------------------

NEEDS_A_PERSON = [
    "git push origin feature-branch",
    "git commit -m 'fix'",
    "git merge main",
    "git rebase main",
    "gh pr merge 42",
    "gh pr create --title x",
    "pip install requests",
    "npm install left-pad",
    "hm-chatbot-eval run --mode smoke",
    "powershell -File scripts/run_isolated_setup_chat_smoke.ps1",
    "curl -X POST https://example.com/hook -d '{}'",
    "requests.post('https://example.com', json={})",
]


@pytest.mark.parametrize("command", NEEDS_A_PERSON)
def test_consequential_but_legitimate_actions_stop_for_a_person(command: str) -> None:
    verdict = POLICY.evaluate(command)
    assert verdict.decision is Decision.CONFIRM, f"{command!r} was {verdict.decision}"


# ---------------------------------------------------------------------------------
# Ordinary work is not blocked
# ---------------------------------------------------------------------------------

ORDINARY_WORK = [
    ".venv/Scripts/python -m pytest tests/unit -q",
    ".venv/Scripts/python -m pytest tests/unit/test_x.py::test_y -q -p no:randomly",
    ".venv/Scripts/python -m ruff check .",
    ".venv/Scripts/python -m mypy src/ai_market_monitor src/hm_chatbot_eval",
    ".venv/Scripts/python scripts/replay_recorded_turns.py --run abc",
    ".venv/Scripts/python scripts/check_release_invariants.py",
    ".venv/Scripts/python -m alembic heads",
    "git status --porcelain",
    "git diff -- src/ai_market_monitor/engine/comparators.py",
    "git log --oneline -20",
    "git grep -n route_task -- src",
    "Get-Content src/ai_market_monitor/engine/comparators.py",
    "ls src/ai_market_monitor/services",
    "Get-ChildItem tests -Recurse -Filter test_*.py",
]


@pytest.mark.parametrize("command", ORDINARY_WORK)
def test_the_work_the_assistant_exists_to_do_is_allowed(command: str) -> None:
    """A policy that blocks the job is a policy that gets switched off."""

    verdict = POLICY.evaluate(command)
    assert verdict.allowed, (
        f"{command!r} was {verdict.decision} by {verdict.rule_id}: {verdict.reason}"
    )


# ---------------------------------------------------------------------------------
# The policy file cannot weaken the policy
# ---------------------------------------------------------------------------------


def test_a_config_file_cannot_switch_off_a_built_in_rule(tmp_path) -> None:
    """The assistant can edit ``.agents/permissions.json``. That is the whole problem.

    A safety file a session can write its way out of is not a safety file, so the file
    may only add rules and may only make an existing one stricter.
    """

    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "alembic.ini").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    agents = tmp_path / ".agents"
    agents.mkdir()
    (agents / "permissions.json").write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "rule_id": "secret.env_file",
                        "pattern": r"\.env",
                        "decision": "allow",
                        "reason": "we would like to read secrets please",
                    },
                    {
                        "rule_id": "production.database",
                        "pattern": "psql",
                        "decision": "confirm",
                        "reason": "downgrade an outright refusal",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    policy = load_policy(tmp_path)
    assert policy.evaluate("cat .env").refused
    assert policy.evaluate("psql postgres://u:p@db.prod.example.com/x").refused


def test_a_config_file_may_add_a_stricter_rule(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "alembic.ini").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    agents = tmp_path / ".agents"
    agents.mkdir()
    (agents / "permissions.json").write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "rule_id": "local.no_notebooks",
                        "pattern": r"jupyter\s+notebook",
                        "decision": "deny",
                        "reason": "not used in this repository",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    policy = load_policy(tmp_path)
    assert policy.evaluate("jupyter notebook").refused
    assert policy.evaluate("git status").allowed


def test_the_strictest_matching_rule_wins() -> None:
    """Stopping at the first match would let a permissive rule shadow a refusal."""

    policy = PermissionPolicy(
        rules=(
            Rule("a.allow", "deploy", Decision.ALLOW, "harmless"),
            Rule("b.deny", "deploy", Decision.DENY, "not harmless"),
            Rule("c.confirm", "deploy", Decision.CONFIRM, "ask"),
        )
    )
    verdict = policy.evaluate("deploy now")
    assert verdict.decision is Decision.DENY
    assert set(verdict.matched_rules) == {"a.allow", "b.deny", "c.confirm"}


def test_every_built_in_rule_explains_itself_in_plain_words() -> None:
    """The reason is printed to a person mid-task. It has to be readable.

    A rule id alone tells an engineer nothing about why the tool stopped, and a refusal
    nobody understands is a refusal somebody disables.
    """

    for rule in BUILTIN_RULES:
        assert rule.reason.strip(), f"{rule.rule_id} has no reason"
        assert len(rule.reason) > 30, f"{rule.rule_id} reason is too terse"
        assert rule.reason[0].isupper(), f"{rule.rule_id} reason is not a sentence"


# ---------------------------------------------------------------------------------
# The guard: the policy applied where code actually runs
# ---------------------------------------------------------------------------------


class _FakeTerminal:
    def __init__(self) -> None:
        self.executed: list[tuple[str, str]] = []

    def run(self, language: str, code: str, stream: bool = False, display: bool = False):
        self.executed.append((language, code))
        return [{"type": "console", "format": "output", "content": "ran\n"}]


class _FakeComputer:
    def __init__(self) -> None:
        self.terminal = _FakeTerminal()


class _FakeInterpreter:
    """Open Interpreter's shape, reduced to what the guard touches."""

    def __init__(self, auto_run: bool = False) -> None:
        self.computer = _FakeComputer()
        self.auto_run = auto_run


@pytest.mark.parametrize(
    "command", SECRET_READS + PRODUCTION_ACTIONS + GOVERNED_ACTIONS + DESTRUCTIVE_ACTIONS
)
def test_a_refused_command_never_reaches_the_shell(command: str) -> None:
    """This is the test that matters.

    The policy returning "deny" is an opinion. What has to be true is that the code was
    never executed, and the only way to know that is to watch the executor.
    """

    interpreter = _FakeInterpreter()
    install_guard(interpreter, POLICY, log=False)

    output = interpreter.computer.terminal.run("shell", command)

    assert interpreter.computer.terminal.executed == [], (
        f"{command!r} reached the shell"
    )
    assert "REFUSED" in output[0]["content"]


def test_allowed_commands_still_run() -> None:
    interpreter = _FakeInterpreter()
    install_guard(interpreter, POLICY, log=False)
    interpreter.computer.terminal.run("shell", "git status --porcelain")
    assert interpreter.computer.terminal.executed == [
        ("shell", "git status --porcelain")
    ]


def test_an_unattended_session_also_refuses_what_needs_a_person() -> None:
    """With nobody watching, "ask first" cannot mean "go ahead"."""

    interpreter = _FakeInterpreter(auto_run=True)
    install_guard(interpreter, POLICY, log=False)

    output = interpreter.computer.terminal.run("shell", "git push origin main")

    assert interpreter.computer.terminal.executed == []
    assert "NEEDS A PERSON" in output[0]["content"]


def test_a_watched_session_lets_a_person_decide() -> None:
    """When ``auto_run`` is off, Open Interpreter already shows every block and waits.

    The guard must not refuse on top of that, or a confirm rule would become a deny.
    """

    interpreter = _FakeInterpreter(auto_run=False)
    install_guard(interpreter, POLICY, log=False)
    interpreter.computer.terminal.run("shell", "git push origin main")
    assert interpreter.computer.terminal.executed == [("shell", "git push origin main")]


def test_streaming_callers_get_something_they_can_iterate() -> None:
    """``stream=True`` expects an iterator. Returning the list would iterate its keys."""

    interpreter = _FakeInterpreter()
    install_guard(interpreter, POLICY, log=False)
    chunks = list(interpreter.computer.terminal.run("shell", "cat .env", stream=True))
    assert chunks and "REFUSED" in chunks[0]["content"]


def test_installing_the_guard_twice_does_not_stack_it() -> None:
    interpreter = _FakeInterpreter()
    install_guard(interpreter, POLICY, log=False)
    first = interpreter.computer.terminal.run
    install_guard(interpreter, POLICY, log=False)
    assert interpreter.computer.terminal.run is first


def test_the_guard_can_be_removed_again_for_tests_only() -> None:
    interpreter = _FakeInterpreter()
    install_guard(interpreter, POLICY, log=False)
    assert guard_is_installed(interpreter)
    assert uninstall_guard(interpreter)
    assert not guard_is_installed(interpreter)
    interpreter.computer.terminal.run("shell", "cat .env")
    assert interpreter.computer.terminal.executed == [("shell", "cat .env")]


def test_a_session_is_refused_when_the_guard_has_nothing_to_attach_to() -> None:
    """An Open Interpreter whose executor moved must stop the session, not run unguarded.

    Silently unprotected looks exactly like well-behaved: nothing is ever refused.
    """

    from hm_oi.guard import GuardSurfaceMissing

    class _Unrecognised:
        pass

    with pytest.raises(GuardSurfaceMissing):
        install_guard(_Unrecognised(), POLICY, log=False)


@pytest.mark.parametrize("language", ["python", "shell", "powershell", "javascript", "ruby"])
def test_every_language_goes_through_the_same_check(language: str) -> None:
    """Open Interpreter supports ten languages. All of them reach one executor, and a
    guard that only understood shell would be trivially avoidable."""

    interpreter = _FakeInterpreter()
    install_guard(interpreter, POLICY, log=False)
    interpreter.computer.terminal.run(language, "open('.env').read()")
    assert interpreter.computer.terminal.executed == []
