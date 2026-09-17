"""Hilal evidence: inputs, account records, passports, origins and size.

Covers R16 (a card with one filled and one empty input reaches the payload with
both), R17 (one person's turn never carries another person's rows), R18 (one
Passport row per coin per standard, for every status and both methodology kinds),
R19 (every active methodology carries its origin fields) and R20 (payloads stay
under the size cap and carry no links, no aggregate winner and no default).

New file. No existing test is touched.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    ShariaEvidenceSource,
    ShariaMethodology,
    Strategy,
    User,
)
from ai_market_monitor.db.models.enums import (
    ShariaAssetStatus,
    ShariaMethodologyStatus,
    StrategyStatus,
    UserRole,
)
from ai_market_monitor.schemas.hilal_chat import HilalChatBoard, HilalChatBoardCard, HilalChatView
from ai_market_monitor.services.hilal_chat_knowledge import HilalChatKnowledge
from ai_market_monitor.services.hilal_methodology import (
    METHODOLOGY_PUBLIC_PATH,
    METHODOLOGY_SYSTEM_CODE,
)
from ai_market_monitor.services.sharia_screening import STATUS_LABELS
from tests.factories import methodology_evidence_requirements, methodology_rules

#: The real automated-standard code, so the row must carry the automated notice.
AUTOMATED_CODE = METHODOLOGY_SYSTEM_CODE


async def _user(session, *, name: str = "Hilal Evidence") -> User:
    user = User(display_name=name, role=UserRole.USER)
    session.add(user)
    await session.flush()
    return user


async def _methodology(
    session, *, code: str, name: str, automated: bool = False
) -> ShariaMethodology:
    now = datetime.now(UTC)
    row = ShariaMethodology(
        code=code if not automated else AUTOMATED_CODE,
        name=name,
        version="2026.08-test.1",
        description="A test screening standard with a stored origin.",
        status=ShariaMethodologyStatus.ACTIVE,
        governing_body="Test Governance Council" if not automated else "Hilal Markets",
        reviewer_group=(
            "No Shariah advisor — automated screen, under development"
            if automated
            else "Qualified test reviewers"
        ),
        published_at=now - timedelta(days=2),
        effective_from=now - timedelta(days=2),
        rules_json=methodology_rules(source_family="evidence_test"),
        evidence_requirements_json=methodology_evidence_requirements(),
    )
    session.add(row)
    await session.flush()
    return row


async def _assess(
    session,
    *,
    symbol: str,
    methodology: ShariaMethodology,
    status: ShariaAssetStatus,
    admission: str | None = None,
) -> None:
    now = datetime.now(UTC)
    qualifications = (
        ["A test condition applies."]
        if status is ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS
        else []
    )
    exclusions = (
        [{"reason": "The project's own pages describe lending with interest."}]
        if status is ShariaAssetStatus.EXCLUDED
        else []
    )
    assessment = AssetShariaAssessment(
        canonical_asset=symbol,
        asset_name=f"Test {symbol}",
        methodology_id=methodology.id,
        status=status,
        summary=f"A qualified test review recorded {symbol} as {status.value}.",
        qualifications=qualifications,
        exclusion_reasons=exclusions,
        evidence_snapshot={"admission": admission} if admission else {},
        reviewed_by="Qualified test reviewer",
        reviewed_at=now - timedelta(days=1),
        valid_from=now - timedelta(days=1),
    )
    session.add(assessment)
    await session.flush()
    session.add(
        ShariaEvidenceSource(
            assessment_id=assessment.id,
            source_type="project_page",
            title=f"{symbol} project pages",
            publisher=f"Test {symbol} project",
            source_url=f"https://example.test/{symbol.lower()}",
            retrieved_at=now - timedelta(days=1),
            evidence_category="project_own_pages",
            evidence_summary=f"Pages read while screening {symbol}.",
            source_hash=uuid4().hex,
        )
    )
    await session.commit()


async def _strategy(
    session, *, user: User, name: str, status: StrategyStatus
) -> Strategy:
    row = Strategy(user_id=user.id, name=name, status=status)
    session.add(row)
    await session.flush()
    await session.commit()
    return row


# --------------------------------------------------------------------------------
# R16: one filled and one empty input reach the payload with both.
# --------------------------------------------------------------------------------


async def test_card_inputs_reach_the_payload_filled_and_empty(test_context):
    view = HilalChatView(
        page="monitor_canvas",
        board=HilalChatBoard(
            sentence="Watch BTC.",
            cards=[
                HilalChatBoardCard(
                    label="Price moves",
                    needs=["Threshold"],
                    inputs=[
                        {"label": "Direction", "filled": True, "value": "up at least"},
                        {"label": "Threshold", "filled": False, "value": None},
                    ],
                )
            ],
        ),
    )
    async with test_context["session_factory"]() as session:
        evidence = await HilalChatKnowledge(session, get_settings()).gather(
            message="what is my board still missing", view=view
        )
    cards = evidence.to_payload()["what_they_can_see"]["the_monitor_they_are_drawing"][
        "cards_on_the_board"
    ]
    assert cards[0]["fields_the_person_filled_in"] == [
        {"field": "Direction", "filled": True, "value": "up at least"},
        {"field": "Threshold", "filled": False, "value": None},
    ]


# --------------------------------------------------------------------------------
# R17: cross-user isolation. A blocker if it ever fails.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["running", "paused", "draft"])
async def test_account_records_carry_state_words(test_context, state: str):
    status = {
        "running": StrategyStatus.ACTIVE,
        "paused": StrategyStatus.PAUSED,
        "draft": StrategyStatus.DRAFT,
    }[state]
    async with test_context["session_factory"]() as session:
        user = await _user(session)
        await _strategy(session, user=user, name="My first watch", status=status)
        evidence = await HilalChatKnowledge(session, get_settings()).gather(
            message="what are my monitors", view=None, user_id=user.id
        )
    monitors = evidence.to_payload()["their_own_account"]["monitors"]
    assert [(item["name"], item["state"]) for item in monitors] == [
        ("My first watch", state)
    ]


async def test_one_persons_turn_never_carries_another_persons_rows(test_context):
    async with test_context["session_factory"]() as session:
        first = await _user(session, name="First Person")
        second = await _user(session, name="Second Person")
        await _strategy(
            session, user=first, name="First persons secret watch", status=StrategyStatus.ACTIVE
        )
        knowledge = HilalChatKnowledge(session, get_settings())
        for_first = await knowledge.gather(
            message="what are my monitors", view=None, user_id=first.id
        )
        for_second = await knowledge.gather(
            message="what are my monitors", view=None, user_id=second.id
        )
        anonymous = await knowledge.gather(message="what are my monitors", view=None)
    assert "First persons secret watch" in json.dumps(for_first.to_payload(), default=str)
    assert "First persons secret watch" not in json.dumps(for_second.to_payload(), default=str)
    assert for_second.to_payload()["their_own_account"]["monitors"] == []
    assert anonymous.to_payload()["their_own_account"] == {}
    assert "account:monitors" in for_first.ids
    assert "account:monitors" not in anonymous.ids


async def test_account_records_name_the_plan_and_channels(test_context):
    async with test_context["session_factory"]() as session:
        user = await _user(session)
        evidence = await HilalChatKnowledge(session, get_settings()).gather(
            message="what does my plan cover", view=None, user_id=user.id
        )
    account = evidence.to_payload()["their_own_account"]
    assert account["plan"]["name"]
    assert "web" in account["alert_channels"]["chosen"]
    assert "web" in account["alert_channels"]["connected"]
    assert {"account:plan", "account:channels"} <= evidence.ids


# --------------------------------------------------------------------------------
# R18: one Passport row per coin per standard, every status, both kinds.
# --------------------------------------------------------------------------------


STATUSES = [
    ShariaAssetStatus.ELIGIBLE,
    ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS,
    ShariaAssetStatus.DISPUTED,
    ShariaAssetStatus.UNDER_REVIEW,
    ShariaAssetStatus.EXCLUDED,
    ShariaAssetStatus.INSUFFICIENT_INFORMATION,
]


@pytest.mark.parametrize("status", STATUSES, ids=lambda item: item.value)
@pytest.mark.parametrize("kind", ["automated", "authority"])
async def test_a_passport_row_names_its_standard_and_its_evidence(
    test_context, status: ShariaAssetStatus, kind: str
):
    automated = kind == "automated"
    async with test_context["session_factory"]() as session:
        methodology = await _methodology(
            session,
            code="TEST_AUTH_EVIDENCE",
            name="Evidence test authority",
            automated=automated,
        )
        # The real automated code, so the row must carry the automated notice.
        await _assess(
            session,
            symbol="BTC",
            methodology=methodology,
            status=status,
            admission="automated_screen" if automated else None,
        )
        evidence = await HilalChatKnowledge(session, get_settings()).gather(
            message="is BTC eligible", view=None
        )
    assert len(evidence.passports) == 1
    row = evidence.passports[0]
    assert row["id"] == f"passport:BTC:{methodology.code}"
    assert row["id"] in evidence.ids
    assert row["symbol"] == "BTC"
    assert row["methodology_name"] == methodology.name
    assert row["status_words"] == STATUS_LABELS[status]
    assert row["why"]
    assert row["reviewed_at"]
    if status is ShariaAssetStatus.EXCLUDED:
        assert row["exclusion_reasons"] == [
            "The project's own pages describe lending with interest."
        ]
    if status is ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS:
        assert row["qualifications"] == ["A test condition applies."]
    # Names and dates only. The model may not output links, so none travel.
    assert row["evidence"] != []
    for source in row["evidence"]:
        assert set(source) == {"name", "from", "retrieved"}
        assert source["name"]
        assert source["retrieved"]
    assert "http" not in json.dumps(row)
    if automated:
        assert row["admission_route"] == "automated_screen"
        assert row["automated_notice"]
        assert "machine" in row["automated_notice"].lower() or (
            "automated" in row["automated_notice"].lower()
        )
    else:
        assert row["admission_route"] == "reviewed_decision"
        assert row["automated_notice"] is None


async def test_the_coin_open_on_screen_gets_its_passport_rows(test_context):
    async with test_context["session_factory"]() as session:
        methodology = await _methodology(
            session, code="TEST_AUTH_SUBJECT", name="Subject test authority"
        )
        await _assess(
            session,
            symbol="BTC",
            methodology=methodology,
            status=ShariaAssetStatus.ELIGIBLE,
        )
        evidence = await HilalChatKnowledge(session, get_settings()).gather(
            message="why is this one listed",
            view=HilalChatView(page="passport", subject="BTC"),
        )
    assert [row["symbol"] for row in evidence.passports] == ["BTC"]


# --------------------------------------------------------------------------------
# R19: every active methodology carries its origin fields.
# --------------------------------------------------------------------------------


async def test_every_active_methodology_has_origin_fields(test_context):
    async with test_context["session_factory"]() as session:
        await _methodology(
            session, code="TEST_AUTH_ORIGIN", name="Origin test authority"
        )
        await _methodology(
            session, code="AUTOMATED_CODE", name="Origin test automated", automated=True
        )
        evidence = await HilalChatKnowledge(session, get_settings()).gather(
            message="which screening standard is being used", view=None
        )
    rows = {
        item["name"]: item
        for item in evidence.methodologies
        if item["name"].startswith("Origin test")
    }
    assert len(rows) == 2
    for name, row in rows.items():
        for field in (
            "governing_body",
            "decided_by",
            "source_document",
            "screens",
            "in_force_from",
        ):
            assert field in row, f"{name} is missing its origin field {field!r}"
        assert row["governing_body"]
        assert row["decided_by"]
        assert row["screens"]
        assert row["in_force_from"]
    assert rows["Origin test automated"]["source_document"] == METHODOLOGY_PUBLIC_PATH
    assert rows["Origin test authority"]["source_document"] is None


# --------------------------------------------------------------------------------
# R20: bounded size, no links, no aggregate winner, no default.
# --------------------------------------------------------------------------------


async def test_a_three_coin_question_stays_under_the_size_cap(test_context):
    async with test_context["session_factory"]() as session:
        first = await _methodology(session, code="TEST_AUTH_A", name="Size test A")
        second = await _methodology(
            session, code="AUTOMATED_SIZE", name="Size test automated", automated=True
        )
        for symbol in ("BTC", "ETH", "SOL"):
            await _assess(
                session, symbol=symbol, methodology=first, status=ShariaAssetStatus.ELIGIBLE
            )
            await _assess(
                session,
                symbol=symbol,
                methodology=second,
                status=ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS,
                admission="automated_screen",
            )
        settings = get_settings()
        evidence = await HilalChatKnowledge(session, settings).gather(
            message="why is BTC listed and ETH and SOL under which standard",
            view=None,
        )
    payload = evidence.to_payload()
    assert len(json.dumps(payload, default=str)) <= settings.hilal_chat_evidence_max_chars
    assert len(evidence.passports) == 6


async def test_the_payload_names_no_links_no_winner_no_default(test_context):
    async with test_context["session_factory"]() as session:
        methodology = await _methodology(session, code="TEST_AUTH_CLEAN", name="Clean test")
        await _assess(
            session,
            symbol="BTC",
            methodology=methodology,
            status=ShariaAssetStatus.ELIGIBLE,
        )
        evidence = await HilalChatKnowledge(session, get_settings()).gather(
            message="is BTC eligible", view=None
        )
    dump = json.dumps(evidence.to_payload(), default=str).lower()
    assert "http" not in dump
    assert "winner" not in dump
    assert "aggregate" not in dump
    assert "all_approved_methodologies" not in dump
