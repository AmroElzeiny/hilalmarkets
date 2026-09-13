"""Set the Hilal Markets bot's command list and descriptions on Telegram.

The words come from ``ai_market_monitor/telegram/profile.py``, the same place the bot's
main menu and About screen read theirs, so what Telegram shows before the first message
cannot disagree with what the bot says after it.

Nothing is sent unless ``--apply`` is given. Without it the script prints exactly what
it would send and stops.

    .venv/Scripts/python scripts/telegram_bot_profile.py            # show only
    .venv/Scripts/python scripts/telegram_bot_profile.py --apply    # change the live bot

``--apply`` uses ``TELEGRAM_BOT_TOKEN`` from the environment of wherever it runs. It
never prints the token.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from ai_market_monitor.telegram.profile import bot_profile_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Show, or with --apply set, the Telegram bot's commands and descriptions."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Send the commands and descriptions to Telegram. Without it nothing is sent.",
    )
    args = parser.parse_args(argv)

    payload = bot_profile_payload()
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    if not args.apply:
        print("\nDry run: nothing was sent to Telegram. Add --apply to change the live bot.")
        return 0
    return asyncio.run(_apply(payload))


async def _apply(payload: dict[str, Any]) -> int:
    from ai_market_monitor.core.config import get_settings
    from ai_market_monitor.telegram.adapter import TelegramDeliveryError, TelegramHttpAdapter

    try:
        adapter = TelegramHttpAdapter(get_settings())
        await adapter.set_my_commands(payload["commands"])
        await adapter.set_my_description(payload["description"])
        await adapter.set_my_short_description(payload["short_description"])
    except TelegramDeliveryError as exc:
        print(f"Telegram refused the change ({exc.code}). Nothing more was sent.", file=sys.stderr)
        return 1
    print("\nThe bot's commands and descriptions were updated on Telegram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
