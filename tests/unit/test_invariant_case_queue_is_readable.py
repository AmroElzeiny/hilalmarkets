"""A queue a person can read, and an assistant that can see it.

Two hundred rows of free text under a column called "Why" is not a queue. The sentences
come from five different producers, they answer five different questions, and none of them
says which rows are the *same* problem — so a reviewer reads two hundred of them and still
does not know what to do first.

What this pins down:

* every case carries a **tag**, chosen by one owner from facts the case already holds, so
  rows of the same kind read the same and can be counted and filtered;
* no tag ever says or implies a Shariah status, and no tag is ever derived by reading text
  for suspicious words. The one that routes a case to a reviewer first says *the pipeline
  marked this* — it is a routing signal, never an answer;
* the reason next to the tag is about **this coin**, naming the asset and the exact thing
  that is missing;
* the source-gap reason never asks a person to type an address the machine is supposed to
  find. It names what was tried, and what is stopping the machine going further;
* the assistant in the corner is told which page the reader is on and which cases are on
  it, and can open a case by the reference a person can actually see.

Parametrised across the whole family rather than the one case that was reported: a fix
that only helps the example must fail these.
"""

from __future__ import annotations

import re
from uuid import uuid4

import pytest

from ai_market_monitor.db.models.enums import ReviewCaseType
from ai_market_monitor.schemas.system_brain import (
    SystemBrainAgentTurnRequest,
    SystemBrainPageContext,
)
from ai_market_monitor.services.sharia_case_tags import (
    ACTIVITY_TO_CHECK,
    DECIDED,
    EMPTY_COVERAGE,
    FACTS_MISSING,
    IDENTITY_UNCLEAR,
    READER_REPORT,
    READY,
    SOURCES_MISSING,
    TAG_DEFINITIONS,
    WORKING_ON_IT,
    SourceCoverage,
    classify,
    coverage_from_rows,
)
from ai_market_monitor.services.system_brain_agent import SystemBrainAgentPolicy

#: Every word that would make a tag or a reason read as a religious verdict. None of them
#: may appear anywhere in what this module produces.
FORBIDDEN_WORDS = (
    "halal",
    "haram",
    "haraam",
    "forbidden",
    "permissible",
    "impermissible",
    "compliant",
    "non-compliant",
    "eligible",
    "sharia ruling",
    "shariah ruling",
)


def _signal(**overrides):
    base = {
        "case_type": ReviewCaseType.INITIAL_ASSET_REVIEW,
        "state": "ready_for_review",
        "risk_severity": "none",
        "asset_name": "VeChain",
        "done": False,
        "dossier_present": True,
        "evidence_completeness": 1.0,
        "missing_information_count": 0,
        "contradiction_count": 0,
    }
    base.update(overrides)
    return classify(**base)


# --------------------------------------------------------------------------------
# The tag says the kind of problem, never a religious answer.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(TAG_DEFINITIONS))
def test_no_tag_ever_reads_as_a_shariah_answer(key):
    """The rule, for every tag in the table — not for the ones somebody remembered."""

    definition = TAG_DEFINITIONS[key]
    text = f"{definition.label} {definition.meaning}".casefold()
    for word in FORBIDDEN_WORDS:
        assert word not in text, f"{key} says {word!r}"


@pytest.mark.parametrize("key", sorted(TAG_DEFINITIONS))
def test_every_tag_has_a_short_label_and_a_plain_explanation(key):
    definition = TAG_DEFINITIONS[key]
    assert definition.key == key
    assert 0 < len(definition.label) <= 24, definition.label
    assert definition.meaning.endswith(".")
    assert definition.tone in {"attention", "watch", "waiting", "ready", "done"}


@pytest.mark.parametrize(
    ("case_type", "expected"),
    [
        (ReviewCaseType.OFFICIAL_SOURCE_GAP, SOURCES_MISSING),
        (ReviewCaseType.SOURCE_IDENTITY_CONFLICT, IDENTITY_UNCLEAR),
        (ReviewCaseType.USER_FACTUAL_REPORT, READER_REPORT),
        (ReviewCaseType.MATERIAL_SOURCE_CHANGE, ACTIVITY_TO_CHECK),
        (ReviewCaseType.INITIAL_ASSET_REVIEW, READY),
    ],
)
def test_every_kind_of_case_lands_on_a_tag_of_its_own(case_type, expected):
    """One tag per kind of job. A case type nobody mapped would fall to a default and
    read exactly like the case next to it, which is the failure being fixed."""

    assert _signal(case_type=case_type).tag == expected


@pytest.mark.parametrize("severity", ["high", "critical"])
def test_research_that_flagged_a_possible_effect_is_routed_to_be_read_first(severity):
    """The signal is the pipeline's own structured verdict, not a keyword search."""

    signal = _signal(risk_severity=severity)
    assert signal.tag == ACTIVITY_TO_CHECK
    assert "before you decide" in signal.reason
    for word in FORBIDDEN_WORDS:
        assert word not in signal.reason.casefold()


@pytest.mark.parametrize("severity", ["none", "low", "medium"])
def test_a_quiet_case_is_not_routed_as_something_to_read_first(severity):
    """Fails closed the other way too: over-flagging makes the flag worth nothing."""

    assert _signal(risk_severity=severity).tag != ACTIVITY_TO_CHECK


def test_a_case_with_no_research_folder_says_so_rather_than_reading_as_ready():
    signal = _signal(dossier_present=False, evidence_completeness=0.0)
    assert signal.tag == FACTS_MISSING
    assert "no research folder" in signal.reason


@pytest.mark.parametrize(
    ("completeness", "missing", "percent"),
    [(0.0, 3, "0%"), (0.5, 2, "50%"), (0.82, 1, "82%")],
)
def test_the_reason_names_the_exact_amount_that_is_missing(completeness, missing, percent):
    """Specific, about this coin. "Some evidence is missing" is not something to act on."""

    signal = _signal(evidence_completeness=completeness, missing_information_count=missing)
    assert signal.tag == FACTS_MISSING
    assert "VeChain" in signal.reason
    assert percent in signal.reason
    assert str(missing) in signal.reason


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("researching", WORKING_ON_IT),
        ("safety_hold", "on_hold"),
        ("published", DECIDED),
        ("rejected", DECIDED),
    ],
)
def test_the_state_a_case_is_in_decides_its_tag_before_anything_else(state, expected):
    assert _signal(state=state).tag == expected


def test_a_finished_case_is_finished_whatever_else_is_true_of_it():
    """`done_at` beats every other signal. A decided case is not a job."""

    assert _signal(done=True, risk_severity="critical").tag == DECIDED


# --------------------------------------------------------------------------------
# The source-gap reason never asks a person to do the machine's job.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "coverage",
    [
        EMPTY_COVERAGE,
        SourceCoverage(proved={}, tried=7),
        SourceCoverage(proved={"official_news": 1}, tried=3),
    ],
)
def test_a_missing_page_says_the_system_keeps_looking(coverage):
    """Whatever has been tried, the answer is never "go and type the address".

    ``sharia_source_resolution`` walks its layers again on every sweep. Asking a reviewer
    to supply an address for two hundred coins is asking a person to do the machine's job,
    and it is the sentence that started this work.
    """

    signal = classify(
        case_type=ReviewCaseType.OFFICIAL_SOURCE_GAP,
        state="needs_evidence",
        risk_severity="low",
        asset_name="VeChain",
        done=False,
        dossier_present=False,
        evidence_completeness=0.0,
        missing_information_count=0,
        contradiction_count=0,
        coverage=coverage,
    )

    assert signal.tag == SOURCES_MISSING
    assert "VeChain" in signal.reason
    assert "keeps looking" in signal.reason
    assert "add the correct address" not in signal.reason


def test_the_number_of_addresses_already_tried_is_named():
    """"Nothing worked" and "eleven things were tried" are different facts."""

    signal = classify(
        case_type=ReviewCaseType.OFFICIAL_SOURCE_GAP,
        state="needs_evidence",
        risk_severity="low",
        asset_name="Sonic",
        done=False,
        dossier_present=False,
        evidence_completeness=0.0,
        missing_information_count=0,
        contradiction_count=0,
        coverage=SourceCoverage(proved={}, tried=11, failed=11),
    )

    assert "11 address(es)" in signal.reason


def _gap_reason(coverage: SourceCoverage) -> str:
    return classify(
        case_type=ReviewCaseType.OFFICIAL_SOURCE_GAP,
        state="needs_evidence",
        risk_severity="low",
        asset_name="HTX DAO",
        done=False,
        dossier_present=False,
        evidence_completeness=0.0,
        missing_information_count=0,
        contradiction_count=0,
        coverage=coverage,
    ).reason


def test_none_worked_is_only_said_when_none_worked():
    """The sentence a reviewer stopped believing.

    "10 address(es) have been tried and none worked yet" was built from the count of
    **every stored address**, working ones included. A coin holding two live community
    pages and eight dead news guesses was told it had ten failures — about pages the
    reviewer could open in a browser. Asserted across the whole family of shapes, not the
    one coin that was reported.
    """

    # Every address failed: the old sentence, and here it is true.
    assert "none worked yet" in _gap_reason(
        SourceCoverage(proved={}, tried=8, failed=8)
    )
    # Some work. It must not claim they did not.
    mixed = _gap_reason(
        SourceCoverage(proved={"official_community": 2}, tried=10, failed=8)
    )
    assert "none worked" not in mixed
    assert "8 address(es)" in mixed
    assert "2 that do work" in mixed
    # Everything stored works, but none of it is the required page.
    only_working = _gap_reason(
        SourceCoverage(proved={"official_community": 3}, tried=3, failed=0)
    )
    assert "none worked" not in only_working
    assert "3 working address(es)" in only_working
    # Nothing tried at all must not claim a failure.
    nothing = _gap_reason(EMPTY_COVERAGE)
    assert "none worked" not in nothing
    assert "no address has been tried yet" in nothing


@pytest.mark.parametrize(
    "coverage",
    [
        EMPTY_COVERAGE,
        SourceCoverage(proved={}, tried=8, failed=8),
        SourceCoverage(proved={"official_community": 2}, tried=10, failed=8),
        SourceCoverage(proved={"official_community": 3}, tried=3, failed=0),
        SourceCoverage(proved={}, tried=1, failed=1),
    ],
)
def test_the_gap_sentence_never_counts_more_failures_than_addresses(coverage):
    """No shape of coverage may produce a number larger than the addresses that exist."""

    reason = _gap_reason(coverage)
    for number in re.findall(r"(\d+) address\(es\)", reason):
        assert int(number) <= coverage.tried
    assert coverage.working == max(coverage.tried - coverage.failed, 0)


def test_failed_addresses_are_counted_apart_from_working_ones():
    """The count behind the sentence, taken straight from the rows the page selects."""

    asset = uuid4()
    coverage = coverage_from_rows(
        [
            (asset, "official_news", "verified", True),
            (asset, "official_news", "unreachable", True),
            (asset, "official_news", "not_permitted", True),
            (asset, "official_community", "verified", False),
        ]
    )[asset]

    assert coverage.tried == 4
    # Two never worked, and the switched-off one does not work now either.
    assert coverage.failed == 3
    assert coverage.working == 1


def test_coverage_is_counted_from_plain_rows_without_loading_the_table():
    """Four columns, one query. Loading whole rows to count them is what made another
    list view unservable."""

    asset = uuid4()
    other = uuid4()
    coverage = coverage_from_rows(
        [
            (asset, "official_news", "verified", True),
            (asset, "official_news", "unreachable", True),
            (asset, "official_community", "verified", False),
            (asset, "official_website", "verified", True),
            (other, "official_news", "verified", True),
        ]
    )

    assert coverage[asset].tried == 4
    # A verified-but-switched-off row is not coverage, and the website is not one of the
    # categories counted here at all.
    assert coverage[asset].proved == {"official_news": 1}
    # Nothing is missing: the news page works, and the community page is optional. This
    # asserted ("official_community",) until 1 September 2026, which is exactly the row
    # that filled the queue for every project that runs no forum.
    assert coverage[asset].missing_categories == ()
    assert coverage[other].proved == {"official_news": 1}


def test_a_coin_with_a_forum_and_no_news_page_is_still_a_gap():
    """The rule cuts one way only. Dropping the community requirement must not make a
    community page stand in for the missing news page — the news page is what a reviewer
    reads to learn the project changed, and a forum is not a substitute for it."""

    asset = uuid4()
    coverage = coverage_from_rows(
        [
            (asset, "official_community", "verified", True),
            (asset, "official_community", "verified", True),
            (asset, "official_news", "unreachable", True),
        ]
    )

    assert coverage[asset].proved == {"official_community": 2}
    assert coverage[asset].missing_categories == ("official_news",)


# --------------------------------------------------------------------------------
# The assistant can see the page, and can open a case by its visible name.
# --------------------------------------------------------------------------------


def test_the_page_a_reader_is_on_can_reach_the_assistant_at_all():
    """A typed field, not a blob of the page. An unbounded copy of the screen would send
    customer text to a provider on every turn."""

    request = SystemBrainAgentTurnRequest(
        message="ليه الحالة دي واقفة؟",
        client_message_id="page-context-0001",
        page_context=SystemBrainPageContext(
            path="/dashboard/system-brain/cases",
            section="cases",
            heading="Cases",
            case_references=["IMP-FASSET-170-HTX-EBD42F8B44"],
        ),
    )

    assert request.page_context is not None
    assert request.page_context.case_references == ["IMP-FASSET-170-HTX-EBD42F8B44"]
    # A turn without one still works: the assistant existed before the window did.
    assert SystemBrainAgentTurnRequest(
        message="hello", client_message_id="no-context-0001"
    ).page_context is None


@pytest.mark.parametrize(
    "reference",
    [
        "IMP-FASSET-170-HTX-EBD42F8B44",
        "IMP-FASSET-107-WORMHOLE-ADF42CF42B",
        "SRC-4F8E3D679A12",
        "SC-BTC-TEST",
    ],
)
def test_a_case_reference_on_the_page_offers_the_tools_that_can_read_it(
    test_context, reference
):
    """The question a person asks most — "what does this one want?" — is a governance
    question even when the sentence contains no governance word at all.

    Before this the assistant was offered a default tool set with no way to open a case,
    and answered from nothing.
    """

    policy = SystemBrainAgentPolicy(test_context["settings"])
    page = f'{{"path": "/dashboard/system-brain/cases", "case_references": ["{reference}"]}}'

    offered = policy.offered_tools("ايه اللي ناقص هنا؟", page=page)

    assert "inspect_review_case" in offered
    assert "review_queue_summary" in offered


def test_asking_about_a_case_by_name_offers_the_case_tools_without_any_page():
    """Typed into the box on any page, with no context at all."""

    from ai_market_monitor.core.config import Settings

    policy = SystemBrainAgentPolicy(Settings(_env_file=None))
    offered = policy.offered_tools("what is wrong with IMP-FASSET-078-SONIC-0DA2BF2DF4")

    assert "inspect_review_case" in offered


def test_the_assistant_is_told_to_read_the_case_rather_than_answer_from_the_page():
    """The context says *which* case. Only the tool says what is in it."""

    from ai_market_monitor.services.system_brain_agent import _instructions

    text = _instructions()
    assert "inspect_review_case" in text
    assert "never answer a question about a specific case from the page context alone" in (
        text.casefold()
    )


def test_the_assistant_answers_in_egyptian_arabic_and_can_be_asked_to_switch():
    from ai_market_monitor.services.system_brain_agent import _instructions

    text = _instructions()
    assert "Egyptian Arabic" in text
    assert "Switch to English" in text
    # And it still may never decide anything, in any language.
    assert "deciding it is never yours, in any language" in text


async def test_the_assistant_can_open_a_case_by_the_name_a_person_can_see(test_context):
    """The reference is the only name of a case anybody ever sees.

    It is on every row of the Cases page, in every refusal message and in the flash after
    a quick decision; the internal id is printed nowhere. Accepting only the id meant the
    question this whole window exists for could not be asked.
    """

    from ai_market_monitor.db.models import SystemBrainConversation, User
    from ai_market_monitor.db.models.enums import UserRole
    from ai_market_monitor.schemas.system_brain import SystemBrainToolArguments
    from ai_market_monitor.services.system_brain_tools import SystemBrainToolRegistry
    from tests.services.test_sc_malaysia_governance import _ready_case

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        admin = User(display_name="Amr", role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()
        conversation = SystemBrainConversation(admin_user_id=admin.id, title="Ask")
        session.add(conversation)
        await session.flush()

        registry = SystemBrainToolRegistry(settings)
        by_reference = await registry.execute(
            session,
            admin_user_id=admin.id,
            conversation_id=conversation.id,
            tool_name="inspect_review_case",
            arguments=SystemBrainToolArguments(target_id=case.case_reference),
            request_id="ask-by-reference",
        )
        by_id = await registry.execute(
            session,
            admin_user_id=admin.id,
            conversation_id=conversation.id,
            tool_name="inspect_review_case",
            arguments=SystemBrainToolArguments(target_id=str(case.id)),
            request_id="ask-by-id",
        )
        missing = await registry.execute(
            session,
            admin_user_id=admin.id,
            conversation_id=conversation.id,
            tool_name="inspect_review_case",
            arguments=SystemBrainToolArguments(target_id="IMP-NOT-A-REAL-CASE"),
            request_id="ask-for-nothing",
        )

    assert by_reference.evidence_refs == [f"db:review_cases:{case.id}"]
    assert by_reference.evidence_refs == by_id.evidence_refs
    # And the answer can actually be sent. The payload holds database rows, and an
    # envelope that cannot be serialised is an HTTP 500 for the whole turn rather than
    # one failed tool call.
    encoded = by_reference.model_dump_json()
    assert case.case_reference in encoded
    assert len(encoded) > 200
    # A reference that belongs to no case is said plainly, never guessed at.
    assert missing.data is None
    assert "IMP-NOT-A-REAL-CASE" in missing.limitations[0]


def test_any_tool_payload_at_all_can_be_sent():
    """The class, not the one tool that met it.

    Four producers build an evidence envelope without going through the shared helper, so
    the coercion lives on the envelope itself. Anything unrecognised becomes its text —
    never an exception, because a diagnostic must never become the failure.
    """

    from datetime import UTC, datetime
    from decimal import Decimal

    from ai_market_monitor.schemas.system_brain import EvidenceEnvelope

    class Unknown:
        def __repr__(self) -> str:
            return "<an object nobody taught pydantic about>"

    envelope = EvidenceEnvelope(
        data={
            "when": datetime(2026, 8, 25, tzinfo=UTC),
            "who": uuid4(),
            "how_much": Decimal("12.50"),
            "rows": [Unknown(), {"nested": Unknown()}],
        },
        evidence_refs=["db:x:1"],
        freshness="now",
        coverage="one",
    )

    encoded = envelope.model_dump_json()
    assert "2026-08-25" in encoded
    assert "12.50" in encoded
    assert "nobody taught pydantic" in encoded


def test_a_case_reference_is_recognised_but_never_parsed_into_an_answer():
    """The pattern only notices that a turn is about a case. The case itself is still
    looked up through the governed tool — a reference read out of a sentence is not
    evidence that the case exists."""

    from ai_market_monitor.services.system_brain_agent import _CASE_REFERENCE_RE

    assert _CASE_REFERENCE_RE.search("IMP-FASSET-170-HTX-EBD42F8B44")
    assert _CASE_REFERENCE_RE.search("please look at SRC-4F8E3D679A12 today")
    # Ordinary prose is not a case reference.
    assert not _CASE_REFERENCE_RE.search("why is this one stuck")
    assert not _CASE_REFERENCE_RE.search("the close-to-close move")
    assert isinstance(_CASE_REFERENCE_RE, re.Pattern)
