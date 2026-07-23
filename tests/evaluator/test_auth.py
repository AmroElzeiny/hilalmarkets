import httpx
from pydantic import SecretStr

from hm_chatbot_eval.config import Settings
from hm_chatbot_eval.targets import auth


async def test_test_account_authentication_is_reused_without_repeated_signin(monkeypatch):
    real_client = httpx.AsyncClient
    login_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_calls
        login_calls += 1
        assert request.url.path == "/signin"
        return httpx.Response(
            303,
            headers={
                "location": "/dashboard",
                "set-cookie": "amm_session=test-session; Path=/; HttpOnly",
            },
        )

    def client_factory(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(auth.httpx, "AsyncClient", client_factory)
    auth.clear_authenticated_session_cache()
    settings = Settings(
        _env_file=None,
        target_backend_base_url="http://testserver",
        target_backend_email="evaluator@example.com",
        target_backend_password="not-a-production-secret",
    )

    first = await auth.authenticated_session_cookies(settings)
    second = await auth.authenticated_session_cookies(settings)

    assert first == {"amm_session": "test-session"}
    assert second == first
    assert login_calls == 1


async def test_pre_authenticated_session_does_not_call_signin(monkeypatch):
    def unexpected_client(**_):
        raise AssertionError("A configured test session must not call the sign-in route")

    monkeypatch.setattr(auth.httpx, "AsyncClient", unexpected_client)
    settings = Settings(
        _env_file=None,
        target_session_cookie=SecretStr("short-lived-test-session"),
    )
    assert await auth.authenticated_session_cookies(settings) == {
        "amm_session": "short-lived-test-session"
    }
