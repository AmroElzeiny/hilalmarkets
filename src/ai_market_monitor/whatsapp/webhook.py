from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from ai_market_monitor.whatsapp.types import (
    WhatsAppDeliveryStatusEvent,
    WhatsAppInboundButtonReply,
    WhatsAppInboundListReply,
    WhatsAppInboundMessage,
    WhatsAppInboundText,
    WhatsAppInboundUnsupported,
)


class WhatsAppWebhookPayloadError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WhatsAppWebhookEvent:
    event_key: str
    event_type: str
    provider_message_id: str
    provider_status: str | None
    payload: dict[str, Any]
    event_at: datetime


def extract_whatsapp_events(
    payload: Any,
    *,
    expected_waba_id: str,
    expected_phone_number_id: str,
) -> list[WhatsAppWebhookEvent]:
    if not isinstance(payload, dict):
        raise WhatsAppWebhookPayloadError("WhatsApp webhook payload must be an object.")
    if payload.get("object") != "whatsapp_business_account":
        raise WhatsAppWebhookPayloadError("Unexpected WhatsApp webhook object.")
    entries = payload.get("entry")
    if not isinstance(entries, list):
        raise WhatsAppWebhookPayloadError("WhatsApp webhook entry list is missing.")
    events: list[WhatsAppWebhookEvent] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise WhatsAppWebhookPayloadError("WhatsApp webhook entry is invalid.")
        if str(entry.get("id") or "") != expected_waba_id:
            raise WhatsAppWebhookPayloadError("WhatsApp business account does not match.")
        changes = entry.get("changes")
        if not isinstance(changes, list):
            raise WhatsAppWebhookPayloadError("WhatsApp webhook changes are missing.")
        for change in changes:
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                raise WhatsAppWebhookPayloadError("WhatsApp messages value is invalid.")
            metadata = value.get("metadata")
            if not isinstance(metadata, dict):
                raise WhatsAppWebhookPayloadError("WhatsApp phone metadata is missing.")
            if str(metadata.get("phone_number_id") or "") != expected_phone_number_id:
                raise WhatsAppWebhookPayloadError("WhatsApp phone number does not match.")
            profiles = _contact_profiles(value.get("contacts"))
            messages = value.get("messages", [])
            statuses = value.get("statuses", [])
            if not isinstance(messages, list) or not isinstance(statuses, list):
                raise WhatsAppWebhookPayloadError("WhatsApp messages or statuses are invalid.")
            for message in messages:
                inbound = _parse_message(message, profiles)
                events.append(
                    WhatsAppWebhookEvent(
                        event_key=f"message:{inbound.message_id}",
                        event_type="inbound",
                        provider_message_id=inbound.message_id,
                        provider_status=None,
                        payload=inbound.model_dump(mode="json", exclude_none=True),
                        event_at=inbound.timestamp,
                    )
                )
            for status in statuses:
                parsed = _parse_status(status)
                events.append(
                    WhatsAppWebhookEvent(
                        event_key=(
                            f"status:{parsed.provider_message_id}:{parsed.status}"
                        ),
                        event_type="status",
                        provider_message_id=parsed.provider_message_id,
                        provider_status=parsed.status,
                        payload=parsed.model_dump(mode="json", exclude_none=True),
                        event_at=parsed.timestamp,
                    )
                )
    return events


def canonical_event_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _contact_profiles(raw_contacts: Any) -> dict[str, str]:
    profiles: dict[str, str] = {}
    if not isinstance(raw_contacts, list):
        return profiles
    for contact in raw_contacts:
        if not isinstance(contact, dict) or contact.get("wa_id") is None:
            continue
        profile = contact.get("profile")
        name = profile.get("name") if isinstance(profile, dict) else None
        if name:
            profiles[str(contact["wa_id"])] = " ".join(str(name).split())[:160]
    return profiles


def _parse_message(raw: Any, profiles: dict[str, str]) -> WhatsAppInboundMessage:
    if not isinstance(raw, dict):
        raise WhatsAppWebhookPayloadError("WhatsApp message is invalid.")
    message_id = str(raw.get("id") or "")
    wa_id = str(raw.get("from") or "")
    timestamp = _timestamp(raw.get("timestamp"))
    profile_name = profiles.get(wa_id)
    message_type = str(raw.get("type") or "unknown")
    try:
        if message_type == "text":
            text = raw.get("text")
            body = text.get("body") if isinstance(text, dict) else None
            return WhatsAppInboundText(
                message_id=message_id,
                wa_id=wa_id,
                profile_name=profile_name,
                timestamp=timestamp,
                text=_required_text(body),
            )
        if message_type == "interactive":
            interactive = raw.get("interactive")
            if not isinstance(interactive, dict):
                raise WhatsAppWebhookPayloadError("WhatsApp interactive reply is invalid.")
            interactive_type = interactive.get("type")
            if interactive_type == "button_reply":
                reply = interactive.get("button_reply")
                if not isinstance(reply, dict):
                    raise WhatsAppWebhookPayloadError("WhatsApp button reply is invalid.")
                return WhatsAppInboundButtonReply(
                    message_id=message_id,
                    wa_id=wa_id,
                    profile_name=profile_name,
                    timestamp=timestamp,
                    reply_id=_required_text(reply.get("id")),
                    title=_required_text(reply.get("title")),
                )
            if interactive_type == "list_reply":
                reply = interactive.get("list_reply")
                if not isinstance(reply, dict):
                    raise WhatsAppWebhookPayloadError("WhatsApp list reply is invalid.")
                return WhatsAppInboundListReply(
                    message_id=message_id,
                    wa_id=wa_id,
                    profile_name=profile_name,
                    timestamp=timestamp,
                    reply_id=_required_text(reply.get("id")),
                    title=_required_text(reply.get("title")),
                    description=_optional_text(reply.get("description")),
                )
        if message_type == "button":
            button = raw.get("button")
            if isinstance(button, dict):
                return WhatsAppInboundButtonReply(
                    message_id=message_id,
                    wa_id=wa_id,
                    profile_name=profile_name,
                    timestamp=timestamp,
                    reply_id=_required_text(button.get("payload") or button.get("text")),
                    title=_required_text(button.get("text") or button.get("payload")),
                )
        return WhatsAppInboundUnsupported(
            message_id=message_id,
            wa_id=wa_id,
            profile_name=profile_name,
            timestamp=timestamp,
            message_type=message_type,
        )
    except (TypeError, ValueError) as exc:
        raise WhatsAppWebhookPayloadError("WhatsApp message fields are invalid.") from exc


def _parse_status(raw: Any) -> WhatsAppDeliveryStatusEvent:
    if not isinstance(raw, dict):
        raise WhatsAppWebhookPayloadError("WhatsApp status is invalid.")
    raw_status = str(raw.get("status") or "unknown")
    known = {"sent", "delivered", "read", "failed", "deleted"}
    status = cast(
        Literal["sent", "delivered", "read", "failed", "deleted", "unknown"],
        raw_status if raw_status in known else "unknown",
    )
    errors = raw.get("errors")
    first_error = errors[0] if isinstance(errors, list) and errors else {}
    first_error = first_error if isinstance(first_error, dict) else {}
    error_data = first_error.get("error_data")
    error_data = error_data if isinstance(error_data, dict) else {}
    try:
        return WhatsAppDeliveryStatusEvent(
            provider_message_id=str(raw.get("id") or ""),
            status=status,
            timestamp=_timestamp(raw.get("timestamp")),
            recipient_wa_id=(
                str(raw["recipient_id"]) if raw.get("recipient_id") is not None else None
            ),
            error_code=(
                str(first_error["code"]) if first_error.get("code") is not None else None
            ),
            error_title=_bounded(first_error.get("title"), 160),
            error_message=_bounded(first_error.get("message"), 500),
            error_details=_bounded(error_data.get("details"), 500),
        )
    except (TypeError, ValueError) as exc:
        raise WhatsAppWebhookPayloadError("WhatsApp status fields are invalid.") from exc


def _timestamp(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(str(value)), tz=UTC)
    except (OSError, TypeError, ValueError) as exc:
        raise WhatsAppWebhookPayloadError("WhatsApp event timestamp is invalid.") from exc


def _bounded(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized[:limit] or None


def _required_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WhatsAppWebhookPayloadError("WhatsApp message text is invalid.")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WhatsAppWebhookPayloadError("WhatsApp message text is invalid.")
    return value
