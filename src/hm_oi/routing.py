"""Which model tier an engineering task deserves, decided without asking a model.

The routing decision is made in Python, from words and counts. That is not a shortcut:
a router that calls a model to decide which model to call has already spent the money it
was meant to save, and it makes the cheapest possible task — "which file defines
``route_task``?" — cost a premium request. Every rule here is a regular expression or an
integer comparison, so the same task always routes the same way and a surprising choice
can be explained by reading the reasons rather than by re-running it.

The shape deliberately mirrors ``ai_market_monitor.services.ai_model_routing``: signals
are collected as *reasons*, the reasons decide the tier, and the reasons are recorded
next to the decision. An engineer who has read one router can read this one. The two are
otherwise unrelated and must stay that way — that module routes a customer's sentence,
this one routes an engineer's task, and neither imports the other.

Three tiers, and what separates them:

FAST
    Looking things up. No file is changed and no judgement is offered beyond "here it
    is". Getting it wrong costs a re-run.
NORMAL
    Ordinary engineering: read the code, understand it, propose or make a small change,
    write a test. Getting it wrong costs a review comment.
DEEP
    Work where being wrong is expensive or hard to notice — architecture, anything
    touching money, identity, Sharia status or approval, a bug that has already survived
    one fix, or a Setup Chat turn that read a person's words incorrectly.

The default is NORMAL, not FAST. A router that has to be *persuaded* to think is a
router that answers hard questions cheaply, which is the failure that matters.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final


class Tier(StrEnum):
    """The three logical tiers. Ordered by ``RANK`` below, not by member order."""

    FAST = "fast"
    NORMAL = "normal"
    DEEP = "deep"


#: How the tiers compare. A separate mapping because ``StrEnum`` members compare as
#: strings, and "deep" < "fast" alphabetically — which would silently invert every
#: escalation if a reader reached for ``max()`` on the members themselves.
RANK: Final[dict[Tier, int]] = {Tier.FAST: 0, Tier.NORMAL: 1, Tier.DEEP: 2}


class TaskCategory(StrEnum):
    """What kind of engineering work this is.

    The category is a *floor*, never a ceiling: ``DISCOVERY`` may still route DEEP if the
    thing being discovered sits in the Sharia governance path.
    """

    DISCOVERY = "discovery"
    TESTING = "testing"
    BUG = "bug"
    IMPLEMENTATION = "implementation"
    RELEASE_REVIEW = "release_review"
    SETUP_CHAT_SEMANTICS = "setup_chat_semantics"
    SECURITY = "security"
    ARCHITECTURE = "architecture"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------------------
# Vocabulary. Each concept has exactly one alternation, exported so a caller shares the
# precise wording rather than hand-writing a subset that drifts from it. Re-declaring
# any of these lists somewhere else is the mistake this module exists to prevent.
# --------------------------------------------------------------------------------------

#: Asking where something is. No file changes, no judgement.
DISCOVERY_WORDS: Final[str] = (
    r"where (?:is|are|does|do)|which file|which module|what file|locate|find the|"
    r"list (?:the |all )?|search for|look up|show me|what (?:tests?|test file)|"
    r"who (?:calls|imports|uses)|what (?:calls|imports|uses)|grep|"
    r"summari[sz]e (?:the )?log|tail the log|read the log|what does .{1,40} import|"
    r"is there (?:a|an) |does .{1,40} exist|how many"
)

#: Asking for a change to be made. Blocks FAST even when phrased as a question.
CHANGE_WORDS: Final[str] = (
    r"implement|fix|repair|patch|add (?:a |an |the )?|remove|delete|rename|refactor|"
    r"rewrite|migrate|update|change|modify|introduce|extract|consolidate|wire|"
    r"write (?:a |the )?(?:test|function|class|module)"
)

#: Running or authoring checks.
TESTING_WORDS: Final[str] = (
    r"\btests?\b|pytest|ruff|mypy|playwright|regression|coverage|smoke|replay|"
    r"release gate|lint|type ?check|assert"
)

#: Something is behaving wrongly.
BUG_WORDS: Final[str] = (
    r"\bbug\b|broken|fails?\b|failing|error|exception|traceback|crash|regress\w*|"
    r"wrong (?:value|result|answer|number|order)|does ?n[o']?t work|"
    r"unexpected|incorrect|misread|stack trace|500\b|returns? the wrong"
)

#: Structural questions. Being wrong here costs more than one change.
ARCHITECTURE_WORDS: Final[str] = (
    r"architect\w*|design|boundary|boundaries|authority|authoritative|"
    # "should the capability resolver own the operator table" is an ownership question,
    # not a Setup Chat question, even though it names a Setup Chat module. Matching only
    # `should we|it|this` missed every version of it that named the subject — which is
    # how the clearest architecture questions ended up classified as something else.
    r"should\s+(?:\w+\s+){0,5}?(?:be|live|own|handle|belong|know)\b|"
    r"who owns|what owns|which (?:module|layer|service|component) (?:should|owns)|"
    r"where should|responsib\w+|"
    r"coupling|decouple|abstraction|contract between|split (?:the |up )|"
    r"single source of truth|duplicate (?:parser|authority|implementation)|"
    r"restructure|redesign"
)

#: Security and privacy. Always DEEP: a quiet mistake here is the worst kind.
SECURITY_WORDS: Final[str] = (
    r"security|securit\w+|privacy|\bauth\b|authn|authz|authenticat\w+|authoris\w+|"
    r"authoriz\w+|credential|secret|\btoken\b|password|\bcsrf\b|\bxss\b|injection|"
    r"tenant isolation|ownership check|permission|leak\w*|\bpii\b|personal data|"
    r"session fixation|rate limit|sanitis\w+|sanitiz\w+|"
    # Tenant isolation is usually asked about in plain words rather than named: "can
    # another user see this?" is the question, and it never contains the word "security".
    r"(?:another|other|someone else'?s?|a different) (?:user|account|customer|tenant)|"
    r"cross[- ]tenant|other people'?s|\bimpersonat\w+|"
    # Output escaping, from either direction, plus the phrase that nearly always signals
    # an escaping question. Scoped rather than matching bare "escape", which appears
    # harmlessly in every discussion of regular expressions in this repository.
    r"(?:un)?escap\w*\b.{0,40}?\b(?:html|output|template|input|browser|render)|"
    r"\b(?:html|output|template|user[- ]input|browser)\b.{0,40}?(?:un)?escap\w*|"
    r"user[- ]input|untrusted"
)

#: The Setup Chat semantic path — reading a person's words into a typed draft.
SETUP_CHAT_WORDS: Final[str] = (
    r"setup chat|setup_chat|planner|composer|interpreter|grounding|grounded|"
    r"segmentation|fragment|clarification|capability (?:key|shortlist|resolver)|"
    r"strategydraft|strategy draft|apply_setup_turn|semantic (?:diff|equivalence|"
    r"validation)|compil\w+ (?:the )?draft|turn (?:failed|failure)|misunderstood|"
    r"misinterpret\w*|read (?:it|the value|the number) as"
)

#: Surfaces where a wrong change reaches money, identity, or a governed decision.
HIGH_RISK_WORDS: Final[str] = (
    r"sharia|shariah|halal|haram|screening|methodology|passport|governance|"
    r"billing|payment|invoice|subscription|entitlement|plan tier|refund|stripe|"
    r"approval|approve|activation|activate|migration|alembic|schema change|"
    r"production|deploy|rollout|feature flag|budget|quota|ownership|user data|"
    r"delete .{0,20}(?:data|record|row|table)"
)

#: Wording that says nobody knows why it happens yet. Cheap models guess here.
UNCLEAR_CAUSE_WORDS: Final[str] = (
    r"intermittent|flak(?:y|iness)|sometimes|randomly|no idea|not sure why|"
    r"cannot reproduce|can[o']?t reproduce|unclear|mysterious|only (?:in|on) ci|"
    r"only sometimes|hard to reproduce|race condition|heisenbug|"
    r"still (?:happening|failing|broken)|keeps (?:happening|failing|coming back)"
)

#: Release readiness.
RELEASE_WORDS: Final[str] = (
    r"release|ship|launch readiness|go ?/ ?no ?go|release gate|pre-?flight|"
    r"ready (?:to|for) (?:ship|release|launch)|blockers?"
)


def _compiled(alternation: str) -> re.Pattern[str]:
    return re.compile(alternation, re.IGNORECASE)


_DISCOVERY_RE: Final = _compiled(DISCOVERY_WORDS)
_CHANGE_RE: Final = _compiled(CHANGE_WORDS)
_TESTING_RE: Final = _compiled(TESTING_WORDS)
_BUG_RE: Final = _compiled(BUG_WORDS)
_ARCHITECTURE_RE: Final = _compiled(ARCHITECTURE_WORDS)
_SECURITY_RE: Final = _compiled(SECURITY_WORDS)
_SETUP_CHAT_RE: Final = _compiled(SETUP_CHAT_WORDS)
_HIGH_RISK_RE: Final = _compiled(HIGH_RISK_WORDS)
_UNCLEAR_CAUSE_RE: Final = _compiled(UNCLEAR_CAUSE_WORDS)
_RELEASE_RE: Final = _compiled(RELEASE_WORDS)


# --------------------------------------------------------------------------------------
# Components. "Cross-component" needs a definition, or every task involving two files
# counts as one.
# --------------------------------------------------------------------------------------

#: Path fragment -> the component it belongs to. Ordered longest-first at match time so
#: ``src/ai_market_monitor/api/routers`` resolves to ``api`` and not to ``src``.
COMPONENTS: Final[dict[str, str]] = {
    "src/ai_market_monitor/api": "api",
    "src/ai_market_monitor/engine": "engine",
    "src/ai_market_monitor/services": "services",
    "src/ai_market_monitor/db": "database",
    "src/ai_market_monitor/schemas": "schemas",
    "src/ai_market_monitor/core": "core",
    "src/ai_market_monitor/telegram": "telegram",
    "src/ai_market_monitor/whatsapp": "whatsapp",
    "src/ai_market_monitor/static": "frontend",
    "src/ai_market_monitor/templates": "frontend",
    "src/ai_market_monitor/worker.py": "worker",
    "src/hm_chatbot_eval": "evaluator",
    "src/hm_oi": "engineering_tooling",
    "alembic": "database",
    "tests/browser": "browser_tests",
    "tests": "tests",
    "scripts": "scripts",
    "Hilal-Markets-Website": "landing",
    "deploy": "deployment",
}


def component_for_path(path: str) -> str | None:
    """Which component a repository path belongs to, or ``None`` if it is outside one."""

    normalised = str(path).replace("\\", "/").strip().lstrip("./")
    for prefix in sorted(COMPONENTS, key=len, reverse=True):
        if normalised == prefix or normalised.startswith(f"{prefix}/"):
            return COMPONENTS[prefix]
    return None


def components_for(paths: Iterable[str] | None) -> frozenset[str]:
    """The distinct components a set of paths touches."""

    if not paths:
        return frozenset()
    found = {component_for_path(str(item)) for item in paths}
    return frozenset(item for item in found if item)


# --------------------------------------------------------------------------------------
# Request and decision
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskRequest:
    """One engineering task, described well enough to route it.

    ``text`` alone is usually enough. The other fields exist because two of the
    strongest signals are not in the sentence: how many components the work spans, and
    whether somebody has already tried and failed. "It is still broken" is five words and
    the hardest task in the session.
    """

    text: str
    #: Repository paths already known to be involved. Optional; the router falls back to
    #: paths mentioned in the text.
    paths: tuple[str, ...] = ()
    #: How many times this same task has already been attempted and not worked.
    previous_attempts: int = 0
    #: An explicit override from the caller, when a human already knows the answer.
    forced_tier: Tier | None = None
    #: A caller that has already classified the work may say so; the router still checks.
    declared_category: TaskCategory | None = None


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """The tier, the model behind it, and why."""

    tier: Tier
    category: TaskCategory
    #: Every reason that fired, in the order it was found.
    reasons: tuple[str, ...]
    #: The reasons that raised the tier above where it started.
    escalation_reasons: tuple[str, ...]
    components: tuple[str, ...]
    previous_attempts: int
    #: Filled in by ``hm_oi.models.bind_model``; the router itself knows nothing about
    #: providers, so the rules stay testable without any configuration present.
    model: str | None = None
    provider: str | None = None
    reasoning_effort: str | None = None

    def explain(self) -> str:
        """One line a person can read, for the session log and the console."""

        escalation = (
            f" escalated by: {', '.join(self.escalation_reasons)}"
            if self.escalation_reasons
            else ""
        )
        return (
            f"{self.tier.value.upper()} ({self.category.value})"
            f" model={self.model or 'unbound'}{escalation}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Routing metadata, safe to log.

        Deliberately contains no task text, no file contents and no key. Enough to
        explain the choice, nothing that could carry a secret into a log file.
        """

        return {
            "tier": str(self.tier),
            "category": str(self.category),
            "reasons": list(self.reasons),
            "escalation_reasons": list(self.escalation_reasons),
            "components": list(self.components),
            "previous_attempts": self.previous_attempts,
            "model": self.model,
            "provider": self.provider,
            "reasoning_effort": self.reasoning_effort,
        }


@dataclass
class _Signals:
    """Working state while the rules run."""

    reasons: list[str] = field(default_factory=list)
    floor: Tier = Tier.FAST
    escalations: list[str] = field(default_factory=list)

    def note(self, reason: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)

    def raise_to(self, tier: Tier, reason: str) -> None:
        """Raise the floor, recording the reason only when it actually moved.

        A reason that did not change the outcome is noise in the log, and noise in the
        log is why nobody reads it during an incident.
        """

        self.note(reason)
        if RANK[tier] > RANK[self.floor]:
            self.floor = tier
            self.escalations.append(reason)


#: Paths mentioned inside free text. Matched loosely on purpose: the point is to notice
#: that ``services/billing.py`` was named, not to validate that it exists.
_PATH_IN_TEXT_RE: Final = re.compile(
    r"(?<![\w/\\.])((?:[\w.\-]+[/\\])+[\w.\-]+\.(?:py|js|css|html|md|json|yml|yaml|ini|toml))"
)


def classify(request: TaskRequest) -> TaskCategory:
    """What kind of work this is.

    Checked most-consequential first. A task that is *both* a bug report and a security
    question is a security question, because the cost of misjudging it is set by the
    security half.
    """

    text = request.text or ""
    if _SECURITY_RE.search(text):
        return TaskCategory.SECURITY
    if _ARCHITECTURE_RE.search(text):
        return TaskCategory.ARCHITECTURE

    # A pure lookup is a lookup whatever it is about. "What tests cover the capability
    # resolver?" names a Setup Chat module, but answering it means listing files — and
    # classifying it as a semantics question sent every such question to the most
    # expensive tier. Checked before the subject-matter categories, and only when nothing
    # in the sentence asks for a change or reports a defect.
    #
    # This lowers the *category*, never the safety: the risk, security and unclear-cause
    # escalations in ``route_task`` are applied to every category alike, so
    # "where is the Sharia screening applied" is still answered at the deep tier.
    if _DISCOVERY_RE.search(text) and not (_CHANGE_RE.search(text) or _BUG_RE.search(text)):
        return TaskCategory.DISCOVERY

    if _SETUP_CHAT_RE.search(text):
        return TaskCategory.SETUP_CHAT_SEMANTICS
    if _RELEASE_RE.search(text):
        return TaskCategory.RELEASE_REVIEW
    if _BUG_RE.search(text):
        return TaskCategory.BUG
    if _CHANGE_RE.search(text):
        return TaskCategory.IMPLEMENTATION
    if _TESTING_RE.search(text):
        return TaskCategory.TESTING
    if _DISCOVERY_RE.search(text):
        return TaskCategory.DISCOVERY
    # Nothing matched. An unrecognised task is not a cheap task; it is a task nobody has
    # described well enough to price, which is exactly when guessing low is wrong.
    return TaskCategory.UNKNOWN


#: The lowest tier each category may be answered at. Discovery and testing are the only
#: kinds of work that can legitimately be FAST.
CATEGORY_FLOOR: Final[dict[TaskCategory, Tier]] = {
    TaskCategory.DISCOVERY: Tier.FAST,
    TaskCategory.TESTING: Tier.FAST,
    TaskCategory.BUG: Tier.NORMAL,
    TaskCategory.IMPLEMENTATION: Tier.NORMAL,
    TaskCategory.RELEASE_REVIEW: Tier.NORMAL,
    TaskCategory.UNKNOWN: Tier.NORMAL,
    TaskCategory.SETUP_CHAT_SEMANTICS: Tier.DEEP,
    TaskCategory.SECURITY: Tier.DEEP,
    TaskCategory.ARCHITECTURE: Tier.DEEP,
}

#: Touching this many distinct components at once is a cross-component change, and a
#: cross-component change is where a locally correct edit breaks something else.
CROSS_COMPONENT_THRESHOLD: Final[int] = 3


def route_task(request: TaskRequest) -> RoutingDecision:
    """Pick a tier for one task. Pure, deterministic, and free."""

    text = request.text or ""
    category = request.declared_category or classify(request)
    signals = _Signals()

    paths = tuple(request.paths) or tuple(_PATH_IN_TEXT_RE.findall(text))
    components = components_for(paths)

    signals.raise_to(CATEGORY_FLOOR[category], f"category_{category.value}")

    # --- Scope -------------------------------------------------------------------
    if len(components) >= CROSS_COMPONENT_THRESHOLD:
        signals.raise_to(Tier.DEEP, "cross_component_scope")
    elif len(components) == 2:
        signals.raise_to(Tier.NORMAL, "two_components")

    # --- History -----------------------------------------------------------------
    # A fix that did not work is the clearest evidence available that the first reading
    # of the problem was wrong. Repeating it at the same tier repeats the mistake.
    if request.previous_attempts >= 2:
        signals.raise_to(Tier.DEEP, "repeated_failed_attempts")
    elif request.previous_attempts == 1:
        signals.raise_to(Tier.NORMAL, "previous_attempt_failed")

    # --- Risk --------------------------------------------------------------------
    if _HIGH_RISK_RE.search(text):
        signals.raise_to(Tier.DEEP, "high_risk_surface")
    if _SECURITY_RE.search(text):
        signals.raise_to(Tier.DEEP, "security_or_privacy_relevant")
    if _UNCLEAR_CAUSE_RE.search(text):
        signals.raise_to(Tier.DEEP, "root_cause_unclear")

    # --- Intent ------------------------------------------------------------------
    # Wanting a file changed is never FAST, however short the sentence. "Rename this
    # variable" is cheap; "rename this variable" in the compiler is not, and the router
    # cannot tell the two apart from the words alone.
    if _CHANGE_RE.search(text):
        signals.raise_to(Tier.NORMAL, "requests_a_code_change")
    if _BUG_RE.search(text) and category is not TaskCategory.DISCOVERY:
        signals.raise_to(Tier.NORMAL, "diagnoses_a_defect")

    if not text.strip():
        signals.raise_to(Tier.NORMAL, "empty_task_description")

    tier = signals.floor
    if request.forced_tier is not None:
        # A human override is obeyed and recorded. Recorded because an override that
        # lowered the tier is the first thing to check when an answer was poor.
        if request.forced_tier is not tier:
            signals.note(f"forced_{request.forced_tier.value}")
            signals.escalations.append(f"forced_{request.forced_tier.value}")
        tier = request.forced_tier

    if not signals.reasons:  # pragma: no cover - the category reason always fires
        signals.note("no_signal_default")

    return RoutingDecision(
        tier=tier,
        category=category,
        reasons=tuple(signals.reasons),
        escalation_reasons=tuple(signals.escalations),
        components=tuple(sorted(components)),
        previous_attempts=request.previous_attempts,
    )
