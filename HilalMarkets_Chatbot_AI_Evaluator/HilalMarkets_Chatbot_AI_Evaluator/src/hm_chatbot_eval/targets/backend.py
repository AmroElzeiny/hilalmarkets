from __future__ import annotations

import json
import time
from typing import Any

import httpx

from ..config import Settings
from ..util import get_path, redact, render_template, stable_hash
from .base import ChatTarget, TargetReply


class BackendTarget(ChatTarget):
    kind = "backend"

    def __init__(self, settings: Settings):
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
            response = await self.client.post(self.settings.target_backend_reset_url, headers=self.settings.backend_headers)
            response.raise_for_status()

    async def send(self, message: str, *, scenario_id: str, fault: str | None = None) -> TargetReply:
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
        headers.update({str(k): str(v) for k, v in (self.variant.get("headers") or {}).items()})
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
            raw: Any
            try:
                raw = response.json()
            except ValueError:
                raw = {"text": response.text}
            text = get_path(raw, self.settings.target_backend_response_text_path, "")
            if not isinstance(text, str):
                text = json.dumps(text, ensure_ascii=False)
            structured = get_path(raw, self.settings.target_backend_response_object_path)
            if not isinstance(structured, dict):
                structured = None
            cid = get_path(raw, self.settings.target_backend_conversation_id_path, self.conversation_id)
            self.conversation_id = str(cid or self.conversation_id)
            model = get_path(raw, self.settings.target_backend_model_path)
            usage = get_path(raw, self.settings.target_backend_usage_path, {})
            if not isinstance(usage, dict):
                usage = {}
            self.history.extend([{"role": "user", "content": message}, {"role": "assistant", "content": text}])
            safe_raw = redact(raw, self.settings.redacted_keys)
            return TargetReply(
                text=text or f"HTTP {response.status_code} with no assistant text",
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
