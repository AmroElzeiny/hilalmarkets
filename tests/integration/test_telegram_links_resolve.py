"""R10 — every address the Telegram bot can send is a page that exists now.

A button inside a Telegram message lives as long as the message does. The bot had been
sending ``/dashboard/billing`` and ``/dashboard/trial`` after the dashboard moved to
``/dashboard/subscription``, ``/dashboard`` as "home" after Home moved to ``/home``, and
a "Performance" button to a page that does not exist, each typed into a call by hand.

Two rules, checked from both sides:

* **No address is typed by hand.** Every path handed to ``_dashboard_url`` or
  ``_dashboard_button`` is a name from ``core/dashboard_paths.py`` — the one owner of
  addresses — never a string literal. A new literal fails here before it is sent.
* **Every address resolves.** Each path the bot can produce is opened in the real app as
  a signed-in person, following redirects, and must end on a page that loads.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from ai_market_monitor.core import dashboard_paths
from ai_market_monitor.telegram import service as telegram_service
from ai_market_monitor.telegram.service import TelegramBotService
from tests.integration.test_dashboard_web import _signup_and_verify

SERVICE_FILE = Path(inspect.getfile(telegram_service))
TELEGRAM_PACKAGE = SERVICE_FILE.parent
ADDRESS_CALLS = {"_dashboard_url": 0, "_dashboard_button": 1}
SIGN_IN_PAGES = {"/signin", "/signup"}


def _path_argument(call: ast.Call, position: int) -> ast.expr | None:
    if len(call.args) > position:
        return call.args[position]
    for keyword in call.keywords:
        if keyword.arg == "path":
            return keyword.value
    return None


def _literal_address(node: ast.expr) -> str | None:
    """The hand-typed address inside ``node``, if it contains one."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _hand_typed_addresses() -> list[str]:
    tree = ast.parse(SERVICE_FILE.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        position = ADDRESS_CALLS.get(node.func.attr)
        if position is None:
            continue
        argument = _path_argument(node, position)
        literal = _literal_address(argument) if argument is not None else None
        if literal is not None:
            found.append(f"line {node.lineno}: {literal!r}")
    return found


def test_no_address_in_the_bot_is_typed_by_hand() -> None:
    assert _hand_typed_addresses() == [], (
        "these addresses are typed into a Telegram call instead of named from "
        "core/dashboard_paths.py"
    )


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


@pytest.mark.parametrize(
    "source",
    sorted(TELEGRAM_PACKAGE.glob("*.py")),
    ids=lambda path: path.name,
)
def test_no_telegram_module_writes_a_dashboard_address_as_text(source: Path) -> None:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    docstrings = _docstring_nodes(tree)
    offenders = [
        f"line {node.lineno}: {node.value!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and node.value.startswith(("/dashboard", "/home", "/pricing", "/billing"))
    ]
    assert offenders == []


def _page_keys() -> list[str]:
    """Every page name ``_dashboard_path_for_page`` knows, read from its own table."""

    source = inspect.getsource(TelegramBotService._dashboard_path_for_page)
    tree = ast.parse(inspect.cleandoc("\n" + source))
    keys = [
        key.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    ]
    assert keys, "the page table could not be read"
    return keys


def _addresses_the_bot_can_send() -> list[str]:
    named = {
        value
        for name, value in vars(telegram_service).items()
        if name.endswith("_PATH")
        and isinstance(value, str)
        and value.startswith("/")
        and getattr(dashboard_paths, name, None) == value
    }
    named.add(telegram_service._ONE_TIME_SCAN_PATH)
    from_pages = {TelegramBotService._dashboard_path_for_page(key) for key in _page_keys()}
    from_pages.add(TelegramBotService._dashboard_path_for_page("a page nobody named"))
    return sorted(named | from_pages)


ADDRESSES = _addresses_the_bot_can_send()


def test_the_address_list_is_not_empty_or_partial() -> None:
    for required in (
        dashboard_paths.HOME_PATH,
        dashboard_paths.SUBSCRIPTION_PATH,
        dashboard_paths.CONNECTIONS_PATH,
        dashboard_paths.PRICING_PATH,
    ):
        assert required in ADDRESSES, required
    assert "/dashboard/billing" not in ADDRESSES
    assert "/dashboard/trial" not in ADDRESSES


@pytest.mark.parametrize("address", ADDRESSES)
async def test_every_address_the_bot_sends_opens_a_real_page(
    test_context: dict, address: str
) -> None:
    slug = "".join(character for character in address if character.isalnum()) or "root"
    await _signup_and_verify(test_context, email=f"tg-link-{slug}@example.com")

    response = await test_context["client"].get(address, follow_redirects=True)

    assert response.status_code == 200, f"{address} answered {response.status_code}"
    assert response.url.path not in SIGN_IN_PAGES, (
        f"{address} sent a signed-in person to {response.url.path}"
    )
