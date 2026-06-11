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
from api.observability.metrics import (
    http_request_duration_seconds,
    http_requests_total,
)

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


def _metric_route(request: Request) -> str:
    """Route label for Prometheus: the matched route *template*, not the raw path.

    Raw paths embed UUIDs/slugs (`/api/campaigns/<uuid>`), so labelling by
    them grows metric cardinality without bound — and any unauthenticated
    scanner probing random paths would mint a new label per probe. The router
    sets `scope["route"]` once a route matches; unmatched requests (404s,
    probes) collapse into a single "unmatched" label.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return str(path) if path else "unmatched"


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
        # Pull the path early so the metric label is set even when
        # call_next raises (otherwise the histogram observation in
        # `finally` would refer to the variable before it's defined).
        route = request.url.path
        method = request.method
        try:
            try:
                response = await call_next(request)
            except Exception:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                logger.exception(
                    "request_failed",
                    extra={"route": route, "method": method, "latency_ms": elapsed_ms},
                )
                # 500 is a fair label for an uncaught exception — the
                # actual response Starlette returns will also be 500.
                # Routing has already run by the time an endpoint raises,
                # so the route template is available here too.
                metric_route = _metric_route(request)
                http_requests_total.labels(route=metric_route, method=method, status=500).inc()
                http_request_duration_seconds.labels(route=metric_route, method=method).observe(
                    time.monotonic() - start
                )
                raise
            elapsed = time.monotonic() - start
            elapsed_ms = int(elapsed * 1000)
            response.headers[_REQUEST_ID_HEADER] = request_id
            metric_route = _metric_route(request)
            http_requests_total.labels(
                route=metric_route, method=method, status=response.status_code
            ).inc()
            http_request_duration_seconds.labels(route=metric_route, method=method).observe(elapsed)
            # Don't log /metrics or the health probes at INFO — they're polled
            # constantly by the platform and would flood the log. The access
            # log MUST land before the `finally` resets the contextvar, or
            # the bound request_id is gone by the time _RequestIdFilter reads it.
            if route not in {"/metrics", "/api/health", "/api/readiness"}:
                logger.info(
                    "request_complete",
                    extra={
                        "route": route,
                        "method": method,
                        "status": response.status_code,
                        "latency_ms": elapsed_ms,
                    },
                )
            return response
        finally:
            reset_request_id(token)
