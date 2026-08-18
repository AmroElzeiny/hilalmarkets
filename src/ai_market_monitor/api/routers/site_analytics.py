"""Where the public site reports what it measured.

One address, one small body, no reply worth reading. The page fires this with
``navigator.sendBeacon`` while it is being closed, so the endpoint answers ``204`` and
never asks the browser to wait for anything.

Nothing here identifies anybody: the caller's address is turned into a one-way daily hash
inside ``services/site_analytics.py`` and is never stored.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.route_security import public_api
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.services.site_analytics import (
    SiteAnalyticsService,
    valid_session_key,
)

router = APIRouter(tags=["site-analytics"])


class SiteVisitBeacon(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: Literal["open", "ping", "close", "action"]
    # Bounded here only so an enormous body cannot be sent. What a *valid* key looks
    # like is `valid_session_key`, and it is the only place that rule lives: a second
    # length written into this schema would be a second answer to the same question, and
    # the two would disagree the first time either changed.
    session_key: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=400)
    referrer: str | None = Field(default=None, max_length=500)
    campaign: str | None = Field(default=None, max_length=120)
    active_ms: int = Field(default=0, ge=0, le=24 * 60 * 60 * 1000)
    action: Literal["page", "chat", "signup", "pricing"] | None = None
    action_detail: str | None = Field(default=None, max_length=200)


@router.post("/site-analytics/collect", include_in_schema=False, status_code=204)
@public_api(
    "Counts visits to the public site, where nobody is signed in — a stranger opening "
    "the front page is exactly what it measures. It reads nothing, returns no body, "
    "stores no address and identifies nobody: the caller becomes a one-way hash that "
    "changes every day. Rate limited under the `site_analytics` scope."
)
async def collect_site_visit(
    payload: SiteVisitBeacon,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    if not settings.site_visit_measurement_enabled or not valid_session_key(payload.session_key):
        # A rejected beacon is not an error the visitor should ever see. The page is
        # already closing; there is nothing it could do with a failure.
        return Response(status_code=204)
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    remote = (forwarded.split(",", 1)[0].strip() if forwarded else "") or (
        request.client.host if request.client else "unknown"
    )
    await SiteAnalyticsService(session, settings).record(
        event=payload.event,
        session_key=payload.session_key.lower(),
        path=payload.path,
        remote_address=remote,
        user_agent=request.headers.get("user-agent", ""),
        referrer=payload.referrer,
        campaign=payload.campaign,
        active_ms=payload.active_ms,
        action=payload.action,
        action_detail=payload.action_detail,
    )
    await session.commit()
    return Response(status_code=204)
