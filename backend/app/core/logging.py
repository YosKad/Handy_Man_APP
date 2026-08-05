"""Structured JSON logging with request correlation and redaction.

Two guarantees this module exists to provide:

* every log line carries the request id, so a user-reported problem can be
  traced across API, job and provider call;
* nothing sensitive is ever written — tokens, prompt bodies containing user
  content, image bytes and email addresses are redacted by a filter rather than
  by developer discipline.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)

# Attributes of logging.LogRecord that are not "extra" context.
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "stacklevel",
        "thread",
        "threadName",
        "taskName",
    }
)

_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bBearer\s+[\w\-.]+", re.IGNORECASE), "Bearer [redacted]"),
    (re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9\-_]{12,}"), "[redacted-api-key]"),
    (re.compile(r"\beyJ[\w\-]+\.[\w\-]+\.[\w\-]+"), "[redacted-jwt]"),
    (re.compile(r"data:image/[a-z+]+;base64,[A-Za-z0-9+/=]+"), "[redacted-image]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[redacted-email]"),
)

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "secret",
        "secret_key",
        "image_bytes",
        "prompt",
        "messages",
        "content_b64",
    }
)


def redact(value: str) -> str:
    for pattern, replacement in _REDACT_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _redact_value(key: str, value: Any) -> Any:
    if key.lower() in _SENSITIVE_KEYS:
        return "[redacted]"
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(key, item) for item in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"[{len(bytes(value))} bytes]"
    return value


class JsonFormatter(logging.Formatter):
    """Renders records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }

        request_id = _request_id.get()
        if request_id:
            payload["request_id"] = request_id
        user_id = _user_id.get()
        if user_id:
            payload["user_id"] = user_id

        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = _redact_value(key, value)

        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Install the root handler. Idempotent — safe to call from tests."""
    root = logging.getLogger()
    root.setLevel(level)
    for existing in list(root.handlers):
        root.removeHandler(existing)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else logging.Formatter("%(levelname)-8s %(name)s: %(message)s")
    )
    root.addHandler(handler)

    # uvicorn duplicates access logs into its own handlers; route them here.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True

    # SQLAlchemy is chatty at INFO and can echo parameter values.
    logging.getLogger("sqlalchemy.engine").setLevel("WARNING")


# ------------------------------------------------------------------ context


def new_request_id() -> str:
    return uuid.uuid4().hex


def set_request_context(request_id: str, user_id: str | None = None) -> None:
    _request_id.set(request_id)
    _user_id.set(user_id)


def set_request_user(user_id: str | None) -> None:
    _user_id.set(user_id)


def get_request_id() -> str | None:
    return _request_id.get()


def clear_request_context() -> None:
    _request_id.set(None)
    _user_id.set(None)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
