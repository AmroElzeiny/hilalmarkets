import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.auth_pages import (
    CODE_RESEND_SECONDS,
    password_validation_error,
)
from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.security import (
    InvalidContinuationToken,
    WebSessionTokenService,
    hash_password,
    opaque_token,
    token_digest,
    verify_password,
)
from ai_market_monitor.db.models import (
    AccountEmailDelivery,
    DashboardPreference,
    EmailAuthChallenge,
    PendingEmailSignup,
    User,
    UserIdentity,
    WebSession,
)
from ai_market_monitor.db.models.enums import IdentityProvider, UserRole, UserStatus
from ai_market_monitor.services.account_admin import identifier_is_banned
from ai_market_monitor.services.email_delivery import AuthEmailService
from ai_market_monitor.services.governance_bootstrap import grant_owner_governance_roles

SESSION_COOKIE_NAME = "amm_session"
SESSION_DAYS = 30


class WebAuthError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class WebAuthService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def signup_or_signin_email(
        self,
        *,
        email: str,
        password: str,
        display_name: str | None,
    ) -> tuple[User, bool]:
        normalized = normalize_email(email)
        if not normalized:
            raise WebAuthError("invalid_email", "Enter a valid email address.")
        await self._ensure_not_banned(normalized)
        password_error = password_validation_error(password)
        if password_error:
            raise WebAuthError("invalid_password", password_error)
        identity = await self.session.scalar(
            select(UserIdentity).where(
                UserIdentity.provider == IdentityProvider.EMAIL,
                UserIdentity.normalized_identifier == normalized,
            )
        )
        created = False
        if identity is None:
            is_configured_admin = normalized in self.settings.system_brain_authorized_emails
            user = User(
                display_name=display_name or normalized.split("@", 1)[0],
                role=UserRole.ADMIN if is_configured_admin else UserRole.USER,
            )
            self.session.add(user)
            await self.session.flush()
            identity = UserIdentity(
                user_id=user.id,
                provider=IdentityProvider.EMAIL,
                provider_subject=normalized,
                normalized_identifier=normalized,
                display_identifier=email.strip(),
                password_hash=hash_password(password),
                is_verified=True,
                is_primary=True,
                verified_at=datetime.now(UTC),
                profile_data={},
            )
            self.session.add(identity)
            self.session.add(
                DashboardPreference(
                    user_id=user.id,
                    theme="dark",
                    default_timezone=user.timezone,
                    default_dashboard_path="/dashboard",
                    notification_preferences={
                        "timezone": "UTC",
                        "near_miss_enabled": True,
                        "near_miss_threshold": 70,
                        "maximum_alerts_per_hour": 50,
                        "alert_channels": ["web", "telegram"],
                        "channels": ["web", "telegram"],
                        "providers": ["binance", "bybit"],
                        "alert_days": ["Every Day"],
                        "alert_hours": [],
                    },
                )
            )
            created = True
        else:
            existing_user = await self.session.get(User, identity.user_id)
            if existing_user is None:
                raise WebAuthError("identity_broken", "This identity is not linked correctly.")
            if existing_user.status == UserStatus.SUSPENDED:
                raise WebAuthError("account_banned", "Your profile is banned.")
            if not verify_password(password, identity.password_hash):
                raise WebAuthError("invalid_login", "Email or password is incorrect.")
            user = existing_user
            user.last_seen_at = datetime.now(UTC)
        await self.session.flush()
        if created and user.role == UserRole.ADMIN:
            await grant_owner_governance_roles(
                self.session,
                email=normalized,
                reason="Configured System Brain administrator completed verified signup.",
            )
        return user, created

    async def request_signup_email_code(
        self,
        *,
        email: str,
        password: str,
        display_name: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        telegram_link: str | None = None,
        requested_ip: str | None = None,
    ) -> bool:
        normalized = normalize_email(email)
        if not normalized:
            raise WebAuthError("invalid_email", "Enter a valid email address.")
        await self._ensure_not_banned(normalized)
        password_error = password_validation_error(password)
        if password_error:
            raise WebAuthError("invalid_password", password_error)
        clean_first_name = normalize_person_name(
            first_name,
            label="First name",
            required=False,
        )
        clean_last_name = normalize_person_name(
            last_name,
            label="Last name",
            required=False,
        )
        if not clean_first_name and display_name:
            name_parts = str(display_name).split(maxsplit=1)
            clean_first_name = normalize_person_name(name_parts[0], label="First name")
            clean_last_name = normalize_person_name(
                name_parts[1] if len(name_parts) > 1 else None,
                label="Last name",
                required=False,
            )
        if not clean_first_name:
            clean_first_name = normalize_person_name(
                normalized.split("@", 1)[0],
                label="First name",
            )
        existing_identity = await self.session.scalar(
            select(UserIdentity.id).where(
                UserIdentity.provider == IdentityProvider.EMAIL,
                UserIdentity.normalized_identifier == normalized,
            )
        )
        if existing_identity is not None:
            raise WebAuthError("account_exists", "This email already has an account.")

        now = datetime.now(UTC)
        latest = await self.session.scalar(
            select(PendingEmailSignup)
            .where(PendingEmailSignup.email == normalized)
            .order_by(PendingEmailSignup.created_at.desc())
            .limit(1)
        )
        resend_gate = timedelta(seconds=CODE_RESEND_SECONDS)
        if latest is not None and (_as_aware(latest.created_at) + resend_gate) > now:
            raise WebAuthError(
                "code_recently_sent",
                f"A code was sent recently. Wait {CODE_RESEND_SECONDS} seconds "
                "before requesting another.",
            )
        if latest is not None and latest.consumed_at is None:
            latest.consumed_at = now

        await self._store_signup_code(
            normalized=normalized,
            display_identifier=email.strip(),
            first_name=clean_first_name,
            last_name=clean_last_name,
            password_hash=hash_password(password),
            telegram_link=telegram_link,
            requested_ip_hash=self._request_ip_hash(requested_ip),
            now=now,
        )
        return True

    async def resend_signup_email_code(self, *, email: str) -> bool:
        """Send a waiting sign-up a fresh code, without asking for anything again.

        The row created by :meth:`request_signup_email_code` already holds the name, the
        hashed password and any Telegram link, so somebody whose first email never
        arrived does not have to type all of it a second time. Until this existed the
        only way forward from the confirm step was "Start again" and an empty form —
        which is a lot to ask of a person whose only problem is a slow mail server.

        It stores nothing new and it grants nothing: the fresh code is checked by exactly
        the same path as the first one.
        """

        normalized = normalize_email(email)
        if not normalized:
            raise WebAuthError("invalid_email", "Enter a valid email address.")
        await self._ensure_not_banned(normalized)
        now = datetime.now(UTC)
        latest = await self.session.scalar(
            select(PendingEmailSignup)
            .where(PendingEmailSignup.email == normalized)
            .order_by(PendingEmailSignup.created_at.desc())
            .limit(1)
        )
        if latest is None or latest.consumed_at is not None:
            raise WebAuthError(
                "account_not_registered",
                "No sign-up is waiting for that email.",
            )
        resend_gate = timedelta(seconds=CODE_RESEND_SECONDS)
        if (_as_aware(latest.created_at) + resend_gate) > now:
            raise WebAuthError(
                "code_recently_sent",
                f"A code was sent recently. Wait {CODE_RESEND_SECONDS} seconds "
                "before requesting another.",
            )
        latest.consumed_at = now
        await self._store_signup_code(
            normalized=normalized,
            display_identifier=latest.display_identifier,
            first_name=latest.first_name,
            last_name=latest.last_name,
            password_hash=latest.password_hash,
            telegram_link=latest.telegram_link,
            requested_ip_hash=latest.requested_ip_hash,
            now=now,
        )
        return True

    async def _store_signup_code(
        self,
        *,
        normalized: str,
        display_identifier: str,
        first_name: str | None,
        last_name: str | None,
        password_hash: str,
        telegram_link: str | None,
        requested_ip_hash: str | None,
        now: datetime,
    ) -> None:
        """Write one waiting sign-up and email its code.

        Both the first request and a resend land here, so the two can never disagree
        about how long a code lasts or how many tries it gets.
        """

        code = self._new_auth_code()
        self.session.add(
            PendingEmailSignup(
                email=normalized,
                display_identifier=display_identifier,
                first_name=first_name,
                last_name=last_name,
                password_hash=password_hash,
                telegram_link=telegram_link,
                code_digest=self._auth_code_digest(normalized, "signup", code),
                created_at=now,
                expires_at=now + timedelta(minutes=self.settings.auth_code_ttl_minutes),
                attempts=0,
                max_attempts=self.settings.auth_code_max_attempts,
                requested_ip_hash=requested_ip_hash,
            )
        )
        await self.session.flush()
        await AuthEmailService(self.settings).send_code(
            recipient=display_identifier,
            code=code,
            purpose="signup",
        )

    async def complete_signup_with_email_code(self, *, email: str, code: str) -> User:
        normalized = normalize_email(email)
        if not normalized or not code.isdigit() or len(code) != 6:
            raise WebAuthError("invalid_code", "Enter the six-digit code from your email.")
        await self._ensure_not_banned(normalized)
        pending = await self.session.scalar(
            select(PendingEmailSignup)
            .where(
                PendingEmailSignup.email == normalized,
                PendingEmailSignup.consumed_at.is_(None),
            )
            .order_by(PendingEmailSignup.created_at.desc())
            .limit(1)
        )
        now = datetime.now(UTC)
        if pending is None or _as_aware(pending.expires_at) <= now:
            raise WebAuthError("code_expired", "The code is invalid or expired.")
        if pending.attempts >= pending.max_attempts:
            pending.consumed_at = now
            raise WebAuthError("code_locked", "Too many attempts. Request a new code.")
        pending.attempts += 1
        expected = self._auth_code_digest(normalized, "signup", code)
        if not hmac.compare_digest(expected, pending.code_digest):
            if pending.attempts >= pending.max_attempts:
                pending.consumed_at = now
            raise WebAuthError("invalid_code", "The code is invalid or expired.")

        existing_identity = await self.session.scalar(
            select(UserIdentity.id).where(
                UserIdentity.provider == IdentityProvider.EMAIL,
                UserIdentity.normalized_identifier == normalized,
            )
        )
        if existing_identity is not None:
            pending.consumed_at = now
            raise WebAuthError("account_exists", "This email already has an account.")

        display_name = " ".join(
            part for part in (pending.first_name, pending.last_name) if part
        ) or normalized.split("@", 1)[0]
        is_configured_admin = normalized in self.settings.system_brain_authorized_emails
        user = User(
            display_name=display_name,
            role=UserRole.ADMIN if is_configured_admin else UserRole.USER,
        )
        self.session.add(user)
        await self.session.flush()
        self.session.add(
            UserIdentity(
                user_id=user.id,
                provider=IdentityProvider.EMAIL,
                provider_subject=normalized,
                normalized_identifier=normalized,
                display_identifier=pending.display_identifier,
                password_hash=pending.password_hash,
                is_verified=True,
                is_primary=True,
                verified_at=now,
                profile_data={
                    "first_name": pending.first_name,
                    "last_name": pending.last_name,
                },
            )
        )
        self.session.add(
            DashboardPreference(
                user_id=user.id,
                theme="dark",
                default_timezone=user.timezone,
                default_dashboard_path="/dashboard",
                notification_preferences={
                    "timezone": "UTC",
                    "near_miss_enabled": True,
                    "near_miss_threshold": 70,
                    "maximum_alerts_per_hour": 50,
                    "alert_channels": ["web", "telegram"],
                    "channels": ["web", "telegram"],
                    "providers": ["binance", "bybit"],
                    "alert_days": ["Every Day"],
                    "alert_hours": [],
                },
            )
        )
        pending.consumed_at = now
        # The welcome. Queued rather than sent here, so a mail provider having a bad
        # minute cannot fail the sign-up itself — the account is made either way and the
        # email follows. `event_key` is the account, so it can only ever be sent once.
        self.session.add(
            AccountEmailDelivery(
                user_id=user.id,
                recipient=normalized,
                template_kind="signup_welcome",
                event_key=f"signup_welcome:{user.id}",
                payload_redacted={"first_name": pending.first_name or ""},
                status="pending",
                # This table has no automatic timestamp, and the outbox orders by it.
                created_at=now,
            )
        )
        await self.session.flush()
        if is_configured_admin:
            await grant_owner_governance_roles(
                self.session,
                email=normalized,
                reason="Configured System Brain administrator completed verified signup.",
            )
        return user

    async def request_email_code(
        self,
        *,
        email: str,
        purpose: str,
        requested_ip: str | None = None,
    ) -> bool:
        normalized = normalize_email(email)
        if not normalized or purpose not in {"login", "password_reset"}:
            return False
        await self._ensure_not_banned(normalized)
        identity = await self.session.scalar(
            select(UserIdentity).where(
                UserIdentity.provider == IdentityProvider.EMAIL,
                UserIdentity.normalized_identifier == normalized,
            )
        )
        if identity is None:
            return False
        user = await self.session.get(User, identity.user_id)
        if user is None:
            return False
        if user.status == UserStatus.SUSPENDED:
            raise WebAuthError("account_banned", "Your profile is banned.")
        if user.status != UserStatus.ACTIVE:
            return False
        now = datetime.now(UTC)
        latest = await self.session.scalar(
            select(EmailAuthChallenge)
            .where(
                EmailAuthChallenge.email == normalized,
                EmailAuthChallenge.purpose == purpose,
            )
            .order_by(EmailAuthChallenge.created_at.desc())
            .limit(1)
        )
        resend_gate = timedelta(seconds=CODE_RESEND_SECONDS)
        if latest is not None and (_as_aware(latest.created_at) + resend_gate) > now:
            raise WebAuthError(
                "code_recently_sent",
                f"A code was sent recently. Wait {CODE_RESEND_SECONDS} seconds "
                "before requesting another.",
            )
        if latest is not None and latest.consumed_at is None:
            latest.consumed_at = now
        code = self._new_auth_code()
        challenge = EmailAuthChallenge(
            user_id=user.id,
            email=normalized,
            purpose=purpose,
            code_digest=self._auth_code_digest(normalized, purpose, code),
            created_at=now,
            expires_at=now + timedelta(minutes=self.settings.auth_code_ttl_minutes),
            attempts=0,
            max_attempts=self.settings.auth_code_max_attempts,
            requested_ip_hash=self._request_ip_hash(requested_ip),
        )
        self.session.add(challenge)
        await self.session.flush()
        await AuthEmailService(self.settings).send_code(
            recipient=identity.display_identifier or normalized,
            code=code,
            purpose=purpose,
        )
        return True

    async def signin_with_email_code(self, *, email: str, code: str) -> User:
        user, _ = await self._consume_email_code(email=email, code=code, purpose="login")
        user.last_seen_at = datetime.now(UTC)
        await self.session.flush()
        return user

    async def reset_password_with_email_code(
        self,
        *,
        email: str,
        code: str,
        password: str,
    ) -> User:
        password_error = password_validation_error(password)
        if password_error:
            raise WebAuthError("invalid_password", password_error)
        user, identity = await self._consume_email_code(
            email=email,
            code=code,
            purpose="password_reset",
        )
        identity.password_hash = hash_password(password)
        user.last_seen_at = datetime.now(UTC)
        await self.session.flush()
        return user

    async def _consume_email_code(
        self,
        *,
        email: str,
        code: str,
        purpose: str,
    ) -> tuple[User, UserIdentity]:
        normalized = normalize_email(email)
        if not normalized or not code.isdigit() or len(code) != 6:
            raise WebAuthError("invalid_code", "Enter the six-digit code from your email.")
        await self._ensure_not_banned(normalized)
        challenge = await self.session.scalar(
            select(EmailAuthChallenge)
            .where(
                EmailAuthChallenge.email == normalized,
                EmailAuthChallenge.purpose == purpose,
                EmailAuthChallenge.consumed_at.is_(None),
            )
            .order_by(EmailAuthChallenge.created_at.desc())
            .limit(1)
        )
        now = datetime.now(UTC)
        if challenge is None or _as_aware(challenge.expires_at) <= now:
            raise WebAuthError("code_expired", "The code is invalid or expired.")
        if challenge.attempts >= challenge.max_attempts:
            challenge.consumed_at = now
            raise WebAuthError("code_locked", "Too many attempts. Request a new code.")
        challenge.attempts += 1
        expected = self._auth_code_digest(normalized, purpose, code)
        if not hmac.compare_digest(expected, challenge.code_digest):
            if challenge.attempts >= challenge.max_attempts:
                challenge.consumed_at = now
            raise WebAuthError("invalid_code", "The code is invalid or expired.")
        challenge.consumed_at = now
        identity = await self.session.scalar(
            select(UserIdentity).where(
                UserIdentity.provider == IdentityProvider.EMAIL,
                UserIdentity.normalized_identifier == normalized,
            )
        )
        if identity is None:
            raise WebAuthError("identity_broken", "This identity is not linked correctly.")
        user = await self.session.get(User, identity.user_id)
        if user is None:
            raise WebAuthError("identity_broken", "This identity is not linked correctly.")
        if user.status == UserStatus.SUSPENDED:
            raise WebAuthError("account_banned", "Your profile is banned.")
        if user.status != UserStatus.ACTIVE:
            raise WebAuthError("account_unavailable", "This profile is not available.")
        return user, identity

    def _auth_code_digest(self, email: str, purpose: str, code: str) -> str:
        secret = self.settings.app_secret_key.get_secret_value().encode("utf-8")
        payload = f"{email}:{purpose}:{code}".encode()
        return hmac.new(secret, payload, hashlib.sha256).hexdigest()

    def _new_auth_code(self) -> str:
        return self.settings.fixed_auth_code or f"{secrets.randbelow(1_000_000):06d}"

    def _request_ip_hash(self, requested_ip: str | None) -> str | None:
        if not requested_ip:
            return None
        secret = self.settings.app_secret_key.get_secret_value().encode("utf-8")
        return hmac.new(secret, requested_ip.encode("utf-8"), hashlib.sha256).hexdigest()

    async def _ensure_not_banned(self, normalized_email: str) -> None:
        if await identifier_is_banned(self.session, self.settings, normalized_email):
            raise WebAuthError("account_banned", "Your profile is banned.")

    async def signin_email(self, *, email: str, password: str) -> User:
        normalized = normalize_email(email)
        if not normalized:
            raise WebAuthError("invalid_email", "Enter a valid email address.")
        await self._ensure_not_banned(normalized)
        password = password or ""
        if not password:
            raise WebAuthError("invalid_login", "Email or password is incorrect.")
        identity = await self.session.scalar(
            select(UserIdentity).where(
                UserIdentity.provider == IdentityProvider.EMAIL,
                UserIdentity.normalized_identifier == normalized,
            )
        )
        if identity is None:
            raise WebAuthError("invalid_login", "No verified account exists for that email.")
        if not verify_password(password, identity.password_hash):
            raise WebAuthError("invalid_login", "Email or password is incorrect.")
        user = await self.session.get(User, identity.user_id)
        if user is None:
            raise WebAuthError("identity_broken", "This identity is not linked correctly.")
        if user.status == UserStatus.SUSPENDED:
            raise WebAuthError("account_banned", "Your profile is banned.")
        if user.status != UserStatus.ACTIVE:
            raise WebAuthError("account_unavailable", "This profile is not available.")
        user.last_seen_at = datetime.now(UTC)
        await self.session.flush()
        return user

    async def create_session(self, user: User, *, user_agent: str | None = None) -> str:
        raw = opaque_token()
        row = WebSession(
            user_id=user.id,
            session_digest=token_digest(raw),
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=SESSION_DAYS),
            user_agent=user_agent,
        )
        self.session.add(row)
        await self.session.flush()
        return WebSessionTokenService(self.settings).issue(row.id, raw)

    async def current_user(self, cookie_value: str | None) -> User | None:
        if not cookie_value:
            return None
        try:
            payload = WebSessionTokenService(self.settings).decode(cookie_value)
            session_id = UUID(payload["session_id"])
            digest = token_digest(payload["token"])
        except (InvalidContinuationToken, KeyError, ValueError):
            return None
        row = await self.session.get(WebSession, session_id)
        now = datetime.now(UTC)
        expires_at = _as_aware(row.expires_at) if row is not None else now
        if (
            row is None
            or row.revoked_at is not None
            or expires_at <= now
            or row.session_digest != digest
        ):
            return None
        user = await self.session.get(User, row.user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            return None
        row.last_seen_at = now
        user.last_seen_at = now
        return user

    async def revoke(self, cookie_value: str | None) -> None:
        if not cookie_value:
            return
        try:
            payload = WebSessionTokenService(self.settings).decode(cookie_value)
            session_id = UUID(payload["session_id"])
        except (InvalidContinuationToken, KeyError, ValueError):
            return
        row = await self.session.get(WebSession, session_id)
        if row is not None:
            row.revoked_at = datetime.now(UTC)
            await self.session.flush()


def normalize_email(email: str) -> str:
    value = email.strip().casefold()
    if "@" not in value or value.startswith("@") or value.endswith("@"):
        return ""
    return value


def normalize_person_name(
    value: str | None,
    *,
    label: str,
    required: bool = True,
) -> str:
    cleaned = " ".join(str(value or "").split())
    if not cleaned:
        if required:
            raise WebAuthError("invalid_name", f"{label} is required.")
        return ""
    if len(cleaned) > 60 or not any(character.isalpha() for character in cleaned):
        raise WebAuthError("invalid_name", f"Enter a valid {label.lower()}.")
    if any(ord(character) < 32 or character in "<>" for character in cleaned):
        raise WebAuthError("invalid_name", f"Enter a valid {label.lower()}.")
    return cleaned


def normalize_password(password: str) -> str:
    return password if not password_validation_error(password) else ""


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
