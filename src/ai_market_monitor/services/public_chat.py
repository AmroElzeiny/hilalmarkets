from __future__ import annotations

import hashlib
import hmac
import html
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import PLAN_DEFINITIONS, PUBLIC_PLAN_CODES
from ai_market_monitor.core.site_content import HELP_CATEGORIES, PUBLIC_PAGES, PURCHASE_FAQS
from ai_market_monitor.db.models import (
    PublicChatAnswerEvent,
    PublicChatAnswerFeedback,
    PublicChatConversation,
    PublicChatTurn,
    PublicInquiry,
    PublicInquiryEmailDelivery,
    PublicInquiryRating,
    User,
)
from ai_market_monitor.schemas.public_chat import (
    PublicChatAnswerFeedbackRequest,
    PublicChatAnswerFeedbackResponse,
    PublicChatAnswerRequest,
    PublicChatAnswerResponse,
    PublicChatAnswerStatus,
    PublicChatRelatedLink,
    PublicInquiryRatingRequest,
    PublicInquiryRequest,
    PublicSupportMode,
    PublicSupportStage,
)
from ai_market_monitor.services.agent_control import AgentResponsesClient
from ai_market_monitor.services.email_delivery import AuthEmailService, EmailDeliveryError
from ai_market_monitor.services.notion_knowledge import NotionKnowledgeService
from ai_market_monitor.services.public_support_ai import (
    PublicSupportAICall,
    PublicSupportAIService,
    PublicSupportAIUnavailable,
)
from ai_market_monitor.services.public_support_tools import PublicSupportReadTools
from ai_market_monitor.services.web_auth import normalize_email

logger = logging.getLogger(__name__)

PUBLIC_CHAT_PROFILE_STORAGE_KEY = "hm-public-chat-profile-v1"
PUBLIC_CHAT_CSRF_COOKIE = "hm_public_chat_csrf"

PUBLIC_ROUTE_PATHS: dict[str, tuple[str, str]] = {
    "home": ("Hilal Markets", "/"),
    "features": ("Features", "/features"),
    "how_it_works": ("How It Works", "/how-it-works"),
    "how_we_screen": ("How We Screen", "/how-we-screen"),
    "pricing": ("Pricing", "/pricing"),
    "help": ("Help Center", "/help"),
    "contact": ("Contact", "/contact"),
    "about": ("About", "/about"),
    "trust_safety": ("Trust & Safety", "/trust-safety"),
    "risk_disclosure": ("Risk Disclosure", "/risk-disclosure"),
    "privacy": ("Privacy", "/privacy"),
    "terms": ("Terms", "/terms"),
    "cookies": ("Cookie Policy", "/cookies"),
    "dashboard_entry": ("Dashboard", "/dashboard-entry"),
}

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]*>")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_STOPWORDS = {
    "a",
    "about",
    "and",
    "are",
    "can",
    "does",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "the",
    "to",
    "what",
    "when",
    "where",
    "why",
    "with",
    "you",
}
_ADVICE_PATTERNS = (
    re.compile(r"\bshould\s+i\s+(buy|sell|trade)\b", re.IGNORECASE),
    re.compile(r"\b(?:what|which)\s+(coin|token).*(buy|pump|moon)\b", re.IGNORECASE),
    re.compile(r"\b(price prediction|guaranteed return|guaranteed profit)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:guarantee|guaranteed)\b.*\b(?:profit|profitable|return|setup)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(leverage|futures|margin)\b.*\b(advice|recommend|use)\b", re.IGNORECASE),
)
_RELIGIOUS_RULING_PATTERNS = (
    re.compile(r"\bis\s+[a-z0-9._-]+\s+(halal|haram)\b", re.IGNORECASE),
    re.compile(r"\b(give|issue|make)\s+(me\s+)?(a\s+)?fatwa\b", re.IGNORECASE),
)
_PRIVATE_ACCOUNT_PATTERNS = (
    re.compile(r"\b(my|our)\s+(account|watch plan|passport|subscription|payment)\b", re.I),
    re.compile(r"\blook\s+up\s+(my|this)\s+(account|email)\b", re.I),
)
_CROSS_ACCOUNT_PATTERNS = (
    re.compile(
        r"\b(?:another|other)\s+(?:user|customer|person)(?:'s|\s+account)\b",
        re.I,
    ),
    re.compile(r"\bsomeone\s+else(?:'s)?\s+(?:account|watch\s+plans?)\b", re.I),
    re.compile(r"\b(?:bypass|override)\s+(?:ownership|account\s+access)\b", re.I),
)
_INJECTION_PATTERNS = (
    re.compile(r"\b(ignore|reveal|override)\b.*\b(system|instructions|prompt|rules)\b", re.I),
    re.compile(r"\b(show|print|give)\b.*\b(secret|api key|credentials?)\b", re.I),
)
_GREETING_PATTERNS = (
    re.compile(r"^\s*(?:hi|hello|hey)(?:\s+there)?[!.?\s]*$", re.I),
    re.compile(r"^\s*good\s+(?:morning|afternoon|evening)[!.?\s]*$", re.I),
    re.compile(r"^\s*how\s+are\s+you[!.?\s]*$", re.I),
)
_EXPLICIT_SUPPORT_PATTERNS = (
    re.compile(
        r"\b(?:contact|email|message|speak|talk)\s+(?:to\s+|with\s+)?"
        r"(?:support|the\s+team|a\s+human|someone)\b",
        re.I,
    ),
    re.compile(r"\b(?:open|submit|send)\s+(?:a\s+)?support\s+(?:form|ticket|request)\b", re.I),
    re.compile(
        r"\bi\s+(?:want|need|would\s+like)\s+(?:to\s+)?"
        r"(?:contact|email|message|speak|talk)\b",
        re.I,
    ),
)
_UNSAFE_EDUCATION_OUTPUT = (
    re.compile(r"\b(?:you\s+should|i\s+recommend\s+you)\s+(?:buy|sell|trade)\b", re.I),
    re.compile(r"\b(?:buy|sell)\s+now\b", re.I),
    re.compile(r"\b(?:guaranteed|certain)\s+(?:profit|return|outcome)\b", re.I),
    re.compile(r"\b(?:will|is\s+going\s+to)\s+(?:pump|moon|rise|fall)\b", re.I),
)


@dataclass(frozen=True, slots=True)
class PublicKnowledgeEntry:
    source_id: str
    title: str
    answer: str
    route_id: str
    keywords: tuple[str, ...] = ()

    @property
    def tokens(self) -> set[str]:
        # The spaced brand name contains the domain word "Markets". Do not let
        # that repeated boilerplate make every document look relevant to a
        # market-scope question; explicit entry keywords still rank normally.
        content = re.sub(
            r"\bhilal\s+markets\b",
            " ",
            " ".join((self.title, self.answer)),
            flags=re.IGNORECASE,
        )
        return _tokens(content) | _tokens(" ".join(self.keywords))


class PublicKnowledgeService:
    """Retrieve only from versioned, public product content owned by the application."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.entries = self._entries()

    def boundary_answer(
        self,
        question: str,
        *,
        authenticated: bool = False,
    ) -> tuple[
        PublicChatAnswerStatus,
        str,
        float,
        list[str],
        list[str],
        str | None,
    ] | None:
        cleaned = _clean_text(question, maximum=800)
        if any(pattern.search(cleaned) for pattern in _INJECTION_PATTERNS):
            return (
                "refused",
                "I can explain verified Hilal Markets product information, but I cannot reveal "
                "internal instructions, credentials, or private system details.",
                1.0,
                ["boundary:public-assistant-security"],
                ["trust_safety"],
                "security_boundary",
            )
        if any(pattern.search(cleaned) for pattern in _ADVICE_PATTERNS):
            return (
                "refused",
                "Hilal Markets does not tell you what to buy or sell, predict pumps, or provide "
                "leverage advice. It helps you monitor your own crypto spot rules with evidence.",
                1.0,
                ["boundary:no-investment-advice"],
                ["risk_disclosure", "how_it_works"],
                "investment_advice",
            )
        if any(pattern.search(cleaned) for pattern in _RELIGIOUS_RULING_PATTERNS):
            return (
                "refused",
                "Hilal Markets does not issue religious rulings. It shows the status, scope, "
                "methodology, evidence, and qualified human decision recorded for each asset.",
                1.0,
                ["boundary:no-religious-rulings"],
                ["how_we_screen"],
                "religious_ruling",
            )
        if any(pattern.search(cleaned) for pattern in _CROSS_ACCOUNT_PATTERNS):
            return (
                "refused",
                "I cannot bypass account ownership or inspect another customer's private data.",
                1.0,
                ["boundary:no-cross-account-access"],
                ["trust_safety"],
                "cross_account_access",
            )
        if not authenticated and any(
            pattern.search(cleaned) for pattern in _PRIVATE_ACCOUNT_PATTERNS
        ):
            return (
                "refused",
                "I cannot inspect private accounts or Watch Plans without an authenticated "
                "session. Sign in to use read-only account support.",
                1.0,
                ["boundary:no-private-account-access"],
                ["dashboard_entry", "help"],
                "private_account",
            )
        return None

    def documents_for_ai(self) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        character_budget = 36_000
        used = 0
        for entry in self.entries:
            document = {
                "source_id": entry.source_id,
                "title": entry.title,
                "content": entry.answer,
                "route_id": entry.route_id,
                "authority": "authoritative_product_fact",
            }
            size = len(entry.title) + len(entry.answer) + len(entry.source_id)
            if documents and used + size > character_budget:
                break
            documents.append(document)
            used += size
        return documents

    @property
    def source_ids(self) -> frozenset[str]:
        return frozenset(item.source_id for item in self.entries)

    def answer(
        self, question: str
    ) -> tuple[
        PublicChatAnswerStatus,
        str,
        float,
        list[str],
        list[str],
        str | None,
    ]:
        cleaned = _clean_text(question, maximum=800)
        boundary = self.boundary_answer(cleaned)
        if boundary is not None:
            return boundary

        query_tokens = _tokens(cleaned)
        ranked = sorted(
            ((self._score(cleaned, query_tokens, entry), entry) for entry in self.entries),
            key=lambda item: item[0],
            reverse=True,
        )
        best_score, best = ranked[0] if ranked else (0.0, None)
        if best is None or best_score < 0.24:
            return (
                "unsupported",
                "I don't have a verified answer for that yet, but I can send your question to "
                "the Hilal Markets team.",
                best_score,
                [],
                ["contact"],
                "unverified_product_question",
            )
        selected = [best]
        if len(ranked) > 1:
            second_score, second = ranked[1]
            if second_score >= max(0.35, best_score * 0.90) and second.route_id != best.route_id:
                selected.append(second)
        message = " ".join(dict.fromkeys(item.answer for item in selected))
        return (
            "answered",
            message[:1800],
            min(1.0, best_score),
            [item.source_id for item in selected],
            list(dict.fromkeys(item.route_id for item in selected)),
            None,
        )

    def retrieve(
        self,
        question: str,
        *,
        previous_source_ids: list[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """Shortlist approved facts for AI generation; this method never writes an answer."""

        cleaned = _clean_text(question, maximum=800)
        query_tokens = _tokens(cleaned)
        previous = set(previous_source_ids or [])
        ranked = sorted(
            (
                (
                    self._score(cleaned, query_tokens, entry)
                    + (0.08 if entry.source_id in previous else 0),
                    entry,
                )
                for entry in self.entries
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        selected = [
            (score, entry)
            for score, entry in ranked
            if score > 0 or entry.source_id in previous
        ][:limit]
        return [
            {
                "source_id": entry.source_id,
                "title": entry.title,
                "content": entry.answer,
                "route_id": entry.route_id,
                "authority": "authoritative_product_fact",
                "retrieval_score": round(min(1.0, score), 5),
            }
            for score, entry in selected
        ]

    def _entries(self) -> tuple[PublicKnowledgeEntry, ...]:
        entries: list[PublicKnowledgeEntry] = [
            PublicKnowledgeEntry(
                source_id="product:overview:v1",
                title="Hilal Markets product overview",
                answer=(
                    "Hilal Markets is an explainable crypto spot research and monitoring product. "
                    "It helps users screen eligible assets, describe their own market rules, "
                    "check those rules, follow opportunity journeys, and receive evidence-backed "
                    "alerts. It does not execute trades or promise outcomes."
                ),
                route_id="home",
                keywords=(
                    "what Hilal Markets does",
                    "product overview",
                    "crypto spot monitoring",
                    "not an exchange",
                ),
            ),
            PublicKnowledgeEntry(
                source_id="beta:account-access:v1",
                title="Private-beta account and dashboard access",
                answer=(
                    "Create or sign in to a Hilal Markets account through the Dashboard entry. "
                    "Email verification protects account ownership. Password recovery, account "
                    "preferences, and authenticated support are available from the account flow; "
                    "private account details are never available to anonymous visitors."
                ),
                route_id="dashboard_entry",
                keywords=(
                    "create account",
                    "sign in",
                    "email verification code",
                    "reset password",
                    "private beta",
                    "dashboard access",
                ),
            ),
            PublicKnowledgeEntry(
                source_id="product:screened-market:v1",
                title="Screened Market and My Screened Watchlist",
                answer=(
                    "Screened Market shows assets allowed by the active user policy and "
                    "methodology. My Screened Watchlist saves assets to follow or use as a "
                    "Watch Plan universe; saving an asset never overrides its current status."
                ),
                route_id="features",
                keywords=("screened market", "saved assets", "screened watchlist"),
            ),
            PublicKnowledgeEntry(
                source_id="product:evidence-passport:v1",
                title="Evidence Passports, methodology versions, and qualifications",
                answer=(
                    "An Evidence Passport is the immutable published record for one asset, "
                    "methodology version, evidence set, review scope, use-case decisions, and "
                    "qualifications. It reports the recorded decision; Hilal Markets does not "
                    "issue an independent religious ruling."
                ),
                route_id="how_we_screen",
                keywords=("passport", "methodology", "qualification", "use case"),
            ),
            PublicKnowledgeEntry(
                source_id="product:opportunity-journey:v1",
                title="Opportunity Cards and Opportunity Journeys",
                answer=(
                    "Opportunity Cards summarize real evaluated Watch Plan evidence. An "
                    "Opportunity Journey preserves how one occurrence moved through forming, "
                    "confirmation, invalidation, expiry, policy holds, and delivery outcomes."
                ),
                route_id="features",
                keywords=("opportunity card", "opportunity journey", "lifecycle"),
            ),
            PublicKnowledgeEntry(
                source_id="product:compliance-change:v1",
                title="Compliance status changes and affected Watch Plans",
                answer=(
                    "When a published screening status changes, Hilal Markets preserves the old "
                    "and current Passport versions, re-resolves affected Watch Plans, applies "
                    "the selected fail-closed policy, and explains the impact."
                ),
                route_id="how_we_screen",
                keywords=("compliance change", "status change", "hold", "restoration"),
            ),
            PublicKnowledgeEntry(
                source_id="beta:channel-state:v1",
                title="Private-beta notification channel availability",
                answer=(
                    "The private beta supports in-app notifications and Telegram. WhatsApp and "
                    "billing are disabled for the initial beta, and Discord is retired."
                ),
                route_id="features",
                keywords=("telegram", "whatsapp", "discord", "notification channel"),
            ),
            PublicKnowledgeEntry(
                source_id="product:security-privacy:v1",
                title="Account security, privacy, and cookies",
                answer=(
                    "Email verification and authenticated ownership protect private account "
                    "data. Essential security storage remains enabled; optional functional and "
                    "analytics cookies follow the visitor's choices and can be changed later."
                ),
                route_id="privacy",
                keywords=("security", "privacy", "cookies", "account ownership"),
            ),
            PublicKnowledgeEntry(
                source_id="product:limitations-risk:v1",
                title="Product limitations and risk disclosure",
                answer=(
                    "Hilal Markets is research and monitoring software for crypto spot. It does "
                    "not execute trades, hold funds, promise returns, predict prices, provide "
                    "personalized investment advice, or issue personal religious rulings."
                ),
                route_id="risk_disclosure",
                keywords=("limitation", "risk", "financial advice", "trading execution"),
            ),
            PublicKnowledgeEntry(
                source_id="product:support-partnerships:v1",
                title="Product support and partnership inquiries",
                answer=(
                    "Visitors can ask the Support AI about Hilal Markets and choose the Support "
                    "form when they want a human follow-up. Product, screening, technical, and "
                    "partnership inquiries are routed to the Hilal Markets team by email."
                ),
                route_id="contact",
                keywords=("support", "contact", "partnership", "email the team"),
            ),
        ]
        for category in HELP_CATEGORIES:
            for index, article in enumerate(category["articles"]):
                entries.append(
                    PublicKnowledgeEntry(
                        source_id=f"help:{category['slug']}:{index + 1}",
                        title=article["question"],
                        answer=article["answer"],
                        route_id="help",
                        keywords=(category["title"], category["slug"]),
                    )
                )
        for index, item in enumerate(PURCHASE_FAQS):
            entries.append(
                PublicKnowledgeEntry(
                    source_id=f"purchase-faq:{index + 1}",
                    title=item["question"],
                    answer=item["answer"],
                    route_id="pricing" if "plan" in item["answer"].casefold() else "help",
                    keywords=("purchase", "pricing", "product boundary"),
                )
            )
        for page in PUBLIC_PAGES:
            entries.append(
                PublicKnowledgeEntry(
                    source_id=f"public-page:{page.page}",
                    title=page.title,
                    answer=page.description,
                    route_id=page.page,
                    keywords=(page.page.replace("_", " "),),
                )
            )
        if self.settings.billing_enabled:
            plan_summary = "; ".join(
                f"{PLAN_DEFINITIONS[code].name}: ${PLAN_DEFINITIONS[code].monthly_price}"
                for code in PUBLIC_PLAN_CODES
            )
            pricing_answer = (
                f"Current public plan pricing is {plan_summary}. The Pricing page is the "
                "authoritative catalog for limits and provider-accurate renewal terms."
            )
        else:
            pricing_answer = (
                "The private beta is free and invite-only. Paid checkout is disabled until "
                "provider sandbox validation and an explicit production enablement are complete."
            )
        entries.extend(
            (
                PublicKnowledgeEntry(
                    source_id="plan-catalog:public",
                    title="How much does Hilal Markets cost during private beta?",
                    answer=pricing_answer,
                    route_id="pricing",
                    keywords=("price", "pricing", "cost", "free", "plan", "beta", "trial"),
                ),
                PublicKnowledgeEntry(
                    source_id="beta-channels:v1",
                    title="Which notification channels are available?",
                    answer="The private beta supports in-app and Telegram notifications.",
                    route_id="features",
                    keywords=("telegram", "notification", "alert", "channel"),
                ),
                PublicKnowledgeEntry(
                    source_id="beta-scope:v1",
                    title="Which markets are in the private beta?",
                    answer=(
                        "The initial private beta is limited to BTC, ETH, and SOL on Binance "
                        "spot under one disclosed active screening methodology."
                    ),
                    route_id="how_it_works",
                    keywords=(
                        "btc",
                        "eth",
                        "sol",
                        "binance",
                        "spot",
                        "market",
                        "markets",
                        "asset",
                        "coin",
                        "coins",
                        "exchange",
                        "universe",
                    ),
                ),
            )
        )
        return tuple(entries)

    @staticmethod
    def _score(
        question: str,
        query_tokens: set[str],
        entry: PublicKnowledgeEntry,
    ) -> float:
        if not query_tokens:
            return 0.0
        overlap = query_tokens & entry.tokens
        score = len(overlap) / max(1, min(len(query_tokens), 7))
        title = entry.title.casefold().rstrip("?")
        lowered = question.casefold()
        if title and title in lowered:
            score += 0.45
        keyword_hits = sum(
            1 for keyword in entry.keywords if keyword.casefold() in lowered
        )
        score += min(0.30, keyword_hits * 0.10)
        return min(1.0, score)


class PublicChatSessionLimitExceeded(RuntimeError):
    pass


class PublicChatService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        ai_client: AgentResponsesClient | None = None,
    ):
        self.session = session
        self.settings = settings
        self.knowledge = PublicKnowledgeService(settings)
        self.notion_knowledge = NotionKnowledgeService(settings)
        self.ai_client = ai_client

    async def answer(
        self,
        payload: PublicChatAnswerRequest,
        *,
        user_id: UUID | None = None,
    ) -> PublicChatAnswerResponse:
        started = monotonic()
        conversation = await self._conversation(payload, user_id=user_id)
        await self.session.commit()
        turn, cached = await self._begin_turn(conversation, payload)
        if cached is not None:
            return cached
        state = dict(conversation.state_json or {})
        boundary = self.knowledge.boundary_answer(
            payload.question,
            authenticated=user_id is not None,
        )
        ai_calls: list[PublicSupportAICall] = []
        tool_results = []
        validation_failure: str | None = None
        safety_boundary: str | None = None
        authenticated_context_used = False
        status: PublicChatAnswerStatus
        stage: PublicSupportStage
        mode: PublicSupportMode
        is_greeting = _is_greeting(payload.question)
        explicit_support_request = _explicit_support_requested(payload.question)
        support_handoff_available = False
        support_handoff_reason: str | None = None
        if boundary is not None:
            status, message, score, source_ids, route_ids, gap = boundary
            stage = "REFUSAL"
            mode = "SAFETY_REFUSAL"
            intent = gap or "safety_refusal"
            clarification = None
            answer_complete = True
            follow_ups = ["What can Hilal Markets monitor for me?"]
            safety_boundary = gap
        elif not self.settings.public_chat_ai_enabled:
            if is_greeting:
                status = "answered"
                message = _safe_greeting(
                    state,
                    supplied_name=payload.profile.name if payload.profile else None,
                )
                score = 1.0
                source_ids = []
                route_ids = []
                gap = None
                stage = "GREETING_AND_PROFILE"
                mode = "PRODUCT_CONVERSATION"
                intent = "greeting"
            else:
                status, message, score, source_ids, route_ids, gap = (
                    self.knowledge.answer(payload.question)
                )
                stage = "ANSWER" if status == "answered" else "KNOWLEDGE_GAP"
                mode = "PRODUCT_FACT"
                intent = "legacy_grounded_fallback"
            clarification = None
            answer_complete = status == "answered"
            follow_ups = []
            support_handoff_available = status == "unsupported"
            support_handoff_reason = gap if support_handoff_available else None
        else:
            documents = self.knowledge.documents_for_ai()
            documents.extend(
                self.notion_knowledge.retrieve(
                    payload.question,
                    previous_source_ids=list(state.get("previous_source_ids") or []),
                )
            )
            ai_state = _public_ai_state(state)
            if payload.profile is not None:
                ai_state["visitor_profile"] = {
                    "name": payload.profile.name.split()[0][:80]
                }
            if user_id is not None and not (ai_state.get("visitor_profile") or {}).get(
                "name"
            ):
                user = await self.session.get(User, user_id)
                if user is not None and user.display_name:
                    ai_state["visitor_profile"] = {
                        "name": user.display_name.split()[0][:80]
                    }
            allowed_tools = ["public_passport"]
            if user_id is not None:
                allowed_tools.extend(
                    [
                        "account_state",
                        "telegram_status",
                        "watch_plan_summary",
                        "recent_alerts",
                        "entitlement_usage",
                        "screened_watchlist",
                    ]
                )
            try:
                first = await PublicSupportAIService(
                    self.settings,
                    client=self.ai_client,
                ).respond(
                    question=payload.question,
                    history=list(state.get("messages") or []),
                    conversation_state=ai_state,
                    knowledge_documents=documents,
                    allowed_tools=allowed_tools,
                    authenticated=user_id is not None,
                )
                ai_calls.append(first)
                self._validate_ai_response(
                    first.response,
                    documents=documents,
                    allowed_tools=allowed_tools,
                    tool_results=[],
                    final_after_tools=False,
                )
                requested_tools = list(dict.fromkeys(first.response.requested_tools))
                if len(requested_tools) > self.settings.public_chat_ai_max_read_tools:
                    raise PublicSupportAIUnavailable(
                        "The assistant requested too many account reads."
                    )
                if requested_tools:
                    tools = PublicSupportReadTools(self.session)
                    for tool_name in requested_tools:
                        tool_results.append(
                            await tools.execute(
                                tool_name,
                                user_id=user_id,
                                question=payload.question,
                            )
                        )
                    second = await PublicSupportAIService(
                        self.settings,
                        client=self.ai_client,
                    ).respond(
                        question=payload.question,
                        history=list(state.get("messages") or []),
                        conversation_state=ai_state,
                        knowledge_documents=documents,
                        allowed_tools=[],
                        authenticated=user_id is not None,
                        tool_results=[item.model_dump(mode="json") for item in tool_results],
                        final_after_tools=True,
                    )
                    ai_calls.append(second)
                    if (
                        sum(item.estimated_cost_usd for item in ai_calls)
                        > self.settings.public_chat_ai_max_estimated_cost_usd_per_turn
                    ):
                        raise PublicSupportAIUnavailable(
                            "The public-assistant turn cost limit was reached."
                        )
                    self._validate_ai_response(
                        second.response,
                        documents=documents,
                        allowed_tools=[],
                        tool_results=tool_results,
                        final_after_tools=True,
                    )
                    generated = second.response
                else:
                    generated = first.response
                status = (
                    "refused"
                    if generated.mode == "SAFETY_REFUSAL"
                    else "unsupported"
                    if generated.mode == "OUT_OF_SCOPE"
                    or generated.stage in {"KNOWLEDGE_GAP", "INQUIRY_FORM"}
                    else "answered"
                )
                message = generated.answer
                score = generated.confidence
                source_ids = list(generated.source_ids)
                route_ids = self._authoritative_route_ids(
                    generated.related_route_ids,
                    tool_results,
                )
                gap = (
                    generated.support_handoff_reason
                    if generated.support_handoff_available
                    else None
                )
                stage = generated.stage
                mode = generated.mode
                intent = generated.intent
                clarification = generated.clarification_question
                answer_complete = generated.answer_complete
                follow_ups = generated.suggested_follow_ups
                support_handoff_available = generated.support_handoff_available
                support_handoff_reason = generated.support_handoff_reason
                safety_boundary = generated.safety_boundary
                authenticated_context_used = bool(
                    user_id is not None
                    and any(
                        item.status == "success" and item.tool_name != "public_passport"
                        for item in tool_results
                    )
                )
                if score < self.settings.public_chat_ai_min_confidence:
                    if clarification:
                        stage = "CLARIFY"
                        answer_complete = False
                    else:
                        status = "unsupported"
                        stage = "KNOWLEDGE_GAP"
                        answer_complete = False
                        gap = gap or "low_confidence"
            except PublicSupportAIUnavailable as exc:
                validation_failure = type(exc).__name__
                mode = "PRODUCT_CONVERSATION" if is_greeting else "PRODUCT_FACT"
                status = "answered" if is_greeting else "unsupported"
                message = _safe_greeting(
                    state,
                    supplied_name=payload.profile.name if payload.profile else None,
                ) if is_greeting else (
                    "I couldn't verify that answer just now. You can retry, ask it another "
                    "way, or use the Support form below if you prefer."
                )
                score = 0.0
                source_ids = []
                route_ids = [] if is_greeting else ["help", "contact"]
                gap = "grounded_ai_unavailable"
                stage = "GREETING_AND_PROFILE" if is_greeting else "KNOWLEDGE_GAP"
                intent = "greeting" if is_greeting else "provider_unavailable"
                clarification = None
                answer_complete = is_greeting
                follow_ups = ["Try that question again"]
                support_handoff_available = not is_greeting
                support_handoff_reason = gap if support_handoff_available else None
                safety_boundary = "product_scope_only"

        support_handoff_explicitly_requested = bool(
            explicit_support_request
            and mode not in {"OUT_OF_SCOPE", "SAFETY_REFUSAL"}
        )
        if support_handoff_explicitly_requested:
            support_handoff_available = True
            support_handoff_reason = "user_requested_human_support"
        usage = self._combined_ai_usage(ai_calls)
        now = datetime.now(UTC)
        event = PublicChatAnswerEvent(
            session_key_hash=conversation.session_key_hash,
            conversation_id=conversation.id,
            user_id=user_id,
            question_hash=self._hash(f"question:{payload.question.casefold()}"),
            outcome=status,
            stage=stage,
            mode=mode,
            intent=intent,
            model=usage["model"],
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            reasoning_tokens=usage["reasoning_tokens"],
            latency_ms=(
                usage["latency_ms"]
                if ai_calls
                else max(0, round((monotonic() - started) * 1000))
            ),
            estimated_cost_usd=Decimal(str(usage["estimated_cost_usd"])),
            validation_failure=validation_failure,
            knowledge_gap_reason=gap,
            is_greeting=is_greeting,
            support_handoff_available=support_handoff_available,
            support_handoff_reason=support_handoff_reason,
            coverage_score=Decimal(str(round(score, 5))),
            source_ids=source_ids,
            related_route_ids=route_ids,
            created_at=now,
            retain_until=now
            + timedelta(days=self.settings.public_chat_answer_audit_retention_days),
        )
        self.session.add(event)
        await self.session.flush()
        self._update_conversation_state(
            conversation,
            payload=payload,
            answer=message,
            answer_event_id=event.id,
            stage=stage,
            mode=mode,
            intent=intent,
            source_ids=source_ids,
            clarification=clarification,
            user_id=user_id,
            tool_results=tool_results,
            support_handoff_available=support_handoff_available,
        )
        response = PublicChatAnswerResponse(
            status=status,
            message=message,
            source_ids=source_ids,
            # Route IDs remain in the private audit event for grounding, but the
            # public assistant does not expose site links while those pages are
            # under construction.
            related_links=[],
            coverage_score=score,
            knowledge_gap_category=gap,
            stage=stage,
            mode=mode,
            intent=intent,
            clarification_question=clarification,
            confidence=score,
            answer_complete=answer_complete,
            suggested_follow_ups=follow_ups,
            safety_boundary=safety_boundary,
            authenticated_context_used=authenticated_context_used,
            support_handoff_available=support_handoff_available,
            support_handoff_reason=support_handoff_reason,
            support_handoff_explicitly_requested=(
                support_handoff_explicitly_requested
            ),
            answer_event_id=event.id,
        )
        turn.status = "completed"
        turn.response_json = response.model_dump(mode="json")
        turn.error_type = validation_failure
        await self.session.flush()
        return response

    async def _conversation(
        self,
        payload: PublicChatAnswerRequest,
        *,
        user_id: UUID | None,
    ) -> PublicChatConversation:
        session_hash = self._hash(f"session:{payload.session_id}")
        conversation = await self.session.scalar(
            select(PublicChatConversation).where(
                PublicChatConversation.session_key_hash == session_hash
            )
        )
        now = datetime.now(UTC)
        if conversation is None:
            conversation = PublicChatConversation(
                session_key_hash=session_hash,
                user_id=user_id,
                state_json={},
                stage="GREETING_AND_PROFILE",
                message_count=0,
                model=self.settings.public_chat_ai_model or self.settings.openai_model,
                reasoning_effort=self.settings.public_chat_ai_reasoning_effort,
                expires_at=now
                + timedelta(days=self.settings.public_chat_session_retention_days),
            )
            self.session.add(conversation)
            await self.session.flush()
        elif _as_utc(conversation.expires_at) <= now:
            conversation.state_json = {}
            conversation.message_count = 0
            conversation.stage = "GREETING_AND_PROFILE"
        elif conversation.user_id is not None and conversation.user_id != user_id:
            # Logout or account switching cannot expose a prior authenticated transcript.
            conversation.state_json = {}
            conversation.message_count = 0
            conversation.stage = "GREETING_AND_PROFILE"
        conversation.user_id = user_id
        conversation.expires_at = now + timedelta(
            days=self.settings.public_chat_session_retention_days
        )
        return conversation

    async def _begin_turn(
        self,
        conversation: PublicChatConversation,
        payload: PublicChatAnswerRequest,
    ) -> tuple[PublicChatTurn, PublicChatAnswerResponse | None]:
        request_hash = self._hash(f"turn:{payload.question.casefold()}")
        existing = await self.session.scalar(
            select(PublicChatTurn).where(
                PublicChatTurn.conversation_id == conversation.id,
                PublicChatTurn.client_message_id == payload.client_message_id,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise ValueError("A client message ID cannot be reused for different text.")
            if existing.status == "completed" and existing.response_json:
                cached = PublicChatAnswerResponse.model_validate(existing.response_json)
                if cached.related_links:
                    cached.related_links = []
                    existing.response_json = cached.model_dump(mode="json")
                if cached.answer_event_id is None:
                    event = await self.session.scalar(
                        select(PublicChatAnswerEvent)
                        .where(
                            PublicChatAnswerEvent.conversation_id == conversation.id,
                            PublicChatAnswerEvent.question_hash
                            == self._hash(f"question:{payload.question.casefold()}"),
                        )
                        .order_by(PublicChatAnswerEvent.created_at.desc())
                        .limit(1)
                    )
                    if event is not None:
                        cached.answer_event_id = event.id
                        cached.mode = cast(PublicSupportMode, event.mode)
                        cached.support_handoff_available = (
                            event.support_handoff_available
                        )
                        cached.support_handoff_reason = event.support_handoff_reason
                        existing.response_json = cached.model_dump(mode="json")
                return existing, cached
            return existing, PublicChatAnswerResponse(
                status="unsupported",
                message="That message is still being processed. Please retry in a moment.",
                source_ids=[],
                related_links=[],
                coverage_score=0,
                knowledge_gap_category="turn_in_progress",
                stage="FOLLOW_UP",
                mode="PRODUCT_CONVERSATION",
                intent="duplicate_in_progress",
                confidence=1,
                answer_complete=False,
            )
        if conversation.message_count // 2 >= self.settings.public_chat_session_max_turns:
            raise PublicChatSessionLimitExceeded(
                "This conversation reached its message limit. Start a new chat to continue."
            )
        turn = PublicChatTurn(
            conversation_id=conversation.id,
            client_message_id=payload.client_message_id,
            request_hash=request_hash,
            status="processing",
            response_json={},
        )
        self.session.add(turn)
        await self.session.commit()
        await self.session.refresh(turn)
        return turn, None

    def _update_conversation_state(
        self,
        conversation: PublicChatConversation,
        *,
        payload: PublicChatAnswerRequest,
        answer: str,
        answer_event_id: UUID,
        stage: str,
        mode: PublicSupportMode,
        intent: str,
        source_ids: list[str],
        clarification: str | None,
        user_id: UUID | None,
        tool_results: list[Any],
        support_handoff_available: bool,
    ) -> None:
        state = dict(conversation.state_json or {})
        messages = list(state.get("messages") or [])
        messages.extend(
            (
                {"role": "user", "content": payload.question, "stage": stage},
                {"role": "assistant", "content": answer, "stage": stage},
            )
        )
        profile = None
        if payload.profile is not None:
            profile = {
                "name": payload.profile.name,
                "email": normalize_email(str(payload.profile.email)),
            }
        elif state.get("visitor_profile"):
            profile = state.get("visitor_profile")
        if user_id is None:
            state.pop("authenticated_tool_state", None)
        state.update(
            {
                "visitor_profile": profile,
                "current_topic": intent,
                "current_mode": mode,
                "last_question": payload.question,
                "last_answer": answer,
                "last_answer_event_id": str(answer_event_id),
                "messages": messages[-self.settings.public_chat_ai_max_history_messages :],
                "previous_source_ids": list(dict.fromkeys(source_ids))[-20:],
                "questions_answered": (
                    list(state.get("questions_answered") or []) + [payload.question]
                )[-20:],
                "pending_clarification": clarification,
                "resolved_references": list(dict.fromkeys(source_ids))[-20:],
                "product_entities": _product_entities(
                    payload.question,
                    previous=list(state.get("product_entities") or []),
                ),
                "current_troubleshooting_step": (
                    answer if stage == "TROUBLESHOOT" else None
                ),
                "tool_results": [
                    {
                        "tool_name": item.tool_name,
                        "status": item.status,
                        "evidence_refs": list(item.evidence_refs),
                    }
                    for item in tool_results
                ][-10:],
                "support_handoff_available": support_handoff_available,
                "support_form_state": "available" if support_handoff_available else "closed",
                "inquiry_state": "none",
                "rating_state": str(state.get("rating_state") or "none"),
            }
        )
        conversation.state_json = state
        conversation.stage = stage
        conversation.message_count += 2
        conversation.model = self.settings.public_chat_ai_model or self.settings.openai_model
        conversation.reasoning_effort = self.settings.public_chat_ai_reasoning_effort

    def _validate_ai_response(
        self,
        response: Any,
        *,
        documents: list[dict[str, Any]],
        allowed_tools: list[str],
        tool_results: list[Any],
        final_after_tools: bool,
    ) -> None:
        valid_sources = {str(item["source_id"]) for item in documents}
        authoritative_sources = {
            str(item["source_id"])
            for item in documents
            if item.get("authority") == "authoritative_product_fact"
        }
        valid_sources.update(
            reference for item in tool_results for reference in item.evidence_refs
        )
        if not set(response.source_ids).issubset(valid_sources):
            raise PublicSupportAIUnavailable("The assistant cited an unknown source.")
        if not set(response.related_route_ids).issubset(PUBLIC_ROUTE_PATHS):
            raise PublicSupportAIUnavailable("The assistant proposed an unknown route.")
        if not set(response.requested_tools).issubset(allowed_tools):
            raise PublicSupportAIUnavailable("The assistant requested an unavailable tool.")
        if final_after_tools and response.requested_tools:
            raise PublicSupportAIUnavailable("The assistant repeated a read tool request.")
        successful_tool = any(item.status == "success" for item in tool_results)
        successful_account_tool = any(
            item.status == "success" and item.tool_name != "public_passport"
            for item in tool_results
        )
        cited_authoritative_source = bool(
            set(response.source_ids) & authoritative_sources
        )
        if (
            response.mode == "PRODUCT_FACT"
            and not response.requested_tools
            and not cited_authoritative_source
            and not successful_tool
        ):
            raise PublicSupportAIUnavailable(
                "The product answer had no authoritative evidence."
            )
        if (
            response.mode == "ACCOUNT_SUPPORT"
            and not response.requested_tools
            and not successful_account_tool
        ):
            raise PublicSupportAIUnavailable(
                "The account answer had no successful authenticated tool result."
            )
        if response.mode == "GENERAL_TRADING_EDUCATION" and any(
            pattern.search(response.answer) for pattern in _UNSAFE_EDUCATION_OUTPUT
        ):
            raise PublicSupportAIUnavailable(
                "The educational answer crossed a product safety boundary."
            )
        if (
            response.mode in {"OUT_OF_SCOPE", "SAFETY_REFUSAL"}
            and response.support_handoff_available
        ):
            raise PublicSupportAIUnavailable(
                "Refusals and out-of-scope answers cannot request a support handoff."
            )
        if response.mode == "PRODUCT_CONVERSATION" and response.requested_tools:
            raise PublicSupportAIUnavailable(
                "Normal product conversation cannot request account tools."
            )

    @staticmethod
    def _authoritative_route_ids(
        model_routes: list[str],
        tool_results: list[Any],
    ) -> list[str]:
        routes = [route for route in model_routes if route in PUBLIC_ROUTE_PATHS]
        routes.extend(
            item.route_id
            for item in tool_results
            if item.route_id and item.route_id in PUBLIC_ROUTE_PATHS
        )
        return list(dict.fromkeys(routes))[:12]

    @staticmethod
    def _combined_ai_usage(calls: list[PublicSupportAICall]) -> dict[str, Any]:
        return {
            "model": calls[-1].model if calls else None,
            "input_tokens": sum(item.input_tokens for item in calls),
            "output_tokens": sum(item.output_tokens for item in calls),
            "reasoning_tokens": sum(item.reasoning_tokens for item in calls),
            "estimated_cost_usd": round(
                sum(item.estimated_cost_usd for item in calls),
                8,
            ),
            "latency_ms": sum(item.latency_ms for item in calls),
        }

    async def record_answer_feedback(
        self,
        answer_event_id: UUID,
        payload: PublicChatAnswerFeedbackRequest,
    ) -> PublicChatAnswerFeedbackResponse:
        event = await self.session.get(PublicChatAnswerEvent, answer_event_id)
        session_hash = self._hash(f"session:{payload.session_id}")
        if event is None or not hmac.compare_digest(
            event.session_key_hash, session_hash
        ):
            raise ValueError("The answer event is unavailable for this chat session.")
        existing = await self.session.scalar(
            select(PublicChatAnswerFeedback).where(
                PublicChatAnswerFeedback.answer_event_id == answer_event_id
            )
        )
        if existing is not None:
            if (
                existing.helpful != payload.helpful
                or existing.support_form_requested != payload.support_form_requested
            ):
                raise ValueError(
                    "Feedback was already recorded for this answer."
                ) from None
            return self._feedback_response(existing)

        feedback = PublicChatAnswerFeedback(
            answer_event_id=event.id,
            conversation_id=event.conversation_id,
            user_id=event.user_id,
            session_key_hash=event.session_key_hash,
            helpful=payload.helpful,
            support_form_requested=payload.support_form_requested,
            stage=event.stage,
            mode=event.mode,
            intent=event.intent,
            model=event.model,
            confidence=event.coverage_score,
            source_ids=list(event.source_ids or []),
            validation_failure=event.validation_failure,
            knowledge_gap_reason=event.knowledge_gap_reason,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(feedback)
                await self.session.flush()
        except IntegrityError:
            existing = await self.session.scalar(
                select(PublicChatAnswerFeedback).where(
                    PublicChatAnswerFeedback.answer_event_id == answer_event_id
                )
            )
            if existing is None:
                raise ValueError(
                    "Feedback could not be recorded for this answer."
                ) from None
            if (
                existing.helpful != payload.helpful
                or existing.support_form_requested != payload.support_form_requested
            ):
                raise ValueError(
                    "Feedback was already recorded for this answer."
                ) from None
            return self._feedback_response(existing)
        conversation = (
            await self.session.get(PublicChatConversation, event.conversation_id)
            if event.conversation_id
            else None
        )
        if conversation is not None:
            state = dict(conversation.state_json or {})
            state["previous_feedback_result"] = (
                "helpful" if payload.helpful else "support_requested"
            )
            state["support_form_state"] = (
                "requested" if payload.support_form_requested else "closed"
            )
            conversation.state_json = state
        await self.session.flush()
        return self._feedback_response(feedback)

    @staticmethod
    def _feedback_response(
        feedback: PublicChatAnswerFeedback,
    ) -> PublicChatAnswerFeedbackResponse:
        return PublicChatAnswerFeedbackResponse(
            status="recorded",
            message=(
                "Great! Ready when you are."
                if feedback.helpful
                else "Thanks. The Support form is ready when you are."
            ),
            support_form_requested=feedback.support_form_requested,
        )

    async def submit_inquiry(self, payload: PublicInquiryRequest) -> PublicInquiry:
        event = await self.session.get(PublicChatAnswerEvent, payload.answer_event_id)
        session_hash = self._hash(f"session:{payload.session_id}")
        if event is None or not hmac.compare_digest(
            event.session_key_hash, session_hash
        ):
            raise ValueError("The answer event is unavailable for this chat session.")
        feedback = await self.session.scalar(
            select(PublicChatAnswerFeedback).where(
                PublicChatAnswerFeedback.answer_event_id == event.id
            )
        )
        if feedback is None or not feedback.support_form_requested:
            raise ValueError(
                "Choose 'No. Submit a support form' before sending an inquiry."
            )
        if feedback.inquiry_id is not None:
            linked = await self.session.get(PublicInquiry, feedback.inquiry_id)
            if linked is not None:
                return linked
        existing = await self.session.scalar(
            select(PublicInquiry).where(
                PublicInquiry.idempotency_key == payload.idempotency_key
            )
        )
        if existing is not None:
            if str((existing.support_metadata or {}).get("answer_event_id")) != str(
                event.id
            ):
                raise ValueError(
                    "The inquiry key belongs to a different answer."
                ) from None
            feedback.inquiry_id = existing.id
            return existing
        now = datetime.now(UTC)
        inquiry = PublicInquiry(
            reference=self._reference(),
            name=_clean_text(payload.profile.name, maximum=120),
            normalized_email=normalize_email(str(payload.profile.email)),
            category=payload.category,
            details=_clean_text(payload.details, maximum=4000, preserve_lines=True),
            source_page=_safe_source_page(payload.source_page),
            referrer=_clean_optional(payload.referrer, maximum=500),
            attribution={
                key: value
                for key, value in {
                    "utm_source": _clean_optional(payload.utm_source, maximum=120),
                    "utm_medium": _clean_optional(payload.utm_medium, maximum=120),
                    "utm_campaign": _clean_optional(payload.utm_campaign, maximum=120),
                }.items()
                if value
            },
            support_metadata={
                "answer_event_id": str(event.id),
                "stage": event.stage,
                "mode": event.mode,
                "intent": event.intent,
                "model": event.model,
                "confidence": str(event.coverage_score),
                "source_ids": list(event.source_ids or []),
                "validation_failure": event.validation_failure,
                "knowledge_gap_reason": event.knowledge_gap_reason,
            },
            knowledge_gap_category=_clean_text(
                feedback.knowledge_gap_reason or "user_requested_support",
                maximum=80,
            ),
            idempotency_key=payload.idempotency_key,
            status="received",
            submitted_at=now,
            retain_until=now + timedelta(days=self.settings.public_chat_inquiry_retention_days),
        )
        try:
            async with self.session.begin_nested():
                self.session.add(inquiry)
                await self.session.flush()
        except IntegrityError:
            existing = await self.session.scalar(
                select(PublicInquiry).where(
                    PublicInquiry.idempotency_key == payload.idempotency_key
                )
            )
            if existing is None:
                raise ValueError("The inquiry could not be recorded.") from None
            if str((existing.support_metadata or {}).get("answer_event_id")) != str(
                event.id
            ):
                raise ValueError(
                    "The inquiry key belongs to a different answer."
                ) from None
            feedback.inquiry_id = existing.id
            return existing
        feedback.inquiry_id = inquiry.id
        conversation = (
            await self.session.get(PublicChatConversation, event.conversation_id)
            if event.conversation_id
            else None
        )
        if conversation is not None:
            state = dict(conversation.state_json or {})
            state["support_form_state"] = "submitted"
            state["inquiry_state"] = "submitted"
            conversation.state_json = state
        await self._ensure_email_deliveries(inquiry)
        return inquiry

    async def record_rating(
        self,
        payload: PublicInquiryRatingRequest,
    ) -> PublicInquiryRating:
        inquiry = await self.session.scalar(
            select(PublicInquiry).where(PublicInquiry.reference == payload.reference)
        )
        if inquiry is None or not self.feedback_token_matches(
            inquiry, payload.feedback_token
        ):
            raise ValueError("Inquiry reference or feedback token is invalid")
        existing = await self.session.scalar(
            select(PublicInquiryRating).where(
                PublicInquiryRating.inquiry_id == inquiry.id
            )
        )
        if existing is not None:
            return existing
        rating = PublicInquiryRating(
            inquiry_id=inquiry.id,
            rating=payload.rating,
            helpful=payload.helpful,
            feedback=_clean_optional(payload.feedback, maximum=800),
            created_at=datetime.now(UTC),
        )
        self.session.add(rating)
        await self.session.flush()
        return rating

    async def process_due(
        self,
        *,
        inquiry_id: UUID | None = None,
        limit: int = 25,
    ) -> dict[str, int]:
        now = datetime.now(UTC)
        legacy_delivery_cleanup = update(PublicInquiryEmailDelivery).where(
                PublicInquiryEmailDelivery.recipient_kind == "office",
                PublicInquiryEmailDelivery.status.not_in({"sent", "cancelled"}),
        )
        if inquiry_id is not None:
            legacy_delivery_cleanup = legacy_delivery_cleanup.where(
                PublicInquiryEmailDelivery.inquiry_id == inquiry_id
            )
        await self.session.execute(
            legacy_delivery_cleanup.values(
                status="cancelled",
                next_retry_at=None,
                last_error=None,
            )
        )
        await self.session.flush()
        abandoned_before = now - timedelta(
            minutes=self.settings.public_chat_email_claim_timeout_minutes
        )
        query = select(PublicInquiryEmailDelivery).where(
            or_(
                and_(
                    PublicInquiryEmailDelivery.status.in_({"pending", "retryable"}),
                    or_(
                        PublicInquiryEmailDelivery.next_retry_at.is_(None),
                        PublicInquiryEmailDelivery.next_retry_at <= now,
                    ),
                ),
                and_(
                    PublicInquiryEmailDelivery.status == "sending",
                    PublicInquiryEmailDelivery.last_attempt_at.is_not(None),
                    PublicInquiryEmailDelivery.last_attempt_at <= abandoned_before,
                ),
            ),
        )
        if inquiry_id is not None:
            query = query.where(PublicInquiryEmailDelivery.inquiry_id == inquiry_id)
        query = (
            query
            .order_by(PublicInquiryEmailDelivery.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list((await self.session.scalars(query)).all())
        result = {"processed": 0, "sent": 0, "retryable": 0, "failed": 0}
        for row in rows:
            row.status = "sending"
            row.attempt_count += 1
            row.last_attempt_at = datetime.now(UTC)
            row.next_retry_at = None
            await self.session.commit()
            try:
                inquiry = await self.session.get(PublicInquiry, row.inquiry_id)
                if inquiry is None:
                    raise RuntimeError("Inquiry disappeared before email delivery")
                subject, text_body, html_body = self._render_email(inquiry, row.recipient_kind)
                message_id = await AuthEmailService(self.settings).send_transactional(
                    recipient=row.recipient,
                    subject=subject,
                    text_body=text_body,
                    html_body=html_body,
                    idempotency_key=row.event_key,
                    purpose=f"public_inquiry_{row.recipient_kind}",
                    sender_email=self.settings.public_chat_inquiry_email,
                    sender_name="Hilal Markets",
                    reply_to=self.settings.public_chat_inquiry_email,
                    bcc=[self.settings.public_chat_inquiry_email],
                )
            except EmailDeliveryError as exc:
                refreshed = await self.session.get(PublicInquiryEmailDelivery, row.id)
                if refreshed is None:
                    continue
                row = refreshed
                exhausted = (
                    not exc.retryable
                    or row.attempt_count >= self.settings.public_chat_email_max_attempts
                )
                row.status = "failed" if exhausted else "retryable"
                provider_status = (
                    f":smtp_status_{exc.provider_status}"
                    if exc.provider_status is not None
                    else ""
                )
                row.last_error = f"{exc.code}{provider_status}: {str(exc)}"[:500]
                row.next_retry_at = (
                    None
                    if exhausted
                    else datetime.now(UTC)
                    + timedelta(minutes=self.settings.public_chat_email_retry_minutes)
                )
                logger.warning(
                    "Public inquiry email delivery failed kind=%s code=%s "
                    "provider_status=%s attempt=%s retryable=%s",
                    row.recipient_kind,
                    exc.code,
                    exc.provider_status,
                    row.attempt_count,
                    not exhausted,
                )
                result["failed" if exhausted else "retryable"] += 1
            except Exception as exc:
                refreshed = await self.session.get(PublicInquiryEmailDelivery, row.id)
                if refreshed is None:
                    continue
                row = refreshed
                row.status = "failed"
                row.last_error = f"render_failed: {exc.__class__.__name__}"[:500]
                row.next_retry_at = None
                result["failed"] += 1
            else:
                refreshed = await self.session.get(PublicInquiryEmailDelivery, row.id)
                if refreshed is None:
                    continue
                row = refreshed
                row.status = "sent"
                row.provider_message_id = str(message_id)[:255]
                row.sent_at = datetime.now(UTC)
                row.last_error = None
                result["sent"] += 1
            result["processed"] += 1
            await self.session.commit()
        return result

    async def cleanup_expired(self) -> dict[str, int]:
        now = datetime.now(UTC)
        conversations = await self.session.execute(
            delete(PublicChatConversation).where(
                PublicChatConversation.expires_at <= now
            )
        )
        events = await self.session.execute(
            delete(PublicChatAnswerEvent).where(
                PublicChatAnswerEvent.retain_until <= now
            )
        )
        inquiries = list(
            (
                await self.session.scalars(
                    select(PublicInquiry).where(
                        PublicInquiry.retain_until <= now,
                        PublicInquiry.status != "redacted",
                    )
                )
            ).all()
        )
        for inquiry in inquiries:
            await self.redact_inquiry(
                inquiry,
                reason="Inquiry content removed under the retention policy.",
            )
        await self.session.flush()
        return {
            "conversations_deleted": int(getattr(conversations, "rowcount", 0) or 0),
            "answer_events_deleted": int(getattr(events, "rowcount", 0) or 0),
            "inquiries_redacted": len(inquiries),
        }

    async def redact_inquiry(self, inquiry: PublicInquiry, *, reason: str) -> None:
        redacted_address = f"redacted+{inquiry.id}@invalid.local"
        inquiry.name = "Redacted"
        inquiry.normalized_email = redacted_address
        inquiry.details = reason
        inquiry.referrer = None
        inquiry.attribution = {}
        inquiry.deletion_requested_at = inquiry.deletion_requested_at or datetime.now(UTC)
        inquiry.status = "redacted"
        deliveries = list(
            (
                await self.session.scalars(
                    select(PublicInquiryEmailDelivery).where(
                        PublicInquiryEmailDelivery.inquiry_id == inquiry.id
                    )
                )
            ).all()
        )
        for delivery in deliveries:
            delivery.recipient = redacted_address
            delivery.last_error = None
            delivery.next_retry_at = None
            if delivery.status != "sent":
                delivery.status = "cancelled"
        await self.session.flush()

    def feedback_token(self, inquiry: PublicInquiry) -> str:
        return hmac.new(
            self.settings.app_secret_key.get_secret_value().encode(),
            f"public-inquiry-feedback:{inquiry.id}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def feedback_token_matches(self, inquiry: PublicInquiry, supplied: str) -> bool:
        return hmac.compare_digest(self.feedback_token(inquiry), supplied)

    async def email_delivery_state(self, inquiry_id: UUID) -> str:
        states = [
            state
            for state in (
                await self.session.scalars(
                    select(PublicInquiryEmailDelivery.status).where(
                        PublicInquiryEmailDelivery.inquiry_id == inquiry_id
                    )
                )
            ).all()
            if state != "cancelled"
        ]
        if states and all(state == "sent" for state in states):
            return "sent"
        if any(state == "sent" for state in states):
            return "partial"
        if any(state in {"retryable", "failed"} for state in states):
            return "retrying"
        return "queued"

    async def _ensure_email_deliveries(self, inquiry: PublicInquiry) -> None:
        recipients = (("customer", inquiry.normalized_email),)
        now = datetime.now(UTC)
        for kind, recipient in recipients:
            event_key = f"public-inquiry:{inquiry.id}:{kind}"
            existing = await self.session.scalar(
                select(PublicInquiryEmailDelivery.id).where(
                    PublicInquiryEmailDelivery.event_key == event_key
                )
            )
            if existing is not None:
                continue
            self.session.add(
                PublicInquiryEmailDelivery(
                    inquiry_id=inquiry.id,
                    event_key=event_key,
                    recipient_kind=kind,
                    recipient=recipient,
                    status="pending",
                    attempt_count=0,
                    next_retry_at=now,
                    created_at=now,
                )
            )
        await self.session.flush()

    def _render_email(
        self,
        inquiry: PublicInquiry,
        recipient_kind: str,
    ) -> tuple[str, str, str]:
        if recipient_kind == "customer":
            subject = f"We received your Hilal Markets inquiry {inquiry.reference}"
            text = (
                f"Assalamu Alaikum {inquiry.name},\n\n"
                "JazakAllahu khayran for reaching out to Hilal Markets. Your message "
                "has reached our team safely, and a real person will review it with care.\n\n"
                "We know that asking for help often comes after spending time trying to "
                "work something out. We do not want to leave you wondering what happens "
                "next: in sha Allah, you can expect a reply in less than 24 hours.\n\n"
                f"Reference: {inquiry.reference}\n"
                f"Your message:\n{inquiry.details}\n\n"
                "If there is anything important you would like to add, simply reply to "
                "this email and it will reach our team.\n\n"
                "May Allah place barakah in your time and make matters easy for you.\n\n"
                "Warmly,\n"
                "Hilal Markets Support\n"
            )
        else:
            subject = f"Public inquiry {inquiry.reference}: {inquiry.category.replace('_', ' ')}"
            attribution = ", ".join(
                f"{key}={value}" for key, value in inquiry.attribution.items()
            ) or "not provided"
            support_metadata = ", ".join(
                f"{key}={value}"
                for key, value in (inquiry.support_metadata or {}).items()
                if value not in (None, "", [])
            ) or "not provided"
            text = (
                "A public Hilal Markets inquiry was received.\n\n"
                "Summary\n"
                f"{inquiry.category.replace('_', ' ').title()} inquiry from {inquiry.name}: "
                f"{inquiry.details[:280]}\n\n"
                f"Reference: {inquiry.reference}\n"
                f"Name: {inquiry.name}\n"
                f"Email: {inquiry.normalized_email}\n"
                f"Category: {inquiry.category}\n"
                f"Submitted: {inquiry.submitted_at.isoformat()}\n"
                f"Source page: {inquiry.source_page}\n"
                f"Referrer: {inquiry.referrer or 'not provided'}\n"
                f"Attribution: {attribution}\n"
                f"Knowledge gap: {inquiry.knowledge_gap_category}\n\n"
                f"AI answer metadata: {support_metadata}\n\n"
                f"Inquiry:\n{inquiry.details}\n"
            )
        escaped = html.escape(text).replace("\n", "<br>")
        html_body = (
            '<div style="font-family:Arial,sans-serif;line-height:1.55;color:#2b2e35">'
            f"{escaped}</div>"
        )
        return subject, text, html_body

    def _hash(self, value: str) -> str:
        return hmac.new(
            self.settings.app_secret_key.get_secret_value().encode(),
            value.encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _reference() -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%d")
        return f"HM-{stamp}-{secrets.token_hex(4).upper()}"

    @staticmethod
    def _related_link(route_id: str) -> PublicChatRelatedLink:
        label, path = PUBLIC_ROUTE_PATHS[route_id]
        return PublicChatRelatedLink(route_id=route_id, label=label, path=path)


def issue_public_chat_csrf(settings: Settings) -> tuple[str, str]:
    nonce = secrets.token_urlsafe(32)
    return nonce, public_chat_csrf_token(settings, nonce)


def public_chat_csrf_token(settings: Settings, nonce: str) -> str:
    return hmac.new(
        settings.app_secret_key.get_secret_value().encode(),
        f"public-chat:{nonce}".encode(),
        hashlib.sha256,
    ).hexdigest()


def public_chat_csrf_matches(
    settings: Settings,
    nonce: str | None,
    supplied: str | None,
) -> bool:
    if not nonce or not supplied or len(nonce) > 128:
        return False
    return hmac.compare_digest(public_chat_csrf_token(settings, nonce), supplied)


def mask_email(value: str) -> str:
    local, separator, domain = value.partition("@")
    if not separator:
        return "hidden"
    visible = local[:1]
    return f"{visible}{'*' * max(2, min(8, len(local) - 1))}@{domain}"


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in _TOKEN_RE.findall(value)
        if token.casefold() not in _STOPWORDS and len(token) > 1
    }


def _clean_text(value: str, *, maximum: int, preserve_lines: bool = False) -> str:
    cleaned = html.unescape(_TAG_RE.sub(" ", _CONTROL_RE.sub("", value)))
    if preserve_lines:
        cleaned = "\n".join(" ".join(line.split()) for line in cleaned.splitlines())
        cleaned = "\n".join(line for line in cleaned.splitlines() if line).strip()
    else:
        cleaned = " ".join(cleaned.split())
    return cleaned[:maximum]


def _clean_optional(value: str | None, *, maximum: int) -> str | None:
    if not value:
        return None
    cleaned = _clean_text(value, maximum=maximum)
    return cleaned or None


def _safe_source_page(value: str) -> str:
    cleaned = value.strip()
    if not cleaned.startswith("/") or cleaned.startswith("//"):
        return "/"
    return cleaned[:240]


def _product_entities(value: str, *, previous: list[str]) -> list[str]:
    patterns = {
        "BTC": r"\bBTC\b|\bBitcoin\b",
        "ETH": r"\bETH\b|\bEthereum\b",
        "SOL": r"\bSOL\b|\bSolana\b",
        "Telegram": r"\bTelegram\b",
        "Watch Plan": r"\bWatch\s+Plan\b|\bmonitor\b",
        "Scanner": r"\bScanner\b|\bmarket\s+check\b",
        "Evidence Passport": r"\bPassport\b|\bscreening\s+record\b",
        "My Screened Watchlist": r"\bscreened\s+watchlist\b|\bsaved\s+assets\b",
    }
    entities = list(previous)
    for label, pattern in patterns.items():
        if re.search(pattern, value, flags=re.IGNORECASE):
            entities.append(label)
    return list(dict.fromkeys(entities))[-20:]


def _is_greeting(value: str) -> bool:
    return any(pattern.search(value) for pattern in _GREETING_PATTERNS)


def _explicit_support_requested(value: str) -> bool:
    return any(pattern.search(value) for pattern in _EXPLICIT_SUPPORT_PATTERNS)


def _safe_greeting(
    state: dict[str, Any],
    *,
    supplied_name: str | None = None,
) -> str:
    profile = dict(state.get("visitor_profile") or {})
    name = _clean_optional(
        str(supplied_name or profile.get("name") or ""),
        maximum=80,
    )
    salutation = f"Hi {name.split()[0]}!" if name else "Hi!"
    return f"{salutation} How can I help you with Hilal Markets today?"


def _public_ai_state(state: dict[str, Any]) -> dict[str, Any]:
    profile = dict(state.get("visitor_profile") or {})
    return {
        "visitor_profile": {"name": profile.get("name")} if profile.get("name") else None,
        "current_topic": state.get("current_topic"),
        "current_mode": state.get("current_mode"),
        "last_question": state.get("last_question"),
        "last_answer": state.get("last_answer"),
        "last_answer_event_id": state.get("last_answer_event_id"),
        "previous_source_ids": list(state.get("previous_source_ids") or [])[-20:],
        "product_entities": list(state.get("product_entities") or [])[-20:],
        "current_troubleshooting_step": state.get("current_troubleshooting_step"),
        "pending_clarification": state.get("pending_clarification"),
        "inquiry_state": state.get("inquiry_state"),
        "support_form_state": state.get("support_form_state"),
        "previous_feedback_result": state.get("previous_feedback_result"),
        "rating_state": state.get("rating_state"),
        "questions_answered": list(state.get("questions_answered") or [])[-10:],
    }


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
