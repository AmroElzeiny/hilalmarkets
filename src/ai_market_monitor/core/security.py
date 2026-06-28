import hashlib
import hmac
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from uuid import UUID

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ai_market_monitor.core.config import Settings


class InvalidContinuationToken(ValueError):
    pass


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def opaque_token() -> str:
    return secrets.token_urlsafe(32)


PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 310_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )
    return "$".join(
        [
            PASSWORD_HASH_ALGORITHM,
            str(PASSWORD_HASH_ITERATIONS),
            _b64(salt),
            _b64(digest),
        ]
    )


def verify_password(password: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = stored_hash.split("$", 3)
        if algorithm != PASSWORD_HASH_ALGORITHM:
            return False
        iterations = int(iterations_raw)
        salt = _unb64(salt_raw)
        expected = _unb64(digest_raw)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected)


def _b64(value: bytes) -> str:
    return urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return urlsafe_b64decode((value + padding).encode("ascii"))


class ContinuationTokenService:
    salt = "onboarding-continuation-v1"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.serializer = URLSafeTimedSerializer(settings.app_secret_key.get_secret_value())

    def issue(self, user_id: UUID, onboarding_session_id: UUID) -> tuple[str, datetime]:
        nonce = opaque_token()
        token = self.serializer.dumps(
            {"user_id": str(user_id), "session_id": str(onboarding_session_id), "nonce": nonce},
            salt=self.salt,
        )
        expires_at = datetime.now(UTC) + timedelta(
            minutes=self.settings.continuation_token_ttl_minutes
        )
        return token, expires_at

    def decode(self, token: str) -> dict[str, str]:
        try:
            return self.serializer.loads(
                token,
                salt=self.salt,
                max_age=self.settings.continuation_token_ttl_minutes * 60,
            )
        except (BadSignature, SignatureExpired) as exc:
            raise InvalidContinuationToken("Continuation link is invalid or expired") from exc


class OnboardingAccessTokenService:
    salt = "onboarding-access-v1"
    max_age_seconds = 24 * 60 * 60

    def __init__(self, settings: Settings):
        self.serializer = URLSafeTimedSerializer(settings.app_secret_key.get_secret_value())

    def issue(self, user_id: UUID, onboarding_session_id: UUID) -> str:
        return self.serializer.dumps(
            {"user_id": str(user_id), "session_id": str(onboarding_session_id)}, salt=self.salt
        )

    def decode(self, token: str) -> dict[str, str]:
        try:
            return self.serializer.loads(token, salt=self.salt, max_age=self.max_age_seconds)
        except (BadSignature, SignatureExpired) as exc:
            raise InvalidContinuationToken(
                "Onboarding session token is invalid or expired"
            ) from exc


class IdentityAssertionTokenService:
    """Verifies short-lived assertions created by trusted OAuth, bot, or magic-link adapters."""

    salt = "identity-provider-assertion-v1"
    max_age_seconds = 5 * 60

    def __init__(self, settings: Settings):
        self.serializer = URLSafeTimedSerializer(settings.app_secret_key.get_secret_value())

    def issue(self, provider: str, provider_subject: str, email: str | None = None) -> str:
        return self.serializer.dumps(
            {
                "provider": provider,
                "provider_subject": provider_subject,
                "email": email.casefold() if email else None,
            },
            salt=self.salt,
        )

    def verify(
        self,
        token: str,
        *,
        provider: str,
        provider_subject: str,
        email: str | None,
    ) -> None:
        try:
            payload = self.serializer.loads(token, salt=self.salt, max_age=self.max_age_seconds)
        except (BadSignature, SignatureExpired) as exc:
            raise InvalidContinuationToken("Identity assertion is invalid or expired") from exc
        expected_email = email.casefold() if email else None
        if (
            payload.get("provider") != provider
            or payload.get("provider_subject") != provider_subject
            or payload.get("email") != expected_email
        ):
            raise InvalidContinuationToken("Identity assertion does not match the identity")


class WebSessionTokenService:
    salt = "web-session-v1"
    max_age_seconds = 30 * 24 * 60 * 60

    def __init__(self, settings: Settings):
        self.serializer = URLSafeTimedSerializer(settings.app_secret_key.get_secret_value())

    def issue(self, session_id: UUID, raw_token: str) -> str:
        return self.serializer.dumps(
            {"session_id": str(session_id), "token": raw_token},
            salt=self.salt,
        )

    def decode(self, cookie_value: str) -> dict[str, str]:
        try:
            return self.serializer.loads(
                cookie_value,
                salt=self.salt,
                max_age=self.max_age_seconds,
            )
        except (BadSignature, SignatureExpired) as exc:
            raise InvalidContinuationToken("Dashboard session is invalid or expired") from exc


class DashboardLinkTokenService:
    salt = "telegram-dashboard-link-v1"
    max_age_seconds = 15 * 60

    def __init__(self, settings: Settings):
        self.serializer = URLSafeTimedSerializer(settings.app_secret_key.get_secret_value())

    def issue(self, link_id: UUID, raw_token: str) -> str:
        return self.serializer.dumps({"link_id": str(link_id), "token": raw_token}, salt=self.salt)

    def decode(self, token: str) -> dict[str, str]:
        try:
            return self.serializer.loads(token, salt=self.salt, max_age=self.max_age_seconds)
        except (BadSignature, SignatureExpired) as exc:
            raise InvalidContinuationToken("Dashboard link is invalid or expired") from exc
