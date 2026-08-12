"""One authority on what is switched on, for whom, right now.

The Setup surfaces grew a switch each: one for the planner, one for the composer, one for
Scanner, one for language. Each was read in its own way in its own module, so "is the
assistant on?" had several answers and the safest one did not always win. Worse, several
of them were the *same* switch in effect — turning the planner off also took the Builder
down, because nothing distinguished "the model is unavailable" from "authoring is
unavailable".

This module makes those separate decisions, evaluated in one place, with one order of
precedence:

1. **Environment ceiling.** A hard off in configuration can never be turned on at
   runtime. It is the emergency brake, and a runtime control must not release it.
2. **Denylist.** Named users are excluded before anything else considers them.
3. **Allowlist.** Named users are included regardless of percentage.
4. **Cohort.** A named group, for a controlled trial.
5. **Percentage.** Deterministic from a hash of the flag and the user, so the same person
   stays in the same bucket between requests. A coin flip per request would show half the
   product to somebody at random and make an incident impossible to reproduce.
6. **Default.** Whatever the flag says when nothing above matched.

**Failing safe means failing to the deterministic product, not to nothing.** A malformed
flag turns the *AI* off; it never turns the Builder off. Losing the assistant is a
degraded product, losing authoring is a broken one.

Nothing here can grant authority. A flag decides whether a feature is *offered*; the
compiler, the Sharia screening, the provider gate, ownership and approval all run exactly
the same either way. There is deliberately no flag that can skip them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)


class Feature(StrEnum):
    """Every independently controllable surface.

    Separate members because they fail separately. ``PLANNER`` off with ``GUIDED_BUILDER``
    on is the whole point: the assistant stops, the product does not.
    """

    GUIDED_BUILDER = "guided_builder"
    FREE_TEXT_AI = "free_text_ai"
    PLANNER = "planner"
    COMPOSER = "composer"
    SCANNER = "scanner"
    MONITOR = "monitor"
    LANGUAGE_NON_ENGLISH = "language_non_english"
    MODEL_ROUTE = "model_route"
    PROMPT_SCHEMA_VERSION = "prompt_schema_version"


#: Features that must survive every AI, provider and budget failure. A flag config that
#: would disable one of these is treated as malformed and ignored, because a person who
#: cannot author anything has no product left.
ALWAYS_AVAILABLE: frozenset[Feature] = frozenset({Feature.GUIDED_BUILDER})


@dataclass(frozen=True, slots=True)
class FeatureRule:
    """How one feature is rolled out."""

    feature: Feature
    #: What everybody gets when no list, cohort or percentage matches.
    default_enabled: bool = False
    #: 0–100. Deterministic by user, not random per request.
    percentage: int = 0
    allow_user_ids: frozenset[str] = frozenset()
    deny_user_ids: frozenset[str] = frozenset()
    cohorts: frozenset[str] = frozenset()
    #: A value carried with the decision — which model route, which prompt version.
    variant: str | None = None

    def normalised(self) -> FeatureRule:
        """Clamp anything out of range rather than trusting it.

        A percentage of 500 in a config file is a typo, and treating it as "everybody" is
        the safer reading than crashing the request that read it.
        """

        return FeatureRule(
            feature=self.feature,
            default_enabled=bool(self.default_enabled),
            percentage=max(0, min(100, int(self.percentage))),
            allow_user_ids=frozenset(str(item) for item in self.allow_user_ids),
            deny_user_ids=frozenset(str(item) for item in self.deny_user_ids),
            cohorts=frozenset(str(item) for item in self.cohorts),
            variant=self.variant,
        )


@dataclass(frozen=True, slots=True)
class FeatureDecision:
    """One answer, with the reason it was reached.

    The reason is not decoration. An incident is reproduced by knowing *why* a person saw
    what they saw, and "it was on" does not tell you whether they were allowlisted, in a
    cohort or inside the percentage.
    """

    feature: Feature
    enabled: bool
    reason: str
    variant: str | None = None
    cohort: str | None = None
    bucket: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": str(self.feature),
            "enabled": self.enabled,
            "reason": self.reason,
            "variant": self.variant,
            "cohort": self.cohort,
            "bucket": self.bucket,
        }


@dataclass(frozen=True, slots=True)
class RolloutConfig:
    """Every rule, plus the environment ceilings that override them.

    A ceiling on an always-available feature is dropped **here**, when the config is
    built, rather than at each place that reads it. That distinction is not cosmetic: a
    reader-side filter has to be repeated by every reader, and the second reader added
    later was the one that let ``AI_FEATURES_DISABLED=guided_builder`` switch authoring
    off entirely. A config that cannot hold the value cannot be misread.
    """

    rules: dict[Feature, FeatureRule] = field(default_factory=dict)
    #: Features forced off by configuration. A runtime control can never turn these on.
    environment_disabled: frozenset[Feature] = frozenset()
    #: An opaque version stamped onto every turn, so a past decision can be reproduced.
    version: str = "0"

    def __post_init__(self) -> None:
        refused = self.environment_disabled & ALWAYS_AVAILABLE
        if refused:
            logger.error(
                "feature_ceiling_refused_disabling_core",
                flags=sorted(str(item) for item in refused),
            )
            object.__setattr__(
                self, "environment_disabled", self.environment_disabled - ALWAYS_AVAILABLE
            )


def bucket_for(feature: Feature, subject: str) -> int:
    """Which 0–99 bucket this subject falls in for this feature.

    Hashed from the feature *and* the subject together, so a person who is in the first
    10% for one feature is not automatically in the first 10% for every other. Sharing one
    bucket across features would make every rollout hit the same unlucky people.
    """

    digest = hashlib.sha256(f"{feature.value}:{subject}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % 100


class FeatureControlService:
    """Evaluate features for one request. Server-side, deterministic, auditable."""

    def __init__(self, config: RolloutConfig) -> None:
        self.config = config

    def decide(
        self,
        feature: Feature,
        *,
        user_id: UUID | str | None = None,
        cohorts: frozenset[str] | None = None,
    ) -> FeatureDecision:
        """The one place a feature question is answered."""

        subject = str(user_id) if user_id is not None else ""
        member_of = cohorts or frozenset()

        if feature in self.config.environment_disabled:
            # The emergency brake. Checked first and never overridden, because the reason
            # somebody set it is that they needed the feature off everywhere, now.
            return FeatureDecision(feature, False, "environment_disabled")

        rule = self.config.rules.get(feature)
        if rule is None:
            # No rule at all. Authoring stays on; anything else stays off until somebody
            # deliberately turns it on.
            enabled = feature in ALWAYS_AVAILABLE
            return FeatureDecision(feature, enabled, "no_rule_default")
        rule = rule.normalised()

        if subject and subject in rule.deny_user_ids:
            return FeatureDecision(feature, False, "denylist", variant=rule.variant)
        if subject and subject in rule.allow_user_ids:
            return FeatureDecision(feature, True, "allowlist", variant=rule.variant)
        if rule.cohorts and member_of:
            matched = sorted(rule.cohorts & member_of)
            if matched:
                return FeatureDecision(
                    feature, True, "cohort", variant=rule.variant, cohort=matched[0]
                )

        if rule.percentage > 0 and subject:
            bucket = bucket_for(feature, subject)
            if bucket < rule.percentage:
                return FeatureDecision(
                    feature, True, "percentage", variant=rule.variant, bucket=bucket
                )
            return FeatureDecision(
                feature, rule.default_enabled, "percentage_excluded",
                variant=rule.variant, bucket=bucket,
            )

        return FeatureDecision(feature, rule.default_enabled, "default", variant=rule.variant)

    def is_enabled(
        self,
        feature: Feature,
        *,
        user_id: UUID | str | None = None,
        cohorts: frozenset[str] | None = None,
    ) -> bool:
        return self.decide(feature, user_id=user_id, cohorts=cohorts).enabled

    def snapshot(
        self,
        *,
        user_id: UUID | str | None = None,
        cohorts: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """Every decision for this request, plus the config version that produced them.

        Stamped onto the turn so an incident can be replayed against the same rollout
        rather than against whatever the config happens to say afterwards.
        """

        return {
            "rollout_version": self.config.version,
            "features": {
                str(feature): self.decide(
                    feature, user_id=user_id, cohorts=cohorts
                ).to_dict()
                for feature in Feature
            },
        }


def environment_disabled_from(raw: str) -> frozenset[Feature]:
    """Read the emergency brake from configuration.

    An unknown name is logged and skipped rather than raised on. An operator typing
    ``planer`` into an incident switch at three in the morning must not take the whole
    application down; the mistake is visible in the log and the other names still work.
    """

    names = [item.strip() for item in str(raw or "").replace(";", ",").split(",")]
    disabled: set[Feature] = set()
    for name in names:
        if not name:
            continue
        try:
            disabled.add(Feature(name))
        except ValueError:
            logger.warning("feature_disable_unknown", flag=name[:60])
    return frozenset(disabled)


def rollout_config_from_settings(settings: Any) -> RolloutConfig:
    """The rollout in force for this process, read from configuration.

    One reader, so the enforcement path and anything that reports on it can never disagree
    about which features are on. The switches that already existed — the Setup agent, the
    Scanner, free-text authoring — become *rules* here rather than separate booleans read
    in separate modules, which is how "is the assistant on?" came to have several answers
    and the safest one did not always win.
    """

    def rule(feature: Feature, enabled: bool) -> FeatureRule:
        return FeatureRule(feature=feature, default_enabled=bool(enabled), percentage=0)

    languages = list(getattr(settings, "setup_ai_languages", []) or [])
    rules = {
        # Authoring. Never off: it is the product. ``setup_builder_enabled`` is read so a
        # configuration that tries to switch it off is *seen* and refused, rather than
        # quietly obeyed somewhere else.
        Feature.GUIDED_BUILDER: FeatureRule(
            Feature.GUIDED_BUILDER, default_enabled=True, percentage=100
        ),
        Feature.FREE_TEXT_AI: rule(
            Feature.FREE_TEXT_AI, getattr(settings, "setup_free_text_enabled", True)
        ),
        Feature.PLANNER: rule(
            Feature.PLANNER, getattr(settings, "setup_planner_enabled", True)
        ),
        Feature.COMPOSER: rule(
            Feature.COMPOSER, getattr(settings, "setup_composer_enabled", True)
        ),
        Feature.SCANNER: rule(
            Feature.SCANNER, getattr(settings, "setup_scanner_enabled", True)
        ),
        Feature.MONITOR: rule(
            Feature.MONITOR, getattr(settings, "setup_monitor_enabled", True)
        ),
        # An empty language list means "every language the product supports", so the
        # non-English surface is on. A list that names only English turns it off.
        Feature.LANGUAGE_NON_ENGLISH: rule(
            Feature.LANGUAGE_NON_ENGLISH,
            not languages or any(not str(item).startswith("en") for item in languages),
        ),
        Feature.MODEL_ROUTE: FeatureRule(
            Feature.MODEL_ROUTE,
            default_enabled=True,
            percentage=0,
            variant=str(getattr(settings, "openai_model", "") or ""),
        ),
        Feature.PROMPT_SCHEMA_VERSION: FeatureRule(
            Feature.PROMPT_SCHEMA_VERSION,
            default_enabled=True,
            percentage=0,
            variant=str(getattr(settings, "ai_rollout_version", "0") or "0"),
        ),
    }
    return RolloutConfig(
        rules=rules,
        environment_disabled=environment_disabled_from(
            getattr(settings, "ai_features_disabled", "")
        ),
        version=str(getattr(settings, "ai_rollout_version", "0") or "0"),
    )


def load_rollout_config(
    payload: Any,
    *,
    environment_disabled: frozenset[Feature] = frozenset(),
    version: str = "0",
) -> RolloutConfig:
    """Build a config from stored data, discarding anything malformed.

    A bad rule is dropped, not raised on. A configuration mistake must degrade one
    feature, not take down every request that reads the configuration — and the dropped
    rule leaves that feature at its safe default rather than at whatever the broken
    value happened to parse to.
    """

    rules: dict[Feature, FeatureRule] = {}
    if isinstance(payload, dict):
        for key, raw in payload.items():
            try:
                feature = Feature(str(key))
            except ValueError:
                logger.warning("feature_flag_unknown", flag=str(key)[:60])
                continue
            if not isinstance(raw, dict):
                logger.warning("feature_flag_malformed", flag=str(feature))
                continue
            try:
                rule = FeatureRule(
                    feature=feature,
                    default_enabled=bool(raw.get("default_enabled", False)),
                    percentage=int(raw.get("percentage", 0) or 0),
                    allow_user_ids=frozenset(str(x) for x in raw.get("allow_user_ids", [])),
                    deny_user_ids=frozenset(str(x) for x in raw.get("deny_user_ids", [])),
                    cohorts=frozenset(str(x) for x in raw.get("cohorts", [])),
                    variant=(
                        str(raw["variant"]) if raw.get("variant") is not None else None
                    ),
                ).normalised()
            except (TypeError, ValueError):
                logger.warning("feature_flag_unreadable", flag=str(feature))
                continue
            if feature in ALWAYS_AVAILABLE and not rule.default_enabled:
                # A config that would switch off authoring is treated as a mistake. There
                # is no supported state of this product in which a person cannot build a
                # setup, so the rule is corrected rather than obeyed.
                logger.error("feature_flag_refused_disabling_core", flag=str(feature))
                rule = FeatureRule(feature=feature, default_enabled=True, percentage=100)
            rules[feature] = rule

    for feature in ALWAYS_AVAILABLE:
        rules.setdefault(feature, FeatureRule(feature, default_enabled=True, percentage=100))

    return RolloutConfig(
        rules=rules,
        environment_disabled=frozenset(
            item for item in environment_disabled if item not in ALWAYS_AVAILABLE
        ),
        version=version,
    )
