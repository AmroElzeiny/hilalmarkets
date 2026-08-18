"""The four things the Hilal chat window asks the server for.

``GET /status`` is the odd one and worth explaining: the window polls it every second
while it is open, because the allowance has to lock the box the moment it runs out and
unlock it the moment the day turns over — without the person reloading anything. It is
kept to one indexed counter row and one plan lookup for that reason, and the browser
stops asking the moment the tab is hidden or the window is closed.

Every write carries the dashboard's own form token. Everything is scoped to the person
who is signed in: there is no route here that can name somebody else's conversation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import UserPrincipal
from ai_market_monitor.api.routers.dashboard_api import get_dashboard_principal
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.csrf import csrf_token_matches
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.db.models import User
from ai_market_monitor.schemas.hilal_chat import (
    HilalChatAsk,
    HilalChatRatingInput,
    HilalChatReport,
)
from ai_market_monitor.services.hilal_chat import HilalChatError, HilalChatService

router = APIRouter(prefix="/dashboard/hilal", tags=["hilal-chat"])


async def _current_user(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    user = await session.get(User, principal.user_id)
    if user is None:  # pragma: no cover - the principal was just resolved from this row
        raise HTTPException(status_code=401, detail="Dashboard session required")
    return user


def _check_form_token(
    settings: Settings, user: User, token: str | None
) -> None:
    if not csrf_token_matches(settings, user.id, token):
        raise HTTPException(status_code=403, detail="Invalid form token.")


def _as_http(error: HilalChatError) -> HTTPException:
    """Turn a refusal into a response that still says something useful.

    429 for "you have used today's allowance" and 503 for "Hilal itself is not
    available", because those are different things and a browser that treats them the
    same would offer an upgrade for a provider outage.
    """

    status = {
        "USER_DAILY_BUDGET_EXCEEDED": 429,
        "USER_MONTHLY_BUDGET_EXCEEDED": 429,
        "PLAN_MESSAGE_QUOTA_EXCEEDED": 429,
        "GLOBAL_DAILY_BUDGET_EXCEEDED": 503,
        "GLOBAL_MONTHLY_BUDGET_EXCEEDED": 503,
        "MODEL_DAILY_BUDGET_EXCEEDED": 503,
        "TOO_MANY_IN_FLIGHT": 429,
        "AI_FEATURE_DISABLED": 503,
        "HILAL_DISABLED": 503,
        "HILAL_UNAVAILABLE": 503,
        "MODEL_PRICE_UNKNOWN": 503,
        "UNKNOWN_MESSAGE": 404,
    }.get(error.code, 400)
    return HTTPException(
        status_code=status,
        detail={"code": error.code, "message": error.message},
    )


@router.get("/status")
async def hilal_status(
    user: User = Depends(_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """This person's own allowance, and whether the box may be used."""

    return await HilalChatService(session, settings).status_for(user.id)


@router.get("/history")
async def hilal_history(
    user: User = Depends(_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Everything said so far, across every session this person has had."""

    service = HilalChatService(session, settings)
    messages = await service.history_for(user.id)
    status = await service.status_for(user.id)
    await session.commit()
    return {"messages": messages, "status": status}


@router.post("/message")
async def hilal_message(
    request: Request,
    ask: HilalChatAsk,
    x_csrf_token: str | None = Header(default=None),
    user: User = Depends(_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _check_form_token(settings, user, x_csrf_token)
    service = HilalChatService(session, settings)
    try:
        turn = await service.ask(user=user, ask=ask)
    except HilalChatError as error:
        # The question and any refusal that was already written down are kept. Rolling
        # back would lose the person's own message from the transcript they can see.
        status = await service.status_for(user.id)
        await session.commit()
        problem = _as_http(error)
        if isinstance(problem.detail, dict):
            problem.detail["status"] = status
        raise problem from error
    await session.commit()
    return {
        "message_id": turn.message_id,
        "reply": turn.reply.reply,
        "mode": turn.reply.mode,
        "language": turn.reply.language,
        "suggestions": turn.reply.suggestions,
        "status": turn.status,
    }


@router.post("/report")
async def hilal_report(
    report: HilalChatReport,
    x_csrf_token: str | None = Header(default=None),
    user: User = Depends(_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    _check_form_token(settings, user, x_csrf_token)
    try:
        await HilalChatService(session, settings).report(user=user, report=report)
    except HilalChatError as error:
        raise _as_http(error) from error
    await session.commit()
    return {"status": "received"}


@router.post("/rating")
async def hilal_rating(
    rating: HilalChatRatingInput,
    x_csrf_token: str | None = Header(default=None),
    user: User = Depends(_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    _check_form_token(settings, user, x_csrf_token)
    await HilalChatService(session, settings).rate(user=user, rating=rating)
    await session.commit()
    return {"status": "thank you"}
