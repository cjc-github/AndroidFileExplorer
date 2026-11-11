"""Every response carries a request id that pins the matching log lines."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.request_id import REQUEST_ID_HEADER
from app.main import app


def test_every_response_is_stamped():
    with TestClient(app) as client:
        first = client.get("/api/v1/health")
        second = client.get("/api/v1/health")
    assert REQUEST_ID_HEADER in first.headers
    assert REQUEST_ID_HEADER in second.headers
    assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]


def test_an_incoming_request_id_is_echoed():
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/health", headers={REQUEST_ID_HEADER: "my-trace-id"}
        )
    assert response.headers[REQUEST_ID_HEADER] == "my-trace-id"


def test_a_401_is_stamped_too():
    """The auth middleware answers on its own, without the inner stack.

    The request id middleware must therefore sit OUTSIDE the auth check,
    otherwise a rejected request would carry no id at all - and the id is
    exactly what a user needs to quote when they report a rejected call.
    """
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from app.api.auth import AuthTokenMiddleware
    from app.api.request_id import RequestIdMiddleware

    async def endpoint(_request) -> JSONResponse:
        return JSONResponse({"ok": True})

    inner = Starlette(routes=[Route("/api/v1/engines", endpoint)])
    # Same order as app/main.py: the request id is added last (outermost).
    inner.add_middleware(AuthTokenMiddleware, token="secret")
    inner.add_middleware(RequestIdMiddleware)

    with TestClient(inner) as client:
        response = client.get("/api/v1/engines")
    assert response.status_code == 401
    assert REQUEST_ID_HEADER in response.headers
