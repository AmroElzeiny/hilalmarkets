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
    CapabilityResolutionEvent,
    SystemBrainSession,
)
from ai_market_monitor.db.models.accounts import User
from ai_market_monitor.engine.capability_resolver import CapabilityResolver
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
