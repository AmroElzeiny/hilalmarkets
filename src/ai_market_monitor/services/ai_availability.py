"""Whether the assistant can be used right now, and what to say when it cannot.

Setup Chat used to be the only way to build a Watch Plan, so any AI problem was a
product outage. It is now an accelerator over the Guided Builder, and this module is the
one place that decides whether the accelerator is available.

Two rules hold everywhere below:

* **An AI problem is never reported as a problem with the setup.** A provider timeout is
  not a compiler error, not a screening failure, and never a Sharia finding. Saying so
  would tell somebody their Watch Plan is broken when nothing about it changed.
* **Nothing is lost and nothing retries by itself.** The draft is already saved before a
  model is called. When the assistant is unavailable the person is told once, pointed at
  the guided fields, and left in control of when to try again.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from ai_market_monitor.core.config import Settings


class AIUnavailableReason(StrEnum):
    """Why the assistant cannot be used. One member per cause we can actually detect."""

    #: The provider did not answer, answered too slowly, or rate-limited us.
    PROVIDER_UNREACHABLE = "provider_unreachable"
    #: Too many recent failures, so calls are being held back on purpose.
    CIRCUIT_OPEN = "circuit_open"
    #: An operator switched this part off.
    FEATURE_OFF = "feature_off"
    #: This account has used its AI allowance.
    QUOTA_REACHED = "quota_reached"
    #: The spending limit for this account was reached.
    COST_LIMIT = "cost_limit"
    #: The model answered, but not in a shape the platform can act on.
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    #: Several attempts in a row could not be read.
    REPEATED_FAILURE = "repeated_failure"


#: What each cause looks like to the person. Never an error code, never a provider name,
#: and never a suggestion that their setup is at fault.
_REASON_TEXT: dict[AIUnavailableReason, str] = {
    AIUnavailableReason.PROVIDER_UNREACHABLE: (
        "The assistant is not answering right now. Your progress is saved — "
        "carry on with the guided fields."
    ),
    AIUnavailableReason.CIRCUIT_OPEN: (
        "The assistant is resting after some errors. Your progress is saved — "
        "carry on with the guided fields."
    ),
    AIUnavailableReason.FEATURE_OFF: (
        "The assistant is switched off at the moment. Your progress is saved — "
        "carry on with the guided fields."
    ),
    AIUnavailableReason.QUOTA_REACHED: (
        "You have used all of your assistant messages for now. Your progress is saved — "
        "carry on with the guided fields."
    ),
    AIUnavailableReason.COST_LIMIT: (
        "You have reached your assistant limit for now. Your progress is saved — "
        "carry on with the guided fields."
    ),
    AIUnavailableReason.INVALID_MODEL_OUTPUT: (
        "The assistant could not read that clearly. Your progress is saved — "
        "carry on with the guided fields, or try saying it a different way."
    ),
    AIUnavailableReason.REPEATED_FAILURE: (
        "The assistant could not read the last few messages. Your progress is saved — "
        "carry on with the guided fields."
    ),
}

_MISSING = tuple(sorted(str(item) for item in AIUnavailableReason if item not in _REASON_TEXT))
if _MISSING:  # pragma: no cover - import-time guard
    raise RuntimeError("every reason needs plain wording; missing: " + ", ".join(_MISSING))

#: Error codes the launch path already raises, and the cause each one is. Mapping them
#: here — rather than at each raise site — is what stops one of them being reported as a
#: strategy failure by a caller that did not know better.
_CODE_REASONS: dict[str, AIUnavailableReason] = {
    "AI_PROVIDER_UNAVAILABLE": AIUnavailableReason.PROVIDER_UNREACHABLE,
    "PROVIDER_TIMEOUT": AIUnavailableReason.PROVIDER_UNREACHABLE,
    "PLANNER_TIMEOUT": AIUnavailableReason.PROVIDER_UNREACHABLE,
    "COMPOSER_TIMEOUT": AIUnavailableReason.PROVIDER_UNREACHABLE,
    "RATE_LIMITED": AIUnavailableReason.PROVIDER_UNREACHABLE,
    "CIRCUIT_OPEN": AIUnavailableReason.CIRCUIT_OPEN,
    "AI_DISABLED": AIUnavailableReason.FEATURE_OFF,
    "SETUP_CHAT_DISABLED": AIUnavailableReason.FEATURE_OFF,
    "FREE_TEXT_DISABLED": AIUnavailableReason.FEATURE_OFF,
    "AI_QUOTA_EXCEEDED": AIUnavailableReason.QUOTA_REACHED,
    "AI_USAGE_LIMIT": AIUnavailableReason.QUOTA_REACHED,
    "COST_BUDGET_EXCEEDED": AIUnavailableReason.COST_LIMIT,
    "PLAN_SCHEMA_INVALID": AIUnavailableReason.INVALID_MODEL_OUTPUT,
    "PLANNER_OUTPUT_INVALID": AIUnavailableReason.INVALID_MODEL_OUTPUT,
    "MODEL_OUTPUT_UNREADABLE": AIUnavailableReason.INVALID_MODEL_OUTPUT,
}


@dataclass(frozen=True, slots=True)
class AIAvailability:
    """What each surface can do for this person right now."""

    free_text: bool
    planner: bool
    composer: bool
    builder: bool
    scanner: bool
    monitor: bool
    reason: AIUnavailableReason | None = None

    @property
    def assistant_available(self) -> bool:
        """True when a free-text message can actually be answered."""

        return self.free_text and self.planner

    @property
    def message(self) -> str | None:
        return _REASON_TEXT[self.reason] if self.reason is not None else None

    def to_dict(self) -> dict[str, object]:
        return {
            "assistant_available": self.assistant_available,
            "free_text": self.free_text,
            "planner": self.planner,
            "composer": self.composer,
            "builder": self.builder,
            "scanner": self.scanner,
            "monitor": self.monitor,
            "reason": str(self.reason) if self.reason else None,
            "message": self.message,
        }


def degraded_message(reason: AIUnavailableReason) -> str:
    """The one sentence shown for a cause. Used by every surface, so they agree."""

    return _REASON_TEXT[reason]


def reason_for_code(code: str | None) -> AIUnavailableReason | None:
    """Classify an error code as an assistant problem, or ``None`` if it is not one.

    ``None`` matters as much as a match. A compile failure, a screening block or an
    ownership refusal is not an assistant problem, and dressing one up as "the assistant
    is resting" would hide a real finding the person needs to act on.
    """

    return _CODE_REASONS.get((code or "").strip().upper())


def availability_for(
    settings: Settings,
    *,
    user_id: UUID | None = None,
    language: str | None = None,
    circuit_open: bool = False,
    quota_exhausted: bool = False,
    cost_exhausted: bool = False,
) -> AIAvailability:
    """Read every switch once, in a fixed order, and report one answer.

    The order is the order of certainty. An operator switch is a decision somebody made;
    a spent budget is a fact about this account; an open circuit is a guess about the
    provider based on recent behaviour. The most certain cause is the one reported, so
    the message a person reads matches the thing that is actually stopping them.
    """

    builder = settings.setup_builder_enabled and _in_cohort(
        user_id, settings.setup_builder_user_ids
    )
    ai_off = settings.setup_chat_emergency_disabled or not settings.setup_chat_launch_v2_enabled
    language_allowed = _language_allowed(language, settings.setup_ai_languages)
    free_text = settings.setup_free_text_enabled and not ai_off and language_allowed
    planner = settings.setup_planner_enabled and not ai_off
    composer = settings.setup_composer_enabled and not ai_off

    reason: AIUnavailableReason | None = None
    if not (free_text and planner):
        reason = AIUnavailableReason.FEATURE_OFF
    elif cost_exhausted:
        reason = AIUnavailableReason.COST_LIMIT
        free_text = False
    elif quota_exhausted:
        reason = AIUnavailableReason.QUOTA_REACHED
        free_text = False
    elif circuit_open:
        reason = AIUnavailableReason.CIRCUIT_OPEN
        free_text = False

    return AIAvailability(
        free_text=free_text,
        planner=planner,
        composer=composer,
        builder=builder,
        scanner=settings.setup_scanner_enabled,
        monitor=settings.setup_monitor_enabled,
        reason=reason,
    )


def _in_cohort(user_id: UUID | None, allowed: list[str]) -> bool:
    if not allowed:
        return True
    if user_id is None:
        return False
    wanted = {item.strip().casefold() for item in allowed if item.strip()}
    return str(user_id).casefold() in wanted


def _language_allowed(language: str | None, allowed: list[str]) -> bool:
    if not allowed:
        return True
    wanted = {item.strip().casefold() for item in allowed if item.strip()}
    return (language or "en").strip().casefold() in wanted
