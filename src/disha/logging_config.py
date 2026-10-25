"""Disha logging: text for dev, JSON for production.

Configured once at CLI entry. Other modules use `logger = logging.getLogger(__name__)`
and pass structured fields via `extra={...}`. The JSON formatter emits ts/level/
logger/event/extras flat, suitable for ingestion by Loki/Datadog/etc.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

_RESERVED = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "message",
}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k in _RESERVED:
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_TEXT_FMT = "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s"

# Do NOT pass any of these as `extra={...}` keys — Logger.makeRecord raises
# KeyError on collision with built-in LogRecord attributes. Prefix with `n_`
# for counts, `t_` for timestamps, etc.
_RESERVED_EXTRA_NAMES = frozenset(_RESERVED)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Idempotent: replaces existing handlers on the `disha` root logger."""
    handler = logging.StreamHandler()
    handler.setFormatter(
        _JsonFormatter() if fmt == "json" else logging.Formatter(_TEXT_FMT, "%H:%M:%S")
    )
    root = logging.getLogger("disha")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.propagate = False
