from typing import Any

import httpx

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import DiscordDeliveryDestination
from ai_market_monitor.discord.types import (
    DiscordEmbed,
    DiscordOAuthProfile,
    DiscordSendResult,
)


class DiscordHttpError(RuntimeError):
    def __init__(self, message: str, code: str = "discord_http_error"):
        super().__init__(message)
        self.code = code


class DiscordHttpGateway:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        token = settings.discord_bot_token
        if token is None:
            raise DiscordHttpError("Discord bot token is not configured")
        self.authorization = f"Bot {token.get_secret_value()}"
        self.transport = transport

    async def send_test(self, *, destination: DiscordDeliveryDestination) -> DiscordSendResult:
        channel_id = await self._channel_id(destination)
        result = await self._request(
            "POST",
            f"/channels/{channel_id}/messages",
            json={"content": "TraceEdge test notification. Permissions are working."},
        )
        return DiscordSendResult(provider_message_id=str(result["id"]))

    async def send_embed(
        self,
        *,
        destination: DiscordDeliveryDestination,
        embed: DiscordEmbed,
        thread_id: str | None = None,
    ) -> DiscordSendResult:
        channel_id = thread_id or await self._channel_id(destination)
        payload: dict[str, Any] = {
            "embeds": [
                {
                    "title": embed.title,
                    "description": embed.description,
                    "fields": [
                        {
                            "name": field.name,
                            "value": field.value,
                            "inline": field.inline,
                        }
                        for field in embed.fields
                    ],
                    **({"image": {"url": embed.image_url}} if embed.image_url else {}),
                    **({"footer": {"text": embed.footer}} if embed.footer else {}),
                    **({"timestamp": embed.timestamp.isoformat()} if embed.timestamp else {}),
                }
            ],
            "components": self._components(embed),
        }
        result = await self._request(
            "POST",
            f"/channels/{channel_id}/messages",
            json=payload,
        )
        return DiscordSendResult(
            provider_message_id=str(result["id"]),
            thread_id=thread_id,
        )

    async def create_thread(
        self,
        *,
        destination: DiscordDeliveryDestination,
        name: str,
        first_message_id: str | None = None,
    ) -> str:
        channel_id = await self._channel_id(destination)
        if first_message_id:
            path = f"/channels/{channel_id}/messages/{first_message_id}/threads"
            payload = {"name": name[:100], "auto_archive_duration": 1440}
        else:
            path = f"/channels/{channel_id}/threads"
            payload = {
                "name": name[:100],
                "type": 11,
                "auto_archive_duration": 1440,
            }
        result = await self._request("POST", path, json=payload)
        return str(result["id"])

    async def sync_role(
        self,
        *,
        discord_user_id: str,
        guild_id: str,
        role_id: str,
        action: str,
    ) -> None:
        method = "PUT" if action == "add" else "DELETE"
        await self._request(
            method,
            f"/guilds/{guild_id}/members/{discord_user_id}/roles/{role_id}",
        )

    async def _channel_id(self, destination: DiscordDeliveryDestination) -> str:
        if destination.mode == "server_channel" and destination.channel_id:
            return destination.channel_id
        if destination.mode == "dm" and destination.discord_user_id:
            result = await self._request(
                "POST",
                "/users/@me/channels",
                json={"recipient_id": destination.discord_user_id},
            )
            return str(result["id"])
        raise DiscordHttpError("Discord destination is incomplete")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> Any:
        async with httpx.AsyncClient(
            base_url="https://discord.com/api/v10",
            timeout=15,
            transport=self.transport,
            headers={
                "Authorization": self.authorization,
                "User-Agent": "DiscordBot (https://aimarketmonitor.example, 0.1)",
            },
        ) as client:
            response = await client.request(method, path, json=json)
        if response.status_code == 204:
            return {}
        if response.is_error:
            raise DiscordHttpError(f"Discord API request failed with status {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise DiscordHttpError("Discord returned an invalid response")
        return payload

    @staticmethod
    def _components(embed: DiscordEmbed) -> list[dict[str, Any]]:
        if not embed.actions:
            return []
        style = {"primary": 1, "secondary": 2, "danger": 4, "link": 5}
        buttons = []
        for action in embed.actions[:5]:
            button: dict[str, Any] = {
                "type": 2,
                "style": style[action.style],
                "label": action.label,
            }
            if action.style == "link":
                if not action.url:
                    continue
                button["url"] = action.url
            else:
                button["custom_id"] = action.custom_id
            buttons.append(button)
        return [{"type": 1, "components": buttons}] if buttons else []


class DiscordOAuthClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not settings.discord_client_id or settings.discord_client_secret is None:
            raise DiscordHttpError("Discord OAuth credentials are not configured")
        self.client_id = settings.discord_client_id
        self.client_secret = settings.discord_client_secret.get_secret_value()
        self.transport = transport

    async def exchange(
        self,
        *,
        code: str,
        redirect_url: str,
    ) -> DiscordOAuthProfile:
        async with httpx.AsyncClient(
            base_url="https://discord.com/api/v10",
            timeout=15,
            transport=self.transport,
        ) as client:
            token_response = await client.post(
                "/oauth2/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_url,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if token_response.is_error:
                raise DiscordHttpError("Discord OAuth code exchange failed")
            token_payload = token_response.json()
            access_token = token_payload.get("access_token")
            if not access_token:
                raise DiscordHttpError("Discord OAuth response omitted access token")
            profile_response = await client.get(
                "/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if profile_response.is_error:
            raise DiscordHttpError("Discord profile retrieval failed")
        profile = profile_response.json()
        if not profile.get("id") or not profile.get("username"):
            raise DiscordHttpError("Discord profile response is incomplete")
        avatar_hash = profile.get("avatar")
        avatar_url = (
            f"https://cdn.discordapp.com/avatars/{profile['id']}/{avatar_hash}.png"
            if avatar_hash
            else None
        )
        return DiscordOAuthProfile(
            discord_user_id=str(profile["id"]),
            username=str(profile["username"]),
            discriminator=profile.get("discriminator"),
            email=profile.get("email"),
            email_verified=bool(profile.get("verified", False)),
            avatar_url=avatar_url,
            scopes=str(token_payload.get("scope") or "").split(),
        )
