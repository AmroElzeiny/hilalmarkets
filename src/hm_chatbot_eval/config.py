from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


def _default_model_pricing() -> dict[str, dict[str, dict[str, float]]]:
    # OpenAI public API prices per 1M tokens, reviewed 2026-07-23. Explicit
    # TEST_AI_* / TARGET_* rates still override this catalog.
    return {
        "gpt-5-mini": {
            "standard": {"input": 0.25, "cached_input": 0.025, "output": 2.0},
            "flex": {"input": 0.125, "cached_input": 0.0125, "output": 1.0},
        },
        "gpt-5.4-mini": {
            "standard": {"input": 0.75, "cached_input": 0.075, "output": 4.5},
            "flex": {"input": 0.375, "cached_input": 0.0375, "output": 2.25},
        },
        "gpt-5.4-nano": {
            "standard": {"input": 0.2, "cached_input": 0.02, "output": 1.25},
            "flex": {"input": 0.1, "cached_input": 0.01, "output": 0.625},
        },
    }


#: Fields whose value in ``.env`` outranks the ambient environment. Kept to
#: credentials, where a stale machine-wide copy causes a hard auth failure that is
#: expensive to diagnose. Non-secret settings keep normal env-var precedence.
CREDENTIAL_FIELDS: frozenset[str] = frozenset({"openai_api_key"})


class CredentialDotEnvSource(PydanticBaseSettingsSource):
    """Supplies only the credential fields, and only from the ``.env`` file."""

    def __init__(self, dotenv_settings: PydanticBaseSettingsSource) -> None:
        super().__init__(dotenv_settings.settings_cls)
        self._dotenv = dotenv_settings

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        raise NotImplementedError

    def __call__(self) -> dict[str, Any]:
        values = self._dotenv()
        return {
            name: value for name, value in values.items() if name.casefold() in CREDENTIAL_FIELDS
        }


def process_openai_key_overrides_dotenv(env_file: str | Path = ".env") -> bool:
    """Detect a different process-level key without exposing either credential."""
    process_value = os.environ.get("OPENAI_API_KEY", "").strip()
    path = Path(env_file)
    if not process_value or not path.is_file():
        return False
    dotenv_value = str(dotenv_values(path).get("OPENAI_API_KEY") or "").strip()
    return bool(dotenv_value) and not hmac.compare_digest(process_value, dotenv_value)


def discard_stale_process_openai_key(env_file: str | Path = ".env") -> bool:
    """Remove an inherited key when this project's credential file is authoritative.

    The process environment cannot be changed in its parent terminal, but clearing the
    conflicting value here keeps every evaluator subprocess and HTTP client isolated
    from a stale credential. The project `.env` remains the only evaluator key source.
    """
    if not process_openai_key_overrides_dotenv(env_file):
        return False
    os.environ.pop("OPENAI_API_KEY", None)
    return True


_ISOLATED_TARGET_ENVIRONMENT = (
    "TARGET_BACKEND_BASE_URL",
    "TARGET_BACKEND_HEALTH_URL",
    "TARGET_UI_URL",
)


def discard_stale_isolated_target_overrides() -> tuple[str, ...]:
    """Remove target addresses leaked by the historical isolated-run wrapper.

    The original PowerShell wrapper set its default isolated target (port 8124) in
    the *caller's* process and did not restore it.  A later ordinary evaluator run
    then ignored the project's port-8000 configuration and failed readiness against
    the already-stopped test server.  Current wrappers set an active marker and
    restore their environment; this cleanup exists for terminals contaminated by an
    older invocation.

    Only the wrapper's exact historical localhost port is removed.  Arbitrary
    operator/CI target overrides keep normal environment precedence.
    """

    if os.environ.get("HM_ISOLATED_EVALUATOR_ACTIVE", "").strip() == "1":
        return ()
    removed: list[str] = []
    for name in _ISOLATED_TARGET_ENVIRONMENT:
        value = os.environ.get(name, "").strip().rstrip("/").casefold()
        if value == "http://127.0.0.1:8124" or value.startswith(
            "http://127.0.0.1:8124/"
        ):
            os.environ.pop(name, None)
            removed.append(name)
    return tuple(removed)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Let ``.env`` win over the ambient environment for credentials only.

        A stale machine-wide ``OPENAI_API_KEY`` used to outrank the project's own
        file and kill the run on HTTP 401. The project file is the credential of
        record, so it is consulted first *for those fields*.

        Everything else keeps normal precedence, because overriding a setting for
        one process — ``DATABASE_URL=... alembic upgrade head`` — is ordinary and
        must keep working.
        """
        return (
            init_settings,
            CredentialDotEnvSource(dotenv_settings),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    openai_api_key: str = ""
    test_ai_model: str = "gpt-5-mini"
    test_ai_reasoning: str = "low"
    test_ai_service_tier: str = "flex"
    test_ai_base_url: str = "https://api.openai.com/v1"
    test_ai_timeout_seconds: float = 120
    test_ai_max_output_tokens: int = 1200
    eval_challenger_max_message_chars: int = Field(default=1200, ge=200, le=1600)
    test_ai_input_usd_per_1m: float = 0
    test_ai_cached_input_usd_per_1m: float = 0
    test_ai_output_usd_per_1m: float = 0
    judge_model: str = ""
    judge_reasoning: str = "low"
    judge_service_tier: str = "flex"
    judge_max_output_tokens: int = 4000

    eval_output_dir: Path = Path("chatbot_eval_runs")
    eval_cache_db: Path = Path(".chatbot_eval_cache.sqlite3")
    eval_budget_usd: float = Field(default=2.5, gt=0)
    eval_budget_profile_max_usd: float = Field(default=2.5, gt=0, le=3)
    eval_max_concurrency: int = 2
    eval_default_tests_per_topic: int = 24
    eval_default_max_turns: int = 8
    eval_readiness_attempts: int = Field(default=2, ge=1, le=3)
    eval_circuit_breaker_failures: int = Field(default=2, ge=1, le=5)
    eval_redact_keys: str = "password,token,secret,api_key,authorization,cookie,set-cookie"

    target_mode: str = "backend"
    target_name: str = "HilalMarkets AI Setup Chat"
    target_version_label: str = "current"
    target_strategy_schema_file: Path | None = Path(
        "tests/evaluator/contracts/strategy_definition.schema.json"
    )
    target_schema_file: Path | None = Path(
        "tests/evaluator/contracts/setup_chat_evaluation_contract.schema.json"
    )
    target_field_map_file: Path | None = Path("tests/evaluator/contracts/field_map.json")
    target_field_map_json: str = ""

    target_backend_adapter: str = "hilalmarkets"
    target_backend_base_url: str = "http://127.0.0.1:8000"
    target_backend_session_path: str = "/api/v1/dashboard/setup-chat/sessions"
    target_backend_message_path_template: str = (
        "/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages"
    )
    target_backend_login_path: str = "/signin"
    target_backend_email: str = ""
    target_backend_password: str = ""
    target_session_cookie_name: str = "amm_session"
    target_session_cookie: SecretStr | None = None
    target_backend_url: str = "http://127.0.0.1:8000/api/v1/dashboard/setup-chat/sessions"
    target_backend_method: str = "POST"
    target_backend_health_url: str = "http://127.0.0.1:8000/health"
    target_backend_reset_url: str = ""
    target_backend_auth_header: str = "Authorization"
    target_backend_auth_token: str = ""
    target_backend_headers_json: str = "{}"
    target_backend_request_template_json: str = (
        '{"message":"{{message}}","conversation_id":"{{conversation_id}}"}'
    )
    target_backend_response_text_path: str = "message"
    target_backend_response_object_path: str = "compiled_strategy"
    target_backend_conversation_id_path: str = "conversation_id"
    target_backend_model_path: str = "model"
    target_backend_usage_path: str = "usage"
    target_backend_timeout_seconds: float = 90
    target_fault_header: str = "X-HM-Eval-Fault"
    target_version_header: str = "X-HM-Eval-Target-Version"
    target_model: str = "gpt-5.4-nano"
    target_service_tier: str = "standard"
    target_input_usd_per_1m: float = 0
    target_cached_input_usd_per_1m: float = 0
    target_output_usd_per_1m: float = 0
    eval_model_pricing_usd_per_million: dict[str, dict[str, dict[str, float]]] = Field(
        default_factory=_default_model_pricing
    )

    target_ui_url: str = "http://127.0.0.1:8000/dashboard/strategies/new"
    target_ui_login_url: str = "http://127.0.0.1:8000/signin"
    target_ui_email: str = ""
    target_ui_password: str = ""
    target_ui_storage_state: str = ""
    target_ui_headless: bool = True
    target_ui_expected_marker: str = '[data-evaluator-target="authenticated-ai-setup-chat"]'
    target_ui_forbidden_markers: str = (
        '[data-evaluator-target="public-support-chat"],[data-public-support-page]'
    )
    target_ui_email_selector: str = "input[type=email]"
    target_ui_password_selector: str = "input[type=password]"
    target_ui_login_submit_selector: str = "button[type=submit]"
    target_ui_input_selector: str = "[data-testid=ai-setup-input]"
    target_ui_send_selector: str = "[data-testid=ai-setup-send]"
    target_ui_assistant_message_selector: str = "[data-testid=ai-setup-assistant-message]"
    target_ui_new_chat_selector: str = "[data-testid=new-ai-setup-chat]"
    # Playwright glob routes need ``**`` for the scheme, host, and nested path.
    # A leading single ``*`` still let Python's ``fnmatch`` observe a response, but
    # did not reliably intercept the browser request to attach a test-only fault.
    target_ui_chat_api_pattern: str = "**/api/v1/dashboard/setup-chat/sessions/*/messages"
    target_ui_response_object_path: str = "evaluation_contract"
    target_ui_timeout_ms: int = 90000
    target_ui_screenshots: bool = True
    target_variants_json: str = "[]"

    @field_validator(
        "target_strategy_schema_file",
        "target_schema_file",
        "target_field_map_file",
        mode="before",
    )
    @classmethod
    def empty_path_to_none(cls, value):
        return None if value in (None, "") else value

    @field_validator("target_session_cookie", mode="before")
    @classmethod
    def empty_session_cookie_to_none(cls, value):
        if value is None:
            return None
        if isinstance(value, SecretStr):
            return value if value.get_secret_value().strip() else None
        return value if str(value).strip() else None

    @field_validator("eval_default_tests_per_topic")
    @classmethod
    def validate_case_count(cls, value: int) -> int:
        if not 20 <= value <= 30:
            raise ValueError("EVAL_DEFAULT_TESTS_PER_TOPIC must be between 20 and 30")
        return value

    @property
    def judge_model_resolved(self) -> str:
        return self.judge_model or self.test_ai_model

    @property
    def redacted_keys(self) -> set[str]:
        return {x.strip().lower() for x in self.eval_redact_keys.split(",") if x.strip()}

    @property
    def target_field_map(self) -> dict[str, Any]:
        if self.target_field_map_json:
            value = json.loads(self.target_field_map_json)
        elif self.target_field_map_file:
            value = json.loads(Path(self.target_field_map_file).read_text(encoding="utf-8"))
        else:
            value = {}
        if not isinstance(value, dict):
            raise ValueError("TARGET_FIELD_MAP_JSON must be an object")
        return {str(key): item for key, item in value.items()}

    @property
    def backend_headers(self) -> dict[str, str]:
        value = json.loads(self.target_backend_headers_json or "{}")
        if self.target_backend_auth_token:
            value[self.target_backend_auth_header] = self.target_backend_auth_token
        return {str(k): str(v) for k, v in value.items()}

    @property
    def target_authentication_configured(self) -> bool:
        cookie = (
            self.target_session_cookie.get_secret_value().strip()
            if self.target_session_cookie is not None
            else ""
        )
        credentials = self.target_backend_email.strip() and self.target_backend_password
        return bool(cookie or credentials)

    @property
    def target_variants(self) -> list[dict[str, Any]]:
        value = json.loads(self.target_variants_json or "[]")
        if not isinstance(value, list):
            raise ValueError("TARGET_VARIANTS_JSON must be a list")
        return value or [{"name": self.target_version_label}]

    @staticmethod
    def _normalized_tier(value: str) -> str:
        return "standard" if value in {"auto", "default", "standard"} else value

    def model_pricing(self, model: str, service_tier: str) -> dict[str, float]:
        model_rates = self.eval_model_pricing_usd_per_million.get(model)
        tier = self._normalized_tier(service_tier)
        rates = model_rates.get(tier) if model_rates else None
        if not rates or any(float(rates.get(key, 0)) <= 0 for key in ("input", "output")):
            raise ValueError(
                f"No positive evaluator pricing is configured for model={model}, tier={tier}"
            )
        return {
            "input": float(rates["input"]),
            "cached_input": float(rates.get("cached_input", rates["input"])),
            "output": float(rates["output"]),
        }

    @property
    def test_ai_pricing(self) -> dict[str, float]:
        return self.evaluator_pricing(self.test_ai_model, self.test_ai_service_tier)

    def evaluator_pricing(self, model: str, service_tier: str) -> dict[str, float]:
        explicit = {
            "input": self.test_ai_input_usd_per_1m,
            "cached_input": self.test_ai_cached_input_usd_per_1m,
            "output": self.test_ai_output_usd_per_1m,
        }
        if (
            model == self.test_ai_model
            and self._normalized_tier(service_tier)
            == self._normalized_tier(self.test_ai_service_tier)
            and explicit["input"] > 0
            and explicit["output"] > 0
        ):
            if explicit["cached_input"] <= 0:
                explicit["cached_input"] = explicit["input"]
            return explicit
        return self.model_pricing(model, service_tier)

    def target_pricing(self, model: str | None = None) -> dict[str, float]:
        explicit = {
            "input": self.target_input_usd_per_1m,
            "cached_input": self.target_cached_input_usd_per_1m,
            "output": self.target_output_usd_per_1m,
        }
        if explicit["input"] > 0 and explicit["output"] > 0:
            if explicit["cached_input"] <= 0:
                explicit["cached_input"] = explicit["input"]
            return explicit
        return self.model_pricing(model or self.target_model, self.target_service_tier)

    def load_schema(self) -> dict[str, Any] | None:
        if not self.target_schema_file:
            return None
        path = Path(self.target_schema_file)
        return json.loads(path.read_text(encoding="utf-8"))
