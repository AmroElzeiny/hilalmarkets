"""R11 and R12 — what the Telegram bot says, and what it says about itself.

* The bot speaks to beginners. Words that describe how the code works — "deterministic",
  "webhook", "worker layer", "forward-test", "JSON-like" — are not words a person using
  the bot can act on, so no sentence the bot can send may contain one.
* The descriptions have one owner, ``telegram/profile.py``. The start screen and About use
  the brand descriptor from there, and Telegram's own profile is sent from there.
* Every command Telegram lists is one the bot answers.
* Choosing a plan happens on the Subscription page. The bot used to build a payment link
  itself without asking whether that plan was on sale.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from ai_market_monitor.core.copy_rules import scan_text
from ai_market_monitor.core.dashboard_paths import SUBSCRIPTION_PATH
from ai_market_monitor.db.models import TelegramConversationState, UserIdentity
from ai_market_monitor.db.models.enums import IdentityProvider
from ai_market_monitor.services.billing import BillingService
from ai_market_monitor.telegram import profile
from ai_market_monitor.telegram import service as telegram_service
from ai_market_monitor.telegram.service import MAIN_MENU_TEXT, TelegramBotService
from ai_market_monitor.telegram.types import TelegramCallback, TelegramInboundMessage

REPO_ROOT = Path(__file__).resolve().parents[2]
JARGON = ("deterministic", "webhook", "worker layer", "forward-test", "json-like")
BOT_SOURCES = (Path(inspect.getfile(telegram_service)), Path(inspect.getfile(profile)))


class _Previewer:
    async def run(self, strategy):  # pragma: no cover
        raise AssertionError("no preview here")


def _sentences(source: Path) -> list[tuple[int, str]]:
    """Every string the module can send, docstrings left out."""

    tree = ast.parse(source.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
        ):
            docstrings.add(id(body[0].value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


@pytest.mark.parametrize("term", JARGON)
@pytest.mark.parametrize("source", BOT_SOURCES, ids=lambda path: path.name)
def test_no_bot_sentence_uses_a_word_about_the_code(source: Path, term: str) -> None:
    offenders = [
        f"line {line}: {text[:90]!r}"
        for line, text in _sentences(source)
        if term in text.casefold()
    ]
    assert offenders == [], f"{term!r} is in a sentence the bot sends"


def test_the_bot_profile_obeys_the_brand_copy_rules() -> None:
    source = Path(inspect.getfile(profile))
    assert scan_text(source.read_text(encoding="utf-8"), source) == ()


def test_the_descriptor_is_the_brand_guide_descriptor() -> None:
    guide = (REPO_ROOT / "brand guide.md").read_text(encoding="utf-8")
    assert profile.PRODUCT_DESCRIPTOR in guide


def test_the_start_screen_and_about_use_the_one_descriptor() -> None:
    assert profile.PRODUCT_DESCRIPTOR in MAIN_MENU_TEXT
    assert profile.BOUNDARY_TEXT in MAIN_MENU_TEXT
    about = TelegramBotService._about_text(object.__new__(TelegramBotService))
    assert profile.PRODUCT_DESCRIPTOR in about
    assert profile.PRODUCT_DESCRIPTOR in profile.BOT_DESCRIPTION
    assert profile.BOUNDARY_TEXT in profile.BOT_DESCRIPTION


def test_the_profile_fits_telegrams_limits() -> None:
    assert len(profile.BOT_SHORT_DESCRIPTION) <= 120
    assert len(profile.BOT_DESCRIPTION) <= 512
    names = [command for command, _ in profile.BOT_COMMANDS]
    assert len(names) == len(set(names))
    for command, description in profile.BOT_COMMANDS:
        assert re.fullmatch(r"[a-z0-9_]{1,32}", command), command
        assert 1 <= len(description) <= 256, command


def _load_profile_script():
    path = REPO_ROOT / "scripts" / "telegram_bot_profile.py"
    spec = importlib.util.spec_from_file_location("telegram_bot_profile_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_profile_script_sends_nothing_without_apply(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ai_market_monitor.telegram import adapter

    def refuse(*_args, **_kwargs):
        raise AssertionError("a dry run built a Telegram connection")

    monkeypatch.setattr(adapter.TelegramHttpAdapter, "__init__", refuse)
    script = _load_profile_script()

    assert script.main([]) == 0
    printed = capsys.readouterr().out
    shown = json.loads(printed[: printed.rindex("}") + 1])
    assert shown == profile.bot_profile_payload()
    assert "nothing was sent" in printed


@pytest.mark.parametrize("command", [command for command, _ in profile.BOT_COMMANDS])
async def test_every_listed_command_is_answered(test_context: dict, command: str) -> None:
    async with test_context["session_factory"]() as session:
        service = TelegramBotService(session, test_context["settings"], previewer=_Previewer())
        reply = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id=f"tg-command-{command}",
                chat_id=f"chat-command-{command}",
                username="commander",
                text=f"/{command}",
            )
        )
    assert "Send /start" not in reply.text, f"/{command} was not understood"
    assert "Choose an item from the menu" not in reply.text, f"/{command} was not understood"


async def _linked_conversation(test_context: dict, session) -> TelegramBotService:
    service = TelegramBotService(session, test_context["settings"], previewer=_Previewer())
    await service.handle_start(
        TelegramInboundMessage(
            telegram_user_id="tg-plans",
            chat_id="chat-plans",
            username="planner",
            text="/start",
        )
    )
    conversation = await session.scalar(
        select(TelegramConversationState).where(
            TelegramConversationState.telegram_user_id == "tg-plans"
        )
    )
    session.add(
        UserIdentity(
            user_id=conversation.user_id,
            provider=IdentityProvider.EMAIL,
            provider_subject="planner@example.com",
            normalized_identifier="planner@example.com",
            display_identifier="planner@example.com",
            is_verified=True,
            is_primary=True,
            verified_at=datetime.now(UTC),
            profile_data={},
        )
    )
    await session.commit()
    return service


def _no_billing_from_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(*_args, **_kwargs):
        raise AssertionError("the bot changed a plan or opened a payment itself")

    monkeypatch.setattr(BillingService, "checkout_session", refuse)
    monkeypatch.setattr(BillingService, "activate_free_plan", refuse)


@pytest.mark.parametrize(
    "data",
    ["billing:checkout:trader", "billing:checkout:pro", "billing:checkout:creator", "billing:free"],
)
async def test_a_plan_button_sends_a_signed_in_person_to_the_subscription_page(
    test_context: dict, monkeypatch: pytest.MonkeyPatch, data: str
) -> None:
    _no_billing_from_telegram(monkeypatch)
    async with test_context["session_factory"]() as session:
        service = await _linked_conversation(test_context, session)
        reply = await service.handle_callback(
            TelegramCallback(
                callback_query_id=f"cb-{data}",
                telegram_user_id="tg-plans",
                chat_id="chat-plans",
                data=data,
            )
        )
    assert any(button.url and button.url.endswith(SUBSCRIPTION_PATH) for button in reply.buttons)
    assert "Subscription page" in reply.text


@pytest.mark.parametrize(
    "label", ["Upgrade Trader", "Upgrade Pro", "Upgrade Creator", "Activate Free Plan"]
)
async def test_a_plan_key_sends_a_signed_in_person_to_the_subscription_page(
    test_context: dict, monkeypatch: pytest.MonkeyPatch, label: str
) -> None:
    _no_billing_from_telegram(monkeypatch)
    async with test_context["session_factory"]() as session:
        service = await _linked_conversation(test_context, session)
        reply = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-plans",
                chat_id="chat-plans",
                username="planner",
                text=label,
            )
        )
    assert any(button.url and button.url.endswith(SUBSCRIPTION_PATH) for button in reply.buttons)
    assert "Subscription page" in reply.text
