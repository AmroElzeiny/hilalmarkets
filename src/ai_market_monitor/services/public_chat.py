from __future__ import annotations

import hashlib
import hmac
import html
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import PLAN_DEFINITIONS, PUBLIC_PLAN_CODES
from ai_market_monitor.core.site_content import HELP_CATEGORIES, PUBLIC_PAGES, PURCHASE_FAQS
from ai_market_monitor.db.models import (
    PublicChatAnswerEvent,
    PublicInquiry,
    PublicInquiryEmailDelivery,
    PublicInquiryRating,
)
from ai_market_monitor.schemas.public_chat import (
    PublicChatAnswerRequest,
    PublicChatAnswerResponse,
    PublicChatRelatedLink,
    PublicInquiryRatingRequest,
    PublicInquiryRequest,
)
from ai_market_monitor.services.email_delivery import AuthEmailService, EmailDeliveryError
from ai_market_monitor.services.web_auth import normalize_email

PUBLIC_CHAT_PROFILE_STORAGE_KEY = "hm-public-chat-profile-v1"
PUBLIC_CHAT_CSRF_COOKIE = "hm_public_chat_csrf"

PUBLIC_ROUTE_PATHS: dict[str, tuple[str, str]] = {
    "home": ("HilalMarkets", "/"),
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
    re.compile(r"\bwhat\s+(coin|token).*(buy|pump|moon)\b", re.IGNORECASE),
    re.compile(r"\b(price prediction|guaranteed return|guaranteed profit)\b", re.IGNORECASE),
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
_INJECTION_PATTERNS = (
    re.compile(r"\b(ignore|reveal|override)\b.*\b(system|instructions|prompt|rules)\b", re.I),
    re.compile(r"\b(show|print|give)\b.*\b(secret|api key|credential)\b", re.I),
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
        return _tokens(" ".join((self.title, self.answer, *self.keywords)))


class PublicKnowledgeService:
    """Retrieve only from versioned, public product content owned by the application."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.entries = self._entries()

    def answer(self, question: str) -> tuple[str, str, float, list[str], list[str], str | None]:
        cleaned = _clean_text(question, maximum=800)
        if any(pattern.search(cleaned) for pattern in _INJECTION_PATTERNS):
            return (
                "refused",
                "I can explain verified HilalMarkets product information, but I cannot reveal "
                "internal instructions, credentials, or private system details.",
                1.0,
                ["boundary:public-assistant-security"],
                ["trust_safety"],
                "security_boundary",
            )
        if any(pattern.search(cleaned) for pattern in _ADVICE_PATTERNS):
            return (
                "refused",
                "HilalMarkets does not tell you what to buy or sell, predict pumps, or provide "
                "leverage advice. It helps you monitor your own crypto spot rules with evidence.",
                1.0,
                ["boundary:no-investment-advice"],
                ["risk_disclosure", "how_it_works"],
                "investment_advice",
            )
        if any(pattern.search(cleaned) for pattern in _RELIGIOUS_RULING_PATTERNS):
            return (
                "refused",
                "HilalMarkets does not issue religious rulings. It shows the status, scope, "
                "methodology, evidence, and qualified human decision recorded for each asset.",
                1.0,
                ["boundary:no-religious-rulings"],
                ["how_we_screen"],
                "religious_ruling",
            )
        if any(pattern.search(cleaned) for pattern in _PRIVATE_ACCOUNT_PATTERNS):
            return (
                "refused",
                "I cannot inspect private accounts or Watch Plans from this public chat. Sign in "
                "to use the authenticated dashboard and support tools.",
                1.0,
                ["boundary:no-private-account-access"],
                ["dashboard_entry", "help"],
                "private_account",
            )

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
                "the HilalMarkets team.",
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

    def _entries(self) -> tuple[PublicKnowledgeEntry, ...]:
        entries: list[PublicKnowledgeEntry] = []
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
                    title="How much does HilalMarkets cost during private beta?",
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


class PublicChatService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings
        self.knowledge = PublicKnowledgeService(settings)

    async def answer(self, payload: PublicChatAnswerRequest) -> PublicChatAnswerResponse:
        status, message, score, source_ids, route_ids, gap = self.knowledge.answer(
            payload.question
        )
        now = datetime.now(UTC)
        event = PublicChatAnswerEvent(
            session_key_hash=self._hash(f"session:{payload.session_id}"),
            question_hash=self._hash(f"question:{payload.question.casefold()}"),
            outcome=status,
            coverage_score=Decimal(str(round(score, 5))),
            source_ids=source_ids,
            related_route_ids=route_ids,
            created_at=now,
            retain_until=now
            + timedelta(days=self.settings.public_chat_answer_audit_retention_days),
        )
        self.session.add(event)
        await self.session.flush()
        return PublicChatAnswerResponse(
            status=status,
            message=message,
            source_ids=source_ids,
            related_links=[self._related_link(route_id) for route_id in route_ids],
            coverage_score=score,
            show_inquiry_form=status == "unsupported",
            knowledge_gap_category=gap,
        )

    async def submit_inquiry(self, payload: PublicInquiryRequest) -> PublicInquiry:
        existing = await self.session.scalar(
            select(PublicInquiry).where(
                PublicInquiry.idempotency_key == payload.idempotency_key
            )
        )
        if existing is not None:
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
            knowledge_gap_category=_clean_text(
                payload.knowledge_gap_category, maximum=80
            ),
            idempotency_key=payload.idempotency_key,
            status="received",
            submitted_at=now,
            retain_until=now + timedelta(days=self.settings.public_chat_inquiry_retention_days),
        )
        self.session.add(inquiry)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.session.scalar(
                select(PublicInquiry).where(
                    PublicInquiry.idempotency_key == payload.idempotency_key
                )
            )
            if existing is None:
                raise
            return existing
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
                )
            except EmailDeliveryError as exc:
                refreshed = await self.session.get(PublicInquiryEmailDelivery, row.id)
                if refreshed is None:
                    continue
                row = refreshed
                exhausted = row.attempt_count >= self.settings.public_chat_email_max_attempts
                row.status = "failed" if exhausted else "retryable"
                row.last_error = f"{exc.code}: {str(exc)}"[:500]
                row.next_retry_at = (
                    None
                    if exhausted
                    else datetime.now(UTC)
                    + timedelta(minutes=self.settings.public_chat_email_retry_minutes)
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
            "answer_events_deleted": int(events.rowcount or 0),
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
        states = list(
            (
                await self.session.scalars(
                    select(PublicInquiryEmailDelivery.status).where(
                        PublicInquiryEmailDelivery.inquiry_id == inquiry_id
                    )
                )
            ).all()
        )
        if states and all(state == "sent" for state in states):
            return "sent"
        if any(state == "sent" for state in states):
            return "partial"
        if any(state in {"retryable", "failed"} for state in states):
            return "retrying"
        return "queued"

    async def _ensure_email_deliveries(self, inquiry: PublicInquiry) -> None:
        recipients = (
            ("customer", inquiry.normalized_email),
            ("office", self.settings.public_chat_inquiry_email),
        )
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
        base_url = str(self.settings.public_base_url).rstrip("/")
        if recipient_kind == "customer":
            first_name = inquiry.name.split()[0]
            subject = f"HilalMarkets received your inquiry {inquiry.reference}"
            text = (
                f"Hello {first_name},\n\n"
                "We received your HilalMarkets inquiry. A team member will review it; "
                "response timing depends on the question and does not imply a fixed SLA.\n\n"
                f"Reference: {inquiry.reference}\n"
                f"Your question: {inquiry.details}\n\n"
                f"Help Center: {base_url}/help\n"
                f"HilalMarkets: {base_url}/\n"
                f"Support: {base_url}/contact\n"
                f"Privacy: {base_url}/privacy\n"
            )
        else:
            subject = f"Public inquiry {inquiry.reference}: {inquiry.category.replace('_', ' ')}"
            attribution = ", ".join(
                f"{key}={value}" for key, value in inquiry.attribution.items()
            ) or "not provided"
            text = (
                "A public HilalMarkets inquiry was received.\n\n"
                f"Reference: {inquiry.reference}\n"
                f"Name: {inquiry.name}\n"
                f"Email: {inquiry.normalized_email}\n"
                f"Category: {inquiry.category}\n"
                f"Submitted: {inquiry.submitted_at.isoformat()}\n"
                f"Source page: {inquiry.source_page}\n"
                f"Referrer: {inquiry.referrer or 'not provided'}\n"
                f"Attribution: {attribution}\n"
                f"Knowledge gap: {inquiry.knowledge_gap_category}\n\n"
                f"Inquiry:\n{inquiry.details}\n"
            )
        escaped = html.escape(text).replace("\n", "<br>")
        html_body = (
            '<div style="font-family:Arial,sans-serif;line-height:1.55;color:#16322c">'
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
