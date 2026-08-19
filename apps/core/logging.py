"""Structured logging with automatic secret redaction.

Requirement: API keys, tokens and passwords must never reach a log file, a
console, or an error report — including when they appear inside an exception
message or a formatted URL. The filter below is attached to every handler.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
_REDACTED = "***REDACTED***"

# Keys whose *value* must always be masked, wherever they appear.
SENSITIVE_KEY_NAMES = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "secret_key",
    "authorization",
    "auth",
    "credential",
    "private_key",
    "session_key",
    "csrfmiddlewaretoken",
    "sifre",  # Turkish: password
    "parola",  # Turkish: password
)

_PATTERNS: tuple[re.Pattern[str], ...] = (
    # NVIDIA build.nvidia.com keys
    re.compile(r"nvapi-[A-Za-z0-9_\-]{20,}"),
    # Anthropic keys
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    # Generic OpenAI-style keys
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    # GitHub tokens
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    # AWS access key ids
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # Bearer / Basic auth headers
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{12,}"),
    # JWTs
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
    # key=value / "key": "value" forms for any sensitive key name
    re.compile(
        r"(?i)([\"']?(?:" + "|".join(SENSITIVE_KEY_NAMES) + r")[\"']?\s*[:=]\s*[\"']?)"
        r"([^\s,;)}\"']{4,})"
    ),
    # PostgreSQL / Redis DSN passwords:  scheme://user:PASSWORD@host
    re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^:/\s]+:)([^@/\s]+)(@)"),
)


def redact(text: str) -> str:
    """Return *text* with every recognised secret replaced."""
    if not text:
        return text
    result = text
    for pattern in _PATTERNS:
        if pattern.groups == 3:
            result = pattern.sub(rf"\1{_REDACTED}\3", result)
        elif pattern.groups == 2:
            result = pattern.sub(rf"\1{_REDACTED}", result)
        else:
            result = pattern.sub(_REDACTED, result)
    return result


def redact_mapping(data: Any, _depth: int = 0) -> Any:
    """Recursively redact a dict/list structure (used for audit payloads)."""
    if _depth > 8:
        return data
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            if any(name in str(key).lower() for name in SENSITIVE_KEY_NAMES):
                out[key] = _REDACTED
            else:
                out[key] = redact_mapping(value, _depth + 1)
        return out
    if isinstance(data, (list, tuple)):
        return type(data)(redact_mapping(v, _depth + 1) for v in data)
    if isinstance(data, str):
        return redact(data)
    return data


class SecretRedactionFilter(logging.Filter):
    """Scrubs secrets from the message and every positional argument."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = redact_mapping(record.args)
                else:
                    record.args = tuple(
                        redact(a) if isinstance(a, str) else a for a in record.args
                    )
            # Exception text can carry a URL with an embedded key.
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
        except Exception:  # noqa: BLE001, S110 - a logging filter must never raise; deliberate best-effort cleanup; a failure here must not break the caller  # nosec B110
            pass
        return True


class JsonFormatter(logging.Formatter):
    """Emits one JSON object per line — suitable for log shipping."""

    RESERVED = {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "message", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "module": record.module,
            "line": record.lineno,
        }
        # Anything passed via `extra=` is included.
        for key, value in record.__dict__.items():
            if key not in self.RESERVED and not key.startswith("_"):
                try:
                    json.dumps(value)
                    payload[key] = redact_mapping(value)
                except (TypeError, ValueError):
                    payload[key] = redact(str(value))
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)
