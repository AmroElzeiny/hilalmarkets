from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import AuditEvent, SystemBrainActionProposal
from ai_market_monitor.schemas.system_brain import ActionProposalRequest
from ai_market_monitor.services.account_admin import (
    AccountAdminError,
    SystemBrainUserAdminService,
)
from ai_market_monitor.services.system_brain_privacy import redact_customer_text

CONSEQUENTIAL_ACTIONS = frozenset(
    {
        "send_email",
        "grant_access",
        "reduce_access",
        "ban_user",
        "delete_user",
        "change_production_setting",
        "queue_customer_job",
        "modify_campaign",
        "alter_billing",
        "governance_decision",
        "publish_evidence",
        "reject_evidence",
        "activate_strategy",
        "approve_strategy",
    }
)
CANONICAL_ACTION_ADAPTERS = frozenset({"ban_user", "delete_user", "grant_access", "reduce_access"})


class SystemBrainActionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SystemBrainActionService:
    """Proposal/confirmation boundary; execution delegates to canonical services only."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def propose(
        self,
        *,
        admin_user_id: UUID,
        conversation_id: UUID | None,
        request: ActionProposalRequest,
    ) -> tuple[SystemBrainActionProposal, str]:
        if request.action not in CONSEQUENTIAL_ACTIONS:
            raise SystemBrainActionError(
                "unknown_consequential_action",
                "The requested action is not in the consequential-action registry.",
            )
        if request.action not in CANONICAL_ACTION_ADAPTERS:
            raise SystemBrainActionError(
                "canonical_action_unavailable",
                "This consequential action has no authorized canonical service adapter "
                "and was not proposed.",
            )
        sanitized = request.model_copy(
            update={
                "reason": redact_customer_text(request.reason, limit=2000),
                "expected_effect": redact_customer_text(request.expected_effect, limit=2000),
                "risks": [redact_customer_text(item, limit=500) for item in request.risks],
                "rollback_path": redact_customer_text(request.rollback_path, limit=2000),
            }
        )
        exact_changes = sanitized.exact_changes.model_dump(mode="json", exclude_none=True)
        if sanitized.action in {"grant_access", "reduce_access"} and not exact_changes.get("tier"):
            raise SystemBrainActionError(
                "action_change_incomplete",
                "An access proposal must name the exact governed tier.",
            )
        if sanitized.action in {"ban_user", "delete_user"} and exact_changes:
            raise SystemBrainActionError(
                "action_change_invalid",
                "This account-state proposal does not accept additional model-authored changes.",
            )
        existing = await self.session.scalar(
            select(SystemBrainActionProposal).where(
                SystemBrainActionProposal.idempotency_key == sanitized.idempotency_key
            )
        )
        canonical = _canonical_proposal(sanitized)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        if existing is not None:
            if existing.confirmation_digest != digest or existing.admin_user_id != admin_user_id:
                raise SystemBrainActionError(
                    "idempotency_conflict",
                    "That action idempotency key is already bound to different exact changes.",
                )
            return existing, digest
        proposal = SystemBrainActionProposal(
            admin_user_id=admin_user_id,
            conversation_id=conversation_id,
            action=sanitized.action,
            target_type=sanitized.target_type,
            target_id=sanitized.target_id,
            exact_changes=exact_changes,
            reason=sanitized.reason,
            evidence_refs=sanitized.evidence_refs,
            expected_effect=sanitized.expected_effect,
            risks=sanitized.risks,
            rollback_path=sanitized.rollback_path,
            idempotency_key=sanitized.idempotency_key,
            status="pending",
            confirmation_digest=digest,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            execution_result_redacted={},
        )
        self.session.add(proposal)
        await self.session.flush()
        self.session.add(
            AuditEvent(
                actor_user_id=admin_user_id,
                actor_type="system_brain_admin",
                action="system_brain.action.proposed",
                target_type=sanitized.target_type,
                target_id=sanitized.target_id,
                request_id=None,
                ip_hash=None,
                metadata_redacted={
                    "proposal_id": str(proposal.id),
                    "action": sanitized.action,
                    "evidence_refs": sanitized.evidence_refs,
                },
                created_at=datetime.now(UTC),
            )
        )
        return proposal, digest

    async def confirm(
        self,
        proposal_id: UUID,
        *,
        admin_user_id: UUID,
        confirmation_token: str,
        human_reason: str,
    ) -> dict[str, Any]:
        proposal = await self.session.scalar(
            select(SystemBrainActionProposal)
            .where(
                SystemBrainActionProposal.id == proposal_id,
                SystemBrainActionProposal.admin_user_id == admin_user_id,
            )
            .with_for_update()
        )
        if proposal is None:
            raise SystemBrainActionError(
                "proposal_not_found", "The action proposal is unavailable."
            )
        if proposal.status == "executed":
            return dict(proposal.execution_result_redacted or {})
        if proposal.status != "pending":
            raise SystemBrainActionError(
                "proposal_not_pending", "The action proposal is no longer pending."
            )
        if proposal.expires_at <= datetime.now(UTC):
            proposal.status = "expired"
            raise SystemBrainActionError(
                "proposal_expired", "The action proposal expired without execution."
            )
        if not hmac.compare_digest(confirmation_token, proposal.confirmation_digest):
            raise SystemBrainActionError(
                "proposal_binding_mismatch",
                "Confirmation did not bind to the exact displayed proposal.",
            )
        result = await self._execute_canonical(
            proposal,
            admin_user_id=admin_user_id,
            human_reason=human_reason,
        )
        proposal.status = "executed"
        proposal.confirmed_at = datetime.now(UTC)
        proposal.executed_at = proposal.confirmed_at
        proposal.execution_result_redacted = result
        self.session.add(
            AuditEvent(
                actor_user_id=admin_user_id,
                actor_type="system_brain_admin",
                action="system_brain.action.confirmed",
                target_type=proposal.target_type,
                target_id=proposal.target_id,
                request_id=None,
                ip_hash=None,
                metadata_redacted={
                    "proposal_id": str(proposal.id),
                    "action": proposal.action,
                    "human_reason": human_reason[:500],
                    "idempotency_key": proposal.idempotency_key,
                },
                created_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        return result

    async def _execute_canonical(
        self,
        proposal: SystemBrainActionProposal,
        *,
        admin_user_id: UUID,
        human_reason: str,
    ) -> dict[str, Any]:
        try:
            target_user_id = UUID(proposal.target_id)
        except ValueError:
            target_user_id = None
        service = SystemBrainUserAdminService(self.session, self.settings)
        reason = f"{proposal.reason}\nHuman confirmation: {human_reason}"[:2000]
        canonical_idempotency_key = str(
            uuid5(
                NAMESPACE_URL,
                (
                    f"hilalmarkets:system-brain:{proposal.action}:"
                    f"{proposal.target_id}:{proposal.idempotency_key}"
                ),
            )
        )
        try:
            if proposal.action == "ban_user" and target_user_id:
                outcome = await service.ban_user(
                    actor_user_id=admin_user_id,
                    target_user_id=target_user_id,
                    reason=reason,
                    idempotency_key=canonical_idempotency_key,
                )
            elif proposal.action == "delete_user" and target_user_id:
                outcome = await service.delete_profile(
                    actor_user_id=admin_user_id,
                    target_user_id=target_user_id,
                    reason=reason,
                    idempotency_key=canonical_idempotency_key,
                )
            elif proposal.action in {"grant_access", "reduce_access"} and target_user_id:
                tier = str(proposal.exact_changes.get("tier") or "")
                months_raw = proposal.exact_changes.get("months")
                outcome = await service.apply_access(
                    actor_user_id=admin_user_id,
                    target_user_id=target_user_id,
                    tier=tier,
                    months=int(months_raw) if months_raw is not None else None,
                    reason=reason,
                    idempotency_key=canonical_idempotency_key,
                )
            else:
                raise SystemBrainActionError(
                    "canonical_action_unavailable",
                    "This action has no authorized canonical execution adapter and was not run.",
                )
        except AccountAdminError as exc:
            raise SystemBrainActionError(exc.code, str(exc)) from exc
        return {
            "action": outcome.action.action,
            "status": outcome.action.status,
            "message": outcome.message,
            "repeated": outcome.repeated,
        }


def _canonical_proposal(request: ActionProposalRequest) -> str:
    return json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
