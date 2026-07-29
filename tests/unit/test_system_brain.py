from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from ai_market_monitor.core.security import hash_password
from ai_market_monitor.db.models import (
    AgentRun,
    AgentToolCall,
    AISetupChatSession,
    AIUsageEvent,
    CapabilityAliasProposal,
    CapabilityExtension,
    CapabilityResolutionEvent,
    PublicChatAnswerEvent,
    PublicChatAnswerFeedback,
    PublicInquiry,
    PublicInquiryEmailDelivery,
    PublicInquiryRating,
    SystemBrainSession,
)
from ai_market_monitor.db.models.accounts import User
from ai_market_monitor.engine.capability_resolver import CapabilityResolver
from ai_market_monitor.services.ai_usage_context import ai_usage_correlation
from ai_market_monitor.services.system_brain import (
    CapabilityCoverageService,
    SystemBrainAccessError,
    SystemBrainAuthService,
)


def _configure(test_context) -> None:
    settings = test_context["settings"]
    settings.system_brain_admin_username = "contact@trace-edge.com"
    settings.system_brain_admin_password_hash = SecretStr(hash_password("Admin-Test-Password!"))
    settings.auth_test_fixed_code = "123456"


async def test_system_brain_password_otp_session_and_logout(test_context):
    _configure(test_context)
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        service = SystemBrainAuthService(settings)
        pending = await service.begin_login(
            session,
            username="CONTACT@trace-edge.com",
            password="Admin-Test-Password!",
            remote_ip="127.0.0.1",
        )
        assert settings.email_test_outbox[-1]["purpose"] == "system_brain"
        cookie = await service.verify_otp(
            session,
            pending_cookie=pending,
            code="123456",
            remote_ip="127.0.0.1",
            user_agent="pytest",
        )
        principal = await service.current_session(session, cookie)
        assert principal is not None
        assert principal.email == "contact@trace-edge.com"

        await service.logout(session, cookie)
        assert await service.current_session(session, cookie) is None


async def test_system_brain_rejects_wrong_password_and_wrong_otp(test_context):
    _configure(test_context)
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        service = SystemBrainAuthService(settings)
        with pytest.raises(SystemBrainAccessError, match="username or password"):
            await service.begin_login(
                session,
                username="contact@trace-edge.com",
                password="wrong",
                remote_ip="127.0.0.2",
            )
        pending = await service.begin_login(
            session,
            username="contact@trace-edge.com",
            password="Admin-Test-Password!",
            remote_ip="127.0.0.2",
        )
        with pytest.raises(SystemBrainAccessError, match="incorrect"):
            await service.verify_otp(
                session,
                pending_cookie=pending,
                code="000000",
                remote_ip="127.0.0.2",
                user_agent="pytest",
        )


async def test_system_brain_accepts_each_configured_operator_email(test_context):
    _configure(test_context)
    settings = test_context["settings"]
    settings.system_brain_admin_emails = "office@hilalmarkets.com"
    assert settings.system_brain_authorized_emails == {
        "contact@trace-edge.com",
        "office@hilalmarkets.com",
    }

    async with test_context["session_factory"]() as session:
        service = SystemBrainAuthService(settings)
        pending = await service.begin_login(
            session,
            username="office@hilalmarkets.com",
            password="Admin-Test-Password!",
            remote_ip="127.0.0.9",
        )
        cookie = await service.verify_otp(
            session,
            pending_cookie=pending,
            code="123456",
            remote_ip="127.0.0.9",
            user_agent="pytest",
        )
        principal = await service.current_session(session, cookie)
        assert principal is not None
        assert principal.email == "office@hilalmarkets.com"


async def test_capability_telemetry_records_false_ranking_alias_and_cost(test_context):
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        user = User(display_name="Coverage User")
        session.add(user)
        await session.flush()
        chat = AISetupChatSession(user_id=user.id, title="Coverage chat")
        session.add(chat)
        await session.flush()
        coverage = CapabilityCoverageService(settings)
        report = CapabilityResolver().resolve_prompt("coins with momentum")
        await coverage.record_resolution(session, chat=chat, report=report)
        await session.flush()
        event = await session.scalar(select(CapabilityResolutionEvent))
        assert event is not None
        assert event.candidates
        alternative = event.candidates[1]["capability_key"]
        await coverage.record_clarification_choice(
            session,
            chat=chat,
            option_key="capability_meaning_coins_with_momentum",
            option_value=f"Interpret 'momentum' as another mechanic ({alternative})",
        )
        await coverage.record_usage(
            session,
            chat=chat,
            operation="setup_interview",
            usage={
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
                "input_tokens_details": {"cached_tokens": 200_000},
                "output_tokens_details": {"reasoning_tokens": 300_000},
            },
        )
        await session.commit()

        proposal = await session.scalar(select(CapabilityAliasProposal))
        assert proposal is not None
        assert proposal.capability_key == alternative
        usage = await session.scalar(select(AIUsageEvent))
        assert usage is not None
        assert usage.reasoning_tokens == 300_000
        assert usage.estimated_cost_usd == Decimal("1.41400000")


async def test_ai_usage_records_the_current_request_correlation(test_context):
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        user = User(display_name="Correlated Usage")
        session.add(user)
        await session.flush()
        chat = AISetupChatSession(user_id=user.id, title="Correlated chat")
        session.add(chat)
        await session.flush()

        with ai_usage_correlation("request-correlation-1"):
            await CapabilityCoverageService(settings).record_usage(
                session,
                chat=chat,
                operation="strategy_compile",
                usage={
                    "input_tokens": 100,
                    "output_tokens": 20,
                },
            )
        await session.commit()

        usage = await session.scalar(select(AIUsageEvent))
        assert usage is not None
        assert usage.raw_usage["_traceedge_correlation_id"] == "request-correlation-1"


async def test_system_brain_session_is_database_backed(test_context):
    _configure(test_context)
    async with test_context["session_factory"]() as session:
        service = SystemBrainAuthService(test_context["settings"])
        pending = await service.begin_login(
            session,
            username="contact@trace-edge.com",
            password="Admin-Test-Password!",
            remote_ip="127.0.0.3",
        )
        await service.verify_otp(
            session,
            pending_cookie=pending,
            code="123456",
            remote_ip="127.0.0.3",
            user_agent="pytest",
        )
        assert await session.scalar(select(SystemBrainSession)) is not None


async def test_system_brain_reports_bounded_agent_safety_metrics(test_context):
    settings = test_context["settings"]
    settings.ai_agent_control_enabled = True
    settings.ai_agent_shadow_mode = True
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        run = AgentRun(
            user_id=None,
            chat_session_id=None,
            model=settings.openai_model,
            reasoning_effort=settings.openai_reasoning_effort,
            started_at=now - timedelta(milliseconds=250),
            ended_at=now,
            status="contained",
            step_count=2,
            tool_call_count=1,
            input_tokens=100,
            cached_input_tokens=25,
            output_tokens=40,
            reasoning_tokens=10,
            estimated_cost_usd=Decimal("0.00010000"),
            correlation_id="agent-test-correlation",
            error_type="ungrounded:scan_claim_without_result",
            shadow_mode=False,
            fallback_used=False,
            final_intent="refusal",
            final_response_status="blocked",
            comparison={
                "tool_result_summaries": [
                    {
                        "tool_name": "compile_strategy_draft",
                        "status": "success",
                        "approval_eligible": True,
                        "unsupported_count": 0,
                        "ambiguity_count": 0,
                        "lint_count": 0,
                    },
                    {
                        "tool_name": "resolve_trading_capabilities",
                        "status": "success",
                        "clarification_count": 1,
                    },
                ]
            },
        )
        session.add(run)
        await session.flush()
        session.add(
            AgentToolCall(
                agent_run_id=run.id,
                openai_call_id="agent-test-call",
                tool_name="activate_monitor",
                argument_hash="a" * 64,
                redacted_arguments={},
                policy_decision="rejected",
                result_status="blocked",
                evidence_refs=[],
                duration_ms=1,
                retry_count=0,
                created_at=now,
            )
        )
        await session.commit()
        data = await CapabilityCoverageService(settings).overview(session)

    assert data["agent_control"]["configured_enabled"] is True
    assert data["agent_control"]["shadow_enabled"] is True
    assert data["agent_control"]["recorded_runs"] == 1
    assert data["agent_control"]["forbidden_executed"] == 0
    assert data["agent_control"]["forbidden_attempts"] == 1
    assert data["agent_control"]["ungrounded_claims_contained"] == 1
    assert data["agent_control"]["draft_compilation_success_rate"] == 100
    assert data["agent_control"]["unsupported_condition_leakage"] == 0
    assert data["agent_control"]["clarification_turns"] == 1
    assert data["agent_control"]["tool_breakdown"][0]["tool_name"] == "activate_monitor"
    assert data["agent_control"]["tool_breakdown"][0]["blocked"] == 1


async def test_system_brain_reports_persisted_setup_model_routes(test_context):
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        session.add_all(
            [
                AIUsageEvent(
                    operation="setup_interview",
                    model="configured-simple",
                    reasoning_effort="low",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    estimated_cost_usd=Decimal("0.00000100"),
                    pricing_source="test",
                    raw_usage={
                        "_traceedge_route_tier": "simple",
                        "_traceedge_route_reasons": ["simple_clear_request"],
                    },
                    created_at=datetime.now(UTC),
                ),
                AIUsageEvent(
                    operation="setup_interview",
                    model="configured-complex",
                    reasoning_effort="medium",
                    input_tokens=20,
                    output_tokens=10,
                    reasoning_tokens=2,
                    estimated_cost_usd=Decimal("0.00000200"),
                    pricing_source="test",
                    raw_usage={
                        "_traceedge_route_tier": "complex",
                        "_traceedge_route_reasons": [
                            "mixed_boolean_logic",
                            "multiple_timeframes",
                        ],
                    },
                    created_at=datetime.now(UTC),
                ),
                AIUsageEvent(
                    operation="legacy_interpretation",
                    model="legacy-model",
                    reasoning_effort="low",
                    input_tokens=5,
                    output_tokens=2,
                    reasoning_tokens=0,
                    estimated_cost_usd=Decimal("0"),
                    pricing_source="test",
                    raw_usage={},
                    created_at=datetime.now(UTC),
                ),
            ]
        )
        await session.commit()

        data = await CapabilityCoverageService(settings).overview(session)

    routing = data["setup_model_routing"]
    assert routing["routed_calls"] == 2
    assert routing["unclassified_calls"] == 1
    assert {item["tier"]: item["calls"] for item in routing["tiers"]} == {
        "simple": 1,
        "complex": 1,
    }
    assert routing["reasons"][0]["calls"] == 1
    assert {item["model"] for item in routing["models"]} == {
        "configured-simple",
        "configured-complex",
    }


async def test_system_brain_reports_live_ai_and_public_support_operations(test_context):
    settings = test_context["settings"]
    settings.ai_agent_control_enabled = True
    settings.ai_agent_shadow_mode = False
    settings.ai_agent_rollout_percent = 100
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        user = User(display_name="AI operations owner")
        session.add(user)
        await session.flush()
        chat = AISetupChatSession(
            user_id=user.id,
            status="approved",
            title="Certified candle monitor",
        )
        session.add(chat)
        await session.flush()
        run = AgentRun(
            user_id=user.id,
            chat_session_id=chat.id,
            model="configured-live-model",
            reasoning_effort="low",
            started_at=now - timedelta(milliseconds=180),
            ended_at=now,
            status="completed",
            step_count=2,
            tool_call_count=1,
            input_tokens=90,
            cached_input_tokens=10,
            output_tokens=30,
            reasoning_tokens=4,
            estimated_cost_usd=Decimal("0.00020000"),
            correlation_id="live-agent-operations-test",
            shadow_mode=False,
            fallback_used=False,
            final_intent="draft_ready",
            final_response_status="completed",
            comparison={
                "model_route": {"correction_count": 2},
                "clause_coverage_failures": 1,
                "tool_result_summaries": [
                    {
                        "tool_name": "compile_strategy_draft",
                        "status": "success",
                        "approval_eligible": True,
                        "unsupported_count": 0,
                    },
                    {
                        "tool_name": "resolve_trading_capabilities",
                        "status": "success",
                        "provider_requirement_count": 1,
                    },
                ],
            },
        )
        session.add(run)
        await session.flush()
        session.add(
            AgentToolCall(
                agent_run_id=run.id,
                openai_call_id="live-agent-tool-call",
                tool_name="compile_strategy_draft",
                argument_hash="b" * 64,
                redacted_arguments={},
                policy_decision="allowed",
                result_status="success",
                evidence_refs=["chat:draft:test"],
                duration_ms=35,
                retry_count=0,
                created_at=now,
            )
        )
        session.add(
            CapabilityExtension(
                user_id=user.id,
                chat_session_id=chat.id,
                request_fingerprint="c" * 64,
                capability_key="custom_body_ratio_test",
                capability_version="0.1.0",
                registry_hash="d" * 64,
                artifact_hash="e" * 64,
                source_prompt="Candle body exceeds seventy percent of its range",
                conversation_history=[],
                status="certified_user",
                stage="monitoring",
                validation_score=92,
                repair_generation=1,
                certified_at=now,
            )
        )
        inquiry = PublicInquiry(
            reference="HM-OPS-TEST-001",
            name="Operations Visitor",
            normalized_email="visitor@example.com",
            category="product",
            details="Please clarify this product limitation.",
            source_page="/help",
            attribution={},
            support_metadata={},
            knowledge_gap_category="product_limitation",
            idempotency_key="public-inquiry:operations:test",
            status="received",
            submitted_at=now,
            retain_until=now + timedelta(days=30),
        )
        session.add(inquiry)
        await session.flush()
        answer_event = PublicChatAnswerEvent(
            session_key_hash="f" * 64,
            conversation_id=None,
            user_id=None,
            question_hash="1" * 64,
            outcome="answered",
            stage="ANSWER",
            mode="PRODUCT_FACT",
            intent="product_help",
            model="configured-live-model",
            input_tokens=80,
            output_tokens=24,
            reasoning_tokens=2,
            latency_ms=140,
            estimated_cost_usd=Decimal("0.00010000"),
            coverage_score=Decimal("0.95000"),
            source_ids=["product-overview:v1"],
            related_route_ids=["home"],
            created_at=now,
            retain_until=now + timedelta(days=30),
        )
        session.add(answer_event)
        await session.flush()
        session.add_all(
            [
                PublicChatAnswerFeedback(
                    answer_event_id=answer_event.id,
                    conversation_id=None,
                    user_id=None,
                    session_key_hash="f" * 64,
                    helpful=False,
                    support_form_requested=True,
                    stage="ANSWER",
                    mode="PRODUCT_FACT",
                    intent="product_help",
                    model="configured-live-model",
                    confidence=Decimal("0.95000"),
                    source_ids=["product-overview:v1"],
                    inquiry_id=inquiry.id,
                ),
                PublicInquiryEmailDelivery(
                    inquiry_id=inquiry.id,
                    event_key="public-inquiry:operations:test:customer",
                    recipient_kind="customer",
                    recipient="visitor@example.com",
                    status="sent",
                    attempt_count=1,
                    provider_message_id="provider-test-message",
                    sent_at=now,
                    created_at=now,
                ),
                PublicInquiryRating(
                    inquiry_id=inquiry.id,
                    rating=5,
                    helpful=True,
                    feedback="Clear answer.",
                    created_at=now,
                ),
            ]
        )
        await session.commit()
        data = await CapabilityCoverageService(settings).operations_summary(session)

    assert data["agent"]["configured_enabled"] is True
    assert data["agent"]["shadow_enabled"] is False
    assert data["agent"]["rollout_percent"] == 100
    assert data["agent"]["tool_success_rate"] == 100
    assert data["agent"]["draft_compilation_success_rate"] == 100
    assert data["agent"]["approval_conversion_rate"] == 100
    assert data["agent"]["user_corrections"] == 2
    assert data["agent"]["clause_coverage_failures"] == 1
    assert data["agent"]["unsupported_provider_turns"] == 1
    assert data["custom_capabilities"]["certified"] == 1
    assert data["custom_capabilities"]["certification_success_rate"] == 100
    assert data["custom_capabilities"]["repair_rate"] == 100
    assert data["public_support"]["answered"] == 1
    assert data["public_support"]["source_coverage_percent"] == 100
    assert data["public_support"]["email_states"] == [{"state": "sent", "count": 1}]
    assert data["public_support"]["average_rating"] == 5
    assert data["public_support"]["answer_feedback_count"] == 1
    assert data["public_support"]["support_form_request_percent"] == 100
    assert data["public_support"]["support_form_completion_percent"] == 100
    assert data["public_support"]["greeting_misclassification_count"] == 0
    assert data["public_support"]["knowledge_gaps"] == [
        {"category": "product_limitation", "count": 1}
    ]
