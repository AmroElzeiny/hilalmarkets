"""Every factual sentence in a reply must point at server evidence.

Constraining the composer to an execution result is necessary but not sufficient: it is
still free prose, and prose can drift from the object it was given. Checking the drift
with English phrase patterns fails for Arabic, for mixed language, and for any wording
nobody anticipated.

So a factual claim is not a sentence the server tries to parse — it is a *typed record
carrying the evidence ids it rests on*. The server looks the ids up. A claim whose ids do
not exist, or do not support its type, is dropped and replaced with deterministic text
built from the evidence itself. That check reads ids, not language, so it behaves the
same in every language.

Conversational acknowledgement stays free and needs no evidence, because it asserts
nothing about the platform.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

#: What a claim asserts. Each type has one evidence family that can support it.
ClaimType = Literal[
    "mutation",
    "readiness",
    "approval",
    "condition_explanation",
    "universe",
    "provider",
    "product_fact",
    "open_item",
]

#: Which evidence-id prefix each claim type must cite. A readiness claim cannot rest on
#: a product-knowledge key, and a mutation claim cannot rest on a gate status.
_REQUIRED_PREFIX: dict[str, tuple[str, ...]] = {
    "mutation": ("operation:",),
    "readiness": ("status:",),
    "approval": ("approval:",),
    "condition_explanation": ("condition:",),
    "universe": ("screening:", "universe:"),
    "provider": ("provider:", "preflight:"),
    "product_fact": ("product:",),
    "open_item": ("unresolved:", "unsupported:"),
}

#: A readiness claim must cite *every* gate, not just the one that suits it. Claiming
#: "ready" while a provider is unavailable was possible when one status id sufficed.
_READINESS_REQUIRED = (
    "status:compile",
    "status:screening",
    "status:provider",
    "status:approval_eligible",
)


@dataclass(frozen=True, slots=True)
class EvidenceLedger:
    """Every id a claim may cite this turn, and the fact behind it."""

    facts: dict[str, Any] = field(default_factory=dict)

    def has(self, evidence_id: str) -> bool:
        return evidence_id in self.facts

    def get(self, evidence_id: str) -> Any:
        return self.facts.get(evidence_id)

    def ids(self) -> list[str]:
        return sorted(self.facts)


@dataclass(frozen=True, slots=True)
class ValidatedClaim:
    """One claim that survived validation, or the reason it did not."""

    claim_type: str
    text: str
    evidence_ids: tuple[str, ...]
    accepted: bool
    reason: str | None = None


def build_evidence_ledger(
    *,
    reconciled_operations: list[dict[str, Any]],
    execution: dict[str, Any],
    draft_read_model: dict[str, Any],
    screening_evidence: dict[str, Any] | None,
    preflight_evidence: dict[str, Any] | None,
    product_knowledge: dict[str, Any],
) -> EvidenceLedger:
    """Collect this turn's citable facts under stable ids.

    Only *effective* operations get an id. An operation that was overwritten or
    cancelled inside the turn is not something a reply may claim happened.
    """

    facts: dict[str, Any] = {}
    for item in reconciled_operations:
        if item.get("net_effect") != "effective":
            continue
        facts[f"operation:{item['operation_id']}"] = item

    facts["status:compile"] = execution.get("compile_status")
    facts["status:screening"] = execution.get("screening_status")
    facts["status:provider"] = execution.get("provider_status")
    facts["status:approval_eligible"] = execution.get("approval_eligible")
    facts["status:final_chat_status"] = execution.get("final_chat_status")
    facts["approval:status"] = execution.get("approval_status")

    for condition in draft_read_model.get("conditions") or []:
        condition_id = condition.get("condition_id")
        if condition_id:
            facts[f"condition:{condition_id}"] = condition
    facts["universe:included"] = draft_read_model.get("included_symbols")
    facts["universe:excluded"] = draft_read_model.get("excluded_symbols")

    if screening_evidence:
        for key, value in screening_evidence.items():
            facts[f"screening:{key}"] = value
    if preflight_evidence:
        for key, value in preflight_evidence.items():
            facts[f"preflight:{key}"] = value

    for item in execution.get("unresolved_fields") or []:
        key = item.get("key")
        if key:
            facts[f"unresolved:{key}"] = item
    for item in execution.get("unsupported_requirements") or []:
        key = item.get("key")
        if key:
            facts[f"unsupported:{key}"] = item

    for key, value in product_knowledge.items():
        facts[f"product:{key}"] = value
    return EvidenceLedger(facts=facts)


def validate_claims(
    claims: list[Any],
    ledger: EvidenceLedger,
) -> list[ValidatedClaim]:
    """Accept only claims whose cited evidence exists and fits the claim type."""

    validated: list[ValidatedClaim] = []
    for claim in claims:
        claim_type = str(getattr(claim, "claim_type", "") or "")
        text = str(getattr(claim, "text", "") or "").strip()
        ids = tuple(getattr(claim, "evidence_ids", ()) or ())
        if not text:
            continue
        if not ids:
            validated.append(
                ValidatedClaim(claim_type, text, ids, False, "no evidence cited")
            )
            continue
        missing = [item for item in ids if not ledger.has(item)]
        if missing:
            validated.append(
                ValidatedClaim(
                    claim_type, text, ids, False, f"unknown evidence: {missing[:3]}"
                )
            )
            continue
        required = _REQUIRED_PREFIX.get(claim_type)
        if required and not any(item.startswith(required) for item in ids):
            validated.append(
                ValidatedClaim(
                    claim_type,
                    text,
                    ids,
                    False,
                    f"{claim_type} cannot rest on this evidence",
                )
            )
            continue
        if claim_type == "readiness":
            absent = [item for item in _READINESS_REQUIRED if item not in ids]
            if absent:
                validated.append(
                    ValidatedClaim(
                        claim_type,
                        text,
                        ids,
                        False,
                        f"a readiness claim must cite every gate; missing {absent}",
                    )
                )
                continue
            if not ledger.get("status:approval_eligible"):
                validated.append(
                    ValidatedClaim(
                        claim_type, text, ids, False, "the draft is not eligible"
                    )
                )
                continue
        if claim_type == "mutation" and not any(item.startswith("operation:") for item in ids):
            validated.append(
                ValidatedClaim(
                    claim_type, text, ids, False, "no effective operation to cite"
                )
            )
            continue
        validated.append(ValidatedClaim(claim_type, text, ids, True))
    return validated


def deterministic_claim_text(ledger: EvidenceLedger) -> list[str]:
    """Factual lines built from the evidence itself, for dropped claims.

    Used when a claim is refused, so the user still learns what happened rather than
    reading a sentence with the facts quietly removed.
    """

    lines: list[str] = []
    for evidence_id in ledger.ids():
        if not evidence_id.startswith("operation:"):
            continue
        item = ledger.get(evidence_id)
        summary = (item or {}).get("summary")
        if summary:
            lines.append(str(summary))
    status = ledger.get("status:final_chat_status")
    if status == "approved":
        lines.append("This setup stays approved.")
    elif ledger.get("status:approval_eligible"):
        lines.append("The inactive preview is ready to review and approve.")
    for evidence_id in ledger.ids():
        if evidence_id.startswith("unsupported:"):
            item = ledger.get(evidence_id) or {}
            contract = item.get("missing_contract")
            if contract:
                lines.append(f"Not expressible exactly: {contract}")
    return lines[:8]
