from ai_market_monitor.core.config import get_settings
from ai_market_monitor.worker import _fetch_exchange_symbols, app


def test_sharia_governance_worker_tasks_and_cadences_are_registered():
    schedule = app.conf.beat_schedule
    settings = get_settings()

    assert schedule["process-sharia-authority-imports"] == {
        "task": "ai_market_monitor.process_sharia_authority_imports",
        "schedule": settings.sharia_source_scan_interval_hours * 60 * 60,
    }
    assert schedule["send-sharia-review-reminders-hourly"] == {
        "task": "ai_market_monitor.send_sharia_review_reminders",
        "schedule": 60 * 60,
    }
    assert schedule["retry-sharia-admin-telegram-every-minute"] == {
        "task": "ai_market_monitor.retry_sharia_admin_telegram",
        "schedule": 60,
    }
    assert schedule["retry-account-emails-every-minute"] == {
        "task": "ai_market_monitor.retry_account_emails",
        "schedule": 60,
    }
    assert schedule["monitor-published-sharia-sources"] == {
        "task": "ai_market_monitor.monitor_published_sharia_sources",
        "schedule": settings.sharia_source_scan_interval_hours * 60 * 60,
    }

    for task_name in (
        "ai_market_monitor.process_sharia_authority_imports",
        "ai_market_monitor.process_sc_malaysia_imports",
        "ai_market_monitor.send_sharia_review_reminders",
        "ai_market_monitor.retry_sharia_admin_telegram",
        "ai_market_monitor.retry_account_emails",
        "ai_market_monitor.monitor_published_sharia_sources",
    ):
        assert task_name in app.tasks


class _PartiallyFailingProvider:
    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        if exchange == "binance":
            raise RuntimeError("api.binance.com unreachable")
        return ["BTC/USDT", "ETH/USDT"]


async def test_fetch_exchange_symbols_isolates_a_failing_exchange():
    result = await _fetch_exchange_symbols(_PartiallyFailingProvider(), ("binance", "bybit"))

    assert "binance" not in result, (
        "a failed fetch must not be recorded as an empty, confirmed-delisted symbol set"
    )
    assert result["bybit"] == {"BTC/USDT", "ETH/USDT"}, (
        "binance's failure must not prevent bybit's fetch from completing"
    )


class _BothExchangesSucceedProvider:
    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        return {"binance": ["BTC/USDT"], "bybit": ["btc/usdt", "eth/usdt"]}[exchange]


async def test_fetch_exchange_symbols_returns_every_exchange_when_all_succeed():
    result = await _fetch_exchange_symbols(
        _BothExchangesSucceedProvider(), ("binance", "bybit")
    )

    assert result == {"binance": {"BTC/USDT"}, "bybit": {"BTC/USDT", "ETH/USDT"}}
