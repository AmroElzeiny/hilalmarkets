"""What the Hilal Markets bot says about itself, written once.

Telegram shows three things before a person has sent the bot a single message: the
command list (what appears when they type ``/``), the description (the text in an empty
chat, above the Start button) and the short description (the bot's profile page and any
shared link). The bot's own main menu and About screen describe the product as well.

Written separately, those descriptions drift. The main menu said "approved crypto
spot-market conditions" and "deterministic proof", About said "the deterministic
scanner", and Telegram's own profile was set by hand in BotFather with no copy in this
repository at all. So every one of them reads the sentences below, and
``scripts/telegram_bot_profile.py`` sends the three Telegram-side ones.

The sentences follow ``brand guide.md``: the descriptor in section 1, "Islamic
principles" for a general explanation (section 16), and the product boundaries — spot
only, no leverage, no trades placed, no promise of outcomes.
"""

from __future__ import annotations

from typing import Any, Final

#: Brand guide section 1, the working descriptor, word for word.
PRODUCT_DESCRIPTOR: Final[str] = (
    "A platform for Muslim traders to build strategies and monitor setups in line with "
    "Islamic principles."
)

#: What this chat is for, as opposed to the website.
TELEGRAM_ROLE_TEXT: Final[str] = (
    "This chat brings you your alerts and a quick look at your monitors."
)

#: What the product never does. "does not guarantee outcomes" is a phrase the start
#: screen has always carried, and a test holds it there.
BOUNDARY_TEXT: Final[str] = (
    "Spot market only. No leverage. Hilal Markets does not guarantee outcomes, place "
    "trades or tell you what to buy or sell. You make every decision."
)

#: The commands Telegram lists when somebody types "/". Every one is answered by
#: ``TelegramBotService.handle_message``; a test sends each one and fails on a command the
#: bot would not understand.
BOT_COMMANDS: Final[tuple[tuple[str, str], ...]] = (
    ("start", "Open the main menu"),
    ("about", "What Hilal Markets does"),
    ("pricing", "See plans and prices"),
    ("support", "Get help from our team"),
    ("help", "How this bot works"),
)

#: Telegram allows 120 characters here.
BOT_SHORT_DESCRIPTION: Final[str] = (
    "Alerts for the crypto market conditions you choose, in line with Islamic principles. "
    "Never places trades."
)

#: Telegram allows 512 characters here.
BOT_DESCRIPTION: Final[str] = (
    f"{PRODUCT_DESCRIPTOR}\n\n{TELEGRAM_ROLE_TEXT}\n\n{BOUNDARY_TEXT}\n\nPress Start to begin."
)


def bot_profile_payload() -> dict[str, Any]:
    """The three things ``setMyCommands``, ``setMyDescription`` and
    ``setMyShortDescription`` are sent, in the shape the Bot API takes them."""

    return {
        "commands": [
            {"command": command, "description": description}
            for command, description in BOT_COMMANDS
        ],
        "description": BOT_DESCRIPTION,
        "short_description": BOT_SHORT_DESCRIPTION,
    }
