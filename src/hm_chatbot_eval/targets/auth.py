from __future__ import annotations

import asyncio

import httpx

from ..config import Settings

_auth_lock = asyncio.Lock()
_session_cookies: dict[tuple[str, str], dict[str, str]] = {}


async def authenticated_session_cookies(settings: Settings) -> dict[str, str]:
    """Return one process-local authenticated cookie set for a test account."""

    configured_cookie = (
        settings.target_session_cookie.get_secret_value()
        if settings.target_session_cookie is not None
        else ""
    )
    if configured_cookie:
        return {settings.target_session_cookie_name: configured_cookie}
    email = settings.target_backend_email.strip().lower()
    if not email:
        return {}
    key = (settings.target_backend_base_url.rstrip("/"), email)
    cached = _session_cookies.get(key)
    if cached:
        return dict(cached)

    async with _auth_lock:
        cached = _session_cookies.get(key)
        if cached:
            return dict(cached)
        async with httpx.AsyncClient(
            base_url=settings.target_backend_base_url,
            timeout=settings.target_backend_timeout_seconds,
            follow_redirects=False,
        ) as client:
            response = await client.post(
                settings.target_backend_login_path,
                data={
                    "email": settings.target_backend_email,
                    "password": settings.target_backend_password,
                },
            )
            if response.status_code not in {302, 303, 307, 308}:
                response.raise_for_status()
            cookies = {
                cookie.name: cookie.value
                for cookie in client.cookies.jar
                if cookie.value is not None
            }
            if settings.target_session_cookie_name not in cookies:
                raise RuntimeError("Test-account sign-in returned no authenticated session cookie")
            _session_cookies[key] = cookies
            return dict(cookies)


def clear_authenticated_session_cache() -> None:
    """Clear process-local auth state between explicit evaluator test runs."""

    _session_cookies.clear()
