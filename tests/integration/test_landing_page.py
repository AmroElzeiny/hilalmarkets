from html import unescape


async def test_landing_page_contains_product_flow_without_performance_claims(test_context):
    response = await test_context["client"].get("/")
    assert response.status_code == 200
    content = response.text
    for required in (
        "See your setup forming",
        "Sign up",
        "TraceEdge",
        "Lifecycle Watchlist",
        "Explainable Condition Proof",
        "Why Wasn't I Alerted?",
        "Risk disclaimer",
    ):
        assert required in content
    lowered = content.lower()
    for forbidden in ("guaranteed profits", "guaranteed returns", "fake win rate"):
        assert forbidden not in lowered
    assert "Entry zone" not in content
    assert "Conditions complete" in content
    assert "Alert delivered" in content


async def test_landing_page_strips_at_sign_from_telegram_username(test_context):
    test_context["settings"].telegram_bot_username = "@simiautobybit_bot"
    response = await test_context["client"].get("/")
    assert response.status_code == 200
    content = unescape(response.text)
    assert "https://t.me/simiautobybit_bot?start=landing" in content
    assert "https://t.me/@simiautobybit_bot" not in content


async def test_landing_page_links_to_primary_start_paths(test_context):
    response = await test_context["client"].get("/")
    assert response.status_code == 200
    assert "Open dashboard preview" not in response.text
    assert "/signup" in response.text
    assert "Join the trace" in response.text
    assert 'href="/dashboard-entry"' in response.text
    assert ">Dashboard</a>" in response.text
    assert "Start on Discord" not in response.text
    assert "Start on Telegram" not in response.text
    assert "data-theme-toggle" in response.text
    assert "theme.js" in response.text
    assert "20260625-launch-polish" in response.text


async def test_dashboard_entry_uses_saved_session_or_signup(test_context):
    anonymous = await test_context["client"].get("/dashboard-entry", follow_redirects=False)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/signup"

    requested = await test_context["client"].post(
        "/signup",
        data={
            "email": "entry@example.com",
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert requested.headers["location"].startswith("/signup/verify")
    code = test_context["settings"].email_test_outbox[-1]["code"]
    verified = await test_context["client"].post(
        "/signup/verify",
        data={"email": "entry@example.com", "code": code},
        follow_redirects=False,
    )
    assert verified.headers["location"].startswith("/dashboard")
    authenticated = await test_context["client"].get(
        "/dashboard-entry",
        follow_redirects=False,
    )
    assert authenticated.status_code == 303
    assert authenticated.headers["location"] == "/dashboard"


async def test_dashboard_preview_pages_are_available(test_context):
    response = await test_context["client"].get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/signin")
