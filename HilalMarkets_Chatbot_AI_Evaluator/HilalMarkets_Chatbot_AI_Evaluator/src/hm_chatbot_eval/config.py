from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    openai_api_key: str = ""
    test_ai_model: str = "gpt-5-mini"
    test_ai_reasoning: str = "low"
    test_ai_service_tier: str = "flex"
    test_ai_base_url: str = "https://api.openai.com/v1"
    test_ai_timeout_seconds: float = 120
    test_ai_max_output_tokens: int = 1200
    test_ai_input_usd_per_1m: float = 0
    test_ai_cached_input_usd_per_1m: float = 0
    test_ai_output_usd_per_1m: float = 0
    judge_model: str = ""
    judge_reasoning: str = "low"
    judge_service_tier: str = "flex"
    judge_max_output_tokens: int = 1600

    eval_output_dir: Path = Path("chatbot_eval_runs")
    eval_cache_db: Path = Path(".chatbot_eval_cache.sqlite3")
    eval_budget_usd: float = 25
    eval_max_concurrency: int = 2
    eval_default_tests_per_topic: int = 24
    eval_default_max_turns: int = 8
    eval_redact_keys: str = "password,token,secret,api_key,authorization,cookie,set-cookie"

    target_mode: str = "backend"
    target_name: str = "HilalMarkets AI Setup Chat"
    target_version_label: str = "current"
    target_schema_file: Path | None = None
    target_field_map_json: str = "{}"

    target_backend_url: str = "http://127.0.0.1:8000/api/watchlists/ai-chat"
    target_backend_method: str = "POST"
    target_backend_health_url: str = "http://127.0.0.1:8000/health"
    target_backend_reset_url: str = ""
    target_backend_auth_header: str = "Authorization"
    target_backend_auth_token: str = ""
    target_backend_headers_json: str = "{}"
    target_backend_request_template_json: str = '{"message":"{{message}}","conversation_id":"{{conversation_id}}"}'
    target_backend_response_text_path: str = "message"
    target_backend_response_object_path: str = "compiled_strategy"
    target_backend_conversation_id_path: str = "conversation_id"
    target_backend_model_path: str = "model"
    target_backend_usage_path: str = "usage"
    target_backend_timeout_seconds: float = 90
    target_fault_header: str = "X-HM-Eval-Fault"
    target_input_usd_per_1m: float = 0
    target_cached_input_usd_per_1m: float = 0
    target_output_usd_per_1m: float = 0

    target_ui_url: str = "http://127.0.0.1:8000/dashboard/watchlists/new"
    target_ui_login_url: str = "http://127.0.0.1:8000/signin"
    target_ui_email: str = ""
    target_ui_password: str = ""
    target_ui_storage_state: str = ""
    target_ui_headless: bool = True
    target_ui_expected_marker: str = "AI Setup Chat"
    target_ui_forbidden_markers: str = "support agent,customer support,help widget"
    target_ui_email_selector: str = "input[type=email]"
    target_ui_password_selector: str = "input[type=password]"
    target_ui_login_submit_selector: str = "button[type=submit]"
    target_ui_input_selector: str = "[data-testid=ai-setup-input],textarea,[contenteditable=true]"
    target_ui_send_selector: str = "[data-testid=ai-setup-send],button[type=submit]"
    target_ui_assistant_message_selector: str = "[data-role=assistant],[data-message-author-role=assistant],.assistant-message"
    target_ui_new_chat_selector: str = "[data-testid=new-ai-setup-chat]"
    target_ui_chat_api_pattern: str = "*/api/*chat*"
    target_ui_response_object_path: str = "compiled_strategy"
    target_ui_timeout_ms: int = 90000
    target_ui_screenshots: bool = True
    target_variants_json: str = "[]"

    @field_validator("target_schema_file", mode="before")
    @classmethod
    def empty_path_to_none(cls, value):
        return None if value in (None, "") else value

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
    def target_field_map(self) -> dict[str, str]:
        value = json.loads(self.target_field_map_json or "{}")
        if not isinstance(value, dict):
            raise ValueError("TARGET_FIELD_MAP_JSON must be an object")
        return {str(k): str(v) for k, v in value.items()}

    @property
    def backend_headers(self) -> dict[str, str]:
        value = json.loads(self.target_backend_headers_json or "{}")
        if self.target_backend_auth_token:
            value[self.target_backend_auth_header] = self.target_backend_auth_token
        return {str(k): str(v) for k, v in value.items()}

    @property
    def target_variants(self) -> list[dict[str, Any]]:
        value = json.loads(self.target_variants_json or "[]")
        if not isinstance(value, list):
            raise ValueError("TARGET_VARIANTS_JSON must be a list")
        return value or [{"name": self.target_version_label}]

    def load_schema(self) -> dict[str, Any] | None:
        if not self.target_schema_file:
            return None
        path = Path(self.target_schema_file)
        return json.loads(path.read_text(encoding="utf-8"))
