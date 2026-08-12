"""The session must not be handed the product's credentials.

The permission policy screens what a session *types*. This screens what it *has*. The
second is the stronger of the two — a session that never receives ``DATABASE_URL`` cannot
reach a database however cleverly it words the command — and it is the layer that keeps
working when a policy pattern turns out to have a hole in it.

Tested as a rule over families of names, not as a list of the variables that happen to
exist today. A new provider added next year brings a new secret with it, and the test
that only knew about Stripe would pass while the new key went straight into the session.
"""

from __future__ import annotations

import pytest

from hm_oi.launch import build_command, interpreter_executable, profile_path, scrub_environment
from hm_oi.paths import repo_root

ROOT = repo_root()


PRODUCT_SECRETS = [
    "DATABASE_URL",
    "REDIS_URL",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "APP_SECRET_KEY",
    "SESSION_SECRET",
    "OPENAI_API_KEY",
    "SENTRY_DSN",
    "GITHUB_TOKEN",
    "AWS_SECRET_ACCESS_KEY",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "NOWPAYMENTS_API_KEY",
    "CREEM_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "WHATSAPP_ACCESS_TOKEN",
    "SMTP_PASSWORD",
    "TWILIO_AUTH_TOKEN",
    "SOME_NEW_PROVIDER_API_KEY",
    "ANOTHER_SERVICE_PRIVATE_KEY",
    "X_WEBHOOK_SECRET",
    "DB_PASSWORD",
    "ADMIN_CREDENTIAL",
]


@pytest.mark.parametrize("name", PRODUCT_SECRETS)
def test_every_product_secret_is_taken_away(name: str) -> None:
    kept, removed = scrub_environment({name: "live-value", "PATH": "/usr/bin"})
    assert name not in kept, f"{name} was handed to the session"
    assert name in removed
    assert "live-value" not in str(kept)


@pytest.mark.parametrize(
    "name",
    ["PATH", "HOME", "USERPROFILE", "TEMP", "SYSTEMROOT", "LANG", "TERM", "PYTHONPATH"],
)
def test_the_shell_still_works(name: str) -> None:
    """Strip too much and nothing runs at all, which is its own kind of failure."""

    kept, _ = scrub_environment({name: "value"})
    assert kept.get(name) == "value"


@pytest.mark.parametrize(
    "name",
    [
        "HM_OI_API_KEY",
        "HM_OI_REPO_ROOT",
        "HM_OI_TIER",
        "HM_OI_SESSION_BUDGET_USD",
        "HM_OI_NORMAL_MODEL",
    ],
)
def test_the_assistants_own_configuration_survives(name: str) -> None:
    """``HM_OI_API_KEY`` contains ``API_KEY`` and must still get through.

    Checked because the obvious implementation — one list of forbidden fragments — takes
    the assistant's own key away and leaves it unable to start, with a message that
    blames a missing key the engineer can plainly see they set.
    """

    kept, _ = scrub_environment({name: "value"})
    assert kept.get(name) == "value"


def test_the_product_key_is_shared_only_when_asked_for() -> None:
    """Sharing is possible, never accidental."""

    environment = {"OPENAI_API_KEY": "sk-product", "PATH": "/usr/bin"}

    kept, removed = scrub_environment(environment)
    assert "OPENAI_API_KEY" not in kept
    assert "OPENAI_API_KEY" in removed

    shared, _ = scrub_environment(
        {**environment, "HM_OI_ALLOW_SHARED_KEY": "1"}
    )
    assert shared["OPENAI_API_KEY"] == "sk-product"


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "maybe"])
def test_anything_that_is_not_a_clear_yes_keeps_the_key_hidden(value: str) -> None:
    kept, _ = scrub_environment(
        {"OPENAI_API_KEY": "sk-product", "HM_OI_ALLOW_SHARED_KEY": value}
    )
    assert "OPENAI_API_KEY" not in kept


def test_the_scrub_reports_names_only_never_values() -> None:
    """The launcher prints how many credentials it removed. It must not print them."""

    _, removed = scrub_environment({"STRIPE_SECRET_KEY": "sk_live_do_not_print"})
    assert removed == ("STRIPE_SECRET_KEY",)
    assert "sk_live_do_not_print" not in str(removed)


def test_the_launcher_points_at_the_repository_profile() -> None:
    assert profile_path(ROOT).exists()
    assert profile_path(ROOT).name == "hilalmarkets_profile.py"


def test_the_launcher_says_what_to_do_when_open_interpreter_is_missing(tmp_path) -> None:
    """A missing install must produce an instruction, not a stack trace."""

    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "alembic.ini").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()

    assert interpreter_executable(tmp_path) is None
    with pytest.raises(FileNotFoundError, match="bootstrap"):
        build_command(tmp_path)


def test_the_built_command_loads_this_repositorys_profile() -> None:
    """Only meaningful once Open Interpreter is installed; skipped otherwise."""

    if interpreter_executable(ROOT) is None:
        pytest.skip("Open Interpreter is not installed in .oi-venv")

    command = build_command(ROOT)
    assert command[1] == "--profile"
    assert command[2] == str(profile_path(ROOT))
