from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.core.security import (
    InvalidContinuationToken,
    OnboardingAccessTokenService,
)
from ai_market_monitor.db.models import User
from ai_market_monitor.db.models.enums import UserRole, UserStatus
from ai_market_monitor.services.market_preview import MarketPreviewService
from ai_market_monitor.services.market_provider import market_data_provider
from ai_market_monitor.services.web_auth import SESSION_COOKIE_NAME, WebAuthService


@dataclass(frozen=True, slots=True)
class OnboardingPrincipal:
    user_id: UUID
    session_id: UUID


@dataclass(frozen=True, slots=True)
class AdminPrincipal:
    user_id: UUID
    role: UserRole


@dataclass(frozen=True, slots=True)
class UserPrincipal:
    user_id: UUID
    role: UserRole


def get_onboarding_principal(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> OnboardingPrincipal:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing onboarding bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = OnboardingAccessTokenService(settings).decode(token)
        return OnboardingPrincipal(
            user_id=UUID(payload["user_id"]), session_id=UUID(payload["session_id"])
        )
    except (InvalidContinuationToken, KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@lru_cache
def get_market_data_provider() -> Any:
    # Which provider, and whether its answers are shared, is decided in one place for the
    # whole product — the worker used to make this choice seven more times, by hand, and
    # without the fixture branch. See services/market_provider.py.
    return market_data_provider(get_settings())


def get_market_previewer(settings: Settings = Depends(get_settings)) -> MarketPreviewService:
    return MarketPreviewService(
        get_market_data_provider(),
        candle_limit=settings.preview_candle_limit,
        settings=settings,
    )


async def get_admin_principal(
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AdminPrincipal:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing admin principal")
    if settings.app_env not in {"development", "test"}:
        raise HTTPException(
            status_code=401,
            detail="Header principals are disabled outside development/test",
        )
    try:
        user_id = UUID(x_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid admin principal") from exc
    user = await session.get(User, user_id)
    if user is None or user.role not in {UserRole.ADMIN, UserRole.SUPPORT}:
        raise HTTPException(status_code=403, detail="Admin role required")
    return AdminPrincipal(user_id=user.id, role=user.role)


async def get_user_principal(
    request: Request,
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> UserPrincipal:
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token:
        user = await WebAuthService(session, settings).current_user(session_token)
        if user is not None:
            if user.status == UserStatus.SUSPENDED:
                raise HTTPException(status_code=403, detail="User account is suspended")
            return UserPrincipal(user_id=user.id, role=user.role)
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Authenticated user session required")
    if settings.app_env not in {"development", "test"}:
        raise HTTPException(
            status_code=401,
            detail="Header principals are disabled outside development/test",
        )
    try:
        user_id = UUID(x_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid user principal") from exc
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid user principal")
    if user.status == UserStatus.SUSPENDED:
        raise HTTPException(status_code=403, detail="User account is suspended")
    return UserPrincipal(user_id=user.id, role=user.role)


def require_admin(principal: AdminPrincipal = Depends(get_admin_principal)) -> AdminPrincipal:
    if principal.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Administrator role required")
    return principal
