"""Hilal, driven through the real application.

What only a running app can answer: that the widget reaches the pages it is meant to
and no others, that the transcript really survives a session, that the daily allowance
is enforced by the server rather than by the browser, and that a report and a rating
land where somebody can read them.

The model itself is replaced with a stand-in. Every check here is about the machinery
around the model — grounding, storage, limits, refusals — and paying a provider to
confirm that a row was written would be spending money to learn nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from ai_market_monitor.core.dashboard_paths import MONITOR_PATH
from ai_market_monitor.db.models import (
    AIBudgetCounter,
    HilalChatConversation,
    HilalChatMessage,
    HilalChatMessageReport,
    HilalChatRating,
    User,
)
from ai_market_monitor.services.ai_budget import day_window
from ai_market_monitor.services.hilal_chat import HILAL_FEATURE
from tests.integration.test_dashboard_web import _signup_and_verify

CHAT = "/api/v1/dashboard/hilal"


async def _signed_in(test_context, email: str) -> User:
    await _signup_and_verify(test_context, email=email)
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User))
        assert user is not None
        return user


def _headers(test_context) -> dict[str, str]:
    """The dashboard's own form token, read from a page the way the browser reads it."""
    return {"X-CSRF-Token": test_context["client"].cookies.get("hm_csrf") or ""}


async def _csrf(test_context) -> str:
    page = await test_context["client"].get("/dashboard/market")
    assert page.status_code == 200
    marker = 'data-csrf-token="'
    start = page.text.index(marker) + len(marker)
    return page.text[start : page.text.index('"', start)]


# --------------------------------------------------------------------------------
# Where it appears, and where it must not.
# --------------------------------------------------------------------------------


#: Every page on the design path. The Passport and the report are reached *from* the
#: market page, so a person who followed a coin there and then wanted to ask about it
#: is exactly who Hilal is for.
#: `/dashboard/monitor` used to be here. It is the canvas's *older* address and has been
#: a permanent redirect for a while, so this was asking a redirect whether it carried the
#: assistant — which it never does, because it carries no page at all.
DESIGN_PATH = (
    "/dashboard/market",
    MONITOR_PATH,
    "/dashboard/market/btc",
    "/dashboard/market/btc/report",
)


async def test_hilal_is_on_every_dashboard_test_page(test_context):
    await _signed_in(test_context, email="hilal-where@example.com")
    client = test_context["client"]
    seen = 0
    for path in DESIGN_PATH:
        page = await client.get(path)
        if page.status_code == 404:
            # The Passport pages need a screened coin, which this test does not seed.
            continue
        assert page.status_code == 200, page.text[:400]
        assert 'class="hm-hilal"' in page.text, f"Hilal is missing from {path}"
        assert "hm-hilal-chat.js" in page.text, f"Hilal's script is missing from {path}"
        # Its two dialogs are styled by the shared design-path sheet. Loaded by each
        # page rather than by the widget, so a page that forgot it would show Hilal
        # with an unstyled report box — and nothing else would notice.
        assert "hm-dashboard-test.css" in page.text, (
            f"{path} carries Hilal but not the stylesheet its dialogs need"
        )
        seen += 1
    assert seen >= 2, "no design-path page was actually checked"


async def test_hilal_is_on_no_other_dashboard_page(test_context):
    """It belongs to the redesigned pages, and to nothing else (rule A2).

    The pages named here are the ones that were *not* redesigned: the builder, the
    checkout side of billing, and Evidence and Activity. They are still served, they
    still use the older design, and the assistant is not on any of them.

    Looked for by the widget's own class and script rather than by "data-hilal", which
    is a prefix the shared sidebar already uses for something else entirely.
    """
    await _signed_in(test_context, email="hilal-nowhere@example.com")
    client = test_context["client"]
    for path in ("/dashboard", "/dashboard/strategies", "/dashboard/billing"):
        page = await client.get(path, follow_redirects=True)
        if page.status_code != 200:
            continue
        assert 'class="hm-hilal"' not in page.text, f"Hilal leaked onto {path}"
        assert "hm-hilal-chat.js" not in page.text, f"Hilal's script leaked onto {path}"


async def test_the_canvas_still_carries_no_assistant_inside_it(test_context):
    """`dashboard-test-monitor-rules.md` A3/A4: nothing in the canvas itself."""
    await _signed_in(test_context, email="hilal-canvas@example.com")
    page = await test_context["client"].get(MONITOR_PATH)
    assert page.status_code == 200
    board = page.text[page.text.index("m-board") : page.text.index("m-readout")]
    assert "hilal" not in board.lower()
    assert "Hilal Markets Assistant" not in page.text


# --------------------------------------------------------------------------------
# The allowance, enforced on the server.
# --------------------------------------------------------------------------------


async def test_the_status_says_what_a_free_person_may_spend(test_context):
    await _signed_in(test_context, email="hilal-status@example.com")
    response = await test_context["client"].get(f"{CHAT}/status")
    assert response.status_code == 200, response.text[:400]
    body = response.json()
    assert float(body["allowance_usd"]) == pytest.approx(0.10)
    assert body["can_send"] is True
    assert body["paying"] is False


async def test_the_cycle_resets_at_midnight_utc(test_context):
    """Rule E2. The reset instant the browser counts down to has to be UTC midnight."""
    await _signed_in(test_context, email="hilal-reset@example.com")
    body = (await test_context["client"].get(f"{CHAT}/status")).json()
    resets = datetime.fromisoformat(body["resets_at"])
    assert resets.tzinfo is not None
    at_utc = resets.astimezone(UTC)
    assert (at_utc.hour, at_utc.minute, at_utc.second) == (0, 0, 0)
    assert at_utc > datetime.now(UTC)


async def test_a_spent_allowance_locks_the_box_and_says_why(test_context):
    """Rule E4/E7. Written straight into the counter the server itself reads."""
    user = await _signed_in(test_context, email="hilal-spent@example.com")
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        session.add(
            AIBudgetCounter(
                scope="user_feature_daily",
                scope_key=f"{HILAL_FEATURE}:{user.id}",
                window_key=day_window(now),
                spent_usd=Decimal("0.10"),
                reserved_usd=Decimal("0"),
                reserved_count=0,
                updated_at=now,
            )
        )
        await session.commit()

    body = (await test_context["client"].get(f"{CHAT}/status")).json()
    assert body["can_send"] is False
    assert body["locked_reason"]
    assert body["offer_upgrade"] is True, "a free person is not offered the upgrade"

    # And the server refuses, whatever the browser thinks.
    token = await _csrf(test_context)
    refused = await test_context["client"].post(
        f"{CHAT}/message",
        json={"message": "Is BTC eligible?"},
        headers={"X-CSRF-Token": token},
    )
    assert refused.status_code == 429, refused.text[:400]


async def test_hilal_spends_from_its_own_window_not_the_shared_one(test_context):
    """Rule E3. One authority, but its own window.

    Held against the shared per-person daily figure, whichever assistant ran first
    would spend the other's allowance, and the promise of "this much a day with Hilal"
    would be true only for whoever went first.
    """
    user = await _signed_in(test_context, email="hilal-window@example.com")
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        session.add(
            AIBudgetCounter(
                scope="user_daily",
                scope_key=str(user.id),
                window_key=day_window(now),
                spent_usd=Decimal("4.99"),
                reserved_usd=Decimal("0"),
                reserved_count=0,
                updated_at=now,
            )
        )
        await session.commit()

    body = (await test_context["client"].get(f"{CHAT}/status")).json()
    assert body["can_send"] is True, "another feature's spending closed Hilal"
    assert float(body["remaining_usd"]) == pytest.approx(0.10)


# --------------------------------------------------------------------------------
# The refusals, through the real route.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "should I buy bitcoin now",
        "build me a strategy for ETH",
        "what rsi settings should i use",
        "will BTC go up this week",
    ],
)
async def test_a_forbidden_question_is_refused_without_spending_anything(
    test_context, question: str
):
    """The refusal is free: no provider call, so no money and no allowance used."""
    user = await _signed_in(test_context, email=f"hilal-refuse-{uuid4().hex[:8]}@example.com")
    token = await _csrf(test_context)
    response = await test_context["client"].post(
        f"{CHAT}/message",
        json={"message": question},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.text[:400]
    body = response.json()
    assert body["mode"] == "REFUSAL"
    assert body["suggestions"], "the refusal offered nothing instead"
    assert float(body["status"]["used_usd"]) == 0.0

    async with test_context["session_factory"]() as session:
        counter = await session.get(
            AIBudgetCounter,
            ("user_feature_daily", f"{HILAL_FEATURE}:{user.id}", day_window(datetime.now(UTC))),
        )
        assert counter is None, "a refusal reserved budget it never needed"


async def test_a_refusal_is_kept_in_the_transcript(test_context):
    await _signed_in(test_context, email="hilal-refusal-history@example.com")
    token = await _csrf(test_context)
    await test_context["client"].post(
        f"{CHAT}/message",
        json={"message": "should I buy bitcoin"},
        headers={"X-CSRF-Token": token},
    )
    history = (await test_context["client"].get(f"{CHAT}/history")).json()
    roles = [item["role"] for item in history["messages"]]
    assert roles == ["user", "assistant"]
    assert history["messages"][1]["mode"] == "REFUSAL"


# --------------------------------------------------------------------------------
# History that outlives a session.
# --------------------------------------------------------------------------------


async def test_the_conversation_survives_signing_out_and_back_in(test_context):
    """Rule D1. The whole point of storing it on the server."""
    email = "hilal-memory@example.com"
    await _signed_in(test_context, email=email)
    client = test_context["client"]
    token = await _csrf(test_context)
    await client.post(
        f"{CHAT}/message",
        json={"message": "should I sell my ETH"},
        headers={"X-CSRF-Token": token},
    )

    before = (await client.get(f"{CHAT}/history")).json()["messages"]
    assert len(before) == 2

    # A new browser entirely: every cookie gone, then signing back in.
    client.cookies.clear()
    assert (await client.get(f"{CHAT}/history")).status_code == 401
    signed_in = await client.post(
        "/signin",
        data={"email": email, "password": "CorrectHorse123!"},
        follow_redirects=False,
    )
    assert signed_in.status_code == 303, signed_in.text[:300]
    assert "error" not in signed_in.headers["location"], signed_in.headers["location"]

    after = (await client.get(f"{CHAT}/history")).json()["messages"]
    assert [item["text"] for item in after] == [item["text"] for item in before]


async def test_one_person_has_exactly_one_conversation(test_context):
    await _signed_in(test_context, email="hilal-one@example.com")
    client = test_context["client"]
    for _ in range(3):
        await client.get(f"{CHAT}/history")
    async with test_context["session_factory"]() as session:
        rows = (await session.execute(select(HilalChatConversation))).scalars().all()
        assert len(rows) == 1


async def test_the_same_message_sent_twice_is_answered_once(test_context):
    """A dropped connection makes people press send again. It must not cost twice."""
    await _signed_in(test_context, email="hilal-replay@example.com")
    token = await _csrf(test_context)
    payload = {"message": "should I buy SOL", "client_message_id": "same-one"}
    first = await test_context["client"].post(
        f"{CHAT}/message", json=payload, headers={"X-CSRF-Token": token}
    )
    second = await test_context["client"].post(
        f"{CHAT}/message", json=payload, headers={"X-CSRF-Token": token}
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["message_id"] == second.json()["message_id"]

    async with test_context["session_factory"]() as session:
        rows = (await session.execute(select(HilalChatMessage))).scalars().all()
        assert len(rows) == 2, "the question was recorded twice"


async def test_resending_a_question_that_never_got_an_answer_works(test_context):
    """The retry that actually happens is the one after a failure.

    A question is written down before the answer is attempted. When that attempt failed,
    the question stayed in the table — and sending it again broke the unique constraint
    on it, turning a failure the person could have retried into a server error.
    """
    user = await _signed_in(test_context, email="hilal-retry-failed@example.com")
    conversation_id = None
    async with test_context["session_factory"]() as session:
        conversation = HilalChatConversation(
            user_id=user.id, message_count=1, next_sequence=2
        )
        session.add(conversation)
        await session.flush()
        conversation_id = conversation.id
        now = datetime.now(UTC)
        session.add(
            HilalChatMessage(
                conversation_id=conversation.id,
                sequence=1,
                role="user",
                content="should I buy bitcoin",
                mode="ASK",
                client_message_id="the-one-that-failed",
                suggestions=[],
                created_at=now,
                retain_until=now + timedelta(days=30),
            )
        )
        await session.commit()

    token = await _csrf(test_context)
    response = await test_context["client"].post(
        f"{CHAT}/message",
        json={
            "message": "should I buy bitcoin",
            "client_message_id": "the-one-that-failed",
        },
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.text[:400]
    assert response.json()["mode"] == "REFUSAL"

    async with test_context["session_factory"]() as session:
        rows = (
            (
                await session.execute(
                    select(HilalChatMessage).where(
                        HilalChatMessage.conversation_id == conversation_id
                    )
                )
            )
            .scalars()
            .all()
        )
        asked = [row for row in rows if row.role == "user"]
        assert len(asked) == 1, "the question was written down twice"
        assert any(row.role == "assistant" for row in rows), "it was never answered"


# --------------------------------------------------------------------------------
# Reporting and rating.
# --------------------------------------------------------------------------------


async def test_an_answer_can_be_reported_and_the_words_are_kept(test_context):
    await _signed_in(test_context, email="hilal-report@example.com")
    client = test_context["client"]
    token = await _csrf(test_context)
    answered = await client.post(
        f"{CHAT}/message",
        json={"message": "should I buy bitcoin"},
        headers={"X-CSRF-Token": token},
    )
    message_id = answered.json()["message_id"]

    response = await client.post(
        f"{CHAT}/report",
        json={"message_id": message_id, "reason": "wrong", "note": "It misread me."},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.text[:400]

    async with test_context["session_factory"]() as session:
        report = await session.scalar(select(HilalChatMessageReport))
        assert report is not None
        assert report.reason == "wrong"
        # Copied, not referenced: a retention sweep must not empty the report.
        assert report.reported_content
        assert report.note == "It misread me."


async def test_reporting_the_same_answer_twice_is_still_one_report(test_context):
    await _signed_in(test_context, email="hilal-report-twice@example.com")
    client = test_context["client"]
    token = await _csrf(test_context)
    answered = await client.post(
        f"{CHAT}/message",
        json={"message": "should I buy bitcoin"},
        headers={"X-CSRF-Token": token},
    )
    body = {"message_id": answered.json()["message_id"], "reason": "confusing"}
    for _ in range(2):
        assert (
            await client.post(f"{CHAT}/report", json=body, headers={"X-CSRF-Token": token})
        ).status_code == 200

    async with test_context["session_factory"]() as session:
        rows = (await session.execute(select(HilalChatMessageReport))).scalars().all()
        assert len(rows) == 1


async def test_somebody_elses_answer_cannot_be_reported(test_context):
    await _signed_in(test_context, email="hilal-report-other@example.com")
    token = await _csrf(test_context)
    response = await test_context["client"].post(
        f"{CHAT}/report",
        json={"message_id": str(uuid4()), "reason": "wrong"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 404


async def test_a_rating_is_kept_with_how_long_the_conversation_was(test_context):
    await _signed_in(test_context, email="hilal-rating@example.com")
    client = test_context["client"]
    token = await _csrf(test_context)
    await client.post(
        f"{CHAT}/message",
        json={"message": "should I buy bitcoin"},
        headers={"X-CSRF-Token": token},
    )
    response = await client.post(
        f"{CHAT}/rating",
        json={"stars": 4, "comment": "Clear, thank you."},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200

    async with test_context["session_factory"]() as session:
        rating = await session.scalar(select(HilalChatRating))
        assert rating is not None
        assert rating.stars == 4
        assert rating.comment == "Clear, thank you."
        assert rating.message_count == 2


@pytest.mark.parametrize("stars", [0, 6, -1])
async def test_a_rating_outside_one_to_five_is_refused(test_context, stars: int):
    await _signed_in(test_context, email=f"hilal-stars-{abs(stars)}@example.com")
    token = await _csrf(test_context)
    response = await test_context["client"].post(
        f"{CHAT}/rating", json={"stars": stars}, headers={"X-CSRF-Token": token}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------------
# Nobody else's conversation, and nothing without a form token.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/message", {"message": "hello"}),
        ("/report", {"message_id": "x", "reason": "wrong"}),
        ("/rating", {"stars": 5}),
    ],
)
async def test_every_write_needs_the_dashboard_form_token(test_context, path, body):
    await _signed_in(test_context, email=f"hilal-csrf-{path.strip('/')}@example.com")
    response = await test_context["client"].post(f"{CHAT}{path}", json=body)
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/status", "/history"])
async def test_a_stranger_gets_nothing(test_context, path: str):
    test_context["client"].cookies.clear()
    response = await test_context["client"].get(f"{CHAT}{path}")
    assert response.status_code == 401


async def test_a_message_longer_than_the_limit_is_refused_kindly(test_context):
    await _signed_in(test_context, email="hilal-long@example.com")
    token = await _csrf(test_context)
    response = await test_context["client"].post(
        f"{CHAT}/message",
        json={"message": "a" * 900},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 400
    assert "one thing at a time" in response.json()["detail"]["message"]


async def test_stored_messages_carry_a_retention_boundary(test_context):
    """Rule D4. Every stored conversation on this platform has one."""
    await _signed_in(test_context, email="hilal-retention@example.com")
    token = await _csrf(test_context)
    await test_context["client"].post(
        f"{CHAT}/message",
        json={"message": "should I buy bitcoin"},
        headers={"X-CSRF-Token": token},
    )
    async with test_context["session_factory"]() as session:
        rows = (await session.execute(select(HilalChatMessage))).scalars().all()
        assert rows
        for row in rows:
            assert row.retain_until > row.created_at + timedelta(days=1)
