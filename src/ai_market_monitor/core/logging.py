import logging
import re
from typing import Any

import structlog

from ai_market_monitor.services.security_review import SecurityReviewService


def redact_sensitive_values(_logger, _method_name, event_dict):
    return SecurityReviewService().redact(event_dict)


_TELEGRAM_BOT_URL_RE = re.compile(r"(https://api\.telegram\.org/bot)[^/\s\"']+")
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+")


def _redact_log_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    redacted = _TELEGRAM_BOT_URL_RE.sub(r"\1[redacted]", value)
    redacted = _BEARER_RE.sub(r"\1[redacted]", redacted)
    return redacted


class SensitiveLogFilter(logging.Filter):
    """Redact secrets from standard-library log records before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_log_text(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_log_text(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: _redact_log_text(value) for key, value in record.args.items()}
        else:
            record.args = _redact_log_text(record.args)
        return True


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", level=level.upper())
    redaction_filter = SensitiveLogFilter()
    root_logger = logging.getLogger()
    root_logger.addFilter(redaction_filter)
    for handler in root_logger.handlers:
        handler.addFilter(redaction_filter)
    for logger_name in ("httpx", "httpcore"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
        logging.getLogger(logger_name).addFilter(redaction_filter)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            redact_sensitive_values,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
    )
