"""Structured-logging config.

`configure_logging()` is called once from the FastAPI lifespan. It
wires the root logger to write either plain-text (default, dev) or
single-line JSON (`LOG_FORMAT=json`, recommended in prod) records,
each carrying the contextvars-bound `request_id` so a multi-line
trace can be reassembled by ingesting only the matching id.

No new runtime deps — JSON is emitted with the stdlib `json` module so
deployments don't pay for `python-json-logger` when plain logs are fine.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

# Bound by the request-id middleware on every inbound HTTP request, and
# left empty for non-request log lines (lifespan, workers without their
# own context). `set(...)` returns a Token; reset that token in a finally
# block to scope the binding to the request.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Fields that the stdlib LogRecord already exposes; everything else on a
# record is treated as a user-supplied extra and emitted in the JSON
# payload.
_STANDARD_LOGRECORD_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
})


class _RequestIdFilter(logging.Filter):
    """Attach the contextvars-bound request_id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per record on a single line.

    Extras passed via `logger.info("...", extra={...})` are flattened
    into the top-level payload so log-aggregator queries don't have to
    descend into a nested dict.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Flatten extras. Keep value types JSON-serialisable; fall back
        # to str() so a stray Decimal/datetime doesn't drop the log line.
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOGRECORD_ATTRS or key in payload:
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = str(value)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def configure_logging(*, level: str | None = None, fmt: str | None = None) -> None:
    """Configure the root logger.

    LOG_LEVEL  default INFO. Override via env or kwarg.
    LOG_FORMAT default "plain" (human-readable). Set to "json" in prod
               so the log aggregator can index every field.

    Re-configurable: clears existing handlers so a test calling
    `configure_logging` after the lifespan has run reaches a clean state.
    """
    level_name = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    log_format = (fmt or os.environ.get("LOG_FORMAT") or "plain").lower()

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.addFilter(_RequestIdFilter())
    if log_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
    root.addHandler(handler)
    root.setLevel(level_name)
    # Quiet some chatter that doesn't add value at INFO.
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).setLevel("WARNING")


def get_request_id() -> str | None:
    """Read the current request id, if any. Workers and tests get None."""
    return request_id_var.get()


def bind_request_id(value: str) -> Any:
    """Bind a request id for the current context; return a token to reset."""
    return request_id_var.set(value)


def reset_request_id(token: Any) -> None:
    """Restore the previous request-id binding."""
    request_id_var.reset(token)


def log_extra(**kwargs: Any) -> Mapping[str, Any]:
    """Tiny helper so callers can write `logger.info("msg", extra=log_extra(user=u))`
    without having to import Mapping at every call site."""
    return kwargs
