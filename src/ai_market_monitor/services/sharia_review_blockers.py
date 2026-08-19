"""Why a case could not be decided, said once, in words a reviewer can act on.

Every refusal from ``ShariaGovernanceService`` carries a short code. Two screens show
those refusals — the Cases page after a quick decision, and the single case after an
Approve or Reject — and each used to print the raw sentence the service raised. Those
sentences describe the rule that fired, not what the person should do next, so a page of
them reads as a page of errors.

This module is the one owner of the plain-language version. A code maps to two things:

* **what happened**, in everyday words;
* **the next step**, naming the one action that clears it.

A code that is not listed falls back to the service's own sentence rather than to a
guess. An invented explanation for a rule nobody mapped would be worse than a technical
one, because the reviewer would act on it.
"""

from __future__ import annotations

from dataclasses import dataclass

#: What happened, and what to do about it, for each refusal a reviewer can meet.
#:
#: Keyed by ``ShariaGovernanceError.code``. Kept next to nothing else so a new refusal in
#: the governance service has exactly one place to be explained.
_EXPLANATIONS: dict[str, tuple[str, str]] = {
    # --- the case is not in a state where this decision applies -------------------
    "case_not_found": ("This case no longer exists.", "Refresh the list."),
    "case_already_decided": (
        "This case already has a final decision.",
        "Open it and reopen the case if it needs deciding again.",
    ),
    "case_not_ready": (
        "This case is not ready for a decision yet.",
        "Open it and start or finish its research first.",
    ),
    # --- the evidence folder is not complete --------------------------------------
    "case_evidence_incomplete": (
        "This case is missing part of its evidence: the asset identity, the official "
        "source record, or the research folder.",
        "Open it and run research.",
    ),
    "case_evidence_missing": (
        "Part of this case's evidence can no longer be found.",
        "Open it and run research again.",
    ),
    "case_evidence_mismatch": (
        "The evidence on this case belongs to a different asset.",
        "Open it and run research again so the evidence matches.",
    ),
    "identity_not_verified": (
        "The asset identity has not been confirmed yet.",
        "Confirm the identity on the case before deciding.",
    ),
    "dossier_not_complete": (
        "The research folder for this asset is not finished.",
        "Open it and run research.",
    ),
    "validated_analysis_missing": (
        "The factual summary of the evidence is missing.",
        "Open it and run research again.",
    ),
    "evidence_snapshot_ids_invalid": (
        "One of the saved copies of the evidence is recorded wrongly.",
        "Open it and run research again.",
    ),
    "official_snapshot_not_reviewed": (
        "The saved copy of the official source page is not in the reviewed evidence.",
        "Open it and run research again.",
    ),
    "evidence_snapshot_missing": (
        "One of the saved copies of the evidence is gone.",
        "Open it and run research again.",
    ),
    "analysis_evidence_version_mismatch": (
        "The factual summary was written from older evidence than the case now holds.",
        "Open it and run research again.",
    ),
    "required_evidence_stale": (
        "The saved evidence is older than this methodology allows, or a source page "
        "could not be read.",
        "Open it and run research again to fetch the sources.",
    ),
    "evidence_completeness_below_threshold": (
        "Some official sources for this asset could not be read.",
        "Open it and run research again.",
    ),
    "critical_contradiction_unresolved": (
        "The evidence disagrees with itself and nobody has settled it.",
        "Open it and read the disagreement before deciding.",
    ),
    "critical_evidence_field_missing": (
        "Facts this methodology requires are missing from the evidence.",
        "Open it and run research again.",
    ),
    "mandatory_evidence_category_missing": (
        "A kind of evidence this methodology requires is not there.",
        "Open it and run research again.",
    ),
    # --- the methodology itself ---------------------------------------------------
    "case_methodology_required": (
        "No methodology is set on this case.",
        "Open it and set the methodology.",
    ),
    "case_methodology_missing": (
        "The methodology this case used no longer exists.",
        "Open it and set the current methodology.",
    ),
    "methodology_not_active": (
        "The methodology for this case is not active.",
        "Move the case to the active methodology version.",
    ),
    "methodology_not_effective": (
        "The methodology for this case has not started yet.",
        "Wait for its start date, or use the active version.",
    ),
    "methodology_expired": (
        "The methodology for this case has ended.",
        "Move the case to the current methodology version.",
    ),
    "development_methodology_not_publishable": (
        "This case uses a test methodology, which can never reach customers.",
        "Move it to a real methodology version.",
    ),
    "methodology_source_mismatch": (
        "The official record on this case does not match its methodology.",
        "Open it and check which authority the record came from.",
    ),
    "methodology_contract_invalid": (
        "The methodology's own rules are not readable.",
        "Fix the methodology before deciding any case under it.",
    ),
    "source_adapter_unsupported": (
        "There is no reviewed reader for this authority's records.",
        "Fix the methodology before deciding any case under it.",
    ),
    # --- the conditions on the decision itself ------------------------------------
    "criteria_not_approvable": (
        "One condition was marked in a way that blocks approval.",
        "Open it and decide that condition on its own.",
    ),
    "criterion_outcome_not_allowed": (
        "One condition cannot be marked that way under this methodology.",
        "Open it and decide that condition on its own.",
    ),
    "criterion_evidence_missing": (
        "One condition needs evidence this case does not have.",
        "Open it and run research again.",
    ),
    "criterion_decisions_incomplete": (
        "Not every required condition was decided.",
        "Open it and decide the remaining conditions.",
    ),
    "criterion_decisions_required": (
        "No condition was decided.",
        "Open it and decide each condition.",
    ),
    "criterion_decision_duplicate": (
        "The same condition was sent twice.",
        "Open it and decide each condition once.",
    ),
    "criterion_decision_invalid": (
        "One condition was sent in a form the system could not read.",
        "Open it and decide the conditions there.",
    ),
    "criterion_decision_unknown": (
        "A condition was sent that this methodology does not have.",
        "Open it and decide the conditions this methodology lists.",
    ),
    "criterion_reason_required": (
        "One condition needs a written reason.",
        "Open it and write what the evidence shows.",
    ),
    "use_case_decisions_required": (
        "The use rules for this asset were not decided.",
        "Open it and decide each use rule.",
    ),
    "use_case_decisions_incomplete": (
        "Not every use rule was decided.",
        "Open it and decide the remaining use rules.",
    ),
    "use_case_decision_duplicate": (
        "The same use rule was sent twice.",
        "Open it and decide each use rule once.",
    ),
    "use_case_decision_invalid": (
        "One use rule was sent in a form the system could not read.",
        "Open it and decide the use rules there.",
    ),
    "use_case_decision_unknown": (
        "A use rule was sent that this methodology does not have.",
        "Open it and decide the use rules this methodology lists.",
    ),
    "use_case_outcome_not_allowed": (
        "One use rule cannot be decided that way under this methodology.",
        "Open it and decide that use rule on its own.",
    ),
    "use_case_evidence_missing": (
        "One use rule needs evidence this case does not have.",
        "Open it and run research again.",
    ),
    "use_scope_not_approvable": (
        "One use rule was decided in a way that blocks approval.",
        "Open it and decide that use rule on its own.",
    ),
    "decision_reason_required": (
        "A written reason is required.",
        "Write one sentence saying why.",
    ),
    "qualification_required": (
        "Approving with a note needs the note written out.",
        "Open it and write the qualification.",
    ),
    # --- publication ---------------------------------------------------------------
    "external_rights_clearance_required": (
        "Written permission to show this provider's content publicly is not recorded.",
        "Open it and record the permission, then publish.",
    ),
    "external_status_not_publishable": (
        "The official record for this asset is not one that can be published.",
        "Open it and check the imported status.",
    ),
    "second_reviewer_required": (
        "Your settings require a second reviewer to publish.",
        "Ask another reviewer to publish it.",
    ),
    "approval_record_missing": (
        "There is no approval on this case to publish.",
        "Approve it first.",
    ),
    "case_not_approved": (
        "This case has not been approved yet.",
        "Approve it first.",
    ),
    "publication_reason_required": (
        "Publishing needs a written reason.",
        "Write one sentence saying why.",
    ),
    # --- who is allowed -------------------------------------------------------------
    "admin_required": ("You are not an administrator.", "Ask an administrator to do this."),
    "governance_grant_required": (
        "You have no review permission in this environment.",
        "Ask a system administrator to grant it.",
    ),
    "governance_permission_required": (
        "You do not have the permission this action needs.",
        "Ask a system administrator to grant it.",
    ),
    "unknown_permission": (
        "The permission this action asked for does not exist.",
        "Report this: it is a fault in the application, not in your case.",
    ),
    # --- something the reviewer forgot to write ------------------------------------
    "assignment_reason_required": (
        "Giving this case to someone needs a reason.",
        "Write one short sentence saying why.",
    ),
    "reopen_reason_required": (
        "Reopening this case needs a reason.",
        "Write one sentence saying why it should be looked at again.",
    ),
    "research_reason_required": (
        "Starting research needs a reason.",
        "Write one short sentence saying why.",
    ),
    "readiness_reason_required": (
        "Handing this case to a reviewer needs a reason.",
        "Write one sentence saying why the evidence is ready.",
    ),
    "retry_reason_required": (
        "Sending this message again needs a reason.",
        "Write one short sentence saying why.",
    ),
    "undo_reason_required": (
        "Taking this decision back needs a reason.",
        "Write one sentence saying why.",
    ),
    "note_required": (
        "The note is too short.",
        "Write at least a few words.",
    ),
    "evidence_request_required": (
        "Asking for evidence needs a reason and at least one thing to find.",
        "Write both, one item per line.",
    ),
    "dismissal_reason_required": (
        "Closing this as a false alarm needs a reason.",
        "Write one sentence saying why the change does not matter.",
    ),
    "safety_hold_reason_required": (
        "Holding the public Passport needs a reason.",
        "Write one sentence saying why it must come down.",
    ),
    "safety_hold_review_reason_required": (
        "Starting a fresh review after a hold needs a reason.",
        "Write one sentence saying why it should go ahead.",
    ),
    "rights_clearance_reference_required": (
        "The permission reference is too short.",
        "Paste the written permission or legal reference.",
    ),
    "field_review_reason_required": (
        "Reviewing this fact needs a reason.",
        "Write one sentence saying what the evidence shows.",
    ),
    "reviewer_value_required": (
        "Your own value for this fact is missing.",
        "Write what the evidence says it should be.",
    ),
    # --- the action does not apply to this case right now ----------------------------
    "invalid_research_transition": (
        "Research cannot start from where this case is now.",
        "Open it and look at what state it is in.",
    ),
    "invalid_ready_transition": (
        "This case cannot go to a reviewer from where it is now.",
        "Open it and look at what state it is in.",
    ),
    "invalid_priority": (
        "That priority is not one this system uses.",
        "Choose low, normal, high or urgent.",
    ),
    "invalid_review_field": (
        "That fact is not one this case records.",
        "Choose a fact shown on the case.",
    ),
    "invalid_ai_field_disposition": (
        "That is not a choice this system offers for a fact.",
        "Choose one of the options shown.",
    ),
    "dismissal_not_available": (
        "Only a change review or a customer report can be closed as a false alarm.",
        "Reject it instead if the asset should not be shown.",
    ),
    "dismissal_not_reviewable": (
        "This case is still being researched.",
        "Finish or pause the research first.",
    ),
    "safety_hold_not_active": (
        "This case is not on a safety hold.",
        "Nothing needs removing.",
    ),
    "active_publication_missing": (
        "There is no live Passport to hold.",
        "Nothing is public for this asset right now.",
    ),
    "asset_identity_missing": (
        "This case has no asset attached.",
        "Open it and attach the asset first.",
    ),
    "notification_not_found": (
        "That message record no longer exists.",
        "Refresh the list.",
    ),
    # --- the evidence behind an existing approval moved on --------------------------
    "approved_methodology_version_mismatch": (
        "The methodology changed after this case was approved.",
        "Reopen it and decide it again under the current version.",
    ),
    "approved_analysis_version_mismatch": (
        "The factual summary changed after this case was approved.",
        "Reopen it and decide it again on the current evidence.",
    ),
    "approved_evidence_version_mismatch": (
        "The evidence changed after this case was approved.",
        "Reopen it and decide it again on the current evidence.",
    ),
    "case_external_assessment_missing": (
        "This case has no official source record attached.",
        "Open it and run research.",
    ),
    "external_assessment_missing": (
        "The official source record for this case is gone.",
        "Open it and run research again.",
    ),
    "sc_reference_incomplete": (
        "The SC Malaysia meeting number or decision date is missing.",
        "Open it and check the imported record.",
    ),
    # --- undo ------------------------------------------------------------------------
    "undo_superseded": (
        "A newer decision was recorded, so the earlier one cannot be taken back.",
        "Reopen the case instead.",
    ),
    "undo_published_blocked": (
        "This Passport is already in front of customers.",
        "Put it on a safety hold instead of undoing it.",
    ),
    "undo_state_unknown": (
        "The place this case came from cannot be restored on its own.",
        "Reopen it and decide it again.",
    ),
    # --- automatic publication policy ------------------------------------------------
    "automatic_publication_disabled": (
        "Automatic publication is switched off.",
        "Approve the case yourself, or switch the setting on.",
    ),
    "human_review_required": (
        "Your settings require a person to review this one.",
        "Open it and decide it yourself.",
    ),
    "second_reviewer_policy_active": (
        "Your settings require a second reviewer, so nothing publishes automatically.",
        "Have two reviewers decide it.",
    ),
    "external_reference_not_eligible": (
        "Only an imported record explicitly marked compliant can publish this way.",
        "Open it and decide it yourself.",
    ),
}


@dataclass(frozen=True, slots=True)
class BlockerExplanation:
    """One refusal, in the two parts a reviewer needs."""

    code: str
    what_happened: str
    next_step: str

    def sentence(self) -> str:
        return f"{self.what_happened} {self.next_step}".strip()


def explain(code: str | None, fallback: str) -> BlockerExplanation:
    """The plain-language version of one refusal.

    ``fallback`` is the service's own sentence, used unchanged when the code has no entry
    here. Never invent an explanation for a rule that is not mapped: a reviewer acts on
    what this says.
    """

    mapped = _EXPLANATIONS.get(code or "")
    if mapped is None:
        return BlockerExplanation(code=code or "", what_happened=fallback, next_step="")
    return BlockerExplanation(code=code or "", what_happened=mapped[0], next_step=mapped[1])


def explain_error(error: Exception) -> BlockerExplanation:
    """The plain-language version of a raised governance refusal."""

    return explain(getattr(error, "code", None), str(error))
