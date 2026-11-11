"""Per-request correlation IDs for log tracing.

Every response carries ``X-Request-ID`` (echoing an incoming one), and the
access log line includes it: when a user reports a failed operation, the id
pins down the exact log lines for that request.
"""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.logger import logger

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Stamp each request with a short id and log the outcome."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        started = time.perf_counter()
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            status_code = response.status_code if response is not None else 500
            logger.info(
                f"rid={request_id} {request.method} {request.url.path} "
                f"-> {status_code} ({duration_ms:.0f}ms)"
            )
            if response is not None:
                response.headers[REQUEST_ID_HEADER] = request_id
