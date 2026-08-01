from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.security import (
    InvalidContinuationToken,
    SystemBrainTokenService,
    opaque_token,
    token_digest,
    verify_password,
)
from ai_market_monitor.db.models import (
    AgentRun,
    AgentToolCall,
    AISetupChatMessage,
    AISetupChatSession,
    AIUsageEvent,
    Alert,
    AuditEvent,
    CapabilityAliasProposal,
    CapabilityClarificationEvidence,
    CapabilityExtension,
    CapabilityExtensionAttempt,
    CapabilityResolutionEvent,
    PublicChatAnswerEvent,
    PublicChatAnswerFeedback,
    PublicInquiry,
    PublicInquiryEmailDelivery,
    PublicInquiryRating,
    ScanJob,
    Strategy,
    SystemBrainAuthChallenge,
    SystemBrainLoginAttempt,
    SystemBrainSession,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, StrategyStatus
from ai_market_monitor.engine.capabilities import all_capabilities
from ai_market_monitor.engine.capability_compatibility import compatibility_report
from ai_market_monitor.engine.capability_quality import capability_quality_snapshot
from ai_market_monitor.engine.capability_resolver import CapabilityResolutionReport
from ai_market_monitor.services.agent_policy import FORBIDDEN_AGENT_TOOLS
from ai_market_monitor.services.ai_usage_context import (
    current_ai_usage_correlation_id,
)
from ai_market_monitor.services.email_delivery import AuthEmailService

SYSTEM_BRAIN_PENDING_COOKIE = "traceedge_system_brain_pending"
SYSTEM_BRAIN_SESSION_COOKIE = "traceedge_system_brain_session"
_EXCLUDED_USER_EMAILS = {
    "amroelzene@gmail.com",
    "amroelzeiny@gmail.com",
    "loadsleas@gmail.com",
    "uxui.fa@gmail.com",
    "fatima00505@gmail.com",
}


class SystemBrainAccessError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class SystemBrainAuthService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tokens = SystemBrainTokenService(settings)

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.system_brain_authorized_emails
            and self.settings.system_brain_admin_password_hash
        )

    async def begin_login(
        self,
        session: AsyncSession,
        *,
        username: str,
        password: str,
        remote_ip: str | None,
    ) -> str:
        if not self.configured:
            raise SystemBrainAccessError(
                "system_brain_not_configured",
                "System Brain access is not configured on this server.",
                status_code=503,
            )
        normalized = username.strip().casefold()
        ip_hash = self._private_hash(remote_ip or "unknown")
        username_hash = self._private_hash(normalized)
        since = datetime.now(UTC) - timedelta(minutes=15)
        failures = await session.scalar(
            select(func.count(SystemBrainLoginAttempt.id)).where(
                SystemBrainLoginAttempt.ip_hash == ip_hash,
                SystemBrainLoginAttempt.successful.is_(False),
                SystemBrainLoginAttempt.created_at >= since,
            )
        )
        if int(failures or 0) >= self.settings.system_brain_login_attempts_per_15_minutes:
            raise SystemBrainAccessError(
                "login_rate_limited",
                "Too many access attempts. Wait 15 minutes and try again.",
                status_code=429,
            )
        password_hash = (
            self.settings.system_brain_admin_password_hash.get_secret_value()
            if self.settings.system_brain_admin_password_hash
            else None
        )
        valid = any(
            hmac.compare_digest(normalized, email)
            for email in self.settings.system_brain_authorized_emails
        )
        valid = valid and verify_password(password, password_hash)
        session.add(
            SystemBrainLoginAttempt(
                ip_hash=ip_hash,
                username_hash=username_hash,
                successful=valid,
                created_at=datetime.now(UTC),
            )
        )
        if not valid:
            await session.commit()
            raise SystemBrainAccessError(
                "invalid_credentials",
                "The username or password is incorrect.",
                status_code=401,
            )
        now = datetime.now(UTC)
        active = list(
            (
                await session.scalars(
                    select(SystemBrainAuthChallenge).where(
                        SystemBrainAuthChallenge.email == normalized,
                        SystemBrainAuthChallenge.consumed_at.is_(None),
                    )
                )
            ).all()
        )
        for old in active:
            old.consumed_at = now
        code = self.settings.auth_test_fixed_code or f"{secrets.randbelow(1_000_000):06d}"
        challenge = SystemBrainAuthChallenge(
            email=normalized,
            code_digest=self._otp_digest(normalized, code),
            created_at=now,
            expires_at=now + timedelta(minutes=self.settings.system_brain_otp_ttl_minutes),
            attempts=0,
            max_attempts=self.settings.system_brain_otp_max_attempts,
            requested_ip_hash=ip_hash,
        )
        session.add(challenge)
        await session.flush()
        try:
            await AuthEmailService(self.settings).send_code(
                recipient=normalized,
                code=code,
                purpose="system_brain",
            )
        except Exception:
            await session.rollback()
            raise
        self._audit(
            session,
            action="system_brain.otp_sent",
            target_id=str(challenge.id),
            ip_hash=ip_hash,
        )
        await session.commit()
        return self.tokens.issue_pending(challenge.id)

    async def verify_otp(
        self,
        session: AsyncSession,
        *,
        pending_cookie: str,
        code: str,
        remote_ip: str | None,
        user_agent: str | None,
    ) -> str:
        try:
            payload = self.tokens.decode_pending(pending_cookie)
            challenge_id = UUID(payload["challenge_id"])
        except (InvalidContinuationToken, KeyError, ValueError) as exc:
            raise SystemBrainAccessError(
                "otp_expired",
                "This verification request is invalid or expired. Sign in again.",
                status_code=401,
            ) from exc
        challenge = await session.get(SystemBrainAuthChallenge, challenge_id)
        now = datetime.now(UTC)
        if (
            challenge is None
            or challenge.consumed_at is not None
            or _aware(challenge.expires_at) <= now
        ):
            raise SystemBrainAccessError(
                "otp_expired",
                "This verification code has expired. Sign in again.",
                status_code=401,
            )
        if challenge.attempts >= challenge.max_attempts:
            raise SystemBrainAccessError(
                "otp_attempts_exceeded",
                "Too many incorrect codes. Sign in again to request a new code.",
                status_code=429,
            )
        challenge.attempts += 1
        if not hmac.compare_digest(
            challenge.code_digest,
            self._otp_digest(challenge.email, code.strip()),
        ):
            await session.commit()
            raise SystemBrainAccessError(
                "invalid_otp",
                "That code is incorrect. Check the latest email and try again.",
                status_code=401,
            )
        challenge.consumed_at = now
        raw_token = opaque_token()
        admin_session = SystemBrainSession(
            email=challenge.email,
            session_digest=token_digest(raw_token),
            created_at=now,
            expires_at=now + timedelta(hours=self.settings.system_brain_session_hours),
            last_seen_at=now,
            ip_hash=self._private_hash(remote_ip or "unknown"),
            user_agent=(user_agent or "")[:500] or None,
        )
        session.add(admin_session)
        await session.flush()
        self._audit(
            session,
            action="system_brain.login_succeeded",
            target_id=str(admin_session.id),
            ip_hash=admin_session.ip_hash,
        )
        await session.commit()
        return self.tokens.issue_session(admin_session.id, raw_token)

    async def current_session(
        self,
        session: AsyncSession,
        cookie_value: str | None,
    ) -> SystemBrainSession | None:
        if not cookie_value:
            return None
        try:
            payload = self.tokens.decode_session(cookie_value)
            session_id = UUID(payload["session_id"])
            raw_token = payload["token"]
        except (InvalidContinuationToken, KeyError, ValueError):
            return None
        admin_session = await session.get(SystemBrainSession, session_id)
        if (
            admin_session is None
            or admin_session.revoked_at is not None
            or _aware(admin_session.expires_at) <= datetime.now(UTC)
            or not hmac.compare_digest(admin_session.session_digest, token_digest(raw_token))
            or admin_session.email not in self.settings.system_brain_authorized_emails
        ):
            return None
        admin_session.last_seen_at = datetime.now(UTC)
        await session.commit()
        return admin_session

    async def logout(self, session: AsyncSession, cookie_value: str | None) -> None:
        admin_session = await self.current_session(session, cookie_value)
        if admin_session is None:
            return
        admin_session.revoked_at = datetime.now(UTC)
        self._audit(
            session,
            action="system_brain.logout",
            target_id=str(admin_session.id),
            ip_hash=admin_session.ip_hash,
        )
        await session.commit()

    def _private_hash(self, value: str) -> str:
        return hmac.new(
            self.settings.app_secret_key.get_secret_value().encode(),
            value.encode(),
            hashlib.sha256,
        ).hexdigest()

    def _otp_digest(self, email: str, code: str) -> str:
        return self._private_hash(f"system-brain:{email}:{code}")

    @staticmethod
    def _audit(
        session: AsyncSession,
        *,
        action: str,
        target_id: str,
        ip_hash: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        session.add(
            AuditEvent(
                actor_user_id=None,
                actor_type="system_brain_admin",
                action=action,
                target_type="system_brain",
                target_id=target_id,
                ip_hash=ip_hash,
                metadata_redacted=metadata or {},
                created_at=datetime.now(UTC),
            )
        )


class CapabilityCoverageService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def record_resolution(
        self,
        session: AsyncSession,
        *,
        chat: AISetupChatSession,
        report: CapabilityResolutionReport,
        source: str = "setup_chat",
    ) -> None:
        latest_sequence = await session.scalar(
            select(func.max(AISetupChatMessage.sequence)).where(
                AISetupChatMessage.session_id == chat.id
            )
        )
        for fragment in report.fragments:
            candidates = [candidate.to_dict() for candidate in fragment.candidates]
            top = candidates[0] if candidates else {}
            provider_requirement = None
            status: str = fragment.status
            if top and top.get("availability") != "available":
                status = "provider_blocked"
                provider_requirement = str(top.get("availability") or "provider_required")
            normalized = _normalize_fragment(fragment.fragment)
            fingerprint = hashlib.sha256(
                f"{chat.id}:{latest_sequence}:{source}:{normalized}".encode()
            ).hexdigest()
            exists = await session.scalar(
                select(CapabilityResolutionEvent.id).where(
                    CapabilityResolutionEvent.event_fingerprint == fingerprint
                )
            )
            if exists:
                continue
            session.add(
                CapabilityResolutionEvent(
                    user_id=chat.user_id,
                    chat_session_id=chat.id,
                    event_fingerprint=fingerprint,
                    source=source,
                    source_fragment=fragment.fragment[:2000],
                    normalized_fragment=normalized[:500],
                    status=status,
                    selected_capability_key=fragment.selected_capability_key,
                    selection_source=fragment.selection_source,
                    selected_parameters=dict(fragment.selected_parameters or {}),
                    parameters_validated=(
                        True
                        if fragment.status == "matched"
                        and fragment.selected_capability_key
                        and fragment.selected_parameters is not None
                        else None
                    ),
                    candidates=candidates,
                    unknown_terms=list(fragment.unknown_terms),
                    top_confidence=(
                        float(top["confidence"])
                        if isinstance(top.get("confidence"), (int, float))
                        else None
                    ),
                    provider_requirement=provider_requirement,
                    created_at=datetime.now(UTC),
                )
            )

    async def record_clarification_choice(
        self,
        session: AsyncSession,
        *,
        chat: AISetupChatSession,
        option_key: str | None,
        option_value: str | None,
    ) -> None:
        if not option_key or not option_key.startswith("capability_meaning_") or not option_value:
            return
        match = re.search(r"\(([a-z0-9_]+)\)\s*$", option_value.casefold())
        if not match:
            return
        selected_key = match.group(1)
        option_fragment = option_key.removeprefix("capability_meaning_").strip("_")
        unresolved = list(
            (
                await session.scalars(
                    select(CapabilityResolutionEvent)
                    .where(
                        CapabilityResolutionEvent.chat_session_id == chat.id,
                        CapabilityResolutionEvent.selected_capability_key.is_(None),
                    )
                    .order_by(CapabilityResolutionEvent.created_at.desc())
                    .limit(50)
                )
            ).all()
        )
        event = next(
            (
                item
                for item in unresolved
                if re.sub(r"[^a-z0-9]+", "_", item.normalized_fragment).strip("_")[:48]
                == option_fragment
            ),
            unresolved[0] if unresolved else None,
        )
        if event is None:
            return
        event.selected_capability_key = selected_key
        event.selection_source = (
            "user_choice_after_ai"
            if event.selection_source == "ai_reranker"
            else "user_choice"
        )
        top_key = str(event.candidates[0].get("capability_key")) if event.candidates else ""
        if selected_key == top_key:
            return
        proposal = await session.scalar(
            select(CapabilityAliasProposal).where(
                CapabilityAliasProposal.normalized_alias == event.normalized_fragment,
                CapabilityAliasProposal.capability_key == selected_key,
            )
        )
        if proposal is None:
            session.add(
                CapabilityAliasProposal(
                    alias=event.source_fragment[:240],
                    normalized_alias=event.normalized_fragment[:240],
                    capability_key=selected_key,
                    status="pending",
                    evidence_count=1,
                    source_event_ids=[str(event.id)],
                )
            )
        else:
            source_ids = list(proposal.source_event_ids or [])
            if str(event.id) not in source_ids:
                source_ids.append(str(event.id))
                proposal.source_event_ids = source_ids
                proposal.evidence_count += 1

    async def record_usage(
        self,
        session: AsyncSession,
        *,
        chat: AISetupChatSession,
        operation: str,
        usage: dict[str, Any] | None,
    ) -> None:
        if not usage:
            return
        input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        input_details = (
            usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
        )
        output_details = (
            usage.get("output_tokens_details") or usage.get("completion_tokens_details") or {}
        )
        cached = int(input_details.get("cached_tokens") or 0)
        reasoning = int(output_details.get("reasoning_tokens") or 0)
        model = str(usage.get("_traceedge_model") or self.settings.openai_model)
        reasoning_effort = str(
            usage.get("_traceedge_reasoning_effort")
            or self.settings.openai_reasoning_effort
        )
        service_tier = str(
            usage.get("_setup_service_tier")
            or usage.get("_setup_requested_service_tier")
            or "default"
        )
        cost = estimate_usage_cost(
            self.settings,
            model=model,
            usage=usage,
            service_tier=service_tier,
        )
        used_reserved_cost = False
        if input_tokens == 0 and output_tokens == 0:
            reserved_cost = Decimal(
                str(usage.get("_setup_reserved_cost_usd") or 0)
            )
            if reserved_cost > cost:
                cost = reserved_cost
                used_reserved_cost = True
        recorded_usage = dict(usage)
        correlation_id = current_ai_usage_correlation_id()
        if correlation_id:
            recorded_usage["_traceedge_correlation_id"] = correlation_id
        session.add(
            AIUsageEvent(
                user_id=chat.user_id,
                chat_session_id=chat.id,
                operation=operation,
                model=model,
                reasoning_effort=reasoning_effort,
                input_tokens=input_tokens,
                cached_input_tokens=cached,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning,
                estimated_cost_usd=cost,
                pricing_source=(
                    "reserved_from_openai_fast_pricing"
                    if used_reserved_cost and service_tier in {"fast", "priority"}
                    else "reserved_from_openai_pricing"
                    if used_reserved_cost
                    else "configured_from_openai_fast_pricing"
                    if service_tier in {"fast", "priority"}
                    else "configured_from_openai_pricing"
                ),
                raw_usage=recorded_usage,
                created_at=datetime.now(UTC),
            )
        )

    async def record_clarification_evidence(
        self,
        session: AsyncSession,
        *,
        chat: AISetupChatSession,
        source_fragment: str,
        question: str,
        answer: str,
        capability_key: str | None,
        confidence: float | None,
    ) -> None:
        fingerprint = hashlib.sha256(
            (
                f"{chat.id}:{_normalize_fragment(source_fragment)}:"
                f"{_normalize_fragment(answer)}:{capability_key or ''}"
            ).encode()
        ).hexdigest()
        exists = await session.scalar(
            select(CapabilityClarificationEvidence.id).where(
                CapabilityClarificationEvidence.evidence_fingerprint == fingerprint
            )
        )
        if exists is not None:
            return
        session.add(
            CapabilityClarificationEvidence(
                user_id=chat.user_id,
                chat_session_id=chat.id,
                evidence_fingerprint=fingerprint,
                source_fragment=source_fragment[:2000],
                clarification_question=question[:2000],
                answer=answer[:2000],
                capability_key=capability_key,
                successful=True,
                confidence=confidence,
                created_at=datetime.now(UTC),
            )
        )

    async def review_alias(
        self,
        session: AsyncSession,
        *,
        proposal_id: UUID,
        action: str,
        note: str,
        admin_session: SystemBrainSession,
    ) -> CapabilityAliasProposal:
        proposal = await session.get(CapabilityAliasProposal, proposal_id)
        if proposal is None:
            raise SystemBrainAccessError(
                "alias_not_found", "Alias proposal was not found.", status_code=404
            )
        if action not in {"approve", "reject"}:
            raise SystemBrainAccessError("invalid_alias_action", "Invalid alias review action.")
        proposal.status = "approved" if action == "approve" else "rejected"
        proposal.review_note = note.strip()[:2000] or None
        proposal.reviewed_at = datetime.now(UTC)
        SystemBrainAuthService._audit(
            session,
            action=f"system_brain.alias_{proposal.status}",
            target_id=str(proposal.id),
            ip_hash=admin_session.ip_hash,
            metadata={
                "alias": proposal.normalized_alias,
                "capability_key": proposal.capability_key,
                "release_required": proposal.status == "approved",
            },
        )
        if proposal.status == "approved":
            from ai_market_monitor.services.capability_registry import CapabilityRegistryService

            await session.flush()
            await CapabilityRegistryService(self.settings).publish(session)
        await session.commit()
        return proposal

    async def overview(self, session: AsyncSession) -> dict[str, Any]:
        events = list(
            (
                await session.scalars(
                    select(CapabilityResolutionEvent)
                    .order_by(CapabilityResolutionEvent.created_at.desc())
                    .limit(5000)
                )
            ).all()
        )
        sessions = list((await session.scalars(select(AISetupChatSession))).all())
        usage = list((await session.scalars(select(AIUsageEvent))).all())
        agent_runs = list(
            (
                await session.scalars(
                    select(AgentRun).order_by(AgentRun.started_at.desc()).limit(5000)
                )
            ).all()
        )
        agent_tool_calls = list(
            (
                await session.scalars(
                    select(AgentToolCall)
                    .order_by(AgentToolCall.created_at.desc())
                    .limit(10000)
                )
            ).all()
        )
        extensions = list(
            (
                await session.scalars(
                    select(CapabilityExtension)
                    .order_by(CapabilityExtension.updated_at.desc())
                    .limit(500)
                )
            ).all()
        )
        extension_attempts = list(
            (
                await session.scalars(
                    select(CapabilityExtensionAttempt).order_by(
                        CapabilityExtensionAttempt.created_at.desc()
                    )
                )
            ).all()
        )
        user_rows = (
            await session.execute(
                select(User, UserIdentity)
                .join(UserIdentity, UserIdentity.user_id == User.id)
                .where(UserIdentity.provider == IdentityProvider.EMAIL)
                .order_by(User.created_at.desc())
            )
        ).all()
        users = []
        seen_users: set[UUID] = set()
        for user, identity in user_rows:
            email = (identity.normalized_identifier or identity.display_identifier or "").casefold()
            if not email or email in _EXCLUDED_USER_EMAILS or user.id in seen_users:
                continue
            seen_users.add(user.id)
            name = (user.display_name or "").strip()
            users.append(
                {
                    "first_name": name.split()[0] if name else email.split("@", 1)[0],
                    "email": email,
                    "status": str(user.status),
                    "created_at": user.created_at,
                    "last_seen_at": user.last_seen_at,
                }
            )

        unmatched = _event_counter(events, statuses={"unknown"})
        low_confidence = _event_counter(
            [
                event
                for event in events
                if event.status in {"matched", "ambiguous"}
                and event.candidates
                and event.candidates[0].get("availability") == "available"
                and (event.top_confidence or 0) < 0.8
            ],
            statuses=None,
        )
        provider_blocked = _event_counter(events, statuses={"provider_blocked"})
        clarification_choices = _choice_counter(events)
        false_rankings = _false_rankings(events)
        deterministic_quality = capability_quality_snapshot()
        ai_labeled = [
            event
            for event in events
            if event.selection_source == "user_choice_after_ai"
            and event.selected_capability_key
            and event.candidates
        ]
        ai_correct = sum(
            event.selected_capability_key
            == str(event.candidates[0].get("capability_key") or "")
            for event in ai_labeled
        )
        parameter_events = [
            event
            for event in events
            if event.selection_source == "ai_reranker"
            and event.parameters_validated is not None
        ]
        parameter_passed = sum(bool(event.parameters_validated) for event in parameter_events)
        quality_metrics = {
            **deterministic_quality,
            "ai_selection": {
                "passed": ai_correct,
                "total": len(ai_labeled),
                "percent": _percentage(ai_correct, len(ai_labeled)),
            },
            "parameter": {
                "passed": parameter_passed,
                "total": len(parameter_events),
                "percent": _percentage(parameter_passed, len(parameter_events)),
            },
        }
        unknown_keywords = Counter(
            term.casefold().strip()
            for event in events
            for term in (event.unknown_terms or [])
            if term.strip()
        )
        for chat in sessions:
            for item in chat.unsupported_conditions or []:
                label = str(item.get("label") or item.get("message") or "").strip().casefold()
                if label:
                    unknown_keywords[label] += 1

        compatibility = {row.key: row for row in compatibility_report()}
        poor_aliases = []
        regressions = []
        for capability in all_capabilities():
            row = compatibility[capability.key]
            if len(capability.aliases) < 2 or not capability.negative_examples:
                poor_aliases.append(
                    {
                        "key": capability.key,
                        "label": capability.label,
                        "aliases": len(capability.aliases),
                        "negative_examples": len(capability.negative_examples),
                        "status": "needs aliases"
                        if len(capability.aliases) < 2
                        else "needs negatives",
                    }
                )
            regression_status = (
                "pass"
                if row.availability == "available"
                and row.template_valid
                and row.evaluator_supported
                else "blocked"
                if row.availability == "provider_required"
                else "fail"
            )
            regressions.append(
                {
                    "key": capability.key,
                    "label": capability.label,
                    "version": capability.capability_version,
                    "status": regression_status,
                    "availability": row.availability,
                    "template": row.template_valid,
                    "evaluator": row.evaluator_supported,
                    "notes": ", ".join(row.notes) or "Registry, template and evaluator aligned",
                }
            )
        poor_aliases.sort(
            key=lambda item: (item["aliases"], item["negative_examples"], item["label"])
        )
        proposals = list(
            (
                await session.scalars(
                    select(CapabilityAliasProposal).order_by(
                        CapabilityAliasProposal.status.asc(),
                        CapabilityAliasProposal.evidence_count.desc(),
                    )
                )
            ).all()
        )
        usage_breakdown: dict[tuple[str, str, str], dict[str, Any]] = {}
        for event in usage:
            key = (event.model, event.reasoning_effort, "default")
            bucket = usage_breakdown.setdefault(
                key,
                {
                    "model": event.model,
                    "reasoning_effort": event.reasoning_effort,
                    "service_tier": "default",
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_tokens": 0,
                    "cost": Decimal("0"),
                },
            )
            bucket["calls"] += 1
            bucket["input_tokens"] += event.input_tokens
            bucket["output_tokens"] += event.output_tokens
            bucket["reasoning_tokens"] += event.reasoning_tokens
            bucket["cost"] += event.estimated_cost_usd
        extension_ai_cost = Decimal("0")
        extension_ai_calls = 0
        for attempt in extension_attempts:
            if attempt.status != "succeeded" or not attempt.usage:
                continue
            raw = dict(attempt.usage)
            input_tokens = int(raw.get("input_tokens") or raw.get("prompt_tokens") or 0)
            output_tokens = int(raw.get("output_tokens") or raw.get("completion_tokens") or 0)
            output_details = (
                raw.get("output_tokens_details") or raw.get("completion_tokens_details") or {}
            )
            reasoning_tokens = int(output_details.get("reasoning_tokens") or 0)
            cost = estimate_usage_cost(
                self.settings,
                model=attempt.model,
                usage=raw,
                service_tier=attempt.service_tier,
            )
            key = (attempt.model, attempt.reasoning_effort, attempt.service_tier)
            bucket = usage_breakdown.setdefault(
                key,
                {
                    "model": attempt.model,
                    "reasoning_effort": attempt.reasoning_effort,
                    "service_tier": attempt.service_tier,
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_tokens": 0,
                    "cost": Decimal("0"),
                },
            )
            bucket["calls"] += 1
            bucket["input_tokens"] += input_tokens
            bucket["output_tokens"] += output_tokens
            bucket["reasoning_tokens"] += reasoning_tokens
            bucket["cost"] += cost
            extension_ai_calls += 1
            extension_ai_cost += cost

        counts = {
            "registered_users": int(await session.scalar(select(func.count(User.id))) or 0),
            "visible_users": len(users),
            "chat_sessions": len(sessions),
            "chat_messages": int(
                await session.scalar(select(func.count(AISetupChatMessage.id))) or 0
            ),
            "approved_chats": sum(chat.status == "approved" for chat in sessions),
            "active_monitors": int(
                await session.scalar(
                    select(func.count(Strategy.id)).where(Strategy.status == StrategyStatus.ACTIVE)
                )
                or 0
            ),
            "scan_jobs": int(await session.scalar(select(func.count(ScanJob.id))) or 0),
            "alerts": int(await session.scalar(select(func.count(Alert.id))) or 0),
            "resolution_events": len(events),
            "unmatched": sum(event.status == "unknown" for event in events),
            "provider_blocked": sum(event.status == "provider_blocked" for event in events),
            "pending_aliases": sum(item.status == "pending" for item in proposals),
            "ai_calls": len(usage) + extension_ai_calls,
            "ai_cost_usd": (
                sum((item.estimated_cost_usd for item in usage), Decimal("0"))
                + extension_ai_cost
            ),
            "extension_total": len(extensions),
            "extension_certified": sum(
                item.status in {"certified_user", "approved_global"} for item in extensions
            ),
            "extension_failed": sum(item.status == "failed" for item in extensions),
            "extension_repair_ready": sum(item.status == "repair_ready" for item in extensions),
            "agent_runs": len(agent_runs),
            "agent_completed": sum(item.status == "completed" for item in agent_runs),
            "agent_fallbacks": sum(item.fallback_used for item in agent_runs),
            "agent_contained": sum(item.status == "contained" for item in agent_runs),
            "agent_shadow_runs": sum(item.shadow_mode for item in agent_runs),
        }
        agent_control = _agent_control_summary(agent_runs, agent_tool_calls)
        agent_control.update(
            {
                "configured_enabled": self.settings.ai_agent_control_enabled,
                "shadow_enabled": self.settings.ai_agent_shadow_mode,
                "rollout_percent": self.settings.ai_agent_rollout_percent,
                "max_steps": self.settings.ai_agent_max_steps,
                "max_tool_calls": self.settings.ai_agent_max_tool_calls_per_turn,
                "cost_limit": self.settings.ai_agent_max_estimated_cost_usd_per_turn,
            }
        )
        setup_model_routing = _setup_model_routing_summary(usage)
        attempts_by_extension: dict[UUID, list[CapabilityExtensionAttempt]] = {}
        for attempt in extension_attempts:
            attempts_by_extension.setdefault(attempt.extension_id, []).append(attempt)
        extension_rows = []
        for extension in extensions:
            attempts = attempts_by_extension.get(extension.id, [])
            last_attempt = attempts[0] if attempts else None
            extension_rows.append(
                {
                    "id": extension.id,
                    "capability_key": extension.capability_key,
                    "version": extension.capability_version,
                    "status": extension.status,
                    "stage": extension.stage,
                    "source_prompt": extension.source_prompt,
                    "validation_score": round(extension.validation_score, 1),
                    "scan_count": extension.scan_count,
                    "symbols_scanned": extension.symbols_scanned_total,
                    "candidates": extension.candidates_total,
                    "notifications": extension.notifications_total,
                    "candidate_rate": (
                        round(
                            extension.candidates_total
                            / max(1, extension.symbols_scanned_total)
                            * 100,
                            4,
                        )
                    ),
                    "repair_generation": extension.repair_generation,
                    "attempts": len(attempts),
                    "last_model": last_attempt.model if last_attempt else None,
                    "last_effort": last_attempt.reasoning_effort if last_attempt else None,
                    "last_error": extension.last_error,
                    "generated_code": extension.generated_code,
                    "build_log": list(extension.build_log or []),
                    "validation_report": dict(extension.validation_report or {}),
                    "updated_at": extension.updated_at,
                }
            )
        return {
            "generated_at": datetime.now(UTC),
            "counts": counts,
            "users": users,
            "unmatched": unmatched[:100],
            "low_confidence": low_confidence[:100],
            "clarification_choices": clarification_choices[:100],
            "false_rankings": false_rankings[:100],
            "provider_blocked": provider_blocked[:100],
            "unsupported_keywords": [
                {"keyword": keyword, "count": count}
                for keyword, count in unknown_keywords.most_common(100)
            ],
            "poor_aliases": poor_aliases[:100],
            "alias_proposals": proposals,
            "regressions": regressions,
            "usage_breakdown": sorted(
                usage_breakdown.values(), key=lambda item: item["cost"], reverse=True
            ),
            "capability_extensions": extension_rows,
            "quality_metrics": quality_metrics,
            "agent_control": agent_control,
            "setup_model_routing": setup_model_routing,
        }

    async def operations_summary(self, session: AsyncSession) -> dict[str, Any]:
        """Return bounded aggregate chatbot telemetry for the owner console."""

        runs = list(
            (
                await session.scalars(
                    select(AgentRun).order_by(AgentRun.started_at.desc()).limit(5000)
                )
            ).all()
        )
        calls = list(
            (
                await session.scalars(
                    select(AgentToolCall)
                    .order_by(AgentToolCall.created_at.desc())
                    .limit(10000)
                )
            ).all()
        )
        extensions = list(
            (
                await session.scalars(
                    select(CapabilityExtension)
                    .order_by(CapabilityExtension.updated_at.desc())
                    .limit(1000)
                )
            ).all()
        )
        public_events = list(
            (
                await session.scalars(
                    select(PublicChatAnswerEvent)
                    .order_by(PublicChatAnswerEvent.created_at.desc())
                    .limit(10000)
                )
            ).all()
        )
        public_feedback = list(
            (
                await session.scalars(
                    select(PublicChatAnswerFeedback)
                    .order_by(PublicChatAnswerFeedback.created_at.desc())
                    .limit(10000)
                )
            ).all()
        )
        inquiries = list(
            (
                await session.scalars(
                    select(PublicInquiry)
                    .order_by(PublicInquiry.submitted_at.desc())
                    .limit(5000)
                )
            ).all()
        )
        email_deliveries = list(
            (
                await session.scalars(
                    select(PublicInquiryEmailDelivery)
                    .order_by(PublicInquiryEmailDelivery.created_at.desc())
                    .limit(10000)
                )
            ).all()
        )
        ratings = list(
            (
                await session.scalars(
                    select(PublicInquiryRating)
                    .order_by(PublicInquiryRating.created_at.desc())
                    .limit(5000)
                )
            ).all()
        )
        chats = list((await session.scalars(select(AISetupChatSession))).all())

        agent = _agent_control_summary(runs, calls)
        for rate_name in (
            "completion_rate",
            "fallback_rate",
            "contained_rate",
            "invalid_call_rate",
            "correct_tool_selection_rate",
            "draft_compilation_success_rate",
        ):
            if agent[rate_name] is None:
                agent[rate_name] = 0.0
        correction_count = sum(
            int(
                dict((run.comparison or {}).get("model_route") or {}).get(
                    "correction_count"
                )
                or 0
            )
            for run in runs
        )
        approval_ready = sum(chat.status == "ready_for_approval" for chat in chats)
        approved = sum(chat.status == "approved" for chat in chats)
        tool_summaries = [
            item
            for run in runs
            for item in (run.comparison or {}).get("tool_result_summaries", [])
            if isinstance(item, dict)
        ]
        provider_limited_turns = sum(
            int(item.get("provider_requirement_count") or 0) > 0
            for item in tool_summaries
        )
        agent.update(
            {
                "configured_enabled": self.settings.ai_agent_control_enabled,
                "shadow_enabled": self.settings.ai_agent_shadow_mode,
                "rollout_percent": self.settings.ai_agent_rollout_percent,
                "kill_switch_active": not self.settings.ai_agent_control_enabled,
                "user_corrections": correction_count,
                "tool_calls": len(calls),
                "tool_success_rate": _percentage(
                    sum(call.result_status == "success" for call in calls),
                    len(calls),
                )
                or 0.0,
                "tool_failure_rate": _percentage(
                    sum(
                        call.result_status
                        in {"blocked", "validation_error", "unavailable", "error"}
                        for call in calls
                    ),
                    len(calls),
                )
                or 0.0,
                "approval_ready_chats": approval_ready,
                "approved_chats": approved,
                "approval_conversion_rate": _percentage(
                    approved,
                    approval_ready + approved,
                )
                or 0.0,
                "clause_coverage_failures": sum(
                    int((run.comparison or {}).get("clause_coverage_failures") or 0)
                    for run in runs
                ),
                "unsupported_provider_turns": provider_limited_turns,
            }
        )

        extension_statuses = Counter(item.status for item in extensions)
        public_outcomes = Counter(item.outcome for item in public_events)
        public_stages = Counter(item.stage for item in public_events)
        public_modes = Counter(item.mode for item in public_events)
        delivery_states = Counter(item.status for item in email_deliveries)
        knowledge_gaps = Counter(
            item.knowledge_gap_category
            for item in inquiries
            if item.knowledge_gap_category
        )
        latencies = sorted(max(0, item.latency_ms) for item in public_events)
        p95_index = max(0, round((len(latencies) - 1) * 0.95)) if latencies else 0
        numeric_ratings = [item.rating for item in ratings if item.rating is not None]
        factual_events = [
            item
            for item in public_events
            if item.mode in {"PRODUCT_FACT", "ACCOUNT_SUPPORT"}
        ]
        grounded_answers = sum(bool(item.source_ids) for item in factual_events)
        support_requests = sum(item.support_form_requested for item in public_feedback)
        helpful_answers = sum(item.helpful for item in public_feedback)
        greeting_misclassifications = sum(
            item.is_greeting
            and (
                item.mode != "PRODUCT_CONVERSATION"
                or item.stage not in {"GREETING_AND_PROFILE", "FOLLOW_UP", "ANSWER"}
                or item.outcome != "answered"
            )
            for item in public_events
        )
        dissatisfied_topics = Counter(
            item.intent for item in public_feedback if not item.helpful
        )
        unanswered_topics = Counter(
            item.knowledge_gap_reason or item.intent
            for item in public_events
            if item.outcome == "unsupported" or item.validation_failure
        )
        model_events: dict[str, list[PublicChatAnswerEvent]] = {}
        for event in public_events:
            model_events.setdefault(event.model or "fallback", []).append(event)

        return {
            "generated_at": datetime.now(UTC),
            "agent": agent,
            "custom_capabilities": {
                "requests": len(extensions),
                "certified": sum(
                    extension_statuses[state]
                    for state in {"certified_user", "approved_global"}
                ),
                "failed": extension_statuses["failed"],
                "repair_ready": extension_statuses["repair_ready"],
                "quarantined": sum(item.paused_at is not None for item in extensions),
                "certification_success_rate": _percentage(
                    sum(
                        extension_statuses[state]
                        for state in {"certified_user", "approved_global"}
                    ),
                    len(extensions),
                )
                or 0.0,
                "repairs": sum(item.repair_generation > 0 for item in extensions),
                "repair_rate": _percentage(
                    sum(item.repair_generation > 0 for item in extensions),
                    len(extensions),
                )
                or 0.0,
            },
            "public_support": {
                "questions": len(public_events),
                "answered": public_outcomes["answered"],
                "clarifications": public_stages["CLARIFY"],
                "unsupported": public_outcomes["unsupported"],
                "refusals": public_outcomes["refused"],
                "out_of_scope": public_modes["OUT_OF_SCOPE"],
                "ai_unavailable": sum(
                    bool(item.validation_failure) or item.intent == "provider_unavailable"
                    for item in public_events
                ),
                "inquiries": len(inquiries),
                "inquiry_conversion_percent": _percentage(
                    len(inquiries),
                    len(public_events),
                ),
                "source_coverage_percent": _percentage(
                    grounded_answers,
                    len(factual_events),
                ),
                "validation_failures": sum(
                    bool(item.validation_failure) for item in public_events
                ),
                "email_states": [
                    {"state": state, "count": count}
                    for state, count in delivery_states.most_common()
                ],
                "rating_count": len(ratings),
                "average_rating": (
                    round(sum(numeric_ratings) / len(numeric_ratings), 2)
                    if numeric_ratings
                    else None
                ),
                "helpful_percent": _percentage(
                    helpful_answers,
                    len(public_feedback),
                ),
                "answer_feedback_count": len(public_feedback),
                "support_form_request_percent": _percentage(
                    support_requests,
                    len(public_feedback),
                ),
                "support_form_completion_percent": _percentage(
                    sum(item.inquiry_id is not None for item in public_feedback),
                    support_requests,
                ),
                "greeting_misclassification_count": greeting_misclassifications,
                "average_latency_ms": round(
                    sum(latencies) / max(1, len(latencies)),
                    1,
                ),
                "p95_latency_ms": latencies[p95_index] if latencies else 0,
                "estimated_cost_usd": sum(
                    (item.estimated_cost_usd for item in public_events),
                    Decimal("0"),
                ),
                "knowledge_gaps": [
                    {"category": category, "count": count}
                    for category, count in knowledge_gaps.most_common(10)
                ],
                "dissatisfied_topics": [
                    {"topic": topic, "count": count}
                    for topic, count in dissatisfied_topics.most_common(10)
                ],
                "unanswered_topics": [
                    {"topic": topic, "count": count}
                    for topic, count in unanswered_topics.most_common(10)
                ],
                "models": [
                    {
                        "model": model,
                        "answers": len(events),
                        "average_latency_ms": round(
                            sum(item.latency_ms for item in events) / len(events),
                            1,
                        ),
                        "estimated_cost_usd": sum(
                            (item.estimated_cost_usd for item in events),
                            Decimal("0"),
                        ),
                    }
                    for model, events in sorted(model_events.items())
                ],
            },
        }


def _setup_model_routing_summary(events: list[AIUsageEvent]) -> dict[str, Any]:
    """Summarize persisted model-route decisions without treating them as quality proof."""

    tiers: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    models: Counter[str] = Counter()
    efforts: Counter[str] = Counter()
    routed_calls = 0
    for event in events:
        raw = dict(event.raw_usage or {})
        tier = str(raw.get("_traceedge_route_tier") or "").strip()
        if not tier:
            continue
        routed_calls += 1
        tiers[tier] += 1
        models[event.model] += 1
        efforts[event.reasoning_effort] += 1
        raw_reasons = raw.get("_traceedge_route_reasons") or []
        if isinstance(raw_reasons, list):
            reasons.update(str(item) for item in raw_reasons if str(item).strip())
    return {
        "routed_calls": routed_calls,
        "unclassified_calls": max(0, len(events) - routed_calls),
        "tiers": [
            {"tier": name, "calls": count} for name, count in tiers.most_common()
        ],
        "reasons": [
            {"reason": name, "calls": count} for name, count in reasons.most_common()
        ],
        "models": [
            {"model": name, "calls": count} for name, count in models.most_common()
        ],
        "reasoning_efforts": [
            {"reasoning_effort": name, "calls": count}
            for name, count in efforts.most_common()
        ],
    }


def _agent_control_summary(
    runs: list[AgentRun],
    calls: list[AgentToolCall],
) -> dict[str, Any]:
    non_shadow = [run for run in runs if not run.shadow_mode]
    completed = [run for run in non_shadow if run.status == "completed"]
    fallback = [run for run in non_shadow if run.fallback_used]
    contained = [run for run in non_shadow if run.status == "contained"]
    forbidden_codes = {
        "forbidden_tool",
        "unknown_tool",
        "tool_not_offered",
        "monitor_not_owned",
        "scan_not_entitled",
    }
    forbidden_attempt_run_ids = {
        run.id for run in runs if run.error_type in forbidden_codes
    }
    forbidden_attempt_run_ids.update(
        call.agent_run_id for call in calls if call.tool_name in FORBIDDEN_AGENT_TOOLS
    )
    forbidden_executed = sum(
        call.tool_name in FORBIDDEN_AGENT_TOOLS and call.result_status == "success"
        for call in calls
    )
    ungrounded = [
        run for run in runs if (run.error_type or "").startswith("ungrounded:")
    ]
    labeled_shadow = [
        run
        for run in runs
        if (run.comparison or {}).get("agent_first_tool_correct") is not None
    ]
    correct_shadow = sum(
        bool((run.comparison or {}).get("agent_first_tool_correct"))
        for run in labeled_shadow
    )
    invalid_calls = [
        call
        for call in calls
        if call.policy_decision in {"rejected", "shadow_rejected"}
        or call.result_status in {"blocked", "validation_error"}
    ]
    durations = sorted(
        max(0.0, (_aware(run.ended_at) - _aware(run.started_at)).total_seconds() * 1000)
        for run in runs
        if run.ended_at is not None
    )
    p95_index = max(0, round((len(durations) - 1) * 0.95)) if durations else 0
    tool_buckets: dict[str, dict[str, Any]] = {}
    for call in calls:
        bucket = tool_buckets.setdefault(
            call.tool_name,
            {
                "tool_name": call.tool_name,
                "calls": 0,
                "successes": 0,
                "blocked": 0,
                "average_duration_ms": 0,
                "duration_total_ms": 0,
            },
        )
        bucket["calls"] += 1
        bucket["successes"] += call.result_status == "success"
        bucket["blocked"] += call.result_status in {"blocked", "validation_error"}
        bucket["duration_total_ms"] += call.duration_ms
    for bucket in tool_buckets.values():
        bucket["average_duration_ms"] = round(
            bucket.pop("duration_total_ms") / max(1, bucket["calls"]),
            1,
        )
        bucket["success_rate"] = _percentage(bucket["successes"], bucket["calls"])

    result_summaries = [
        summary
        for run in runs
        for summary in (run.comparison or {}).get("tool_result_summaries", [])
        if isinstance(summary, dict)
    ]
    compile_summaries = [
        item
        for item in result_summaries
        if item.get("tool_name") == "compile_strategy_draft"
    ]
    successful_compiles = sum(item.get("status") == "success" for item in compile_summaries)
    unsupported_leakage = sum(
        item.get("status") == "success"
        and bool(item.get("approval_eligible"))
        and int(item.get("unsupported_count") or 0) > 0
        for item in compile_summaries
    )
    clarification_turns = sum(
        item.get("tool_name") == "resolve_trading_capabilities"
        and int(item.get("clarification_count") or 0) > 0
        for item in result_summaries
    )

    has_runtime_evidence = bool(runs)
    return {
        "enabled": has_runtime_evidence,
        "recorded_runs": len(runs),
        "completion_rate": _percentage(len(completed), len(non_shadow)),
        "fallback_rate": _percentage(len(fallback), len(non_shadow)),
        "contained_rate": _percentage(len(contained), len(non_shadow)),
        "invalid_call_rate": _percentage(len(invalid_calls), len(calls)),
        "correct_tool_selection_rate": _percentage(
            correct_shadow,
            len(labeled_shadow),
        ),
        "labeled_shadow_turns": len(labeled_shadow),
        "forbidden_attempts": len(forbidden_attempt_run_ids),
        "forbidden_executed": forbidden_executed,
        "ungrounded_claims_contained": len(ungrounded),
        "draft_compilation_success_rate": _percentage(
            successful_compiles,
            len(compile_summaries),
        ),
        "unsupported_condition_leakage": unsupported_leakage,
        "clarification_turns": clarification_turns,
        "average_calls_per_run": round(
            sum(run.tool_call_count for run in non_shadow) / max(1, len(non_shadow)),
            2,
        ),
        "average_latency_ms": round(sum(durations) / max(1, len(durations)), 1),
        "p95_latency_ms": round(durations[p95_index], 1) if durations else 0,
        "input_tokens": sum(run.input_tokens for run in runs),
        "output_tokens": sum(run.output_tokens for run in runs),
        "estimated_cost_usd": sum(
            (run.estimated_cost_usd for run in runs), Decimal("0")
        ),
        "tool_breakdown": sorted(
            tool_buckets.values(),
            key=lambda item: (-item["calls"], item["tool_name"]),
        ),
        "recent_runs": [
            {
                "id": run.id,
                "started_at": run.started_at,
                "status": run.status,
                "model": run.model,
                "reasoning_effort": run.reasoning_effort,
                "steps": run.step_count,
                "tool_calls": run.tool_call_count,
                "cost": run.estimated_cost_usd,
                "fallback": run.fallback_used,
                "shadow": run.shadow_mode,
                "error_type": run.error_type,
                "final_intent": run.final_intent,
                "comparison": run.comparison,
            }
            for run in runs[:100]
        ],
        "has_runtime_evidence": has_runtime_evidence,
    }


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _normalize_fragment(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9%]+", " ", value.casefold()).split())


def _percentage(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100, 2) if denominator else None


def estimate_usage_cost(
    settings: Settings,
    *,
    model: str,
    usage: dict[str, Any],
    service_tier: str = "default",
) -> Decimal:
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens") or 0)
    uncached = max(0, input_tokens - cached)
    pricing = (
        settings.openai_fast_model_pricing_usd_per_million.get(model, {})
        if service_tier in {"fast", "priority"}
        else settings.openai_model_pricing_usd_per_million.get(model, {})
    )
    cost = (
        Decimal(uncached) * Decimal(str(pricing.get("input", 0)))
        + Decimal(cached) * Decimal(str(pricing.get("cached_input", 0)))
        + Decimal(output_tokens) * Decimal(str(pricing.get("output", 0)))
    ) / Decimal(1_000_000)
    return cost * (Decimal("0.5") if service_tier == "flex" else Decimal("1"))


def _event_counter(
    events: list[CapabilityResolutionEvent],
    *,
    statuses: set[str] | None,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for event in events:
        if statuses is not None and event.status not in statuses:
            continue
        item = grouped.setdefault(
            event.normalized_fragment,
            {
                "fragment": event.source_fragment,
                "count": 0,
                "confidence_total": 0.0,
                "confidence_count": 0,
                "top_candidate": (
                    str(event.candidates[0].get("label")) if event.candidates else "No candidate"
                ),
                "provider": event.provider_requirement,
            },
        )
        item["count"] += 1
        if event.top_confidence is not None:
            item["confidence_total"] += event.top_confidence
            item["confidence_count"] += 1
    rows = []
    for item in grouped.values():
        count = item.pop("confidence_count")
        total = item.pop("confidence_total")
        item["confidence"] = round(total / count * 100) if count else None
        rows.append(item)
    return sorted(rows, key=lambda item: (-item["count"], item["fragment"]))


def _choice_counter(events: list[CapabilityResolutionEvent]) -> list[dict[str, Any]]:
    counter: Counter[tuple[str, str]] = Counter(
        (event.source_fragment, event.selected_capability_key)
        for event in events
        if event.selected_capability_key
    )
    return [
        {"fragment": fragment, "capability_key": key, "count": count}
        for (fragment, key), count in counter.most_common()
    ]


def _false_rankings(events: list[CapabilityResolutionEvent]) -> list[dict[str, Any]]:
    counter: Counter[tuple[str, str, str]] = Counter()
    for event in events:
        if not event.selected_capability_key or not event.candidates:
            continue
        ranked = str(event.candidates[0].get("capability_key") or "")
        if ranked and ranked != event.selected_capability_key:
            counter[(event.source_fragment, ranked, event.selected_capability_key)] += 1
    return [
        {"fragment": fragment, "ranked": ranked, "chosen": chosen, "count": count}
        for (fragment, ranked, chosen), count in counter.most_common()
    ]
