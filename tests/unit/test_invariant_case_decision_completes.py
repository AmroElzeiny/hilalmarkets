"""A decision a reviewer takes has to finish.

Approve means the asset reaches customers. Reject means it does not, and the evidence is
kept. Anything in between — a decision recorded but never published, a queue full of
cases the approval refuses — is the system telling the reviewer one thing and doing
another.

The failures this file pins down were all the same shape: **one fact with more than one
owner, and each owner knowing a different version of it.**

* "this research folder is finished" was written two ways, ``completed`` and ``ready``,
  and the approval had only learned one of them;
* "the reviewed evidence" meant *the changed pages* to the change pipeline and *the
  changed pages plus the authority row* to the approval;
* "how old evidence may be" was owned by the methodology **and**, in parallel, by a
  configuration timer on the case, and the timer was stricter;
* "this case is ready for a reviewer" was decided by the research pipeline with a
  shorter list of conditions than the approval actually checks.

Each test below asserts the rule for the whole family, not the one example that was
reported. The fixtures come from ``tests/services/test_sc_malaysia_governance.py``: a
second copy of a methodology fixture is a second definition of what a valid review looks
like, and the two would drift.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from ai_market_monitor.db.models import (
    AssetResearchDossier,
    ExternalAssessment,
    PublishedAssetAssessment,
    ReviewCase,
    SourceSnapshot,
    User,
)
from ai_market_monitor.db.models.enums import UserRole
from ai_market_monitor.services import sharia_dossier_state as dossier_state
from ai_market_monitor.services.sharia_governance import (
    ShariaGovernanceError,
    ShariaGovernanceService,
)
from ai_market_monitor.services.sharia_review_blockers import explain, explain_error
from ai_market_monitor.services.system_brain_bulk_review import (
    BulkReviewService,
    CaseOutcome,
)
from tests.services.test_sc_malaysia_governance import (
    TEST_CRITERIA,
    TEST_USE_CASES,
    _ready_case,
)

REASON = "The retained official evidence supports every condition for this asset."

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"

#: The one module allowed to spell out what state a dossier is in.
DOSSIER_STATE_OWNER = "sharia_dossier_state.py"


async def _reviewer(session, name: str = "Decision reviewer") -> User:
    reviewer = User(display_name=name, role=UserRole.ADMIN)
    session.add(reviewer)
    await session.flush()
    return reviewer


def _criteria() -> list[dict]:
    return [
        {"key": item["key"], "outcome": "pass", "reviewer_explanation": REASON}
        for item in TEST_CRITERIA
    ]


def _uses() -> list[dict]:
    return [
        {"key": item["key"], "decision": "covered", "reason": REASON}
        for item in TEST_USE_CASES
    ]


# --------------------------------------------------------------------------------
# One word for a finished research folder.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", sorted(dossier_state.COMPLETE_STATES))
async def test_every_stored_spelling_of_a_finished_folder_can_be_approved(
    test_context, spelling
):
    """Both spellings mean finished, so both have to be approvable.

    A dossier written by the source-change pipeline said ``ready``; the approval accepted
    only ``completed`` and answered "The factual research dossier is not complete." about
    a folder that was complete. Testing only the spelling that was reported would leave
    the next one waiting.
    """

    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        dossier = await session.get(AssetResearchDossier, case.dossier_id)
        assert dossier is not None
        dossier.state = spelling
        await session.flush()
        reviewer = await _reviewer(session)

        outcome = await ShariaGovernanceService(
            session, test_context["settings"]
        ).approve_and_publish(
            case.id,
            admin_user_id=reviewer.id,
            reason=REASON,
            criterion_decisions=_criteria(),
            use_case_decisions=_uses(),
        )
        await session.commit()

    assert outcome.published is True


@pytest.mark.parametrize("spelling", sorted(dossier_state.COMPLETE_STATES))
def test_every_stored_spelling_counts_as_finished_everywhere(spelling):
    """The in-memory answer and the database answer are the same answer."""

    assert dossier_state.is_complete(spelling) is True
    assert dossier_state.canonical_state(spelling) == dossier_state.COMPLETE
    clause = dossier_state.complete_state_clause(AssetResearchDossier.state)
    assert spelling in clause.right.value


@pytest.mark.parametrize("unfinished", [dossier_state.RESEARCHING, dossier_state.NEEDS_EVIDENCE])
def test_an_unfinished_folder_is_never_treated_as_finished(unfinished):
    assert dossier_state.is_complete(unfinished) is False


def test_no_other_module_decides_what_a_finished_folder_is_called():
    """One owner, or the two spellings come back the next time somebody adds a state.

    Any module comparing ``dossier.state`` — or the database column — to a word of its
    own has quietly become a second owner of the vocabulary. That is exactly how the two
    spellings drifted apart in the first place.
    """

    comparison = re.compile(
        r"(?:dossier\.state|AssetResearchDossier\.state)\s*(?:==|!=|in\b)"
    )
    offenders = [
        path.relative_to(SRC_ROOT).as_posix()
        for path in SRC_ROOT.rglob("*.py")
        if path.name != DOSSIER_STATE_OWNER and comparison.search(path.read_text("utf-8"))
    ]

    assert offenders == []


# --------------------------------------------------------------------------------
# Approve publishes. Reject rejects. Both, one case and in bulk.
# --------------------------------------------------------------------------------


async def test_approving_one_case_puts_it_in_front_of_customers(test_context):
    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = await _reviewer(session)
        outcome = await ShariaGovernanceService(
            session, test_context["settings"]
        ).approve_and_publish(
            case.id,
            admin_user_id=reviewer.id,
            reason=REASON,
            criterion_decisions=_criteria(),
            use_case_decisions=_uses(),
        )
        await session.commit()
        refreshed = await session.get(ReviewCase, case.id)
        publication = await session.scalar(
            select(PublishedAssetAssessment).where(
                PublishedAssetAssessment.canonical_asset_id == case.canonical_asset_id
            )
        )

    assert outcome.published is True
    assert outcome.publication_pending_reason is None
    assert refreshed is not None
    assert refreshed.state == "published"
    assert refreshed.publication_state == "published"
    assert publication is not None and publication.is_active is True


async def test_rejecting_one_case_stores_it_and_publishes_nothing(test_context):
    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = await _reviewer(session)
        await ShariaGovernanceService(session, test_context["settings"]).reject_and_store(
            case.id,
            admin_user_id=reviewer.id,
            reason="The official source does not cover this asset at all.",
        )
        await session.commit()
        refreshed = await session.get(ReviewCase, case.id)
        publication = await session.scalar(select(PublishedAssetAssessment))

    assert refreshed is not None
    assert refreshed.state == "rejected"
    assert refreshed.publication_state == "stored_not_published"
    assert publication is None


@pytest.mark.parametrize(
    ("action", "expected_state"),
    [("approve", "published"), ("reject", "rejected")],
)
async def test_a_quick_decision_finishes_the_same_way_as_a_single_one(
    test_context, action, expected_state
):
    """The Cases page and the case screen must not reach different endings."""

    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = await _reviewer(session)
        outcome = await BulkReviewService(session, test_context["settings"]).apply(
            [case.id],
            action=action,
            reason=REASON,
            admin_user_id=reviewer.id,
        )
        await session.commit()
        refreshed = await session.get(ReviewCase, case.id)

    assert outcome.applied == 1
    assert outcome.failed == 0
    assert refreshed is not None and refreshed.state == expected_state


async def test_a_second_reviewer_policy_keeps_the_approval_and_says_what_it_waits_for(
    test_context,
):
    """A publication that legitimately waits is an outcome, never a lost decision."""

    settings = test_context["settings"].model_copy(
        update={"require_second_reviewer": True}
    )
    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = await _reviewer(session)
        outcome = await ShariaGovernanceService(session, settings).approve_and_publish(
            case.id,
            admin_user_id=reviewer.id,
            reason=REASON,
            criterion_decisions=_criteria(),
            use_case_decisions=_uses(),
        )
        await session.commit()
        refreshed = await session.get(ReviewCase, case.id)

    assert outcome.published is False
    assert outcome.decision is not None
    assert outcome.publication_pending_reason
    assert refreshed is not None
    assert refreshed.state == "approved"
    assert refreshed.publication_state == "awaiting_second_approval"


async def test_a_publication_that_refuses_never_throws_away_the_approval(test_context):
    """Written permission is still missing, so the Passport waits — the decision does not.

    Rolling the approval back because the second step refused would lose a judgement a
    person actually made, and the reviewer would have to make it again.
    """

    settings = test_context["settings"].model_copy(
        update={
            "sharia_external_rights_enforcement": True,
            "sharia_import_metadata_only_publication": False,
        }
    )
    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        external = await session.get(ExternalAssessment, case.external_assessment_id)
        assert external is not None
        external.source_row_id = "SC-001-bitcoin"
        external.commercial_display_allowed = False
        await session.flush()
        reviewer = await _reviewer(session)

        outcome = await ShariaGovernanceService(session, settings).approve_and_publish(
            case.id,
            admin_user_id=reviewer.id,
            reason=REASON,
            criterion_decisions=_criteria(),
            use_case_decisions=_uses(),
        )
        await session.commit()
        refreshed = await session.get(ReviewCase, case.id)

    assert outcome.published is False
    assert outcome.publication_pending_reason
    assert refreshed is not None and refreshed.state == "approved"


async def test_the_rights_rule_asks_what_is_published_not_who_pressed_the_button(
    test_context,
):
    """Metadata-only publication reproduces no provider content, whoever decided it.

    Keying the exemption on the actor let the scheduled import publish an asset while a
    human approving the same asset, producing the same page, was refused.
    """

    settings = test_context["settings"].model_copy(
        update={
            "sharia_external_rights_enforcement": True,
            "sharia_import_metadata_only_publication": True,
        }
    )
    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        external = await session.get(ExternalAssessment, case.external_assessment_id)
        assert external is not None
        external.source_row_id = "SC-001-bitcoin"
        external.commercial_display_allowed = False
        await session.flush()
        reviewer = await _reviewer(session)

        outcome = await ShariaGovernanceService(session, settings).approve_and_publish(
            case.id,
            admin_user_id=reviewer.id,
            reason=REASON,
            criterion_decisions=_criteria(),
            use_case_decisions=_uses(),
        )
        await session.commit()

    assert outcome.published is True


# --------------------------------------------------------------------------------
# One owner for how old evidence may be.
# --------------------------------------------------------------------------------


async def test_an_expired_re_check_reminder_does_not_refuse_a_decision(test_context):
    """The methodology owns evidence age. A configuration timer does not get a vote.

    ``source_freshness_deadline`` is a reminder to re-check the sources, worked out from
    a scan interval in configuration. Refusing on it meant a second, stricter, ungoverned
    age rule silently beat the methodology's own — and every case older than a day became
    impossible to decide.
    """

    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        case.source_freshness_deadline = datetime.now(UTC) - timedelta(days=30)
        await session.flush()

        blocker = await ShariaGovernanceService(
            session, test_context["settings"]
        ).review_blocker(case)

    assert blocker is None


async def test_evidence_older_than_the_methodology_allows_is_still_refused(test_context):
    """Removing the duplicate rule must not remove the real one."""

    async with test_context["session_factory"]() as session:
        case, methodology = await _ready_case(session)
        requirements = dict(methodology.evidence_requirements_json)
        requirements["maximum_source_age_days"] = 1
        methodology.evidence_requirements_json = requirements
        dossier = await session.get(AssetResearchDossier, case.dossier_id)
        assert dossier is not None
        snapshot_ids = list(dossier.source_snapshot_ids)
        for value in snapshot_ids:
            snapshot = await session.get(SourceSnapshot, UUID(value))
            if snapshot is not None:
                snapshot.retrieved_at = datetime.now(UTC) - timedelta(days=5)
        await session.flush()

        blocker = await ShariaGovernanceService(
            session, test_context["settings"]
        ).review_blocker(case)

    assert blocker is not None
    assert blocker.code == "required_evidence_stale"


# --------------------------------------------------------------------------------
# "Ready for review" means the approval would accept it.
# --------------------------------------------------------------------------------


async def test_ready_for_review_is_decided_by_the_approval_and_nothing_else(test_context):
    """One question, one answer, wherever it is asked.

    A pipeline with its own shorter list of conditions is what filled the queue with
    cases that could never be approved.
    """

    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        service = ShariaGovernanceService(session, test_context["settings"])
        assert await service.is_ready_for_review(case) is True

        dossier = await session.get(AssetResearchDossier, case.dossier_id)
        assert dossier is not None
        dossier.state = dossier_state.NEEDS_EVIDENCE
        await session.flush()

        blocker = await service.review_blocker(case)
        assert blocker is not None

        researcher = await _reviewer(session, "Researcher")
        case.state = "researching"
        await session.flush()
        with pytest.raises(ShariaGovernanceError) as refused:
            await service.mark_ready_for_review(
                case.id,
                admin_user_id=researcher.id,
                reason="Handing this to a reviewer.",
            )

    assert refused.value.code == blocker.code


async def test_a_case_waiting_for_a_reviewer_can_still_be_sent_back_for_evidence(
    test_context,
):
    """A blocked case needs an action, or it is stuck for ever.

    Research would not take back a case sitting in ``ready_for_review``, and approval
    refused it. There was no third button.
    """

    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        researcher = await _reviewer(session, "Researcher")
        refreshed = await ShariaGovernanceService(
            session, test_context["settings"]
        ).start_research(
            case.id,
            admin_user_id=researcher.id,
            reason="The official source page has to be fetched again.",
        )
        await session.commit()

    assert refreshed.state == "researching"


# --------------------------------------------------------------------------------
# Every refusal a reviewer can meet is explained in words they can act on.
# --------------------------------------------------------------------------------


def _every_governance_refusal_code() -> list[str]:
    """Every refusal the governance service can raise, read from its source.

    Read out of the file rather than from ``inspect.getsource`` on chosen methods: a
    hand-picked list of methods documents what somebody remembered, and line-based
    source lookup goes wrong the moment the file is edited while the tests run.
    """

    source = (
        SRC_ROOT / "services" / "sharia_governance.py"
    ).read_text("utf-8")
    return sorted(set(re.findall(r'ShariaGovernanceError\(\s*"([a-z0-9_]+)"', source)))


def test_every_refusal_a_reviewer_can_meet_has_plain_words_and_a_next_step():
    """A page of rule sentences reads as a page of errors.

    Every refusal the governance service raises reaches a person through the same
    banner, so every one of them has to say what happened and what to do about it. A new
    refusal added without an explanation fails here rather than reaching a reviewer as
    jargon.
    """

    unexplained = [
        code
        for code in _every_governance_refusal_code()
        if not explain(code, "fallback").next_step
    ]

    assert unexplained == []


def test_the_refusal_wording_is_not_a_copy_of_the_rule_it_reports():
    """Plain words, not the rule sentence with a full stop moved.

    Every explanation has to be two real parts: what happened, and the one thing to do.
    An entry whose next step just repeats the first half helps nobody.
    """

    for code in _every_governance_refusal_code():
        explanation = explain(code, "fallback")
        assert explanation.what_happened
        assert explanation.next_step
        assert explanation.next_step != explanation.what_happened


def test_a_long_list_of_refusals_still_fits_in_a_message_a_browser_will_take():
    """A report of a problem must never become a worse problem.

    The outcome travels back in the address bar. Naming every one of a few hundred
    refused cases builds an address longer than servers accept, and the reviewer gets a
    broken page instead of being told what happened.

    The summary groups by **reason**, because four hundred cases stuck on the same
    missing evidence are one problem, not four hundred.
    """

    from ai_market_monitor.api.routers.system_brain import (
        CASES_NAMED_PER_REASON,
        _refused_summary,
    )

    many = [
        CaseOutcome(
            case_id=uuid4(),
            reference=f"SB-{index:04d}",
            applied=False,
            message="The research folder for this asset is not finished. Open it and run research.",
        )
        for index in range(400)
    ]

    summary = _refused_summary(many)

    assert summary.count("SB-") == CASES_NAMED_PER_REASON
    assert "400 case(s)" in summary
    assert f"and {400 - CASES_NAMED_PER_REASON} more" in summary
    assert len(summary) < 1000
    # Nothing refused means nothing to say about refusals.
    assert _refused_summary([]) == ""


def test_cases_sent_for_research_are_not_reported_as_waiting_for_the_reviewer():
    """They are handled. Listing them as failures is what made a good batch read badly."""

    from ai_market_monitor.api.routers.system_brain import _refused_summary

    handled = [
        CaseOutcome(
            case_id=uuid4(),
            reference=f"SB-{index:04d}",
            applied=False,
            message="This case is missing part of its evidence. It was sent for research.",
            sent_for_research=True,
        )
        for index in range(50)
    ]

    assert _refused_summary(handled) == ""


def test_an_unmapped_refusal_keeps_the_original_sentence_rather_than_inventing_one():
    """Never guess at a rule nobody wrote words for — the reviewer would act on it."""

    explanation = explain("a_code_that_does_not_exist", "The original sentence.")

    assert explanation.what_happened == "The original sentence."
    assert explanation.next_step == ""
    assert explanation.sentence() == "The original sentence."


def test_a_raised_refusal_is_explained_by_its_code():
    explanation = explain_error(
        ShariaGovernanceError(
            "dossier_not_complete", "The factual research dossier is not complete."
        )
    )

    assert "research folder" in explanation.what_happened
    assert explanation.next_step
