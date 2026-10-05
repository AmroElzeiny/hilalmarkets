"""A reviewer's own words, made into the reasons a reader sees on a Passport.

A reviewer types a reason in a hurry — "pages say staking w/ fixed APY, lending desk on
blog, team anon". That is the record, and it is kept exactly as typed. What a reader of
the Passport needs is the same points said plainly. This module drafts that version and
nothing else.

**The draft is never the decision.** It is shown to the reviewer, who edits or keeps it,
and only what the reviewer submits is stored as :attr:`ReviewDecision.public_reasons`.
The AI never approves, never rejects and never publishes; the decision route does, and it
refuses public reasons it has not been sent.

**It may only restate.** The model is told to add nothing, and two checks hold it to
that without trusting it:

* a number in the draft that the reviewer never typed is invented — the sentence is
  dropped (confidence is the model's opinion of itself; only the source text can show
  a threshold was made up);
* a sentence that breaks the brand guide's forbidden claims or spelling rules
  (:func:`core.copy_rules.scan_text`) is dropped. The same check runs again on the
  reasons the reviewer finally submits, in :func:`clean_public_reasons`.

When the model cannot be asked, the reviewer's own sentences are offered instead, said
so, and never as a failure that blocks the decision.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.copy_rules import scan_text
from ai_market_monitor.db.models import AIUsageEvent
from ai_market_monitor.services.ai_provider import (
    AIProviderConfigError,
    is_configured,
    resolve_feature_model,
    resolve_provider,
)
from ai_market_monitor.services.openai_structured_call import (
    StructuredCallError,
    structured_call,
)
from ai_market_monitor.services.system_brain import estimate_usage_cost

logger = logging.getLogger(__name__)

#: Caps on what is kept. They truncate; they never raise — a decision must never fail
#: because a reason was long.
MAX_REASONS = 8
MAX_REASON_CHARS = 400
MAX_SOURCE_CHARS = 5_000

#: The draft is a short rewrite, not research: a reviewer is waiting on the page.
DRAFT_TIMEOUT_SECONDS = 45
DRAFT_MAX_OUTPUT_TOKENS = 1_200

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")

DecisionKind = str  # "approve" or "reject"


class _Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasons: list[str]


_INSTRUCTIONS = (
    "You rewrite a Hilal Markets reviewer's note into short public reasons for a coin's "
    "Evidence Passport. Readers are beginners and many are not native English speakers. "
    "\n\nRules:\n"
    "1. Restate ONLY the points in the reviewer's note. Add no fact, number, name or "
    "claim that is not in the note. Drop nothing that matters.\n"
    "2. One point per reason. Each reason is one or two short sentences in very simple "
    "English. No jargon, no abbreviations.\n"
    "3. Never write halal, haram, permissible, forbidden, guaranteed, or any advice to "
    "buy, sell or hold. Say what the reviewer found, not what the reader should do.\n"
    "4. Write the brand as 'Hilal Markets' and the word as 'Shariah'.\n"
    f"5. At most {MAX_REASONS} reasons."
)


@dataclass(frozen=True, slots=True)
class ReasonDraft:
    reasons: list[str]
    #: ``True`` when the model wrote them; ``False`` when they are the reviewer's own
    #: sentences because the model could not be asked.
    by_ai: bool
    #: One plain sentence for the reviewer when something is worth saying.
    note: str = ""
    model: str = ""
    usage: dict[str, Any] | None = None

    def usage_event(self, settings: Settings) -> AIUsageEvent | None:
        """The spending ledger row for this draft, priced by the one cost calculator."""

        if not self.by_ai or not self.usage:
            return None
        try:
            provider = resolve_provider(self.model)
        except AIProviderConfigError:
            return None
        usage = dict(self.usage)
        output_details = usage.get("output_tokens_details") or {}
        input_details = usage.get("input_tokens_details") or {}
        priced = self.model in settings.openai_model_pricing_usd_per_million
        return AIUsageEvent(
            operation="public_reasons_draft",
            provider=provider,
            model=self.model,
            reasoning_effort=settings.coin_terms_ai_reasoning_effort,
            input_tokens=int(usage.get("input_tokens") or 0),
            cached_input_tokens=int(input_details.get("cached_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            reasoning_tokens=int(output_details.get("reasoning_tokens") or 0),
            estimated_cost_usd=estimate_usage_cost(settings, model=self.model, usage=usage),
            pricing_source="configured_model_pricing" if priced else "no_configured_pricing",
            outcome="completed",
            raw_usage=usage,
            created_at=datetime.now(UTC),
        )


def _clip(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text[: MAX_REASON_CHARS - 1] + "…" if len(text) > MAX_REASON_CHARS else text


def _breaks_copy_rules(text: str) -> bool:
    return bool(scan_text(text, Path("public_reason")))


def clean_public_reasons(values: Sequence[str] | str) -> list[str]:
    """The reasons a reviewer submitted, made safe to publish. Never raises.

    One reason per line. Empty lines and repeats go; each line is capped. A line that
    breaks the forbidden-claims or spelling rules is dropped — the reviewer sees what
    was kept, because the Passport shows exactly this list.
    """

    lines = values.splitlines() if isinstance(values, str) else list(values)
    kept: list[str] = []
    seen: set[str] = set()
    for line in lines:
        text = _clip(str(line).strip().lstrip("-•*").strip())
        if not text or text.casefold() in seen or _breaks_copy_rules(text):
            continue
        seen.add(text.casefold())
        kept.append(text)
    return kept[:MAX_REASONS]


def own_sentences(note: str) -> list[str]:
    """The reviewer's note split into sentences — the draft when no model answers."""

    return clean_public_reasons(_SENTENCE_END.split(note or ""))


def grounded_reasons(draft: Sequence[str], note: str) -> list[str]:
    """Keep the drafted reasons whose every number the reviewer actually typed."""

    typed = set(_NUMBER.findall(note or ""))
    kept = [
        reason
        for reason in draft
        if set(_NUMBER.findall(reason)) <= typed
    ]
    return clean_public_reasons(kept)


class PublicReasonWriter:
    """Drafts public reasons from a reviewer's note. Never raises."""

    def __init__(self, settings: Settings, *, transport=None) -> None:
        self.settings = settings
        self.transport = transport

    def _model(self) -> str | None:
        # The model that already reads new coins for System Brain. A second setting for
        # a short rewrite would be one more key to keep in four files for no gain.
        if not self.settings.coin_terms_ai_enabled:
            return None
        try:
            model = resolve_feature_model(
                self.settings.coin_terms_ai_model, setting_name="COIN_TERMS_AI_MODEL"
            )
        except AIProviderConfigError:
            return None
        return model if is_configured(self.settings, model) else None

    async def draft(
        self,
        *,
        note: str,
        decision: DecisionKind,
        coin: str,
    ) -> ReasonDraft:
        note = (note or "").strip()[:MAX_SOURCE_CHARS]
        if len(note) < 10:
            return ReasonDraft(
                reasons=[],
                by_ai=False,
                note="Write your reason first (at least 10 characters).",
            )
        fallback = own_sentences(note)
        model = self._model()
        if model is None:
            return ReasonDraft(
                reasons=fallback,
                by_ai=False,
                note="The AI is not set up here, so your own sentences are shown.",
            )
        try:
            answer, usage = await structured_call(
                self.settings,
                schema_model=_Draft,
                schema_name="hilalmarkets_public_reasons",
                instructions=_INSTRUCTIONS,
                payload={
                    "coin": coin,
                    **(
                        {"decision": "approved" if decision == "approve" else "not approved"}
                        if decision in {"approve", "reject"}
                        else {}
                    ),
                    "reviewer_note": note,
                },
                model=model,
                reasoning_effort=self.settings.coin_terms_ai_reasoning_effort,
                max_output_tokens=DRAFT_MAX_OUTPUT_TOKENS,
                timeout_seconds=min(
                    DRAFT_TIMEOUT_SECONDS, self.settings.coin_terms_ai_timeout_seconds
                ),
                stage="public_reasons_draft",
                transport=self.transport,
            )
        except StructuredCallError as exc:
            logger.info("public_reasons_draft_failed", extra={"code": exc.code})
            return ReasonDraft(
                reasons=fallback,
                by_ai=False,
                note="The AI did not answer, so your own sentences are shown.",
            )
        reasons = grounded_reasons(answer.reasons, note)
        dropped = len([item for item in answer.reasons if item.strip()]) - len(reasons)
        if not reasons:
            return ReasonDraft(
                reasons=fallback,
                by_ai=False,
                note="The AI's version added things you did not write, so your own "
                "sentences are shown.",
            )
        return ReasonDraft(
            reasons=reasons,
            by_ai=True,
            model=model,
            usage=dict(usage or {}),
            note=(
                f"{dropped} AI sentence(s) were left out because they added something "
                "you did not write."
                if dropped > 0
                else ""
            ),
        )


__all__ = [
    "MAX_REASONS",
    "PublicReasonWriter",
    "ReasonDraft",
    "clean_public_reasons",
    "grounded_reasons",
    "own_sentences",
]
