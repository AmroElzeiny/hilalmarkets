"""Temporary: screenshot the auth pages across tablet and phone widths. Delete after use."""

from __future__ import annotations

import pathlib

import pytest
from playwright.sync_api import Page

OUT = pathlib.Path(
    r"C:\Users\amroe\AppData\Local\Temp\claude\c--Users-amroe-Downloads-NovaAIS-Systems-Trading-Trading-assistant\3e98b8e4-3e25-4243-b141-17d3e1d32415\scratchpad\widths"
)

WIDTHS = (1024, 900, 820, 768, 600, 480, 390)


@pytest.mark.parametrize("name,path", [("signin", "/signin"), ("signup", "/signup")])
def test_widths(page: Page, base_url: str, name: str, path: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 1000})
        page.goto(f"{base_url}{path}", wait_until="networkidle")
        banner = page.locator("[data-cookie-banner].is-visible")
        if banner.count():
            page.locator("[data-cookie-banner] [data-cookie-essential]").first.click()
            page.wait_for_timeout(300)
        page.wait_for_timeout(300)
        box = page.locator(".auth-card").bounding_box()
        main = page.locator("main.auth-main").bounding_box()
        print(f"{name} {width}: card={box} main={main}")
        page.screenshot(path=str(OUT / f"{name}-{width}.png"), full_page=True)
