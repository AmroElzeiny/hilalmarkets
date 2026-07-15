from ai_market_monitor.core.config import get_settings
from ai_market_monitor.worker import app


def test_sharia_governance_worker_tasks_and_cadences_are_registered():
    schedule = app.conf.beat_schedule
    settings = get_settings()

    assert schedule["process-sc-malaysia-imports-daily"] == {
        "task": "ai_market_monitor.process_sc_malaysia_imports",
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
    assert schedule["monitor-published-sharia-sources"] == {
        "task": "ai_market_monitor.monitor_published_sharia_sources",
        "schedule": settings.sharia_source_scan_interval_hours * 60 * 60,
    }

    for task_name in (
        "ai_market_monitor.process_sc_malaysia_imports",
        "ai_market_monitor.send_sharia_review_reminders",
        "ai_market_monitor.retry_sharia_admin_telegram",
        "ai_market_monitor.monitor_published_sharia_sources",
    ):
        assert task_name in app.tasks
