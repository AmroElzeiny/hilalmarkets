import ipaddress
from dataclasses import dataclass
from importlib import metadata
from urllib.parse import urlparse


class SecurityReviewError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DependencyReport:
    package_count: int
    unpinned_packages: list[str]
    packages: list[dict[str, str]]


class SecurityReviewService:
    SENSITIVE_KEYS = {
        "api_key",
        "authorization",
        "binance_api_key",
        "binance_api_secret",
        "billing_webhook_secret",
        "client_secret",
        "nowpayments_api_key",
        "openai_api_key",
        "password",
        "private_key",
        "seed_phrase",
        "secret",
        "stripe_secret_key",
        "telegram_webhook_secret",
        "telegram_bot_token",
        "token",
    }
    SENSITIVE_KEY_FRAGMENTS = (
        "api_key",
        "authorization",
        "client_secret",
        "password",
        "private_key",
        "secret",
        "seed",
        "token",
        "webhook",
    )
    ALLOWED_UPLOAD_TYPES = {"image/png", "image/jpeg", "image/webp"}
    BLOCKED_UPLOAD_EXTENSIONS = {".exe", ".bat", ".cmd", ".scr", ".js", ".ps1", ".sh"}

    def dependency_inventory(self) -> DependencyReport:
        packages = sorted(
            [
                {"name": dist.metadata["Name"], "version": dist.version}
                for dist in metadata.distributions()
            ],
            key=lambda item: item["name"].casefold(),
        )
        return DependencyReport(
            package_count=len(packages), unpinned_packages=[], packages=packages
        )

    def validate_external_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"https", "http"}:
            raise SecurityReviewError("invalid_url_scheme", "Only HTTP and HTTPS URLs are allowed.")
        if not parsed.hostname:
            raise SecurityReviewError("invalid_url_host", "URL host is required.")
        host = parsed.hostname.casefold()
        if host in {"localhost", "metadata.google.internal"} or host.endswith(".local"):
            raise SecurityReviewError("blocked_private_host", "Private hosts are not allowed.")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return
        if address.is_private or address.is_loopback or address.is_link_local:
            raise SecurityReviewError("blocked_private_ip", "Private IP URLs are not allowed.")

    def validate_upload(self, *, filename: str, content_type: str, size_bytes: int) -> None:
        lowered = filename.casefold()
        if any(lowered.endswith(extension) for extension in self.BLOCKED_UPLOAD_EXTENSIONS):
            raise SecurityReviewError("blocked_file_type", "Executable uploads are not allowed.")
        if content_type not in self.ALLOWED_UPLOAD_TYPES:
            raise SecurityReviewError(
                "unsupported_content_type", "Unsupported upload content type."
            )
        if size_bytes > 5 * 1024 * 1024:
            raise SecurityReviewError("file_too_large", "Uploads are limited to 5 MB.")

    def validate_strategy_source(self, source_text: str) -> None:
        lowered = source_text.casefold()
        blocked = ["eval(", "exec(", "__import__", "subprocess", "open(", "os.system"]
        if any(term in lowered for term in blocked):
            raise SecurityReviewError(
                "unsafe_strategy_source",
                "Strategy input cannot execute code or access the server runtime.",
            )

    def redact(self, payload):
        if isinstance(payload, dict):
            return {
                key: "[redacted]" if self._sensitive_key(key) else self.redact(value)
                for key, value in payload.items()
            }
        if isinstance(payload, list):
            return [self.redact(value) for value in payload]
        if isinstance(payload, str) and self._looks_like_secret(payload):
            return "[redacted]"
        return payload

    @classmethod
    def _sensitive_key(cls, key: str) -> bool:
        lowered = key.casefold()
        return lowered in cls.SENSITIVE_KEYS or any(
            fragment in lowered for fragment in cls.SENSITIVE_KEY_FRAGMENTS
        )

    @staticmethod
    def _looks_like_secret(value: str) -> bool:
        if len(value) < 24:
            return False
        lowered = value.casefold()
        prefixes = ("sk-", "xoxb-", "bot", "whsec_", "rk_live_", "pk_live_")
        if lowered.startswith(prefixes):
            return True
        has_digit = any(character.isdigit() for character in value)
        has_alpha = any(character.isalpha() for character in value)
        return has_digit and has_alpha and len(set(value)) > 12
