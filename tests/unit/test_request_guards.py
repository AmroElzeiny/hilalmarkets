from types import SimpleNamespace

from starlette.requests import Request

from ai_market_monitor.api.request_guards import (
    client_fingerprint,
    matching_rate_limit_rule,
    same_origin_failure,
)
from ai_market_monitor.core.config import Settings


def _settings(**overrides) -> Settings:
    values = {
        "app_env": "test",
        "app_secret_key": "test-secret-key-with-at-least-thirty-two-characters",
        "database_url": "sqlite+aiosqlite://",
        "public_base_url": "https://hilal.example",
        "app_base_url": "https://app.hilal.example",
    }
    values.update(overrides)
    return Settings(**values)


def _request(
    *,
    method: str,
    path: str,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers or [],
            "client": ("127.0.0.1", 12345),
            "server": ("app.hilal.example", 443),
            "root_path": "",
            "app": SimpleNamespace(),
        }
    )


def test_named_sensitive_routes_have_rate_limit_scopes() -> None:
    settings = _settings()
    cases = {
        "/signin": "authentication",
        "/forgot-password": "authentication",
        "/api/v1/setup-chat/sessions": "ai_chat",
        "/api/v1/dashboard/setup-chat/sessions": "ai_chat",
        "/api/v1/on-demand-scans": "market_check",
        "/api/v1/billing/checkout": "checkout",
        "/dashboard/billing/checkout": "checkout",
        "/api/v1/billing/portal": "portal",
        "/api/v1/support/tickets": "support",
        "/api/v1/sharia/assets/btc/problem-reports": "passport_report",
        "/api/v1/integrations/telegram/send-test": "telegram_test",
        "/api/v1/whatsapp/link": "whatsapp_test",
        "/api/v1/whatsapp/test": "whatsapp_test",
        "/api/v1/public-chat/answers": "public_chat",
        "/api/v1/public-chat/answers/00000000-0000-0000-0000-000000000001/feedback": (
            "public_chat"
        ),
        "/api/v1/public-chat/ratings": "public_chat",
        "/api/v1/public-chat/inquiries": "public_inquiry",
        "/api/v1/public-forms/waitlist": "public_waitlist",
        "/api/v1/public-forms/contact": "public_contact",
        "/api/v1/admin/incidents": "admin_mutation",
        "/api/v1/sharia/admin/methodologies": "admin_mutation",
    }
    for path, scope in cases.items():
        rule = matching_rate_limit_rule("POST", path, settings)
        assert rule is not None, path
        assert rule.scope == scope


def test_deployed_cookie_mutation_requires_same_origin() -> None:
    settings = _settings(app_env="production")
    cookie = (b"cookie", b"amm_session=opaque-session")
    rejected = same_origin_failure(
        _request(method="POST", path="/api/v1/billing/checkout", headers=[cookie]),
        settings,
    )
    assert rejected is not None
    assert rejected.status_code == 403

    accepted = same_origin_failure(
        _request(
            method="POST",
            path="/api/v1/billing/checkout",
            headers=[cookie, (b"origin", b"https://app.hilal.example")],
        ),
        settings,
    )
    assert accepted is None


def test_signed_provider_callbacks_do_not_require_browser_origin() -> None:
    settings = _settings(app_env="production")
    request = _request(
        method="POST",
        path="/api/v1/telegram/webhook",
        headers=[(b"cookie", b"amm_session=opaque-session")],
    )
    assert same_origin_failure(request, settings) is None

    whatsapp = _request(
        method="POST",
        path="/api/v1/whatsapp/webhook",
        headers=[(b"cookie", b"amm_session=opaque-session")],
    )
    assert same_origin_failure(whatsapp, settings) is None


def test_deployed_rate_limit_identity_does_not_trust_user_header() -> None:
    settings = _settings(app_env="production")
    first = _request(
        method="POST",
        path="/api/v1/billing/checkout",
        headers=[(b"x-user-id", b"attacker-selected-one")],
    )
    second = _request(
        method="POST",
        path="/api/v1/billing/checkout",
        headers=[(b"x-user-id", b"attacker-selected-two")],
    )

    assert client_fingerprint(first, settings) == client_fingerprint(second, settings)
