import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    AgentRun,
    AgentToolCall,
    AISetupChatMessage,
    AISetupChatSession,
    AuditEvent,
    CustomerConversationEvent,
    PublicChatConversation,
    PublicChatMessage,
    PublicChatTurn,
    RepositoryEvidenceIndex,
    SystemBrainActionProposal,
    SystemBrainConversation,
    SystemBrainMessage,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, UserRole, UserStatus
from ai_market_monitor.schemas.public_chat import PublicChatAnswerRequest
from ai_market_monitor.schemas.system_brain import (
    ActionExactChanges,
    ActionProposalRequest,
    EvidenceEnvelope,
    SystemBrainAgentTurnRequest,
    SystemBrainToolArguments,
)
from ai_market_monitor.services.public_chat import PublicChatService
from ai_market_monitor.services.system_brain_actions import (
    SystemBrainActionError,
    SystemBrainActionService,
)
from ai_market_monitor.services.system_brain_agent import (
    SystemBrainAgentPolicy,
    SystemBrainAgentService,
    SystemBrainAgentUnavailable,
    SystemBrainConversationService,
)
from ai_market_monitor.services.system_brain_conversations import AdminConversationExplorer
from ai_market_monitor.services.system_brain_repository_index import (
    SENSITIVE_NAME_PARTS,
    RepositoryEvidenceIndexService,
    _classification,
)
from ai_market_monitor.services.system_brain_tools import (
    ALL_READ_TOOLS,
    ALL_SYSTEM_BRAIN_TOOLS,
    SystemBrainToolRegistry,
)


class SequencedResponsesClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.payloads: list[dict[str, Any]] = []

    async def create(self, payload: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
        assert timeout_seconds > 0
        self.payloads.append(payload)
        return self.responses.pop(0)


class UnexpectedFailureClient:
    def __init__(self) -> None:
        self.calls = 0

    async def create(self, payload: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
        del payload, timeout_seconds
        self.calls += 1
        raise RuntimeError("provider internals must not escape")


async def test_system_brain_agent_selects_tools_persists_and_replays_exactly(test_context):
    settings = test_context["settings"]
    settings.system_brain_ai_enabled = True
    settings.openai_api_key = "test-key"
    settings.system_brain_ai_model = "gpt-5.4-nano"
    final = {
        "answer": "The maintained repository index has no matching retained evidence.",
        "findings": [],
        "opportunities": [],
        "suggested_actions": [],
        "evidence_refs": ["query:repository_search:no-result"],
        "limitations": ["No indexed file matched; missing evidence is not a zero result."],
    }
    client = SequencedResponsesClient(
        [
            {
                "output": [
                    {
                        "type": "function_call",
                        "name": "repository_search",
                        "call_id": "call-1",
                        "arguments": json.dumps({"query": "impossible-index-term", "limit": 5}),
                    }
                ],
                "usage": {"input_tokens": 30, "output_tokens": 5},
            },
            {
                "output_text": json.dumps(final),
                "output": [],
                "usage": {"input_tokens": 45, "output_tokens": 22},
            },
        ]
    )
    async with test_context["session_factory"]() as session:
        admin = User(display_name="Admin", role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()
        conversation = await SystemBrainConversationService().create(
            session, admin_user_id=admin.id, title="Repository evidence"
        )
        await session.commit()
        service = SystemBrainAgentService(settings, client=client)
        request = SystemBrainAgentTurnRequest(
            message="Search the code for impossible-index-term",
            client_message_id="brain-idempotent-0001",
        )
        first = await service.run_turn(
            session, conversation.id, admin_user_id=admin.id, request=request
        )
        replay = await service.run_turn(
            session, conversation.id, admin_user_id=admin.id, request=request
        )
        message_count = int(await session.scalar(select(func.count(SystemBrainMessage.id))) or 0)
        run_count = int(await session.scalar(select(func.count(AgentRun.id))) or 0)
        call_count = int(await session.scalar(select(func.count(AgentToolCall.id))) or 0)

    assert first == replay
    assert first.status == "completed"
    assert message_count == 2
    assert run_count == 1
    assert call_count == 1
    assert len(client.payloads) == 2
    assert client.payloads[0]["tools"][0]["name"] == "repository_search"
    assert "revenue_summary" not in {item["name"] for item in client.payloads[0]["tools"]}
    assert "conversation_history" in client.payloads[0]["input"][0]["content"]
    continuation_types = [
        item.get("type") for item in client.payloads[1]["input"] if isinstance(item, dict)
    ]
    assert "function_call" in continuation_types
    assert "function_call_output" in continuation_types


async def test_unexpected_provider_failure_is_persisted_and_replayed_safely(test_context):
    settings = test_context["settings"]
    settings.system_brain_ai_enabled = True
    settings.openai_api_key = "test-key"
    client = UnexpectedFailureClient()
    request = SystemBrainAgentTurnRequest(
        message="Inspect current revenue evidence",
        client_message_id="provider-failure-safe-0001",
    )
    async with test_context["session_factory"]() as session:
        admin = User(display_name="Admin", role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()
        conversation = SystemBrainConversation(admin_user_id=admin.id, title="Failure")
        session.add(conversation)
        await session.commit()
        service = SystemBrainAgentService(
            settings,
            client=client,
            session_factory=test_context["session_factory"],
        )
        first = await service.run_turn(
            session,
            conversation.id,
            admin_user_id=admin.id,
            request=request,
        )
        replay = await service.run_turn(
            session,
            conversation.id,
            admin_user_id=admin.id,
            request=request,
        )
        run = await session.get(AgentRun, first.run_id)

    assert first.status == "failed"
    assert replay == first
    assert client.calls == 1
    assert run is not None and run.error_type == "provider:unavailable"
    assert "provider internals" not in first.answer


async def test_ungrounded_model_narrative_uses_only_deterministic_tool_evidence(test_context):
    settings = test_context["settings"]
    settings.system_brain_ai_enabled = True
    settings.openai_api_key = "test-key"
    client = SequencedResponsesClient(
        [
            {
                "output": [
                    {
                        "type": "function_call",
                        "name": "repository_search",
                        "call_id": "fallback-call",
                        "arguments": json.dumps({"query": "nothing-indexed", "limit": 5}),
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
            {
                "output_text": json.dumps(
                    {
                        "answer": "Invented result",
                        "findings": [],
                        "opportunities": [],
                        "suggested_actions": [],
                        "evidence_refs": ["invented:evidence"],
                        "limitations": [],
                    }
                ),
                "output": [],
                "usage": {"input_tokens": 10, "output_tokens": 3},
            },
        ]
    )
    async with test_context["session_factory"]() as session:
        admin = User(display_name="Admin", role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()
        conversation = SystemBrainConversation(admin_user_id=admin.id, title="Fallback")
        session.add(conversation)
        await session.commit()
        result = await SystemBrainAgentService(
            settings,
            client=client,
            session_factory=test_context["session_factory"],
        ).run_turn(
            session,
            conversation.id,
            admin_user_id=admin.id,
            request=SystemBrainAgentTurnRequest(
                message="Search code for nothing-indexed",
                client_message_id="grounded-fallback-0001",
            ),
        )

    assert result.status == "degraded"
    assert result.evidence_refs == ["query:repository_search:no-result"]
    assert "invented:evidence" not in result.evidence_refs
    assert "Invented result" not in result.answer


def test_system_brain_policy_and_registry_enforce_bounded_authorities(test_context):
    settings = test_context["settings"]
    policy = SystemBrainAgentPolicy(settings)
    customer_tools = policy.offered_tools("Show trial conversion and churn")
    engineering_tools = policy.offered_tools("Search the repository for this function")

    assert "revenue_summary" in customer_tools
    assert "repository_search" not in customer_tools
    assert "repository_search" in engineering_tools
    assert len(ALL_SYSTEM_BRAIN_TOOLS) == len(set(ALL_SYSTEM_BRAIN_TOOLS))
    assert len(SystemBrainToolRegistry(settings).openai_tools()) == len(ALL_SYSTEM_BRAIN_TOOLS)
    assert "credential" in SENSITIVE_NAME_PARTS


async def test_every_registered_read_tool_returns_a_bounded_evidence_envelope(
    test_context,
):
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        admin = User(display_name="Admin", role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()
        conversation = SystemBrainConversation(admin_user_id=admin.id, title="Tool smoke")
        session.add(conversation)
        await session.flush()
        registry = SystemBrainToolRegistry(settings)
        for tool_name in ALL_READ_TOOLS:
            result = await registry.execute(
                session,
                admin_user_id=admin.id,
                conversation_id=conversation.id,
                tool_name=tool_name,
                arguments=SystemBrainToolArguments(limit=5),
                request_id="tool-smoke",
                pii_access_allowed=True,
            )
            assert result.coverage
            assert len(result.evidence_refs) <= 100


async def test_customer_conversation_explorer_uses_exact_messages_identity_and_audit(
    test_context,
):
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        admin = User(display_name="Admin", role=UserRole.ADMIN)
        customer = User(display_name="Trader")
        session.add_all([admin, customer])
        await session.flush()
        session.add(
            UserIdentity(
                user_id=customer.id,
                provider=IdentityProvider.EMAIL,
                provider_subject="trader@example.com",
                normalized_identifier="trader@example.com",
                display_identifier="Trader@Example.com",
                is_verified=True,
                is_primary=True,
            )
        )
        setup = AISetupChatSession(user_id=customer.id, title="Momentum watch")
        public = PublicChatConversation(
            session_key_hash="a" * 64,
            user_id=None,
            state_json={},
            stage="ANSWER",
            message_count=2,
            expires_at=now + timedelta(days=2),
        )
        session.add_all([setup, public])
        await session.flush()
        session.add_all(
            [
                AISetupChatMessage(
                    session_id=setup.id,
                    sequence=1,
                    role="user",
                    content="Use RSI; API_KEY=must-not-leak",
                    payload={},
                    created_at=now,
                ),
                AISetupChatMessage(
                    session_id=setup.id,
                    sequence=2,
                    role="assistant",
                    content="I need one timeframe.",
                    payload={},
                    created_at=now + timedelta(seconds=1),
                ),
                PublicChatMessage(
                    conversation_id=public.id,
                    sequence=1,
                    role="user",
                    content="What is Hilal Markets?",
                    created_at=now,
                    retain_until=public.expires_at,
                ),
                PublicChatMessage(
                    conversation_id=public.id,
                    sequence=2,
                    role="assistant",
                    content="A research platform.",
                    created_at=now + timedelta(seconds=1),
                    retain_until=public.expires_at,
                ),
            ]
        )
        await session.commit()
        explorer = AdminConversationExplorer(session)
        page = await explorer.list_conversations(limit=20)
        setup_summary = next(item for item in page.items if item.conversation_id == setup.id)
        public_summary = next(item for item in page.items if item.conversation_id == public.id)
        timeline = await explorer.conversation(
            setup.id,
            source="authenticated_setup_chat",
            admin_user_id=admin.id,
            access_reason="Quality review",
            request_id="request-test",
            ip_hash="hash",
        )
        await session.commit()
        audit = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "system_brain.customer_conversation.view",
                AuditEvent.target_id == str(setup.id),
            )
        )

    assert setup_summary.user_email == "Trader@Example.com"
    assert public_summary.anonymous is True
    assert public_summary.user_email is None
    assert [item.sequence for item in timeline.messages] == [1, 2]
    assert "must-not-leak" not in timeline.messages[0].content
    assert "[REDACTED]" in timeline.messages[0].content
    assert audit is not None
    assert audit.metadata_redacted["access_reason"] == "Quality review"


async def test_deleted_customer_content_is_not_exposed(test_context):
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        admin = User(display_name="Admin", role=UserRole.ADMIN)
        deleted = User(
            display_name="Deleted",
            status=UserStatus.DELETED,
        )
        session.add_all([admin, deleted])
        await session.flush()
        setup = AISetupChatSession(user_id=deleted.id)
        session.add(setup)
        await session.flush()
        session.add(
            AISetupChatMessage(
                session_id=setup.id,
                sequence=1,
                role="user",
                content="private deleted content",
                payload={},
                created_at=now,
            )
        )
        await session.commit()
        timeline = await AdminConversationExplorer(session).conversation(
            setup.id,
            source="authenticated_setup_chat",
            admin_user_id=admin.id,
            access_reason="Deletion verification",
            request_id=None,
            ip_hash=None,
        )

    assert timeline.summary.display_name == "Deleted user"
    assert timeline.summary.user_id is None
    assert timeline.messages[0].content == "[Content unavailable after account deletion]"


async def test_agent_failure_preserves_domain_state(test_context):
    settings = test_context["settings"]
    settings.system_brain_ai_enabled = True
    settings.openai_api_key = "test-key"
    client = SequencedResponsesClient([{"output_text": "not-json", "output": []}])
    async with test_context["session_factory"]() as session:
        admin = User(display_name="Admin", role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()
        conversation = SystemBrainConversation(admin_user_id=admin.id, title="Failure")
        session.add(conversation)
        await session.commit()
        result = await SystemBrainAgentService(settings, client=client).run_turn(
            session,
            conversation.id,
            admin_user_id=admin.id,
            request=SystemBrainAgentTurnRequest(
                message="Inspect customer revenue",
                client_message_id=f"failure-{uuid4().hex}",
            ),
        )

    assert result.status == "failed"
    assert (
        "No customer, billing, governance, strategy, or production state was changed"
        in result.answer
    )


async def test_public_chat_persists_exact_future_transcript_once(test_context):
    settings = test_context["settings"]
    settings.public_chat_ai_enabled = False
    payload = PublicChatAnswerRequest(
        question="Hello",
        session_id="system-brain-public-transcript-session",
        client_message_id="system-brain-public-message-0001",
        source_page="/",
    )
    async with test_context["session_factory"]() as session:
        service = PublicChatService(session, settings)
        first = await service.answer(payload)
        await session.commit()
        replay = await service.answer(payload)
        await session.commit()
        messages = list(
            (
                await session.scalars(
                    select(PublicChatMessage).order_by(PublicChatMessage.sequence)
                )
            ).all()
        )
        turns = int(await session.scalar(select(func.count(PublicChatTurn.id))) or 0)

    assert first == replay
    assert turns == 1
    assert [(item.role, item.content) for item in messages] == [
        ("user", "Hello"),
        ("assistant", first.message),
    ]
    assert all(
        item.retain_until.replace(tzinfo=item.retain_until.tzinfo or UTC) > datetime.now(UTC)
        for item in messages
    )


async def test_conversation_cursor_pages_do_not_duplicate_or_omit(test_context):
    async with test_context["session_factory"]() as session:
        customer = User(display_name="Trader")
        session.add(customer)
        await session.flush()
        for index in range(7):
            chat = AISetupChatSession(user_id=customer.id, title=f"Chat {index}")
            chat.updated_at = datetime.now(UTC) + timedelta(seconds=index)
            session.add(chat)
            await session.flush()
            session.add(
                AISetupChatMessage(
                    session_id=chat.id,
                    sequence=1,
                    role="user",
                    content=f"message {index}",
                    payload={},
                    created_at=chat.updated_at,
                )
            )
        await session.commit()
        explorer = AdminConversationExplorer(session)
        first = await explorer.list_conversations(source="authenticated_setup_chat", limit=3)
        second = await explorer.list_conversations(
            source="authenticated_setup_chat", limit=3, cursor=first.next_cursor
        )
        third = await explorer.list_conversations(
            source="authenticated_setup_chat", limit=3, cursor=second.next_cursor
        )

    ids = [item.conversation_id for page in (first, second, third) for item in page.items]
    assert len(ids) == 7
    assert len(ids) == len(set(ids))


async def test_live_event_cursor_resumes_once_without_duplicates(test_context):
    now = datetime.now(UTC)
    conversation_id = uuid4()
    message_id = uuid4()
    async with test_context["session_factory"]() as session:
        session.add_all(
            [
                CustomerConversationEvent(
                    id=1,
                    source_type="public_site_chat",
                    conversation_id=conversation_id,
                    event_type="conversation_created",
                    message_id=None,
                    occurred_at=now,
                ),
                CustomerConversationEvent(
                    id=2,
                    source_type="public_site_chat",
                    conversation_id=conversation_id,
                    event_type="message_persisted",
                    message_id=message_id,
                    occurred_at=now + timedelta(milliseconds=1),
                ),
            ]
        )
        await session.commit()
        explorer = AdminConversationExplorer(session)
        initial = await explorer.events(after_id=0, limit=100)
        resumed = await explorer.events(after_id=1, limit=100)
        complete = await explorer.events(after_id=2, limit=100)

    assert [item["event_id"] for item in initial] == [1, 2]
    assert [item["event_id"] for item in resumed] == [2]
    assert complete == []


async def test_repository_index_never_returns_hidden_prompt_text(test_context):
    assert _classification("src/prompts.py", "SYSTEM PROMPT: never expose this") == (
        "restricted_prompt"
    )
    async with test_context["session_factory"]() as session:
        session.add(
            RepositoryEvidenceIndex(
                path="src/prompts.py",
                content_hash="a" * 64,
                updated_commit=None,
                symbol_names=["instructions"],
                searchable_text="SYSTEM PROMPT: never expose this",
                line_references=[],
                sensitivity_classification="restricted_prompt",
                indexed_at=datetime.now(UTC),
            )
        )
        await session.commit()
        result = await RepositoryEvidenceIndexService().search(
            session, query="never expose", limit=5
        )
        excerpt = await RepositoryEvidenceIndexService().excerpt(session, path="src/prompts.py")

    assert result.data == []
    assert excerpt.data is None


async def test_action_proposal_requires_turn_evidence_and_exact_human_binding(
    test_context,
):
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        admin = User(display_name="Admin", role=UserRole.ADMIN)
        customer = User(display_name="Customer")
        session.add_all([admin, customer])
        await session.flush()
        session.add(
            UserIdentity(
                user_id=customer.id,
                provider=IdentityProvider.EMAIL,
                provider_subject="action@example.com",
                normalized_identifier="action@example.com",
                display_identifier="action@example.com",
                is_verified=True,
                is_primary=True,
            )
        )
        conversation = SystemBrainConversation(admin_user_id=admin.id, title="Controlled action")
        session.add(conversation)
        await session.flush()
        request = ActionProposalRequest(
            action="ban_user",
            target_type="user",
            target_id=str(customer.id),
            exact_changes=ActionExactChanges(),
            reason="Confirmed abuse evidence",
            evidence_refs=[f"db:users:{customer.id}"],
            expected_effect="Suspend the selected account",
            risks=["Customer access stops"],
            rollback_path="Use the existing account reinstatement service",
            idempotency_key="system-brain-action-test-0001",
        )
        registry = SystemBrainToolRegistry(settings)
        try:
            await registry.execute(
                session,
                admin_user_id=admin.id,
                conversation_id=conversation.id,
                tool_name="propose_action",
                arguments=request,
                request_id="action-test",
                available_evidence_refs=set(),
                pii_access_allowed=True,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("An uncited consequential proposal was accepted")
        proposal, binding = await SystemBrainActionService(session, settings).propose(
            admin_user_id=admin.id,
            conversation_id=conversation.id,
            request=request,
        )
        assert customer.status == UserStatus.ACTIVE
        try:
            await SystemBrainActionService(session, settings).confirm(
                proposal.id,
                admin_user_id=admin.id,
                confirmation_token="0" * 64,
                human_reason="I reviewed the exact target and evidence",
            )
        except SystemBrainActionError as exc:
            assert exc.code == "proposal_binding_mismatch"
        else:
            raise AssertionError("A mismatched confirmation binding was accepted")
        result = await SystemBrainActionService(session, settings).confirm(
            proposal.id,
            admin_user_id=admin.id,
            confirmation_token=binding,
            human_reason="I reviewed the exact target and evidence",
        )
        await session.commit()

    assert result["action"] == "ban"
    assert customer.status == UserStatus.SUSPENDED


class ConcurrentEvidenceTools(SystemBrainToolRegistry):
    def __init__(self, settings):
        super().__init__(settings)
        self.active = 0
        self.maximum_active = 0

    async def execute(self, session, **kwargs):
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        await asyncio.sleep(0.06)
        self.active -= 1
        name = kwargs["tool_name"]
        return EvidenceEnvelope(
            data={"result": name, "sample_size": 1},
            evidence_refs=[f"metric:{name}:test"],
            freshness="test",
            coverage="one deterministic test row",
            limitations=[],
        )


async def test_independent_read_tools_run_in_parallel_and_writes_remain_bounded(
    test_context,
):
    settings = test_context["settings"]
    settings.system_brain_ai_enabled = True
    settings.openai_api_key = "test-key"
    tools = ConcurrentEvidenceTools(settings)
    refs = ["metric:revenue_summary:test", "metric:trial_conversion:test"]
    client = SequencedResponsesClient(
        [
            {
                "output": [
                    {
                        "type": "function_call",
                        "name": "revenue_summary",
                        "call_id": "parallel-1",
                        "arguments": "{}",
                    },
                    {
                        "type": "function_call",
                        "name": "trial_conversion",
                        "call_id": "parallel-2",
                        "arguments": "{}",
                    },
                ],
                "usage": {"input_tokens": 10, "output_tokens": 3},
            },
            {
                "output_text": json.dumps(
                    {
                        "answer": "Both deterministic metrics were inspected.",
                        "findings": [],
                        "opportunities": [],
                        "suggested_actions": [],
                        "evidence_refs": refs,
                        "limitations": [],
                    }
                ),
                "output": [],
                "usage": {"input_tokens": 20, "output_tokens": 8},
            },
        ]
    )
    async with test_context["session_factory"]() as session:
        admin = User(display_name="Admin", role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()
        conversation = SystemBrainConversation(admin_user_id=admin.id, title="Parallel")
        session.add(conversation)
        await session.commit()
        result = await SystemBrainAgentService(
            settings,
            client=client,
            tools=tools,
            session_factory=test_context["session_factory"],
        ).run_turn(
            session,
            conversation.id,
            admin_user_id=admin.id,
            request=SystemBrainAgentTurnRequest(
                message="Compare revenue and trial conversion",
                client_message_id="parallel-tool-test-0001",
            ),
        )
    assert result.status == "completed"
    assert tools.maximum_active == 2
    assert client.payloads[0]["parallel_tool_calls"] is True


async def test_pii_tools_require_explicit_admin_request_policy(test_context):
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        admin = User(display_name="Admin", role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()
        conversation = SystemBrainConversation(admin_user_id=admin.id, title="PII")
        session.add(conversation)
        await session.flush()
        try:
            await SystemBrainToolRegistry(settings).execute(
                session,
                admin_user_id=admin.id,
                conversation_id=conversation.id,
                tool_name="search_users",
                arguments=SystemBrainToolArguments(query="person@example.com"),
                request_id="pii-policy",
            )
        except ValueError as exc:
            assert "explicit customer" in str(exc)
        else:
            raise AssertionError("A PII tool ran without explicit PII policy authorization")


async def test_unadapted_consequential_action_is_not_persisted(test_context):
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        admin = User(display_name="Admin", role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()
        request = ActionProposalRequest(
            action="send_email",
            target_type="user",
            target_id=str(uuid4()),
            exact_changes=ActionExactChanges(email_subject="Notice"),
            reason="Requested follow-up",
            evidence_refs=["db:support:1"],
            expected_effect="Inform the user",
            risks=["Unwanted contact"],
            rollback_path="No send can be rolled back",
            idempotency_key="unsupported-send-action-0001",
        )
        try:
            await SystemBrainActionService(session, settings).propose(
                admin_user_id=admin.id, conversation_id=None, request=request
            )
        except SystemBrainActionError as exc:
            assert exc.code == "canonical_action_unavailable"
        else:
            raise AssertionError("An action without a canonical adapter was proposed")
        count = int(await session.scalar(select(func.count(SystemBrainActionProposal.id))) or 0)

    assert count == 0


async def test_agent_rejects_model_authored_sharia_ruling(test_context):
    settings = test_context["settings"]
    settings.system_brain_ai_enabled = True
    settings.openai_api_key = "test-key"
    client = SequencedResponsesClient(
        [
            {
                "output_text": json.dumps(
                    {
                        "answer": "BTC is halal.",
                        "findings": [],
                        "opportunities": [],
                        "suggested_actions": [],
                        "evidence_refs": [],
                        "limitations": [],
                    }
                ),
                "output": [],
                "usage": {"input_tokens": 10, "output_tokens": 4},
            }
        ]
    )
    async with test_context["session_factory"]() as session:
        admin = User(display_name="Admin", role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()
        conversation = SystemBrainConversation(admin_user_id=admin.id, title="Boundary")
        session.add(conversation)
        await session.commit()
        result = await SystemBrainAgentService(settings, client=client).run_turn(
            session,
            conversation.id,
            admin_user_id=admin.id,
            request=SystemBrainAgentTurnRequest(
                message="Issue a Sharia ruling",
                client_message_id="sharia-ruling-boundary-0001",
            ),
        )

    assert result.status == "failed"
    assert "BTC is halal" not in result.answer


async def test_persisted_per_admin_budget_blocks_before_provider_call(test_context):
    settings = test_context["settings"]
    settings.system_brain_ai_enabled = True
    settings.openai_api_key = "test-key"
    settings.system_brain_agent_max_turns_per_hour = 1
    client = SequencedResponsesClient([])
    async with test_context["session_factory"]() as session:
        admin = User(display_name="Admin", role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()
        conversation = SystemBrainConversation(admin_user_id=admin.id, title="Budget")
        session.add(conversation)
        session.add(
            AgentRun(
                user_id=admin.id,
                chat_session_id=None,
                model="test",
                reasoning_effort="low",
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
                status="completed",
                correlation_id=uuid4().hex,
                final_intent="system_brain",
            )
        )
        await session.commit()
        try:
            await SystemBrainAgentService(settings, client=client).run_turn(
                session,
                conversation.id,
                admin_user_id=admin.id,
                request=SystemBrainAgentTurnRequest(
                    message="Show revenue",
                    client_message_id="budget-blocked-turn-0001",
                ),
            )
        except SystemBrainAgentUnavailable:
            pass
        else:
            raise AssertionError("The persisted per-admin rate budget was bypassed")
        audit = await session.scalar(
            select(AuditEvent).where(AuditEvent.action == "system_brain.agent.budget_blocked")
        )

    assert client.payloads == []
    assert audit is not None
