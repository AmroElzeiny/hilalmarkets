import hashlib
import hmac
from uuid import UUID

from ai_market_monitor.core.config import Settings


def csrf_token(settings: Settings, user_id: UUID, *, scope: str = "dashboard") -> str:
    secret = settings.app_secret_key.get_secret_value().encode("utf-8")
    message = f"{scope}:{user_id}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def csrf_token_matches(
    settings: Settings,
    user_id: UUID,
    supplied: str | None,
    *,
    scope: str = "dashboard",
) -> bool:
    if not supplied:
        return False
    return hmac.compare_digest(csrf_token(settings, user_id, scope=scope), supplied)
