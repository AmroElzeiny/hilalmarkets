"""Validation cases 5 and 7: permissions and redaction.

Case 5 asks that four things fail: reading `.env`, writing a production database,
`git push origin main`, and a destructive filesystem command. The test records *which
layer* refuses each, because "the model declined" and "the harness refused" are not the
same control and only one of them is worth relying on.

Case 7 asks that a synthetic seed phrase and a synthetic API key never reach a log, an
audit record, a prompt, a commit, or a pull-request body.

Every secret in this file is invented and matches nothing real.
"""

from __future__ import annotations

import json

import pytest

from hm_oi.audit import AuditRecord, AuditRefused, write_record
from hm_oi.builder_permissions import BUILDER_RULES, builder_policy, is_strictly_tighter
from hm_oi.conversation_source import (
    ALLOWED_CORPORA,
    ConversationSourceRefused,
    load_conversations,
    resolve_corpus,
)
from hm_oi.permissions import BUILTIN_RULES, Decision
from hm_oi.redaction import (
    PLACEHOLDER,
    SECRET_PATTERNS,
    SecretLeak,
    contains_secret,
    find_secrets,
    redact,
    redact_structure,
    refuse_if_secret,
)
from hm_oi.workspace import (
    BRANCH_PREFIX,
    PROTECTED_BRANCHES,
    WorkspaceRefused,
    branch_name,
    guard_worktree_operation,
)

# Invented. Shaped like the real thing, valid nowhere.
FAKE_SEED = (
    "abandon ability able about above absent absorb abstract absurd abuse access accident"
)
FAKE_OPENAI_KEY = "sk-proj0000FAKE1111NOTREAL2222abcdefghijklmn"
FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.c2lnbmF0dXJl"
FAKE_TELEGRAM = "123456789:AAFakeTokenNotRealAtAllabcdefghijklmno"
FAKE_XPRV = "xprv9s21ZrQH143K3FAKEnotarealkeyatallzzzzzzzzzzzz"


# ----------------------------------------------------------------------------------
# Validation case 5 — four commands, all must fail.
# ----------------------------------------------------------------------------------

CASE_5_COMMANDS = {
    "read .env": (
        "cat .env",
        "type .env",
        "Get-Content .env.production",
        "open('.env').read()",
    ),
    "production database write": (
        "psql postgres://user:pw@prod.example/db -c \"UPDATE users SET plan='pro'\"",
        "DELETE FROM users WHERE id = 1",
        "redis-cli -h prod.example FLUSHALL",
    ),
    "git push to main": (
        "git push origin main",
        "git push",
        "git push --force origin main",
    ),
    "destructive filesystem": (
        "rm -rf src",
        "Remove-Item -Recurse -Force src",
        "shutil.rmtree('src')",
    ),
}


@pytest.mark.parametrize(
    "label,command",
    [(label, cmd) for label, cmds in CASE_5_COMMANDS.items() for cmd in cmds],
)
def test_case_5_forbidden_commands_are_denied(label: str, command: str) -> None:
    """All four families are refused outright in autonomous mode."""

    verdict = builder_policy().evaluate(command)
    assert verdict.decision is Decision.DENY, (
        f"{label}: {command!r} was {verdict.decision.value}, not denied "
        f"(rule: {verdict.rule_id})"
    )
    assert verdict.reason, "a refusal must explain itself"


def test_case_5_push_is_only_confirm_in_interactive_mode() -> None:
    """The difference autonomous mode makes, stated as a test.

    OI-1 asks a person about `git push`. That is a real control while a person is
    there. Unattended, there is nobody to ask, so it becomes a refusal.
    """

    from hm_oi.permissions import PermissionPolicy

    interactive = PermissionPolicy(rules=BUILTIN_RULES).evaluate("git push origin main")
    autonomous = builder_policy().evaluate("git push origin main")
    assert interactive.decision is Decision.CONFIRM
    assert autonomous.decision is Decision.DENY


@pytest.mark.parametrize(
    "command",
    (
        "git push",
        "git merge main",
        "git rebase main",
        "gh pr merge 12",
        "gh pr create --fill",
        "pip install requests",
        "curl -X POST https://example.com/hook -d @payload.json",
        "git branch -D oi/task",
        "git tag v1.0.0",
        "hm-chatbot-eval run --mode smoke",
        "SELECT * FROM users",
        "cat ai_market_monitor.db.bak-20260803",
        "docker compose -f docker-compose.prod.yml up -d",
        "kubectl apply -f deploy.yaml",
        "ssh deploy@prod.example",
    ),
)
def test_autonomous_mode_denies_every_reaching_command(command: str) -> None:
    assert builder_policy().evaluate(command).decision is Decision.DENY, command


@pytest.mark.parametrize(
    "command",
    (
        "git status --porcelain",
        "git diff HEAD",
        "git log --oneline -5",
        ".venv/Scripts/python -m pytest tests/unit -q",
        ".venv/Scripts/python -m ruff check src",
        ".venv/Scripts/python -m mypy src",
        ".venv/Scripts/python scripts/check_release_invariants.py",
        ".venv/Scripts/python -m alembic heads",
        "git grep -n comparators -- src",
    ),
)
def test_the_work_it_is_supposed_to_do_is_still_allowed(command: str) -> None:
    """A policy that refuses everything is not a policy. This is the control."""

    assert builder_policy().evaluate(command).decision is Decision.ALLOW, command


def test_autonomous_mode_is_never_looser_than_interactive_mode() -> None:
    """Across every command in this file, autonomous is >= interactive strictness."""

    commands = [cmd for cmds in CASE_5_COMMANDS.values() for cmd in cmds]
    commands += ["git status", "pytest tests/unit", "gh pr create", "pip install x"]
    for command in commands:
        assert is_strictly_tighter(command), command


def test_every_builder_rule_explains_itself() -> None:
    for rule in BUILDER_RULES:
        assert len(rule.reason) > 40, f"{rule.rule_id} needs a real explanation"
        assert rule.decision is Decision.DENY


# ----------------------------------------------------------------------------------
# Workspace: the branch boundary, checked by destination rather than by command text.
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize("branch", sorted(PROTECTED_BRANCHES))
def test_protected_branches_are_refused(branch: str) -> None:
    with pytest.raises(WorkspaceRefused):
        guard_worktree_operation("worktree", branch)


@pytest.mark.parametrize(
    "operation", ("push", "merge", "rebase", "tag", "reset", "filter-branch", "remote")
)
def test_escaping_git_operations_are_refused(operation: str) -> None:
    with pytest.raises(WorkspaceRefused):
        guard_worktree_operation(operation)


def test_branch_names_are_deterministic_and_traceable() -> None:
    assert branch_name("HM-123 fix the comparator") == branch_name("HM-123 fix the comparator")
    assert branch_name("HM-123 fix").startswith(BRANCH_PREFIX)
    assert "hm-123" in branch_name("HM-123 fix")


def test_a_branch_outside_the_prefix_is_refused() -> None:
    with pytest.raises(WorkspaceRefused):
        guard_worktree_operation("worktree", "feature/something")


# ----------------------------------------------------------------------------------
# Validation case 7 — no secret reaches a log, an audit record, a commit or a PR body.
# ----------------------------------------------------------------------------------

SECRET_SAMPLES = {
    "seed_phrase": FAKE_SEED,
    "openai_api_key": FAKE_OPENAI_KEY,
    "aws_access_key": FAKE_AWS_KEY,
    "json_web_token": FAKE_JWT,
    "telegram_bot_token": FAKE_TELEGRAM,
    "extended_private_key": FAKE_XPRV,
    "private_key_block": "-----BEGIN RSA PRIVATE KEY-----\nZmFrZQ==\n-----END RSA PRIVATE KEY-----",
    "bearer_token": "Authorization: Bearer abcdefghijklmnop",
    "secret_assignment": "api_key = notarealsecretvalue",
}


@pytest.mark.parametrize("kind,sample", sorted(SECRET_SAMPLES.items()))
def test_case_7_every_secret_shape_is_detected(kind: str, sample: str) -> None:
    assert contains_secret(sample), f"{kind} was not detected"


@pytest.mark.parametrize("kind,sample", sorted(SECRET_SAMPLES.items()))
def test_case_7_every_secret_shape_is_removed(kind: str, sample: str) -> None:
    cleaned = redact(sample)
    assert "REDACTED" in cleaned
    # The distinctive middle of the secret must be gone.
    core = sample.strip().split()[-1][:16]
    if len(core) >= 12:
        assert core not in cleaned, f"{kind}: the secret survived redaction"


def test_case_7_a_secret_cannot_reach_an_audit_record() -> None:
    record = AuditRecord(
        task_id="t-7",
        description=f"user pasted {FAKE_SEED} and {FAKE_OPENAI_KEY}",
        branch="oi/t-7",
        disposition="completed",
    )
    path = write_record(record)
    written = path.read_text(encoding="utf-8")
    assert FAKE_SEED not in written
    assert FAKE_OPENAI_KEY not in written
    assert "REDACTED" in written


def test_case_7_an_audit_record_refuses_forbidden_fields() -> None:
    record = AuditRecord(
        task_id="t-8",
        description="fine",
        branch="oi/t-8",
        disposition="completed",
        escalation={"reasoning": "the hidden chain of thought"},
    )
    with pytest.raises(AuditRefused, match="never-log list"):
        write_record(record)


@pytest.mark.parametrize("where", ("a commit message", "a pull-request body"))
def test_case_7_a_commit_or_pr_refuses_rather_than_redacts(where: str) -> None:
    """Silently rewriting somebody's commit message is worse than stopping."""

    with pytest.raises(SecretLeak, match="Refusing to write"):
        refuse_if_secret(f"fix: remove {FAKE_OPENAI_KEY}", where=where)


def test_case_7_nested_structures_are_redacted_all_the_way_down() -> None:
    payload = {"a": [{"b": {"c": FAKE_OPENAI_KEY}}], "d": (FAKE_SEED,)}
    cleaned = json.dumps(redact_structure(payload))
    assert FAKE_OPENAI_KEY not in cleaned
    assert FAKE_SEED not in cleaned


def test_redaction_is_not_so_broad_it_eats_ordinary_text() -> None:
    """A policy that redacts everything tells you nothing. The control case."""

    ordinary = "The RSI threshold should be 17, not 15, when the close is below the open."
    assert not contains_secret(ordinary)
    assert redact(ordinary) == ordinary


def test_truncation_happens_after_redaction() -> None:
    """Cutting first could leave half a key visible with the pattern no longer matching."""

    text = "x" * 50 + FAKE_OPENAI_KEY
    cleaned = redact(text, limit=60)
    assert FAKE_OPENAI_KEY not in cleaned


def test_the_placeholder_names_which_kind_was_found() -> None:
    assert PLACEHOLDER.format(kind="seed_phrase") in redact(FAKE_SEED)


def test_find_secrets_never_returns_the_secret() -> None:
    kinds = find_secrets(FAKE_OPENAI_KEY)
    assert kinds
    assert all(FAKE_OPENAI_KEY not in kind for kind in kinds)


def test_every_declared_pattern_has_a_sample() -> None:
    """Stops a pattern being added with nothing proving it works."""

    declared = {pattern.kind for pattern in SECRET_PATTERNS}
    covered = set(SECRET_SAMPLES) | {"private_key_header", "basic_credentials"}
    assert declared <= covered, f"no sample for: {sorted(declared - covered)}"


# ----------------------------------------------------------------------------------
# The precondition restriction: conversation material is fixtures only, in code.
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALLOWED_CORPORA)
def test_allowed_fixtures_load(name: str) -> None:
    cases = load_conversations(name, limit=3)
    assert cases, f"{name} produced nothing"


@pytest.mark.parametrize(
    "name",
    (
        "ai_market_monitor.db.bak-20260803",
        "ai_market_monitor.db",
        "../../etc/passwd",
        "tests/fixtures/../../.env",
        "/var/log/production.log",
        "postgres://prod/db",
        "reports/production_conversations.jsonl",
        "",
    ),
)
def test_everything_else_is_refused(name: str) -> None:
    with pytest.raises(ConversationSourceRefused):
        resolve_corpus(name)


def test_the_refusal_explains_the_precondition() -> None:
    with pytest.raises(ConversationSourceRefused, match="redaction and retention"):
        resolve_corpus("ai_market_monitor.db")


def test_loaded_conversations_are_redacted_on_the_way_in() -> None:
    """Redaction at the point of reading, not at the point of writing."""

    cases = load_conversations(ALLOWED_CORPORA[0])
    for case in cases:
        assert not contains_secret(case.text), f"{case.case_id} carried a secret through"
