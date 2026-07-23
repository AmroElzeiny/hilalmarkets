from __future__ import annotations

import asyncio
import fnmatch
import json
import time
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from ..config import Settings
from ..util import ensure_dir, get_path, redact, stable_hash
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
        self.variant: dict[str, Any] = {}

    async def _capture_response(self, response) -> None:
        if fnmatch.fnmatch(response.url, self.settings.target_ui_chat_api_pattern):
            self.last_status = response.status
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
        self.page = await self.context.new_page()
        self.page.on("response", self._capture_response)
        await self.page.goto(self.settings.target_ui_url, wait_until="domcontentloaded", timeout=self.settings.target_ui_timeout_ms)
        if self.settings.target_ui_login_url in self.page.url or "signin" in self.page.url.lower():
            if not self.settings.target_ui_email or not self.settings.target_ui_password:
                raise RuntimeError("UI login required but TARGET_UI_EMAIL/PASSWORD are missing")
            await self.page.fill(self.settings.target_ui_email_selector, self.settings.target_ui_email)
            await self.page.fill(self.settings.target_ui_password_selector, self.settings.target_ui_password)
            await self.page.click(self.settings.target_ui_login_submit_selector)
            await self.page.wait_for_load_state("networkidle", timeout=self.settings.target_ui_timeout_ms)
            await self.page.goto(self.settings.target_ui_url, wait_until="networkidle", timeout=self.settings.target_ui_timeout_ms)
        body = (await self.page.locator("body").inner_text()).lower()
        expected = self.settings.target_ui_expected_marker.lower()
        if expected and expected not in body:
            raise RuntimeError(f"Expected AI Setup Chat marker not found: {expected!r}")
        forbidden = [x.strip().lower() for x in self.settings.target_ui_forbidden_markers.split(",") if x.strip()]
        present = [x for x in forbidden if x in body]
        if present:
            raise RuntimeError(f"Refusing to test likely support-agent UI; forbidden markers found: {present}")
        if await self.page.locator(self.settings.target_ui_new_chat_selector).count():
            await self.page.locator(self.settings.target_ui_new_chat_selector).first.click()

    async def _apply_fault(self, fault: str | None) -> None:
        if not fault or not self.page:
            return
        pattern = self.settings.target_ui_chat_api_pattern
        triggered = {"done": False}

        async def handler(route):
            if triggered["done"]:
                await route.continue_()
                return
            triggered["done"] = True
            if fault == "timeout_once":
                await asyncio.sleep(self.settings.target_ui_timeout_ms / 1000 + 1)
                await route.abort("timedout")
            elif fault == "429_once":
                await route.fulfill(status=429, content_type="application/json", body='{"error":"rate_limited"}')
            elif fault == "empty_once":
                await route.fulfill(status=200, content_type="application/json", body='{}')
            elif fault in {"invalid_json_once", "partial_json_once"}:
                await route.fulfill(status=200, content_type="application/json", body='{"message":"partial"')
            else:
                await route.abort("connectionfailed")
        await self.page.route(pattern, handler)

    async def send(self, message: str, *, scenario_id: str, fault: str | None = None) -> TargetReply:
        assert self.page is not None
        self.last_api_json = None
        self.last_status = None
        await self._apply_fault(fault)
        messages = self.page.locator(self.settings.target_ui_assistant_message_selector)
        before = await messages.count()
        started = time.perf_counter()
        try:
            input_locator = self.page.locator(self.settings.target_ui_input_selector).first
            await input_locator.fill(message)
            await self.page.locator(self.settings.target_ui_send_selector).first.click()
            await self.page.wait_for_function(
                "([selector,before]) => document.querySelectorAll(selector).length > before",
                [self.settings.target_ui_assistant_message_selector.split(",")[0], before],
                timeout=self.settings.target_ui_timeout_ms,
            )
            current = self.page.locator(self.settings.target_ui_assistant_message_selector)
            text = await current.nth((await current.count()) - 1).inner_text()
            latency = (time.perf_counter() - started) * 1000
            structured = get_path(self.last_api_json, self.settings.target_ui_response_object_path)
            if not isinstance(structured, dict):
                structured = None
            safe_raw = redact(self.last_api_json, self.settings.redacted_keys)
            artifacts: list[str] = []
            if self.settings.target_ui_screenshots:
                path = self.evidence_dir / f"{scenario_id}-{before+1}.png"
                await self.page.screenshot(path=str(path), full_page=True)
                artifacts.append(str(path))
            return TargetReply(
                text=text,
                latency_ms=latency,
                status_code=self.last_status,
                structured=structured,
                raw=safe_raw,
                raw_hash=stable_hash(safe_raw),
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
            return TargetReply(text="", latency_ms=(time.perf_counter() - started) * 1000, error=f"{type(exc).__name__}: {exc}", artifacts=artifacts)
        finally:
            if fault:
                try:
                    await self.page.unroute(self.settings.target_ui_chat_api_pattern)
                except Exception:
                    pass

    async def close(self) -> None:
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.pw:
            await self.pw.stop()
