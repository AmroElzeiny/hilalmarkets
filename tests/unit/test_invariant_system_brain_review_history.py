"""System Brain can answer what was reviewed and what the automated coin reading did.

The assistant was given the review queue and one case at a time. It could not list a
case somebody had already closed, could not list the coins the automated reading went
through, and could not say which of a coin's pages were read and why the others were not.
These tests hold the three readers to that, and hold the tool offer to the words the
owner actually writes — in Egyptian Arabic as well as English.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from ai_market_monitor.db.models import (
    AutomatedScreenRun,
    CoinEvidenceDocument,
    ReviewCase,
    ReviewDecision,
    SystemBrainConversation,
    User,
)
from ai_market_monitor.db.models.enums import ReviewCaseType, UserRole
from ai_market_monitor.schemas.system_brain import SystemBrainToolArguments, json_safe
from ai_market_monitor.services.sharia_research import fetch_failure_in_plain_words
from ai_market_monitor.services.system_brain_agent import SystemBrainAgentPolicy
from ai_market_monitor.services.system_brain_tools import (
    GOVERNANCE_OPERATIONS_TOOLS,
    SystemBrainToolRegistry,
    _fit_coin_reading,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
NEW_TOOLS = ("list_review_cases", "list_coin_screenings", "inspect_coin_screening")


async def _seed(session):
    admin = User(display_name="Owner", role=UserRole.ADMIN)
    session.add(admin)
    await session.flush()
    conversation = SystemBrainConversation(admin_user_id=admin.id, title="History")
    session.add(conversation)
    finished = ReviewCase(
        case_reference="NEW-ABC-1",
        case_type=ReviewCaseType.AUTOMATED_COIN_REVIEW,
        state="published",
        publication_state="published",
        title="New coin ABC",
        priority="normal",
        risk_severity="low",
        human_review_reason="Automated reading finished.",
        idempotency_key="automated-coin:ABC",
        done_at=NOW,
    )
    still_open = ReviewCase(
        case_reference="NEW-XYZ-1",
        case_type=ReviewCaseType.AUTOMATED_COIN_REVIEW,
        state="ready_for_review",
        publication_state="unpublished",
        title="New coin XYZ",
        priority="normal",
        risk_severity="low",
        human_review_reason="Automated reading finished.",
        idempotency_key="automated-coin:XYZ",
    )
    session.add_all([finished, still_open])
    await session.flush()
    session.add(
        ReviewDecision(
            review_case_id=finished.id,
            admin_user_id=admin.id,
            decision="approve",
            reason="Own whitepaper describes a plain payment token.",
            evidence_snapshot_ids=[],
            decision_version=1,
            created_at=NOW,
        )
    )
    run = AutomatedScreenRun(
        symbol="ABC",
        asset_name="Abc Coin",
        verdict="eligible",
        hold_state="for_review",
        ai_review_state="completed",
        ai_review_attempts=1,
        documents_read=1,
        primary_documents_read=1,
        decided_at=NOW - timedelta(days=1),
        review_report={"summary": "A payment token with no lending."},
        review_case_id=finished.id,
    )
    other = AutomatedScreenRun(
        symbol="XYZ",
        asset_name="Xyz",
        verdict="not_enough_data",
        hold_state="not_enough_data",
        ai_review_state="skipped_no_pages",
        decided_at=NOW,
        review_case_id=still_open.id,
    )
    session.add_all([run, other])
    await session.flush()
    session.add_all(
        [
            CoinEvidenceDocument(
                symbol="ABC",
                run_id=run.id,
                url="https://abc.example/whitepaper",
                title="Whitepaper",
                category="whitepaper",
                characters=5000,
                is_primary=True,
                fetched_at=NOW,
            ),
            CoinEvidenceDocument(
                symbol="ABC",
                run_id=run.id,
                url="https://abc.example/blog",
                category="blog",
                failure_code="http_404",
            ),
        ]
    )
    await session.flush()
    return admin, conversation


async def _call(session, settings, admin, conversation, tool, **arguments):
    return await SystemBrainToolRegistry(settings).execute(
        session,
        admin_user_id=admin.id,
        conversation_id=conversation.id,
        tool_name=tool,
        arguments=SystemBrainToolArguments(**arguments),
        request_id="history",
    )


def test_the_new_readers_are_governance_tools_with_their_own_descriptions(test_context):
    registry = SystemBrainToolRegistry(test_context["settings"])
    offered = {item["name"]: item for item in registry.openai_tools()}
    for tool in NEW_TOOLS:
        assert tool in GOVERNANCE_OPERATIONS_TOOLS
        assert "bounded authoritative" not in offered[tool]["description"]


async def test_finished_cases_are_listed_with_the_human_decision(test_context):
    async with test_context["session_factory"]() as session:
        admin, conversation = await _seed(session)
        settings = test_context["settings"]
        finished = await _call(
            session, settings, admin, conversation, "list_review_cases", lifecycle="finished"
        )
        assert [row["reference"] for row in finished.data] == ["NEW-ABC-1"]
        assert finished.data[0]["latest_decision"]["decision"] == "approve"
        still_open = await _call(
            session, settings, admin, conversation, "list_review_cases", lifecycle="open"
        )
        assert [row["reference"] for row in still_open.data] == ["NEW-XYZ-1"]
        by_coin = await _call(
            session, settings, admin, conversation, "list_review_cases", query="abc"
        )
        assert [row["reference"] for row in by_coin.data] == ["NEW-ABC-1"]


async def test_coin_readings_are_listed_as_machine_proposals(test_context):
    async with test_context["session_factory"]() as session:
        admin, conversation = await _seed(session)
        result = await _call(
            session, test_context["settings"], admin, conversation, "list_coin_screenings"
        )
        assert "never a published Shariah status" in result.data["note"]
        rows = {row["symbol"]: row for row in result.data["coins"]}
        assert rows["ABC"]["review_case"] == "NEW-ABC-1"
        assert rows["ABC"]["own_pages_read"] == 1
        assert result.data["all_coins_by_machine_verdict"] == {
            "eligible": 1,
            "not_enough_data": 1,
        }
        held = await _call(
            session,
            test_context["settings"],
            admin,
            conversation,
            "list_coin_screenings",
            lifecycle="skipped_no_pages",
        )
        assert [row["symbol"] for row in held.data["coins"]] == ["XYZ"]


@pytest.mark.parametrize("wanted", ["ABC", "abc", "NEW-ABC-1"])
async def test_one_coin_shows_every_page_it_tried_and_why_some_were_not_read(
    test_context, wanted
):
    async with test_context["session_factory"]() as session:
        admin, conversation = await _seed(session)
        result = await _call(
            session,
            test_context["settings"],
            admin,
            conversation,
            "inspect_coin_screening",
            target_id=wanted,
        )
        pages = {page["url"]: page for page in result.data["pages"]}
        assert pages["https://abc.example/whitepaper"]["read"] is True
        assert pages["https://abc.example/blog"]["read"] is False
        assert pages["https://abc.example/blog"]["why_not_read"] == (
            fetch_failure_in_plain_words("http_404")
        )
        assert result.data["ai_report"] == {"summary": "A payment token with no lending."}
        assert result.data["review_case"]["decisions"][0]["decision"] == "approve"
        assert "never a published Shariah status" in result.data["note"]


async def test_an_unknown_coin_is_reported_missing_not_invented(test_context):
    async with test_context["session_factory"]() as session:
        admin, conversation = await _seed(session)
        result = await _call(
            session,
            test_context["settings"],
            admin,
            conversation,
            "inspect_coin_screening",
            target_id="NOPE",
        )
        assert result.data is None
        assert result.coverage == "missing"


def test_a_large_reading_is_trimmed_to_fit_never_dropped():
    pages = [
        {"url": f"https://x.example/{index}", "read": index % 2 == 0, "title": "t" * 400}
        for index in range(60)
    ]
    data = {
        "pages": pages,
        "ai_report": {f"part{index}": "r" * 3_000 for index in range(10)},
    }
    limitations = _fit_coin_reading(data, 24_000)

    # Measured as it is sent: the envelope passes `data` through `json_safe` first.
    assert len(json.dumps(json_safe(data))) <= 24_000
    assert limitations
    # Pages that were not read go first: while any of them is still shown, every page
    # that was read is shown too.
    kept_read = sum(1 for page in data["pages"] if page["read"])
    if any(not page["read"] for page in data["pages"]):
        assert kept_read == 30


@pytest.mark.parametrize(
    "question",
    [
        "which cases were reviewed last week?",
        "what did the AI do with the pages of this coin?",
        "show me the verdict for ABC",
        "ايه اللي اتراجع من الحالات؟",
        "الذكاء عمل ايه في صفحات العملة دي؟",
        "عملة ABC اتقرأت ازاي؟",
    ],
)
def test_review_history_questions_are_offered_the_new_readers(test_context, question):
    offered = SystemBrainAgentPolicy(test_context["settings"]).offered_tools(question)
    for tool in NEW_TOOLS:
        assert tool in offered, (question, tool)


def test_a_question_touching_many_groups_still_gets_every_review_reader(test_context):
    # Customer and quality words came first and filled the cap before governance was
    # reached. The group the question is most about now goes first.
    question = (
        "for which coin review cases did the decision upset a customer, "
        "and what was the quality and cost?"
    )
    offered = SystemBrainAgentPolicy(test_context["settings"]).offered_tools(question)
    for tool in NEW_TOOLS:
        assert tool in offered
