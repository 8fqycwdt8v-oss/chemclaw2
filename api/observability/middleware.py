"""HTTP middleware that binds a per-request id into log/metric context.

Honours an inbound `X-Request-ID` header so a caller (load balancer,
upstream service) can correlate logs across the hop. Otherwise mints a
uuid4. The bound id is echoed back in the response header so the same
id appears in client-side traces.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from api.observability.logging import bind_request_id, reset_request_id

logger = logging.getLogger(__name__)


_REQUEST_ID_HEADER = "X-Request-ID"
# Bound a request id to a reasonable length so a hostile caller can't
# stuff log lines with arbitrary content.
_MAX_REQUEST_ID_LEN = 64


def _normalise_inbound(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or len(value) > _MAX_REQUEST_ID_LEN:
        return None
    # Allow only safe characters so the id survives every log/metric
    # serialiser without surprises.
    if not all(c.isalnum() or c in "-_." for c in value):
        return None
    return value


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Bind X-Request-ID (or a fresh uuid4) into contextvars; emit access log."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        inbound = _normalise_inbound(request.headers.get(_REQUEST_ID_HEADER))
        request_id = inbound or uuid.uuid4().hex
        token = bind_request_id(request_id)
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.exception(
                "request_failed",
                extra={
                    "route": request.url.path,
                    "method": request.method,
                    "latency_ms": elapsed_ms,
                },
            )
            raise
        finally:
            reset_request_id(token)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        response.headers[_REQUEST_ID_HEADER] = request_id
        # Don't log /metrics or /api/health at INFO — they're polled
        # constantly by the platform's probe and would flood the log.
        if request.url.path not in {"/metrics", "/api/health", "/api/readiness"}:
            logger.info(
                "request_complete",
                extra={
                    "route": request.url.path,
                    "method": request.method,
                    "status": response.status_code,
                    "latency_ms": elapsed_ms,
                },
            )
        return response
