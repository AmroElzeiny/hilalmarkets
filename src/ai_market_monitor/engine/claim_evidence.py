"""Every factual sentence in a reply must state a proposition the server can check.

Constraining the composer to an execution result is necessary but not sufficient: it is
still free prose, and prose can drift from the object it was given. Checking the drift
with English phrase patterns fails for Arabic, for mixed language, and for any wording
nobody anticipated.

The first fix made a claim carry *evidence ids*. That proved the claim rested on
something real. It did not prove the sentence described that thing — a claim could cite a
genuinely effective operation and then describe a different one, and citing valid
evidence for the wrong sentence passed every check.

So a claim now carries a **proposition**: subject, predicate, asserted value. The server
looks the subject up and compares the value with the authoritative one. Six checks run,
in order:

1. every cited evidence id exists
2. the evidence family fits the claim type
3. the subject exists
4. the predicate is supported for that subject
5. the asserted value equals the authoritative value
6. every symbol, condition or version named in the value exists in the final state

All six read ids and values, never language, so they behave the same in every language.
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

#: Which subject family each claim type may talk about.
_SUBJECT_PREFIX: dict[str, tuple[str, ...]] = dict(_REQUIRED_PREFIX)

#: A readiness claim must cite *every* gate, not just the one that suits it. Claiming
#: "ready" while a provider is unavailable was possible when one status id sufficed.
_READINESS_REQUIRED = (
    "status:compile",
    "status:screening",
    "status:provider",
    "status:approval_eligible",
)


def _normalise(value: Any) -> str:
    """One spelling for comparison, so `True`, `true` and `TRUE` are one value."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, list | tuple | set):
        return ",".join(sorted(_normalise(item) for item in value))
    return str(value).strip().casefold()


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
    claim_id: str = ""
    subject_id: str = ""
    predicate: str = ""
    asserted_value: str = ""


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


# --------------------------------------------------------------------------------
# Predicates: what may be asserted about each kind of subject, and what the
# authoritative answer is.
# --------------------------------------------------------------------------------


def _operation_values(fact: Any, predicate: str) -> set[str] | None:
    """Authoritative values for a claim about one effective operation."""

    if not isinstance(fact, dict):
        return None
    changes = [item for item in (fact.get("changes") or []) if isinstance(item, dict)]
    if predicate == "applied":
        return {_normalise(fact.get("net_effect"))}
    if predicate == "operation_kind":
        return {_normalise(fact.get("operation_kind"))}
    kind_by_predicate = {
        "symbol_included": "symbol_included",
        "symbol_excluded": "symbol_excluded",
        "symbol_include_removed": "symbol_include_removed",
        "symbol_exclude_removed": "symbol_exclude_removed",
        "condition_added": "condition_added",
        "condition_removed": "condition_removed",
        "condition_updated": "condition_updated",
        "threshold_changed": "threshold_changed",
        "timeframe_changed": "timeframe_changed",
        "operator_changed": "operator_changed",
        "direction_changed": "direction_changed",
        "formula_changed": "formula_changed",
        "sharia_policy_changed": "sharia_policy_changed",
        "market_scope_changed": "market_scope_changed",
        "mode_changed": "mode_changed",
    }
    change_kind = kind_by_predicate.get(predicate)
    if change_kind is None:
        return None
    values: set[str] = set()
    for change in changes:
        if change.get("kind") != change_kind:
            continue
        # The target is the identity; `after` is what a value-changing edit landed on.
        for candidate in (change.get("target"), change.get("after")):
            if candidate not in (None, ""):
                values.add(_normalise(candidate))
    return values


def _condition_values(fact: Any, predicate: str) -> set[str] | None:
    if not isinstance(fact, dict):
        return None
    by_predicate = {
        "threshold_equals": "threshold",
        "timeframe_equals": "trigger_timeframe",
        "operator_equals": "operator",
        "direction_equals": "movement_direction",
        "formula_equals": "formula",
        "unit_equals": "unit",
    }
    if predicate == "exists":
        return {"true"}
    field_name = by_predicate.get(predicate)
    if field_name is None:
        return None
    return {_normalise(fact.get(field_name))}


def _collection_values(fact: Any, predicate: str) -> set[str] | None:
    """`contains` over a list, `count_equals` over its length."""

    if predicate == "contains":
        if isinstance(fact, list | tuple):
            return {_normalise(item) for item in fact}
        return None
    if predicate == "count_equals":
        if isinstance(fact, list | tuple):
            return {_normalise(len(fact))}
        if isinstance(fact, int):
            return {_normalise(fact)}
        return None
    if predicate == "equals":
        return {_normalise(fact)}
    return None


def _scalar_values(fact: Any, predicate: str) -> set[str] | None:
    if predicate == "equals":
        return {_normalise(fact)}
    return None


def _open_item_values(fact: Any, predicate: str) -> set[str] | None:
    if not isinstance(fact, dict):
        return None
    if predicate == "exists":
        return {"true"}
    if predicate == "question_equals":
        return {_normalise(fact.get("question"))}
    if predicate == "missing_contract_equals":
        return {_normalise(fact.get("missing_contract"))}
    return None


def _product_values(fact: Any, predicate: str) -> set[str] | None:
    if predicate in {"states", "equals"}:
        return {_normalise(fact)}
    return None


#: Subject family → how to answer a predicate about it. Keyed by the id prefix, so a new
#: evidence family cannot be claimed about until it is given a resolver here.
_PREDICATE_RESOLVERS = {
    "operation:": _operation_values,
    "condition:": _condition_values,
    "status:": _scalar_values,
    "approval:": _scalar_values,
    "universe:": _collection_values,
    "screening:": _collection_values,
    "preflight:": _collection_values,
    "provider:": _collection_values,
    "unresolved:": _open_item_values,
    "unsupported:": _open_item_values,
    "product:": _product_values,
}


def _resolver_for(subject_id: str) -> Any:
    for prefix, resolver in _PREDICATE_RESOLVERS.items():
        if subject_id.startswith(prefix):
            return resolver
    return None


def _final_state_names(ledger: EvidenceLedger) -> set[str]:
    """Every symbol and condition id that exists in the final state.

    Rule 6: a value naming a market or rule that is not in the final draft is refused,
    even when the rest of the proposition checks out.
    """

    names: set[str] = set()
    for evidence_id in ledger.ids():
        if evidence_id.startswith("condition:"):
            names.add(_normalise(evidence_id.split(":", 1)[1]))
    for key in ("universe:included", "universe:excluded"):
        value = ledger.get(key)
        if isinstance(value, list | tuple):
            names.update(_normalise(item) for item in value)
    screening = ledger.get("screening:included_symbols")
    if isinstance(screening, list | tuple):
        names.update(_normalise(item) for item in screening)
    return names


#: Predicates whose asserted value names a market or a rule, so it must exist in the
#: final state. A value like `available` or `5` is not a name and is not checked here.
_NAMING_PREDICATES = frozenset(
    {
        "symbol_included",
        "symbol_excluded",
        "symbol_include_removed",
        "symbol_exclude_removed",
        "condition_added",
        "condition_updated",
        "contains",
    }
)


def _refusal_reason(
    *,
    claim_type: str,
    ids: tuple[str, ...],
    subject_id: str,
    predicate: str,
    asserted: str,
    ledger: EvidenceLedger,
    final_names: set[str],
) -> str | None:
    """Why this proposition cannot be accepted, or ``None`` when it can.

    The six checks in order. Each one returns the plain reason it failed, which the
    operator trace records — a claim silently dropped is a claim nobody can debug.
    """

    # 1. Every evidence id exists.
    if not ids:
        return "no evidence cited"
    missing = [item for item in ids if not ledger.has(item)]
    if missing:
        return f"unknown evidence: {missing[:3]}"
    # 2. The evidence family supports the claim type.
    required = _REQUIRED_PREFIX.get(claim_type)
    if required and not any(item.startswith(required) for item in ids):
        return f"{claim_type} cannot rest on this evidence"
    if claim_type == "readiness":
        absent = [item for item in _READINESS_REQUIRED if item not in ids]
        if absent:
            return f"a readiness claim must cite every gate; missing {absent}"
        if not ledger.get("status:approval_eligible"):
            return "the draft is not eligible"
    if claim_type == "mutation" and not any(
        item.startswith("operation:") for item in ids
    ):
        return "no effective operation to cite"

    # 3-6 need a proposition. A claim without one passes the evidence checks alone only
    # when there is nothing specific to assert — never for a mutation, which is the claim
    # most able to describe the wrong thing.
    if not subject_id or not predicate:
        if claim_type == "mutation":
            return "a mutation claim must state what it changed"
        return None
    # 3. The subject exists, and it is one of the ids this claim cites.
    if not ledger.has(subject_id):
        return f"unknown subject {subject_id!r}"
    if subject_id not in ids:
        return "the subject must be one of the cited evidence ids"
    subject_prefixes = _SUBJECT_PREFIX.get(claim_type)
    if subject_prefixes and not subject_id.startswith(subject_prefixes):
        return f"{claim_type} cannot be about {subject_id!r}"
    # 4. The predicate is supported for that subject.
    resolver = _resolver_for(subject_id)
    if resolver is None:
        return f"nothing can be asserted about {subject_id!r}"
    authoritative = resolver(ledger.get(subject_id), predicate)
    if authoritative is None:
        return f"{predicate!r} is not a supported statement about {subject_id!r}"
    # 5. The asserted value equals the authoritative value.
    wanted = _normalise(asserted)
    if wanted not in {_normalise(item) for item in authoritative}:
        return "the stated value is not what the evidence says"
    # 6. A value that names a market or a rule must exist in the final state.
    if predicate in _NAMING_PREDICATES and final_names and wanted not in final_names:
        return "that market or rule is not in the final setup"
    return None


def validate_claims(
    claims: list[Any],
    ledger: EvidenceLedger,
) -> list[ValidatedClaim]:
    """Accept a claim only when its whole proposition matches the evidence."""

    final_names = _final_state_names(ledger)
    validated: list[ValidatedClaim] = []
    for claim in claims:
        claim_type = str(getattr(claim, "claim_type", "") or "")
        text = str(getattr(claim, "text", "") or "").strip()
        ids = tuple(getattr(claim, "evidence_ids", ()) or ())
        claim_id = str(getattr(claim, "claim_id", "") or "")
        subject_id = str(getattr(claim, "subject_id", "") or "")
        predicate = str(getattr(claim, "predicate", "") or "")
        asserted = str(getattr(claim, "asserted_value", "") or "")
        if not text:
            continue
        reason = _refusal_reason(
            claim_type=claim_type,
            ids=ids,
            subject_id=subject_id,
            predicate=predicate,
            asserted=asserted,
            ledger=ledger,
            final_names=final_names,
        )
        validated.append(
            ValidatedClaim(
                claim_type,
                text,
                ids,
                reason is None,
                reason,
                claim_id=claim_id,
                subject_id=subject_id,
                predicate=predicate,
                asserted_value=asserted,
            )
        )
    return validated


def requires_factual_answer(
    *,
    reconciled_operations: list[dict[str, Any]],
    response_points: list[dict[str, Any]],
    questions_to_answer: list[str],
) -> bool:
    """Does this turn owe the user a fact?

    An empty claim list is fine for "hello" and refused for "I added your rule". Read from
    what the turn actually did and what the plan said the reply must cover — never from
    the reply itself, which is the thing being checked.
    """

    if any(item.get("net_effect") == "effective" for item in reconciled_operations):
        return True
    if questions_to_answer:
        return True
    factual_kinds = {"answer_question", "explain_change", "explain_refusal", "state_next_step"}
    return any(item.get("kind") in factual_kinds for item in response_points)


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
