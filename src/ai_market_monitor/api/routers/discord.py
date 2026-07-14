import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.cockpit_service import StrategyCockpitService
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.core.security import token_digest
from ai_market_monitor.db.models import (
    Alert,
    AuditEvent,
    DashboardPreference,
    DiscordConnection,
    DiscordOAuthState,
    Strategy,
    StrategyVersion,
)
from ai_market_monitor.discord.http_gateway import (
    DiscordHttpError,
    DiscordOAuthClient,
)
from ai_market_monitor.discord.service import (
    DiscordConnectionService,
    DiscordError,
    DiscordModerationService,
    DiscordSlashCommandService,
    DiscordSupportService,
)
from ai_market_monitor.discord.types import DiscordCommandContext, DiscordPermissionSet

router = APIRouter(prefix="/discord", tags=["discord"])


class OAuthStateRequest(BaseModel):
    user_id: UUID
    redirect_url: str = Field(min_length=1, max_length=1000)
    scopes: list[str] = Field(default_factory=lambda: ["identify", "email", "guilds"])
    metadata: dict = Field(default_factory=dict)


class OAuthCompleteRequest(BaseModel):
    state: str = Field(min_length=20, max_length=500)
    code: str = Field(min_length=5, max_length=2000)


def get_discord_oauth_client(
    settings: Settings = Depends(get_settings),
) -> DiscordOAuthClient:
    return DiscordOAuthClient(settings)


@router.post("/interactions")
async def discord_interactions(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    signature: str | None = Header(default=None, alias="X-Signature-Ed25519"),
    timestamp: str | None = Header(default=None, alias="X-Signature-Timestamp"),
) -> dict:
    body = await request.body()
    _verify_discord_signature(settings, body, signature, timestamp)
    try:
        interaction = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid Discord interaction") from exc
    if interaction.get("type") == 1:
        return {"type": 1}
    if interaction.get("type") not in {2, 3}:
        return {
            "type": 4,
            "data": {"content": "Unsupported interaction.", "flags": 64},
        }
    member = interaction.get("member") or {}
    discord_user = member.get("user") or interaction.get("user") or {}
    discord_user_id = str(discord_user.get("id") or "")
    connection = await session.scalar(
        select(DiscordConnection).where(DiscordConnection.discord_user_id == discord_user_id)
    )
    if connection is None:
        return {
            "type": 4,
            "data": {
                "content": "Connect Discord from your HilalMarkets account first.",
                "flags": 64,
            },
        }
    if interaction.get("type") == 3:
        response_text = await _handle_component_interaction(
            session,
            connection,
            str((interaction.get("data") or {}).get("custom_id") or ""),
        )
        await session.commit()
        return {
            "type": 4,
            "data": {"content": response_text, "flags": 64},
        }
    data = interaction.get("data") or {}
    options = {str(option.get("name")): option.get("value") for option in data.get("options") or []}
    response = await DiscordSlashCommandService(session, settings).handle(
        DiscordCommandContext(
            command_name=str(data.get("name") or ""),
            user_id=connection.user_id,
            discord_user_id=discord_user_id,
            guild_id=str(interaction.get("guild_id")) if interaction.get("guild_id") else None,
            channel_id=str(interaction.get("channel_id"))
            if interaction.get("channel_id")
            else None,
            options=options,
        )
    )
    await session.commit()
    return {
        "type": 4,
        "data": {
            "content": response.content,
            "flags": 64 if response.ephemeral else 0,
        },
    }


async def _handle_component_interaction(
    session: AsyncSession,
    connection: DiscordConnection,
    custom_id: str,
) -> str:
    if custom_id.startswith("feedback:"):
        parts = custom_id.split(":", 2)
        if len(parts) != 3:
            return "This feedback action has expired."
        try:
            alert_id = UUID(parts[2])
        except ValueError:
            return "This feedback action is invalid."
        alert = await session.get(Alert, alert_id)
        if alert is None or alert.user_id != connection.user_id:
            return "This alert is unavailable for your account."
        feedback = await StrategyCockpitService(session).submit_feedback(
            user_id=connection.user_id,
            alert=alert,
            feedback_type=parts[1],
            source="discord",
        )
        session.add(
            AuditEvent(
                actor_user_id=connection.user_id,
                actor_type="discord_user",
                action="alert.feedback_submitted",
                target_type="alert",
                target_id=str(alert.id),
                metadata_redacted={
                    "feedback_id": str(feedback.id),
                    "feedback_type": parts[1],
                },
                created_at=datetime.now(UTC),
            )
        )
        return "Feedback recorded. No monitor rule was changed."
    if custom_id.startswith("mute_strategy:"):
        try:
            version_id = UUID(custom_id.partition(":")[2])
        except ValueError:
            return "This mute action has expired."
        version = await session.get(StrategyVersion, version_id)
        strategy = await session.get(Strategy, version.strategy_id) if version else None
        if strategy is None or strategy.user_id != connection.user_id:
            return "This monitor is unavailable for your account."
        preference = await session.scalar(
            select(DashboardPreference).where(DashboardPreference.user_id == connection.user_id)
        )
        if preference is None:
            preference = DashboardPreference(
                user_id=connection.user_id,
                theme="dark",
                default_timezone="UTC",
            )
            session.add(preference)
        values = dict(preference.notification_preferences or {})
        muted_until = dict(values.get("muted_strategy_until", {}) or {})
        muted_until[str(version_id)] = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
        values["muted_strategy_until"] = muted_until
        preference.notification_preferences = values
        return (
            f"{strategy.name} notifications are muted for 24 hours. Stored evidence is unchanged."
        )
    return "This alert action is no longer available."


def _verify_discord_signature(
    settings: Settings,
    body: bytes,
    signature: str | None,
    timestamp: str | None,
) -> None:
    public_key = settings.discord_webhook_public_key
    if public_key is None or not signature or not timestamp:
        raise HTTPException(status_code=401, detail="Invalid Discord signature")
    try:
        verifier = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key.get_secret_value()))
        verifier.verify(bytes.fromhex(signature), timestamp.encode("ascii") + body)
    except (ValueError, InvalidSignature) as exc:
        raise HTTPException(status_code=401, detail="Invalid Discord signature") from exc


class DestinationRequest(BaseModel):
    user_id: UUID
    mode: str
    permissions: DiscordPermissionSet
    discord_user_id: str | None = None
    guild_id: str | None = None
    channel_id: str | None = None
    thread_policy: str = "per_setup"


class SupportTicketRequest(BaseModel):
    user_id: UUID
    category: str
    description: str = Field(min_length=1, max_length=4000)
    strategy_id: UUID | None = None
    setup_instance_id: UUID | None = None
    alert_id: UUID | None = None


class ModerationRequest(BaseModel):
    content: str
    attachment_names: list[str] = Field(default_factory=list)


@router.post("/oauth/state")
async def create_oauth_state(
    request: OAuthStateRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    state = await DiscordConnectionService(session, settings).generate_oauth_state(
        user_id=request.user_id,
        redirect_url=request.redirect_url,
        scopes=request.scopes,
        metadata=request.metadata,
    )
    await session.commit()
    return {"state": state}


@router.post("/oauth/complete")
async def complete_oauth(
    request: OAuthCompleteRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    oauth_client: DiscordOAuthClient = Depends(get_discord_oauth_client),
) -> dict:
    try:
        oauth_state = await session.scalar(
            select(DiscordOAuthState).where(
                DiscordOAuthState.state_digest == token_digest(request.state),
                DiscordOAuthState.consumed_at.is_(None),
                DiscordOAuthState.expires_at > datetime.now(UTC),
            )
        )
        if oauth_state is None:
            raise DiscordError("invalid_oauth_state", "Discord connection link expired.")
        profile = await oauth_client.exchange(
            code=request.code,
            redirect_url=oauth_state.redirect_url,
        )
        connection = await DiscordConnectionService(session, settings).complete_oauth(
            state=request.state,
            profile=profile,
        )
        await session.commit()
        return {
            "connection_id": connection.id,
            "user_id": connection.user_id,
            "discord_user_id": connection.discord_user_id,
            "status": connection.status,
        }
    except (DiscordError, DiscordHttpError) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "message": str(exc)}
        ) from exc


@router.post("/destinations")
async def select_destination(
    request: DestinationRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        destination = await DiscordConnectionService(session, settings).select_destination(
            user_id=request.user_id,
            mode=request.mode,
            permissions=request.permissions,
            discord_user_id=request.discord_user_id,
            guild_id=request.guild_id,
            channel_id=request.channel_id,
            thread_policy=request.thread_policy,
        )
        await session.commit()
        return {
            "destination_id": destination.id,
            "mode": destination.mode,
            "permissions_status": destination.permissions_status,
            "test_status": destination.test_status,
        }
    except DiscordError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "message": str(exc)}
        ) from exc


@router.post("/support")
async def create_support_ticket(
    request: SupportTicketRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    ticket = await DiscordSupportService(session).create_ticket(
        user_id=request.user_id,
        category=request.category,
        description=request.description,
        strategy_id=request.strategy_id,
        setup_instance_id=request.setup_instance_id,
        alert_id=request.alert_id,
    )
    await session.commit()
    return {"support_request_id": ticket.id, "status": ticket.status}


@router.post("/moderation/check")
async def check_moderation(request: ModerationRequest) -> dict:
    result = DiscordModerationService().assess(
        content=request.content,
        attachment_names=request.attachment_names,
    )
    return {
        "allowed": result.allowed,
        "requires_human_review": result.requires_human_review,
        "findings": [finding.__dict__ for finding in result.findings],
        "official_support_notice": DiscordModerationService.official_support_notice(),
    }
