from __future__ import annotations

import calendar
import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AccountAdminAction,
    AccountBan,
    AccountEmailDelivery,
    AdminOverride,
    AuditEvent,
    DashboardPreference,
    DiscordConnection,
    EmailAuthChallenge,
    IdentityLinkToken,
    OnboardingSession,
    PendingEmailSignup,
    SetupChatOperationalIssue,
    Strategy,
    Subscription,
    TelegramConnection,
    TelegramConversationState,
    TelegramDashboardLink,
    Trial,
    TrialCycle,
    User,
    UserIdentity,
    WebSession,
    WhatsAppConnection,
    WhatsAppConversationState,
)
from ai_market_monitor.db.models.enums import (
    IdentityProvider,
    StrategyStatus,
    SubscriptionStatus,
    TrialStatus,
    UserRole,
    UserStatus,
)
from ai_market_monitor.services.entitlements import EntitlementService, PlanCatalogService

ACCOUNT_PLAN_OPTIONS = (
    {
        "value": "free",
        "label": "Free plan",
        "description": "A fresh 7-day Monitor trial granted by an administrator.",
        "rank": 1,
    },
    {
        "value": "full_access",
        "label": "Full access",
        "description": "All HilalMarkets features except WhatsApp, for a chosen number of months.",
        "rank": 2,
    },
    {
        "value": "lifetime_partner",
        "label": "Lifetime partner",
        "description": "Permanent access to every HilalMarkets feature except WhatsApp.",
        "rank": 3,
    },
)


class AccountAdminError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AccountActionResult:
    action: AccountAdminAction
    message: str
    email_delivery_id: UUID | None = None
    repeated: bool = False


def account_identifier_hash(settings: Settings, normalized_identifier: str) -> str:
    secret = settings.app_secret_key.get_secret_value().encode("utf-8")
    payload = f"account-ban:{normalized_identifier.strip().casefold()}".encode()
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


async def identifier_is_banned(
    session: AsyncSession,
    settings: Settings,
    normalized_identifier: str,
) -> bool:
    digest = account_identifier_hash(settings, normalized_identifier)
    return bool(
        await session.scalar(
            select(AccountBan.id).where(
                AccountBan.identifier_hash == digest,
                AccountBan.is_active.is_(True),
            )
        )
    )


class SystemBrainUserAdminService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def list_users(
        self,
        *,
        query: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        page = max(1, page)
        page_size = min(max(10, page_size), 100)
        statement = select(User).outerjoin(UserIdentity, UserIdentity.user_id == User.id)
        count_statement = select(func.count(func.distinct(User.id))).select_from(User).outerjoin(
            UserIdentity,
            UserIdentity.user_id == User.id,
        )
        filters = []
        cleaned_query = " ".join((query or "").split())
        if cleaned_query:
            like = f"%{cleaned_query.casefold()}%"
            filters.append(
                or_(
                    func.lower(User.display_name).like(like),
                    func.lower(UserIdentity.display_identifier).like(like),
                    func.lower(UserIdentity.normalized_identifier).like(like),
                )
            )
        normalized_status = "suspended" if status == "banned" else status
        if normalized_status in {item.value for item in UserStatus}:
            filters.append(User.status == UserStatus(normalized_status))
        if filters:
            statement = statement.where(*filters)
            count_statement = count_statement.where(*filters)
        total = int(await self.session.scalar(count_statement) or 0)
        page_count = max(1, (total + page_size - 1) // page_size)
        page = min(page, page_count)
        users = (
            await self.session.scalars(
                statement.distinct()
                .order_by(User.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        items = [await self._user_row(user) for user in users]
        return {
            "users": items,
            "total": total,
            "page": min(page, page_count),
            "page_count": page_count,
            "page_size": page_size,
            "query": cleaned_query,
            "status": status or "",
            "plan_options": ACCOUNT_PLAN_OPTIONS,
        }

    async def ban_user(
        self,
        *,
        actor_user_id: UUID,
        target_user_id: UUID,
        reason: str,
        idempotency_key: str,
    ) -> AccountActionResult:
        reason = _required_reason(reason)
        action, repeated = await self._begin_action(
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            action_name="ban",
            idempotency_key=idempotency_key,
        )
        if repeated:
            return AccountActionResult(
                action=action,
                message="This profile was already banned by the recorded action.",
                repeated=True,
            )
        user = await self._manageable_user(actor_user_id, target_user_id)
        if user.status == UserStatus.DELETED:
            raise AccountAdminError("profile_deleted", "A deleted profile cannot be banned.")
        if user.status == UserStatus.SUSPENDED:
            raise AccountAdminError("profile_banned", "This profile is already banned.")
        previous_status = user.status.value
        identities = await self._email_identities(target_user_id)
        if not identities:
            raise AccountAdminError(
                "email_identity_missing",
                "This profile has no email identity that can be blocked from future signup.",
            )
        now = datetime.now(UTC)
        for identity in identities:
            normalized = (identity.normalized_identifier or "").strip().casefold()
            if not normalized:
                continue
            digest = account_identifier_hash(self.settings, normalized)
            ban = await self.session.scalar(
                select(AccountBan).where(AccountBan.identifier_hash == digest)
            )
            if ban is None:
                self.session.add(
                    AccountBan(
                        identifier_hash=digest,
                        banned_user_id=user.id,
                        banned_by_user_id=actor_user_id,
                        reason=reason,
                        is_active=True,
                        created_at=now,
                    )
                )
            else:
                ban.banned_user_id = user.id
                ban.banned_by_user_id = actor_user_id
                ban.reason = reason
                ban.is_active = True
                ban.revoked_at = None
        user.status = UserStatus.SUSPENDED
        await self._revoke_sessions(target_user_id, now)
        await self._pause_strategies(target_user_id, now)
        return await self._complete_action(
            action,
            reason=reason,
            payload={
                "previous_profile_status": previous_status,
                "profile_status": "banned",
                "identifiers_blocked": len(identities),
            },
            message="The profile is banned and its email cannot be used for another signup.",
        )

    async def delete_profile(
        self,
        *,
        actor_user_id: UUID,
        target_user_id: UUID,
        reason: str,
        idempotency_key: str,
    ) -> AccountActionResult:
        reason = _required_reason(reason)
        action, repeated = await self._begin_action(
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            action_name="delete_profile",
            idempotency_key=idempotency_key,
        )
        if repeated:
            return AccountActionResult(
                action=action,
                message="This profile was already deleted by the recorded action.",
                repeated=True,
            )
        user = await self._manageable_user(actor_user_id, target_user_id)
        if user.status == UserStatus.SUSPENDED:
            raise AccountAdminError(
                "banned_profile_delete_blocked",
                "A banned profile cannot be deleted because its blocked identity must be retained.",
            )
        if user.status == UserStatus.DELETED:
            raise AccountAdminError("profile_deleted", "This profile is already deleted.")
        await self._reject_active_provider_subscription(target_user_id)
        now = datetime.now(UTC)
        identities = list(
            (
                await self.session.scalars(
                    select(UserIdentity).where(UserIdentity.user_id == target_user_id)
                )
            ).all()
        )
        released_emails = [
            identity.normalized_identifier
            for identity in identities
            if identity.provider == IdentityProvider.EMAIL and identity.normalized_identifier
        ]
        for identity in identities:
            identity.provider_subject = f"deleted:{identity.id}"
            identity.normalized_identifier = None
            identity.display_identifier = None
            identity.password_hash = None
            identity.is_verified = False
            identity.is_primary = False
            identity.verified_at = None
            identity.profile_data = {}
        await self.session.execute(
            delete(EmailAuthChallenge).where(EmailAuthChallenge.user_id == target_user_id)
        )
        await self.session.execute(
            delete(IdentityLinkToken).where(IdentityLinkToken.user_id == target_user_id)
        )
        await self.session.execute(
            delete(OnboardingSession).where(OnboardingSession.user_id == target_user_id)
        )
        await self.session.execute(
            delete(TelegramDashboardLink).where(
                TelegramDashboardLink.user_id == target_user_id
            )
        )
        if released_emails:
            await self.session.execute(
                delete(PendingEmailSignup).where(PendingEmailSignup.email.in_(released_emails))
            )
        await self._revoke_sessions(target_user_id, now)
        await self._pause_strategies(target_user_id, now)
        await self.session.execute(
            delete(TelegramConnection).where(TelegramConnection.user_id == target_user_id)
        )
        await self.session.execute(
            delete(DiscordConnection).where(DiscordConnection.user_id == target_user_id)
        )
        await self.session.execute(
            delete(TelegramConversationState).where(
                TelegramConversationState.user_id == target_user_id
            )
        )
        await self.session.execute(
            delete(WhatsAppConversationState).where(
                WhatsAppConversationState.user_id == target_user_id
            )
        )
        await self.session.execute(
            delete(WhatsAppConnection).where(WhatsAppConnection.user_id == target_user_id)
        )
        await self.session.execute(
            delete(DashboardPreference).where(DashboardPreference.user_id == target_user_id)
        )
        # Operational fingerprints and failure classes remain useful for aggregate
        # reliability, but the trader's quoted source text and proof payload are not
        # needed after profile deletion. Remove that content and the direct owner link.
        await self.session.execute(
            update(SetupChatOperationalIssue)
            .where(SetupChatOperationalIssue.user_id == target_user_id)
            .values(
                user_id=None,
                chat_session_id=None,
                setup_chat_turn_id=None,
                safe_source_excerpt="",
                failure_proof={},
            )
        )
        access_deliveries = list(
            (
                await self.session.scalars(
                    select(AccountEmailDelivery).where(
                        AccountEmailDelivery.user_id == target_user_id
                    )
                )
            ).all()
        )
        for delivery in access_deliveries:
            delivery.user_id = None
            delivery.recipient = f"deleted-{delivery.id}@invalid.local"
            delivery.payload_redacted = {}
            delivery.next_retry_at = None
            delivery.last_error = None
            if delivery.status in {"pending", "retryable", "sending"}:
                delivery.status = "canceled"
        await self._end_access(target_user_id, now)
        user.status = UserStatus.DELETED
        user.display_name = None
        user.locale = "en"
        user.timezone = "UTC"
        user.onboarding_completed_at = None
        user.last_seen_at = None
        return await self._complete_action(
            action,
            reason=reason,
            payload={
                "profile_status": "deleted",
                "identities_released": len(released_emails),
                "onboarding_data_removed": True,
                "access_email_records_anonymized": len(access_deliveries),
                "history_retained": True,
            },
            message=(
                "The profile was deleted and anonymized. Its email can be used to sign up again."
            ),
        )

    async def apply_access(
        self,
        *,
        actor_user_id: UUID,
        target_user_id: UUID,
        tier: str,
        months: int | None,
        reason: str,
        idempotency_key: str,
    ) -> AccountActionResult:
        reason = _required_reason(reason)
        if tier not in {"free", "full_access", "lifetime_partner"}:
            raise AccountAdminError("invalid_plan", "Select a valid access level.")
        if tier == "full_access":
            if months is None or not 1 <= months <= 120:
                raise AccountAdminError(
                    "invalid_duration",
                    "Full access requires a duration from 1 to 120 months.",
                )
        elif months not in {None, 0}:
            raise AccountAdminError(
                "duration_not_allowed",
                "Only Full access accepts a month duration.",
            )
        action, repeated = await self._begin_action(
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            action_name=f"apply_{tier}",
            idempotency_key=idempotency_key,
        )
        if repeated:
            delivery = await self.session.scalar(
                select(AccountEmailDelivery).where(
                    AccountEmailDelivery.admin_action_id == action.id
                )
            )
            return AccountActionResult(
                action=action,
                message="This access change was already applied.",
                email_delivery_id=delivery.id if delivery else None,
                repeated=True,
            )
        user = await self._manageable_user(actor_user_id, target_user_id)
        if user.status != UserStatus.ACTIVE:
            raise AccountAdminError(
                "profile_not_active",
                "Access can only be changed for an active profile.",
            )
        await self._reject_active_provider_subscription(target_user_id)
        previous_entitlement = await EntitlementService(self.session).current(target_user_id)
        now = datetime.now(UTC)
        await self._cancel_admin_subscriptions(target_user_id, now)
        await self.session.execute(
            update(AdminOverride)
            .where(
                AdminOverride.target_user_id == target_user_id,
                AdminOverride.override_type == "account_access_change",
                AdminOverride.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        plan_name: str
        duration_label: str
        ends_at: datetime | None
        plan_code: str
        if tier == "free":
            plan_code = "pro_trial"
            plan_name = "Free plan"
            duration_label = f"{self.settings.trial_days}-day Monitor trial"
            ends_at = now + timedelta(days=self.settings.trial_days)
        else:
            plan_code = tier
            ends_at = _add_months(now, int(months or 0)) if tier == "full_access" else None
            plan_name = "Full access" if tier == "full_access" else "Lifetime partner"
            duration_label = (
                f"{months} month{'s' if months != 1 else ''}"
                if tier == "full_access"
                else "Lifetime"
            )
        plan = await PlanCatalogService(self.session).get_or_sync(plan_code)
        await self._cancel_trial(target_user_id, now)
        subscription = Subscription(
            user_id=target_user_id,
            plan_id=plan.id,
            status=(
                SubscriptionStatus.TRIALING
                if tier == "free"
                else SubscriptionStatus.ACTIVE
            ),
            provider="admin",
            provider_subscription_id=f"admin:{action.id}",
            current_period_start=now,
            current_period_end=ends_at,
            cancel_at_period_end=tier in {"free", "full_access"},
        )
        self.session.add(subscription)
        await self.session.flush()
        override = AdminOverride(
            admin_user_id=actor_user_id,
            target_user_id=target_user_id,
            override_type="account_access_change",
            reason=reason,
            payload={
                "tier": tier,
                "plan_code": plan_code,
                "months": months if tier == "full_access" else None,
                "whatsapp": False,
                "admin_action_id": str(action.id),
            },
            effective_at=now,
            expires_at=ends_at,
            created_at=now,
        )
        self.session.add(override)
        await self.session.flush()
        await EntitlementService(self.session).snapshot(target_user_id)
        if tier == "free":
            await EntitlementService(self.session).pause_excess_after_downgrade(target_user_id)
        action_result = await self._complete_action(
            action,
            reason=reason,
            payload={
                "profile_status": "active",
                "previous_plan_code": previous_entitlement.plan.code,
                "tier": tier,
                "plan_code": plan_code,
                "duration_months": months if tier == "full_access" else None,
                "ends_at": ends_at.isoformat() if ends_at else None,
                "whatsapp": False,
            },
            message=f"{plan_name} was applied successfully.",
        )
        delivery = await self._enqueue_access_email(
            user=user,
            action=action,
            plan_name=plan_name,
            duration_label=duration_label,
            ends_at=ends_at,
        )
        return AccountActionResult(
            action=action_result.action,
            message=action_result.message,
            email_delivery_id=delivery.id if delivery else None,
        )

    async def _user_row(self, user: User) -> dict:
        identity = await self.session.scalar(
            select(UserIdentity)
            .where(
                UserIdentity.user_id == user.id,
                UserIdentity.provider == IdentityProvider.EMAIL,
            )
            .order_by(UserIdentity.is_primary.desc(), UserIdentity.created_at.asc())
            .limit(1)
        )
        email = (
            identity.display_identifier or identity.normalized_identifier
            if identity is not None
            else None
        )
        if user.status == UserStatus.DELETED:
            plan_label = "No active access"
            access_ends_at = None
        else:
            entitlement = await EntitlementService(self.session).current(user.id)
            plan_label = {
                "demo": "Free plan",
                "pro_trial": "Free plan",
                "full_access": "Full access",
                "lifetime_partner": "Lifetime partner",
            }.get(entitlement.plan.code, entitlement.plan.name)
            access_ends_at = entitlement.ends_at
        return {
            "id": user.id,
            "display_name": user.display_name or "Deleted profile",
            "email": email or "Email identity removed",
            "role": user.role.value,
            "status": (
                "banned" if user.status == UserStatus.SUSPENDED else user.status.value
            ),
            "created_at": user.created_at,
            "last_seen_at": user.last_seen_at,
            "plan_label": plan_label,
            "access_ends_at": access_ends_at,
            "can_manage": user.role != UserRole.ADMIN and user.status == UserStatus.ACTIVE,
            "can_delete": user.role != UserRole.ADMIN and user.status == UserStatus.ACTIVE,
            "can_ban": user.role != UserRole.ADMIN and user.status == UserStatus.ACTIVE,
            "can_upgrade": user.role != UserRole.ADMIN and user.status == UserStatus.ACTIVE,
            "delete_action_key": str(uuid4()),
            "ban_action_key": str(uuid4()),
            "plan_action_key": str(uuid4()),
        }

    async def _manageable_user(self, actor_user_id: UUID, target_user_id: UUID) -> User:
        user = await self.session.get(User, target_user_id)
        if user is None:
            raise AccountAdminError("user_missing", "User profile not found.")
        if actor_user_id == target_user_id:
            raise AccountAdminError(
                "self_management_blocked",
                "System Brain cannot ban, delete, or replace the current administrator.",
            )
        if user.role == UserRole.ADMIN:
            raise AccountAdminError(
                "administrator_protected",
                "Administrator profiles are protected from customer account controls.",
            )
        return user

    async def _begin_action(
        self,
        *,
        actor_user_id: UUID,
        target_user_id: UUID,
        action_name: str,
        idempotency_key: str,
    ) -> tuple[AccountAdminAction, bool]:
        try:
            canonical_key = str(UUID(idempotency_key))
        except (ValueError, TypeError) as exc:
            raise AccountAdminError(
                "invalid_action_token",
                "This action confirmation expired. Refresh the user list and try again.",
            ) from exc
        existing = await self.session.scalar(
            select(AccountAdminAction).where(
                AccountAdminAction.idempotency_key == canonical_key
            )
        )
        if existing is not None:
            if (
                existing.actor_user_id != actor_user_id
                or existing.target_user_id != target_user_id
                or existing.action != action_name
            ):
                raise AccountAdminError(
                    "action_token_mismatch",
                    "This action token cannot be used for that profile or operation.",
                )
            if existing.status == "completed":
                return existing, True
            raise AccountAdminError(
                "action_in_progress",
                "This account action is already being processed. Refresh before trying again.",
            )
        action = AccountAdminAction(
            idempotency_key=canonical_key,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            action=action_name,
            status="processing",
            payload_redacted={},
            created_at=datetime.now(UTC),
        )
        self.session.add(action)
        await self.session.flush()
        return action, False

    async def _complete_action(
        self,
        action: AccountAdminAction,
        *,
        reason: str,
        payload: dict,
        message: str,
    ) -> AccountActionResult:
        now = datetime.now(UTC)
        action.status = "completed"
        action.payload_redacted = payload
        action.completed_at = now
        self.session.add(
            AuditEvent(
                actor_user_id=action.actor_user_id,
                actor_type="admin",
                action=f"account.{action.action}",
                target_type="user",
                target_id=str(action.target_user_id),
                metadata_redacted={
                    "reason": reason,
                    "admin_action_id": str(action.id),
                    **payload,
                },
                created_at=now,
            )
        )
        await self.session.flush()
        return AccountActionResult(action=action, message=message)

    async def _enqueue_access_email(
        self,
        *,
        user: User,
        action: AccountAdminAction,
        plan_name: str,
        duration_label: str,
        ends_at: datetime | None,
    ) -> AccountEmailDelivery | None:
        identity = await self.session.scalar(
            select(UserIdentity)
            .where(
                UserIdentity.user_id == user.id,
                UserIdentity.provider == IdentityProvider.EMAIL,
                UserIdentity.is_verified.is_(True),
            )
            .order_by(UserIdentity.is_primary.desc(), UserIdentity.created_at.asc())
            .limit(1)
        )
        recipient = identity.normalized_identifier if identity else None
        if not recipient:
            return None
        delivery = AccountEmailDelivery(
            user_id=user.id,
            admin_action_id=action.id,
            event_key=f"account-access-changed:{action.id}",
            recipient=recipient,
            template_kind="access_changed",
            payload_redacted={
                "first_name": ((user.display_name or "").split() or ["there"])[0],
                "plan_name": plan_name,
                "duration_label": duration_label,
                "ends_at": ends_at.isoformat() if ends_at else None,
            },
            status="pending",
            attempt_count=0,
            next_retry_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        self.session.add(delivery)
        await self.session.flush()
        return delivery

    async def _email_identities(self, user_id: UUID) -> list[UserIdentity]:
        return list(
            (
                await self.session.scalars(
                    select(UserIdentity).where(
                        UserIdentity.user_id == user_id,
                        UserIdentity.provider == IdentityProvider.EMAIL,
                        UserIdentity.normalized_identifier.is_not(None),
                    )
                )
            ).all()
        )

    async def _revoke_sessions(self, user_id: UUID, now: datetime) -> None:
        await self.session.execute(
            update(WebSession)
            .where(WebSession.user_id == user_id, WebSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    async def _pause_strategies(self, user_id: UUID, now: datetime) -> None:
        await self.session.execute(
            update(Strategy)
            .where(Strategy.user_id == user_id, Strategy.status == StrategyStatus.ACTIVE)
            .values(status=StrategyStatus.PAUSED, paused_at=now)
        )

    async def _end_access(self, user_id: UUID, now: datetime) -> None:
        await self._cancel_admin_subscriptions(user_id, now)
        await self._cancel_trial(user_id, now)

    async def _cancel_trial(self, user_id: UUID, now: datetime) -> None:
        trial = await self.session.scalar(select(Trial).where(Trial.user_id == user_id))
        if trial is not None and trial.status not in {
            TrialStatus.CONVERTED,
            TrialStatus.EXPIRED,
            TrialStatus.CANCELED,
        }:
            trial.status = TrialStatus.CANCELED
        if trial is not None:
            await self.session.execute(
                update(TrialCycle)
                .where(TrialCycle.trial_id == trial.id, TrialCycle.status == "active")
                .values(status="cancelled", closed_at=now)
            )

    async def _cancel_admin_subscriptions(
        self,
        user_id: UUID,
        now: datetime,
    ) -> None:
        query = select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status.in_(
                {
                    SubscriptionStatus.PENDING,
                    SubscriptionStatus.TRIALING,
                    SubscriptionStatus.ACTIVE,
                    SubscriptionStatus.PAST_DUE,
                }
            ),
        )
        subscriptions = list((await self.session.scalars(query)).all())
        for subscription in subscriptions:
            if subscription.provider not in {None, "admin", "free"}:
                continue
            subscription.status = SubscriptionStatus.CANCELED
            subscription.cancel_at_period_end = True
            subscription.canceled_at = now

    async def _reject_active_provider_subscription(self, user_id: UUID) -> None:
        paid = await self.session.scalar(
            select(Subscription.id).where(
                Subscription.user_id == user_id,
                Subscription.status.in_(
                    {SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING}
                ),
                Subscription.provider.is_not(None),
                Subscription.provider.not_in({"admin", "free"}),
            )
        )
        if paid is not None:
            raise AccountAdminError(
                "provider_subscription_active",
                "This user has an active provider-managed subscription. End it through the "
                "billing provider before replacing access in System Brain.",
            )


def _required_reason(value: str) -> str:
    reason = " ".join(value.split())
    if len(reason) < 3:
        raise AccountAdminError("reason_required", "Enter a reason for this admin action.")
    return reason[:500]


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)
