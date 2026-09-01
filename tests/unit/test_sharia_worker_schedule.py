from ai_market_monitor.worker import _fetch_exchange_symbols, app


def test_sharia_governance_worker_tasks_and_cadences_are_registered():
    schedule = app.conf.beat_schedule

    # The tick is daily and the cadence is a week. They are deliberately different
    # numbers: this task is also where a newly listed coin gets discovered and first
    # researched, so tying its tick to the re-check cadence would have made a coin added
    # on Tuesday wait for the next weekly firing before anything looked at it.
    assert schedule["process-sharia-authority-imports"] == {
        "task": "ai_market_monitor.process_sharia_authority_imports",
        "schedule": 24 * 60 * 60,
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
        "schedule": 24 * 60 * 60,
    }


def test_the_shariah_recheck_cadence_ships_as_one_week():
    """Every published coin's evidence is looked at again once a week.

    The product owner set this on 1 September 2026; it was 24 hours, which re-fetched
    every authority page and every project blog six times more often than any of them
    changes. The **default** is asserted, not the running value: an operator may still
    set SHARIA_SOURCE_SCAN_INTERVAL_HOURS for a particular deployment, and that is not a
    regression. What must not happen quietly is the shipped default going back to daily.
    """

    from ai_market_monitor.core.config import Settings

    assert Settings.model_fields["sharia_source_scan_interval_hours"].default == 168, (
        "the shipped Shariah re-check cadence is meant to be one week (168 hours). It is "
        "read by the importers, the research pipeline, the source monitor and the "
        "governance record at once, so it changes all of them together — which is why "
        "there is one number and not four."
    )

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
