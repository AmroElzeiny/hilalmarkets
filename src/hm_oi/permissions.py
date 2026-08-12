"""What the engineering assistant may run, and what it may never run.

Two things make this more than a list of good intentions:

* The rules below are **built into the code**. ``.agents/permissions.json`` can add rules
  and can tighten an ``allow`` to a ``confirm``, but it cannot delete a rule or loosen
  one. A safety file that a session can edit its way out of is not a safety file, and the
  assistant has write access to the repository it is protecting.
* The verdict is applied by ``hm_oi.guard``, which wraps Open Interpreter's own code
  execution. The check happens between "the model proposed this" and "the shell ran it",
  so refusing is not a matter of the model choosing to obey.

**The honest limit.** Screening is done on the text of the command. It reliably stops the
things that actually go wrong — a session that reaches for ``.env`` while looking for a
setting, a cleanup that walks into ``alembic/versions``, a "let me just check production"
moment at the end of a long debugging session. It does not stop somebody who is trying to
get around it: text can be assembled at runtime, base64-decoded, or written to a file and
executed from there. This is a guard rail against accidents by a capable assistant, not a
sandbox against an adversary. Running the assistant with production credentials present
in the environment is unsafe no matter what this module says, which is why the launcher
starts a session with those variables stripped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from hm_oi.paths import policy_path, repo_root


class Decision(StrEnum):
    """What happens to a command."""

    ALLOW = "allow"
    #: Runs only after a person reads it and agrees. Open Interpreter already has this
    #: behaviour; the policy decides which commands cannot skip it.
    CONFIRM = "confirm"
    DENY = "deny"


#: Ordered weakest to strongest. Used to combine verdicts: when two rules match, the
#: stricter one wins, always.
_STRICTNESS: Final[dict[Decision, int]] = {
    Decision.ALLOW: 0,
    Decision.CONFIRM: 1,
    Decision.DENY: 2,
}

#: The start of a command-line flag, for patterns that need to spot ``-f`` or ``-X``.
#:
#: Written once because the obvious spelling is wrong in a way that silently disables the
#: rule. ``\b-X`` never matches ``curl -X POST``: ``\b`` needs a word character on one
#: side, and in ``" -X"`` the character before the dash is a space while the dash itself
#: is not a word character, so there is no boundary there at all. The rule then looks
#: correct, tests as present, and refuses nothing. Two rules had this before it was
#: given a name — the deploy rule and the outbound-request rule — so it is a class, not
#: a typo.
FLAG: Final[str] = r"(?<![\w-])"


@dataclass(frozen=True, slots=True)
class Rule:
    """One pattern and what it means."""

    rule_id: str
    pattern: str
    decision: Decision
    #: Written for a person, in plain words, because it is printed when a command is
    #: refused and the reader is mid-task and probably annoyed.
    reason: str

    def matches(self, text: str) -> bool:
        return bool(re.search(self.pattern, text, re.IGNORECASE))


@dataclass(frozen=True, slots=True)
class PermissionVerdict:
    """The answer for one command, and which rule produced it."""

    decision: Decision
    rule_id: str | None
    reason: str
    #: Every rule that matched, so an over-broad pattern is visible rather than guessed at.
    matched_rules: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW

    @property
    def refused(self) -> bool:
        return self.decision is Decision.DENY

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": str(self.decision),
            "rule_id": self.rule_id,
            "reason": self.reason,
            "matched_rules": list(self.matched_rules),
        }


# --------------------------------------------------------------------------------------
# Built-in rules. Never removable.
#
# Grouped by what the rule protects, because that is how somebody reviewing them will
# want to read them. Within a group the order does not matter: the strictest match wins.
# --------------------------------------------------------------------------------------

#: Secrets. Reading them is enough to leak them into a transcript that goes to a model
#: provider, so reading is refused, not only writing.
_SECRET_RULES: Final[tuple[Rule, ...]] = (
    Rule(
        "secret.env_file",
        # ``.env`` and ``.env.production`` are refused; ``.env.example`` and
        # ``.env.production.example`` are not. The two example files are checked into
        # Git, hold only placeholders, and are the normal way to find out which settings
        # exist — refusing them would block ordinary investigation and teach the reader
        # that the policy is noise. The trailing ``(?![\w.])`` is what makes the
        # exception work: without it, ``.env.example`` still matches on its ``.env``
        # prefix alone.
        r"(?<![\w.\-])\.env(?:\.[\w.\-]+)?(?<!\.example)(?![\w.])",
        Decision.DENY,
        "Environment files hold live keys. Reading one would copy it into the "
        "conversation, which is sent to a model provider. Ask a person for the value. "
        "(.env.example is allowed and lists every setting.)",
    ),
    Rule(
        "secret.credential_files",
        r"(?:id_rsa|id_ed25519|\.pem\b|\.pfx\b|\.p12\b|\.netrc|_netrc|"
        r"credentials\.json|service[-_]account.*\.json|\.aws[/\\]credentials|"
        r"\.ssh[/\\]|\.kube[/\\]config|secrets?\.(?:json|ya?ml|toml))",
        Decision.DENY,
        "This looks like a credential file. It is never needed to investigate the "
        "repository.",
    ),
    Rule(
        "secret.env_dump",
        r"\b(?:printenv|env)\b\s*(?:\||$)|Get-ChildItem\s+env:|\$env:\w*(?:KEY|SECRET|TOKEN|PASSWORD)"
        r"|os\.environ\b(?!\.get\(\s*[\"']HM_OI)",
        Decision.CONFIRM,
        "Printing the environment can reveal keys. Say which single variable you need.",
    ),
)

#: Production. The assistant has no business reaching a live system at all in OI-1.
_PRODUCTION_RULES: Final[tuple[Rule, ...]] = (
    Rule(
        "production.database",
        r"(?:psql|pg_dump|pg_restore|dropdb|createdb)\b|"
        r"postgres(?:ql)?(?:\+\w+)?://(?!\w*:?\w*@?(?:localhost|127\.0\.0\.1))|"
        r"DATABASE_URL\s*=\s*[\"']?postgres",
        Decision.DENY,
        "This points at a database server that is not the local one. Production data is "
        "never changed or read from here.",
    ),
    Rule(
        "production.redis",
        r"redis-cli\b|redis://(?!\w*:?\w*@?(?:localhost|127\.0\.0\.1))|"
        r"\bFLUSHALL\b|\bFLUSHDB\b",
        Decision.DENY,
        "Redis holds queues and locks for the running product. Clearing or editing it "
        "from here would affect live work.",
    ),
    Rule(
        "production.deploy",
        r"\b(?:docker\s+compose|docker-compose)\b.*(?:" + FLAG + r"-f\s+\S*prod|\bup\b.*\bprod)|"
        r"\bdocker\s+push\b|\bkubectl\b|\bhelm\b|\bterraform\b|\bansible\b|"
        r"\bfly\s+deploy\b|\brailway\s+up\b|\bvercel\s+(?:deploy|--prod)|"
        r"deploy[/\\].*\.(?:sh|ps1)\b|\bsystemctl\b|\bservice\s+\w+\s+restart\b",
        Decision.DENY,
        "Deploying or restarting a service is an operator action with a human on the "
        "other end of it. This tool investigates and reports.",
    ),
    Rule(
        "production.remote_shell",
        r"\bssh\s+[\w.\-]+@|\bscp\b|\brsync\b\s+.*:|\bcloudflared\b\s+tunnel",
        Decision.DENY,
        "Connecting to another machine is outside what an engineering assistant does "
        "here.",
    ),
    Rule(
        "production.feature_flags",
        r"AI_FEATURES_DISABLED\s*=|feature[-_ ]flag.*(?:prod|production)|"
        # The checked-in ``.env.production.example`` is a placeholder template, not the
        # real file, and reading it is how somebody finds out which settings production
        # expects.
        r"\.env\.production(?!\.example)",
        Decision.DENY,
        "Feature switches change what live customers see. Change them through the "
        "normal operator process.",
    ),
)

#: Governed product decisions. These have their own authority in the product and the
#: assistant is not part of it. Refused even against a local database, because a habit
#: formed locally is the habit that gets used elsewhere.
_GOVERNED_RULES: Final[tuple[Rule, ...]] = (
    Rule(
        "governed.sharia",
        # Up to three words may sit between the verb and the subject, so
        # ``INSERT INTO sharia_assessments`` and ``UPDATE the halal flag`` are both
        # caught. Matching only adjacent words missed every real SQL statement, which is
        # the shape this actually arrives in.
        r"(?:publish|approve|assign|set|update|insert|delete)\w*\b(?:\s+\w+){0,3}?"
        r"[\s_\-]{0,3}(?:sharia|shariah|halal|haram)|"
        r"(?:sharia|shariah)\w*[\s_\-]{0,3}(?:status|ruling|verdict|decision)\s*=|"
        r"bootstrap_governance_owner\.py|import_sharia_methodology_pack\.py",
        Decision.DENY,
        "Sharia status comes from the platform's own review process with evidence "
        "behind it. No tool, and no model, may set or publish it.",
    ),
    Rule(
        "governed.approval_activation",
        r"(?:approve|activate|publish)[\s_\-]{0,3}(?:strategy|setup|version|draft|monitor)|"
        r"strategy[\s_\-]{0,3}(?:approval|activation)\b.*(?:=|insert|update|post)|"
        r"grant_lifetime_admin\.py",
        Decision.DENY,
        "Approving or activating a setup is something the account owner does, signed in, "
        "against a checked copy of the rules. It cannot come from here.",
    ),
    Rule(
        "governed.billing",
        r"\bstripe\b(?!.*\b(?:test|docs|read)\b)|nowpayments|creem\b|"
        r"(?:insert|update|delete)\w*\s+.{0,40}\b(?:billing|invoice|subscription|"
        r"entitlement|plan_code|payment)\b|entitlement\w*\s*=",
        Decision.DENY,
        "Money and plan access are changed by the billing system, not by an assistant.",
    ),
)

#: Destroying things. The repository is a working tree with uncommitted work in it.
_DESTRUCTIVE_RULES: Final[tuple[Rule, ...]] = (
    Rule(
        "destructive.recursive_delete",
        r"\brm\s+-[rf]{1,2}\b|\bRemove-Item\b(?=.*-Recurse)|\brmdir\s+/s\b|"
        r"\bdel\s+/[sq]\b|shutil\.rmtree|\bformat\s+[a-z]:",
        Decision.DENY,
        "Deleting a directory tree is not something this tool needs to do, and it cannot "
        "be undone.",
    ),
    Rule(
        "destructive.migrations_and_history",
        r"alembic[/\\]versions[/\\].*\b(?:rm|del|Remove-Item|unlink)|"
        r"\b(?:rm|del|Remove-Item)\b.*alembic|alembic\s+downgrade\b|"
        r"\bgit\s+(?:reset\s+--hard|clean\s+-[fdx]|filter-branch|reflog\s+expire)|"
        r"\bgit\s+push\b.*--force",
        Decision.DENY,
        "Migrations and Git history are the record of how the product got here. "
        "Rewriting or deleting them loses work that cannot be recovered.",
    ),
    Rule(
        "destructive.drop_or_truncate",
        r"\b(?:DROP\s+(?:TABLE|DATABASE|SCHEMA)|TRUNCATE\s+TABLE?|DELETE\s+FROM)\b",
        Decision.DENY,
        "This removes stored records. Investigation never needs to.",
    ),
)

#: Actions that are legitimate but that a person should see first.
_CONFIRM_RULES: Final[tuple[Rule, ...]] = (
    Rule(
        "confirm.git_push",
        r"\bgit\s+push\b",
        Decision.CONFIRM,
        "Pushing shares the work with everybody else. Confirm that this is what you "
        "asked for.",
    ),
    Rule(
        "confirm.git_write",
        r"\bgit\s+(?:commit|merge|rebase|cherry-pick|revert|tag|stash\s+(?:drop|clear))\b",
        Decision.CONFIRM,
        "This changes the branch you are on. Read it before it runs.",
    ),
    Rule(
        "confirm.pull_request",
        r"\bgh\s+pr\s+(?:merge|create|close|edit|ready)\b|\bgh\s+release\b|"
        r"\bgh\s+(?:issue|api)\s+.*\b(?:-X|--method)\s*(?:POST|PATCH|PUT|DELETE)",
        Decision.CONFIRM,
        "Creating or merging a pull request is a decision with reviewers attached. "
        "OI-1 reads GitHub; it does not act on it.",
    ),
    Rule(
        "confirm.install",
        r"\bpip\s+(?:install|uninstall)\b|\bnpm\s+(?:install|i|uninstall)\b|"
        r"\bpnpm\s+(?:add|remove)\b|\bwinget\s+install\b|\bchoco\s+install\b|"
        r"\bpoetry\s+(?:add|remove)\b",
        Decision.CONFIRM,
        "Installing a package changes the environment every later test runs in, and the "
        "dependency pins are checked by the release gate.",
    ),
    Rule(
        "confirm.paid_evaluator",
        r"\bhm-chatbot-eval\b\s+(?:run|batch-submit|replay|launch-core)|"
        r"run_isolated_setup_chat_smoke\.ps1",
        Decision.CONFIRM,
        "This calls a paid model provider for real. Say why it is needed and what budget "
        "applies before it runs.",
    ),
    Rule(
        "confirm.network_write",
        r"\bcurl\b.*(?:" + FLAG + r"-X\s*(?:POST|PUT|PATCH|DELETE)|--data|"
        + FLAG + r"-d\s)|"
        r"Invoke-(?:RestMethod|WebRequest).*-Method\s*(?:Post|Put|Patch|Delete)|"
        r"requests\.(?:post|put|patch|delete)\(",
        Decision.CONFIRM,
        "This sends data to another service. Check where it is going.",
    ),
    Rule(
        "confirm.write_outside_repo",
        r"(?:>|>>|Out-File|Set-Content|open\()\s*[\"']?(?:[A-Za-z]:[/\\]|/(?:etc|usr|bin|var)/|~[/\\])",
        Decision.CONFIRM,
        "This writes outside the repository. Temporary files belong under test-results "
        "or reports.",
    ),
)

#: The whole set, in the order they are reported. Assembled once so a caller cannot
#: accidentally evaluate against a subset.
BUILTIN_RULES: Final[tuple[Rule, ...]] = (
    *_SECRET_RULES,
    *_PRODUCTION_RULES,
    *_GOVERNED_RULES,
    *_DESTRUCTIVE_RULES,
    *_CONFIRM_RULES,
)


#: Repository areas the assistant may read freely. Everything under the repository root
#: is readable except what a rule refuses; this list exists for the doctor report and for
#: the instructions given to the model, not as a second enforcement path.
READABLE_AREAS: Final[tuple[str, ...]] = (
    "src/",
    "tests/",
    "scripts/",
    "docs/",
    "alembic/",
    "Notion/",
    ".github/",
    "*.md",
    "pyproject.toml",
    ".env.example",
)

#: Where generated files may be written. All three are already ignored by Git and
#: already refused by ``scripts/check_release_invariants.py`` if they were ever staged,
#: so a stray artifact cannot reach a commit.
WRITABLE_ARTIFACT_AREAS: Final[tuple[str, ...]] = (
    "reports/",
    "test-results/",
    "playwright-report/",
)


@dataclass(frozen=True, slots=True)
class PermissionPolicy:
    """The rules in force, built-in plus any additions from the repository."""

    rules: tuple[Rule, ...] = BUILTIN_RULES

    def evaluate(self, command: str) -> PermissionVerdict:
        """What happens if this text is executed.

        Every rule is checked, not just the first match, and the strictest verdict wins.
        Stopping at the first match would let an ``allow`` added to the config file sit in
        front of a built-in ``deny`` and cancel it.
        """

        text = str(command or "")
        if not text.strip():
            return PermissionVerdict(Decision.ALLOW, None, "Nothing to run.")

        matched = [rule for rule in self.rules if rule.matches(text)]
        if not matched:
            return PermissionVerdict(
                Decision.ALLOW, None, "No rule objects to this.", ()
            )

        strictest = max(matched, key=lambda rule: _STRICTNESS[rule.decision])
        return PermissionVerdict(
            decision=strictest.decision,
            rule_id=strictest.rule_id,
            reason=strictest.reason,
            matched_rules=tuple(rule.rule_id for rule in matched),
        )

    def rules_by_decision(self, decision: Decision) -> tuple[Rule, ...]:
        return tuple(rule for rule in self.rules if rule.decision is decision)


def load_policy(root: Path | None = None) -> PermissionPolicy:
    """The built-in rules, plus anything ``.agents/permissions.json`` adds.

    The file may only add. A rule in the file that reuses a built-in ``rule_id`` is
    ignored unless it is *stricter*, so the file cannot be used to switch a protection
    off — which matters because the assistant can edit that file.
    """

    root = root or repo_root()
    path = policy_path(root)
    extra: list[Rule] = []
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        builtin_by_id = {rule.rule_id: rule for rule in BUILTIN_RULES}
        for raw in payload.get("rules", []) if isinstance(payload, dict) else []:
            if not isinstance(raw, dict):
                continue
            try:
                decision = Decision(str(raw.get("decision", "")).casefold())
                rule = Rule(
                    rule_id=str(raw["rule_id"]),
                    pattern=str(raw["pattern"]),
                    decision=decision,
                    reason=str(raw.get("reason") or "Refused by a repository rule."),
                )
                re.compile(rule.pattern)
            except (KeyError, ValueError, re.error):
                continue
            existing = builtin_by_id.get(rule.rule_id)
            if existing is not None and (
                _STRICTNESS[rule.decision] <= _STRICTNESS[existing.decision]
            ):
                continue
            extra.append(rule)
    return PermissionPolicy(rules=(*BUILTIN_RULES, *tuple(extra)))
