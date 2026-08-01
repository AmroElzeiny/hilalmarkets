from __future__ import annotations

import asyncio
import fnmatch
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from ..config import Settings
from ..util import ensure_dir, get_path, redact, stable_hash
from .auth import authenticated_session_cookies
from .backend import _assistant_runtime_metadata, _latest_assistant_message
from .base import ChatTarget, TargetReply


class UITarget(ChatTarget):
    kind = "ui"

    def __init__(self, settings: Settings, evidence_dir: Path):
        self.settings = settings
        self.evidence_dir = ensure_dir(evidence_dir)
        self.pw: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.last_api_json: Any = None
        self.last_status: int | None = None
        self.last_evaluator_fault_applied: str | None = None
        self.variant: dict[str, Any] = {}

    async def _capture_response(self, response) -> None:
        if fnmatch.fnmatch(response.url, self.settings.target_ui_chat_api_pattern):
            self.last_status = response.status
            # Playwright exposes a plain, lower-cased header dictionary while the
            # backend adapter receives HTTPX's case-insensitive headers.  Reading
            # the mixed-case wire spelling here made a successfully injected UI
            # fault look unavailable, then the browser waited for a normal terminal
            # turn after its deliberate 4xx/5xx response.
            headers = {
                str(key).casefold(): str(value)
                for key, value in response.headers.items()
            }
            self.last_evaluator_fault_applied = headers.get(
                "x-hm-eval-fault-applied"
            ) or None
            try:
                self.last_api_json = await response.json()
            except Exception:
                try:
                    self.last_api_json = {"text": await response.text()}
                except Exception:
                    self.last_api_json = None

    async def start(self, scenario_id: str, variant: dict[str, Any]) -> None:
        self.variant = variant
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=self.settings.target_ui_headless)
        storage = self.settings.target_ui_storage_state or None
        self.context = await self.browser.new_context(storage_state=storage)
        if not storage and (
            self.settings.target_session_cookie is not None or self.settings.target_ui_email
        ):
            if self.settings.target_session_cookie is None and (
                self.settings.target_ui_email.strip().lower()
                != self.settings.target_backend_email.strip().lower()
                or self.settings.target_ui_password != self.settings.target_backend_password
            ):
                raise RuntimeError(
                    "UI and backend credentials must identify the same dedicated test account"
                )
            cookies = await authenticated_session_cookies(self.settings)
            target = urlsplit(self.settings.target_ui_url)
            origin = f"{target.scheme}://{target.netloc}"
            await self.context.add_cookies(
                [{"name": name, "value": value, "url": origin} for name, value in cookies.items()]
            )
        self.page = await self.context.new_page()
        self.page.on("response", self._capture_response)
        self.page.on("dialog", lambda dialog: asyncio.create_task(dialog.accept()))
        await self.page.goto(
            self.settings.target_ui_url,
            wait_until="domcontentloaded",
            timeout=self.settings.target_ui_timeout_ms,
        )
        if self.settings.target_ui_login_url in self.page.url or "signin" in self.page.url.lower():
            if not self.settings.target_ui_email or not self.settings.target_ui_password:
                raise RuntimeError("UI login required but TARGET_UI_EMAIL/PASSWORD are missing")
            await self.page.fill(
                self.settings.target_ui_email_selector,
                self.settings.target_ui_email,
            )
            await self.page.fill(
                self.settings.target_ui_password_selector,
                self.settings.target_ui_password,
            )
            await self.page.click(self.settings.target_ui_login_submit_selector)
            await self.page.wait_for_load_state(
                "networkidle",
                timeout=self.settings.target_ui_timeout_ms,
            )
            await self.page.goto(
                self.settings.target_ui_url,
                wait_until="networkidle",
                timeout=self.settings.target_ui_timeout_ms,
            )
        await self._dismiss_cookie_banner()
        await self._verify_authenticated_setup_chat()
        new_chat = self.page.locator(self.settings.target_ui_new_chat_selector)
        if await new_chat.count():
            async with self.page.expect_response(
                lambda response: (
                    response.request.method == "POST"
                    and response.url.rstrip("/").endswith(
                        self.settings.target_backend_session_path.rstrip("/")
                    )
                ),
                timeout=self.settings.target_ui_timeout_ms,
            ) as response_info:
                await new_chat.first.click()
            response = await response_info.value
            if response.status >= 400:
                raise RuntimeError(
                    f"AI Setup Chat session creation returned HTTP {response.status}"
                )

    async def _dismiss_cookie_banner(self) -> None:
        """Make the test user's explicit essential-only choice before chat actions."""

        assert self.page is not None
        button = self.page.locator(
            "[data-cookie-banner] [data-cookie-essential]"
        ).first
        if not await button.is_visible():
            return
        await button.click()
        await self.page.locator("[data-cookie-banner]").wait_for(
            state="hidden",
            timeout=self.settings.target_ui_timeout_ms,
        )

    async def _verify_authenticated_setup_chat(self) -> None:
        assert self.page is not None
        if await self.page.locator(self.settings.target_ui_expected_marker).count() != 1:
            raise RuntimeError("Authenticated AI Setup Chat marker was not found exactly once")
        forbidden = [
            item.strip()
            for item in self.settings.target_ui_forbidden_markers.split(",")
            if item.strip()
        ]
        present = [selector for selector in forbidden if await self.page.locator(selector).count()]
        if present:
            raise RuntimeError(
                "Refusing to evaluate a public support-agent page; "
                f"forbidden selectors found: {present}"
            )

    async def _install_evaluator_headers(self, fault: str | None) -> bool:
        assert self.page is not None
        target_version = str(self.variant.get("target_version") or "").strip()
        if not fault and not target_version:
            return False
        pattern = self.settings.target_ui_chat_api_pattern

        async def handler(route):
            headers = await route.request.all_headers()
            if fault:
                headers[self.settings.target_fault_header] = fault
            if target_version:
                headers[self.settings.target_version_header] = target_version
            await route.continue_(headers=headers)

        await self.page.route(pattern, handler)
        return True

    async def send(
        self,
        message: str,
        *,
        scenario_id: str,
        fault: str | None = None,
    ) -> TargetReply:
        assert self.page is not None
        self.last_api_json = None
        self.last_status = None
        self.last_evaluator_fault_applied = None
        routed = await self._install_evaluator_headers(fault)
        started = time.perf_counter()
        try:
            input_locator = self.page.locator(self.settings.target_ui_input_selector).first
            await input_locator.fill(message)
            async with self.page.expect_response(
                lambda response: (
                    response.request.method == "POST"
                    and fnmatch.fnmatch(
                        response.url,
                        self.settings.target_ui_chat_api_pattern,
                    )
                ),
                timeout=self.settings.target_ui_timeout_ms,
            ) as response_info:
                await self.page.locator(self.settings.target_ui_send_selector).first.click()
            response = await response_info.value
            await self._capture_response(response)
            if fault and self.last_evaluator_fault_applied == fault:
                raw = self.last_api_json
                if isinstance(raw, dict):
                    raw = {**raw, "_evaluator_fault_applied": fault}
                else:
                    raw = {"response": raw, "_evaluator_fault_applied": fault}
                safe_raw = redact(raw, self.settings.redacted_keys)
                return TargetReply(
                    text="",
                    latency_ms=(time.perf_counter() - started) * 1000,
                    status_code=self.last_status,
                    raw=safe_raw,
                    raw_hash=stable_hash(safe_raw),
                    conversation_id=str(get_path(raw, "id", "") or ""),
                    error=(
                        f"HTTP {self.last_status}"
                        if self.last_status is not None and self.last_status >= 400
                        else None
                    ),
                )
            # The browser view receives the same structured error envelope as the
            # backend target. The application persists and renders its safe assistant
            # error reply even though the transport status is non-2xx. Capturing that
            # rendered text lets deterministic grounding rejections remain measured
            # product behavior while retryable provider errors keep their typed
            # infrastructure classification.
            if self.last_status is not None and self.last_status >= 400:
                assistant = _latest_assistant_message(self.last_api_json)
                assistant_id = str((assistant or {}).get("id") or "")
                text = str((assistant or {}).get("content") or "")
                if assistant_id:
                    current = self.page.locator(f'[data-message-id="{assistant_id}"]')
                    await current.wait_for(
                        state="visible",
                        timeout=self.settings.target_ui_timeout_ms,
                    )
                    text = await current.inner_text()
                structured = get_path(
                    self.last_api_json,
                    self.settings.target_ui_response_object_path,
                )
                if not isinstance(structured, dict):
                    structured = None
                model, usage = _assistant_runtime_metadata(assistant)
                safe_raw = redact(self.last_api_json, self.settings.redacted_keys)
                return TargetReply(
                    text=text,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    status_code=self.last_status,
                    structured=structured,
                    raw=safe_raw,
                    raw_hash=stable_hash(safe_raw),
                    conversation_id=str(get_path(safe_raw, "id", "") or ""),
                    model=model,
                    usage=usage,
                    error=f"HTTP {self.last_status}",
                )
            request_payload = response.request.post_data_json
            client_message_id = (
                str(request_payload.get("client_message_id") or "")
                if isinstance(request_payload, dict)
                else ""
            )
            if client_message_id and not self._response_has_client_message(client_message_id):
                raise RuntimeError(
                    "UI response did not match the submitted client_message_id"
                )
            await self._await_terminal_turn()
            assistant = _latest_assistant_message(self.last_api_json)
            assistant_id = str((assistant or {}).get("id") or "")
            if not assistant_id:
                raise RuntimeError("Matching API response contained no assistant message id")
            current = self.page.locator(f'[data-message-id="{assistant_id}"]')
            await current.wait_for(
                state="visible",
                timeout=self.settings.target_ui_timeout_ms,
            )
            text = await current.inner_text()
            latency = (time.perf_counter() - started) * 1000
            structured = get_path(
                self.last_api_json,
                self.settings.target_ui_response_object_path,
            )
            if not isinstance(structured, dict):
                structured = None
            ui_contract = await self._verify_ui_contract(structured)
            model, usage = _assistant_runtime_metadata(assistant)
            safe_raw = redact(self.last_api_json, self.settings.redacted_keys)
            if isinstance(safe_raw, dict):
                safe_raw = {**safe_raw, "_evaluator_ui_contract": ui_contract}
            artifacts: list[str] = []
            if self.settings.target_ui_screenshots:
                path = self.evidence_dir / f"{scenario_id}-{assistant_id}.png"
                await self.page.screenshot(path=str(path), full_page=True)
                artifacts.append(str(path))
            return TargetReply(
                text=text,
                latency_ms=latency,
                status_code=self.last_status,
                structured=structured,
                raw=safe_raw,
                raw_hash=stable_hash(safe_raw),
                conversation_id=str(get_path(self.last_api_json, "id", "") or ""),
                model=model,
                usage=usage,
                artifacts=artifacts,
            )
        except Exception as exc:
            artifacts = []
            if self.settings.target_ui_screenshots:
                path = self.evidence_dir / f"{scenario_id}-error.png"
                try:
                    await self.page.screenshot(path=str(path), full_page=True)
                    artifacts.append(str(path))
                except Exception:
                    pass
            return TargetReply(
                text="",
                latency_ms=(time.perf_counter() - started) * 1000,
                error=f"{type(exc).__name__}: {exc}",
                artifacts=artifacts,
            )
        finally:
            if routed:
                with suppress(Exception):
                    await self.page.unroute(self.settings.target_ui_chat_api_pattern)

    async def _await_terminal_turn(self) -> None:
        """Wait until the session reports a state that waits on the user.

        A new assistant message is necessary but not sufficient: a clarification
        checkpoint, a process-state note and a refusal all render a message while the
        turn continues. The session's own ``turn_complete`` flag is the authority.
        Sessions that do not report the flag are treated as already complete, so this
        can only ever add certainty, never a hang.
        """
        deadline = time.perf_counter() + (self.settings.target_ui_timeout_ms / 1000)
        while time.perf_counter() < deadline:
            payload = self.last_api_json
            if not isinstance(payload, dict) or "turn_complete" not in payload:
                return
            if bool(payload.get("turn_complete")):
                return
            await asyncio.sleep(0.1)
        raise TimeoutError("UI response did not reach a terminal setup-chat state")

    def _response_has_client_message(self, client_message_id: str) -> bool:
        payload = self.last_api_json
        if not isinstance(payload, dict):
            return False
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return False
        return any(
            isinstance(item, dict)
            and item.get("role") == "user"
            and item.get("client_message_id") == client_message_id
            for item in messages
        )

    def _turn_state(self) -> dict[str, Any]:
        payload = self.last_api_json
        if not isinstance(payload, dict):
            return {}
        return {
            "lifecycle_state": payload.get("lifecycle_state"),
            "turn_complete": payload.get("turn_complete"),
        }

    async def _verify_ui_contract(
        self,
        structured: dict[str, Any] | None,
    ) -> dict[str, Any]:
        assert self.page is not None
        if structured is None:
            return {"captured": False, **self._turn_state()}
        expected_hash = str(structured.get("canonical_hash") or "")
        marker = self.page.locator(self.settings.target_ui_expected_marker)
        ui_hash = await marker.get_attribute("data-evaluation-contract-hash")
        if not expected_hash or ui_hash != expected_hash:
            raise RuntimeError("Structured preview hash differs from the backend contract")

        expected_nodes = {
            str(item.get("id"))
            for item in get_path(structured, "canvas.nodes", [])
            if isinstance(item, dict) and item.get("id")
        }
        canvas_markers = self.page.locator(
            '[data-testid="strategy-canvas-node"],[data-testid="strategy-canvas-group"]'
        )
        if expected_nodes and await canvas_markers.count() == 0:
            open_canvas = self.page.locator("[data-ai-open-canvas]")
            if await open_canvas.count():
                await open_canvas.first.click()
        if expected_nodes:
            await self.page.wait_for_function(
                """([selector, expectedCount]) =>
                    document.querySelectorAll(selector).length === expectedCount
                """,
                arg=[
                    '[data-testid="strategy-canvas-node"],[data-testid="strategy-canvas-group"]',
                    len(expected_nodes),
                ],
                timeout=self.settings.target_ui_timeout_ms,
            )
        actual_nodes = set(
            await canvas_markers.evaluate_all(
                "(nodes) => nodes.map((node) => node.dataset.canvasNodeId)"
            )
        )
        if expected_nodes and actual_nodes != expected_nodes:
            raise RuntimeError("Canvas node ids differ from the backend evaluation contract")
        return {
            "captured": True,
            "canonical_hash": ui_hash,
            "canvas_node_ids": sorted(actual_nodes),
            **self._turn_state(),
        }

    async def close(self) -> None:
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.pw:
            await self.pw.stop()
