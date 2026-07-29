import pytest
from pydantic import ValidationError

from ai_market_monitor.core.config import Settings


def test_deployed_environment_rejects_legacy_writable_setup_path():
    settings = Settings()
    payload = settings.model_dump(mode="python")
    payload.update(
        {
            "app_env": "staging",
            "setup_chat_launch_v2_enabled": True,
            "setup_chat_legacy_test_compat_enabled": True,
        }
    )

    with pytest.raises(ValidationError, match="forbidden outside local tests"):
        Settings.model_validate(payload)


def test_launch_v2_cannot_be_disabled():
    settings = Settings()
    payload = settings.model_dump(mode="python")
    payload["setup_chat_launch_v2_enabled"] = False

    with pytest.raises(ValidationError, match="must remain true"):
        Settings.model_validate(payload)
