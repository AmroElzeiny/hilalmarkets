import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Final
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
from ai_market_monitor.services.google_oauth import GoogleProfile
from ai_market_monitor.services.governance_bootstrap import ensure_configured_owner_grants

SESSION_COOKIE_NAME = "amm_session"
SESSION_DAYS = 30

#: What a brand-new account's dashboard is set to.
#:
#: This dictionary was written out three times — once in each place that could create an
#: account — and the three had already drifted: only one of them queued the welcome
#: email, so an account made through the wrong door was silently never welcomed. One
#: copy, read by the one function below that makes an account.
NEW_ACCOUNT_PREFERENCES: dict[str, object] = {
    "timezone": "UTC",
    "near_miss_enabled": True,
    "near_miss_threshold": 70,
    "maximum_alerts_per_hour": 50,
    "alert_channels": ["web", "telegram"],
    "channels": ["web", "telegram"],
    "providers": ["binance", "bybit"],
    "alert_days": ["Every Day"],
    "alert_hours": [],
}


class WebAuthError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


#: Which door opens an account. One question, one answer, one owner.
#:
#: There is exactly **one** account per email address — `user_identities` carries a unique
#: constraint on (provider, normalized_identifier), so a second one cannot be made even by
#: mistake. What differs is how it opens. An account made through Google has no password
#: at all, and `verify_password` refuses a null hash.
#:
#: Four places refuse a password or refuse a sign-up because an account already exists,
#: and all four used to answer as though every account had a password. Somebody who joined
#: with Google and then typed a password was told "Email or password is incorrect" — which
#: is not true, is not actionable, and sent them round the same wrong loop for ever. The
#: question "which door is this?" is asked here, once, so all four say the same thing.
SIGN_IN_DOOR_PASSWORD: Final[str] = "password"
SIGN_IN_DOOR_GOOGLE: Final[str] = "google"


def sign_in_door(identity: UserIdentity) -> str:
    """Which door opens this account: its own password, or Google.

    A stored password hash is the whole of the test. An account with none was made
    through Google — the only path that creates one — and can only be opened by Google
    until its owner sets a password through "I forgot my password".
    """

    return SIGN_IN_DOOR_PASSWORD if identity.password_hash else SIGN_IN_DOOR_GOOGLE


def uses_google_only(identity: UserIdentity) -> bool:
    """Is Google the only way into this account today?"""

    return sign_in_door(identity) == SIGN_IN_DOOR_GOOGLE


class WebAuthService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    # ------------------------------------------------------------------
    # One place an account is made.
    # ------------------------------------------------------------------

    async def create_account(
        self,
        *,
        normalized: str,
        display_identifier: str,
        first_name: str | None,
        last_name: str | None,
        password_hash: str | None,
        now: datetime,
        grant_reason: str,
    ) -> User:
        """Make one account, whichever door the person came through.

        Three doors reach this: the six-digit sign-up, the Google button, and the older
        direct email path. Each of them used to build the account itself — the user row,
        the identity, the dashboard defaults, the welcome email and the administrator
        grant — and the three copies had already drifted apart. Only one of them ever
        queued the welcome email, so whether a new customer was greeted at all depended
        on which door they happened to use.

        ``password_hash`` may be ``None``. That is a Google account: it has no password,
        and ``verify_password`` refuses a null hash, so no password can ever open it.
        """

        # An unknown name stays unknown. It is never taken from the email address.
        #
        # This fallback used to read `or normalized.split("@", 1)[0]`, and it almost never
        # fired while the sign-up form still had two name boxes. Signing up with an email
        # is three screens now — address, password, code — and not one of them asks for a
        # name, so the fallback fired for *every* new account and wrote the local part of
        # the address in as the person's name. Every reader then treated it as a real one:
        # a receipt opened "Assalamu Alaikum render,", the payment form pre-filled "render"
        # as a legal first name, and an affiliate was shown the local part of a customer's
        # email address in place of their name — the one thing `affiliate._display_name`
        # says must never happen, defeated because the value looked like a name to it.
        #
        # `display_name` is nullable, and every place that shows it already has a fallback
        # for empty. Nothing invents a name here; the Google door supplies a real one, and
        # anyone else stays nameless until they choose to give one.
        display_name = " ".join(part for part in (first_name, last_name) if part) or None
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
                display_identifier=display_identifier,
                password_hash=password_hash,
                is_verified=True,
                is_primary=True,
                verified_at=now,
                profile_data={
                    "first_name": first_name or "",
                    "last_name": last_name or "",
                },
            )
        )
        self.session.add(
            DashboardPreference(
                user_id=user.id,
                theme="dark",
                default_timezone=user.timezone,
                default_dashboard_path="/dashboard",
                notification_preferences=dict(NEW_ACCOUNT_PREFERENCES),
            )
        )
        # The welcome. Queued rather than sent here, so a mail provider having a bad
        # minute cannot fail the sign-up itself — the account is made either way and the
        # email follows. `event_key` is the account, so it can only ever be sent once.
        self.session.add(
            AccountEmailDelivery(
                user_id=user.id,
                recipient=normalized,
                template_kind="signup_welcome",
                event_key=f"signup_welcome:{user.id}",
                payload_redacted={"first_name": first_name or ""},
                status="pending",
                # This table has no automatic timestamp, and the outbox orders by it.
                created_at=now,
            )
        )
        await self.session.flush()
        if is_configured_admin:
            await ensure_configured_owner_grants(
                self.session,
                settings=self.settings,
                email=normalized,
                reason=grant_reason,
            )
        return user

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
        identity = await self._email_identity(normalized)
        created = False
        if identity is None:
            parts = str(display_name or "").split(maxsplit=1)
            user = await self.create_account(
                normalized=normalized,
                display_identifier=email.strip(),
                first_name=parts[0] if parts else "",
                last_name=parts[1] if len(parts) > 1 else "",
                password_hash=hash_password(password),
                now=datetime.now(UTC),
                grant_reason=(
                    "Configured System Brain administrator completed verified signup."
                ),
            )
            created = True
        else:
            existing_user = await self.session.get(User, identity.user_id)
            if existing_user is None:
                raise WebAuthError("identity_broken", "This identity is not linked correctly.")
            if existing_user.status == UserStatus.SUSPENDED:
                raise WebAuthError("account_banned", "Your profile is banned.")
            self._check_password(identity, password)
            user = existing_user
            user.last_seen_at = datetime.now(UTC)
        await self.session.flush()
        return user, created

    async def check_signup_details(self, *, email: str, display_name: str = "") -> str:
        """Is this somebody we can make an account for? Answered before any password.

        Signing up is three screens now, and this is the whole of the first one: a name
        and an address. It runs exactly the checks the later screens will run again — the
        shape of the name, the shape of the address, the ban list, and whether an account
        already exists — so a person is told "you already have an account" while they
        have typed two things, instead of after they have chosen and re-typed a password.

        The name is checked here because here is where the box is. A name refused at step
        two would send somebody back past a password they had already chosen, to a field
        that is hidden on that screen.

        It grants nothing and stores nothing. The real work still happens at
        :meth:`request_signup_email_code`, which repeats every check here rather than
        trusting that this ran.
        """

        # "Name", not "Your name": the label is dropped into two different sentences and
        # only one of them takes a possessive. "Enter a valid your name." was the other.
        normalize_person_name(display_name, label="Name")
        normalized = normalize_email(email)
        if not normalized:
            raise WebAuthError("invalid_email", "Enter a valid email address.")
        await self._ensure_not_banned(normalized)
        existing_identity = await self._email_identity(normalized)
        if existing_identity is not None:
            raise self._already_exists(existing_identity)
        return normalized

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
            clean_first_name = normalize_person_name(
                name_parts[0],
                label="First name",
                required=False,
            )
            clean_last_name = normalize_person_name(
                name_parts[1] if len(name_parts) > 1 else None,
                label="Last name",
                required=False,
            )
        # A name is asked for on screen one and carried here in a hidden field, so an
        # empty one means the field was stripped on the way rather than left blank by a
        # person. It is refused rather than filled in.
        #
        # It used to be invented out of the part of the email address before the @, which
        # was wrong twice over: it greeted people as "Assalamu Alaikum trader.99," in the
        # welcome email, and for an address like `123456@example.com` the invented name
        # had no letters in it at all, so `normalize_person_name` refused it and the whole
        # sign-up failed with `invalid_name` — a code with no words written for it, so the
        # page showed "Something went wrong" and nobody could ever get past it. That code
        # is answered in plain words now, and it can only be reached by a request that
        # went round the form.
        if not clean_first_name:
            raise WebAuthError("invalid_name", "Name is required.")
        existing_identity = await self._email_identity(normalized)
        if existing_identity is not None:
            raise self._already_exists(existing_identity)

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

        existing_identity = await self._email_identity(normalized)
        if existing_identity is not None:
            pending.consumed_at = now
            raise self._already_exists(existing_identity)

        pending.consumed_at = now
        return await self.create_account(
            normalized=normalized,
            display_identifier=pending.display_identifier,
            first_name=pending.first_name,
            last_name=pending.last_name,
            password_hash=pending.password_hash,
            now=now,
            grant_reason=(
                "Configured System Brain administrator completed verified signup."
            ),
        )

    async def signin_or_signup_with_google(
        self, *, profile: GoogleProfile
    ) -> tuple[User, bool]:
        """The Google door. One trip, and either a sign-in or a brand-new account.

        Google has already proved the address belongs to this person — that is what
        ``email_verified`` on the identity token means — so there is no six-digit code
        to send and nothing left to confirm. Asking for one anyway would be asking
        somebody to prove twice what they have just proved once.

        The address is the identity, so pressing Google with an address that already has
        a password signs into *that* account rather than making a second one beside it.
        An account created here has no password at all until the person sets one through
        "I forgot my password", which is the same path anybody else uses.
        """

        normalized = normalize_email(profile.email)
        if not normalized:
            raise WebAuthError("google_email_missing", "Google did not share an email.")
        await self._ensure_not_banned(normalized)
        first_name = normalize_person_name(
            profile.first_name, label="First name", required=False
        )
        last_name = normalize_person_name(
            profile.last_name, label="Last name", required=False
        )
        now = datetime.now(UTC)
        identity = await self._email_identity(normalized)
        if identity is None:
            user = await self.create_account(
                normalized=normalized,
                display_identifier=profile.email.strip(),
                first_name=first_name,
                last_name=last_name,
                password_hash=None,
                now=now,
                grant_reason=(
                    "Configured System Brain administrator signed in through Google."
                ),
            )
            await self._record_google_subject(normalized, profile.subject)
            # A half-finished sign-up for the same address is now pointless: the account
            # it was going to create exists. Left alone, its code would still be accepted
            # later and would fail on "this email already has an account".
            await self._retire_pending_signups(normalized, now)
            await self.session.flush()
            return user, True

        existing_user = await self.session.get(User, identity.user_id)
        if existing_user is None:
            raise WebAuthError("identity_broken", "This identity is not linked correctly.")
        if existing_user.status == UserStatus.SUSPENDED:
            raise WebAuthError("account_banned", "Your profile is banned.")
        existing_user.last_seen_at = now
        # An address confirmed by Google is a confirmed address, however the account was
        # first made. Somebody stuck on an unconfirmed sign-up gets in this way.
        identity.is_verified = True
        if identity.verified_at is None:
            identity.verified_at = now
        await self._record_google_subject(normalized, profile.subject, identity=identity)
        await self.session.flush()
        return existing_user, False

    async def _record_google_subject(
        self,
        normalized: str,
        subject: str,
        *,
        identity: UserIdentity | None = None,
    ) -> None:
        """Note that Google vouches for this address, without touching anything else.

        ``profile_data`` is a free-form record, and the name already in it was typed by
        the person themselves. Google's id is added beside it; nothing already there is
        overwritten, because a name somebody chose beats a name a provider guessed.
        """

        if not subject:
            return
        if identity is None:
            identity = await self._email_identity(normalized)
        if identity is None:
            return
        profile_data = dict(identity.profile_data or {})
        if profile_data.get("google_subject") == subject:
            return
        profile_data["google_subject"] = subject
        identity.profile_data = profile_data

    async def _retire_pending_signups(self, normalized: str, now: datetime) -> None:
        pending_rows = await self.session.scalars(
            select(PendingEmailSignup).where(
                PendingEmailSignup.email == normalized,
                PendingEmailSignup.consumed_at.is_(None),
            )
        )
        for row in pending_rows:
            row.consumed_at = now

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
        identity = await self._email_identity(normalized)
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
        identity = await self._email_identity(normalized)
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

    # ------------------------------------------------------------------
    # One place decides what to say when an account already exists, and one place
    # decides what to say when a password does not open it.
    # ------------------------------------------------------------------

    async def _email_identity(self, normalized: str) -> UserIdentity | None:
        """The one identity an email address can have, or ``None``.

        One row, always: `user_identities` is unique on (provider, normalized_identifier),
        so the same address can never end up on two accounts however somebody signs up.
        """

        return await self.session.scalar(
            select(UserIdentity).where(
                UserIdentity.provider == IdentityProvider.EMAIL,
                UserIdentity.normalized_identifier == normalized,
            )
        )

    @staticmethod
    def _already_exists(identity: UserIdentity) -> WebAuthError:
        """"You already have an account" — said differently for the two kinds of account.

        Sending a Google customer to "sign in with your password" is sending them to a
        password that does not exist. They can only go round again.
        """

        if uses_google_only(identity):
            return WebAuthError(
                "account_exists_google",
                "This email already has an account, made with Google.",
            )
        return WebAuthError("account_exists", "This email already has an account.")

    @staticmethod
    def _check_password(identity: UserIdentity, password: str) -> None:
        """Refuse a password, and be honest about which of the two reasons it is.

        A Google account has no password, so "email or password is incorrect" is simply
        false there — the password is neither right nor wrong, there is nothing to check
        it against. Saying so is also the only way somebody learns which button to press.

        This does reveal that an address has an account. That is already true on this
        route: signing up answers "this email already has an account", and signing in
        answers "no verified account exists for that email". Naming the door adds nothing
        an attacker did not already have, and removes a dead end for everybody else.
        """

        if uses_google_only(identity):
            raise WebAuthError(
                "google_sign_in_required",
                "This account was made with Google, so it has no password yet.",
            )
        if not verify_password(password, identity.password_hash):
            raise WebAuthError("invalid_login", "Email or password is incorrect.")

    async def signin_email(self, *, email: str, password: str) -> User:
        normalized = normalize_email(email)
        if not normalized:
            raise WebAuthError("invalid_email", "Enter a valid email address.")
        await self._ensure_not_banned(normalized)
        password = password or ""
        identity = await self._email_identity(normalized)
        if identity is None:
            raise WebAuthError("invalid_login", "No verified account exists for that email.")
        # An empty box used to be refused before the account was looked at, which meant a
        # Google customer who pressed the button with no password typed was told their
        # password was wrong rather than that they have no password. The account is read
        # first now, so the answer is about the account rather than about the form.
        self._check_password(identity, password)
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
        await self._reconcile_governance_authority(user)
        return WebSessionTokenService(self.settings).issue(row.id, raw)

    async def _reconcile_governance_authority(self, user: User) -> None:
        """Bring a configured System Brain owner's governance grants up to date.

        Signing in is the one door every way into the dashboard passes through — sign-up,
        password, emailed code and the one-click link all end here — so it is where the
        grants named by ``SYSTEM_BRAIN_ADMIN_EMAILS`` are reconciled. Writing them only
        while an account was being created left an owner whose account already existed
        with no authority at all, and no way to gain any without a shell on the server.

        Costs nothing for anybody else: an account that is not an administrator, or a
        deployment with no configured owner, returns before touching the database.
        """

        if user.role != UserRole.ADMIN or not self.settings.system_brain_authorized_emails:
            return
        addresses = (
            await self.session.scalars(
                select(UserIdentity.normalized_identifier).where(
                    UserIdentity.user_id == user.id,
                    UserIdentity.provider == IdentityProvider.EMAIL,
                    UserIdentity.is_verified.is_(True),
                )
            )
        ).all()
        for address in addresses:
            granted = await ensure_configured_owner_grants(
                self.session,
                settings=self.settings,
                email=address or "",
                reason="Configured System Brain owner signed in.",
            )
            if granted:
                return

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
