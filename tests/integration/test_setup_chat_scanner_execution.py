"""The three-turn Scanner conversation, all the way through governed execution.

``test_setup_chat_scanner_flow.py`` proves the agent hands over the right request. This
file proves the request is actually run: it drives the real chat service against a real
database, with only the market provider replaced, and checks that a durable scan run and
a scanner result message exist at the end.

    user  Scanner                             -> draft.mode becomes scanner
    user  what coins are up at least 5% now?  -> one question, values held
    user  24 hours                            -> OnDemandScanService runs the scan

Nothing is mocked inside the platform. Quota, screening resolution, idempotency, the
durable scan run and the audit record are the production ones.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import select

from ai_market_monitor.db.models import (
    AISetupChatMessage,
    AssetShariaAssessment,
    AssetShariaStatusHistory,
    OnDemandScanRun,
    ShariaEvidenceSource,
    ShariaMethodology,
    User,
)
from ai_market_monitor.db.models.enums import (
    ShariaAssetStatus,
    ShariaMethodologyStatus,
    ShariaUniverseMode,
)
from ai_market_monitor.engine.requirement_state import universe_mode_is_user_selected
from ai_market_monitor.schemas.strategy_draft_v2 import DraftMode
from ai_market_monitor.services.interfaces import Candle
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter
from ai_market_monitor.services.setup_chat_agent import SCAN_SCOPE_QUESTION
from ai_market_monitor.services.setup_chat_launch import load_strategy_draft_v2
from tests.factories import methodology_evidence_requirements, methodology_rules
from tests.integration.test_setup_chat_launch_v2 import (
    AISetupChatService,
    StandInPlanner,
    _agent,
)

#: What the provider reports as each coin's rolling 24-hour change. BTC clears 5%, ETH
#: does not, so a scan that invents results and a scan that runs cannot look alike.
PERCENTAGES: dict[str, float] = {"BTC/USDT": 7.25, "ETH/USDT": 1.5}


def _scanner_settings(base):
    """Screening on, so choosing Scanner resolves a real governed methodology.

    With screening off the mode change sets no methodology, the screened scope never
    becomes ready, and the scan stops at the scope gate before it can run — which is
    correct behaviour, and useless for proving execution.
    """

    return base.model_copy(
        update={
            "setup_chat_legacy_test_compat_enabled": False,
            "sharia_screening_enforced": True,
            "openai_api_key": SecretStr("unused-test-key"),
        }
    )


async def _seed_methodology(session) -> ShariaMethodology:
    """One active methodology, with BTC and ETH assessed eligible under it."""

    now = datetime.now(UTC)
    methodology = ShariaMethodology(
        code=f"SCANFLOW_{uuid4().hex[:12].upper()}",
        name="Scanner flow test methodology",
        version="1.0-test",
        description="Evidence-backed test methodology for the Scanner continuation flow.",
        status=ShariaMethodologyStatus.ACTIVE,
        governing_body="Qualified test governance",
        reviewer_group="Qualified test reviewers",
        published_at=now - timedelta(days=2),
        effective_from=now - timedelta(days=2),
        rules_json=methodology_rules(source_family="scanner_flow_test"),
        evidence_requirements_json=methodology_evidence_requirements(),
    )
    session.add(methodology)
    await session.flush()

    for asset, name in (("BTC", "Bitcoin"), ("ETH", "Ethereum")):
        assessment = AssetShariaAssessment(
            canonical_asset=asset,
            asset_name=name,
            methodology_id=methodology.id,
            status=ShariaAssetStatus.ELIGIBLE,
            summary="A qualified test reviewer recorded this conclusion from evidence.",
            qualifications=[],
            exclusion_reasons=[],
            evidence_snapshot={
                "reviewed_dimensions": [{"name": "Primary activity", "result": "reviewed"}],
                "methodology_result": {"passed": ["test rule"]},
            },
            reviewed_by="Qualified test reviewer",
            reviewed_at=now - timedelta(days=1),
            valid_from=now - timedelta(days=1),
        )
        session.add(assessment)
        await session.flush()
        session.add_all(
            [
                ShariaEvidenceSource(
                    assessment_id=assessment.id,
                    source_type="official_disclosure",
                    title=f"Official {asset} disclosure",
                    publisher="Project documentation",
                    source_url=f"https://example.com/{asset.casefold()}-evidence",
                    retrieved_at=now - timedelta(days=1),
                    evidence_category="primary_activity",
                    evidence_summary="Retained evidence used only for deterministic tests.",
                    source_hash=uuid4().hex + uuid4().hex,
                ),
                AssetShariaStatusHistory(
                    canonical_asset=asset,
                    methodology_id=methodology.id,
                    previous_status=None,
                    new_status=ShariaAssetStatus.ELIGIBLE,
                    reason_code="test_review",
                    reason_summary="Qualified test evidence review completed.",
                    assessment_id=assessment.id,
                    changed_at=assessment.valid_from,
                    approved_by="Qualified test approver",
                ),
            ]
        )
    await session.flush()
    return methodology


class ScannerMarketProvider:
    """A provider that answers the rolling-percentage question and nothing else."""

    def __init__(self) -> None:
        self.metadata_calls = 0

    async def list_symbols(self, exchange, quote_currencies):
        return list(PERCENTAGES)

    async def fetch_universe_metadata(self, exchange, symbols, include_listing_dates=False):
        self.metadata_calls += 1
        return {
            symbol: {"percentage_24h": PERCENTAGES[symbol]}
            for symbol in symbols
            if symbol in PERCENTAGES
        }

    async def fetch_ohlcv(self, exchange, symbol, timeframe, limit):
        end = datetime.now(UTC) - timedelta(minutes=15)
        return [
            Candle(
                timestamp=end - timedelta(minutes=15 * offset),
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1000,
            )
            for offset in range(limit - 1, -1, -1)
        ]


def _conversation_of(chat) -> dict:
    return dict((chat.context_json or {}).get("setup_conversation_context") or {})


async def _run_full_scanner_journey(service, session, chat, *, prefix: str) -> None:
    """Every step a trader takes, in order, ending in a governed scan."""

    steps: list[dict] = [
        {"message": "Scanner"},
        {"message": "what coins are up at least 5% now?"},
        {"message": "24 hours"},
        {
            "message": "",
            "option_key": "screened_universe_mode",
            "option_value": "explicit_assets",
            "option_label": "Specific eligible assets",
        },
        {
            "message": "",
            "option_key": "screened_explicit_assets",
            "option_value": "BTC/USDT, ETH/USDT",
            "option_label": "BTC/USDT, ETH/USDT",
        },
    ]
    for index, step in enumerate(steps, start=1):
        await service.handle_message(
            session, chat, client_message_id=f"{prefix}-{index}", **step
        )


async def _scanner_user(test_context) -> User:
    async with test_context["session_factory"]() as session:
        user = User(display_name="Scanner Flow Test")
        session.add(user)
        await _seed_methodology(session)
        await session.commit()
        await session.refresh(user)
        return user


async def test_the_three_turn_scanner_flow_reaches_governed_execution(test_context):
    """The whole point: turn three runs the scan the first two described."""

    user = await _scanner_user(test_context)
    planner = StandInPlanner()
    provider = ScannerMarketProvider()
    service = AISetupChatService(
        _scanner_settings(test_context["settings"]),
        provider,
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], planner),
    )

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)

        # 1. Choose Scanner by typing it, not by pressing the button.
        await service.handle_message(
            session, chat, message="Scanner", client_message_id="scanflow-1"
        )
        assert load_strategy_draft_v2(chat).mode.value == "scanner"

        # 2. Ask the live market question.
        await service.handle_message(
            session,
            chat,
            message="what coins are up at least 5% now?",
            client_message_id="scanflow-2",
        )

        # 3. Answer the one question it asked. The screened scope is still unchosen, so
        #    the scan is held here rather than run — governed screening is not skipped
        #    to answer faster.
        await service.handle_message(
            session, chat, message="24 hours", client_message_id="scanflow-3"
        )
        held = _conversation_of(chat)
        assert held["pending_read_only_scan"]["measurement_window"] == "24h"
        assert await session.scalar(select(OnDemandScanRun)) is None

        # 4 and 5. Choose the governed screened scope. The scan the trader already
        #    described resumes from what was stored — the 5%, the direction and the
        #    window are never asked for a second time.
        await service.handle_message(
            session,
            chat,
            message="",
            option_key="screened_universe_mode",
            option_value="explicit_assets",
            option_label="Specific eligible assets",
            client_message_id="scanflow-4",
        )
        await service.handle_message(
            session,
            chat,
            message="",
            option_key="screened_explicit_assets",
            option_value="BTC/USDT, ETH/USDT",
            option_label="BTC/USDT, ETH/USDT",
            client_message_id="scanflow-5",
        )

        run = await session.scalar(
            select(OnDemandScanRun).where(OnDemandScanRun.user_id == user.id)
        )
        assert run is not None, "the scan must reach a durable governed run"
        assert provider.metadata_calls >= 1, "the provider must actually be asked"

        messages = (
            await session.scalars(
                select(AISetupChatMessage)
                .where(AISetupChatMessage.session_id == chat.id)
                .order_by(AISetupChatMessage.sequence)
            )
        ).all()
        kinds = [item.message_type for item in messages]
        assert "scanner_result" in kinds, kinds

        # The result names the coin that really cleared 5%, and not the one that did not.
        # Invented results and a real scan cannot look alike here.
        result_message = next(
            item for item in reversed(messages) if item.message_type == "scanner_result"
        )
        assert "BTC/USDT" in result_message.content
        assert "+7.25%" in result_message.content
        assert "ETH/USDT" not in result_message.content

        # The values from turns two and three were never asked for again.
        asked = [item.content for item in messages if item.role == "assistant"]
        assert sum("rolling 24-hour percentage change" in item for item in asked) == 1

        # And no Monitor state was created on the way.
        after = load_strategy_draft_v2(chat)
        assert after.condition_ast is None
        assert not after.approval.approved


async def _scope_answer_journey(test_context, *, answer: str, prefix: str):
    """Scanner -> query -> window -> a typed scope answer -> governed execution."""

    user = await _scanner_user(test_context)
    provider = ScannerMarketProvider()
    service = AISetupChatService(
        _scanner_settings(test_context["settings"]),
        provider,
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], StandInPlanner()),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        for index, message in enumerate(
            ("Scanner", "what coins are up at least 5% now?", "24 hours", answer),
            start=1,
        ):
            await service.handle_message(
                session,
                chat,
                message=message,
                client_message_id=f"{prefix}-{index}",
            )
        run = await session.scalar(
            select(OnDemandScanRun).where(OnDemandScanRun.user_id == user.id)
        )
        messages = (
            await session.scalars(
                select(AISetupChatMessage)
                .where(AISetupChatMessage.session_id == chat.id)
                .order_by(AISetupChatMessage.sequence)
            )
        ).all()
        return chat, run, messages, provider


async def test_typing_all_answers_the_scope_question_and_runs_the_scan(test_context):
    """The reported dead end: "all" used to be read as a brand new request."""

    chat, run, messages, provider = await _scope_answer_journey(
        test_context, answer="all", prefix="scope-all"
    )

    assert run is not None, "a typed scope answer must reach a durable governed run"
    assert provider.metadata_calls >= 1
    result = next(item for item in reversed(messages) if item.message_type == "scanner_result")
    assert "BTC/USDT" in result.content
    assert "+7.25%" in result.content
    assert "ETH/USDT" not in result.content

    # The values from the earlier turns were never asked for a second time.
    asked = [item.content for item in messages if item.role == "assistant"]
    assert sum("rolling 24-hour percentage change" in item for item in asked) == 1
    assert not any("5%" in item and "?" in item for item in asked[3:])


async def test_choosing_the_whole_market_records_that_a_person_chose_it(test_context):
    """The default value alone proves nothing; the selection is what proves it."""

    chat, run, _messages, _provider = await _scope_answer_journey(
        test_context, answer="All eligible spot assets", prefix="scope-label"
    )

    assert run is not None
    draft = load_strategy_draft_v2(chat)
    assert draft.sharia_policy.universe_mode is ShariaUniverseMode.ELIGIBLE_MARKET
    assert universe_mode_is_user_selected(draft), (
        "choosing the whole screened market must be recorded as a choice, "
        "even though the stored value never changed"
    )
    assert not any(
        item.unresolved_id == "sharia.universe_mode" for item in draft.unresolved_fields
    )


async def test_a_scope_answer_never_mutates_or_approves_a_strategy(test_context):
    chat, run, _messages, _provider = await _scope_answer_journey(
        test_context, answer="all coins", prefix="scope-safety"
    )

    assert run is not None
    draft = load_strategy_draft_v2(chat)
    assert draft.condition_ast is None
    assert not draft.approval.approved
    assert draft.mode is DraftMode.SCANNER


async def test_an_unreadable_scope_answer_keeps_the_question_and_the_scan(test_context):
    """Nothing is lost when the answer cannot be read — the scan is still waiting."""

    user = await _scanner_user(test_context)
    service = AISetupChatService(
        _scanner_settings(test_context["settings"]),
        ScannerMarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], StandInPlanner()),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        for index, message in enumerate(
            ("Scanner", "what coins are up at least 5% now?", "24 hours"), start=1
        ):
            await service.handle_message(
                session, chat, message=message, client_message_id=f"scope-bad-{index}"
            )
        conversation = _conversation_of(chat)
        assert conversation.get("active_question_id") == SCAN_SCOPE_QUESTION

        run = await session.scalar(
            select(OnDemandScanRun).where(OnDemandScanRun.user_id == user.id)
        )
        assert run is None, "no scan may run before its scope is chosen"
        pending = dict(conversation.get("pending_read_only_scan") or {})
        assert pending.get("threshold_percent") == 5
        assert pending.get("movement_direction") == "up"
        assert pending.get("measurement_window") == "24h"


def test_recording_a_turn_can_never_be_the_thing_that_fails_it() -> None:
    """A completed scan was thrown away by its own telemetry.

    ``scan_completed`` was missing from the allowed outcomes, so `_record_funnel`
    raised *after* the scan had run, the provider had been paid and the result had been
    computed. The outcome now exists, and an unknown one is recorded rather than raised.
    """

    from ai_market_monitor.engine.turn_timing import TurnTelemetry
    from ai_market_monitor.services.setup_chat_launch import (
        FUNNEL_OUTCOMES,
        _record_funnel,
    )

    assert "scan_completed" in FUNNEL_OUTCOMES
    assert "scan_refused" in FUNNEL_OUTCOMES

    context: dict = {}
    _record_funnel(
        context,
        outcome="an_outcome_nobody_listed",
        telemetry=TurnTelemetry.start(30),
        failure_code=None,
        model_calls=0,
    )
    assert context["setup_turn_funnel"], "the turn is still recorded, not lost"


async def test_the_governed_scan_is_read_only_and_costs_no_model_call(test_context):
    user = await _scanner_user(test_context)
    planner = StandInPlanner()
    service = AISetupChatService(
        _scanner_settings(test_context["settings"]),
        ScannerMarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], planner),
    )

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        for index, message in enumerate(
            ("Scanner", "what coins are up at least 5% now?", "24 hours"), start=1
        ):
            await service.handle_message(
                session, chat, message=message, client_message_id=f"scanflow-ro-{index}"
            )

        after = load_strategy_draft_v2(chat)
        assert after.condition_ast is None
        assert not after.approval.approved
        # The whole conversation was answerable from the server's own reading.
        assert planner.plan_calls == 0


async def test_the_pending_scan_is_cleared_once_it_has_run(test_context):
    """A finished scan must not re-run itself on the next thing the trader says."""

    user = await _scanner_user(test_context)
    service = AISetupChatService(
        _scanner_settings(test_context["settings"]),
        ScannerMarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], StandInPlanner()),
    )

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _run_full_scanner_journey(service, session, chat, prefix="scanflow-clear")

        conversation = _conversation_of(chat)
        assert not conversation.get("pending_read_only_scan")
        assert conversation.get("active_goal") is None
        assert conversation.get("active_question_id") is None
