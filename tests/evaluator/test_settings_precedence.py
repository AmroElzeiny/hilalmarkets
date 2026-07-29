"""``.env`` outranks the ambient environment for credentials.

A revoked ``OPENAI_API_KEY`` persisted at Windows Machine scope silently outranked
the project's own ``.env``, so every evaluator run died on HTTP 401
(``EVALUATOR_AUTH_FAILURE``) before a single case could be scored. The project file
is the credential of record, so it is now consulted first.

The environment is still a valid configuration source: it supplies anything the file
does not define, which is how containers and CI inject settings.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ai_market_monitor.core.config import Settings as AppSettings
from hm_chatbot_eval.config import Settings as EvalSettings
from hm_chatbot_eval.config import (
    discard_stale_process_openai_key,
    process_openai_key_overrides_dotenv,
)

DOTENV_KEY = "sk-proj-from-dotenv-key"
STALE_ENV_KEY = "sk-proj-stale-machine-scope-key"


@pytest.fixture()
def env_file(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        f"OPENAI_API_KEY={DOTENV_KEY}\nEVAL_MAX_CONCURRENCY=2\n",
        encoding="utf-8",
    )
    return path


def test_dotenv_key_beats_a_stale_environment_key(env_file: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", STALE_ENV_KEY)
    assert EvalSettings(_env_file=env_file).openai_api_key == DOTENV_KEY


def test_app_settings_use_the_same_precedence(env_file: Path, monkeypatch) -> None:
    """The backend must not 401 for the reason the evaluator did."""
    monkeypatch.setenv("OPENAI_API_KEY", STALE_ENV_KEY)
    secret = AppSettings(_env_file=env_file).openai_api_key
    assert secret is not None
    assert secret.get_secret_value() == DOTENV_KEY


def test_environment_still_supplies_values_absent_from_the_file(
    env_file: Path, monkeypatch
) -> None:
    """The container/CI path: no JUDGE_MODEL in the file, so the env provides it."""
    monkeypatch.setenv("JUDGE_MODEL", "gpt-5-from-env")
    assert EvalSettings(_env_file=env_file).judge_model == "gpt-5-from-env"


def test_non_credential_settings_keep_normal_environment_precedence(
    env_file: Path, monkeypatch
) -> None:
    """Only credentials are pinned to the file.

    Overriding an ordinary setting for one process is standard practice — the
    migration tests run ``DATABASE_URL=... alembic upgrade head`` against a temp
    database — so inverting precedence for everything silently broke them.
    """
    monkeypatch.setenv("EVAL_MAX_CONCURRENCY", "99")
    assert EvalSettings(_env_file=env_file).eval_max_concurrency == 99


def test_only_credential_fields_are_pinned_to_the_file(env_file: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", STALE_ENV_KEY)
    monkeypatch.setenv("EVAL_MAX_CONCURRENCY", "7")
    settings = EvalSettings(_env_file=env_file)
    assert settings.openai_api_key == DOTENV_KEY, "credential must come from .env"
    assert settings.eval_max_concurrency == 7, "ordinary setting must come from env"


def test_environment_is_the_only_source_when_no_file_exists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", STALE_ENV_KEY)
    missing = tmp_path / "absent.env"
    assert EvalSettings(_env_file=missing).openai_api_key == STALE_ENV_KEY


def test_explicit_arguments_still_win_over_the_file(env_file: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", STALE_ENV_KEY)
    override = EvalSettings(_env_file=env_file, openai_api_key="sk-proj-explicit")
    assert override.openai_api_key == "sk-proj-explicit"


def test_duplicate_key_detector_still_reports_the_mismatch(env_file: Path, monkeypatch) -> None:
    """The duplicate is now ignored rather than authoritative, but still worth flagging."""
    monkeypatch.setenv("OPENAI_API_KEY", STALE_ENV_KEY)
    assert process_openai_key_overrides_dotenv(env_file) is True

    monkeypatch.setenv("OPENAI_API_KEY", DOTENV_KEY)
    assert process_openai_key_overrides_dotenv(env_file) is False

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert process_openai_key_overrides_dotenv(env_file) is False


def test_stale_process_key_is_removed_before_a_cli_run(env_file: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", STALE_ENV_KEY)

    assert discard_stale_process_openai_key(env_file) is True
    assert "OPENAI_API_KEY" not in os.environ
    assert EvalSettings(_env_file=env_file).openai_api_key == DOTENV_KEY
