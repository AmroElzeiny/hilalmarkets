from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.web_auth import SESSION_COOKIE_NAME


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    scope: str
    methods: frozenset[str]
    pattern: re.Pattern[str]
    limit: int
    window_seconds: int


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_PROVIDER_CALLBACK_PREFIXES = (
    "/api/v1/billing/webhooks/",
    "/api/v1/telegram/webhook",
    "/api/v1/whatsapp/webhook",
)
_MEMORY_LOCK = asyncio.Lock()
_MEMORY_WINDOWS: dict[str, tuple[int, float]] = {}


def rate_limit_rules(settings: Settings) -> tuple[RateLimitRule, ...]:
    configured = settings.api_rate_limits
    return (
        _rule(
            "authentication",
            {"POST"},
            r"^/(signup|signin|forgot-password|reset-password)(/|$)",
            configured,
        ),
        _rule("authentication", {"POST"}, r"^/system-brain/(login|verify)(/|$)", configured),
        _rule(
            "ai_chat",
            {"POST"},
            (
                r"^/api/v1/(setup-chat|strategies/interpret|scan-now/interpret)"
                r"|^/dashboard/system-brain/assistant$"
            ),
            configured,
        ),
        _rule(
            "market_check",
            {"POST"},
            r"^/api/v1/(on-demand-scans|scan-now|light-scan|sharia/market/check)",
            configured,
        ),
        _rule(
            "checkout",
            {"POST"},
            r"^/(?:api/v1/)?billing/checkout(?:/|$)|^/dashboard/billing/checkout(?:/|$)",
            configured,
        ),
        _rule("portal", {"POST"}, r"^/api/v1/billing/portal", configured),
        _rule(
            "support",
            {"POST"},
            r"^/api/v1/(?:[^/]+/)?support(?:/|$)",
            configured,
        ),
        _rule(
            "passport_report",
            {"POST"},
            r"^/api/v1/sharia/.*/(report|problem)(?:-reports?)?(/|$)",
            configured,
        ),
        _rule(
            "telegram_test",
            {"POST"},
            r"^/api/v1/.*/(telegram|notifications)/.*test(/|$)",
            configured,
        ),
        _rule(
            "whatsapp_test",
            {"POST"},
            r"^/api/v1/whatsapp/(test|link)(/|$)",
            configured,
        ),
        _rule(
            "public_chat",
            {"POST"},
            r"^/api/v1/public-chat/(profile|answers|ratings)(/|$)",
            configured,
        ),
        _rule(
            "public_inquiry",
            {"POST", "DELETE"},
            r"^/api/v1/public-chat/inquiries(?:/|$)",
            configured,
        ),
        _rule(
            "public_waitlist",
            {"POST"},
            r"^/api/v1/public-forms/waitlist(?:/|$)",
            configured,
        ),
        _rule(
            "public_contact",
            {"POST"},
            r"^/api/v1/public-forms/contact(?:/|$)",
            configured,
        ),
        _rule(
            "admin_mutation",
            {"POST", "PUT", "PATCH", "DELETE"},
            (
                r"^(/api/v1/(admin|sharia/admin)|/system-brain"
                r"|/dashboard/system-brain)(/|$)"
            ),
            configured,
        ),
    )


async def apply_request_guards(
    request: Request,
    settings: Settings,
) -> JSONResponse | None:
    csrf_failure = same_origin_failure(request, settings)
    if csrf_failure is not None:
        return csrf_failure
    if not settings.api_rate_limiting_enabled:
        return None
    rule = matching_rate_limit_rule(request.method, request.url.path, settings)
    if rule is None:
        return None
    fingerprints = {_request_fingerprint(request, settings)}
    if rule.scope in {"public_chat", "public_waitlist", "public_contact"}:
        fingerprints.add(_request_ip_fingerprint(request, settings))
    for fingerprint in fingerprints:
        key = f"hm:rate:{rule.scope}:{fingerprint}"
        try:
            if settings.app_env == "test":
                raise RedisError("Tests use the isolated in-memory limiter.")
            count, retry_after = await _redis_increment(
                settings.redis_url,
                key,
                rule.window_seconds,
            )
        except RedisError:
            if settings.is_deployed and settings.api_rate_limit_fail_closed:
                return _safe_error(
                    503,
                    "rate_limit_unavailable",
                    "This action is temporarily unavailable. Please try again shortly.",
                )
            count, retry_after = await _memory_increment(
                f"{id(settings)}:{key}", rule.window_seconds
            )
        if count > rule.limit:
            response = _safe_error(
                429,
                "rate_limit_exceeded",
                "Too many requests. Wait a moment and try again.",
            )
            response.headers["Retry-After"] = str(max(1, retry_after))
            return response
    return None


def same_origin_failure(request: Request, settings: Settings) -> JSONResponse | None:
    if not settings.is_deployed or request.method.upper() in _SAFE_METHODS:
        return None
    if request.url.path.startswith(_PROVIDER_CALLBACK_PREFIXES):
        return None
    if SESSION_COOKIE_NAME not in request.cookies:
        return None
    supplied = request.headers.get("origin")
    if not supplied:
        referer = request.headers.get("referer")
        supplied = _origin(referer) if referer else None
    allowed = {
        _origin(str(settings.public_base_url)),
        _origin(str(settings.app_base_url)) if settings.app_base_url else None,
    }
    if supplied and _origin(supplied) in allowed:
        return None
    return _safe_error(
        403,
        "csrf_origin_rejected",
        "The request origin could not be verified. Refresh the page and try again.",
    )


def matching_rate_limit_rule(
    method: str,
    path: str,
    settings: Settings,
) -> RateLimitRule | None:
    normalized_method = method.upper()
    for rule in rate_limit_rules(settings):
        if normalized_method in rule.methods and rule.pattern.search(path):
            return rule
    return None


def _rule(
    scope: str,
    methods: set[str],
    pattern: str,
    configured: dict[str, dict[str, int]],
) -> RateLimitRule:
    values = configured[scope]
    return RateLimitRule(
        scope=scope,
        methods=frozenset(methods),
        pattern=re.compile(pattern),
        limit=values["limit"],
        window_seconds=values["window_seconds"],
    )


async def _redis_increment(redis_url: str, key: str, window_seconds: int) -> tuple[int, int]:
    client = Redis.from_url(redis_url, decode_responses=True)
    try:
        async with client.pipeline(transaction=True) as pipeline:
            pipeline.incr(key)
            pipeline.ttl(key)
            count, ttl = await pipeline.execute()
        if int(count) == 1 or int(ttl) < 0:
            await client.expire(key, window_seconds)
            ttl = window_seconds
        return int(count), max(1, int(ttl))
    finally:
        await client.aclose()


async def _memory_increment(key: str, window_seconds: int) -> tuple[int, int]:
    now = time.monotonic()
    async with _MEMORY_LOCK:
        count, expires_at = _MEMORY_WINDOWS.get(key, (0, now + window_seconds))
        if expires_at <= now:
            count, expires_at = 0, now + window_seconds
        count += 1
        _MEMORY_WINDOWS[key] = (count, expires_at)
        if len(_MEMORY_WINDOWS) > 10_000:
            expired = [stored for stored, (_, expiry) in _MEMORY_WINDOWS.items() if expiry <= now]
            for stored in expired:
                _MEMORY_WINDOWS.pop(stored, None)
        return count, max(1, int(expires_at - now))


def _request_fingerprint(request: Request, settings: Settings) -> str:
    principal_hint = request.headers.get("X-User-ID")
    session_hint = request.cookies.get(SESSION_COOKIE_NAME)
    remote = request.client.host if request.client else "unknown"
    raw = session_hint or (
        principal_hint if principal_hint and not settings.is_deployed else remote
    )
    material = f"{settings.app_secret_key.get_secret_value()}:{raw}".encode()
    return hashlib.sha256(material).hexdigest()[:32]


def _request_ip_fingerprint(request: Request, settings: Settings) -> str:
    remote = request.client.host if request.client else "unknown"
    material = f"{settings.app_secret_key.get_secret_value()}:ip:{remote}".encode()
    return hashlib.sha256(material).hexdigest()[:32]


def _origin(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _safe_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": {"code": code, "message": message}},
    )
