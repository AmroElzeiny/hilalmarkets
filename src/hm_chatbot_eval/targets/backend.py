from __future__ import annotations

import json
import time
from typing import Any

import httpx

from ..config import Settings
from ..util import get_path, redact, render_template, stable_hash
from .auth import authenticated_session_cookies
from .base import ChatTarget, TargetReply


class HilalMarketsBackendTarget(ChatTarget):
    """Adapter for the authenticated production AI Setup Chat session flow."""

    kind = "backend"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            base_url=settings.target_backend_base_url,
            timeout=settings.target_backend_timeout_seconds,
            follow_redirects=True,
        )
        self._owns_client = client is None
        self.conversation_id = ""
        self.history: list[dict[str, str]] = []
        self.variant: dict[str, Any] = {}
        self.turn_number = 0

    async def start(self, scenario_id: str, variant: dict[str, Any]) -> None:
        self.variant = variant
        self.conversation_id = ""
        self.history = []
        self.turn_number = 0
        await self._authenticate_if_configured()
        response = await self.client.post(
            self.settings.target_backend_session_path,
            headers=self.settings.backend_headers,
        )
        response.raise_for_status()
        payload = response.json()
        conversation_id = payload.get("id") if isinstance(payload, dict) else None
        if not conversation_id:
            raise RuntimeError("AI Setup Chat session creation returned no session id")
        self.conversation_id = str(conversation_id)

    async def _authenticate_if_configured(self) -> None:
        if (
            not self.settings.target_backend_email
            and self.settings.target_session_cookie is None
        ):
            return
        self.client.cookies.update(await authenticated_session_cookies(self.settings))

    async def send(
        self,
        message: str,
        *,
        scenario_id: str,
        fault: str | None = None,
    ) -> TargetReply:
        self.turn_number += 1
        headers = dict(self.settings.backend_headers)
        target_version = str(self.variant.get("target_version") or "").strip()
        if target_version:
            headers[self.settings.target_version_header] = target_version
        if fault:
            headers[self.settings.target_fault_header] = fault
        client_message_id = stable_hash(
            {
                "scenario_id": scenario_id,
                "conversation_id": self.conversation_id,
                "turn": self.turn_number,
                "message": message,
            }
        )[:40]
        payload = {
            "message": message,
            "client_message_id": f"eval-{client_message_id}",
        }
        path = self.settings.target_backend_message_path_template.format(
            chat_id=self.conversation_id
        )
        started = time.perf_counter()
        try:
            response = await self.client.post(path, headers=headers, json=payload)
            latency = (time.perf_counter() - started) * 1000
            try:
                raw: Any = response.json()
            except ValueError:
                raw = {"text": response.text}
            fault_applied = response.headers.get("X-HM-Eval-Fault-Applied", "").strip()
            if fault_applied:
                raw = (
                    {**raw, "_evaluator_fault_applied": fault_applied}
                    if isinstance(raw, dict)
                    else {"response": raw, "_evaluator_fault_applied": fault_applied}
                )
            assistant = _latest_assistant_message(raw)
            text = str((assistant or {}).get("content") or "")
            structured = (
                raw.get("evaluation_contract")
                if isinstance(raw, dict) and isinstance(raw.get("evaluation_contract"), dict)
                else None
            )
            model, usage = _assistant_runtime_metadata(assistant)
            structured_application_error = (
                response.status_code == 409
                and structured is not None
                and bool(text.strip())
            )
            self.history.extend(
                [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": text},
                ]
            )
            safe_raw = redact(raw, self.settings.redacted_keys)
            return TargetReply(
                text=text,
                latency_ms=latency,
                status_code=response.status_code,
                structured=structured,
                raw=safe_raw,
                raw_hash=stable_hash(safe_raw),
                conversation_id=self.conversation_id,
                model=model,
                usage=usage,
                error=(
                    None
                    if response.is_success or structured_application_error
                    else f"HTTP {response.status_code}"
                ),
            )
        except Exception as exc:
            return TargetReply(
                text="",
                latency_ms=(time.perf_counter() - started) * 1000,
                error=f"{type(exc).__name__}: {exc}",
                conversation_id=self.conversation_id,
            )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class GenericHTTPBackendTarget(ChatTarget):
    """Original black-box JSON adapter for non-integrated deployments."""

    kind = "backend"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=settings.target_backend_timeout_seconds)
        self.conversation_id = ""
        self.history: list[dict[str, str]] = []
        self.variant: dict[str, Any] = {}

    async def start(self, scenario_id: str, variant: dict[str, Any]) -> None:
        self.variant = variant
        self.conversation_id = ""
        self.history = []
        if self.settings.target_backend_reset_url:
            response = await self.client.post(
                self.settings.target_backend_reset_url,
                headers=self.settings.backend_headers,
            )
            response.raise_for_status()

    async def send(
        self,
        message: str,
        *,
        scenario_id: str,
        fault: str | None = None,
    ) -> TargetReply:
        variables = {
            "message": message,
            "conversation_id": self.conversation_id,
            "history_json": json.dumps(self.history, ensure_ascii=False),
            "scenario_id": scenario_id,
            "fault": fault or "",
        }
        template = json.loads(self.settings.target_backend_request_template_json)
        payload = render_template(template, variables)
        if isinstance(self.variant.get("payload_overrides"), dict):
            payload.update(self.variant["payload_overrides"])
        headers = dict(self.settings.backend_headers)
        headers.update(
            {str(key): str(value) for key, value in (self.variant.get("headers") or {}).items()}
        )
        if fault:
            headers[self.settings.target_fault_header] = fault
        started = time.perf_counter()
        try:
            response = await self.client.request(
                self.settings.target_backend_method,
                self.settings.target_backend_url,
                headers=headers,
                json=payload,
            )
            latency = (time.perf_counter() - started) * 1000
            try:
                raw: Any = response.json()
            except ValueError:
                raw = {"text": response.text}
            text = get_path(raw, self.settings.target_backend_response_text_path, "")
            if not isinstance(text, str):
                text = json.dumps(text, ensure_ascii=False)
            structured = get_path(raw, self.settings.target_backend_response_object_path)
            if not isinstance(structured, dict):
                structured = None
            cid = get_path(
                raw,
                self.settings.target_backend_conversation_id_path,
                self.conversation_id,
            )
            self.conversation_id = str(cid or self.conversation_id)
            model = get_path(raw, self.settings.target_backend_model_path)
            usage = get_path(raw, self.settings.target_backend_usage_path, {})
            if not isinstance(usage, dict):
                usage = {}
            self.history.extend(
                [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": text},
                ]
            )
            safe_raw = redact(raw, self.settings.redacted_keys)
            return TargetReply(
                text=text,
                latency_ms=latency,
                status_code=response.status_code,
                structured=structured,
                raw=safe_raw,
                raw_hash=stable_hash(safe_raw),
                conversation_id=self.conversation_id,
                model=str(model) if model else None,
                usage=usage,
                error=None if response.is_success else f"HTTP {response.status_code}",
            )
        except Exception as exc:
            return TargetReply(
                text="",
                latency_ms=(time.perf_counter() - started) * 1000,
                error=f"{type(exc).__name__}: {exc}",
            )

    async def close(self) -> None:
        await self.client.aclose()


def _latest_assistant_message(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        return None
    messages = [
        item
        for item in payload["messages"]
        if isinstance(item, dict) and item.get("role") == "assistant"
    ]
    return messages[-1] if messages else None


def _assistant_runtime_metadata(
    assistant: dict[str, Any] | None,
) -> tuple[str | None, dict[str, Any]]:
    payload = (assistant or {}).get("payload")
    if not isinstance(payload, dict):
        return None, {}
    model = payload.get("_traceedge_model") or payload.get("model")
    usage = payload.get("usage")
    return str(model) if model else None, dict(usage) if isinstance(usage, dict) else {}


BackendTarget = HilalMarketsBackendTarget
