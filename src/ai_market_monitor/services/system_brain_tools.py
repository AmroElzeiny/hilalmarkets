from __future__ import annotations

import csv
import io
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AISetupChatSession,
    AIUsageEvent,
    AlertDelivery,
    AttributionTouch,
    AuditEvent,
    BillingCheckoutAttempt,
    BillingEvent,
    CanonicalAsset,
    IntegrationHealth,
    MonitorHealthSummary,
    Plan,
    PublicChatAnswerEvent,
    PublicChatAnswerFeedback,
    ReferralRelationship,
    RepositoryEvidenceIndex,
    ReviewCase,
    SetupChatOperationalIssue,
    SetupChatTurn,
    ShariaMonitoringRun,
    SourceSnapshot,
    Strategy,
    Subscription,
    SupportRequest,
    SystemBrainArtifact,
    Trial,
    UsageRecord,
    User,
    UserFeedback,
    UserIdentity,
    WaitlistSignup,
)
from ai_market_monitor.db.models.enums import IdentityProvider, UserRole
from ai_market_monitor.schemas.system_brain import (
    CONVERSATION_SOURCE_LABELS,
    ActionProposalRequest,
    EvidenceEnvelope,
    InternalArtifactRequest,
    SystemBrainToolArguments,
)
from ai_market_monitor.services.agent_tools import strict_json_schema
from ai_market_monitor.services.sharia_admin_dashboard import ShariaAdminDashboardService
from ai_market_monitor.services.system_brain_actions import SystemBrainActionService
from ai_market_monitor.services.system_brain_conversations import AdminConversationExplorer
from ai_market_monitor.services.system_brain_privacy import redact_customer_text
from ai_market_monitor.services.system_brain_repository_index import (
    RepositoryEvidenceIndexService,
)

CUSTOMER_PRODUCT_TOOLS = (
    "search_users",
    "inspect_user_profile",
    "list_customer_conversations",
    "inspect_customer_conversation",
    "inspect_setup_chat_failures",
    "inspect_setup_funnel",
    "inspect_feature_usage",
    "inspect_monitor_health",
    "inspect_alert_delivery",
    "inspect_support_activity",
)
QUALITY_TOOLS = (
    "setup_chat_quality_metrics",
    "top_failed_intents",
    "clarification_failure_analysis",
    "latency_breakdown",
    "model_cost_analysis",
    "user_feedback_analysis",
    "knowledge_gap_analysis",
    "release_comparison",
    "cost_per_successful_setup_chat",
    "setup_chat_approval_analysis",
    "support_driver_ranking",
    "high_value_failure_clusters",
    "experiment_impact",
)
REVENUE_TOOLS = (
    "revenue_summary",
    "subscription_funnel",
    "trial_conversion",
    "trial_activation",
    "plan_distribution",
    "churn_and_expiry",
    "cohort_retention",
    "referral_performance",
    "waitlist_conversion",
    "attribution_performance",
    "revenue_by_feature_usage",
    "feature_conversion_analysis",
    "high_intent_unconverted_users",
)
GOVERNANCE_OPERATIONS_TOOLS = (
    "review_queue_summary",
    "inspect_review_case",
    "source_health",
    "screening_health",
    "delivery_failures",
    "worker_health",
    "audit_search",
)
ENGINEERING_TOOLS = (
    "repository_search",
    "inspect_file_excerpt",
    "recent_release_changes",
    "error_frequency",
    "configuration_read",
)
SAFE_ACTION_TOOLS = (
    "save_internal_report",
    "create_internal_insight",
    "create_admin_task",
    "save_filtered_conversation_view",
    "export_bounded_csv",
    "draft_email",
    "draft_experiment_plan",
    "attach_internal_note",
)
ALL_READ_TOOLS = (
    *CUSTOMER_PRODUCT_TOOLS,
    *QUALITY_TOOLS,
    *REVENUE_TOOLS,
    *GOVERNANCE_OPERATIONS_TOOLS,
    *ENGINEERING_TOOLS,
)
ALL_SYSTEM_BRAIN_TOOLS = (*ALL_READ_TOOLS, *SAFE_ACTION_TOOLS, "propose_action")
PII_TOOLS = frozenset(
    {
        "search_users",
        "inspect_user_profile",
        "list_customer_conversations",
        "inspect_customer_conversation",
        "high_intent_unconverted_users",
        "propose_action",
        "attach_internal_note",
        "draft_email",
    }
)

TOOL_DESCRIPTIONS = {
    name: f"Return bounded authoritative System Brain evidence for {name.replace('_', ' ')}."
    for name in ALL_READ_TOOLS
}
TOOL_DESCRIPTIONS.update(
    {
        name: (
            f"Persist a reversible internal-only artifact for {name.replace('_', ' ')}; "
            "never contact customers or alter domain state."
        )
        for name in SAFE_ACTION_TOOLS
    }
)
TOOL_DESCRIPTIONS["propose_action"] = (
    "Create an exact consequential action proposal. This never executes the action; "
    "a separately authenticated human confirmation is mandatory."
)


class SystemBrainToolRegistry:
    """Typed bounded adapters over existing production data and services."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def openai_tools(self, names: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        offered = names or ALL_SYSTEM_BRAIN_TOOLS
        output = []
        for name in offered:
            model: type[BaseModel]
            if name == "propose_action":
                model = ActionProposalRequest
            elif name in SAFE_ACTION_TOOLS:
                model = InternalArtifactRequest
            else:
                model = SystemBrainToolArguments
            output.append(
                {
                    "type": "function",
                    "name": name,
                    "description": TOOL_DESCRIPTIONS[name],
                    "parameters": strict_json_schema(model),
                    "strict": True,
                }
            )
        return output

    def parse_arguments(self, tool_name: str, raw: dict[str, Any]) -> BaseModel:
        if tool_name not in ALL_SYSTEM_BRAIN_TOOLS:
            raise ValueError("Unknown System Brain tool.")
        if tool_name == "propose_action":
            return ActionProposalRequest.model_validate(raw)
        if tool_name in SAFE_ACTION_TOOLS:
            return InternalArtifactRequest.model_validate(raw)
        return SystemBrainToolArguments.model_validate(raw)

    async def execute(
        self,
        session: AsyncSession,
        *,
        admin_user_id: UUID,
        conversation_id: UUID,
        tool_name: str,
        arguments: BaseModel,
        request_id: str,
        available_evidence_refs: set[str] | None = None,
        pii_access_allowed: bool = False,
    ) -> EvidenceEnvelope:
        await _require_admin_tool_principal(session, admin_user_id)
        if tool_name in PII_TOOLS and not pii_access_allowed:
            raise ValueError(
                "This tool requires an explicit customer, user, conversation, profile, "
                "or email request."
            )
        if tool_name in SAFE_ACTION_TOOLS:
            artifact_request = InternalArtifactRequest.model_validate(arguments.model_dump())
            _require_authorized_evidence(artifact_request.evidence_refs, available_evidence_refs)
            return await self._safe_artifact(
                session,
                admin_user_id=admin_user_id,
                conversation_id=conversation_id,
                kind=tool_name,
                arguments=artifact_request,
            )
        if tool_name == "propose_action":
            action_request = ActionProposalRequest.model_validate(arguments.model_dump())
            _require_authorized_evidence(action_request.evidence_refs, available_evidence_refs)
            proposal, binding = await SystemBrainActionService(session, self.settings).propose(
                admin_user_id=admin_user_id,
                conversation_id=conversation_id,
                request=action_request,
            )
            return _envelope(
                {
                    "proposal_id": str(proposal.id),
                    "action": proposal.action,
                    "target": f"{proposal.target_type}:{proposal.target_id}",
                    "exact_changes": proposal.exact_changes,
                    "reason": proposal.reason,
                    "evidence": proposal.evidence_refs,
                    "expected_effect": proposal.expected_effect,
                    "risks": proposal.risks,
                    "rollback_path": proposal.rollback_path,
                    "idempotency_key": proposal.idempotency_key,
                    "status": proposal.status,
                    "confirmation_binding": binding,
                    "expires_at": proposal.expires_at.isoformat(),
                },
                [f"action-proposal:{proposal.id}"],
                coverage="exact proposal persisted; zero domain mutation",
            )
        args = SystemBrainToolArguments.model_validate(arguments.model_dump())
        if tool_name in CUSTOMER_PRODUCT_TOOLS:
            return await self._customer_tool(
                session,
                tool_name,
                args,
                admin_user_id=admin_user_id,
                request_id=request_id,
            )
        if tool_name in QUALITY_TOOLS:
            return await self._quality_tool(session, tool_name, args)
        if tool_name in REVENUE_TOOLS:
            return await self._revenue_tool(session, tool_name, args)
        if tool_name in GOVERNANCE_OPERATIONS_TOOLS:
            return await self._governance_tool(session, tool_name, args)
        if tool_name in ENGINEERING_TOOLS:
            return await self._engineering_tool(session, tool_name, args)
        raise ValueError("Unknown System Brain tool.")

    async def _customer_tool(
        self,
        session: AsyncSession,
        name: str,
        args: SystemBrainToolArguments,
        *,
        admin_user_id: UUID,
        request_id: str,
    ) -> EvidenceEnvelope:
        if name == "search_users":
            query = (args.query or "").strip()
            statement = select(User).order_by(User.created_at.desc()).limit(args.limit)
            if query:
                identities = select(UserIdentity.user_id).where(
                    UserIdentity.provider == IdentityProvider.EMAIL,
                    or_(
                        UserIdentity.normalized_identifier.ilike(f"%{query}%"),
                        UserIdentity.display_identifier.ilike(f"%{query}%"),
                    ),
                )
                clauses: list[Any] = [
                    User.display_name.ilike(f"%{query}%"),
                    User.id.in_(identities),
                ]
                with suppress(ValueError):
                    clauses.append(User.id == UUID(query))
                statement = statement.where(or_(*clauses))
            users = list((await session.scalars(statement)).all())
            emails = await _authoritative_email_map(session, {item.id for item in users})
            data = []
            for user in users:
                data.append(
                    {
                        "user_id": str(user.id),
                        "email": emails.get(user.id),
                        "display_name": user.display_name,
                        "status": _value(user.status),
                        "role": _value(user.role),
                        "created_at": user.created_at.isoformat(),
                        "last_seen_at": user.last_seen_at.isoformat()
                        if user.last_seen_at
                        else None,
                    }
                )
            return _envelope(
                data,
                [f"db:users:{item['user_id']}" for item in data],
                coverage=f"{len(data)} users, bounded to {args.limit}",
            )
        if name == "inspect_user_profile":
            if args.user_id is None:
                return _missing("user_id is required")
            inspected_user = await session.get(User, args.user_id)
            if inspected_user is None:
                return _missing("User not found")
            counts = {
                "setup_chats": await _count(
                    session,
                    AISetupChatSession,
                    AISetupChatSession.user_id == inspected_user.id,
                ),
                "strategies": await _count(
                    session, Strategy, Strategy.user_id == inspected_user.id
                ),
                "support_requests": await _count(
                    session,
                    SupportRequest,
                    SupportRequest.user_id == inspected_user.id,
                ),
                "subscriptions": await _count(
                    session,
                    Subscription,
                    Subscription.user_id == inspected_user.id,
                ),
                "trials": await _count(session, Trial, Trial.user_id == inspected_user.id),
            }
            return _envelope(
                {
                    "user_id": str(inspected_user.id),
                    "email": await _authoritative_email(session, inspected_user.id),
                    "display_name": inspected_user.display_name,
                    "status": _value(inspected_user.status),
                    "role": _value(inspected_user.role),
                    "created_at": inspected_user.created_at.isoformat(),
                    "last_seen_at": (
                        inspected_user.last_seen_at.isoformat()
                        if inspected_user.last_seen_at
                        else None
                    ),
                    "counts": counts,
                },
                [
                    f"db:users:{inspected_user.id}",
                    f"db:user-profile:{inspected_user.id}",
                ],
                coverage="authoritative profile plus bounded relationship counts",
            )
        if name == "list_customer_conversations":
            page = await AdminConversationExplorer(session).list_conversations(
                search=args.query,
                source=args.source,
                lifecycle=args.lifecycle,
                error_only=args.error_only,
                date_from=args.date_from,
                date_to=args.date_to,
                identity=args.identity,
                approval_state=args.approval_state,
                cursor=args.cursor,
                limit=args.limit,
            )
            session.add(
                AuditEvent(
                    actor_user_id=admin_user_id,
                    actor_type="system_brain_admin",
                    action="system_brain.customer_conversation.list",
                    target_type="customer_conversation_page",
                    target_id=None,
                    request_id=request_id,
                    ip_hash=None,
                    metadata_redacted={
                        "access_category": "agent_evidence_request",
                        "conversation_ids": [str(item.conversation_id) for item in page.items],
                        "result_count": len(page.items),
                    },
                    created_at=datetime.now(UTC),
                )
            )
            return _envelope(
                page.model_dump(mode="json"),
                [f"conversation:{item.source_type}:{item.conversation_id}" for item in page.items],
                coverage=f"{len(page.items)} conversations with cursor pagination",
            )
        if name == "inspect_customer_conversation":
            if args.conversation_id is None:
                return _missing("conversation_id is required")
            source = args.source
            if source is None:
                return _missing("source filter is required for exact conversation inspection")
            timeline = await AdminConversationExplorer(session).conversation(
                args.conversation_id,
                source=source,
                admin_user_id=admin_user_id,
                access_reason="System Brain agent evidence request",
                request_id=request_id,
                ip_hash=None,
            )
            return _envelope(
                timeline.model_dump(mode="json"),
                [f"conversation:{source}:{args.conversation_id}"],
                coverage=(
                    "complete persisted transcript"
                    if timeline.transcript_complete
                    else "persisted future messages only"
                ),
                limitations=[timeline.transcript_limitation]
                if timeline.transcript_limitation
                else [],
            )
        if name in {"inspect_setup_chat_failures", "inspect_setup_funnel"}:
            return await self._setup_metrics(session, name, args)
        if name == "inspect_feature_usage":
            rows = await _group_count(session, UsageRecord.metric, UsageRecord.created_at, args)
            return _metric_envelope(name, rows, args, "count of persisted usage records by feature")
        if name == "inspect_monitor_health":
            health_rows = list(
                (
                    await session.scalars(
                        select(MonitorHealthSummary)
                        .order_by(MonitorHealthSummary.calculated_at.desc())
                        .limit(args.limit)
                    )
                ).all()
            )
            data = [
                {
                    "strategy_id": str(row.strategy_id),
                    "technical_status": row.technical_status,
                    "strategy_status": row.strategy_status,
                    "at": row.calculated_at.isoformat(),
                }
                for row in health_rows
            ]
            return _envelope(
                data,
                [f"db:monitor_health_summaries:{row.id}" for row in health_rows],
                coverage=f"latest {len(health_rows)} health summaries",
            )
        if name == "inspect_alert_delivery":
            rows = await _group_count(session, AlertDelivery.status, AlertDelivery.created_at, args)
            return _metric_envelope(
                name, rows, args, "count of persisted alert deliveries by status"
            )
        rows = await _group_count(session, SupportRequest.category, SupportRequest.created_at, args)
        return _metric_envelope(name, rows, args, "count of persisted support requests by category")

    async def _quality_tool(
        self, session: AsyncSession, name: str, args: SystemBrainToolArguments
    ) -> EvidenceEnvelope:
        if name == "cost_per_successful_setup_chat":
            completed = int(
                await session.scalar(
                    _apply_range(
                        select(func.count(SetupChatTurn.id)).where(
                            SetupChatTurn.status.in_(["COMPLETED", "completed"])
                        ),
                        SetupChatTurn.created_at,
                        args,
                    )
                )
                or 0
            )
            cost = Decimal(
                await session.scalar(
                    _apply_range(
                        select(func.coalesce(func.sum(AIUsageEvent.estimated_cost_usd), 0)).where(
                            AIUsageEvent.chat_session_id.is_not(None)
                        ),
                        AIUsageEvent.created_at,
                        args,
                    )
                )
                or 0
            )
            return _envelope(
                {
                    "formula": (
                        "sum of immutable Setup Chat AI usage cost / completed persisted "
                        "Setup Chat turns"
                    ),
                    "date_range": _range_label(args),
                    "sample_size": completed,
                    "included_population": (
                        "completed authenticated Setup Chat turns and their recorded AI usage"
                    ),
                    "exclusions": ["Missing usage events are not estimated"],
                    "result": {
                        "completed_turns": completed,
                        "recorded_cost_usd": str(cost),
                        "cost_per_completed_turn_usd": str(
                            cost / completed if completed else Decimal("0")
                        ),
                    },
                    "confidence_limitations": (
                        [] if completed else ["No completed turns exist in the selected range."]
                    ),
                },
                [f"metric:{name}:{_range_label(args)}"],
                coverage=f"{completed} completed turns",
            )
        if name == "setup_chat_approval_analysis":
            sessions = await _count_in_range(
                session, AISetupChatSession, AISetupChatSession.created_at, args
            )
            approved = int(
                await session.scalar(
                    _apply_range(
                        select(func.count(AISetupChatSession.id)).where(
                            AISetupChatSession.approved_at.is_not(None)
                        ),
                        AISetupChatSession.created_at,
                        args,
                    )
                )
                or 0
            )
            return _metric_contract_envelope(
                name,
                args,
                sample_size=sessions,
                formula="approved Setup Chat sessions / all Setup Chat sessions created in range",
                result={
                    "sessions": sessions,
                    "approved_sessions": approved,
                    "approval_rate": _rate(approved, sessions),
                },
                limitations=[
                    "Approval is an authenticated product action; this descriptive rate "
                    "does not measure strategy quality or causation."
                ],
            )
        if name == "support_driver_ranking":
            rows = await _group_count(
                session, SupportRequest.category, SupportRequest.created_at, args
            )
            return _metric_contract_envelope(
                name,
                args,
                sample_size=sum(int(row["count"]) for row in rows),
                formula="persisted support requests grouped and ranked by recorded category",
                result=rows,
                limitations=["Free-text support content is not inferred into new categories."],
            )
        if name == "high_value_failure_clusters":
            paid_users = (
                select(Subscription.user_id)
                .where(Subscription.status.in_(["active", "trialing"]))
                .distinct()
                .subquery()
            )
            cluster_statement = (
                select(
                    SetupChatTurn.failure_code,
                    func.count(SetupChatTurn.id),
                    func.count(func.distinct(paid_users.c.user_id)),
                )
                .join(
                    AISetupChatSession,
                    AISetupChatSession.id == SetupChatTurn.chat_session_id,
                )
                .outerjoin(paid_users, paid_users.c.user_id == AISetupChatSession.user_id)
                .where(SetupChatTurn.failure_code.is_not(None))
                .group_by(SetupChatTurn.failure_code)
                .order_by(func.count(func.distinct(paid_users.c.user_id)).desc())
                .limit(args.limit)
            )
            cluster_rows = (
                await session.execute(
                    _apply_range(cluster_statement, SetupChatTurn.created_at, args)
                )
            ).all()
            result = [
                {
                    "failure_code": row[0],
                    "turns": int(row[1]),
                    "paid_or_trialing_users": int(row[2]),
                }
                for row in cluster_rows
            ]
            return _metric_contract_envelope(
                name,
                args,
                sample_size=sum(row["turns"] for row in result),
                formula=(
                    "failed Setup Chat turns grouped by typed failure code with distinct "
                    "active/trialing users"
                ),
                result=result,
                limitations=[
                    "Plan association ranks affected accounts; it is not realized revenue loss."
                ],
            )
        if name == "experiment_impact":
            return _metric_contract_envelope(
                name,
                args,
                sample_size=0,
                formula="requires persisted product-experiment assignment and outcome records",
                result=None,
                limitations=[
                    "Growth experiment assignment/outcome instrumentation is not persisted; "
                    "missing evidence is not zero impact."
                ],
            )
        if name in {
            "setup_chat_quality_metrics",
            "top_failed_intents",
            "clarification_failure_analysis",
            "latency_breakdown",
            "release_comparison",
        }:
            return await self._setup_metrics(session, name, args)
        if name == "model_cost_analysis":
            cost_statement = (
                select(
                    AIUsageEvent.model,
                    func.count(AIUsageEvent.id),
                    func.sum(AIUsageEvent.estimated_cost_usd),
                    func.sum(AIUsageEvent.input_tokens),
                    func.sum(AIUsageEvent.output_tokens),
                )
                .group_by(AIUsageEvent.model)
                .order_by(func.sum(AIUsageEvent.estimated_cost_usd).desc())
                .limit(args.limit)
            )
            cost_statement = _apply_range(cost_statement, AIUsageEvent.created_at, args)
            cost_rows = (await session.execute(cost_statement)).all()
            data = [
                {
                    "model": row[0],
                    "calls": int(row[1]),
                    "cost_usd": str(row[2] or 0),
                    "input_tokens": int(row[3] or 0),
                    "output_tokens": int(row[4] or 0),
                }
                for row in cost_rows
            ]
            return _metric_envelope(
                name, data, args, "sum of immutable AI usage events grouped by model"
            )
        if name == "user_feedback_analysis":
            feedback = await _group_count(
                session, UserFeedback.feedback_type, UserFeedback.created_at, args
            )
            public = await _group_count(
                session, PublicChatAnswerFeedback.helpful, PublicChatAnswerFeedback.created_at, args
            )
            return _metric_envelope(
                name,
                {"product_feedback": feedback, "public_chat_helpful": public},
                args,
                "count of persisted feedback records",
            )
        gap_rows = await _group_count(
            session,
            PublicChatAnswerEvent.knowledge_gap_reason,
            PublicChatAnswerEvent.created_at,
            args,
            exclude_null=True,
        )
        return _metric_envelope(
            name,
            gap_rows,
            args,
            "count of retained public answer events with a knowledge gap",
        )

    async def _revenue_tool(
        self, session: AsyncSession, name: str, args: SystemBrainToolArguments
    ) -> EvidenceEnvelope:
        users = await _count_in_range(session, User, User.created_at, args)
        trials = await _count_in_range(session, Trial, Trial.created_at, args)
        subscriptions = await _count_in_range(session, Subscription, Subscription.created_at, args)
        subscribing_users = int(
            await session.scalar(
                _apply_range(
                    select(func.count(func.distinct(Subscription.user_id))),
                    Subscription.created_at,
                    args,
                )
            )
            or 0
        )
        converted_trials = int(
            await session.scalar(
                _apply_range(
                    select(func.count(Trial.id)).where(
                        Trial.converted_subscription_id.is_not(None)
                    ),
                    Trial.created_at,
                    args,
                )
            )
            or 0
        )
        completed_checkouts = int(
            await session.scalar(
                _apply_range(
                    select(func.count(BillingCheckoutAttempt.id)).where(
                        BillingCheckoutAttempt.status == "completed"
                    ),
                    BillingCheckoutAttempt.created_at,
                    args,
                )
            )
            or 0
        )
        common = {
            "formula": (
                "counts use persisted users, trials, subscriptions and completed checkout "
                "attempts"
            ),
            "date_range": _range_label(args),
            "sample_size": users,
            "included_population": "persisted non-anonymized account and billing records in range",
            "exclusions": [
                "No inferred revenue from uncompleted checkout attempts",
                "Provider fees and refunds are unavailable unless recorded",
            ],
        }
        if name == "revenue_summary":
            statement = (
                select(
                    Plan.code,
                    func.count(Subscription.id),
                    func.sum(Plan.price_monthly),
                )
                .join(Subscription, Subscription.plan_id == Plan.id)
                .where(Subscription.status.in_(["active", "trialing"]))
                .group_by(Plan.code)
            )
            revenue_rows = (await session.execute(statement)).all()
            data = {
                **common,
                "result": {
                    "active_plan_mrr_estimate": [
                        {
                            "plan": row[0],
                            "subscriptions": int(row[1]),
                            "monthly_list_price_usd": str(row[2] or 0),
                        }
                        for row in revenue_rows
                    ],
                    "completed_checkouts": completed_checkouts,
                },
                "confidence_limitations": [
                    "List-price MRR is not recognized revenue and excludes discounts, "
                    "refunds, taxes and provider settlement."
                ],
            }
        elif name in {"subscription_funnel", "trial_conversion"}:
            data = {
                **common,
                "result": {
                    "registered_users": users,
                    "trials": trials,
                    "subscription_records": subscriptions,
                    "subscribing_users": subscribing_users,
                    "converted_trials": converted_trials,
                    "completed_checkouts": completed_checkouts,
                    "trial_conversion_rate": _rate(converted_trials, trials),
                    "registration_to_subscription_rate": _rate(subscribing_users, users),
                },
                "confidence_limitations": [
                    "Subscription records are observational; the calculation does not "
                    "establish attribution."
                ],
            }
        elif name == "trial_activation":
            activation_statement = (
                select(func.count(func.distinct(Trial.id)))
                .join(UsageRecord, UsageRecord.user_id == Trial.user_id)
                .where(
                    UsageRecord.created_at >= Trial.starts_at,
                    UsageRecord.created_at <= Trial.ends_at,
                    UsageRecord.quantity > 0,
                )
            )
            activation_statement = _apply_range(activation_statement, Trial.created_at, args)
            activated = int(await session.scalar(activation_statement) or 0)
            data = {
                **common,
                "result": {
                    "trials": trials,
                    "activated_by_recorded_feature_usage": activated,
                    "converted": converted_trials,
                    "activation_rate": _rate(activated, trials),
                    "conversion_rate": _rate(converted_trials, trials),
                },
                "confidence_limitations": [
                    "Activation means at least one positive persisted usage record during "
                    "the trial window; missing instrumentation is not treated as zero activity."
                ],
            }
        elif name == "plan_distribution":
            plan_rows = (
                await session.execute(
                    select(Plan.code, Subscription.status, func.count(Subscription.id))
                    .join(Subscription, Subscription.plan_id == Plan.id)
                    .group_by(Plan.code, Subscription.status)
                )
            ).all()
            data = {
                **common,
                "result": [
                    {"plan": row[0], "status": _value(row[1]), "count": int(row[2])}
                    for row in plan_rows
                ],
                "confidence_limitations": [],
            }
        elif name == "churn_and_expiry":
            churn_rows = await _group_count(
                session, Subscription.status, Subscription.created_at, args
            )
            data = {
                **common,
                "result": churn_rows,
                "confidence_limitations": ["Status counts are not a causal churn model."],
            }
        elif name == "cohort_retention":
            retained_30 = int(
                await session.scalar(
                    select(func.count(User.id)).where(
                        User.created_at <= datetime.now(UTC) - timedelta(days=30),
                        User.last_seen_at >= User.created_at + timedelta(days=30),
                    )
                )
                or 0
            )
            eligible_30 = int(
                await session.scalar(
                    select(func.count(User.id)).where(
                        User.created_at <= datetime.now(UTC) - timedelta(days=30)
                    )
                )
                or 0
            )
            data = {
                **common,
                "result": {
                    "eligible_30_day_cohort": eligible_30,
                    "retained_at_30_days": retained_30,
                    "retention_rate": _rate(retained_30, eligible_30),
                },
                "confidence_limitations": [
                    "last_seen_at measures product return, not value realization."
                ],
            }
        elif name == "referral_performance":
            total = await _count_in_range(
                session, ReferralRelationship, ReferralRelationship.created_at, args
            )
            converted = int(
                await session.scalar(
                    _apply_range(
                        select(func.count(ReferralRelationship.id)).where(
                            ReferralRelationship.reward_status.in_(
                                ["eligible_after_first_paid_month", "granted"]
                            )
                        ),
                        ReferralRelationship.created_at,
                        args,
                    )
                )
                or 0
            )
            data = {
                **common,
                "result": {
                    "referrals": total,
                    "converted": converted,
                    "conversion_rate": _rate(converted, total),
                },
                "confidence_limitations": [],
            }
        elif name == "waitlist_conversion":
            waitlist = await _count_in_range(
                session, WaitlistSignup, WaitlistSignup.created_at, args
            )
            data = {
                **common,
                "result": {
                    "waitlist_signups": waitlist,
                    "registered_users": users,
                    "upper_bound_conversion_rate": _rate(users, waitlist),
                },
                "confidence_limitations": [
                    "There is no guaranteed identity link between every waitlist signup "
                    "and account; this is an upper bound, not attribution."
                ],
            }
        elif name == "attribution_performance":
            attribution_rows = await _group_count(
                session, AttributionTouch.source, AttributionTouch.created_at, args
            )
            data = {
                **common,
                "result": attribution_rows,
                "confidence_limitations": ["Touch counts do not prove incremental acquisition."],
            }
        elif name in {"revenue_by_feature_usage", "feature_conversion_analysis"}:
            feature_users = (
                _apply_range(
                    select(
                        UsageRecord.metric.label("metric"),
                        UsageRecord.user_id.label("user_id"),
                        func.sum(UsageRecord.quantity).label("quantity"),
                    ),
                    UsageRecord.created_at,
                    args,
                )
                .group_by(UsageRecord.metric, UsageRecord.user_id)
                .subquery()
            )
            active_plan = (
                select(
                    Subscription.user_id.label("user_id"),
                    func.max(Plan.price_monthly).label("monthly_list_price"),
                )
                .join(Plan, Subscription.plan_id == Plan.id)
                .where(Subscription.status.in_(["active", "trialing"]))
                .group_by(Subscription.user_id)
                .subquery()
            )
            feature_rows = (
                await session.execute(
                    select(
                        feature_users.c.metric,
                        func.count(feature_users.c.user_id),
                        func.count(active_plan.c.user_id),
                        func.coalesce(func.sum(active_plan.c.monthly_list_price), 0),
                        func.coalesce(func.sum(feature_users.c.quantity), 0),
                    )
                    .outerjoin(active_plan, active_plan.c.user_id == feature_users.c.user_id)
                    .group_by(feature_users.c.metric)
                    .order_by(func.count(feature_users.c.user_id).desc())
                    .limit(args.limit)
                )
            ).all()
            data = {
                **common,
                "formula": (
                    "distinct feature users joined to active/trialing subscription list "
                    "price by authoritative user_id"
                ),
                "result": [
                    {
                        "feature": row[0],
                        "users": int(row[1]),
                        "paid_or_trialing_users": int(row[2]),
                        "associated_monthly_list_price_usd": str(row[3] or 0),
                        "recorded_usage_quantity": int(row[4]),
                    }
                    for row in feature_rows
                ],
                "confidence_limitations": [
                    "This is an observational association using list price, not recognized "
                    "revenue or causal attribution; discounts, refunds, taxes, settlement "
                    "and uninstrumented usage remain excluded."
                ],
            }
        else:
            subscribed = select(Subscription.user_id).where(
                Subscription.status.in_(["active", "trialing"])
            )
            high_intent_statement = (
                select(User)
                .where(User.id.not_in(subscribed))
                .order_by(User.last_seen_at.desc().nullslast())
                .limit(args.limit)
            )
            high_intent_rows = list((await session.scalars(high_intent_statement)).all())
            result = [
                {
                    "user_id": str(row.id),
                    "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
                    "setup_chats": await _count(
                        session, AISetupChatSession, AISetupChatSession.user_id == row.id
                    ),
                }
                for row in high_intent_rows
            ]
            data = {
                **common,
                "result": result,
                "confidence_limitations": [
                    "High intent is approximated by recent activity and Setup Chat use; "
                    "no purchase intent is inferred."
                ],
            }
        confidence_limitations = data.get("confidence_limitations")
        if users == 0 and isinstance(confidence_limitations, list):
            confidence_limitations.append(
                "No account records exist in the selected range; missing data is not a "
                "measured zero outcome."
            )
        return _envelope(
            data,
            [f"metric:{name}:{_range_label(args)}"],
            coverage=f"sample size {data.get('sample_size', 0)}",
            limitations=(
                [str(item) for item in confidence_limitations]
                if isinstance(confidence_limitations, list)
                else []
            ),
        )

    async def _governance_tool(
        self, session: AsyncSession, name: str, args: SystemBrainToolArguments
    ) -> EvidenceEnvelope:
        dashboard = ShariaAdminDashboardService(session)
        if name == "review_queue_summary":
            data = await dashboard.reviewer_overview()
            return _envelope(
                data, ["db:review_cases:overview"], coverage="current governed review queue"
            )
        if name == "inspect_review_case":
            wanted = (args.target_id or args.query or "").strip()
            if not wanted:
                return _missing("target_id is required: a case reference or its id")
            # A case reference is what a person can actually see. It is printed on every
            # row of the Cases page, in every refusal message and in the address bar; the
            # internal id is printed nowhere. Accepting only the id meant the one question
            # the assistant is asked most — "what does IMP-FASSET-170-HTX want?" — could
            # not be answered at all.
            case_id = await _case_id_for(session, wanted)
            if case_id is None:
                return _missing(f"no review case is recorded as {wanted[:60]}")
            data = await dashboard.case_detail(case_id)
            return _envelope(
                data, [f"db:review_cases:{case_id}"], coverage="one exact persisted review case"
            )
        if name == "source_health":
            rows = await _group_count(
                session, SourceSnapshot.fetch_status, SourceSnapshot.retrieved_at, args
            )
            return _metric_envelope(name, rows, args, "source snapshots grouped by fetch status")
        if name == "screening_health":
            assets = int(await session.scalar(select(func.count(CanonicalAsset.id))) or 0)
            cases = int(await session.scalar(select(func.count(ReviewCase.id))) or 0)
            return _envelope(
                {"canonical_assets": assets, "review_cases": cases},
                ["db:canonical_assets:count", "db:review_cases:count"],
                coverage="current persisted screening inventory",
            )
        if name == "delivery_failures":
            rows = await _group_count(session, AlertDelivery.status, AlertDelivery.created_at, args)
            return _metric_envelope(name, rows, args, "alert delivery records by status")
        if name == "worker_health":
            integrations = await _group_count(
                session, IntegrationHealth.status, IntegrationHealth.checked_at, args
            )
            runs = await _group_count(
                session, ShariaMonitoringRun.status, ShariaMonitoringRun.created_at, args
            )
            return _metric_envelope(
                name,
                {"integrations": integrations, "sharia_runs": runs},
                args,
                "persisted integration and worker-run health",
            )
        statement = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(args.limit)
        statement = _apply_range(statement, AuditEvent.created_at, args)
        if args.query:
            statement = statement.where(
                or_(
                    AuditEvent.action.ilike(f"%{args.query}%"),
                    AuditEvent.target_type.ilike(f"%{args.query}%"),
                )
            )
        audit_rows = list((await session.scalars(statement)).all())
        audit_data = [
            {
                "event_id": str(row.id),
                "action": row.action,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "at": row.created_at.isoformat(),
            }
            for row in audit_rows
        ]
        return _envelope(
            audit_data,
            [f"db:audit_events:{row.id}" for row in audit_rows],
            coverage=f"latest {len(audit_rows)} matching audit events",
        )

    async def _engineering_tool(
        self, session: AsyncSession, name: str, args: SystemBrainToolArguments
    ) -> EvidenceEnvelope:
        index = RepositoryEvidenceIndexService()
        if name == "repository_search":
            return await index.search(session, query=args.query or "", limit=args.limit)
        if name == "inspect_file_excerpt":
            path = args.path or args.query or ""
            return await index.excerpt(session, path=path, line=args.line, lines=args.lines)
        if name == "recent_release_changes":
            rows = list(
                (
                    await session.scalars(
                        select(RepositoryEvidenceIndex)
                        .order_by(RepositoryEvidenceIndex.indexed_at.desc())
                        .limit(args.limit)
                    )
                ).all()
            )
            data = [
                {
                    "path": row.path,
                    "content_hash": row.content_hash,
                    "updated_commit": row.updated_commit,
                    "indexed_at": row.indexed_at.isoformat(),
                }
                for row in rows
            ]
            return _envelope(
                data,
                [f"repo:{row.path}:1" for row in rows],
                coverage=f"{len(rows)} most recently indexed files",
                limitations=["This is index change evidence, not a deployment log."],
            )
        if name == "error_frequency":
            setup = await _group_count(
                session,
                SetupChatTurn.failure_code,
                SetupChatTurn.created_at,
                args,
                exclude_null=True,
            )
            issues = await _group_count(
                session,
                SetupChatOperationalIssue.failure_class,
                SetupChatOperationalIssue.first_seen_at,
                args,
            )
            billing = await _group_count(
                session, BillingEvent.error_code, BillingEvent.created_at, args, exclude_null=True
            )
            return _metric_envelope(
                name,
                {"setup_chat": setup, "operational_issues": issues, "billing": billing},
                args,
                "typed persisted error records grouped by class",
            )
        safe = {
            "environment": self.settings.app_env,
            "application_version": self.settings.application_version,
            "system_brain_ai_enabled": self.settings.system_brain_ai_enabled,
            "system_brain_ai_model": self.settings.system_brain_ai_model,
            "system_brain_cloudflare_access_required": (
                self.settings.system_brain_cloudflare_access_required
            ),
            "public_chat_retention_days": self.settings.public_chat_session_retention_days,
        }
        return _envelope(
            safe,
            ["config:safe-system-brain-allowlist"],
            coverage="explicit non-secret configuration allowlist",
            limitations=[
                "Secrets, credentials, URLs containing credentials, headers and environment "
                "values are excluded."
            ],
        )

    async def _setup_metrics(
        self, session: AsyncSession, name: str, args: SystemBrainToolArguments
    ) -> EvidenceEnvelope:
        total = int(
            await session.scalar(
                _apply_range(select(func.count(SetupChatTurn.id)), SetupChatTurn.created_at, args)
            )
            or 0
        )
        failed = int(
            await session.scalar(
                _apply_range(
                    select(func.count(SetupChatTurn.id)).where(
                        SetupChatTurn.failure_code.is_not(None)
                    ),
                    SetupChatTurn.created_at,
                    args,
                )
            )
            or 0
        )
        completed = int(
            await session.scalar(
                _apply_range(
                    select(func.count(SetupChatTurn.id)).where(
                        SetupChatTurn.status.in_(["COMPLETED", "completed"])
                    ),
                    SetupChatTurn.created_at,
                    args,
                )
            )
            or 0
        )
        failures = await _group_count(
            session, SetupChatTurn.failure_code, SetupChatTurn.created_at, args, exclude_null=True
        )
        if name == "latency_breakdown":
            rows = list(
                (
                    await session.scalars(
                        _apply_range(select(SetupChatTurn), SetupChatTurn.created_at, args)
                        .where(SetupChatTurn.telemetry_json.is_not(None))
                        .limit(1000)
                    )
                ).all()
            )
            samples = sorted(
                int(
                    (row.telemetry_json or {}).get("total_turn_ms")
                    or (row.telemetry_json or {}).get("turn_duration_ms")
                    or 0
                )
                for row in rows
            )
            result: Any = {
                "sample_size": len(samples),
                "p50_ms": _percentile(samples, 0.50),
                "p95_ms": _percentile(samples, 0.95),
            }
        elif name == "clarification_failure_analysis":
            result = {
                "failure_codes": [
                    row
                    for row in failures
                    if "CLAR" in str(row["key"]).upper() or "GROUND" in str(row["key"]).upper()
                ],
                "all_failures": failed,
            }
        elif name == "release_comparison":
            result = {
                "current_application_version": self.settings.application_version,
                "turns": total,
                "completed": completed,
                "failed": failed,
                "note": (
                    "A comparison requires version-tagged telemetry; missing versions are "
                    "not assigned."
                ),
            }
        else:
            result = {
                "turns": total,
                "completed": completed,
                "failed": failed,
                "clean_success_rate": _rate(completed, total),
                "failure_rate": _rate(failed, total),
                "failure_codes": failures,
            }
        data = {
            "formula": (
                "completed or failed persisted SetupChatTurn rows divided by all turns "
                "in range"
            ),
            "date_range": _range_label(args),
            "sample_size": total,
            "included_population": "persisted authenticated Setup Chat turns",
            "exclusions": [
                "In-progress turns are not counted as successes",
                "Missing telemetry is excluded from latency percentiles",
            ],
            "result": result,
            "confidence_limitations": (
                []
                if total
                else ["No turns exist in the selected range; missing data is not zero performance."]
            ),
        }
        return _envelope(
            data,
            [f"metric:{name}:{_range_label(args)}"],
            coverage=f"{total} persisted turns",
            limitations=data["confidence_limitations"],
        )

    async def _safe_artifact(
        self,
        session: AsyncSession,
        *,
        admin_user_id: UUID,
        conversation_id: UUID,
        kind: str,
        arguments: InternalArtifactRequest,
    ) -> EvidenceEnvelope:
        safe_content = redact_customer_text(arguments.content, limit=12_000)
        content: dict[str, Any] = {
            "content": safe_content,
            "target_id": arguments.target_id,
            "model_authored_draft": True,
        }
        if kind == "export_bounded_csv":
            stream = io.StringIO()
            writer = csv.writer(stream)
            writer.writerow(["content"])
            for line in safe_content.splitlines()[:1000]:
                writer.writerow([line])
            content = {
                "csv": stream.getvalue(),
                "row_limit": 1000,
                "model_authored_draft": True,
            }
        artifact = SystemBrainArtifact(
            admin_user_id=admin_user_id,
            conversation_id=conversation_id,
            artifact_kind=kind,
            title=redact_customer_text(arguments.title, limit=200),
            content_redacted=content,
            evidence_refs=arguments.evidence_refs,
        )
        session.add(artifact)
        await session.flush()
        session.add(
            AuditEvent(
                actor_user_id=admin_user_id,
                actor_type="system_brain_admin",
                action="system_brain.artifact.created",
                target_type="system_brain_artifact",
                target_id=str(artifact.id),
                request_id=None,
                ip_hash=None,
                metadata_redacted={
                    "artifact_kind": kind,
                    "conversation_id": str(conversation_id),
                    "evidence_refs": arguments.evidence_refs,
                    "model_authored_draft": True,
                },
                created_at=datetime.now(UTC),
            )
        )
        return _envelope(
            {"artifact_id": str(artifact.id), "kind": kind, "title": artifact.title},
            [f"system-brain-artifact:{artifact.id}", *arguments.evidence_refs],
            coverage="internal artifact persisted; no customer or domain mutation",
        )


def _envelope(
    data: Any, refs: list[str], *, coverage: str, limitations: list[str] | None = None
) -> EvidenceEnvelope:
    # ``data`` is made sendable by the envelope itself — see ``json_safe`` in
    # ``schemas/system_brain.py``. It is done there rather than here because four other
    # producers build an envelope without going through this helper.
    return EvidenceEnvelope(
        data=data,
        evidence_refs=list(dict.fromkeys(refs))[:100],
        freshness=datetime.now(UTC).isoformat(),
        coverage=coverage,
        limitations=limitations or [],
    )


async def _case_id_for(session: AsyncSession, wanted: str) -> UUID | None:
    """Find one review case by its reference or by its id. One owner, both spellings.

    The reference is matched exactly and case-insensitively, never as a prefix: two cases
    whose references share a prefix would otherwise be one lookup away from being confused
    with each other, and the answer is about a governed review.
    """

    try:
        return UUID(wanted)
    except ValueError:
        pass
    row = await session.scalar(
        select(ReviewCase.id).where(func.lower(ReviewCase.case_reference) == wanted.casefold())
    )
    return row


def _missing(message: str) -> EvidenceEnvelope:
    return EvidenceEnvelope(
        data=None,
        evidence_refs=[],
        freshness="unavailable",
        coverage="missing",
        limitations=[message],
    )


def _require_authorized_evidence(requested: list[str], available: set[str] | None) -> None:
    if available is None or not requested or not set(requested).issubset(available):
        raise ValueError(
            "The artifact or proposal cited evidence that was not returned by an "
            "authorized tool in this turn."
        )


async def _require_admin_tool_principal(session: AsyncSession, admin_user_id: UUID) -> None:
    role = await session.scalar(select(User.role).where(User.id == admin_user_id))
    if role != UserRole.ADMIN:
        raise ValueError("Administrator authorization is required for System Brain tools.")


def _metric_envelope(
    name: str, result: Any, args: SystemBrainToolArguments, formula: str
) -> EvidenceEnvelope:
    sample = (
        sum(int(row.get("count", 0)) for row in result if isinstance(row, dict))
        if isinstance(result, list)
        else 0
    )
    data = {
        "formula": formula,
        "date_range": _range_label(args),
        "sample_size": sample,
        "included_population": "persisted records matching the selected date range",
        "exclusions": ["Missing records are not treated as zero events"],
        "result": result,
        "confidence_limitations": ["Descriptive association only; causation is not claimed."],
    }
    return _envelope(
        data,
        [f"metric:{name}:{_range_label(args)}"],
        coverage=f"sample size {sample}",
        limitations=data["confidence_limitations"],
    )


def _metric_contract_envelope(
    name: str,
    args: SystemBrainToolArguments,
    *,
    sample_size: int,
    formula: str,
    result: Any,
    limitations: list[str],
) -> EvidenceEnvelope:
    return _envelope(
        {
            "formula": formula,
            "date_range": _range_label(args),
            "sample_size": sample_size,
            "included_population": "persisted records matching the selected date range",
            "exclusions": ["Missing or uninstrumented records are not treated as zero"],
            "result": result,
            "confidence_limitations": limitations,
        },
        [f"metric:{name}:{_range_label(args)}"],
        coverage=f"sample size {sample_size}",
        limitations=limitations,
    )


async def _count(session: AsyncSession, model: Any, clause: Any) -> int:
    return int(await session.scalar(select(func.count(model.id)).where(clause)) or 0)


async def _count_in_range(
    session: AsyncSession, model: Any, column: Any, args: SystemBrainToolArguments
) -> int:
    return int(await session.scalar(_apply_range(select(func.count(model.id)), column, args)) or 0)


async def _group_count(
    session: AsyncSession,
    key: Any,
    at: Any,
    args: SystemBrainToolArguments,
    *,
    exclude_null: bool = False,
) -> list[dict[str, Any]]:
    statement = (
        select(key, func.count()).group_by(key).order_by(func.count().desc()).limit(args.limit)
    )
    if exclude_null:
        statement = statement.where(key.is_not(None))
    statement = _apply_range(statement, at, args)
    rows = (await session.execute(statement)).all()
    return [{"key": _value(row[0]), "count": int(row[1])} for row in rows]


def _apply_range(statement: Any, column: Any, args: SystemBrainToolArguments) -> Any:
    if args.date_from:
        statement = statement.where(column >= args.date_from)
    if args.date_to:
        statement = statement.where(column <= args.date_to)
    return statement


async def _authoritative_email(session: AsyncSession, user_id: UUID) -> str | None:
    identity = await session.scalar(
        select(UserIdentity)
        .where(
            UserIdentity.user_id == user_id,
            UserIdentity.provider == IdentityProvider.EMAIL,
            UserIdentity.normalized_identifier.is_not(None),
        )
        .order_by(UserIdentity.is_primary.desc(), UserIdentity.created_at.asc())
        .limit(1)
    )
    return (identity.display_identifier or identity.normalized_identifier) if identity else None


async def _authoritative_email_map(session: AsyncSession, user_ids: set[UUID]) -> dict[UUID, str]:
    if not user_ids:
        return {}
    rows = list(
        (
            await session.scalars(
                select(UserIdentity)
                .where(
                    UserIdentity.user_id.in_(user_ids),
                    UserIdentity.provider == IdentityProvider.EMAIL,
                    UserIdentity.normalized_identifier.is_not(None),
                )
                .order_by(UserIdentity.is_primary.desc(), UserIdentity.created_at.asc())
            )
        ).all()
    )
    output: dict[UUID, str] = {}
    for identity in rows:
        output.setdefault(
            identity.user_id,
            identity.display_identifier or identity.normalized_identifier or "",
        )
    return output


def _range_label(args: SystemBrainToolArguments) -> str:
    start = args.date_from.isoformat() if args.date_from else "all-time"
    end = args.date_to.isoformat() if args.date_to else "now"
    return f"{start}..{end}"


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _percentile(values: list[int], quantile: float) -> int | None:
    if not values:
        return None
    return values[min(len(values) - 1, max(0, round((len(values) - 1) * quantile)))]


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _string(value: Any) -> str | None:
    return str(value) if value not in {None, ""} else None


def _source_filter(value: Any) -> Any:
    # The one list of sources lives in `schemas/system_brain.py`. Typing a second copy
    # here is how a filter the page offers becomes a filter the agent silently drops.
    text = _string(value)
    return text if text in CONVERSATION_SOURCE_LABELS else None


def _identity_filter(value: Any) -> Any:
    text = _string(value)
    return text if text in {"authenticated", "anonymous"} else None
