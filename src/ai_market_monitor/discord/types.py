from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class DiscordOAuthProfile:
    discord_user_id: str
    username: str
    discriminator: str | None = None
    email: str | None = None
    email_verified: bool = False
    avatar_url: str | None = None
    scopes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DiscordPermissionSet:
    send_messages: bool = False
    embed_links: bool = False
    read_message_history: bool = False
    create_public_threads: bool = False
    send_messages_in_threads: bool = False
    manage_roles: bool = False

    def missing_for_alerts(self, *, threaded: bool) -> list[str]:
        required = {
            "send_messages": self.send_messages,
            "embed_links": self.embed_links,
            "read_message_history": self.read_message_history,
        }
        if threaded:
            required["create_public_threads"] = self.create_public_threads
            required["send_messages_in_threads"] = self.send_messages_in_threads
        return [name for name, allowed in required.items() if not allowed]


@dataclass(frozen=True, slots=True)
class DiscordField:
    name: str
    value: str
    inline: bool = True


@dataclass(frozen=True, slots=True)
class DiscordAction:
    label: str
    custom_id: str
    style: Literal["primary", "secondary", "danger", "link"] = "secondary"
    url: str | None = None


@dataclass(frozen=True, slots=True)
class DiscordEmbed:
    title: str
    description: str
    fields: list[DiscordField] = field(default_factory=list)
    image_url: str | None = None
    footer: str | None = None
    timestamp: datetime | None = None
    actions: list[DiscordAction] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DiscordSendResult:
    provider_message_id: str
    thread_id: str | None = None


@dataclass(frozen=True, slots=True)
class DiscordCommandContext:
    command_name: str
    user_id: UUID
    discord_user_id: str
    guild_id: str | None = None
    channel_id: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DiscordCommandResponse:
    content: str
    ephemeral: bool = True
    embed: DiscordEmbed | None = None
    actions: list[DiscordAction] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ModerationFinding:
    code: str
    severity: Literal["low", "medium", "high", "critical"]
    message: str


@dataclass(frozen=True, slots=True)
class ModerationResult:
    allowed: bool
    findings: list[ModerationFinding] = field(default_factory=list)

    @property
    def requires_human_review(self) -> bool:
        return any(finding.severity in {"high", "critical"} for finding in self.findings)
