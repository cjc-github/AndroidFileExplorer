"""Optional bearer-token authentication for the API.

The backend is a LAN tool: ``API_AUTH_TOKEN`` empty (the default) keeps it
open for the demo setup.  With a token configured, every ``/api/v1`` request
must carry ``Authorization: Bearer <token>``, except the health probe.

The download endpoint is reached through an anchor click, which cannot set
headers, so the token may also travel as a ``?token=`` query parameter there
(header wins when both are present).
"""

from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

PUBLIC_PATHS = {"/api/v1/health"}


def _unauthorized() -> JSONResponse:
    """Build a fresh 401 response.

    A module level ``JSONResponse`` instance would be shared by every
    rejected request; Starlette responses carry per-request state (the
    request id middleware stamps the header on it), so each one needs its
    own object.
    """
    return JSONResponse(status_code=401, content={"detail": "未授权：缺少或错误的 API token"})


class AuthTokenMiddleware(BaseHTTPMiddleware):
    """Reject requests without the configured shared token."""

    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self._token = token

    def _authorized(self, request: Request) -> bool:
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        if (
            scheme.lower() == "bearer"
            and hmac.compare_digest(
                value.strip().encode(), self._token.encode()
            )
        ):
            return True
        # Anchor downloads cannot set headers: allow the query parameter.
        query_token = request.query_params.get("token", "")
        return bool(query_token) and hmac.compare_digest(
            query_token.encode(), self._token.encode()
        )

    async def dispatch(self, request: Request, call_next):
        if not self._token or request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        if not self._authorized(request):
            return _unauthorized()
        return await call_next(request)
