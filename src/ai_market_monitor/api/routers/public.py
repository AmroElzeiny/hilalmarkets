from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.services.web_auth import SESSION_COOKIE_NAME, WebAuthService

PACKAGE_DIR = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
router = APIRouter(tags=["public"])


def _public_context(settings: Settings) -> dict[str, str]:
    telegram_username = (
        settings.telegram_bot_username.lstrip("@").strip()
        if settings.telegram_bot_username
        else None
    )
    telegram_url = (
        f"https://t.me/{telegram_username}?start=landing" if telegram_username else "#start"
    )
    return {
        "telegram_url": telegram_url,
        "discord_url": "/signup?message=discord_start",
        "dashboard_entry_url": "/dashboard-entry",
    }


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing_page(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=_public_context(settings),
    )


@router.get("/dashboard-entry", include_in_schema=False)
async def dashboard_entry(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    user = await WebAuthService(session, settings).current_user(
        request.cookies.get(SESSION_COOKIE_NAME)
    )
    return RedirectResponse("/dashboard" if user is not None else "/signup", status_code=303)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
