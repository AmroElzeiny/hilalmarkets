"""Preview a compliance Telegram notice; live delivery is explicitly gated."""

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.telegram.adapter import TelegramDeliveryResult, TelegramHttpAdapter
from ai_market_monitor.telegram.types import TelegramButton, TelegramOutboundMessage

LIVE_CONFIRMATION = "LIVE_COMPLIANCE_TELEGRAM_TEST"


@dataclass(slots=True)
class RecordingTelegramAdapter:
    messages: list[TelegramOutboundMessage] = field(default_factory=list)

    async def deliver(self, message: TelegramOutboundMessage) -> TelegramDeliveryResult:
        self.messages.append(message)
        return TelegramDeliveryResult(message_ids=["fake-compliance-message"])


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-id", help="Dedicated test chat; required with --live")
    parser.add_argument("--live", action="store_true", help="Send through Telegram HTTP")
    parser.add_argument("--confirm", help=f"Required with --live: {LIVE_CONFIRMATION}")
    return parser.parse_args()


def _payload(chat_id: str) -> TelegramOutboundMessage:
    event_time = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return TelegramOutboundMessage(
        chat_id=chat_id,
        text=(
            "Screening status changed\n"
            "Asset: TEST/USDT\n"
            "Previous: Eligible\n"
            "Current: Under Review\n"
            "Methodology: Test methodology v1\n"
            "Affected Watchlists: 1\n"
            f"Recorded: {event_time}\n\n"
            "Test payload only. No real asset status was changed."
        ),
        buttons=[
            TelegramButton(
                "Open evidence",
                "open_evidence",
                url="https://example.invalid/passports/test",
            )
        ],
    )


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    target = args.chat_id or "fake-test-chat"
    message = _payload(target)
    print("Outgoing test payload:")
    print(message.text)
    print(f"Button: {message.buttons[0].text} -> {message.buttons[0].url}")
    if not args.live:
        adapter = RecordingTelegramAdapter()
        result = await adapter.deliver(message)
        print(f"FAKE DELIVERY: recorded {len(adapter.messages)} message.")
        print(f"Delivery reference: {result.message_ids[0]}")
        return 0
    if args.confirm != LIVE_CONFIRMATION:
        print(f"Live delivery blocked. Pass --confirm {LIVE_CONFIRMATION}.")
        return 2
    if not args.chat_id:
        print("Live delivery blocked. Pass --chat-id for a dedicated test chat.")
        return 2
    if (
        settings.telegram_adapter != "http"
        or not settings.telegram_enabled
        or settings.telegram_bot_token is None
    ):
        print("Live delivery blocked. Telegram HTTP delivery is not configured.")
        return 2
    print(f"LIVE TEST: sending the displayed payload to test chat {args.chat_id}.")
    result = await TelegramHttpAdapter(settings).deliver(message)
    print(f"Delivery accepted. Message references: {', '.join(result.message_ids)}")
    return 0


def main() -> int:
    return asyncio.run(_run(_arguments()))


if __name__ == "__main__":
    raise SystemExit(main())
