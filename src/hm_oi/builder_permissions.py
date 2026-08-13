"""The permission policy for autonomous mode: OI-1's rules, strictly tightened.

OI-1's policy was written for a session with a person watching. Several of its rules are
``CONFIRM`` — "a person reads this before it runs" — and that is a real control while a
person is there. In autonomous mode nobody is there, so every ``CONFIRM`` is either
refused or it is nothing.

``hm_oi.guard`` already blocks ``CONFIRM`` when ``auto_run`` is on, which covers the
common case. This module goes further and converts the ones that must never happen into
outright ``DENY``, for two reasons:

* a ``DENY`` explains itself correctly. "Nobody is here to approve this" is the wrong
  message for ``git push origin main``, which would be refused even with a person
  present.
* it removes the dependency on ``auto_run`` being set correctly. A session started
  without the flag would otherwise fall back to the weaker interactive rules while
  running unattended.

The builder policy is only ever *stricter* than OI-1's. ``builder_policy`` asserts that,
and ``tests/oi/test_invariant_builder_permissions.py`` asserts it across every rule, so a
future edit cannot loosen autonomous mode by accident.
"""

from __future__ import annotations

from typing import Final

from hm_oi.permissions import (
    BUILTIN_RULES,
    FLAG,
    Decision,
    PermissionPolicy,
    Rule,
)

#: Refusals that exist only in autonomous mode. Each replaces an OI-1 rule that merely
#: asked a person, or closes something that only matters when nobody is watching.
BUILDER_RULES: Final[tuple[Rule, ...]] = (
    Rule(
        "builder.no_push",
        r"\bgit\s+push\b",
        Decision.DENY,
        "The autonomous builder never pushes. It commits to its own branch and stops "
        "there, so that a person decides what leaves this machine.",
    ),
    Rule(
        "builder.no_merge",
        r"\bgit\s+(?:merge|rebase|cherry-pick|revert)\b|\bgit\s+checkout\s+(?:main|master)\b|"
        r"\bgit\s+switch\s+(?:main|master)\b",
        Decision.DENY,
        "Merging, rebasing and moving onto main are decisions with a reviewer attached. "
        "The builder works on its own branch only.",
    ),
    Rule(
        "builder.no_branch_deletion",
        r"\bgit\s+branch\s+(?:-D|-d|--delete)\b|\bgit\s+tag\b|"
        r"\bgit\s+update-ref\b|\bgit\s+symbolic-ref\b",
        Decision.DENY,
        "A branch is the only output of an autonomous task. Deleting branches or moving "
        "tags would throw away the work a person still has to read.",
    ),
    Rule(
        "builder.no_pull_request_action",
        r"\bgh\s+pr\s+(?:merge|create|close|edit|ready|review)\b|\bgh\s+release\b|"
        r"\bgh\s+workflow\s+run\b|\bgh\s+api\b",
        Decision.DENY,
        "Opening or merging a pull request is a person's action. The builder produces a "
        "branch and a written summary; a human opens the request.",
    ),
    Rule(
        "builder.no_install",
        r"\bpip\s+(?:install|uninstall)\b|\bnpm\s+(?:install|i|uninstall)\b|"
        r"\bpnpm\s+(?:add|remove)\b|\bpoetry\s+(?:add|remove)\b|"
        r"\bwinget\s+install\b|\bchoco\s+install\b|\buv\s+(?:add|pip\s+install)\b",
        Decision.DENY,
        "Changing the installed packages changes what every later test runs against, and "
        "the release gate checks the pins. A dependency change is its own reviewed task.",
    ),
    Rule(
        "builder.no_paid_evaluator",
        r"\bhm-chatbot-eval\b\s+(?:run|batch-submit|replay|launch-core)|"
        r"run_isolated_setup_chat_smoke\.ps1",
        Decision.DENY,
        "A paid evaluator run costs real money and needs a stated budget. An unattended "
        "task may not decide to spend it.",
    ),
    Rule(
        "builder.no_outbound_write",
        r"\bcurl\b.*(?:" + FLAG + r"-X\s*(?:POST|PUT|PATCH|DELETE)|--data|" + FLAG + r"-d\s)|"
        r"Invoke-(?:RestMethod|WebRequest).*-Method\s*(?:Post|Put|Patch|Delete)|"
        r"requests\.(?:post|put|patch|delete)\(",
        Decision.DENY,
        "Sending data to another service is not something an unattended task does. It "
        "reads the repository and runs tests.",
    ),
    Rule(
        "builder.no_customer_data",
        r"\.db\.bak|\.sqlite\.bak|ai_market_monitor\.db|"
        r"(?:SELECT|select)\s+.*\bFROM\s+(?:users|user_identities|conversations|"
        r"setup_chat_\w+|public_inquiries)\b",
        Decision.DENY,
        "Conversation redaction and retention are not finished in the product yet, so "
        "this phase may not read customer records at all. Use the synthetic fixtures.",
    ),
    Rule(
        "builder.no_env_write",
        r"(?:>|>>|Out-File|Set-Content|Add-Content)\s*[\"']?\.?[\w/\\.]*\.env",
        Decision.DENY,
        "Writing an environment file would create a credential, not just read one.",
    ),
)


#: Refusals for operational investigation (OI-3). The investigator reads and explains;
#: every one of these is somebody's decision to make with their name on it.
#:
#: Several overlap rules that already exist — production deploys and database writes are
#: refused by OI-1 too. They are restated here with wording aimed at an operator rather
#: than a developer, because the refusal text is what a person reads mid-incident, and
#: "this is not something an assistant does during an incident" is more useful then than
#: a note about the release gate.
OPERATIONAL_RULES: Final[tuple[Rule, ...]] = (
    Rule(
        "ops.no_feature_flag_change",
        r"(?:set|update|toggle|enable|disable|flip)\w*\b(?:\s+\w+){0,3}?[\s_\-]{0,3}"
        r"(?:feature[_\- ]?flag|flag)\b|"
        r"\b(?:AI_FEATURES_DISABLED|FEATURE_\w+|LAUNCH_STAGE|PUBLIC_WAITLIST_MODE)\s*=",
        Decision.DENY,
        "Changing what is switched on changes what live customers see. Write the change "
        "down as a recommendation with the exact command, and let an operator run it.",
    ),
    Rule(
        "ops.no_launch_stage_change",
        r"launch[_\- ]?stage\s*[:=]|resolve_launch_stage\s*\(\s*[\"']|"
        r"\bpromote\b.*\b(?:stage|launch|production)\b",
        Decision.DENY,
        "The launch stage decides who can reach the product at all. It is never moved "
        "by a tool.",
    ),
    Rule(
        "ops.no_alert_suppression",
        r"(?:silence|suppress|mute|snooze|acknowledge|resolve|close)\w*\b(?:\s+\w+){0,3}?"
        r"[\s_\-]{0,3}(?:alert|alarm|page|incident)|"
        r"\balert\w*[\s_\-]{0,3}(?:enabled|active|muted|silenced)\s*=|"
        r"(?:DELETE|UPDATE)\s+.{0,40}\b(?:operational_alert|alert_delivery)\b",
        Decision.DENY,
        "Silencing an alert makes a problem invisible without making it go away. If an "
        "alert is wrong, say so in the report and let a person change the rule.",
    ),
    Rule(
        "ops.no_production_restart",
        r"\b(?:systemctl|service)\b.*\b(?:restart|stop|start|reload)\b|"
        r"\bdocker\s+(?:restart|stop|kill)\b|\bsupervisorctl\b|"
        r"\bcelery\b.*\b(?:control|shutdown|purge)\b|\bpkill\b|\bkill\s+-9\b",
        Decision.DENY,
        "Restarting a service during an incident is an operator's decision. It also "
        "destroys the in-memory state an investigation may still need.",
    ),
    Rule(
        "ops.no_production_write",
        r"(?:INSERT|UPDATE|DELETE|ALTER|DROP|TRUNCATE)\s+(?:INTO\s+|TABLE\s+|FROM\s+)?\w+|"
        r"\.(?:commit|flush)\(\s*\)\s*#?\s*prod|session\.add\(.*[Pp]rod",
        Decision.DENY,
        "The investigator reads. Nothing it finds is fixed by writing to a database from "
        "here - that is a change, and a change goes through review.",
    ),
    Rule(
        "ops.no_live_production_connection",
        r"(?:psql|mysql|mongo|redis-cli)\b|"
        r"(?:postgres(?:ql)?|redis|mongodb)(?:\+\w+)?://(?!\w*:?\w*@?(?:localhost|127\.0\.0\.1))|"
        r"\bPROD(?:UCTION)?_(?:DATABASE|DB|REDIS)_URL\b",
        Decision.DENY,
        "Production evidence is read from an exported, sanitized snapshot. A live "
        "connection is never opened, even to look.",
    ),
)


def builder_policy() -> PermissionPolicy:
    """OI-1's rules plus the autonomous refusals.

    Both sets are kept whole rather than the builder rules replacing the originals.
    ``PermissionPolicy.evaluate`` takes the strictest match, so a command hitting both
    ``confirm.git_push`` and ``builder.no_push`` is denied — and the report still shows
    that two rules objected, which is what makes an over-broad pattern visible.
    """

    return PermissionPolicy(rules=(*BUILTIN_RULES, *BUILDER_RULES, *OPERATIONAL_RULES))


def is_strictly_tighter(command: str) -> bool:
    """Whether autonomous mode treats a command at least as strictly as OI-1 does.

    Used by the invariant test across a corpus of commands. Exposed here rather than
    written inside the test so the property has one definition.
    """

    from hm_oi.permissions import PermissionPolicy as _Policy

    interactive = _Policy(rules=BUILTIN_RULES).evaluate(command)
    autonomous = builder_policy().evaluate(command)
    order = {Decision.ALLOW: 0, Decision.CONFIRM: 1, Decision.DENY: 2}
    return order[autonomous.decision] >= order[interactive.decision]
