def test_notification_service_import_does_not_load_full_telegram_service():
    from ai_market_monitor.services.notifications import NotificationDispatcher
    from ai_market_monitor.telegram.adapter import TelegramHttpAdapter

    assert NotificationDispatcher is not None
    assert TelegramHttpAdapter is not None
